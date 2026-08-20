#!/usr/bin/env python3
"""Testes das reações de recebimento (👀 / ✍️) — bot/reactions.py.

O que estas travas protegem:
- a reação é o ÚNICO sinal de vida que não passa por modelo nem depende do turno
  sobreviver, então ela NUNCA pode derrubar nem atrasar o turno;
- a flag off tem que ser silêncio absoluto (nenhuma chamada à API);
- o semáforo de estágio (👀 → ✍️) tem que usar o MESMO message_id, senão o
  Telegram não substitui a reação — vira duas reações em mensagens diferentes.

Rodar: .venv/bin/python -m pytest tests/test_reactions.py -q
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import reactions


class _FakeBot:
    """Bot fake que grava as chamadas de set_message_reaction."""

    def __init__(self, raises: Exception | None = None) -> None:
        self.calls: list[tuple] = []
        self._raises = raises

    async def set_message_reaction(self, *, chat_id, message_id, reaction):
        self.calls.append((chat_id, message_id, reaction))
        if self._raises is not None:
            raise self._raises
        return True


def test_set_reaction_chama_api() -> None:
    bot = _FakeBot()
    asyncio.run(reactions.set_reaction(bot, -100, 42, "👀"))
    assert bot.calls == [(-100, 42, "👀")]


def test_set_reaction_nunca_levanta() -> None:
    """Emoji recusado / permissão faltando / Telegram fora: engole e segue.

    Esta é a trava mais importante do módulo. Se ela quebrar, uma reação
    recusada passa a derrubar a mensagem do operador — o oposto do objetivo.
    """
    bot = _FakeBot(raises=RuntimeError("REACTION_INVALID"))
    asyncio.run(reactions.set_reaction(bot, -100, 42, "🎧"))  # não levanta
    assert bot.calls == [(-100, 42, "🎧")]


def test_react_dispara_sem_esperar() -> None:
    """`react` é síncrono (fire-and-forget): quem recebe a msg não paga latência."""

    async def _cenario() -> _FakeBot:
        bot = _FakeBot()
        reactions.react(bot, -100, 42, "👀")
        # Ainda não rodou — só foi agendado. Cede o loop pra a task rodar.
        assert bot.calls == []
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return bot

    bot = asyncio.run(_cenario())
    assert bot.calls == [(-100, 42, "👀")]


def test_react_noop_sem_emoji_ou_sem_message_id() -> None:
    """Emoji vazio no .env desliga um estágio sem tocar em código."""

    async def _cenario() -> _FakeBot:
        bot = _FakeBot()
        reactions.react(bot, -100, 42, "")
        reactions.react(bot, -100, 42, None)
        reactions.react(bot, -100, None, "👀")
        await asyncio.sleep(0)
        return bot

    assert asyncio.run(_cenario()).calls == []


# ── Fiação no handler ─────────────────────────────────────────────────────


def _config(**over):
    """Config mínima com os campos que os helpers de reação leem."""
    base = mock.MagicMock()
    base.telegram_reactions_enabled = True
    base.telegram_reaction_received = "👀"
    base.telegram_reaction_transcribed = "✍️"
    for k, v in over.items():
        setattr(base, k, v)
    return base


def _message(bot):
    msg = mock.MagicMock()
    msg.get_bot.return_value = bot
    msg.chat_id = -100
    msg.message_id = 77
    return msg


def test_handler_flag_off_nao_chama_api() -> None:
    """Rollback trivial: flag off = silêncio absoluto, zero chamada."""
    from bot import telegram_handler as th

    bot = _FakeBot()
    msg = _message(bot)
    th._react_received(_config(telegram_reactions_enabled=False), msg)
    th._react_transcribed(_config(telegram_reactions_enabled=False), msg)
    assert bot.calls == []


def test_handler_semaforo_usa_o_mesmo_message_id() -> None:
    """👀 e ✍️ na MESMA mensagem — é isso que faz o Telegram substituir."""
    from bot import telegram_handler as th

    async def _cenario() -> _FakeBot:
        bot = _FakeBot()
        msg = _message(bot)
        th._react_received(_config(), msg)
        th._react_transcribed(_config(), msg)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return bot

    bot = asyncio.run(_cenario())
    assert [c[2] for c in bot.calls] == ["👀", "✍️"]
    assert {c[1] for c in bot.calls} == {77}, "os dois estágios na mesma mensagem"


def test_config_le_emojis_do_env() -> None:
    """Emoji configurável: a lista permitida é do Telegram e pode mudar."""
    import os

    from bot import config as cfg

    env = {
        "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_ALLOWED_USER_IDS": "1",
        "SUPABASE_URL": "u",
        "SUPABASE_KEY": "k",
        "GROQ_API_KEY": "g",
        "KOBE_HOME": "/tmp",
        "TELEGRAM_REACTIONS_ENABLED": "true",
        "TELEGRAM_REACTION_RECEIVED": "🤔",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(cfg, "load_dotenv", lambda *a, **k: None):
            c = cfg.load_config()
    assert c.telegram_reactions_enabled is True
    assert c.telegram_reaction_received == "🤔"
    # Não configurado → default do módulo (uma fonte de verdade só).
    assert c.telegram_reaction_transcribed == reactions.DEFAULT_TRANSCRIBED


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
