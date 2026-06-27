#!/usr/bin/env python3
"""Testes do handoff Mission Control → Coder — comando puro + guards.

Cobrem: montagem do comando de dispatch (puro) e os guards de `disparar` que
retornam ANTES de invocar o Coder (brief inexistente/vazio, Coder não instalado,
cwd inexistente). O dispatch real do Coder é validado no prod VPS.

Rodar:

    .venv/bin/python tests/test_mission_control_handoff.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.mission_control import handoff, storage  # noqa: E402


def _ok(name: str) -> None:
    print(f"  ok: {name}")


def test_build_handoff_command() -> None:
    cmd = handoff.build_handoff_command(
        python="/venv/python", run_remote=Path("/k/plugins/public/coder/scripts/run_remote.py"),
        cwd="/proj", task="construa X")
    assert cmd[:3] == ["/venv/python", "/k/plugins/public/coder/scripts/run_remote.py", "start"]
    assert "--cwd" in cmd and "/proj" in cmd
    assert "--task" in cmd and "construa X" in cmd
    assert "--effort-max" not in cmd
    cmd2 = handoff.build_handoff_command(
        python="p", run_remote=Path("/r"), cwd="/c", task="t", effort_max=True)
    assert "--effort-max" in cmd2
    _ok("build_handoff_command")


def test_disparar_guards() -> None:
    with tempfile.TemporaryDirectory() as d:
        kh = Path(d)
        mid = "2026-06-26-build"
        storage.ensure_workspace(kh, mid)
        # sem brief → erro brief_inexistente
        res = handoff.disparar(kobe_home=kh, missao_id=mid, cwd=str(kh))
        assert res.get("error") == "brief_inexistente", res
        # brief vazio → brief_vazio
        brief = storage.workspace_dir(kh, mid) / "handoff-brief.md"
        brief.write_text("   \n", encoding="utf-8")
        res = handoff.disparar(kobe_home=kh, missao_id=mid, cwd=str(kh))
        assert res.get("error") == "brief_vazio", res
        # brief ok mas Coder não instalado (tmpdir não tem plugins/) → coder_ausente
        brief.write_text("construa X", encoding="utf-8")
        res = handoff.disparar(kobe_home=kh, missao_id=mid, cwd=str(kh))
        assert res.get("error") == "coder_ausente", res
        # Coder presente mas cwd-alvo inexistente → cwd_inexistente
        rr = kh / "plugins" / "public" / "coder" / "scripts"
        rr.mkdir(parents=True, exist_ok=True)
        (rr / "run_remote.py").write_text("# stub", encoding="utf-8")
        res = handoff.disparar(kobe_home=kh, missao_id=mid, cwd=str(kh / "nao-existe"))
        assert res.get("error") == "cwd_inexistente", res
    _ok("disparar_guards")


def main() -> int:
    print("test_mission_control_handoff:")
    test_build_handoff_command()
    test_disparar_guards()
    print("TODOS OS TESTES PASSARAM ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
