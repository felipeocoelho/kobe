"""Reações de recebimento no Telegram — sinal de "chegou" impossível de alucinar.

A dor: uma mensagem que entra e morre no caminho (banco fora, exceção engolida)
é indistinguível, do lado do operador, de uma mensagem que o agente ignorou.
Todo sinal de vida que temos hoje passa por MODELO (o ack do Liveness) ou por
código que roda DEPOIS do montador — se o turno morre antes, não sai nada.

Este módulo é o sinal mais primitivo possível: uma chamada direta à API do
Telegram, disparada no instante em que o update entra no handler, ANTES do
montador da borda e do classificador. Não passa por modelo nenhum, então não
tem o que alucinar; e não depende do turno sobreviver.

Semáforo de estágio (o Telegram permite UMA reação por mensagem, e a nova
SUBSTITUI a anterior — o que transforma a própria mensagem num indicador):

    texto/áudio/anexo chega  → 👀
    transcrição do áudio ok  → ✍️  (substitui o 👀)

Efeito colateral desejado: com isto, "chegou e morreu calado" vira visível — o
👀 aparece e o silêncio depois dele grita. É rede de segurança em cima da rede
de segurança (ver bot/turn_guarantee.py).

Restrição da plataforma (verificada contra a API real em 2026-08-20, no tópico
de fórum do Dev Kobe): 👀 e ✍️ estão na lista permitida e funcionam em tópico
de fórum, inclusive em mensagem do operador. 🎧/👂 NÃO estão (`REACTION_INVALID`).

Atrás da flag `TELEGRAM_REACTIONS_ENABLED` — ver bot/config.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional


logger = logging.getLogger("kobe.reactions")

# Emojis default. Configuráveis por .env (ver bot/config.py) pra trocar sem obra
# — a lista de emojis aceitos é do Telegram e pode mudar sem aviso.
DEFAULT_RECEIVED = "👀"
DEFAULT_TRANSCRIBED = "✍️"

# Tasks fire-and-forget: sem manter a referência, o GC pode coletar a task antes
# de ela rodar (asyncio guarda só referência fraca). Mesmo padrão do handler.
_TASKS: set = set()


async def set_reaction(bot, chat_id: int, message_id: int, emoji: str) -> None:
    """Põe (ou substitui) a reação do bot numa mensagem. NUNCA levanta.

    Best-effort absoluto: emoji fora da lista permitida, permissão faltando,
    Telegram instável — nada disso pode derrubar nem atrasar o turno. O pior
    caso aceito é a reação não aparecer.
    """
    try:
        await bot.set_message_reaction(
            chat_id=chat_id, message_id=message_id, reaction=emoji
        )
    except Exception as exc:  # noqa: BLE001 — sinal é decoração, nunca bloqueio
        logger.warning(
            "reactions: falha reagindo %s em chat=%s msg=%s (%s)",
            emoji, chat_id, message_id, exc,
        )


def react(bot, chat_id: int, message_id: Optional[int], emoji: Optional[str]) -> None:
    """Dispara a reação SEM esperar (fire-and-forget).

    Chamada síncrona de propósito: quem recebe a mensagem não paga latência
    nenhuma por este sinal. `emoji` None/vazio é no-op (permite desligar um dos
    estágios pelo .env sem tocar em código).
    """
    if not emoji or message_id is None:
        return
    task = asyncio.create_task(set_reaction(bot, chat_id, message_id, emoji))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
