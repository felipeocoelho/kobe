#!/usr/bin/env python3
"""Testes do aviso de compactação (BUG 2 — compactação silenciosa).

Travam o contrato do `on_start` do `compact_session`: o operador tem que
ser avisado assim que a compactação começa (antes do summary), exatamente
uma vez por evento, e nunca em sessão vazia. Sem rede — patcha as deps de
DB/Claude do módulo `bot.compactor`. Rodar:

    .venv/bin/python tests/test_compactor_notify.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.claude_runner import ClaudeResult
from bot import compactor


class _FakeClaude:
    """Runner de mentira: devolve um summary fixo, conta as chamadas."""

    def __init__(self, text: str = "resumo da sessão") -> None:
        self.text = text
        self.calls = 0

    async def run(self, prompt, *, chat_id, thread_id, bot_token):
        self.calls += 1
        return ClaudeResult(text=self.text)


def _run(coro):
    return asyncio.run(coro)


def _patched(messages, *, summary="resumo da sessão"):
    """Contexto com as deps do compactor mockadas. Retorna (cm_patches,)."""
    return (
        mock.patch.object(compactor, "get_recent_messages", return_value=messages),
        mock.patch.object(compactor, "archive_active_session", return_value="old-sess"),
        mock.patch.object(compactor, "ensure_active_session", return_value="new-sess"),
        mock.patch.object(compactor, "insert_message", return_value=None),
    )


def test_notify_fires_on_compaction():
    """Com histórico real, on_start dispara 1x e a sessão nova é criada."""
    msgs = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]
    fired = {"n": 0}

    async def on_start():
        fired["n"] += 1

    patches = _patched(msgs)
    for p in patches:
        p.start()
    try:
        new = _run(
            compactor.compact_session(
                db=object(),
                claude=_FakeClaude(),
                topic_id="t1",
                session_id="s1",
                chat_id=123,
                thread_id=None,
                bot_token="tok",
                on_start=on_start,
            )
        )
    finally:
        for p in patches:
            p.stop()

    assert new == "new-sess", f"esperava nova sessão, veio {new!r}"
    assert fired["n"] == 1, f"on_start devia disparar 1x, disparou {fired['n']}x"


def test_no_notify_on_empty_session():
    """Sessão vazia → não compacta, não avisa (não promete o que não faz)."""
    fired = {"n": 0}

    async def on_start():
        fired["n"] += 1

    patches = _patched([])
    for p in patches:
        p.start()
    try:
        new = _run(
            compactor.compact_session(
                db=object(),
                claude=_FakeClaude(),
                topic_id="t1",
                session_id="s1",
                chat_id=123,
                thread_id=None,
                bot_token="tok",
                on_start=on_start,
            )
        )
    finally:
        for p in patches:
            p.stop()

    assert new is None, f"sessão vazia não devia compactar, veio {new!r}"
    assert fired["n"] == 0, f"on_start não devia disparar, disparou {fired['n']}x"


def test_notify_fires_before_summary():
    """O aviso sai ANTES do summary do Claude (operador não fica no escuro)."""
    msgs = [{"role": "user", "content": "oi"}]
    order: list[str] = []
    claude = _FakeClaude()

    async def on_start():
        order.append("notify")

    # Embrulha o run pra registrar quando o summary é gerado.
    real_run = claude.run

    async def _spy_run(*a, **k):
        order.append("summary")
        return await real_run(*a, **k)

    claude.run = _spy_run  # type: ignore[assignment]

    patches = _patched(msgs)
    for p in patches:
        p.start()
    try:
        _run(
            compactor.compact_session(
                db=object(),
                claude=claude,
                topic_id="t1",
                session_id="s1",
                chat_id=123,
                thread_id=None,
                bot_token="tok",
                on_start=on_start,
            )
        )
    finally:
        for p in patches:
            p.stop()

    assert order == ["notify", "summary"], f"ordem errada: {order}"


def test_notify_failure_does_not_break_compaction():
    """Se o aviso falha, a compactação segue (best-effort)."""
    msgs = [{"role": "user", "content": "oi"}]

    async def on_start():
        raise RuntimeError("telegram caiu")

    patches = _patched(msgs)
    for p in patches:
        p.start()
    try:
        new = _run(
            compactor.compact_session(
                db=object(),
                claude=_FakeClaude(),
                topic_id="t1",
                session_id="s1",
                chat_id=123,
                thread_id=None,
                bot_token="tok",
                on_start=on_start,
            )
        )
    finally:
        for p in patches:
            p.stop()

    assert new == "new-sess", f"falha no aviso não devia travar, veio {new!r}"


def test_no_on_start_still_compacts():
    """on_start=None (compatibilidade): compacta normalmente."""
    msgs = [{"role": "user", "content": "oi"}]
    patches = _patched(msgs)
    for p in patches:
        p.start()
    try:
        new = _run(
            compactor.compact_session(
                db=object(),
                claude=_FakeClaude(),
                topic_id="t1",
                session_id="s1",
                chat_id=123,
                thread_id=None,
                bot_token="tok",
            )
        )
    finally:
        for p in patches:
            p.stop()

    assert new == "new-sess", f"esperava nova sessão, veio {new!r}"


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
