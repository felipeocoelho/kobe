#!/usr/bin/env python3
"""Trava do endereço da fábrica do cliente OpenAI (`bot/openai_client.py`).

**Por que este arquivo existe.** A fábrica `_get_openai()` morava dentro de
`bot/conversation_detector.py` — o detector do Chat Manager, que foi aposentado.
Duas funções que NÃO são do Chat Manager importavam de lá, em import tardio
dentro da função:

- `bot.liveness._chamar_modelo` → o ack "já te retorno" da borda (provider
  `openai`, que é o default e o que roda em produção). O import não está
  protegido: se o endereço quebrar, `ImportError` sobe e o ack vira fallback
  fixo — o operador perde o ack semântico sem nenhum erro na cara.
- `bot.turn_classifier._ask_mini` → o desempate de zona cinza (PESADO vs LEVE).
  O import está dentro de um `except Exception` que devolve `None`. Se o
  endereço quebrar, o classificador degrada **CALADO** para o default
  conservador, e nada na suíte acusa.

A suíte anterior à mudança não pegava nenhum dos dois casos (import tardio +
`except` que engole). Estes testes existem para que a próxima pessoa que mexer
no endereço do client quebre AQUI, e não em produção.

Rodar: .venv/bin/python -m pytest tests/test_openai_client.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot import liveness, openai_client, turn_classifier  # noqa: E402


def _fake_openai(content: str):
    """Client OpenAI fake cujo `create()` devolve `content`."""
    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = content
    client = mock.MagicMock()
    client.chat.completions.create = mock.AsyncMock(return_value=resp)
    return client


# ── O módulo neutro em si ─────────────────────────────────────────────────


def test_fabrica_e_singleton() -> None:
    """Recriar o client a cada chamada joga fora o keep-alive — e é o
    keep-alive que segura a latência do ack dentro do orçamento."""
    openai_client._openai_client = None
    try:
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-teste"}):
            with mock.patch.object(openai_client, "AsyncOpenAI") as ctor:
                ctor.return_value = object()
                a = openai_client._get_openai()
                b = openai_client._get_openai()
        assert a is b
        assert ctor.call_count == 1
    finally:
        openai_client._openai_client = None


def test_fabrica_levanta_sem_chave() -> None:
    openai_client._openai_client = None
    try:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            try:
                openai_client._get_openai()
            except RuntimeError as exc:
                assert "OPENAI_API_KEY" in str(exc)
            else:
                raise AssertionError("esperava RuntimeError sem a chave")
    finally:
        openai_client._openai_client = None


# ── Consumidor vivo 1: o ack da borda (provider=openai) ───────────────────


def test_ack_da_borda_com_provider_openai_continua_saindo() -> None:
    """Se o endereço do client quebrar, o ImportError sobe do import tardio de
    `_chamar_modelo` e o ack cai pro fallback — este teste pega isso."""
    fake = _fake_openai("Vou varrer o repositório e já te retorno.")
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-teste"}):
        os.environ.pop("LIVENESS_ACK_PROVIDER", None)  # default = openai
        with mock.patch("bot.openai_client._get_openai", return_value=fake):
            out = asyncio.run(liveness.write_ack("faz uma varredura no repositório"))
    assert out == "Vou varrer o repositório e já te retorno."
    assert out != liveness.fallback_ack(), "caiu no fallback — o client não foi usado"
    fake.chat.completions.create.assert_awaited_once()


# ── Consumidor vivo 2: o desempate de zona cinza ──────────────────────────


def test_classificador_de_zona_cinza_devolve_veredito_nao_none() -> None:
    """O `except` de `_ask_mini` engole ImportError e devolve None calado. Um
    veredito real (não-None) é a prova de que o client foi alcançado."""
    fake = _fake_openai("PESADO")
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-teste"}):
        with mock.patch("bot.openai_client._get_openai", return_value=fake):
            veredito = asyncio.run(turn_classifier._ask_mini("refatora o handler"))
    assert veredito == "PESADO", (
        "None aqui = o mini não respondeu; se for por ImportError, a degradação "
        "é silenciosa em produção"
    )


def test_zona_cinza_usa_o_mini_no_caminho_completo() -> None:
    """Mesma prova, mas pela porta de entrada real (`classify_turn`), pra
    garantir que a zona cinza chega mesmo no client."""
    fake = _fake_openai("PESADO")
    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-teste"}):
        with mock.patch("bot.openai_client._get_openai", return_value=fake):
            decisao = asyncio.run(
                turn_classifier.classify_turn(
                    "dá uma olhada nisso aqui pra mim quando puder",
                    score_high=99,   # força a zona cinza: nunca decide pelo placar
                    score_low=-99,
                )
            )
    assert fake.chat.completions.create.await_count == 1, "o mini não foi consultado"
    assert decisao.route == turn_classifier.ROUTE_BACKGROUND


# ── Conformidade: o acoplamento não pode voltar ───────────────────────────


def test_ninguem_importa_a_fabrica_do_detector_de_conversa() -> None:
    """O detector de conversa foi aposentado. Reimportar a fábrica de lá (ou de
    qualquer módulo de Chat Manager) recria exatamente o acoplamento que
    quebrava o ack em runtime."""
    culpados = []
    for arquivo in RAIZ.joinpath("bot").rglob("*.py"):
        texto = arquivo.read_text(encoding="utf-8")
        for proibido in (
            "from bot.conversation_detector import _get_openai",
            "from bot.conversation_detector import JUDGE_MODEL",
        ):
            if proibido in texto:
                culpados.append(f"{arquivo.relative_to(RAIZ)}: {proibido}")
    assert not culpados, (
        "a fábrica do client OpenAI mora em bot/openai_client.py: " f"{culpados}"
    )


if __name__ == "__main__":  # pragma: no cover
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
