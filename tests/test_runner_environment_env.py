#!/usr/bin/env python3
"""`KOBE_ENV` chega ao subprocesso do Claude (Sessão #1, P2, ponto 2).

Por que isto precisa de teste e não é óbvio: o ambiente do subagente é o que
decide se ele pode publicar, fazer deploy ou tratar a conversa como memória de
produção. Se a variável não descer, o subagente de uma instância de dev acha que
está em produção — e o erro aparece longe da causa, num `git push` que ninguém
pediu.

A técnica é a mesma de `tests/test_claude_runner_buffer.py`: um executável de
mentira no lugar do `claude`, que em vez de responder devolve o próprio env que
recebeu. Nada de rede, nada de CLI real.

Rodar: .venv/bin/python -m pytest tests/test_runner_environment_env.py -q
"""
from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.claude_runner import ClaudeRunner


def _claude_que_devolve_o_env(tmp_path: Path) -> Path:
    """Executável falso que responde com o env que herdou, como texto."""
    fake = tmp_path / "fake_claude_env.py"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, os, json\n"
        "sys.stdin.buffer.read()\n"
        "env = {k: v for k, v in os.environ.items() if k.startswith('KOBE_')}\n"
        "sys.stdout.write(json.dumps("
        "{'type':'assistant','message':{'content':[{'type':'text',"
        "'text': json.dumps(env)}]}}) + '\\n')\n"
        "sys.stdout.write(json.dumps("
        "{'type':'result','result':'ok','usage':{},'total_cost_usd':0.0}) + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return fake


def _env_recebido(tmp_path: Path, **kwargs) -> dict:
    fake = _claude_que_devolve_o_env(tmp_path)
    runner = ClaudeRunner(cwd=tmp_path, timeout_seconds=30, binary=str(fake), **kwargs)
    resultado = asyncio.run(runner.run("prompt qualquer"))
    return json.loads(resultado.text)


@pytest.mark.parametrize("ambiente", ["prod", "dev"])
def test_o_subprocesso_recebe_kobe_env(tmp_path: Path, ambiente: str) -> None:
    assert _env_recebido(tmp_path, environment=ambiente)["KOBE_ENV"] == ambiente


def test_default_do_runner_e_prod(tmp_path: Path) -> None:
    """Runner construído sem `environment` (testes antigos, scripts) → prod."""
    assert _env_recebido(tmp_path)["KOBE_ENV"] == "prod"


def test_o_runner_vence_o_ambiente_herdado(tmp_path: Path, monkeypatch) -> None:
    """O ambiente é o do `Config`, não o que por acaso estava no shell.

    Um `KOBE_ENV` solto no shell de quem iniciou o serviço não pode mandar mais
    que a configuração carregada — senão o ambiente do turno vira acidente.
    """
    monkeypatch.setenv("KOBE_ENV", "dev")
    assert _env_recebido(tmp_path, environment="prod")["KOBE_ENV"] == "prod"


def test_as_outras_variaveis_continuam_indo(tmp_path: Path) -> None:
    """Regressão: a linha nova não pode ter atropelado o contrato dos helpers."""
    fake = _claude_que_devolve_o_env(tmp_path)
    runner = ClaudeRunner(cwd=tmp_path, timeout_seconds=30, binary=str(fake))
    resultado = asyncio.run(
        runner.run("x", chat_id=-100123, thread_id=2, bot_token="token-de-mentira")
    )
    env = json.loads(resultado.text)
    assert env["KOBE_CHAT_ID"] == "-100123"
    assert env["KOBE_THREAD_ID"] == "2"
    assert env["KOBE_TELEGRAM_BOT_TOKEN"] == "token-de-mentira"
    assert env["KOBE_ENV"] == "prod"
