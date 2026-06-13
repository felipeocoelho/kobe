"""Testes da rede de segurança global de turno (`on_error`, Bug 2 2026-06-08).

Garante que uma exceção não-tratada num turno vira aviso ao operador
("travei, reenvia") em vez de morrer calada — e que o aviso é best-effort
(não relança), só pra usuário autorizado, e nunca crasha em update estranho.
"""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram import Chat, Message, Update, User

from bot.telegram_handler import on_error


def _make_update(thread_id=None):
    chat = Chat(id=123, type="supergroup")
    user = User(id=999, first_name="Felipe", is_bot=False)
    msg = Message(
        message_id=1,
        date=datetime.datetime.now(datetime.timezone.utc),
        chat=chat,
        from_user=user,
        message_thread_id=thread_id,
        text="oi",
    )
    return Update(update_id=1, message=msg)


def _make_ctx(allowed_ids):
    app = SimpleNamespace(
        bot_data={"config": SimpleNamespace(allowed_user_ids=frozenset(allowed_ids))}
    )
    return SimpleNamespace(application=app, error=RuntimeError("Server disconnected"))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_avisa_operador_autorizado_no_mesmo_topico():
    upd = _make_update(thread_id=42)
    with patch.object(Message, "reply_text", new=AsyncMock()) as rt:
        _run(on_error(upd, _make_ctx([999])))
    assert rt.await_count == 1
    assert "Travei" in rt.await_args.args[0]
    assert rt.await_args.kwargs.get("message_thread_id") == 42


def test_nao_avisa_usuario_nao_autorizado():
    upd = _make_update()
    with patch.object(Message, "reply_text", new=AsyncMock()) as rt:
        _run(on_error(upd, _make_ctx([111])))
    assert rt.await_count == 0


def test_update_nao_message_nao_crasha():
    _run(on_error("não é um Update", _make_ctx([999])))  # não levanta


def test_falha_no_aviso_e_engolida():
    upd = _make_update()
    with patch.object(
        Message, "reply_text", new=AsyncMock(side_effect=Exception("telegram down"))
    ):
        _run(on_error(upd, _make_ctx([999])))  # best-effort: não relança
