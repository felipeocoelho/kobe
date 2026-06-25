"""Memória de trabalho do Kobe (Highlander — Frente 0).

Casa própria da MEMÓRIA, separada da gerência de CONVERSAS (`bot/chat_manager/`).

Regra de ouro (plano Highlander v4 §1): a memória de trabalho pode CONSUMIR
dado de conversa, mas **conversa nunca monta a janela**. Por isso a janela
imediata (`working_set`) — que era filada dentro de `chat_manager/context.py`
mesmo sem tocar `conversations` — passa a morar aqui. Os blocos de conversa
(quente/frio/relações) continuam em `chat_manager`, porque são de conversa.

Camadas (a crescer nas próximas frentes):
- `working_set` — janela imediata: últimas ~10 min / N msgs DESTE tópico, crua.
- (Frente 1.2) núcleo curado global: identidade + fatos duráveis, teto + esquecimento.
- (Frente 1.1) sinais de grounding baratos resolvidos no código (há N min, estado de bg).
"""

from bot.memory.curated_core import (
    CURATED_CORE_CHAR_LIMIT,
    load_curated_core,
)
from bot.memory.grounding import render_grounding_signals
from bot.memory.working_set import (
    IMMEDIATE_HARD_CAP,
    IMMEDIATE_MIN_COUNT,
    IMMEDIATE_WINDOW_SECONDS,
    get_immediate_messages,
)

__all__ = [
    "CURATED_CORE_CHAR_LIMIT",
    "IMMEDIATE_HARD_CAP",
    "IMMEDIATE_MIN_COUNT",
    "IMMEDIATE_WINDOW_SECONDS",
    "get_immediate_messages",
    "load_curated_core",
    "render_grounding_signals",
]
