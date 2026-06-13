"""Sistema de Alertas — capacidade proativa do agente.

O operador pede em linguagem natural ("me lembra toda terça…", "todo dia
7h faça X") e o Kobe passa a disparar sozinho no horário. Capacidade
CORE (não plugin). Reusa o daemon Keyko (vide `bot/keyko/`) como base —
Alertas é a 2ª `Source` do Keyko (a 1ª é Missões).

Princípio reitor: a lógica determinística (quando disparar, estado,
transições, escalonamento) mora no código Python. O Claude/Hal só é
invocado pra linguagem (traduzir pedido→YAML, redigir o lembrete, julgar
"já marquei"). Código é dono do estado.

Brainstorm/decisões: `user-data/knowledge/kobe/brainstorms/sistema-alertas.md`.
"""

from bot.alertas.models import (
    Acao,
    Agenda,
    Alerta,
    Canal,
    Confirmacao,
    Estado,
    Evento,
    Limites,
    StatusAlerta,
    TIPOS_COMANDO,
    TipoEvento,
)

__all__ = [
    "Acao",
    "Agenda",
    "Alerta",
    "Canal",
    "Confirmacao",
    "Estado",
    "Evento",
    "Limites",
    "StatusAlerta",
    "TIPOS_COMANDO",
    "TipoEvento",
]
