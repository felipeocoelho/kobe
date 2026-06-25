"""Regressão da blindagem do "digitando…" no caminho foreground (bug do typing
fantasma, 2026-06-25).

O foreground (`_handle_user_text`) criava o `typing_task` sem `try/finally` —
quando o turno crashava antes do cancel (ex.: `_resolve_claude` re-levantando um
erro não-`ClaudeError`, ou `_send_long_text` falhando), o loop `_keep_typing`
ficava órfão e reemitia "digitando…" pra sempre até o bot reiniciar. O caminho
background já tinha a proteção; a assimetria era o defeito.

O fix é o context manager `_typing_indicator`, que GARANTE o cancelamento do
loop na saída do bloco — sucesso OU exceção. Estes testes batem exatamente nessa
garantia, em isolamento (sem Telegram/DB/claude).
"""

from __future__ import annotations

import asyncio

from bot.telegram_handler import _typing_indicator


class _FakeBot:
    """Conta quantas chatActions o `_keep_typing` emitiu (sinal de vida do loop)."""

    def __init__(self) -> None:
        self.calls = 0

    async def send_chat_action(self, **kwargs) -> None:
        self.calls += 1


def test_cancela_o_typing_na_saida_normal():
    bot = _FakeBot()

    async def run():
        async with _typing_indicator(chat_id=1, thread_id=None, bot=bot) as task:
            # Deixa o loop emitir ao menos uma vez (prova que estava vivo).
            await asyncio.sleep(0)
            assert not task.done()
            return task

    task = asyncio.run(run())
    # Saiu do bloco → o loop foi cancelado e finalizado.
    assert task.done()


def test_cancela_o_typing_quando_o_corpo_levanta():
    """O caso do bug: o corpo crasha no meio. O typing TEM que morrer mesmo
    assim — senão fica o fantasma."""
    bot = _FakeBot()
    captured = {}

    class _Boom(Exception):
        pass

    async def run():
        try:
            async with _typing_indicator(chat_id=1, thread_id=None, bot=bot) as task:
                captured["task"] = task
                raise _Boom("turno crashou no meio da entrega")
        except _Boom:
            pass

    asyncio.run(run())

    task = captured["task"]
    # A exceção propagou MAS o finally do context manager cancelou o loop.
    assert task.done()
    # E o task terminou limpo (CancelledError tratado internamente em
    # `_keep_typing`, que retorna None) — sem exceção pendente não-recuperada.
    assert task.cancelled() or task.exception() is None
