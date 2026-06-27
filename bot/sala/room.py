"""Núcleo da sala: abertura, monitor, porteiro de prontidão, entrega-com-
confirmação e resume — extraído de `coder_worker.py`, sem opinião sobre quem usa.

Toolkit composável (não um framework): o caller (Mission Control, Coder no
futuro) escreve seu system prompt num arquivo, monta o `SalaSpec`, e compõe
`open_sala` + `monitor_sala` (start) ou `resume_deliver` + `monitor_sala`
(resume). As mensagens ao operador são callbacks — o núcleo não conhece
prefixos `[coder]`/`[mission]`.

As partes delicadas (incident-hardened do Coder, 2026-06-23) estão preservadas:
- **porteiro de prontidão** (`wait_pane_idle`): não digita numa TUI ocupada —
  teclas mandadas a uma sala processando caem no vão.
- **entrega-com-confirmação** (`deliver_to_sala`): manda input e confirma que o
  turno ARRANCOU (vira busy); reenvia 1× antes de desistir.
- **owner-check** (no monitor): um worker velho cede a sessão a um worker mais
  novo (resume disparou outro) em vez de gravar status por cima.
- **idle exige 2 leituras** seguidas (evita a janela curta entre tool calls).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from bot.sala import state as _state
from bot.sala import tmux


logger = logging.getLogger("kobe.sala.room")


# Defaults — mesmos números provados no Coder.
POLL_SECONDS = 8
MONITOR_MAX_SECONDS = 6 * 3600   # backstop: não deixa um worker imortal
HEARTBEAT_SECONDS = 600          # 10 min de silêncio → "ainda trabalhando"
IDLE_MIN_ELAPSED = 60            # turno trivial sem nunca ficar busy: só encerra após isto

# Status terminais: o monitor NÃO sobrescreve (idle) nem reporta morte sobre eles.
# Cobre o fecho INTENCIONAL do operador (`encerrada`) — a sala só fecha por ato
# dele (ressalva Mission Control); o monitor vendo a sala sumir após um
# `encerrada` é fecho normal, não crash.
TERMINAL_STATUSES = frozenset(
    {"encerrada", "terminated", "crashed", "dead", "failed", "merged"}
)


# --- spec da sala -------------------------------------------------------

@dataclass
class SalaSpec:
    """Tudo que o núcleo precisa pra ABRIR uma sala. O caller constrói o
    `sala_name` (com seu prefixo) e escreve o `sysprompt_path` antes."""
    sala_name: str
    cwd: Path
    session_id: str
    sysprompt_path: Path
    launch_prompt: str
    # Gates plugáveis: None = sem `--settings` (sala em bypass de verdade, sem
    # guard hook — o caso do Mission Control). O Coder passa o settings do guard.
    settings_path: Optional[Path] = None
    # Quais envs repassar pra sala (pra kobe-notify/attach lá dentro).
    env_keys: tuple[str, ...] = ("KOBE_CHAT_ID", "KOBE_THREAD_ID", "KOBE_TELEGRAM_BOT_TOKEN")


@dataclass
class OpenResult:
    ok: bool
    claude_pid: Optional[int] = None
    error: Optional[str] = None


class ResumeOutcome(str, Enum):
    DELIVERED = "delivered"     # input pousou e o turno arrancou
    STILL_BUSY = "still_busy"   # sala seguiu ocupada — input PRESERVADO, não digitado
    FAILED = "failed"           # 2 tentativas e o turno não arrancou — input PRESERVADO
    DEAD = "dead"               # a sala não está mais viva


# --- decisão PURA (testável) -------------------------------------------

def turn_is_over(*, saw_busy: bool, idle_streak: int, elapsed: float,
                 min_elapsed: float = IDLE_MIN_ELAPSED) -> bool:
    """O turno terminou? Confirma se: viu busy e agora 2 leituras idle seguidas
    (turno normal que acabou); OU nunca viu busy mas já passou tempo (turno
    trivial que terminou rápido demais pra pegar o busy). Pura — testável sem
    tmux."""
    if saw_busy and idle_streak >= 2:
        return True
    if not saw_busy and elapsed > min_elapsed:
        return True
    return False


# --- abertura da sala ---------------------------------------------------

def build_launcher_command(spec: SalaSpec) -> str:
    """Linha de comando do launcher (bash) que vira o comando da sala tmux.
    Pura (só formata) — testável. `--session-id` dá id ESTÁVEL (recuperável);
    `--append-system-prompt-file` mantém a linha curta (system prompt ~28KB num
    arquivo, não no argv/ps); sem `settings_path` não há `--settings` (bypass)."""
    import shlex
    settings_arg = (
        f"--settings {shlex.quote(str(spec.settings_path))} "
        if spec.settings_path else ""
    )
    return (
        "#!/bin/bash\n"
        f"cd {shlex.quote(str(spec.cwd))}\n"
        f"exec claude --permission-mode bypassPermissions "
        f"--remote-control {shlex.quote(spec.sala_name)} "
        f"--session-id {shlex.quote(spec.session_id)} {settings_arg}"
        f"--append-system-prompt-file {shlex.quote(str(spec.sysprompt_path))} "
        f"{shlex.quote(spec.launch_prompt)}\n"
    )


def open_sala(spec: SalaSpec, *, launcher_path: Path,
              pid_wait_tries: int = 10) -> OpenResult:
    """Escreve o launcher, abre a sala tmux detached e procura o PID do claude.
    NÃO mexe no state (o caller faz, com seus próprios campos). `env_keys` são
    lidos do `os.environ` e injetados na sala."""
    launcher_path = Path(launcher_path)
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text(build_launcher_command(spec), encoding="utf-8")
    launcher_path.chmod(0o755)

    env = {k: os.environ.get(k) for k in spec.env_keys}
    import shlex
    proc = tmux.new_session(
        spec.sala_name, str(spec.cwd), f"bash {shlex.quote(str(launcher_path))}", env=env
    )
    if proc.returncode != 0:
        return OpenResult(ok=False, error=(proc.stderr or "").strip() or "tmux falhou")

    claude_pid = None
    for _ in range(pid_wait_tries):
        claude_pid = tmux.claude_pid_for(spec.sala_name)
        if claude_pid:
            break
        time.sleep(1)
    return OpenResult(ok=True, claude_pid=claude_pid)


# --- porteiro + entrega -------------------------------------------------

def wait_pane_idle(sala: str, *, budget_s: float = 20.0, poll_s: float = 1.5) -> bool:
    """Espera a sala ficar OCIOSA (não-busy) antes de digitar. Retorna True se
    ficou ociosa no orçamento; False se seguiu ocupada (o chamador NÃO digita às
    cegas — preserva o input)."""
    waited = 0.0
    while waited < budget_s:
        if not tmux.pane_busy(tmux.capture_pane(sala)):
            return True
        time.sleep(poll_s)
        waited += poll_s
    return False


def deliver_to_sala(sala: str, text: str, *, confirm_s: float = 8.0) -> bool:
    """Entrega o input e CONFIRMA que pousou: manda send-keys + espera a sala
    VIRAR busy (= o turno começou). Reenvia 1× se não confirmar. Retorna True se
    o turno arrancou; False após 2 tentativas. Pré-condição: pane ocioso."""
    for _attempt in (1, 2):
        tmux.send_keys_literal(sala, text)
        waited = 0.0
        while waited < confirm_s:
            time.sleep(1.0)
            waited += 1.0
            if tmux.pane_busy(tmux.capture_pane(sala)):
                return True
    return False


def resume_deliver(state_path: Path, sala: str, pending: str) -> ResumeOutcome:
    """Compõe porteiro + entrega pra um resume com input novo. NÃO chama
    callbacks (o caller decide o que notificar a partir do outcome) e NÃO inicia
    o monitor (o caller encadeia `monitor_sala`). Atualiza o state conforme o
    outcome (consome `pending_input` só em DELIVERED; preserva nos demais)."""
    if not tmux.has_session(sala):
        return ResumeOutcome.DEAD
    if not pending:
        # Resume sem input novo ("continue de onde parou").
        st = _state.read_state(state_path)
        _state.patch_state(state_path, status="running", pending_input=None,
                           turn_count=(st.get("turn_count") or 0) + 1)
        return ResumeOutcome.DELIVERED
    if not wait_pane_idle(sala):
        _state.patch_state(state_path, status="idle")
        return ResumeOutcome.STILL_BUSY
    if not deliver_to_sala(sala, pending):
        _state.patch_state(state_path, status="idle")
        return ResumeOutcome.FAILED
    st = _state.read_state(state_path)
    _state.patch_state(state_path, status="running", pending_input=None,
                       turn_count=(st.get("turn_count") or 0) + 1)
    return ResumeOutcome.DELIVERED


# --- monitor ------------------------------------------------------------

def monitor_sala(
    state_path: Path,
    sala: str,
    *,
    poll_s: int = POLL_SECONDS,
    heartbeat_s: int = HEARTBEAT_SECONDS,
    max_s: int = MONITOR_MAX_SECONDS,
    on_heartbeat: Optional[Callable[[str, float, dict], None]] = None,
    on_death: Optional[Callable[[str, dict], None]] = None,
    my_pid: Optional[int] = None,
    terminal_statuses: frozenset[str] = TERMINAL_STATUSES,
) -> int:
    """Fica vivo observando a sala DURANTE o turno (o worker não morre no launch).

    - Atualiza status (running/idle) e last_text lendo o capture-pane.
    - Detecta MORTE silenciosa (sala caiu) → marca dead, chama `on_death`, sai 1.
    - Heartbeat: se o turno passa `heartbeat_s` sem encerrar, chama `on_heartbeat`.
    - ENCERRA quando a sala fica idle (turno terminou) → sai 0. A sala segue VIVA
      pro próximo resume.
    - Owner-check: se um worker mais novo assumiu (regravou worker_pid), este
      monitor velho se cala e sai 0.

    Callbacks são best-effort do ponto de vista do núcleo (exceções logadas, não
    propagadas) — quem formata mensagem é o caller.
    """
    my_pid = my_pid if my_pid is not None else os.getpid()
    started = time.monotonic()
    last_hb = started
    saw_busy = False
    idle_streak = 0

    # Margem de boot: o claude leva alguns segundos pra subir; sem isso o monitor
    # poderia ver "idle" no boot e encerrar antes do turno começar.
    time.sleep(poll_s)
    while True:
        # Lê o estado uma vez por volta (owner-check + status terminal).
        try:
            cur = _state.read_state(state_path)
        except Exception:  # noqa: BLE001 — defensivo; na dúvida segue
            cur = {}
        # Owner-check: cedeu a sessão a um worker mais novo? cala e sai.
        owner = cur.get("worker_pid")
        if owner not in (None, my_pid):
            logger.info("monitor velho cedendo sessão ao worker %s (eu=%s)", owner, my_pid)
            return 0
        # Fecho intencional (operador encerrou — direto na sala OU via Hal): o
        # estado já está terminal. Sai quieto, sem reportar morte nem mexer no status.
        if cur.get("status") in terminal_statuses:
            logger.info("monitor: status terminal %r — encerrando quieto", cur.get("status"))
            return 0

        if not tmux.has_session(sala):
            # Pode ser fecho intencional que acabou de marcar terminal entre a
            # leitura acima e agora — relê pra não gritar "caiu" num fecho normal.
            try:
                latest = _state.read_state(state_path)
            except Exception:  # noqa: BLE001
                latest = cur
            if latest.get("status") in terminal_statuses:
                return 0
            st = _state.patch_state(
                state_path, status="dead", exit_code=-1,
                last_text="sala tmux caiu (morte detectada pelo monitor).")
            _safe_cb(on_death, sala, st)
            return 1

        pane = tmux.capture_pane(sala)
        last_text = tmux.extract_pane_last(pane)
        elapsed = time.monotonic() - started

        if tmux.pane_busy(pane):
            saw_busy = True
            idle_streak = 0
            _state.patch_state(state_path, status="running", last_text=last_text)
            if time.monotonic() - last_hb >= heartbeat_s:
                st = _state.read_state(state_path)
                _safe_cb(on_heartbeat, sala, elapsed, st)
                last_hb = time.monotonic()
        else:
            idle_streak += 1
            if turn_is_over(saw_busy=saw_busy, idle_streak=idle_streak, elapsed=elapsed):
                # Não sobrescreve status terminal (fecho intencional) com idle.
                if cur.get("status") not in terminal_statuses:
                    _state.patch_state(state_path, status="idle", last_text=last_text)
                return 0

        if elapsed > max_s:
            # Backstop: solta o worker (a sala continua viva); status fica como está.
            return 0
        time.sleep(poll_s)


def _safe_cb(cb, *args) -> None:
    if cb is None:
        return
    try:
        cb(*args)
    except Exception:  # noqa: BLE001 — callback de notify é nice-to-have
        logger.exception("callback de sala falhou")
