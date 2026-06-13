"""Resolução de tópico por nome para os helpers `kobe-notify`/`kobe-attach`.

Stdlib-only de propósito: os helpers rodam como subprocess de `claude -p` sob
qualquer python3 (não necessariamente o do venv), então aqui não dependemos de
`supabase`/`dotenv`. A tabela `topics` é consultada via REST (PostgREST) e as
credenciais saem do `.env` do projeto.

Compartilhado pelos dois helpers pra a flag `--topic` ter comportamento
idêntico (mesmo match por `current_name`/slug, mesmos erros).
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

# Raiz do projeto ($KOBE_HOME): bot/bin/_kobe_topic.py → bin → bot → raiz.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def slugify(name: str) -> str:
    """Slug kebab-case — mesma regra de bot/topic_manager.slugify.

    Minúsculo, sem acentos, qualquer run não-alfanumérica vira `-` único, sem
    `-` nas pontas.
    """
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def read_dotenv(keys: set[str]) -> dict[str, str]:
    """Lê chaves específicas do `.env` do projeto (parser mínimo, stdlib).

    Primeiro consulta `os.environ` (caso o serviço já exporte), depois faz
    fallback pro arquivo. Ignora comentários e tira aspas do valor.
    """
    found: dict[str, str] = {}
    for k in keys:
        v = os.environ.get(k)
        if v:
            found[k] = v
    missing = keys - found.keys()
    if not missing:
        return found
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return found
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key in missing:
                found[key] = val.strip().strip("'").strip('"')
    except OSError:
        pass
    return found


def resolve_topic(name: str) -> tuple[int, int | None]:
    """Resolve um nome de tópico → (telegram_chat_id, telegram_thread_id).

    Match por `current_name` case-insensitive OU pelo slug. Levanta
    `LookupError` com mensagem clara se não encontrar ou se for ambíguo.
    `telegram_thread_id` 0/None (raiz general/private) vira None — o chamador
    não deve setar `message_thread_id` nesse caso.
    """
    env = read_dotenv({"SUPABASE_URL", "SUPABASE_KEY"})
    url = (env.get("SUPABASE_URL") or "").rstrip("/")
    key = env.get("SUPABASE_KEY") or ""
    if not url or not key:
        raise LookupError(
            "SUPABASE_URL/SUPABASE_KEY ausentes no .env — não consigo resolver "
            "o tópico por nome. Verifique o .env do projeto."
        )

    endpoint = (
        f"{url}/rest/v1/topics"
        "?select=current_name,telegram_chat_id,telegram_thread_id"
    )
    req = urllib.request.Request(
        endpoint,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )
    try:
        raw = urllib.request.urlopen(req, timeout=15).read()
        rows = json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise LookupError(f"Supabase respondeu HTTP {exc.code}: {body}") from exc
    except Exception as exc:  # noqa: BLE001 — rede/timeout/parse
        raise LookupError(f"falha consultando topics no Supabase: {exc}") from exc

    target = name.strip().lower()
    target_slug = slugify(name)
    matches: list[dict] = []
    for row in rows or []:
        chat_id = row.get("telegram_chat_id")
        if chat_id is None:
            continue
        current = (row.get("current_name") or "").strip()
        if current and (current.lower() == target or slugify(current) == target_slug):
            matches.append(row)
            continue
        # Tópicos sem current_name (private/general pré-rename): casa pelo slug
        # derivado do sinal do chat_id, igual a convenção do topic_manager.
        if not current and target_slug in {"private", "general"}:
            thread = row.get("telegram_thread_id")
            is_rootish = thread is None or thread == 0
            if is_rootish and (
                (target_slug == "private" and chat_id > 0)
                or (target_slug == "general" and chat_id < 0)
            ):
                matches.append(row)

    if not matches:
        raise LookupError(
            f"nenhum tópico chamado {name!r} (nem slug {target_slug!r}) na "
            "tabela topics. Confira o nome exato com /conversas_topico ou o "
            "cabeçalho [Telegram] tópico: no prompt."
        )
    if len(matches) > 1:
        nomes = ", ".join(repr((m.get("current_name") or "?")) for m in matches)
        raise LookupError(
            f"nome {name!r} é ambíguo — casou com {len(matches)} tópicos "
            f"({nomes}). Use o nome exato."
        )

    row = matches[0]
    thread = row.get("telegram_thread_id")
    thread_out = thread if thread else None  # 0/None = raiz → sem thread no envio
    return int(row["telegram_chat_id"]), thread_out


def parse_topic_arg(argv: list[str]) -> tuple[str | None, list[str]]:
    """Extrai `--topic <nome>` (ou `--topic=<nome>`) de `argv`.

    Devolve (topic_ou_None, resto_dos_args). Levanta `ValueError` se `--topic`
    vier sem valor.
    """
    topic: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--topic":
            if i + 1 >= len(argv):
                raise ValueError("--topic exige um nome")
            topic = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--topic="):
            topic = arg[len("--topic="):]
            i += 1
            continue
        rest.append(arg)
        i += 1
    return topic, rest
