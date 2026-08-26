#!/usr/bin/env python3
"""Arnês de injeção de `Update` (Sessão #1, P9) — e, sobretudo, suas travas.

Esta ferramenta é uma **porta de entrada no bot**: monta um update que diz ser o
operador e o entrega direto ao despachante, pulando o Telegram. O valor dela é
fechar a bateria de aceite sem ninguém digitar; o risco é acabar no caminho de
produção. Por isso a maior parte dos testes aqui é sobre **recusar**.

A assimetria que vale explicar: em `bot/authz.py`, whitelist vazia LIBERA (a
instância não filtra canal). Aqui, whitelist vazia RECUSA — porque significa
"ninguém me disse onde é seguro bater", e injetar às cegas é justamente o que a
trava existe para impedir.

Rodar: .venv/bin/python -m pytest tests/test_dev_inject.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from infra.dev_inject import (
    RecusaDeSeguranca,
    conferir_alvo,
    conferir_ambiente,
    ler_roteiro,
    montar_update,
)

CHAT_DEV = -1004448020751
OPERADOR = 111222333


# ── Trava 1: só em dev ────────────────────────────────────────────────────


def test_recusa_em_producao() -> None:
    with pytest.raises(RecusaDeSeguranca) as exc:
        conferir_ambiente("prod")
    assert "produção" in str(exc.value)


def test_aceita_em_dev() -> None:
    conferir_ambiente("dev")  # não levanta


# ── Trava 2: o alvo tem de estar na lista, e a lista não pode estar vazia ─


def test_whitelist_vazia_recusa() -> None:
    """Aqui vazia NÃO é liberação — é ausência de informação."""
    with pytest.raises(RecusaDeSeguranca) as exc:
        conferir_alvo(CHAT_DEV, frozenset())
    assert "vazia" in str(exc.value)


def test_chat_fora_da_lista_recusa() -> None:
    with pytest.raises(RecusaDeSeguranca) as exc:
        conferir_alvo(-100999, frozenset([CHAT_DEV]))
    assert "-100999" in str(exc.value)


def test_chat_na_lista_passa() -> None:
    conferir_alvo(CHAT_DEV, frozenset([CHAT_DEV]))  # não levanta


def test_a_recusa_sempre_nomeia_a_razao() -> None:
    """Recusa muda é recusa que ninguém consegue destravar."""
    for chamada in (
        lambda: conferir_ambiente("prod"),
        lambda: conferir_alvo(CHAT_DEV, frozenset()),
        lambda: conferir_alvo(1, frozenset([CHAT_DEV])),
    ):
        with pytest.raises(RecusaDeSeguranca) as exc:
            chamada()
        assert len(str(exc.value)) > 40, "mensagem curta demais pra ser acionável"


# ── O `Update` sintético tem de ser fiel ao que o Telegram entrega ────────


def test_update_carrega_chat_topico_usuario_e_texto() -> None:
    u = montar_update(
        update_id=7, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="oi"
    )
    assert u.update_id == 7
    assert u.effective_chat.id == CHAT_DEV
    assert u.effective_user.id == OPERADOR
    assert u.effective_message.text == "oi"
    assert u.effective_message.message_thread_id == 2


def test_mensagem_de_topico_e_marcada_como_tal() -> None:
    """Sem `is_topic_message`, o bot trata como "Geral" e não carrega a KB do
    tópico — o teste passaria exercitando um caminho diferente do real."""
    com_topico = montar_update(
        update_id=1, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="x"
    )
    sem_topico = montar_update(
        update_id=2, chat_id=CHAT_DEV, thread_id=None, user_id=OPERADOR, texto="x"
    )
    assert com_topico.effective_message.is_topic_message is True
    assert sem_topico.effective_message.is_topic_message is False


def test_o_update_sintetico_passa_pela_autorizacao_real() -> None:
    """Prova de fidelidade: o que a ferramenta monta é aceito por `bot/authz.py`.

    Se não passasse, a bateria testaria a ferramenta em vez de testar o bot.
    """
    from types import SimpleNamespace

    from bot import authz

    cfg = SimpleNamespace(
        allowed_user_ids=frozenset([OPERADOR]),
        telegram_allowed_chat_ids=frozenset([CHAT_DEV]),
    )
    u = montar_update(
        update_id=1, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="x"
    )
    assert authz.update_authorized(u, cfg) is True

    intruso = montar_update(
        update_id=2, chat_id=-100999, thread_id=None, user_id=OPERADOR, texto="x"
    )
    assert authz.update_authorized(intruso, cfg) is False


# ── Roteiro ───────────────────────────────────────────────────────────────


def test_roteiro_simples(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("primeira\nsegunda\n", encoding="utf-8")
    assert ler_roteiro(arq) == [(0.0, "primeira"), (0.0, "segunda")]


def test_roteiro_ignora_comentario_e_linha_vazia(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("# bateria\n\nprimeira\n   \n# fim\n", encoding="utf-8")
    assert ler_roteiro(arq) == [(0.0, "primeira")]


def test_roteiro_com_espera(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("primeira\n@30 segunda\n@1.5 terceira\n", encoding="utf-8")
    assert ler_roteiro(arq) == [
        (0.0, "primeira"),
        (30.0, "segunda"),
        (1.5, "terceira"),
    ]


def test_espera_invalida_aponta_a_linha(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("ok\n@depois mensagem\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ler_roteiro(arq)
    assert ":2:" in str(exc.value)


def test_espera_sem_mensagem_e_erro(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("@30\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ler_roteiro(arq)
    assert "sem mensagem" in str(exc.value)


def test_roteiro_vazio_e_erro(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("# só comentário\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ler_roteiro(arq)


# ── A ferramenta não pode encostar no caminho de produção ─────────────────


def test_nada_do_bot_importa_o_arnes() -> None:
    """`infra/dev_inject.py` é folha: o bot não sabe que ele existe."""
    culpados = [
        str(f.relative_to(RAIZ))
        for f in RAIZ.joinpath("bot").rglob("*.py")
        if "dev_inject" in f.read_text(encoding="utf-8")
    ]
    assert not culpados, f"o caminho de produção importa o arnês: {culpados}"
