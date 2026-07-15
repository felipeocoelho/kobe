#!/usr/bin/env python3
"""Testes da Peça B (Liveness Protocol) — LIV-ack semântico por duração.

Trava:
- sem OPENAI_API_KEY → fallback consistente (start e late distintos).
- com modelo barato mockado → devolve o texto do modelo (semântico).
- modelo devolve vazio ou levanta → fallback (write_ack NUNCA levanta).

O QUANDO disparar (só tarefa pesada) é decidido no handler pelo turn_classifier —
coberto por test_turn_classifier. Aqui travamos o O QUÊ (o texto do ack).

Rodar: .venv/bin/python tests/test_edge_liveness.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import liveness


def _fake_openai(content: str):
    """Client OpenAI fake cujo create() devolve `content`."""
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = content
    client = mock.MagicMock()
    client.chat.completions.create = mock.AsyncMock(return_value=resp)
    return client


def test_fallback_without_api_key() -> None:
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENAI_API_KEY", None)
        start = asyncio.run(liveness.write_ack("varre a VPS"))
        late = asyncio.run(liveness.write_ack("varre a VPS", late=True))
    assert start == liveness.fallback_ack(late=False)
    assert late == liveness.fallback_ack(late=True)
    assert start != late, "start e late têm textos distintos"


def test_uses_model_text_when_available() -> None:
    fake = _fake_openai("Beleza, vou varrer a VPS atrás dos arquivos e já te retorno.")
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}):
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            out = asyncio.run(liveness.write_ack("faz uma varredura na VPS"))
    assert out == "Beleza, vou varrer a VPS atrás dos arquivos e já te retorno."


def test_strips_quotes_from_model() -> None:
    fake = _fake_openai('"Vou cuidar disso e já te volto."')
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}):
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            out = asyncio.run(liveness.write_ack("tarefa"))
    assert out == "Vou cuidar disso e já te volto."


def test_empty_model_falls_back() -> None:
    fake = _fake_openai("   ")
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}):
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            out = asyncio.run(liveness.write_ack("tarefa"))
    assert out == liveness.fallback_ack()


def test_model_error_falls_back_never_raises() -> None:
    client = mock.MagicMock()
    client.chat.completions.create = mock.AsyncMock(side_effect=RuntimeError("boom"))
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}):
        with mock.patch("bot.conversation_detector._get_openai", return_value=client):
            out = asyncio.run(liveness.write_ack("tarefa", late=True))
    assert out == liveness.fallback_ack(late=True)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
