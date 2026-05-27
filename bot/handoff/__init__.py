"""Destilador de handoff doc do Hal.

Implementa o Bloco C (parcial) do "handoff universal" — vide
`CLAUDE.md` do Kobe, seção "Handoff entre canais Claude".

Fluxo:
- `/handoff` (handler) → `destilar_sessao` → escreve em
  `<kobe_home>/.local/handoffs/<topic-slug>/handoff.md` (anterior, se
  existir, vai pra `arquivados/` antes).
- `/nova` (handler) → destila em background fire-and-forget → escreve
  direto em `arquivados/` (a sessão está indo embora).

Custo por chamada: 1 invocação de `claude -p` (típico $0,01-0,05,
latência 5-30s). Por isso é assíncrono em ambos os gatilhos.
"""

from bot.handoff.destilador import (
    DestiladorError,
    HandoffResult,
    destilar_sessao,
)
from bot.handoff.paths import (
    archive_path_for_session,
    active_handoff_path,
    rotate_active_to_archive,
)

__all__ = [
    "DestiladorError",
    "HandoffResult",
    "destilar_sessao",
    "active_handoff_path",
    "archive_path_for_session",
    "rotate_active_to_archive",
]
