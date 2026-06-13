#!/usr/bin/env python3
"""Testes determinísticos da detecção de borda do New Chat Manager.

Usa vetores sintéticos (sem rede) pra travar a lógica do buffer/pista/
digressão — complementa a calibração com embeddings reais
(infra/calibrate_chat_manager.py). Rodar:

    .venv/bin/python tests/test_chat_manager_classifier.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.chat_manager.classifier import CMsg, Knobs, detect_segments


K = Knobs()  # defaults calibrados: border 0.40, sustain 3, coherence 0.35

A = [1.0, 0.0, 0.0]
A2 = [0.97, 0.12, 0.0]
A3 = [0.95, 0.0, 0.1]
B = [0.0, 1.0, 0.0]
B2 = [0.0, 0.97, 0.12]
B3 = [0.0, 0.95, 0.1]
LONG = "texto suficientemente longo pra contar como informativo de verdade aqui"


def _u(i, content, vec):
    return CMsg(id=f"u{i}", role="user", content=content, embedding=vec)


def _a(i):
    return CMsg(id=f"a{i}", role="assistant", content="resposta")


def _seg(msgs):
    p = detect_segments(msgs, None, K)
    return len(p.segments), len(p.pending_tail_ids)


def test_single_subject():
    m = [_u(0, LONG, A), _a(0), _u(1, LONG, A2), _u(2, LONG, A3)]
    assert _seg(m) == (1, 0)


def test_vector_border():
    m = [_u(0, LONG, A), _u(1, LONG, A2), _u(2, LONG, A3),
         _u(3, LONG, B), _u(4, LONG, B2), _u(5, LONG, B3)]
    assert _seg(m) == (2, 0)


def test_digression_returns():
    m = [_u(0, LONG, A), _u(1, LONG, A2), _u(2, LONG, B),
         _u(3, LONG, A3), _u(4, LONG, A2)]
    assert _seg(m) == (1, 0)


def test_short_reply_no_cut():
    m = [_u(0, LONG, A), _u(1, "Kobe", B), _u(2, LONG, A2)]
    assert _seg(m) == (1, 0)


def test_switch_cue_with_onsubject_vector():
    # Pista lexical arma a borda mesmo com vetor on-subject na msg-pivô.
    m = [_u(0, LONG, A), _u(1, LONG, A2),
         _u(2, "muda de assunto, " + LONG, A3),
         _u(3, LONG, B), _u(4, LONG, B2)]
    assert _seg(m) == (2, 0)


def test_pending_tail():
    # 2 off-subject (< sustain=3) ficam pendentes pra próxima passada.
    m = [_u(0, LONG, A), _u(1, LONG, A2), _u(2, LONG, A3),
         _u(3, LONG, B), _u(4, LONG, B2)]
    p = detect_segments(m, None, K)
    assert len(p.segments) == 1 and len(p.pending_tail_ids) == 2


def test_continue_existing_no_border():
    m = [_u(0, LONG, A2), _u(1, LONG, A3)]
    p = detect_segments(m, A, K)
    assert len(p.segments) == 1 and p.segments[0].is_new is False


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
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
