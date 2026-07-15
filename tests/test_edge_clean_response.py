"""Testes da Peça C (separação rascunho/resposta) no ClaudeRunner.

Real (não circular): aponta o ClaudeRunner pra um fake-claude que cospe uma
sequência de eventos stream-json e verifica o texto final retornado.

Trava:
- clean_response ON + ferramenta → resposta = SÓ o texto pós-última-ferramenta
  (a prosa pré-tool NÃO vaza).
- clean_response OFF → concatena tudo (comportamento de 2026-06-01 preservado —
  não engole prosa pré-tool).
- turno SEM ferramenta → resposta = tudo (papo puro idêntico ao legado).
- ferramenta de housekeeping (TodoWrite) depois da resposta NÃO empurra a
  resposta pro balde de rascunho.
- guard anti-engolir: ferramenta sem texto depois → cai no join completo (nunca
  vazio).

Rodar: .venv/bin/python -m pytest tests/test_edge_clean_response.py -q
"""
from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path

from bot.claude_runner import ClaudeRunner


def _write_fake_claude(tmp_path: Path, events: list[dict]) -> Path:
    """Fake-claude: ignora args, drena o stdin, cospe `events` como JSONL +
    um evento `result` no fim."""
    payload = json.dumps(events)
    fake = tmp_path / "fake_claude.py"
    script = (
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "sys.stdin.buffer.read()\n"
        f"events = json.loads(r'''{payload}''')\n"
        "for e in events:\n"
        "    sys.stdout.write(json.dumps(e) + '\\n')\n"
        "sys.stdout.write(json.dumps("
        "{'type':'result','result':'RESULT_EVENT',"
        "'usage':{'input_tokens':1,'output_tokens':1},'total_cost_usd':0.0}"
        ") + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return fake


def _assistant(*blocks: dict) -> dict:
    return {"type": "assistant", "message": {"content": list(blocks)}}


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _tool(name: str) -> dict:
    return {"type": "tool_use", "name": name, "input": {}}


def _run(tmp_path: Path, events: list[dict], *, clean: bool) -> str:
    # O binário É o fake executável (mesmo truque do test_claude_runner_buffer):
    # o runner chama `binary -p ...`, e o fake ignora os args.
    fake = _write_fake_claude(tmp_path, events)
    runner = ClaudeRunner(cwd=tmp_path, timeout_seconds=10, binary=str(fake))
    result = asyncio.run(runner.run("prompt", clean_response=clean))
    return result.text


def test_clean_on_drops_pre_tool_prose(tmp_path) -> None:
    events = [
        _assistant(_text("deixa eu olhar o handler do lock…")),
        _assistant(_tool("Read")),
        _assistant(_text("O handler faz X. Aqui está a resposta final.")),
    ]
    out = _run(tmp_path, events, clean=True)
    assert out == "O handler faz X. Aqui está a resposta final.", out
    assert "deixa eu olhar" not in out


def test_clean_off_concatenates_all(tmp_path) -> None:
    events = [
        _assistant(_text("deixa eu olhar o handler do lock…")),
        _assistant(_tool("Read")),
        _assistant(_text("O handler faz X. Aqui está a resposta final.")),
    ]
    out = _run(tmp_path, events, clean=False)
    assert "deixa eu olhar" in out and "resposta final" in out


def test_no_tool_turn_unchanged(tmp_path) -> None:
    # Papo puro (sem ferramenta): resposta = tudo, com clean ON — igual ao legado.
    events = [_assistant(_text("resposta de bate-pronto, sem tool"))]
    out = _run(tmp_path, events, clean=True)
    assert out == "resposta de bate-pronto, sem tool"


def test_housekeeping_tool_does_not_bucket_answer_as_draft(tmp_path) -> None:
    events = [
        _assistant(_text("rascunho antes")),
        _assistant(_tool("Read")),
        _assistant(_text("a RESPOSTA de verdade")),
        _assistant(_tool("TodoWrite")),  # housekeeping — não move o corte
    ]
    out = _run(tmp_path, events, clean=True)
    assert out == "a RESPOSTA de verdade", out


def test_anti_swallow_fallback_when_no_post_tool_text(tmp_path) -> None:
    # Resposta escrita ANTES de uma ferramenta final (sem texto depois): não pode
    # engolir — cai no join completo.
    events = [
        _assistant(_text("a resposta completa está aqui")),
        _assistant(_tool("Read")),
    ]
    out = _run(tmp_path, events, clean=True)
    assert "a resposta completa está aqui" in out, out
