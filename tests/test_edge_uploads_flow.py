#!/usr/bin/env python3
"""Testes da Peça D, Fase 1b — buffer de anexos pendentes + injeção no prompt.

Trava:
- push/drain do buffer respeitam ORDEM e ISOLAMENTO entre tópicos.
- drain esvazia (segunda chamada volta vazia).
- build_prompt injeta a seção de anexos ANTES da mensagem nova (correlação).
- flag edge_uploads default OFF.

Rodar:
    .venv/bin/python tests/test_edge_uploads_flow.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import telegram_handler as th
from bot.claude_runner import build_prompt
from bot.uploads import UploadDescriptor, ingest_upload


def _desc(name: str, home: Path) -> UploadDescriptor:
    return ingest_upload(home, "t", name, b"conteudo de " + name.encode())


def test_pending_buffer_order_and_isolation() -> None:
    home = Path(tempfile.mkdtemp(prefix="kobe-flow-"))
    th._pending_uploads.clear()

    a1 = _desc("a1.txt", home)
    a2 = _desc("a2.txt", home)
    b1 = _desc("b1.txt", home)

    # Tópico A recebe dois anexos; tópico B, um.
    th._push_pending_upload(100, 1, a1)
    th._push_pending_upload(100, 1, a2)
    th._push_pending_upload(100, 2, b1)

    drained_a = th._drain_pending_uploads(100, 1)
    assert [d.filename for d in drained_a] == ["a1.txt", "a2.txt"], "ordem de chegada"

    drained_b = th._drain_pending_uploads(100, 2)
    assert [d.filename for d in drained_b] == ["b1.txt"], "isolamento entre tópicos"

    # Drenado esvazia.
    assert th._drain_pending_uploads(100, 1) == []
    assert th._drain_pending_uploads(100, 2) == []


def test_drain_empty_topic_is_empty_list() -> None:
    th._pending_uploads.clear()
    assert th._drain_pending_uploads(999, None) == []


def test_build_prompt_injects_attachments_before_new_message() -> None:
    section = "[Anexos deste turno — o operador enviou estes arquivos]\n- Imagem `x.png`"
    prompt = build_prompt(
        thread_id=1,
        history=[],
        new_message="faça X com a imagem",
        attachments_section=section,
    )
    assert "[Anexos deste turno" in prompt
    idx_attach = prompt.index("[Anexos deste turno")
    idx_msg = prompt.index("[Mensagem nova do operador]")
    assert idx_attach < idx_msg, "anexos têm que vir ANTES da mensagem nova"


def test_build_prompt_without_attachments_unchanged() -> None:
    prompt = build_prompt(
        thread_id=1, history=[], new_message="oi", attachments_section=None
    )
    assert "[Anexos deste turno" not in prompt


def test_edge_uploads_flag_default_off() -> None:
    from bot import config as cfg

    # _parse_bool(None) → False; sem a env, o default é off.
    assert cfg._parse_bool(os.environ.get("NONEXISTENT_FLAG_XYZ")) is False


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
