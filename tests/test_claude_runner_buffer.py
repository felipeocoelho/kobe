"""Regressão do buffer de leitura do stdout do `claude` (bug do "digitando…"
fantasma, 2026-06-25).

O stream-json do claude emite UMA linha por evento. Um tool_result gordo (ler
arquivo grande / fetch) produz uma linha única > 64KB. Com o limite default do
asyncio (64KB), o `readline()` levantava `ValueError`/`LimitOverrunError` e
derrubava o turno inteiro (prendendo o typing no foreground). O fix sobe o
limite pra 10MB (`STDOUT_BUFFER_LIMIT_BYTES`).

Este teste é REAL (não circular): aponta o `ClaudeRunner` pra um fake-claude que
cospe uma linha JSON > 64KB e verifica que `run()` NÃO levanta e que o texto
gordo é capturado. Sem o fix, falha com ValueError; com o fix, passa.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

from bot.claude_runner import (
    STDOUT_BUFFER_LIMIT_BYTES,
    ClaudeResult,
    ClaudeRunner,
)


def _write_fake_claude(tmp_path: Path, *, text_len: int) -> Path:
    """Cria um executável que ignora os args do CLI, drena o stdin (o prompt) e
    imprime UMA linha JSON `assistant` com um bloco de texto de `text_len` bytes,
    seguida de um evento `result`. A linha gorda é o que exercita o readline."""
    fake = tmp_path / "fake_claude.py"
    script = (
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "sys.stdin.buffer.read()\n"  # drena o prompt enviado via stdin
        f"big = 'x' * {text_len}\n"
        "sys.stdout.write(json.dumps("
        "{'type':'assistant','message':{'content':[{'type':'text','text':big}]}}"
        ") + '\\n')\n"
        "sys.stdout.write(json.dumps("
        "{'type':'result','result':'ok',"
        "'usage':{'input_tokens':1,'output_tokens':1},'total_cost_usd':0.0}"
        ") + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return fake


def test_linha_maior_que_64kb_nao_derruba_o_turno(tmp_path):
    # 200KB numa linha só: > default de 64KB do asyncio, < 10MB do fix.
    text_len = 200_000
    assert text_len > 64 * 1024
    assert text_len < STDOUT_BUFFER_LIMIT_BYTES
    fake = _write_fake_claude(tmp_path, text_len=text_len)

    runner = ClaudeRunner(cwd=tmp_path, timeout_seconds=30, binary=str(fake))

    async def run() -> ClaudeResult:
        return await runner.run("prompt qualquer")

    result = asyncio.run(run())

    # NÃO levantou ValueError/LimitOverrunError e o texto gordo foi lido inteiro.
    assert isinstance(result, ClaudeResult)
    assert len(result.text) >= text_len


def test_limite_do_buffer_e_generoso():
    # Sanidade: a constante do fix é a esperada (10MB) e bem acima de 64KB.
    assert STDOUT_BUFFER_LIMIT_BYTES == 10 * 1024 * 1024
    assert STDOUT_BUFFER_LIMIT_BYTES > 64 * 1024
