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
from datetime import datetime, timezone
from typing import Optional

from supabase import Client


logger = logging.getLogger("kobe.topics")

GENERAL_THREAD_ID: int = 0


def _normalize_thread_id(thread_id: Optional[int]) -> int:
    return thread_id if thread_id is not None else GENERAL_THREAD_ID


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_topic(db: Client, thread_id: Optional[int]) -> str:
    """Get-or-create do topic. Retorna o `topics.id` (UUID em str)."""
    key = _normalize_thread_id(thread_id)
    res = (
        db.table("topics")
        .upsert(
            {"telegram_thread_id": key, "last_activity_at": _now_iso()},
            on_conflict="telegram_thread_id",
        )
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
    db: Client, topic_id: str, *, summary: Optional[str] = None
) -> Optional[str]:
    """Marca a session ativa do topic como `archived`. Retorna o id arquivado
    ou `None` se não havia sessão ativa (caso /nova num topic recém-criado).

    A próxima mensagem do topic dispara `ensure_active_session`, que cria
    uma sessão nova automaticamente — não criamos aqui pra não deixar
    sessão vazia no banco se o operador rodar /nova e mudar de ideia.
    """
    res = (
        db.table("sessions")
        .update(
            {
                "status": "archived",
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
