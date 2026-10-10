#!/usr/bin/env python3
"""O `kobe-kb` chamado de verdade — o teste que faltava.

POR QUE ESTE TESTE EXISTE
--------------------------
Até 07/10/2026 o `bot/bin/kobe-kb` abria o banco com `KobeDB()` — sem a
conexão. O construtor exige o endereço, então **todo** comando do porteiro
saía como "FALHA DO INSTRUMENTO": admitir, remover, listar e, o pior, `semear`,
que é o passo do deploy que impede o `Dev Kobe` de perder a base curada no
primeiro turno depois da virada. `tests/test_kb_catalog.py` exercitava o módulo
`bot/kb_catalog.py` por dentro e passava verde; ninguém executava o comando.
Quem pegou foi a bateria comportamental da faxina (rodada 15), pelo handler real.

Duas camadas:
- **estática, sem banco** — nenhum helper de `bot/bin/` constrói `KobeDB()` sem
  argumento. Pega a classe do erro, não só esta ocorrência;
- **ponta a ponta, com banco de teste** — o comando roda num subprocesso, como o
  agente roda, e o catálogo muda de verdade.

    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test .venv/bin/python -m pytest -q tests/test_kobe_kb_cli.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
KOBE_KB = RAIZ / "bot" / "bin" / "kobe-kb"
_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


def test_nenhum_helper_constroi_kobedb_sem_conexao():
    achados = []
    for arq in sorted((RAIZ / "bot" / "bin").iterdir()):
        if not arq.is_file():
            continue
        try:
            texto = arq.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, linha in enumerate(texto.splitlines(), start=1):
            codigo = linha.split("#", 1)[0]
            if re.search(r"\bKobeDB\(\s*\)", codigo):
                achados.append(f"{arq.name}:{n}")
    assert not achados, (
        "helper abrindo o banco sem conexão (use `build_client(load_config())`): "
        + ", ".join(achados)
    )


@pytest.fixture()
def topico(tmp_path):
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    import psycopg

    chat = -(10**12) - (uuid.uuid4().int % 10**9)  # chat sintético, sem homônimo
    thread = 7
    with psycopg.connect(_URL, autocommit=True) as cx:
        tid = cx.execute(
            "INSERT INTO topics (telegram_chat_id, telegram_thread_id, current_name)"
            " VALUES (%s, %s, 'Teste KB CLI') RETURNING id",
            (chat, thread),
        ).fetchone()[0]
    pasta = tmp_path / "user-data" / "topics" / "teste-kb-cli" / "knowledge"
    pasta.mkdir(parents=True)
    (pasta / "a.md").write_text("um\n")
    (pasta / "b.md").write_text("dois\n")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(tmp_path)),
        "DATABASE_URL": _URL,
        "KOBE_HOME": str(tmp_path),
        "TELEGRAM_BOT_TOKEN": "000000:teste-sem-canal",
        "TELEGRAM_ALLOWED_USER_IDS": "1",
        "GROQ_API_KEY": "teste",
        "KOBE_CHAT_ID": str(chat),
        "KOBE_THREAD_ID": str(thread),
    }
    yield tid, env
    with psycopg.connect(_URL, autocommit=True) as cx:
        cx.execute("DELETE FROM kb_catalog WHERE topic_id = %s", (tid,))
        cx.execute("DELETE FROM topics WHERE id = %s", (tid,))


def _kb(env, *args):
    # cwd em diretório sem `.env`, para o comando usar só o ambiente dado.
    return subprocess.run([sys.executable, str(KOBE_KB), *args], env=env,
                          cwd=env["KOBE_HOME"], capture_output=True, text=True,
                          timeout=60)


def _catalogo(tid):
    import psycopg

    with psycopg.connect(_URL) as cx:
        return dict(cx.execute(
            "SELECT caminho, status FROM kb_catalog WHERE topic_id = %s", (tid,)
        ).fetchall())


def test_semear_admite_o_que_ja_esta_na_pasta(topico):
    tid, env = topico
    r = _kb(env, "semear", "--ensaio")
    assert r.returncode == 0, r.stderr
    assert _catalogo(tid) == {}, "ensaio não grava"
    r = _kb(env, "semear")
    assert r.returncode == 0, r.stderr
    assert _catalogo(tid) == {"a.md": "admitido", "b.md": "admitido"}
    r = _kb(env, "semear")
    assert r.returncode == 0 and "nada a semear" in r.stdout


def test_remover_e_readmitir_pelo_comando(topico):
    tid, env = topico
    assert _kb(env, "semear").returncode == 0
    r = _kb(env, "remover", "b.md")
    assert r.returncode == 0, r.stderr
    assert _catalogo(tid)["b.md"] == "removido"
    r = _kb(env, "admitir", "b.md", "--titulo", "dois")
    assert r.returncode == 0, r.stderr
    assert "readmitido" in r.stdout
    assert _catalogo(tid)["b.md"] == "admitido"
    r = _kb(env, "listar")
    assert r.returncode == 0 and "a.md" in r.stdout
