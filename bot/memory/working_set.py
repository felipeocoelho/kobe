"""Janela imediata — a camada crua e barata da memória de trabalho.

Movida de `bot/chat_manager/context.py` na Frente 0 do Highlander (refactor
sem mudar comportamento). É memória PURA: consulta `messages` só por `topic_id`
(não toca `conversations`), então o lugar dela é aqui, não no gerenciador de
conversas.

O turno é burro e rápido: lê o que já está no banco e cola no prompt. Nada de
embedding/LLM aqui (plano §6). Filtrada por tópico (predicado obrigatório —
Dev Kobe não puxa Olimpo).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client


# Camada imediata — piso híbrido (doc §3): "últimos 10 min OU últimas N
# msgs, o que for maior". Janela de 10 min (não 2): áudios às vezes
# demoram minutos pra subir (Telegram/upload), então uma janela curta
# deixava a fala cair fora do imediato. 10 min cobre o frenesi de envio
# do operador sem inchar a memória. HARD_CAP dá folga pra janela não ser
# silenciosamente cortada num pico de mensagens.
IMMEDIATE_WINDOW_SECONDS = 600
IMMEDIATE_MIN_COUNT = 8
IMMEDIATE_HARD_CAP = 60


def _parse_ts(value: str) -> Optional[datetime]:
    """Parseia timestamp ISO 8601 (created_at do Supabase) com tolerância a
    sufixo 'Z'. None se vazio/inválido — chamador cai no fallback."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


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
