"""Resolução de tópico por nome para os helpers `kobe-notify`/`kobe-attach`.

Compartilhado pelos dois helpers pra a flag `--topic` ter comportamento
idêntico (mesmo match por `current_name`/slug, mesmos erros).

SOBRE O "STDLIB-ONLY" — o que continua valendo e o que mudou
-------------------------------------------------------------
Os helpers rodam como subprocess de `claude -p` sob qualquer python3, não
necessariamente o do venv. O caminho COMUM (`kobe-notify "texto"`, endereçado
pelas envs) segue **stdlib puro** e não encosta em banco nenhum.

O que mudou é só o caminho `--topic`: antes ele consultava `topics` por HTTP
(PostgREST), o que dava pra fazer com `urllib`. Com a ponte direta pro Postgres
ele precisa de `psycopg`, que só existe no venv — então esse caminho, e só ele,
re-executa sob o python do venv quando necessário.

**Este arquivo é fácil de esquecer numa migração**, e por isso o aviso: ele não
usa o cliente do bot nem aparece numa busca por pontos de consulta. Se ficasse
para trás, o `--topic` das salas destacadas morreria em silêncio — o caminho é
tardio e só dispara quando alguém usa a flag.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
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


def _garantir_psycopg() -> None:
    """Garante que `psycopg` esteja disponível, re-executando sob o venv se não.

    O shebang dos helpers resolve pro python do SISTEMA, que não tem as deps do
    projeto. Mesmo padrão de `kobe-await-response` — mas aqui o re-exec é
    TARDIO, dentro de `resolve_topic`, para que o caminho comum (endereçamento
    por env, sem `--topic`) continue rodando sob qualquer python3 sem custo.

    Re-executar aqui é seguro porque `resolve_topic` é chamada ANTES de o
    helper enviar qualquer coisa: reiniciar o processo não duplica mensagem
    nem anexo. Se o venv não existir, seguimos e o `import` falha com um erro
    claro, em vez de re-executar às cegas.
    """
    import importlib.util

    if importlib.util.find_spec("psycopg") is not None:
        return

    # `execv` substitui o processo: o que estiver em buffer de saída e não foi
    # descarregado some. Fora de um terminal a saída é bufferizada por bloco,
    # então isso não é hipotético.
    sys.stdout.flush()
    sys.stderr.flush()

    from _venv import ensure, venv_do_projeto

    ensure()  # não volta, no caminho normal

    # Voltou: não havia venv pra onde ir (ou o `exec` falhou). Melhor um erro
    # que diz o que fazer do que um `ModuleNotFoundError` cru vindo de dentro de
    # uma função de resolução de tópico — quem chamou `--topic` não vai
    # adivinhar a ligação.
    raise LookupError(
        "`--topic` precisa do pacote `psycopg`, que não está disponível neste "
        f"python ({sys.executable}) e não achei o venv do projeto "
        f"({venv_do_projeto()}). Rode o helper pelo python do venv, ou use o "
        "endereçamento por env (KOBE_CHAT_ID/KOBE_THREAD_ID), que não toca no "
        "banco."
    )


def read_dotenv(keys: set[str], *, prefixo: str | None = None) -> dict[str, str]:
    """Lê chaves específicas do `.env` do projeto (parser mínimo, stdlib).

    Primeiro consulta `os.environ` (caso o serviço já exporte), depois faz
    fallback pro arquivo. Ignora comentários e tira aspas do valor.

    `prefixo` pede **toda** chave que comece por ele (`LUCIEN_`), e não só as
    nomeadas em `keys`. Existe porque uma lista de chaves é uma lista para ficar
    desatualizada — é o mesmo erro que `_venv.py` documenta ter cometido três
    vezes com listas de dependências. Quem carrega a configuração de um
    subsistema quer a configuração dele inteira, não a que alguém lembrou de
    listar. Com `prefixo`, o arquivo é sempre lido (não há como saber pelo
    ambiente quais chaves *existiriam* nele).

    O ambiente do processo continua vencendo o arquivo, sempre: quem exportou a
    variável na mão quis aquilo.
    """
    found: dict[str, str] = {}
    for k in keys:
        v = os.environ.get(k)
        if v:
            found[k] = v
    missing = keys - found.keys()
    if not missing and not prefixo:
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
            if key in missing or (prefixo and key.startswith(prefixo)):
                # Do ambiente, se lá houver; do arquivo, se não.
                found[key] = os.environ.get(key) or val.strip().strip("'").strip('"')
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
    _garantir_psycopg()

    import psycopg
    from psycopg.rows import dict_row

    env = read_dotenv({"DATABASE_URL"})
    url = (env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise LookupError(
            "DATABASE_URL ausente no .env — não consigo resolver o tópico por "
            "nome. Verifique o .env do projeto."
        )

    try:
        with psycopg.connect(url, row_factory=dict_row, connect_timeout=15) as conn:
            rows = conn.execute(
                "SELECT current_name, telegram_chat_id, telegram_thread_id FROM topics"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — conexão/timeout/permissão
        raise LookupError(f"falha consultando topics no banco: {exc}") from exc

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
            "tabela topics. Confira o nome exato no cabeçalho "
            "[Telegram] tópico: do prompt."
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
