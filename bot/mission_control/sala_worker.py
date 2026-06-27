#!/usr/bin/env python3
"""sala_worker.py — worker de background da SALA ESTRATEGISTA do Mission Control.

Análogo ao `coder_worker.py` do plugin Coder, mas usando o núcleo de core
`bot.sala` (decisão A1) e com prompt de estrategista + SEM gates (bypass de
verdade — `settings_path=None`, nenhum guard hook). Lançado detached pelo
dispatcher (`sala_dispatch.py`); não é invocado direto pelo operador.

Uso (interno):
    python -m bot.mission_control.sala_worker --sala-json <path> --mode <start|resume>

O `sala.json` carrega tudo (missao_id, session_id, sala_name, cwd, objetivo,
chat/thread, status, pid, turn_count, pending_input, ...).
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

# Garante que `bot` é importável quando rodado como script standalone.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bot.sala import room, state as sala_state  # noqa: E402
from bot.mission_control import sala_prompt, storage  # noqa: E402


logger = logging.getLogger("kobe.mission_control.sala_worker")


def _notify(kobe_home: Path, msg: str) -> None:
    """kobe-notify best-effort (silencioso se envs/bin ausentes)."""
    notify_bin = kobe_home / "bot" / "bin" / "kobe-notify"
    if not notify_bin.is_file():
        return
    if not os.environ.get("KOBE_TELEGRAM_BOT_TOKEN") or not os.environ.get("KOBE_CHAT_ID"):
        return
    try:
        subprocess.run([str(notify_bin), msg], timeout=15, capture_output=True)
    except Exception:  # noqa: BLE001 — notify é nice-to-have
        logger.exception("falha enviando kobe-notify")


def _on_heartbeat(kobe_home: Path):
    def cb(sala: str, elapsed: float, st: dict) -> None:
        mins = int(elapsed // 60)
        _notify(kobe_home,
                f"⏳ [mission] sala `{sala}` pensando há ~{mins}min — ainda em andamento.")
    return cb


def _on_death(kobe_home: Path):
    def cb(sala: str, st: dict) -> None:
        _notify(kobe_home,
                f"🔴 [mission] a sala `{sala}` caiu durante o turno. O raciocínio "
                f"registrado no workspace está preservado; reabra a missão pra continuar.")
    return cb


def _start(kobe_home: Path, sala_json: Path) -> int:
    st = sala_state.read_state(sala_json)
    missao_id = st["missao_id"]
    cwd = Path(st["cwd"])
    sala = st["sala_name"]
    objetivo = st.get("objetivo", "")

    # Layout da missão: workspace + sysprompt + brief.
    storage.ensure_workspace(kobe_home, missao_id)
    workspace_rel = os.path.relpath(storage.workspace_dir(kobe_home, missao_id), cwd)
    sysprompt_path = storage.path_sala_sysprompt(kobe_home, missao_id)
    sysprompt_path.write_text(
        sala_prompt.build_strategist_system_prompt(
            kobe_home=kobe_home, objetivo=objetivo,
            missao_id=missao_id, workspace_rel=workspace_rel),
        encoding="utf-8",
    )
    brief_path = storage.missao_dir(kobe_home, missao_id) / "sala-brief.md"
    brief_path.write_text(
        sala_prompt.build_mission_brief(
            objetivo=objetivo, sala=sala, missao_id=missao_id,
            workspace_rel=workspace_rel),
        encoding="utf-8",
    )

    brief_rel = os.path.relpath(brief_path, cwd)
    spec = room.SalaSpec(
        sala_name=sala,
        cwd=cwd,
        session_id=st["session_id"],
        sysprompt_path=sysprompt_path,
        launch_prompt=f"Leia {brief_rel} — é o briefing desta missão. Comece por aí.",
        settings_path=None,  # SEM guard: a sala roda em bypass de verdade.
    )
    result = room.open_sala(spec, launcher_path=storage.path_sala_launcher(kobe_home, missao_id))
    if not result.ok:
        sala_state.patch_state(sala_json, status="failed",
                               last_text=f"falha abrindo sala tmux: {result.error}")
        _notify(kobe_home, f"🔴 [mission] não consegui abrir a sala `{sala}`: {result.error}")
        return 1

    sala_state.patch_state(sala_json, status="running", pid=result.claude_pid)
    _notify(kobe_home,
            f"🟢 [mission] sala `{sala}` aberta e visível no Claude Code Desktop. "
            f"Pensando na missão; reporto os marcos por aqui.")
    return room.monitor_sala(sala_json, sala, kobe_home=kobe_home,
                             on_heartbeat=_on_heartbeat(kobe_home),
                             on_death=_on_death(kobe_home))


def _resume(kobe_home: Path, sala_json: Path) -> int:
    st = sala_state.read_state(sala_json)
    sala = st["sala_name"]
    pending = (st.get("pending_input") or "").strip()
    outcome = room.resume_deliver(sala_json, sala, pending)

    if outcome == room.ResumeOutcome.DEAD:
        sala_state.patch_state(sala_json, status="failed",
                               last_text="sala tmux não está mais viva.")
        _notify(kobe_home,
                f"🔴 [mission] a sala `{sala}` não está mais viva; não dá pra retomar. "
                f"Reabra a missão (o workspace está preservado).")
        return 1
    if outcome == room.ResumeOutcome.STILL_BUSY:
        _notify(kobe_home,
                f"🟡 [mission] a sala `{sala}` seguiu ocupada — não injetei tua mensagem "
                f"(pra não perdê-la). Ela está preservada; manda de novo quando parar.")
        return 0
    if outcome == room.ResumeOutcome.FAILED:
        _notify(kobe_home,
                f"🔴 [mission] tua mensagem NÃO pousou na sala `{sala}` após 2 tentativas. "
                f"A sala está viva e o input preservado — tenta de novo.")
        return 1
    # DELIVERED → monitora o turno.
    return room.monitor_sala(sala_json, sala, kobe_home=kobe_home,
                             on_heartbeat=_on_heartbeat(kobe_home),
                             on_death=_on_death(kobe_home))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Worker da sala estrategista (Mission Control)")
    parser.add_argument("--sala-json", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=["start", "resume"])
    args = parser.parse_args(argv)

    kobe_home_raw = os.environ.get("KOBE_HOME") or ""
    if not kobe_home_raw:
        print("KOBE_HOME ausente no env do worker", file=sys.stderr)
        return 2
    kobe_home = Path(kobe_home_raw).expanduser().resolve()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s kobe.mission_control.sala_worker: %(message)s",
        stream=sys.stderr,
    )

    sala_json = args.sala_json
    if not sala_json.is_file():
        print(f"sala.json não existe: {sala_json}", file=sys.stderr)
        return 2

    # Marca o worker corrente (owner-check do monitor usa worker_pid).
    sala_state.patch_state(sala_json, worker_pid=os.getpid())

    def _on_term(signum, frame):  # noqa: ANN001
        try:
            sala_state.patch_state(sala_json, status="terminated")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(143)

    signal.signal(signal.SIGTERM, _on_term)

    try:
        if args.mode == "start":
            return _start(kobe_home, sala_json)
        return _resume(kobe_home, sala_json)
    except Exception as exc:  # noqa: BLE001 — deixa o state sano e avisa
        logger.exception("sala_worker exception")
        try:
            sala_state.patch_state(sala_json, status="crashed",
                                   last_text=f"worker exception: {exc!r}")
            _notify(kobe_home, f"🔴 [mission] worker da sala crashou: {exc!r}")
        except Exception:  # noqa: BLE001
            pass
        return 99


if __name__ == "__main__":
    sys.exit(main())
