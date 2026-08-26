#!/usr/bin/env python3
"""Testes das reações de recebimento (👀 / ✍) — bot/reactions.py.

O que estas travas protegem:
- a reação é o ÚNICO sinal de vida que não passa por modelo nem depende do turno
  sobreviver, então ela NUNCA pode derrubar nem atrasar o turno;
- a flag off tem que ser silêncio absoluto (nenhuma chamada à API);
- o semáforo de estágio (👀 → ✍) tem que usar o MESMO message_id, senão o
  Telegram não substitui a reação — vira duas reações em mensagens diferentes;
- a FORMA do emoji tem que ser a que o Bot API registra (bug de produção de
  2026-08-20: "✍️" com o VARIATION SELECTOR-16 era recusado, e recusado CALADO).

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


def test_react_normaliza_antes_de_chamar_a_api() -> None:
    """O bug de produção de 2026-08-20, travado.

    "✍️" (U+270D + VS16) era recusado pelo Telegram — a lista do Bot API registra
    "✍" puro. Se alguém reintroduzir a forma com marcador em qualquer ponto do
    caminho, a chamada tem que sair NORMALIZADA mesmo assim.
    """

    async def _cenario() -> _FakeBot:
        bot = _FakeBot()
        reactions.react(bot, -100, 42, "✍️")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return bot

    assert asyncio.run(_cenario()).calls == [(-100, 42, "✍")]


def test_react_nao_chama_api_com_emoji_fora_da_lista() -> None:
    """🎧 não é reação aceita (já testado contra a API real). Nem tenta."""

    async def _cenario() -> _FakeBot:
        bot = _FakeBot()
        reactions.react(bot, -100, 42, "🎧")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return bot

    assert asyncio.run(_cenario()).calls == []


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


# ── Forma do emoji: a classe de erro que derrubou a reação em produção ─────


def test_defaults_estao_na_lista_do_bot_api() -> None:
    """A trava mais direta: o emoji que sai de fábrica TEM que ser aceito.

    É exatamente o que faltava em 2026-08-20 — o default era "✍️" (com VS16) e
    o Telegram recusava toda troca de reação, caladamente.
    """
    assert reactions.DEFAULT_RECEIVED in reactions.ALLOWED_REACTIONS
    assert reactions.DEFAULT_TRANSCRIBED in reactions.ALLOWED_REACTIONS
    # E o default é a forma canônica (normalizar não muda nada).
    assert reactions.normalize_reaction(reactions.DEFAULT_RECEIVED) == reactions.DEFAULT_RECEIVED
    assert reactions.normalize_reaction(reactions.DEFAULT_TRANSCRIBED) == reactions.DEFAULT_TRANSCRIBED


def test_normalize_tira_vs16_sobrando() -> None:
    """"✍️" → "✍": o caso literal do bug."""
    assert reactions.normalize_reaction("✍️") == "✍"
    assert reactions.normalize_reaction("✍") == "✍"
    # Espaço em volta (colar no .env) não pode invalidar.
    assert reactions.normalize_reaction("  ✍️  ") == "✍"


def test_normalize_preserva_os_que_exigem_vs16() -> None:
    """O conserto ingênuo ("tira sempre o VS16") quebraria estes TRÊS.

    Dos 73 emojis da lista, ❤️‍🔥 / 🤷‍♂️ / 🤷‍♀️ só são aceitos COM o marcador.
    Esta trava existe pra impedir que o fix de um bug crie outro.
    """
    for emoji in ("❤️‍🔥", "🤷‍♂️", "🤷‍♀️"):
        assert reactions.normalize_reaction(emoji) == emoji, emoji
        assert emoji in reactions.ALLOWED_REACTIONS


def test_normalize_repoe_vs16_faltando() -> None:
    """O sentido inverso: faltou o marcador → devolve a forma exata da lista."""
    assert reactions.normalize_reaction("❤‍🔥") == "❤️‍🔥"
    assert reactions.normalize_reaction("🤷‍♂") == "🤷‍♂️"


def test_normalize_rejeita_fora_da_lista_e_distingue_de_desligado() -> None:
    """Inválido e "desligado de propósito" são coisas diferentes lá em cima.

    Os dois devolvem None aqui, mas quem chama trata diferente: vazio é escolha
    do operador (silêncio), inválido vira aviso no log (ver bot/config.py).
    """
    assert reactions.normalize_reaction("🎧") is None  # recusado pela API real
    assert reactions.normalize_reaction("👂") is None
    assert reactions.normalize_reaction("banana") is None
    assert reactions.normalize_reaction("") is None
    assert reactions.normalize_reaction("   ") is None
    assert reactions.normalize_reaction(None) is None


def test_tabela_canonica_nao_tem_colisao() -> None:
    """Se o Telegram acrescentar um emoji que colida sem o VS16, quebra AQUI.

    Sem esta trava, uma colisão faria a normalização devolver silenciosamente o
    emoji errado — bug muito pior que o que estamos consertando.
    """
    assert len(reactions._CANONICAL) == len(reactions.ALLOWED_REACTIONS)


# ── Fiação no handler ─────────────────────────────────────────────────────


def _config(**over):
    """Config mínima com os campos que os helpers de reação leem."""
    base = mock.MagicMock()
    base.telegram_reactions_enabled = True
    base.telegram_reaction_received = reactions.DEFAULT_RECEIVED
    base.telegram_reaction_transcribed = reactions.DEFAULT_TRANSCRIBED
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
    """👀 e ✍ na MESMA mensagem — é isso que faz o Telegram substituir."""
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
    assert [c[2] for c in bot.calls] == ["👀", "✍"]
    assert {c[1] for c in bot.calls} == {77}, "os dois estágios na mesma mensagem"


def test_config_le_emojis_do_env() -> None:
    """Emoji configurável: a lista permitida é do Telegram e pode mudar."""
    import os

    from bot import config as cfg

    env = {
        "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_ALLOWED_USER_IDS": "1",
        "DATABASE_URL": "postgresql:///kobe_fake",
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


def _load_config_with(**extra):
    """Carrega a config com um .env fake (só o que estas travas precisam)."""
    import os

    from bot import config as cfg

    env = {
        "TELEGRAM_BOT_TOKEN": "t",
        "TELEGRAM_ALLOWED_USER_IDS": "1",
        "DATABASE_URL": "postgresql:///kobe_fake",
        "GROQ_API_KEY": "g",
        "KOBE_HOME": "/tmp",
        "TELEGRAM_REACTIONS_ENABLED": "true",
    }
    env.update(extra)
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(cfg, "load_dotenv", lambda *a, **k: None):
            return cfg.load_config()


def test_config_normaliza_emoji_do_env() -> None:
    """Emoji certo escrito com o marcador (o teclado gruda) não pode quebrar."""
    c = _load_config_with(TELEGRAM_REACTION_TRANSCRIBED="✍️")
    assert c.telegram_reaction_transcribed == "✍"


def test_config_emoji_invalido_cai_no_default_com_aviso() -> None:
    """O objetivo do fix: trocar o emoji no .env NUNCA MAIS quebra calado.

    Valor que o Telegram recusa vira aviso no log + queda para o padrão. O sinal
    continua aparecendo (é o sinal que importa, não o desenho).
    """
    from bot import config as cfg

    with mock.patch.object(cfg.logger, "warning") as warn:
        c = _load_config_with(TELEGRAM_REACTION_RECEIVED="🎧")
    assert c.telegram_reaction_received == reactions.DEFAULT_RECEIVED
    assert warn.called, "emoji inválido tem que gritar no log, não passar batido"
    assert "TELEGRAM_REACTION_RECEIVED" in str(warn.call_args)


def test_config_env_vazio_continua_desligando_o_estagio() -> None:
    """Vazio é escolha do operador, não erro: silêncio, sem fallback nem aviso."""
    from bot import config as cfg

    with mock.patch.object(cfg.logger, "warning") as warn:
        c = _load_config_with(TELEGRAM_REACTION_TRANSCRIBED="")
    assert c.telegram_reaction_transcribed == ""
    assert not warn.called, "desligar um estágio de propósito não é aviso"


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
