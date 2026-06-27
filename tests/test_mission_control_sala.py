#!/usr/bin/env python3
"""Testes da sala estrategista do Mission Control — partes puras + layout + guards.

Cobrem: nome da sala, prompt de estrategista + brief, paths/layout (workspace),
flag/tuning, find_sala_ativa, e os guards de abrir_sala que retornam ANTES de
spawnar worker (flag off, objetivo vazio, limite). O launch real da sala (tmux/
claude) é validado no prod VPS atrás de flag — o bot não roda no dev VPS.

Sem rede, sem tmux real (cleanup degrada pra no-op se tmux não responde).

Rodar:

    .venv/bin/python tests/test_mission_control_sala.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.sala import state as sala_state  # noqa: E402
from bot.mission_control import sala_dispatch, sala_prompt, storage  # noqa: E402


def _ok(name: str) -> None:
    print(f"  ok: {name}")


def _set_env(**kv):
    """Seta envs e devolve um restore()."""
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    def restore():
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return restore


# --- nome da sala ------------------------------------------------------

def test_sala_name() -> None:
    assert sala_prompt.sala_name("abcd1234", "migracao-pg") == "mission-migracao-pg-abcd1234"
    assert sala_prompt.sala_name("abcd1234", "") == "mission-abcd1234"
    # short_id é sempre o último segmento (a faxina extrai por rsplit)
    assert sala_prompt.sala_name("abcd1234", "x").rsplit("-", 1)[-1] == "abcd1234"
    _ok("sala_name")


# --- prompt + brief ----------------------------------------------------

def test_strategist_prompt() -> None:
    p = sala_prompt.build_strategist_system_prompt(
        kobe_home=Path("/home/felipe/kobe"), objetivo="analisa a pesquisa dos alunos",
        missao_id="2026-06-26-pesquisa", workspace_rel="workspace")
    assert "ESTRATEGISTA" in p
    assert "analisa a pesquisa dos alunos" in p
    assert "2026-06-26-pesquisa" in p
    assert "workspace/raciocinio.md" in p
    # prefixos de notify da missão
    for glyph in ("🧭 [mission]", "💡 [mission]", "🤝 [mission]", "🟡 [mission]", "🟢 [mission]"):
        assert glyph in p, glyph
    # handoff é condicional + semi-manual
    assert "handoff-brief.md" in p
    assert "PARE" in p or "espera o operador" in p
    # bypass de verdade, sem rito de 4 etapas do Coder
    assert "bypass" in p.lower()
    _ok("strategist_prompt")


def test_mission_brief() -> None:
    b = sala_prompt.build_mission_brief(
        objetivo="pensar a migração", sala="mission-x-abcd1234",
        missao_id="2026-06-26-migracao", workspace_rel="workspace")
    assert "pensar a migração" in b
    assert "mission-x-abcd1234" in b
    assert "2026-06-26-migracao" in b
    _ok("mission_brief")


# --- layout / paths ----------------------------------------------------

def test_paths_and_workspace() -> None:
    with tempfile.TemporaryDirectory() as d:
        kh = Path(d)
        mid = "2026-06-26-teste"
        assert storage.path_sala_json(kh, mid).name == "sala.json"
        assert storage.path_sala_sysprompt(kh, mid).name == "sala.sysprompt.txt"
        assert storage.workspace_dir(kh, mid).name == "workspace"
        ws = storage.ensure_workspace(kh, mid)
        assert ws.is_dir()
        assert (ws / "rascunhos").is_dir()
        # idempotente
        storage.ensure_workspace(kh, mid)
        assert ws.is_dir()
    _ok("paths_and_workspace")


# --- flag + tuning -----------------------------------------------------

def test_flag_and_tuning() -> None:
    r = _set_env(MISSION_CONTROL_SALA_ENABLED="true")
    try:
        assert sala_dispatch.sala_enabled() is True
    finally:
        r()
    r = _set_env(MISSION_CONTROL_SALA_ENABLED=None)
    try:
        assert sala_dispatch.sala_enabled() is False  # default off
    finally:
        r()
    r = _set_env(MISSION_CONTROL_MAX_SALAS="5")
    try:
        assert sala_dispatch._max_salas() == 5
    finally:
        r()
    # valor inválido cai no default
    r = _set_env(MISSION_CONTROL_MAX_SALAS="abc")
    try:
        assert sala_dispatch._max_salas() == sala_dispatch._DEFAULT_MAX_SALAS
    finally:
        r()
    _ok("flag_and_tuning")


# --- find_sala_ativa ---------------------------------------------------

def _mk_sala(kh: Path, mid: str, *, chat_id: int, thread_id, status: str) -> None:
    storage.missao_dir(kh, mid).mkdir(parents=True, exist_ok=True)
    sala_state.write_state(storage.path_sala_json(kh, mid), {
        "missao_id": mid, "session_id": mid, "sala_name": f"mission-{mid}",
        "chat_id": chat_id, "thread_id": thread_id, "status": status,
    })


def test_find_sala_ativa() -> None:
    with tempfile.TemporaryDirectory() as d:
        kh = Path(d)
        _mk_sala(kh, "2026-06-26-a", chat_id=10, thread_id=5, status="running")
        _mk_sala(kh, "2026-06-26-b", chat_id=10, thread_id=5, status="encerrada")
        _mk_sala(kh, "2026-06-26-c", chat_id=99, thread_id=None, status="running")
        # acha a ativa do tópico (10,5)
        got = sala_dispatch.find_sala_ativa(kh, 10, 5)
        assert got and got["missao_id"] == "2026-06-26-a", got
        # tópico sem sala ativa → None
        assert sala_dispatch.find_sala_ativa(kh, 10, 999) is None
        # chat raiz (thread None)
        assert sala_dispatch.find_sala_ativa(kh, 99, None)["missao_id"] == "2026-06-26-c"
    _ok("find_sala_ativa")


# --- guards de abrir_sala (retornam antes de spawnar) ------------------

def test_abrir_sala_guards() -> None:
    with tempfile.TemporaryDirectory() as d:
        kh = Path(d)
        # flag off → sala_disabled (sem spawn)
        r = _set_env(MISSION_CONTROL_SALA_ENABLED=None)
        try:
            res = sala_dispatch.abrir_sala(kobe_home=kh, objetivo="x", chat_id=1, thread_id=None)
            assert res.get("error") == "sala_disabled", res
        finally:
            r()
        # flag on + objetivo vazio → objetivo_vazio
        r = _set_env(MISSION_CONTROL_SALA_ENABLED="true")
        try:
            res = sala_dispatch.abrir_sala(kobe_home=kh, objetivo="   ", chat_id=1, thread_id=None)
            assert res.get("error") == "objetivo_vazio", res
            # limite atingido → limite_salas (cria 1 ativa, max=1)
            _mk_sala(kh, "2026-06-26-ativa", chat_id=1, thread_id=None, status="running")
            r2 = _set_env(MISSION_CONTROL_MAX_SALAS="1")
            try:
                res = sala_dispatch.abrir_sala(kobe_home=kh, objetivo="tema novo",
                                               chat_id=2, thread_id=None)
                assert res.get("error") == "limite_salas", res
            finally:
                r2()
        finally:
            r()
    _ok("abrir_sala_guards")


# --- render_sala_ativa (ciência read-only pro Hal) ---------------------

def test_render_sala_ativa() -> None:
    with tempfile.TemporaryDirectory() as d:
        kh = Path(d)
        _mk_sala(kh, "2026-06-26-ana", chat_id=7, thread_id=3, status="running")
        # flag off → None (sem ciência)
        r = _set_env(MISSION_CONTROL_SALA_ENABLED=None)
        try:
            assert sala_dispatch.render_sala_ativa(kh, 7, 3) is None
        finally:
            r()
        # flag on + sala ativa no tópico → string com o id + instrução de não-repasse
        r = _set_env(MISSION_CONTROL_SALA_ENABLED="true")
        try:
            line = sala_dispatch.render_sala_ativa(kh, 7, 3)
            assert line and "2026-06-26-ana" in line, line
            assert "NÃO repasse" in line
            assert "EXPLÍCITO" in line
            # tópico sem sala → None
            assert sala_dispatch.render_sala_ativa(kh, 7, 999) is None
        finally:
            r()
    _ok("render_sala_ativa")


# --- encerrar_sala (ato explícito; status terminal) --------------------

def test_encerrar_sala() -> None:
    from bot.sala import tmux as _tmux
    with tempfile.TemporaryDirectory() as d:
        kh = Path(d)
        mid = "2026-06-26-fim"
        _mk_sala(kh, mid, chat_id=1, thread_id=None, status="running")
        # tmux.kill_session monkeypatchado (não depende de tmux real)
        orig = _tmux.kill_session
        _tmux.kill_session = lambda name: type("R", (), {"returncode": 0})()
        try:
            res = sala_dispatch.encerrar_sala(kobe_home=kh, missao_id=mid)
        finally:
            _tmux.kill_session = orig
        assert res.get("ok") is True, res
        assert sala_state.read_state(storage.path_sala_json(kh, mid))["status"] == "encerrada"
        # missão inexistente → erro
        assert sala_dispatch.encerrar_sala(kobe_home=kh, missao_id="nao-existe").get("error")
    _ok("encerrar_sala")


# --- worker start (wiring), tmux monkeypatchado --------------------------

def test_worker_start_wiring() -> None:
    """O worker _start lê o sala.json, escreve sysprompt+brief, abre a sala e
    patcha o estado — sem tmux real (open_sala/monitor_sala/_notify fakeados).
    Pega desalinhamento de campos entre o schema do dispatcher e o worker."""
    from bot.sala import room as _room
    from bot.mission_control import sala_worker

    with tempfile.TemporaryDirectory() as d:
        kh = Path(d)
        mid = "2026-06-26-wiring"
        storage.missao_dir(kh, mid).mkdir(parents=True, exist_ok=True)
        sala_json = storage.path_sala_json(kh, mid)
        # schema idêntico ao que abrir_sala grava
        sala_state.write_state(sala_json, {
            "missao_id": mid, "session_id": "sid-1", "short_id": "wiring00",
            "slug": "", "sala_name": "mission-wiring00",
            "cwd": str(kh), "objetivo": "tema de teste",
            "chat_id": 1, "thread_id": None, "status": "starting",
            "pid": None, "worker_pid": None, "turn_count": 0,
            "pending_input": None,
        })

        captured = {}

        def fake_open(spec, *, launcher_path, **kw):
            captured["spec"] = spec
            captured["launcher_path"] = launcher_path
            return _room.OpenResult(ok=True, claude_pid=4242)

        def fake_monitor(state_path, sala, **kw):
            captured["monitored"] = sala
            return 0

        orig_open, orig_mon = _room.open_sala, _room.monitor_sala
        orig_notify = sala_worker._notify
        _room.open_sala = fake_open
        _room.monitor_sala = fake_monitor
        sala_worker._notify = lambda *a, **k: None
        try:
            rc = sala_worker._start(kh, sala_json)
        finally:
            _room.open_sala, _room.monitor_sala = orig_open, orig_mon
            sala_worker._notify = orig_notify

        assert rc == 0, rc
        # sysprompt + brief foram escritos
        assert storage.path_sala_sysprompt(kh, mid).is_file()
        assert (storage.missao_dir(kh, mid) / "sala-brief.md").is_file()
        # workspace criado
        assert storage.workspace_dir(kh, mid).is_dir()
        # spec sem settings (bypass — sem guard)
        assert captured["spec"].settings_path is None
        assert captured["spec"].sala_name == "mission-wiring00"
        # estado patchado pra running + pid do claude
        st = sala_state.read_state(sala_json)
        assert st["status"] == "running", st
        assert st["pid"] == 4242
        assert captured["monitored"] == "mission-wiring00"
    _ok("worker_start_wiring")


def main() -> int:
    print("test_mission_control_sala:")
    test_sala_name()
    test_strategist_prompt()
    test_mission_brief()
    test_paths_and_workspace()
    test_flag_and_tuning()
    test_find_sala_ativa()
    test_abrir_sala_guards()
    test_render_sala_ativa()
    test_encerrar_sala()
    test_worker_start_wiring()
    print("TODOS OS TESTES PASSARAM ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
