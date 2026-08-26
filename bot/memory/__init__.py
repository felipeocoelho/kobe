"""Memória de trabalho do Kobe (Highlander — Frente 0).

Casa própria da MEMÓRIA, separada da gerência de CONVERSAS.

Regra de ouro (plano Highlander v4 §1): a memória de trabalho pode CONSUMIR
dado de conversa, mas **conversa nunca monta a janela**. Por isso a janela
imediata (`working_set`) — que era filada dentro do pacote do Chat Manager
mesmo sem tocar `conversations` — passou a morar aqui. Foi esse desacoplamento
que deixou a memória sobreviver inteira quando o Chat Manager foi aposentado
(2026-08-25): o que morreu foi conversa, não memória.

Camadas (a crescer nas próximas frentes):
- `working_set` — janela imediata: últimas ~10 min / N msgs DESTE tópico, crua.
- (Frente 1.2) núcleo curado global: identidade + fatos duráveis, teto + esquecimento.
- (Frente 1.1) sinais de grounding baratos resolvidos no código (há N min, estado de bg).
"""

from bot.memory.aging import (
    carimbo,
    estado_com_idade,
    humanizar_idade,
    parse_ts,
)
from bot.memory.background_state import render_background_state
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
    "carimbo",
    "estado_com_idade",
    "humanizar_idade",
    "parse_ts",
    "CURATED_CORE_CHAR_LIMIT",
    "IMMEDIATE_HARD_CAP",
    "IMMEDIATE_MIN_COUNT",
    "IMMEDIATE_WINDOW_SECONDS",
    "get_immediate_messages",
    "load_curated_core",
    "render_background_state",
    "render_grounding_signals",
]
