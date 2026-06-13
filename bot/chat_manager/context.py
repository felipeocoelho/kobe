"""Montagem das camadas de contexto no turno (síncrono, barato, read-only).

O turno é burro e rápido: lê o que o daemon já mastigou e cola no prompt.
Nada de embedding/LLM aqui (doc §6). Quatro camadas:

- IMEDIATO: últimos ~10 min OU últimas N msgs DESTE tópico, verbatim, sempre.
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
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client

from bot.conversation_detector import _parse_vector
from bot.embedding import cosine_similarity


logger = logging.getLogger("kobe.chat_manager.context")


def _parse_ts(value: str) -> Optional[datetime]:
    """Parseia timestamp ISO 8601 (created_at do Supabase) com tolerância a
    sufixo 'Z'. None se vazio/inválido — chamador cai no fallback."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# Camada imediata — piso híbrido (doc §3): "últimos 10 min OU últimas N
# msgs, o que for maior". Janela de 10 min (não 2): áudios às vezes
# demoram minutos pra subir (Telegram/upload), então uma janela curta
# deixava a fala cair fora do imediato. 10 min cobre o frenesi de envio
# do operador sem inchar a memória. HARD_CAP dá folga pra janela não ser
# silenciosamente cortada num pico de mensagens.
IMMEDIATE_WINDOW_SECONDS = 600
IMMEDIATE_MIN_COUNT = 8
IMMEDIATE_HARD_CAP = 60

# Catálogo frio — quantos assuntos passados listar.
COLD_CATALOG_LIMIT = 12
# Relações — quantos assuntos relacionados ao corrente destacar.
RELATED_LIMIT = 3
RELATED_MIN_SIM = 0.35


def get_immediate_messages(
    db: Client, topic_id: str
) -> list[dict]:
    """Camada imediata: piso híbrido (10 min OU N msgs, o que for maior).

    Filtra por tópico (predicado que evita full scan cruzado). Ordem
    cronológica crescente, pronta pro histórico do prompt.
    """
    res = (
        db.table("messages")
        .select("role, content, created_at, audio_transcribed")
        .eq("topic_id", topic_id)
        .order("created_at", desc=True)
        .limit(IMMEDIATE_HARD_CAP)
        .execute()
    )
    rows = list(reversed(res.data or []))
    # Blindagem: jamais deixar um [Resumo da sessão anterior] (role='system'
    # injetado pelo compactador legado) entrar na janela crua. Com Chat
    # Manager a compactação não roda mais, mas summaries de antes deste fix
    # podem estar no fluxo do tópico — filtra pra não poluir o cru. Princípio:
    # ponteiro, nunca resumo. Contexto profundo vem do kobe-recall, não daqui.
    rows = [
        r
        for r in rows
        if not (
            r.get("role") == "system"
            and (r.get("content") or "").lstrip().startswith("[Resumo da sessão")
        )
    ]
    if not rows:
        return []
    # Âncora da janela: timestamp da ÚLTIMA mensagem da conversa, NÃO 'agora'.
    # Assim "últimos 10 min" são os 10 min finais de CONVERSA real — se o
    # operador larga o telefone por horas e volta, o imediato ainda traz o fim
    # do último papo inteiro, em vez de cair pro piso de N msgs decapitado.
    # Fallback pra now() só se o último created_at vier ilegível.
    anchor = _parse_ts(rows[-1].get("created_at") or "") or datetime.now(timezone.utc)
    cutoff = (anchor - timedelta(seconds=IMMEDIATE_WINDOW_SECONDS)).isoformat()
    within = [r for r in rows if (r.get("created_at") or "") >= cutoff]
    keep = max(len(within), IMMEDIATE_MIN_COUNT)
    return rows[-keep:]


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
