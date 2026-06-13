#!/usr/bin/env python3
"""Testes da detecção de TRANSIÇÃO no classify_topic (notify de conversa nova).

Trava o contrato de `ClassifyResult.new_conversations`: populado quando uma
borda fecha a conversation ativa e abre outra (transição real → operador deve
ser avisado), e VAZIO no bootstrap (1ª conversation do topic, sem ativa
anterior → nada a avisar). Sem rede: fake DB + embeddings sintéticos +
_make_title_and_tags/_upsert_tags mockados. Rodar:

    .venv/bin/python tests/test_chat_manager_transition.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.chat_manager import classifier
from bot.chat_manager.classifier import classify_topic

A = [1.0, 0.0, 0.0]
A2 = [0.97, 0.12, 0.0]
B = [0.0, 1.0, 0.0]
B2 = [0.0, 0.97, 0.12]
B3 = [0.0, 0.95, 0.1]
LONG = "texto suficientemente longo pra contar como informativo de verdade aqui"


class _FakeQuery:
    """Encadeável; ignora filtros e resolve dados canônicos por (tabela, op)."""

    def __init__(self, db, table, op="select"):
        self._db = db
        self._table = table
        self._op = op
        self._payload = None

    # encadeáveis no-op
    def select(self, *a, **k): self._op = "select"; return self
    def eq(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def in_(self, *a, **k): return self

    def insert(self, payload):
        self._op = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._op = "update"; self._payload = payload; return self

    def execute(self):
        if self._table == "messages" and self._op == "select":
            return mock.Mock(data=list(self._db.pending), count=len(self._db.pending))
        if self._table == "conversations" and self._op == "select":
            return mock.Mock(data=(list(self._db.active) if self._db.active else []))
        if self._table == "conversations" and self._op == "insert":
            self._db.created += 1
            cid = f"conv-{self._db.created}"
            row = dict(self._payload)
            row["id"] = cid
            self._db.inserted.append(row)
            return mock.Mock(data=[row])
        # updates / stamps / etc — no-op
        return mock.Mock(data=[])


class _FakeDB:
    def __init__(self, pending, active):
        self.pending = pending      # rows de messages
        self.active = active         # rows de conversations ativas (ou [])
        self.created = 0
        self.inserted = []

    def table(self, name):
        return _FakeQuery(self, name)


def _msg(i, content, vec, role="user"):
    return {"id": f"m{i}", "role": role, "content": content,
            "created_at": f"2026-06-04T00:00:{i:02d}+00:00", "embedding": vec}


def _run(db, active_centroid=None):
    knobs = classifier.Knobs()  # 0.40/3/0.35
    active_rows = []
    if active_centroid is not None:
        active_rows = [{"id": "conv-active", "title": "Assunto velho",
                        "centroid_embedding": active_centroid}]
    fake = db
    fake.active = active_rows
    # patcha a chamada LLM (título+tags) pra não bater na rede; devolve um
    # tema fixo e nenhuma tag. _upsert_tags vira no-op.
    with mock.patch.object(classifier, "_make_title_and_tags",
                           new=mock.AsyncMock(return_value=("Tema de Teste", []))), \
         mock.patch.object(classifier, "_upsert_tags", new=lambda *a, **k: None):
        return asyncio.run(classify_topic(fake, "topic-1", watermark=None, knobs=knobs))


def test_transition_populates_new_conversations():
    """Ativa em assunto A; chegam msgs de B (borda) → 1 transição avisável."""
    pending = [
        _msg(1, LONG, A2),         # continua A (on-subject)
        _msg(2, LONG, B),          # off-subject -> abre buffer
        _msg(3, LONG, B2),
        _msg(4, LONG, B3),         # sustain=3 confirma borda
    ]
    db = _FakeDB(pending, active=None)
    res = _run(db, active_centroid=A)
    assert res.borders == 1, f"esperava 1 borda, veio {res.borders}"
    assert len(res.new_conversations) == 1, \
        f"transição devia gerar 1 aviso, veio {res.new_conversations}"
    assert res.new_conversations[0]["id"].startswith("conv-")
    # título por tema (LLM) é usado na conversation criada
    assert res.new_conversations[0]["title"] == "Tema de Teste", \
        f"título LLM devia ser usado, veio {res.new_conversations[0]['title']!r}"


def test_llm_title_used_else_literal_fallback():
    """_create_conversation usa o título LLM quando dado; senão, literal."""
    fake = _FakeDB([], active=None)
    # com título LLM
    classifier._create_conversation(fake, "t", "Eu tô vendo aí que blá blá blá",
                                    [1.0, 0.0], title="Formato do Progress Report")
    assert fake.inserted[-1]["title"] == "Formato do Progress Report"
    assert fake.inserted[-1]["slug"]  # slug derivado do título, não vazio
    # sem título LLM (fallback literal = 1ª frase)
    classifier._create_conversation(fake, "t", "Conserta o bug do atrus. Etc.",
                                    [1.0, 0.0], title=None)
    assert fake.inserted[-1]["title"].startswith("Conserta o bug do atrus")


def test_bootstrap_does_not_notify():
    """Topic sem conversation ativa; 1 assunto só → cria, mas NÃO avisa."""
    pending = [_msg(1, LONG, A), _msg(2, LONG, A2)]
    db = _FakeDB(pending, active=None)
    res = _run(db, active_centroid=None)
    assert res.borders == 0, f"bootstrap não tem borda, veio {res.borders}"
    assert res.new_conversations == [], \
        f"bootstrap não deve avisar, veio {res.new_conversations}"
    assert db.created == 1, "bootstrap cria 1 conversation"


def test_continue_no_transition_no_notify():
    """Ativa em A; chegam mais msgs de A → continua, sem aviso."""
    pending = [_msg(1, LONG, A2), _msg(2, LONG, A)]
    db = _FakeDB(pending, active=None)
    res = _run(db, active_centroid=A)
    assert res.borders == 0
    assert res.new_conversations == []


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
