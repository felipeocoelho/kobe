#!/usr/bin/env python3
"""Testes do flush-por-silêncio do tail órfão (item 3 do bug do Chat Manager).

Trava o contrato do `force_resolve_tail` em `classify_topic`: sob silêncio
prolongado, o tail que a histerese seguraria (esperando "a próxima msg") é
RESOLVIDO via juiz GPT-4o-mini (mockado aqui) — descongela o watermark e nunca
deixa msg órfã (conversation_id NULL) eterna. Cobre:

- bug reproduzido: sem force_resolve, tail curto off-subject fica pendente e o
  watermark NÃO avança (loop estéril);
- fix 'continue': juiz diz que continua → tail absorvido no ativo, watermark
  avança até o fim do lote, sem borda;
- fix 'new': juiz diz assunto novo → conversation nova criada + aviso;
- tail só low-info (sem voto) → absorvido sem chamar o juiz.

Sem rede: fake DB que registra stamps + juiz mockado. Rodar:

    .venv/bin/python tests/test_chat_manager_tail_flush.py
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
LONG = "texto suficientemente longo pra contar como informativo de verdade aqui"


class _FakeQuery:
    def __init__(self, db, table, op="select"):
        self._db = db
        self._table = table
        self._op = op
        self._payload = None

    def select(self, *a, **k): self._op = "select"; return self
    def eq(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def gt(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def in_(self, field, values):
        # captura os ids stampados (update conversation_id ... in id [...])
        if self._table == "messages" and self._op == "update":
            self._db.stamped_ids.update(values)
        return self

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
        if self._table == "conversations" and self._op == "update":
            if self._payload.get("status") == "dormant":
                self._db.dormant += 1
            return mock.Mock(data=[])
        return mock.Mock(data=[])


class _FakeDB:
    def __init__(self, pending, active):
        self.pending = pending
        self.active = active
        self.created = 0
        self.dormant = 0
        self.inserted = []
        self.stamped_ids: set[str] = set()

    def table(self, name):
        return _FakeQuery(self, name)


def _msg(i, content, vec, role="user"):
    return {"id": f"m{i}", "role": role, "content": content,
            "created_at": f"2026-06-08T21:15:{i:02d}+00:00", "embedding": vec}


def _active_rows(centroid, title="Assunto velho"):
    return [{"id": "conv-active", "title": title, "centroid_embedding": centroid}]


def _run(db, *, force, judge=("continue", None)):
    knobs = classifier.Knobs()
    with mock.patch.object(classifier, "_judge_tail",
                           new=mock.AsyncMock(return_value=judge)), \
         mock.patch.object(classifier, "_upsert_tags", new=lambda *a, **k: None):
        return asyncio.run(classify_topic(
            db, "topic-1", watermark="2026-06-08T21:14:31+00:00",
            knobs=knobs, force_resolve_tail=force,
        ))


def test_bug_reproduced_without_flush():
    """SEM force_resolve: tail curto off-subject (1 msg) fica órfão e o
    watermark NÃO avança — exatamente o loop estéril observado em prod."""
    pending = [_msg(11, LONG, B), _msg(17, "ok", A, role="assistant")]
    db = _FakeDB(pending, active=_active_rows(A))
    res = _run(db, force=False)
    assert res.watermark is None, f"watermark devia ficar congelado, veio {res.watermark!r}"
    # tail = msg do operador (off-subject) + resposta do agente que veio junto.
    assert res.pending == 2, f"esperava 2 no tail pendente, veio {res.pending}"
    assert db.stamped_ids == set(), "nada deve ser stampado sem flush"


def test_flush_continue_absorbs_into_active():
    """COM force_resolve + juiz 'continue': tail absorvido no assunto ativo,
    watermark avança até o fim do lote, sem borda."""
    pending = [_msg(11, LONG, B), _msg(17, "resposta", A, role="assistant")]
    db = _FakeDB(pending, active=_active_rows(A))
    res = _run(db, force=True, judge=("continue", None))
    assert res.borders == 0, f"continue não abre borda, veio {res.borders}"
    assert res.pending == 0, f"tail devia ser resolvido, veio pending={res.pending}"
    assert res.new_conversations == [], "continue não avisa conversa nova"
    assert res.watermark == "2026-06-08T21:15:17+00:00", \
        f"watermark devia ir até o fim do lote, veio {res.watermark!r}"
    assert {"m11", "m17"} <= db.stamped_ids, f"tail devia ser stampado, veio {db.stamped_ids}"
    assert res.active_conversation_id == "conv-active"


def test_flush_new_opens_conversation():
    """COM force_resolve + juiz 'new': fecha o ativo (dormant), abre conversa
    nova com o título do juiz, avisa, e o watermark avança."""
    pending = [_msg(11, LONG, B), _msg(12, LONG, B2)]
    db = _FakeDB(pending, active=_active_rows(A))
    res = _run(db, force=True, judge=("new", "Assunto Novo do Tail"))
    assert res.borders == 1, f"new abre 1 borda, veio {res.borders}"
    assert db.dormant == 1, "ativo anterior deve virar dormant"
    assert len(res.new_conversations) == 1, f"new deve avisar, veio {res.new_conversations}"
    assert res.new_conversations[0]["title"] == "Assunto Novo do Tail"
    assert res.pending == 0
    assert res.watermark == "2026-06-08T21:15:12+00:00"
    assert res.active_conversation_id == res.new_conversations[0]["id"]


def test_flush_lowinfo_tail_absorbs_without_judge():
    """Tail só com msg low-info (sem voto informativo): absorvido no ativo sem
    chamar o juiz — continuação trivial."""
    pending = [_msg(11, "ok", B), _msg(17, "resposta", A, role="assistant")]
    db = _FakeDB(pending, active=_active_rows(A))
    judge = mock.AsyncMock(return_value=("new", "Não deveria ser chamado"))
    with mock.patch.object(classifier, "_judge_tail", new=judge), \
         mock.patch.object(classifier, "_upsert_tags", new=lambda *a, **k: None):
        res = asyncio.run(classify_topic(
            db, "topic-1", watermark="2026-06-08T21:14:31+00:00",
            knobs=classifier.Knobs(), force_resolve_tail=True,
        ))
    judge.assert_not_called()
    assert res.borders == 0
    assert res.pending == 0
    assert {"m11", "m17"} <= db.stamped_ids
    assert res.watermark == "2026-06-08T21:15:17+00:00"


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
