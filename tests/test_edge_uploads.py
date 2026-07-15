#!/usr/bin/env python3
"""Testes da Peça D (anexos) — helpers de uploads/, Normalizer e catálogo.

Trava o contrato:
- uploads/ é separado do knowledge/ e preserva a EXTENSÃO ORIGINAL (dedupe).
- classify_kind acerta imagem/documento/outro.
- ingest_upload salva o original cru, extrai texto de doc, cataloga num arquivo
  markdown ÚNICO (agnóstico de tópico) com header uma vez só.
- render_attachments_section frasa imagem (Read), doc (texto inline) e vazio→None.

Rodar:
    .venv/bin/python tests/test_edge_uploads.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import uploads
from bot.topic_manager import (
    topic_knowledge_dir,
    topic_uploads_dir,
    unique_upload_path,
)


def _tmp_home() -> Path:
    return Path(tempfile.mkdtemp(prefix="kobe-uploads-test-"))


def test_uploads_dir_separate_from_knowledge() -> None:
    home = _tmp_home()
    up = topic_uploads_dir(home, "dev-kobe")
    kb = topic_knowledge_dir(home, "dev-kobe")
    assert up != kb, "uploads/ não pode colidir com knowledge/"
    assert up.name == "uploads"
    assert kb.name == "knowledge"
    assert up.parent == kb.parent, "ambos moram sob topics/<slug>/"


def test_unique_upload_preserves_extension_and_dedupes() -> None:
    home = _tmp_home()
    d = topic_uploads_dir(home, "pessoal")
    d.mkdir(parents=True)

    p1 = unique_upload_path(home, "pessoal", "foto.png")
    assert p1.suffix == ".png", "extensão original tem que ser preservada"
    p1.write_bytes(b"x")

    p2 = unique_upload_path(home, "pessoal", "foto.png")
    assert p2 != p1 and p2.suffix == ".png"
    assert p2.stem == "foto-2", f"dedupe esperado foto-2, veio {p2.stem}"


def test_unique_upload_sanitizes_separators() -> None:
    home = _tmp_home()
    topic_uploads_dir(home, "x").mkdir(parents=True)
    p = unique_upload_path(home, "x", "a/b/c.pdf")
    assert "/" not in p.name and p.suffix == ".pdf"


def test_private_and_general_get_own_uploads_dir() -> None:
    # O chat privado (DM, sem thread_id) e o general do supergrupo são tópicos
    # como qualquer outro: derivam do SLUG ("private"/"general"), então têm pasta
    # de uploads própria e distinta — nunca caem num balde comum nem sem pasta.
    home = _tmp_home()
    priv = topic_uploads_dir(home, "private")
    gen = topic_uploads_dir(home, "general")
    forum = topic_uploads_dir(home, "dev-kobe")
    assert priv != gen != forum and priv != forum, "cada tópico tem pasta própria"
    assert priv.parts[-2:] == ("private", "uploads")
    assert gen.parts[-2:] == ("general", "uploads")

    # E o catálogo central lista os três juntos (visão cross-tópico inclui o privado).
    ts = datetime(2026, 7, 14, 15, 0, tzinfo=uploads.OPERATOR_TZ)
    uploads.ingest_upload(home, "private", "p.txt", b"oi", now=ts)
    uploads.ingest_upload(home, "general", "g.txt", b"oi", now=ts)
    uploads.ingest_upload(home, "dev-kobe", "d.txt", b"oi", now=ts)
    text = (home / uploads.CATALOG_RELATIVE).read_text(encoding="utf-8")
    assert "| private |" in text and "| general |" in text and "| dev-kobe |" in text


def test_classify_kind() -> None:
    assert uploads.classify_kind("foto.JPG") == uploads.KIND_IMAGE
    assert uploads.classify_kind("x.png") == uploads.KIND_IMAGE
    assert uploads.classify_kind("qualquer", mime="image/webp") == uploads.KIND_IMAGE
    assert uploads.classify_kind("relatorio.pdf") == uploads.KIND_DOCUMENT
    assert uploads.classify_kind("notas.md") == uploads.KIND_DOCUMENT
    assert uploads.classify_kind("planilha.xlsx") == uploads.KIND_OTHER
    assert uploads.classify_kind("dados.bin") == uploads.KIND_OTHER


def test_ingest_saves_original_and_extracts_text() -> None:
    home = _tmp_home()
    raw = "linha 1\nlinha 2".encode("utf-8")
    desc = uploads.ingest_upload(home, "dev-kobe", "notas.txt", raw)

    assert desc.kind == uploads.KIND_DOCUMENT
    assert desc.path.exists()
    assert desc.path.read_bytes() == raw, "original tem que ser salvo cru"
    assert desc.path.suffix == ".txt"
    assert desc.extracted_text == "linha 1\nlinha 2"
    assert desc.size == len(raw)
    # Mora em uploads/, não em knowledge/.
    assert desc.path.parent == topic_uploads_dir(home, "dev-kobe")


def test_ingest_image_has_no_extracted_text() -> None:
    home = _tmp_home()
    desc = uploads.ingest_upload(home, "dev-kobe", "print.png", b"\x89PNG\r\n")
    assert desc.kind == uploads.KIND_IMAGE
    assert desc.extracted_text is None
    assert desc.path.read_bytes() == b"\x89PNG\r\n"


def test_catalog_single_file_header_once_and_appends() -> None:
    home = _tmp_home()
    ts = datetime(2026, 7, 14, 14, 32, tzinfo=uploads.OPERATOR_TZ)
    uploads.ingest_upload(home, "dev-kobe", "a.txt", b"oi", now=ts)
    uploads.ingest_upload(home, "pessoal", "b.png", b"\x89P", now=ts)

    catalog = home / uploads.CATALOG_RELATIVE
    assert catalog.exists(), "catálogo único tem que existir"
    text = catalog.read_text(encoding="utf-8")
    # Header (título) só uma vez.
    assert text.count("# Catálogo de uploads") == 1
    # Duas linhas de dados, de tópicos DIFERENTES (agnóstico de tópico).
    assert "| dev-kobe |" in text and "| pessoal |" in text
    assert "`a.txt`" in text and "`b.png`" in text
    assert text.count("2026-07-14 14:32") == 2


def test_render_attachments_section() -> None:
    home = _tmp_home()
    img = uploads.ingest_upload(home, "t", "foto.png", b"\x89P")
    doc = uploads.ingest_upload(home, "t", "n.txt", b"conteudo")

    section = uploads.render_attachments_section([img, doc])
    assert section is not None
    assert "[Anexos deste turno" in section
    assert "Read" in section, "imagem deve instruir a usar Read"
    assert str(img.path) in section
    assert "conteudo" in section, "texto do doc deve entrar inline"

    assert uploads.render_attachments_section([]) is None


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
