"""Cálculo determinístico de quando (e o quê) um alerta dispara.

Funções PURAS — sem IO, sem mutação. A `AlertasSource` chama
`calcular_proximo(alerta, desde=...)` pra descobrir o próximo
`(quando, acao)` e grava no estado. Toda a "inteligência de agendamento"
mora aqui — o resto do sistema só compara relógio com `proximo_disparo`.

Modelo unificado (vide design):

- one-shot      → `agenda.quando` (ISO). Dispara 1×, vira CONCLUIDO.
- recorrente    → `agenda.abertura` (cron). Dispara e reagenda.
- c/ follow-up  → `abertura` + `cobranca` + `limite` + confirmação.
  O próximo evento de um alerta ABERTO é o MAIS CEDO entre a próxima
  cobrança, o próximo limite e a próxima abertura — o merge dos três
  crons resolve naturalmente a ordem (para de cobrar ao bater limite,
  reabre na próxima abertura).

Fuso: America/Sao_Paulo em tudo. Cron parseado com `croniter` (tz-aware).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from croniter import croniter

from bot.alertas.models import Acao, Alerta, StatusAlerta


OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")


@dataclass(frozen=True)
class ProximoEvento:
    """Resultado do scheduler: quando agir e o que fazer."""

    quando: datetime
    acao: Acao


# --- helpers de cron ----------------------------------------------------

def _ensure_tz(dt: datetime) -> datetime:
    """Garante que o datetime é tz-aware no fuso do operador. Naives são
    interpretados como horário Brasil (entrada do operador é Brasil)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=OPERATOR_TZ)
    return dt.astimezone(OPERATOR_TZ)


def cron_next(expr: str, base: datetime) -> datetime:
    """Próxima ocorrência do cron ESTRITAMENTE após `base`."""
    it = croniter(expr, _ensure_tz(base))
    return it.get_next(datetime)


def parse_iso(value: str) -> datetime:
    """Parseia ISO 8601 do estado/agenda pro fuso do operador."""
    return _ensure_tz(datetime.fromisoformat(value))


# --- cálculo principal --------------------------------------------------

def calcular_proximo(alerta: Alerta, *, desde: datetime) -> Optional[ProximoEvento]:
    """Próximo `(quando, acao)` do alerta, considerando estado atual.

    `desde` é o ponto de referência: o próximo evento é a 1ª ocorrência
    ESTRITAMENTE após `desde`. Tipicamente a source passa `agora` (no
    agendamento inicial / após disparar) — isso pula naturalmente um
    backlog se o daemon ficou fora do ar (dispara 1× e reagenda pro
    futuro, em vez de floodar lembretes atrasados).

    Devolve None quando não há mais nada a agendar (one-shot já disparado,
    alerta pausado/terminal). Pausado é tratado pela source (não chama
    aqui), mas defendemos mesmo assim.
    """
    desde = _ensure_tz(desde)
    estado = alerta.estado

    if alerta.is_terminal() or estado.status == StatusAlerta.PAUSADO.value:
        return None

    # --- alerta SIMPLES (sem confirmação) ---
    if not alerta.aguarda_confirmacao:
        if alerta.agenda.is_one_shot:
            quando = parse_iso(alerta.agenda.quando)  # type: ignore[arg-type]
            # Já disparou? one-shot não reagenda.
            if estado.ultimo_disparo is not None:
                return None
            return ProximoEvento(quando=quando, acao=Acao.DISPARAR)
        if alerta.agenda.abertura:
            return ProximoEvento(
                quando=cron_next(alerta.agenda.abertura, desde),
                acao=Acao.DISPARAR,
            )
        return None

    # --- alerta COM confirmação (ciclo de vida) ---
    # Dormindo (CONFIRMADO/EXPIRADO) ou ainda não aberto → espera abertura.
    if estado.status != StatusAlerta.ABERTO.value:
        if not alerta.agenda.abertura:
            return None
        return ProximoEvento(
            quando=cron_next(alerta.agenda.abertura, desde),
            acao=Acao.ABRIR,
        )

    # ABERTO: merge dos três crons; o mais cedo vence.
    candidatos: list[ProximoEvento] = []
    if alerta.agenda.cobranca:
        candidatos.append(
            ProximoEvento(cron_next(alerta.agenda.cobranca, desde), Acao.COBRAR)
        )
    if alerta.agenda.limite:
        candidatos.append(
            ProximoEvento(cron_next(alerta.agenda.limite, desde), Acao.EXPIRAR)
        )
    if alerta.agenda.abertura:
        candidatos.append(
            ProximoEvento(cron_next(alerta.agenda.abertura, desde), Acao.ABRIR)
        )

    if not candidatos:
        return None
    # Empate de horário: EXPIRAR tem prioridade (fecha a janela antes de
    # cobrar de novo no mesmo instante); depois ABRIR; COBRAR por último.
    prioridade = {Acao.EXPIRAR: 0, Acao.ABRIR: 1, Acao.COBRAR: 2}
    candidatos.sort(key=lambda e: (e.quando, prioridade[e.acao]))
    return candidatos[0]
