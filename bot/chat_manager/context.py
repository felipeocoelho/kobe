"""Montagem dos blocos de CONVERSA no turno (síncrono, barato, read-only).

O turno é burro e rápido: lê o que o daemon já mastigou e cola no prompt.
Nada de embedding/LLM aqui (doc §6). Três blocos — todos sobre CONVERSAS
(a janela imediata de memória mora em `bot/memory/`, Highlander Frente 0):

- QUENTE: ponteiro do assunto corrente (título + tags + marco). Verbatim
  sob demanda via bot/bin/kobe-recall.
- FRIO: catálogo dos assuntos passados do tópico (tag cloud) + busca
  vetorial sob demanda via kobe-recall.
- RELAÇÕES: arestas leves (assuntos passados relacionados ao corrente),
  calculadas on-the-fly por similaridade de centroide.

Tudo filtrado por tópico (predicado obrigatório — Dev Kobe não puxa Olimpo).
"""

from __future__ import annotations

import logging
from typing import Optional

from supabase import Client

from bot.conversation_detector import _parse_vector
from bot.embedding import cosine_similarity


logger = logging.getLogger("kobe.chat_manager.context")


# Catálogo frio — quantos assuntos passados listar.
COLD_CATALOG_LIMIT = 12
# Relações — quantos assuntos relacionados ao corrente destacar.
RELATED_LIMIT = 3
RELATED_MIN_SIM = 0.35


def _load_active_conversation(db: Client, topic_id: str) -> Optional[dict]:
    res = (
        db.table("conversations")
        .select("id, title, slug, started_at, centroid_embedding")
        .eq("topic_id", topic_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _load_past_conversations(db: Client, topic_id: str) -> list[dict]:
    res = (
        db.table("conversations")
        .select("id, title, status, last_activity_at, centroid_embedding")
        .eq("topic_id", topic_id)
        .in_("status", ["dormant", "archived"])
        .order("last_activity_at", desc=True)
        .limit(COLD_CATALOG_LIMIT)
        .execute()
    )
    return res.data or []


def _load_tags(db: Client, conversation_ids: list[str]) -> dict[str, list[str]]:
    if not conversation_ids:
        return {}
    try:
        res = (
            db.table("conversation_tags")
            .select("conversation_id, tag, weight")
            .in_("conversation_id", conversation_ids)
            .order("weight", desc=True)
            .execute()
        )
    except Exception:  # noqa: BLE001 — tabela ausente (migration não aplicada)
        logger.warning("conversation_tags indisponível (migration?)", exc_info=True)
        return {}
    out: dict[str, list[str]] = {}
    for row in res.data or []:
        out.setdefault(row["conversation_id"], []).append(row["tag"])
    return out


def render_chat_manager_section(db: Client, topic_id: str) -> Optional[str]:
    """Monta o bloco residente (quente + frio + relações + instruções
    de pull sob demanda). None se não há nada (topic sem conversation)."""
    try:
        active = _load_active_conversation(db, topic_id)
        past = _load_past_conversations(db, topic_id)
    except Exception:  # noqa: BLE001 — read-only, nunca derruba o turno
        logger.warning("render_chat_manager_section falhou topic=%s", topic_id, exc_info=True)
        return None

    if not active and not past:
        return None

    conv_ids = ([active["id"]] if active else []) + [p["id"] for p in past]
    tags_by_conv = _load_tags(db, conv_ids)

    parts: list[str] = []

    # --- QUENTE: assunto corrente ---
    if active:
        title = active.get("title") or "(sem título)"
        started = (active.get("started_at") or "")[:10]
        tags = tags_by_conv.get(active["id"], [])
        tag_str = f" — tags: {', '.join(tags)}" if tags else ""
        parts.append("[Chat Manager — assunto corrente (quente)]")
        parts.append(f"Assunto: '{title}' (desde {started}){tag_str}.")
        parts.append(
            "Os últimos minutos já estão no histórico abaixo. Se precisar reler "
            "o assunto INTEIRO (do início até agora), rode: "
            f"`bot/bin/kobe-recall --conversation {active['id']}`."
        )

    # --- RELAÇÕES: assuntos passados próximos do corrente ---
    related_ids: set[str] = set()
    if active and active.get("centroid_embedding") is not None:
        ac = active["centroid_embedding"]
        if isinstance(ac, str):
            ac = _parse_vector(ac)
        scored = []
        for p in past:
            pc = p.get("centroid_embedding")
            if pc is None:
                continue
            if isinstance(pc, str):
                pc = _parse_vector(pc)
            try:
                sim = cosine_similarity(ac, pc)
            except ValueError:
                continue
            if sim >= RELATED_MIN_SIM:
                scored.append((sim, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        related = scored[:RELATED_LIMIT]
        if related:
            parts.append("")
            parts.append("[Assuntos passados relacionados ao corrente (frio)]")
            for sim, p in related:
                related_ids.add(p["id"])
                tags = tags_by_conv.get(p["id"], [])
                tag_str = f" ({', '.join(tags)})" if tags else ""
                parts.append(f"- '{p.get('title') or '(sem título)'}'{tag_str}")

    # --- FRIO: catálogo geral dos assuntos passados ---
    catalog = [p for p in past if p["id"] not in related_ids]
    if catalog:
        parts.append("")
        parts.append("[Catálogo de assuntos passados deste tópico (frio)]")
        for p in catalog:
            title = p.get("title") or "(sem título)"
            last = (p.get("last_activity_at") or "")[:10]
            tags = tags_by_conv.get(p["id"], [])
            tag_str = f" ({', '.join(tags)})" if tags else ""
            parts.append(f"- '{title}'{tag_str} — {last}")
        parts.append(
            "Pra puxar o conteúdo de um assunto passado por tema, rode: "
            '`bot/bin/kobe-recall "<termo ou pergunta>"`.'
        )

    return "\n".join(parts) if parts else None
