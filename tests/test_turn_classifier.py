#!/usr/bin/env python3
"""Testes da cascata de classificação de turno (despacho pesado em background).

Trava o contrato das 4 fileiras:
1. roteamento por tipo (slash pesado/leve)
2+3. placar estrutural + léxico
4. zona cinza → mini (mockado, sem rede)

E o default conservador (mini indisponível → foreground). Rodar:

    .venv/bin/python tests/test_turn_classifier.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import turn_classifier as tc
from bot.turn_classifier import (
    ROUTE_BACKGROUND,
    ROUTE_FOREGROUND,
    classify_turn,
    route_by_type,
    score_turn,
)

HIGH = 6
LOW = 2


def _classify(text, **kw):
    return asyncio.run(
        classify_turn(text, score_high=HIGH, score_low=LOW, **kw)
    )


# ── 1ª fileira — roteamento por tipo ──────────────────────────────────────

def test_slash_pesado_vai_background():
    assert route_by_type("/transcrever https://x.com/a") == ROUTE_BACKGROUND
    assert route_by_type("/imagem um gato astronauta") == ROUTE_BACKGROUND
    assert route_by_type("/monet logo") == ROUTE_BACKGROUND


def test_slash_leve_vai_foreground():
    for cmd in ("/contexto", "/nova", "/conversa x", "/alerta_lista",
                "/renomear x", "/salvar y", "/retomar z", "/handoff"):
        assert route_by_type(cmd) == ROUTE_FOREGROUND, cmd


def test_slash_com_botname_suffix():
    assert route_by_type("/contexto@kobebot") == ROUTE_FOREGROUND
    assert route_by_type("/transcrever@kobebot url") == ROUTE_BACKGROUND


def test_slash_case_insensitive():
    assert route_by_type("/CONTEXTO") == ROUTE_FOREGROUND
    assert route_by_type("/Transcrever url") == ROUTE_BACKGROUND


def test_slash_desconhecido_e_texto_caem_no_placar():
    assert route_by_type("/algumacoisa nova") is None
    assert route_by_type("oi tudo bem?") is None


# ── 2ª + 3ª fileiras — placar ─────────────────────────────────────────────

def test_papo_pontua_zero():
    s, _ = score_turn("oi, tudo bem? o que você acha disso?")
    assert s == 0


def test_varredura_pontua_alto():
    s, _ = score_turn("varre o repo todo e audita cada arquivo")
    assert s >= HIGH


def test_path_e_lexico_somam():
    s, _ = score_turn("refatora o bot/telegram_handler.py")
    assert s >= HIGH  # path(+3) + lex refatora(+4)


def test_multi_etapa_soma():
    s, _ = score_turn(
        "primeiro lê o config, depois reescreve o parser e por fim roda os testes"
    )
    assert s >= HIGH


def test_url_unica_e_sinal_fraco():
    s, _ = score_turn("dá uma olhada nesse link https://example.com/x")
    assert s <= LOW  # 1 url = +2, exatamente no corte → foreground


# ── classify_turn (cascata inteira) ───────────────────────────────────────

def test_classify_foreground_obvio_sem_mini():
    d = _classify("oi, beleza?")
    assert d.route == ROUTE_FOREGROUND
    assert d.used_mini is False


def test_classify_background_obvio_sem_mini():
    d = _classify("varre o repo todo e audita cada arquivo de segurança")
    assert d.route == ROUTE_BACKGROUND
    assert d.used_mini is False


def test_classify_slash_pesado_short_circuit():
    d = _classify("/transcrever https://x.com/a")
    assert d.route == ROUTE_BACKGROUND
    assert d.reason == "type-routing"
    assert d.used_mini is False


def test_zona_cinza_chama_mini_pesado():
    # score 4 (lex "implementa" 2x) cai na zona cinza (LOW<4<HIGH).
    # patch.object detecta async e cria AsyncMock → return_value é o valor já
    # resolvido (não embrulhar em coroutine).
    with mock.patch.object(tc, "_ask_mini", return_value="PESADO"):
        d = _classify("implementa o parser novo")
    assert d.used_mini is True
    assert d.route == ROUTE_BACKGROUND


def test_zona_cinza_chama_mini_leve():
    with mock.patch.object(tc, "_ask_mini", return_value="LEVE"):
        d = _classify("implementa o parser novo")
    assert d.used_mini is True
    assert d.route == ROUTE_FOREGROUND


def test_zona_cinza_mini_indisponivel_default_foreground():
    with mock.patch.object(tc, "_ask_mini", return_value=None):
        d = _classify("implementa o parser novo")
    assert d.used_mini is True
    assert d.route == ROUTE_FOREGROUND  # default conservador


def test_anexo_empurra_score():
    # Sem anexo, "manda isso aí" é leve; com anexo soma +3.
    d_sem = _classify("manda isso aí")
    assert d_sem.route == ROUTE_FOREGROUND
    with mock.patch.object(tc, "_ask_mini", return_value="LEVE"):
        d_com = _classify("manda isso aí", has_attachment=True)
    # +3 entra na zona cinza (3) → mini consultado.
    assert d_com.used_mini is True


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
