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


# ── Guarda-costas anti-invenção (correção de 2026-08-20) ──────────────────
#
# O ack inventava plano/arquivo/ferramenta porque o prompt mandava "NOMEAR a
# ação" a um modelo que só vê a mensagem do operador — pedir especificidade a
# quem não tem informação nenhuma produz chute. O prompt foi invertido e ganhou
# este guarda-costas, atrás de flag própria (LIVENESS_ACK_GUARD_ENABLED).
#
# O guarda-costas é CONSERVADOR de propósito: falso positivo custa o operador
# ver o texto fixo; falso negativo custa uma invenção chegando como verdade.


def test_prompt_proibe_fato_novo() -> None:
    """Trava do prompt: a instrução tem que PROIBIR, não pedir especificidade.

    Protege contra alguém reintroduzir o 'NOMEANDO a ação' (e o exemplo que era
    ele próprio uma invenção) numa edição futura.
    """
    for prompt in (liveness._SYS_START, liveness._SYS_LATE):
        assert "PROIBIDO" in prompt
        assert "NÃO SABE" in prompt and "NÃO PODE SUPOR" in prompt
        assert "NOMEANDO a ação" not in prompt
    assert "varrer a VPS atrás dos arquivos elegíveis" not in liveness._SYS_START


def test_guard_pega_arquivo_inventado() -> None:
    rej, motivo = liveness._tem_invencao(
        "Beleza, vou olhar o bot/liveness.py e já te volto.", "vê aquilo do ack"
    )
    assert rej and "arquivo" in motivo


def test_guard_pega_numero_inventado() -> None:
    rej, motivo = liveness._tem_invencao(
        "Vou cuidar disso, me dá 5 minutos.", "resolve aquilo pra mim"
    )
    assert rej, motivo


def test_guard_pega_termo_tecnico_inventado() -> None:
    rej, motivo = liveness._tem_invencao(
        "Vou varrer os arquivos do servidor e já te retorno.", "dá uma olhada naquilo"
    )
    assert rej and "termo técnico" in motivo


def test_guard_libera_o_que_o_operador_disse() -> None:
    """A régua é sempre 'está na mensagem dele?'. Se o operador falou em
    arquivos, o ack pode falar em arquivos — não é invenção, é ancoragem."""
    rej, motivo = liveness._tem_invencao(
        "Beleza — vou providenciar essa varredura de arquivos e já te volto.",
        "faz uma varredura nos arquivos antigos pra mim",
    )
    assert not rej, motivo


def test_guard_libera_ack_ancorado_normal() -> None:
    rej, motivo = liveness._tem_invencao(
        "Beleza, vou providenciar isso do relatório de ontem e já te volto.",
        "me monta o relatório de ontem",
    )
    assert not rej, motivo


def test_guard_nao_confunde_e_ou_com_caminho() -> None:
    """Falso positivo que o regex ingênuo cometia: 'e/ou' virava 'caminho'."""
    rej, motivo = liveness._tem_invencao(
        "Vou olhar isso e/ou te retornar já.", "olha isso aí"
    )
    assert not rej, motivo


def test_guard_ignora_acento_e_caixa() -> None:
    rej, _ = liveness._tem_invencao("Vou checar o DIRETÓRIO já.", "checa aquilo")
    assert rej, "'DIRETÓRIO' tem que casar com 'diretorio' da lista"


def test_ack_inventado_cai_no_fallback_com_guard_ligado() -> None:
    fake = _fake_openai("Beleza, vou varrer o /home/x atrás de 3 arquivos e já volto.")
    env = {"OPENAI_API_KEY": "x", "LIVENESS_ACK_GUARD_ENABLED": "true"}
    with mock.patch.dict(os.environ, env):
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            out = asyncio.run(liveness.write_ack("resolve aquilo lá pra mim"))
    assert out == liveness.fallback_ack(), "invenção não pode chegar ao operador"


