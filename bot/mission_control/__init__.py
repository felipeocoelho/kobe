"""Sistema de Missões — coordenação de trabalho multi-tarefa do agente.

Conceito introduzido em 2026-05-23 (vide briefing em
`.local/missao-orquestrador-briefing.md` e brainstorm completo em
`user-data/knowledge/kobe/brainstorms/agente-orquestrador-missoes.md`).

Operador abre uma missão via `/missao <descrição>` num tópico. O orquestrador
(Claude rodando em background, hibernando entre invocações) planeja, dispara
subtarefas via `kobe-dispatch claude -p`, e o daemon Keyko (vide `bot/keyko/`)
observa o `eventos.jsonl` e:

  1. atualiza a mensagem-painel no Telegram (sem LLM)
  2. acorda o orquestrador em marcos relevantes (com circuit breaker)

Fonte da verdade do estado: arquivos em `user-data/missoes/<id>/`.
"""

from bot.mission_control.models import (
    Evento,
    Missao,
    StatusMissao,
    StatusTarefa,
    Tarefa,
    TIPOS_MARCO,
    TipoEvento,
)

__all__ = [
    "Evento",
    "Missao",
    "StatusMissao",
    "StatusTarefa",
    "Tarefa",
    "TIPOS_MARCO",
    "TipoEvento",
]
