"""Ciclo de vida de topics, sessions e messages no Supabase.

Camada fina sobre o cliente: encontra-ou-cria o topic correspondente ao
`message_thread_id` do Telegram, garante uma session ativa, e grava
mensagens individuais.

A constante `GENERAL_THREAD_ID = 0` é a chave do "general" do supergrupo:
o Telegram não emite `message_thread_id` quando a mensagem cai no chat raiz,
mas a coluna `topics.telegram_thread_id` é UNIQUE, e PostgreSQL permite
múltiplos NULLs em UNIQUE — o que abriria duplicação. Como o Telegram nunca
usa thread_id=0 pra tópicos reais, usamos 0 como sentinela do general.
Isso permite um único caminho de upsert atômico via ON CONFLICT.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from supabase import Client


logger = logging.getLogger("kobe.topics")

GENERAL_THREAD_ID: int = 0

# Limite de chars do contexto carregado em user-data/topics/<slug>/.
# Acima disso truncamos e avisamos o operador via Telegram pra ele
# reorganizar (mover algo pra saved_artifacts, dividir o KB, etc.).
TOPIC_CONTEXT_CHAR_LIMIT = 20_000

# Sufixo interno que `load_topic_context` adiciona quando a saída foi
# truncada. O caller no handler remove antes de injetar no prompt e
# usa pra disparar 1 aviso ao operador. Caracteres NUL não aparecem
# em conteúdo real de markdown, então não há colisão.
_TRUNCATED_MARKER = "\x00TRUNCATED\x00"


def _normalize_thread_id(thread_id: Optional[int]) -> int:
    return thread_id if thread_id is not None else GENERAL_THREAD_ID


def slugify(name: str) -> str:
    """Converte nome de tópico → slug compatível com filesystem.

    Minúsculo, sem acentos, qualquer run não-alfanumérica vira `-` único,
    sem `-` nas pontas. Casamento com o que `CLAUDE.md` promete pra
    `user-data/topics/<slug>/`.
    """
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def get_topic_slug(
    db: Client, chat_id: int, thread_id: Optional[int]
) -> Optional[str]:
    """Slug do tópico (kebab-case de `topics.current_name`) ou None se
    ainda não está no banco / o nome não foi capturado.

    `thread_id=None` (mensagem no chat raiz do supergrupo) devolve
    "general" — alinhado com a convenção do filesystem.

    `current_name` é populado pelos handlers `forum_topic_created` e
    `forum_topic_edited`. Tópicos pré-existentes (criados antes da v0.10)
    ficam com `current_name=NULL` até o operador renomear o tópico no
    Telegram (qualquer rename dispara `forum_topic_edited`) ou rodar
    UPDATE manual no Supabase. Quando vazio, logamos WARN pra dar pista.
    """
    if thread_id is None:
        return "general"
    res = (
        db.table("topics")
        .select("current_name")
        .eq("telegram_chat_id", chat_id)
        .eq("telegram_thread_id", thread_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    raw_name = (res.data[0].get("current_name") or "").strip()
    if not raw_name:
        logger.info(
            "topic_context: tópico thread_id=%s sem current_name; "
            "renomeie no Telegram ou rode UPDATE topics SET current_name=... "
            "pra ativar o knowledge dele.",
            thread_id,
        )
        return None
    slug = slugify(raw_name)
    return slug or None


def set_topic_name(
    db: Client,
    *,
    chat_id: int,
    thread_id: int,
    name: str,
) -> Optional[str]:
    """Persiste o nome do tópico em `topics.current_name` e arquiva o
    valor anterior em `topic_name_history` (auditoria).

    Chamado pelos handlers `forum_topic_created` / `forum_topic_edited`.
    Idempotente: se o nome já está atualizado, é no-op silencioso.

    Retorna o `current_name` **anterior** (ou `None` se era tópico novo
    ou já estava com esse nome). O caller usa isso pra detectar rename
    real e disparar `rename_topic_dir` no filesystem.
    """
    existing = (
        db.table("topics")
        .select("id, current_name")
        .eq("telegram_chat_id", chat_id)
        .eq("telegram_thread_id", thread_id)
        .limit(1)
        .execute()
    )
    if not existing.data:
        # Tópico ainda não foi visto: cria a linha com o nome já preenchido.
        # `ensure_topic` faria upsert sem o nome — preferimos inserir aqui
        # com tudo de uma vez (evita uma rodada extra).
        db.table("topics").upsert(
            {
                "telegram_chat_id": chat_id,
                "telegram_thread_id": thread_id,
                "current_name": name,
                "last_activity_at": _now_iso(),
            },
            on_conflict="telegram_thread_id",
        ).execute()
        return None

    row = existing.data[0]
    previous = (row.get("current_name") or "") or None
    if previous == name:
        return None

    db.table("topics").update({"current_name": name}).eq("id", row["id"]).execute()
    db.table("topic_name_history").insert(
        {"topic_id": row["id"], "name": name}
    ).execute()
    return previous


def set_topic_status(
    db: Client, *, chat_id: int, thread_id: int, status: str
) -> Optional[str]:
    """Atualiza `topics.status` em resposta a forum_topic_closed/reopened.

    Aceita os valores válidos do CHECK do schema: 'active', 'archived',
    'deleted'. Retorna o `topics.id` modificado ou None se a linha não
    existir (evento sem topic prévio — improvável, mas defensivo).
    """
    res = (
        db.table("topics")
        .update({"status": status})
        .eq("telegram_chat_id", chat_id)
        .eq("telegram_thread_id", thread_id)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]["id"]


def rename_topic_dir(
    kobe_home: Path, old_slug: str, new_slug: str
) -> str:
    """Move `user-data/topics/<old_slug>/` → `<new_slug>/` quando o
    operador renomeia o tópico no Telegram. Retorna status:

    - `"renamed"` — pasta movida com sucesso
    - `"no_source"` — pasta antiga não existia (operador nunca criou KB)
    - `"same"` — slugs iguais (rename foi cosmético, ex: "Olimpo" → "OLIMPO")
    - `"conflict"` — destino já existe com conteúdo, abortado pra evitar perda
    - `"error"` — falha de IO (logada, caller decide o que fazer)
    """
    if old_slug == new_slug or not old_slug or not new_slug:
        return "same"
    base = kobe_home / "user-data" / "topics"
    src = base / old_slug
    dst = base / new_slug
    if not src.is_dir():
        return "no_source"
    if dst.exists():
        logger.warning(
            "rename_topic_dir: destino já existe (%s) — abortado, conteúdo de %s preservado",
            dst,
            src,
        )
        return "conflict"
    try:
        src.rename(dst)
    except OSError as exc:
        logger.exception("rename_topic_dir: falha movendo %s → %s: %s", src, dst, exc)
        return "error"
    logger.info("rename_topic_dir: %s → %s", src, dst)
    return "renamed"


def load_topic_context(kobe_home: Path, slug: str) -> Optional[str]:
    """Lê `user-data/topics/<slug>/prompt.md` + `knowledge/*` (ordem
    alfabética) e devolve string única pra injetar no prompt do Claude.

    Retorna `None` se o diretório do tópico não existir (caso normal —
    nem todo tópico tem KB). Trunca em `TOPIC_CONTEXT_CHAR_LIMIT` chars
    adicionando `_TRUNCATED_MARKER` ao final pra sinalizar ao caller que
    deve avisar o operador. Arquivos individuais ilegíveis são pulados
    com WARN — falha de um arquivo não derruba o tópico inteiro.
    """
    topic_dir = kobe_home / "user-data" / "topics" / slug
    if not topic_dir.is_dir():
        return None

    chunks: list[str] = []

    prompt_md = topic_dir / "prompt.md"
    if prompt_md.is_file():
        try:
            content = prompt_md.read_text(encoding="utf-8").strip()
            if content:
                chunks.append(f"## {slug}/prompt.md\n\n{content}")
        except OSError as exc:
            logger.warning("topic_context: falhou lendo %s: %s", prompt_md, exc)

    knowledge_dir = topic_dir / "knowledge"
    if knowledge_dir.is_dir():
        for f in sorted(knowledge_dir.iterdir()):
            if not f.is_file():
                continue
            try:
                content = f.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("topic_context: falhou lendo %s: %s", f, exc)
                continue
            if content:
                chunks.append(f"## {slug}/knowledge/{f.name}\n\n{content}")

    if not chunks:
        return None

    full = "\n\n---\n\n".join(chunks)
    if len(full) <= TOPIC_CONTEXT_CHAR_LIMIT:
        return full

    original_len = len(full)
    truncated = full[:TOPIC_CONTEXT_CHAR_LIMIT]
    cut = truncated.rfind("\n")
    if cut > 0:
        truncated = truncated[:cut]
    truncated += "\n\n[...truncado em TOPIC_CONTEXT_CHAR_LIMIT chars...]"
    logger.warning(
        "topic_context: tópico '%s' estourou limite (%d > %d chars) — truncado",
        slug,
        original_len,
        TOPIC_CONTEXT_CHAR_LIMIT,
    )
    return truncated + _TRUNCATED_MARKER


def list_unwelcomed_topics(db: Client) -> list[dict]:
    """Tópicos que ainda não receberam a msg de boas-vindas da v0.11.

    Critério: `welcomed_at IS NULL` AND `telegram_chat_id IS NOT NULL`
    (sem chat_id não conseguimos enviar mensagem proativa). Retorna lista
    de `{topic_id, telegram_chat_id, telegram_thread_id, current_name}`
    pra o caller iterar no startup.

    NOTA: tópicos com `current_name=NULL` também são incluídos — General
    (thread_id=0) tem nome implícito mas current_name vazio, e queremos
    enviá-lo lá também. O caller decide o slug via `get_topic_slug`.
    """
    res = (
        db.table("topics")
        .select("id, telegram_chat_id, telegram_thread_id, current_name")
        .is_("welcomed_at", "null")
        .not_.is_("telegram_chat_id", "null")
        .eq("status", "active")
        .execute()
    )
    return res.data or []


def mark_welcomed(db: Client, topic_id: str) -> None:
    """Marca o tópico como onboardado (msg de boas-vindas enviada).

    Idempotente — se já está marcado, é no-op silencioso (update de
    coluna pelo mesmo valor é OK no Postgres).
    """
    db.table("topics").update({"welcomed_at": _now_iso()}).eq("id", topic_id).execute()


def topic_knowledge_dir(kobe_home: Path, slug: str) -> Path:
    """Pasta onde anexos do operador são salvos como KB do tópico."""
    return kobe_home / "user-data" / "topics" / slug / "knowledge"


def unique_knowledge_path(kobe_home: Path, slug: str, basename: str) -> Path:
    """Devolve um path único em `knowledge/` derivado de `basename`.

    Sanitiza separadores de caminho (Telegram aceita filenames com `/`),
    força extensão `.md` (todo upload vira markdown — extraímos texto e
    perdemos formatação original), e adiciona sufixo `-2`, `-3`… se já
    existir arquivo com o mesmo nome (não sobrescreve).
    """
    safe = basename.replace("/", "_").replace("\\", "_").strip() or "anexo"
    stem = Path(safe).stem or "anexo"
    target_dir = topic_knowledge_dir(kobe_home, slug)
    target = target_dir / f"{stem}.md"
    if not target.exists():
        return target
    i = 2
    while True:
        candidate = target_dir / f"{stem}-{i}.md"
        if not candidate.exists():
            return candidate
        i += 1


def consume_truncated_marker(context: Optional[str]) -> tuple[Optional[str], bool]:
    """Retorna `(contexto_limpo, foi_truncado)`. Tira o marcador interno
    antes de injetar no prompt.
    """
    if context is None:
        return None, False
    if context.endswith(_TRUNCATED_MARKER):
        return context[: -len(_TRUNCATED_MARKER)].rstrip(), True
    return context, False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_topic(
    db: Client,
    thread_id: Optional[int],
    *,
    chat_id: Optional[int] = None,
) -> str:
    """Get-or-create do topic. Retorna o `topics.id` (UUID em str).

    `chat_id` (id do supergrupo do Telegram) é atualizado sempre que
    fornecido — viabiliza mensagens proativas (snapshot-de-continuação)
    no tópico correto após restart.
    """
    key = _normalize_thread_id(thread_id)
    payload: dict = {"telegram_thread_id": key, "last_activity_at": _now_iso()}
    if chat_id is not None:
        payload["telegram_chat_id"] = chat_id
    res = (
        db.table("topics")
        .upsert(payload, on_conflict="telegram_thread_id")
        .execute()
    )
    if not res.data:
        raise RuntimeError(f"upsert de topic não retornou linha (thread_id={key})")
    return res.data[0]["id"]


def ensure_active_session(db: Client, topic_id: str) -> str:
    """Get-or-create da session ativa do topic. Retorna `sessions.id`.

    Nota: há uma janela de corrida teórica (duas mensagens chegando no
    primeiro instante de um topic novo, ambas vendo "sem session ativa" e
    ambas inserindo). O schema atual não tem unique parcial em
    (topic_id WHERE status='active'), então aceitamos o risco — em prática
    o handler é serial por mensagem e o caso só dispara no primeiro evento
    de um topic novíssimo.
    """
    existing = (
        db.table("sessions")
        .select("id")
        .eq("topic_id", topic_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    created = (
        db.table("sessions")
        .insert({"topic_id": topic_id, "status": "active"})
        .execute()
    )
    if not created.data:
        raise RuntimeError(f"insert de session não retornou linha (topic_id={topic_id})")
    return created.data[0]["id"]


def get_recent_messages(
    db: Client, session_id: str, limit: int = 20
) -> list[dict]:
    """Últimas N mensagens da session em ordem cronológica (mais antiga primeiro).

    Usado pra montar o histórico que vai no prompt do Claude. Buscamos em
    ordem decrescente pra pegar as mais recentes (caso a sessão seja longa)
    e revertemos pra apresentar como conversa natural.
    """
    res = (
        db.table("messages")
        .select("role, content, created_at")
        .eq("session_id", session_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(res.data or []))


def archive_active_session(
    db: Client,
    topic_id: str,
    *,
    summary: Optional[str] = None,
    status: str = "archived",
) -> Optional[str]:
    """Marca a session ativa do topic como `archived` (ou `compacted` na
    compactação automática da v0.12). Retorna o id da sessão modificada
    ou `None` se não havia sessão ativa (caso /nova num topic recém-criado).

    A próxima mensagem do topic dispara `ensure_active_session`, que cria
    uma sessão nova automaticamente — não criamos aqui pra não deixar
    sessão vazia no banco se o operador rodar /nova e mudar de ideia.

    `status` pode ser 'archived' (operador rodou /nova) ou 'compacted'
    (limiar de mensagens atingido e bot rotacionou pra preservar contexto).
    """
    if status not in ("archived", "compacted"):
        raise ValueError(f"status inválido: {status!r}")
    res = (
        db.table("sessions")
        .update(
            {
                "status": status,
                "ended_at": _now_iso(),
                **({"summary": summary} if summary is not None else {}),
            }
        )
        .eq("topic_id", topic_id)
        .eq("status", "active")
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]["id"]


def count_messages(db: Client, session_id: str) -> int:
    res = (
        db.table("messages")
        .select("id", count="exact")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    return res.count or 0


def get_active_session(db: Client, topic_id: str) -> Optional[dict]:
    """Retorna a session ativa do topic (ou None) — sem criar."""
    res = (
        db.table("sessions")
        .select("id, started_at")
        .eq("topic_id", topic_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def insert_message(
    db: Client,
    *,
    session_id: str,
    topic_id: str,
    role: str,
    content: str,
    telegram_message_id: Optional[int] = None,
    audio_transcribed: bool = False,
) -> str:
    """Grava uma mensagem (user/assistant/system). Retorna `messages.id`."""
    res = (
        db.table("messages")
        .insert(
            {
                "session_id": session_id,
                "topic_id": topic_id,
                "telegram_message_id": telegram_message_id,
                "role": role,
                "content": content,
                "audio_transcribed": audio_transcribed,
            }
        )
        .execute()
    )
    if not res.data:
        raise RuntimeError("insert de message não retornou linha")
    return res.data[0]["id"]
