"""Handoff "nasce aqui → vira Coder" (decisão 4/8 do plano Mission Control).

Quando uma missão conclui "vamos CONSTRUIR X", o estrategista prepara
`workspace/handoff-brief.md`, PARA pedindo o "go" do operador, e — só depois do
go (que vem por qualquer um dos dois canais: direto na sala OU via Hal/Telegram)
— dispara o Coder com o brief como tarefa, no projeto-alvo (`--cwd`).

Este módulo centraliza: ler o brief, resolver o CLI do plugin Coder, montar e
disparar o comando. É invocado pelo estrategista (dentro da sala) como um
one-liner; mantém a lógica num lugar só e testável.

O Coder roda seu próprio rito (plano/gates/deploy) — o handoff só o KICKA. A
partir daí o operador interage com a sala do Coder normalmente.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from bot.mission_control import storage


logger = logging.getLogger("kobe.mission_control.handoff")


def coder_run_remote(kobe_home: Path) -> Optional[Path]:
    """Caminho do CLI do plugin Coder, ou None se o plugin não está instalado."""
    p = kobe_home / "plugins" / "public" / "coder" / "scripts" / "run_remote.py"
    return p if p.is_file() else None


def build_handoff_command(*, python: str, run_remote: Path, cwd: str,
                          task: str, effort_max: bool = False) -> list[str]:
    """Comando de dispatch do Coder (puro — testável). `--task` carrega o brief
    inteiro (autocontido, convenção do Coder). `--cwd` é o projeto-alvo."""
    cmd = [python, str(run_remote), "start", "--cwd", cwd, "--task", task]
    if effort_max:
        cmd.append("--effort-max")
    return cmd


def disparar(*, kobe_home: Path, missao_id: str, cwd: str,
             effort_max: bool = False) -> dict:
    """Dispara o Coder a partir do `workspace/handoff-brief.md` da missão.

    Pré-condições (senão devolve erro, sem disparar):
    - o brief existe e não está vazio (o estrategista tem que tê-lo preparado);
    - o plugin Coder está instalado;
    - o `cwd` (projeto-alvo) existe.
    """
    brief_path = storage.workspace_dir(kobe_home, missao_id) / "handoff-brief.md"
    if not brief_path.is_file():
        return {"error": "brief_inexistente",
                "message": f"prepare {brief_path} antes do handoff."}
    brief = brief_path.read_text(encoding="utf-8").strip()
    if not brief:
        return {"error": "brief_vazio", "message": f"{brief_path} está vazio."}

    run_remote = coder_run_remote(kobe_home)
    if run_remote is None:
        return {"error": "coder_ausente",
                "message": "plugin Coder não instalado (plugins/public/coder)."}

    cwd_path = Path(cwd).expanduser()
    if not cwd_path.is_dir():
        return {"error": "cwd_inexistente", "message": f"projeto-alvo não existe: {cwd}"}

    venv_python = kobe_home / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.is_file() else "python3"
    cmd = build_handoff_command(python=python, run_remote=run_remote,
                                cwd=str(cwd_path.resolve()), task=brief,
                                effort_max=effort_max)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                              cwd=str(kobe_home), env=os.environ.copy())
    except Exception as exc:  # noqa: BLE001
        logger.exception("falha disparando Coder")
        return {"error": "dispatch_falhou", "message": repr(exc)}

    out = (proc.stdout or "").strip()
    coder_info = None
    try:
        coder_info = json.loads(out.splitlines()[-1]) if out else None
    except Exception:  # noqa: BLE001 — output não-JSON não invalida o disparo
        coder_info = None
    return {"ok": proc.returncode == 0, "missao_id": missao_id,
            "coder": coder_info, "raw": out[-500:], "returncode": proc.returncode}


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Handoff Mission Control → Coder")
    p.add_argument("--missao", required=True)
    p.add_argument("--cwd", required=True, help="projeto-alvo do Coder")
    p.add_argument("--effort-max", action="store_true")
    args = p.parse_args(argv)
    kobe_home = Path(os.environ.get("KOBE_HOME", "")).expanduser().resolve()
    if not str(kobe_home):
        print("KOBE_HOME ausente", file=sys.stderr)
        return 2
    res = disparar(kobe_home=kobe_home, missao_id=args.missao, cwd=args.cwd,
                   effort_max=args.effort_max)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
