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
    transcrição do áudio ok  → ✍  (substitui o 👀)

Efeito colateral desejado: com isto, "chegou e morreu calado" vira visível — o
👀 aparece e o silêncio depois dele grita. É rede de segurança em cima da rede
de segurança (ver bot/turn_guarantee.py).

Restrição da plataforma (verificada contra a API real em 2026-08-20, no tópico
de fórum do Dev Kobe): 👀 e ✍ estão na lista permitida e funcionam em tópico de
fórum, inclusive em mensagem do operador. 🎧/👂 NÃO estão (`REACTION_INVALID`).

A forma EXATA importa (bug de produção, 2026-08-20): a lista do Bot API registra
o emoji da mão como "✍" = U+270D PURO. Escrito como "✍️" (U+270D + U+FE0F, o
VARIATION SELECTOR-16 que pede a renderização colorida) o Telegram não acha na
lista, tenta ler como custom emoji e recusa — `Can't parse reactiontype: field
"custom_emoji_id" must be a valid number`. E a recusa é MUDA, porque reação é
decoração e a falha é engolida de propósito (ver `set_reaction`).

O conserto ingênuo — "tira sempre o VS16" — estaria ERRADO: dos 73 emojis da
lista, TRÊS exigem o VS16 (❤️‍🔥, 🤷‍♂️, 🤷‍♀️). Por isso a normalização compara
IGNORANDO o VS16 e devolve a forma exata que a lista registra, o que conserta os
dois sentidos (sobrou marcador / faltou marcador). Ver `normalize_reaction`.

Atrás da flag `TELEGRAM_REACTIONS_ENABLED` — ver bot/config.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram.constants import ReactionEmoji


logger = logging.getLogger("kobe.reactions")

# Emojis default. Configuráveis por .env (ver bot/config.py) pra trocar sem obra
# — a lista de emojis aceitos é do Telegram e pode mudar sem aviso.
DEFAULT_RECEIVED = "👀"
DEFAULT_TRANSCRIBED = "✍"

# VARIATION SELECTOR-16: marcador invisível que pede "desenhe colorido". É o
# estopim do bug — a lista do Bot API guarda uns emojis com ele e outros sem.
# Escrito como `chr()` de propósito: literal, ele seria um caractere INVISÍVEL
# no código — exatamente o que tornou este bug difícil de enxergar.
_VS16 = chr(0xFE0F)

# Lista de reações permitidas. Vem da própria python-telegram-bot em vez de ser
# digitada à mão aqui: quando o Telegram muda a lista, ela chega junto com a
# atualização da lib, e não fica uma cópia velha apodrecendo no nosso código.
ALLOWED_REACTIONS = frozenset(member.value for member in ReactionEmoji)

# Chave = forma sem VS16, valor = forma EXATA que o Bot API aceita. É o que
# permite consertar os dois sentidos com uma consulta só.
_CANONICAL = {emoji.replace(_VS16, ""): emoji for emoji in ALLOWED_REACTIONS}

# Tasks fire-and-forget: sem manter a referência, o GC pode coletar a task antes
# de ela rodar (asyncio guarda só referência fraca). Mesmo padrão do handler.
_TASKS: set = set()


def normalize_reaction(emoji: Optional[str]) -> Optional[str]:
    """Devolve a forma que o Bot API aceita, ou None se não for reação válida.

    Três saídas, e a distinção entre elas importa:
    - None/vazio → None. Estágio desligado DE PROPÓSITO pelo .env; não é erro.
    - emoji da lista (com ou sem VS16) → a forma exata que o Telegram registra.
    - qualquer outra coisa → None. Quem chama decide o que fazer (avisar, cair
      no default), porque a política de fallback não é deste módulo.
    """
    if not emoji:
        return None
    cleaned = emoji.strip()
    if not cleaned:
        return None
    return _CANONICAL.get(cleaned.replace(_VS16, ""))


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

    Última linha de defesa contra o bug de 2026-08-20: normaliza e confere ANTES
    de falar com o Telegram. Emoji que a lista não reconhece não vira chamada —
    já sabemos que a API recusaria, e a recusa seria muda.
    """
    if message_id is None:
        return
    normalized = normalize_reaction(emoji)
    if normalized is None:
        if emoji and emoji.strip():
            logger.warning(
                "reactions: emoji %r não está na lista de reações do Bot API — "
                "nenhuma reação enviada em chat=%s msg=%s. Use um dos permitidos "
                "(ver telegram.constants.ReactionEmoji).",
                emoji, chat_id, message_id,
            )
        return
    task = asyncio.create_task(set_reaction(bot, chat_id, message_id, normalized))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