def test_ack_ancorado_passa_com_guard_ligado() -> None:
    fake = _fake_openai("Beleza — vou providenciar isso do orçamento e já te volto.")
    env = {"OPENAI_API_KEY": "x", "LIVENESS_ACK_GUARD_ENABLED": "true"}
    with mock.patch.dict(os.environ, env):
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            out = asyncio.run(liveness.write_ack("me resolve o orçamento"))
    assert out == "Beleza — vou providenciar isso do orçamento e já te volto."


def test_guard_desligado_deixa_passar() -> None:
    """Flag própria: o guarda-costas é reversível sem mexer no prompt."""
    inventado = "Beleza, vou varrer o /home/x atrás de 3 arquivos e já volto."
    fake = _fake_openai(inventado)
    env = {"OPENAI_API_KEY": "x", "LIVENESS_ACK_GUARD_ENABLED": "false"}
    with mock.patch.dict(os.environ, env):
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            out = asyncio.run(liveness.write_ack("resolve aquilo"))
    assert out == inventado


# ── Modelo configurável (requisito acordado com o operador) ───────────────


def test_modelo_default_e_o_de_hoje() -> None:
    """Trocar de modelo é decisão do operador, com resultado na mão — o default
    tem que continuar sendo exatamente o de hoje."""
    fake = _fake_openai("ok, já te volto")
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}, clear=False):
        os.environ.pop("LIVENESS_ACK_MODEL", None)
        os.environ.pop("LIVENESS_ACK_PROVIDER", None)
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            asyncio.run(liveness.write_ack("tarefa"))
    assert fake.chat.completions.create.await_args.kwargs["model"] == "gpt-4o-mini"


def test_modelo_configuravel_por_env() -> None:
    fake = _fake_openai("ok, já te volto")
    env = {"OPENAI_API_KEY": "x", "LIVENESS_ACK_MODEL": "gpt-4o"}
    with mock.patch.dict(os.environ, env):
        with mock.patch("bot.conversation_detector._get_openai", return_value=fake):
            asyncio.run(liveness.write_ack("tarefa"))
    assert fake.chat.completions.create.await_args.kwargs["model"] == "gpt-4o"


def test_provider_anthropic_usa_a_chave_certa() -> None:
    """Trocar por Haiku depois tem que ser uma linha no .env, sem obra."""
    bloco = mock.MagicMock()
    bloco.type, bloco.text = "text", "Beleza, já te volto."
    resp = mock.MagicMock()
    resp.content = [bloco]
    client = mock.MagicMock()
    client.messages.create = mock.AsyncMock(return_value=resp)
    fake_mod = mock.MagicMock()
    fake_mod.AsyncAnthropic = mock.MagicMock(return_value=client)

    env = {
        "ANTHROPIC_API_KEY": "k",
        "LIVENESS_ACK_PROVIDER": "anthropic",
        "LIVENESS_ACK_MODEL": "claude-haiku-4-5-20251001",
    }
    with mock.patch.dict(os.environ, env):
        with mock.patch.dict(sys.modules, {"anthropic": fake_mod}):
            out = asyncio.run(liveness.write_ack("tarefa"))
    assert out == "Beleza, já te volto."
    assert client.messages.create.await_args.kwargs["model"] == "claude-haiku-4-5-20251001"


def test_sem_credencial_do_provider_cai_no_fallback() -> None:
    env = {"LIVENESS_ACK_PROVIDER": "anthropic"}
    with mock.patch.dict(os.environ, env, clear=False):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        out = asyncio.run(liveness.write_ack("tarefa"))
    assert out == liveness.fallback_ack()


def test_provider_desconhecido_nunca_derruba_o_turno() -> None:
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x", "LIVENESS_ACK_PROVIDER": "vixe"}):
        out = asyncio.run(liveness.write_ack("tarefa"))
    assert out == liveness.fallback_ack()


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
