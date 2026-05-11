"""Persistência e busca de artefatos salvos.

Artefatos são "memórias longas" — snapshots de conversa que o operador
decidiu guardar pra recuperar depois via `/retomar`. Cada um vive em
`saved_artifacts` com title + content (texto cru concatenado da sessão).

A coluna `embedding` é VECTOR(1536) no schema, mas nesta fase não
populamos: a busca semântica fica como pós-MVP (Fase 9+). Por enquanto
`/retomar` faz fallback em ILIKE sobre title+content, que cobre o caso
em que o operador lembra de uma palavra-chave do título.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from supabase import Client


logger = logging.getLogger("kobe.artifacts")

DEFAULT_SEARCH_LIMIT = 5


def _format_messages_as_transcript(messages: Iterable[dict]) -> str:
    """Concatena mensagens da session no formato 'role: content' por linha."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def save_artifact_from_messages(
    db: Client,
    *,
    topic_id: str,
    title: str,
    messages: Iterable[dict],
    tags: Optional[list[str]] = None,
) -> Optional[str]:
    """Cria um artefato a partir das mensagens já serializadas. Retorna o id
    do artefato — ou `None` se a sessão estava vazia (nada pra salvar).
    """
    content = _format_messages_as_transcript(messages)
    if not content:
        return None

    payload: dict = {
        "topic_id": topic_id,
        "title": title,
        "content": content,
    }
    if tags:
        payload["tags"] = tags

    res = db.table("saved_artifacts").insert(payload).execute()
    if not res.data:
        raise RuntimeError("insert de saved_artifact não retornou linha")
    return res.data[0]["id"]


def search_artifacts(
    db: Client,
    query: str,
    *,
    topic_id: Optional[str] = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[dict]:
    """Busca artefatos por substring no title/content (case-insensitive).

    Sem embeddings: este é o fallback bootstrap até a Fase 9. Quando
    embeddings entrarem, esta função vira a estratégia secundária (ou é
    substituída por busca vetorial direta).

    PostgREST `.or_()` usa vírgula como separador entre cláusulas, então
    a query precisa ser sanitizada — caso contrário uma vírgula no input
    do operador quebra o filtro.
    """
    sanitized = query.replace(",", " ").strip()
    if not sanitized:
        return []

    pattern = f"%{sanitized}%"
    builder = (
        db.table("saved_artifacts")
        .select("id, title, content, topic_id, created_at")
        .or_(f"title.ilike.{pattern},content.ilike.{pattern}")
        .order("created_at", desc=True)
        .limit(limit)
    )
    if topic_id is not None:
        builder = builder.eq("topic_id", topic_id)
    res = builder.execute()
    return list(res.data or [])
