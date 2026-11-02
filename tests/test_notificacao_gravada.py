#!/usr/bin/env python3
"""A notificação das salas entra na conversa (Highlander v3, F7).

Medido na rodada 2 da F7: 37 de 90 vezes em que o operador respondeu CITANDO uma
mensagem, a citada não existia em `messages` — era notificação de sala ou legenda
de anexo. Ligada a chave, `kobe-notify`/`kobe-attach` gravam o que mandaram.

O que se guarda aqui: a chave nasce desligada; ligada, a notificação vira fala do
agente com a marca `[notificação]`, no tópico certo e com o id do Telegram; e
nada disso pode fazer a notificação falhar. Banco: transação revertida no
teardown — o teste passa a própria conexão ao gravador, que não commita.

    KOBE_TEST_DATABASE_URL=<banco> .venv/bin/python -m pytest -q tests/test_notificacao_gravada.py
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "bot" / "bin"))

import _kobe_topic  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


def test_chave_desligada_por_omissao(monkeypatch):
    monkeypatch.delenv("KOBE_NOTIFY_RECORD_ENABLED", raising=False)
    monkeypatch.setattr(_kobe_topic, "PROJECT_ROOT", Path("/nao/existe"))
    assert _kobe_topic.registro_ligado() is False
    # desligada, não toca em banco nenhum e não falha
    assert _kobe_topic.gravar_notificacao(1, None, "x", 1) is False


def test_falha_de_banco_nunca_derruba_a_notificacao(capsys):
    assert _kobe_topic.gravar_notificacao(1, None, "x", 1,
                                          conninfo="postgresql:///nao_existe_kobe?host=/nao") is False
    assert "não foi gravada" in capsys.readouterr().err


@pytest.fixture
def banco():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    import psycopg
    from psycopg.rows import dict_row

    cx = psycopg.connect(_URL, row_factory=dict_row)
    chat = -random.randint(10**12, 10**13)
    thread = random.randint(10**5, 10**6)
    topic = cx.execute("INSERT INTO topics (current_name, telegram_chat_id, telegram_thread_id)"
                       " VALUES ('teste f7 notificação', %s, %s) RETURNING id",
                       (chat, thread)).fetchone()["id"]
    session = cx.execute("INSERT INTO sessions (topic_id, status) VALUES (%s, 'active') RETURNING id",
                         (topic,)).fetchone()["id"]
    try:
        yield cx, chat, thread, topic, session
    finally:
        cx.rollback()
        cx.close()


def test_ligada_grava_como_fala_do_agente_marcada(banco):
    cx, chat, thread, topic, session = banco
    assert _kobe_topic.gravar_notificacao(chat, thread, "🟢 [coder] concluído: teste", 987654,
                                          conn=cx) is True
    r = cx.execute("SELECT role, content, telegram_message_id, topic_id FROM messages"
                   " WHERE session_id = %s", (session,)).fetchone()
    assert r["role"] == "assistant"
    assert r["content"] == "[notificação] 🟢 [coder] concluído: teste"
    assert r["telegram_message_id"] == 987654 and r["topic_id"] == topic


def test_topico_desconhecido_nao_grava(banco):
    cx = banco[0]
    assert _kobe_topic.gravar_notificacao(-1, 1, "x", 1, conn=cx) is False
