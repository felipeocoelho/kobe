"""Faxina de salas abandonadas (TTL) e contagem de salas ativas.

Custo de sala (decisão 5 do plano Mission Control): uma sala viva PARADA custa
~$0 em tokens — cota só pesa durante um turno ativo; parada é só processo/
memória. Então o guard é: (a) faxina de salas em estado terminal ou inativas há
mais que o TTL, e (b) um teto de salas ATIVAS concorrentes (turnos rodando).

As decisões são puras e testáveis (`should_kill`, `is_active`); o IO (listar
tmux, ler states, matar sessão, checar PID vivo) fica nas funções com efeito.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from bot.sala import tmux


# Status considerados terminais (sala pode ser morta na faxina).
TERMINAL_STATUSES = frozenset(
    {"dead", "failed", "terminated", "crashed", "merged", "encerrada"}
)


# --- decisões PURAS (testáveis) ----------------------------------------

def should_kill(state: dict, *, now: datetime, ttl_hours: Optional[float],
                terminal: frozenset[str] = TERMINAL_STATUSES) -> bool:
    """Mata se status é terminal OU (quando `ttl_hours` é dado) last_activity é
    mais velho que o TTL. `state` vazio → False (não mexe no que não conhece).

    **`ttl_hours=None` desliga o ramo por idade** — só mata por status terminal.
    É o modo do Mission Control: a sala só fecha por ato explícito do operador
    (ressalva), nunca por inatividade; a faxina só reaproveita o processo tmux de
    salas que o operador JÁ encerrou (ou que morreram)."""
    if not state:
        return False
    if state.get("status") in terminal:
        return True
    if ttl_hours is None:
        return False
    la = state.get("last_activity")
    if not la:
        return False
    try:
        age_h = (now - datetime.fromisoformat(la)).total_seconds() / 3600
    except Exception:  # noqa: BLE001 — timestamp ruim não justifica matar
        return False
    return age_h > ttl_hours


def is_active(state: dict, *, pid_alive: Callable[[int], bool]) -> bool:
    """Sessão conta como ativa? status starting/running E (sem pid OU pid vivo).
    `pid_alive` é injetado pra testar sem processos reais."""
    if state.get("status") not in ("starting", "running"):
        return False
    pid = state.get("pid") or state.get("worker_pid")
    if not pid:
        return True
    return pid_alive(int(pid))


# --- IO ----------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """`kill -0`: True se o processo existe e é sinalizável."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _load_states(state_files: Iterable[Path]) -> list[dict]:
    out: list[dict] = []
    for sp in state_files:
        try:
            out.append(json.loads(Path(sp).read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def count_active(state_files: Iterable[Path], *,
                 pid_alive_fn: Callable[[int], bool] = pid_alive) -> tuple[int, list[dict]]:
    """Conta sessões ativas (starting/running, PID vivo) nos states dados.
    Devolve (n, lista_de_states_ativos) — o caller monta a mensagem de erro."""
    active = [s for s in _load_states(state_files)
              if s.get("session_id") and is_active(s, pid_alive=pid_alive_fn)]
    return len(active), active


def cleanup_stale_salas(*, prefix: str, ttl_hours: Optional[float],
                        state_files: Iterable[Path],
                        short_id_of_sala: Optional[Callable[[str], str]] = None) -> list[str]:
    """Mata salas tmux `<prefix>*` em estado terminal ou inativas há mais que o
    TTL. Best-effort — nunca levanta. Devolve a lista de salas mortas.

    `short_id_of_sala`: extrai o short_id do nome da sala pra casar com o state.
    Default: último segmento após '-' (`<prefix><slug>-<short>` ou `<prefix><short>`)."""
    if short_id_of_sala is None:
        short_id_of_sala = lambda name: name.rsplit("-", 1)[-1]

    try:
        salas = [s for s in tmux.list_sessions() if s.startswith(prefix)]
        if not salas:
            return []
        by_short = {s["short_id"]: s for s in _load_states(state_files) if s.get("short_id")}
        now = datetime.now(timezone.utc)
        mortas: list[str] = []
        for sala in salas:
            st = by_short.get(short_id_of_sala(sala))
            if st is None:
                continue  # sem state conhecido — não é nossa / não mexe
            if should_kill(st, now=now, ttl_hours=ttl_hours):
                tmux.kill_session(sala)
                mortas.append(sala)
        return mortas
    except Exception:  # noqa: BLE001 — faxina é best-effort
        return []
