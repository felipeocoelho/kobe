#!/usr/bin/env python3
"""Testes do núcleo de sala (bot/sala/) — a lógica PURA + o state atômico.

Cobrem o que dá pra exercer sem tmux/claude reais: state I/O com flock,
detecção de pane (busy/last), decisão de fim-de-turno, montagem do launcher,
e as decisões de faxina/contagem (should_kill, is_active, count_active).

O comportamento que precisa de tmux/claude vivos (open_sala, monitor_sala,
resume_deliver) NÃO é coberto aqui — é validado no prod VPS (staging) atrás de
flag, como todo comportamento de sala (o bot não roda no dev VPS).

Sem rede, sem tmux: tudo em tmpdir + funções puras + pid_alive injetado.

Rodar:

    .venv/bin/python tests/test_sala_core.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.sala import cleanup, room, state, tmux  # noqa: E402


def _ok(name: str) -> None:
    print(f"  ok: {name}")


# --- state -------------------------------------------------------------

def test_state_roundtrip_and_patch() -> None:
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "sala.json"
        state.write_state(sp, {"session_id": "abc", "turn_count": 0, "status": "starting"})
        got = state.read_state(sp)
        assert got["session_id"] == "abc", got
        assert got["turn_count"] == 0

        merged = state.patch_state(sp, status="running", turn_count=1)
        assert merged["status"] == "running"
        assert merged["turn_count"] == 1
        # campo antigo preservado
        assert merged["session_id"] == "abc"
        # last_activity foi setado automaticamente
        assert merged.get("last_activity"), merged
        # persistiu no disco
        assert state.read_state(sp)["turn_count"] == 1
    _ok("state_roundtrip_and_patch")


def test_state_write_is_atomic_no_leftover_tmp() -> None:
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "s.json"
        state.write_state(sp, {"a": 1})
        # não deve sobrar arquivo .tmp
        assert not (Path(d) / "s.json.tmp").exists()
        assert sp.exists()
    _ok("state_write_is_atomic_no_leftover_tmp")


# --- tmux helpers puros ------------------------------------------------

def test_pane_busy() -> None:
    assert tmux.pane_busy("blah\n  esc to interrupt  \n>") is True
    assert tmux.pane_busy("idle, waiting for input\n>") is False
    assert tmux.pane_busy("") is False  # pane vazio = não-busy
    _ok("pane_busy")


def test_extract_pane_last() -> None:
    pane = "● primeiro\nruido\n● segundo passo\n> "
    assert tmux.extract_pane_last(pane) == "segundo passo", tmux.extract_pane_last(pane)
    assert tmux.extract_pane_last("sem bullets aqui") is None
    assert tmux.extract_pane_last("") is None
    _ok("extract_pane_last")


# --- decisão de fim de turno -------------------------------------------

def test_turn_is_over() -> None:
    # viu busy + 2 idle seguidas → acabou
    assert room.turn_is_over(saw_busy=True, idle_streak=2, elapsed=120) is True
    # viu busy + só 1 idle → ainda não (janela entre tool calls)
    assert room.turn_is_over(saw_busy=True, idle_streak=1, elapsed=120) is False
    # nunca viu busy mas já passou o min → turno trivial terminou
    assert room.turn_is_over(saw_busy=False, idle_streak=1, elapsed=61) is True
    # nunca viu busy e ainda dentro do min → espera (boot)
    assert room.turn_is_over(saw_busy=False, idle_streak=1, elapsed=10) is False
    _ok("turn_is_over")


# --- launcher ----------------------------------------------------------

def test_build_launcher_command_bypass_no_settings() -> None:
    spec = room.SalaSpec(
        sala_name="mission-abc",
        cwd=Path("/home/felipe/projetos/kobe"),
        session_id="11111111-2222",
        sysprompt_path=Path("/tmp/sp.txt"),
        launch_prompt="leia o brief",
        settings_path=None,
    )
    cmd = room.build_launcher_command(spec)
    assert "--permission-mode bypassPermissions" in cmd
    assert "--remote-control mission-abc" in cmd
    assert "--session-id 11111111-2222" in cmd
    assert "--append-system-prompt-file /tmp/sp.txt" in cmd
    assert "leia o brief" in cmd
    # sem settings_path → NÃO injeta --settings (sala em bypass de verdade)
    assert "--settings" not in cmd
    assert cmd.startswith("#!/bin/bash")
    _ok("build_launcher_command_bypass_no_settings")


def test_build_launcher_command_with_settings() -> None:
    spec = room.SalaSpec(
        sala_name="coder-x",
        cwd=Path("/repo"),
        session_id="sid",
        sysprompt_path=Path("/tmp/sp.txt"),
        launch_prompt="go",
        settings_path=Path("/tmp/settings.json"),
    )
    cmd = room.build_launcher_command(spec)
    assert "--settings /tmp/settings.json" in cmd
    _ok("build_launcher_command_with_settings")


# --- cleanup -----------------------------------------------------------

def test_should_kill() -> None:
    now = datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    old = (now - timedelta(hours=50)).isoformat(timespec="seconds")

    # terminal → mata independente de idade
    assert cleanup.should_kill({"status": "dead", "last_activity": fresh},
                               now=now, ttl_hours=24) is True
    # vivo + fresco → não mata
    assert cleanup.should_kill({"status": "running", "last_activity": fresh},
                               now=now, ttl_hours=24) is False
    # vivo + velho além do TTL → mata
    assert cleanup.should_kill({"status": "idle", "last_activity": old},
                               now=now, ttl_hours=24) is True
    # timestamp ruim → não mata (defensivo)
    assert cleanup.should_kill({"status": "idle", "last_activity": "lixo"},
                               now=now, ttl_hours=24) is False
    # dict vazio → não mata
    assert cleanup.should_kill({}, now=now, ttl_hours=24) is False
    # ttl_hours=None (modo Mission Control): NUNCA mata por idade — só terminal
    assert cleanup.should_kill({"status": "idle", "last_activity": old},
                               now=now, ttl_hours=None) is False
    assert cleanup.should_kill({"status": "encerrada", "last_activity": fresh},
                               now=now, ttl_hours=None) is True
    _ok("should_kill")


def test_is_active_with_injected_pid() -> None:
    alive = lambda pid: pid == 100
    assert cleanup.is_active({"status": "running", "pid": 100}, pid_alive=alive) is True
    assert cleanup.is_active({"status": "running", "pid": 999}, pid_alive=alive) is False
    # starting sem pid → ativo (ainda subindo)
    assert cleanup.is_active({"status": "starting"}, pid_alive=alive) is True
    # idle → não ativo
    assert cleanup.is_active({"status": "idle", "pid": 100}, pid_alive=alive) is False
    _ok("is_active_with_injected_pid")


def test_count_active() -> None:
    with tempfile.TemporaryDirectory() as d:
        files = []
        specs = [
            {"session_id": "a", "status": "running", "pid": 100},   # ativo
            {"session_id": "b", "status": "running", "pid": 999},   # pid morto
            {"session_id": "c", "status": "idle", "pid": 100},      # idle
            {"status": "running", "pid": 100},                       # sem session_id → ignora
        ]
        for i, s in enumerate(specs):
            f = Path(d) / f"{i}.json"
            state.write_state(f, s)
            files.append(f)
        n, active = cleanup.count_active(files, pid_alive_fn=lambda pid: pid == 100)
        assert n == 1, (n, active)
        assert active[0]["session_id"] == "a"
    _ok("count_active")


# --- monitor: guarda de fecho intencional (não confunde com morte) -----

def test_monitor_terminal_guard() -> None:
    """status já terminal (operador encerrou) → monitor sai quieto, sem on_death."""
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "sala.json"
        state.write_state(sp, {"worker_pid": None, "status": "encerrada"})
        deaths = []
        rc = room.monitor_sala(sp, "mission-x", poll_s=0,
                               on_death=lambda *a: deaths.append(a), my_pid=None)
        assert rc == 0, rc
        assert deaths == [], deaths  # NÃO reportou morte
    _ok("monitor_terminal_guard")


def test_monitor_reports_death_when_not_terminal() -> None:
    """sala sumiu e status NÃO é terminal → reporta morte (on_death) e sai 1."""
    import bot.sala.tmux as _tmux
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "sala.json"
        state.write_state(sp, {"worker_pid": None, "status": "running"})
        deaths = []
        orig = _tmux.has_session
        _tmux.has_session = lambda name: False  # sala caiu
        try:
            rc = room.monitor_sala(sp, "mission-x", poll_s=0,
                                   on_death=lambda *a: deaths.append(a), my_pid=None)
        finally:
            _tmux.has_session = orig
        assert rc == 1, rc
        assert len(deaths) == 1, deaths
        assert state.read_state(sp)["status"] == "dead"
    _ok("monitor_reports_death_when_not_terminal")


def main() -> int:
    print("test_sala_core:")
    test_state_roundtrip_and_patch()
    test_state_write_is_atomic_no_leftover_tmp()
    test_pane_busy()
    test_extract_pane_last()
    test_turn_is_over()
    test_build_launcher_command_bypass_no_settings()
    test_build_launcher_command_with_settings()
    test_should_kill()
    test_is_active_with_injected_pid()
    test_count_active()
    test_monitor_terminal_guard()
    test_monitor_reports_death_when_not_terminal()
    print("TODOS OS TESTES PASSARAM ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
