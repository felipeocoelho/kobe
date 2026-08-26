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

from bot.db import KobeDB


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
    db: KobeDB,
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

    criado = db.execute(
        "INSERT INTO saved_artifacts (topic_id, title, content, tags)"
        " VALUES (%s, %s, %s, %s)"
        " RETURNING id",
        (topic_id, title, content, tags or None),
    )
    if not criado:
        raise RuntimeError("insert de saved_artifact não retornou linha")
    return criado[0]["id"]


def search_artifacts(
    db: KobeDB,
    query: str,
    *,
    topic_id: Optional[str] = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[dict]:
    """Busca artefatos por substring no title/content (case-insensitive).

    Sem embeddings: este é o fallback bootstrap até a Fase 9. Quando
    embeddings entrarem, esta função vira a estratégia secundária (ou é
    substituída por busca vetorial direta).

    A vírgula continua sendo trocada por espaço na entrada. Com SQL ela
    deixou de ser sintaxe (o `.or_()` do PostgREST separava cláusulas por
    vírgula, e uma vírgula digitada pelo operador quebrava o filtro) — mas
    a normalização segue, porque mudar o conjunto de resultados de uma
    busca que o operador usa não é assunto desta migração.

    NUANCE CONHECIDA, DELIBERADAMENTE PRESERVADA: `%` e `_` digitados pelo
    operador continuam valendo como curinga do LIKE, porque valiam antes.
    Escapá-los seria uma melhoria defensável — um `%` solto hoje faz a busca
    trazer tudo — mas é mudança de comportamento num comando que o operador
    usa (`/retomar`), e mudar isso não é assunto de uma migração de driver.
    Fica registrado para quem for decidir depois.
    """
    sanitized = query.replace(",", " ").strip()
    if not sanitized:
        return []

    pattern = f"%{sanitized}%"

    sql = [
        "SELECT id, title, content, topic_id, created_at",
        "  FROM saved_artifacts",
        " WHERE (title ILIKE %s OR content ILIKE %s)",
    ]
    params: list = [pattern, pattern]
    if topic_id is not None:
        sql.append("   AND topic_id = %s")
        params.append(topic_id)
    sql.append(" ORDER BY created_at DESC")
    sql.append(" LIMIT %s")
    params.append(limit)

    return db.query("\n".join(sql), params)
