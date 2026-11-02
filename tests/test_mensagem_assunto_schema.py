#!/usr/bin/env python3
"""Migration 013 — a mensagem inteira em cada assunto (Highlander v3, F7).

O QUE ESTES TESTES GUARDAM
--------------------------
A promessa do cabeçalho da 013: ela é ADITIVA, não toca nas travas de origem da
008 nem no texto das mensagens, e o que o banco consegue garantir sobre a
marcação ele garante de fato — exatamente um assunto principal por mensagem
marcada, subassunto de um nível, sustentação literal coerente.

Mesmo desenho da 012: uma família sobre o ARQUIVO (roda sempre, sem banco) e uma
sobre o BANCO (pulada sem `KOBE_TEST_DATABASE_URL`, dentro de transação revertida
— nenhum comando destrutivo).

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=<banco com a 013> .venv/bin/python -m pytest -q \\
        tests/test_mensagem_assunto_schema.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

MIGRATION = RAIZ / "infra" / "migrations" / "013_mensagem_e_assunto.sql"
_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


def _sql_sem_comentarios() -> str:
    return "\n".join(l.split("--", 1)[0] for l in MIGRATION.read_text().splitlines())


# ── Sobre o arquivo ──────────────────────────────────────────────────────


def test_a_migration_existe_e_e_a_013():
    assert MIGRATION.exists()


def test_e_aditiva_nao_apaga_nada():
    sql = _sql_sem_comentarios().upper()
    for proibido in ("DROP TABLE", "DROP COLUMN", "TRUNCATE", "DELETE FROM",
                     "ALTER COLUMN", "RENAME"):
        assert proibido not in sql, f"a 013 promete ser aditiva e contém {proibido}"
    # E não reescreve linha nenhuma (UPDATE como comando; nos gatilhos a palavra
    # aparece só como o evento que eles escutam).
    assert not re.search(r"(^|;)\s*UPDATE\s+\w+\s+SET\b", sql, re.M)
    # Os únicos DROP permitidos são os pares idempotentes DROP/ADD de restrição e
    # de gatilho — a mesma técnica da 009, 011 e 012.
    drops = re.findall(r"DROP\s+(CONSTRAINT|TRIGGER)\s+IF\s+EXISTS\s+(\w+)", sql)
    assert sorted(n for _, n in drops) == sorted([
        "TRG_MESSAGE_SUBJECTS_TEM_PRINCIPAL", "LUCIEN_SUBJECTS_NAO_E_PAI_DE_SI",
        "TRG_LUCIEN_SUBJECTS_UM_NIVEL", "LUCIEN_CLAIMS_SUPPORT_CHECK",
        "LUCIEN_RUNS_MODE_CHECK"])


def test_nao_toca_nas_travas_de_origem_nem_no_texto():
    sql = _sql_sem_comentarios()
    assert "source_message_id" not in sql and "source_seq" not in sql
    assert "lucien_claims_vigencia_check" not in sql
    # O texto da mensagem é o bruto inviolável: a 013 não escreve em messages.
    assert not re.search(r"ALTER TABLE\s+messages\b", sql, re.I)


def test_colunas_novas_em_tabelas_existentes_sao_nulas_ou_com_default():
    sql = _sql_sem_comentarios()
    for m in re.finditer(r"ADD COLUMN IF NOT EXISTS (\w+) ([^;]+);", sql):
        nome, resto = m.group(1), m.group(2).upper()
        assert "NOT NULL" not in resto or "DEFAULT" in resto, \
            f"{nome}: coluna nova obrigatória sem default quebraria linhas antigas"


# ── Sobre o banco ────────────────────────────────────────────────────────


@pytest.fixture
def cx():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(_URL, row_factory=dict_row)
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('message_subjects') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            pytest.skip("este banco não tem a 013")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _tem_natureza(cur) -> bool:
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'lucien_subjects'"
                " AND column_name = 'kind'")
    return cur.fetchone() is not None


def _assunto(cur, slug: str, **extra):
    agora = datetime.now(timezone.utc)
    # Com a 014, só conversa fica dentro de tema.
    if extra.get("parent_id") and "kind" not in extra and _tem_natureza(cur):
        extra["kind"] = "conversa"
    cols = {"title": f"Assunto de teste {slug}", "slug": slug, "opened_at": agora,
            "last_touched_at": agora, **extra}
    cur.execute(f"INSERT INTO lucien_subjects ({', '.join(cols)}) VALUES"
                f" ({', '.join(['%s'] * len(cols))}) RETURNING id", list(cols.values()))
    return cur.fetchone()["id"]


def _mensagem(cur):
    cur.execute("INSERT INTO topics (current_name) VALUES ('teste f7 schema') RETURNING id")
    tid = cur.fetchone()["id"]
    cur.execute("INSERT INTO sessions (topic_id, status) VALUES (%s, 'active') RETURNING id", (tid,))
    sid = cur.fetchone()["id"]
    cur.execute("INSERT INTO messages (session_id, topic_id, role, content) VALUES"
                " (%s, %s, 'user', 'uma mensagem de teste com dois assuntos dentro') RETURNING id",
                (sid, tid))
    return cur.fetchone()["id"]


def _marca(cur, mid, subj, role, parts="[[0, 10]]"):
    cur.execute("INSERT INTO message_subjects (message_id, subject_id, role, parts, content_md5)"
                " VALUES (%s, %s, %s, %s::jsonb, 'x')", (mid, subj, role, parts))


def _imediato(cx):
    cx.cursor().execute("SET CONSTRAINTS trg_message_subjects_tem_principal IMMEDIATE")


def test_banco_um_principal_e_secundarios(cx):
    cur = cx.cursor()
    a, b = _assunto(cur, "t013-a"), _assunto(cur, "t013-b")
    m = _mensagem(cur)
    _marca(cur, m, a, "principal")
    _marca(cur, m, b, "secundario", "[[10, 20]]")
    _imediato(cx)   # confere agora o que só conferiria no commit
    cur.execute("SELECT count(*) AS n FROM message_subjects WHERE message_id = %s", (m,))
    assert cur.fetchone()["n"] == 2


def test_banco_recusa_dois_principais(cx):
    import psycopg

    cur = cx.cursor()
    a, b = _assunto(cur, "t013-c"), _assunto(cur, "t013-d")
    m = _mensagem(cur)
    _marca(cur, m, a, "principal")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _marca(cur, m, b, "principal")


def test_banco_recusa_mensagem_sem_principal(cx):
    import psycopg

    cur = cx.cursor()
    a = _assunto(cur, "t013-e")
    m = _mensagem(cur)
    _marca(cur, m, a, "secundario")
    with pytest.raises(psycopg.errors.CheckViolation):
        _imediato(cx)


def test_banco_recusa_partes_vazias_e_papel_desconhecido(cx):
    import psycopg

    cur = cx.cursor()
    a = _assunto(cur, "t013-f")
    m = _mensagem(cur)
    with pytest.raises(psycopg.errors.CheckViolation):
        _marca(cur, m, a, "principal", "[]")
    cx.rollback()
    cur = cx.cursor()
    a = _assunto(cur, "t013-f2")
    m = _mensagem(cur)
    with pytest.raises(psycopg.errors.CheckViolation):
        _marca(cur, m, a, "outro")


def test_banco_subassunto_de_um_nivel_so(cx):
    import psycopg

    cur = cx.cursor()
    pai = _assunto(cur, "t013-pai")
    filho = _assunto(cur, "t013-filho", parent_id=pai)
    assert filho
    with pytest.raises(psycopg.errors.CheckViolation):
        _assunto(cur, "t013-neto", parent_id=filho)


def test_banco_quem_tem_filho_nao_vira_filho(cx):
    import psycopg

    cur = cx.cursor()
    pai = _assunto(cur, "t013-p2")
    _assunto(cur, "t013-f3", parent_id=pai)
    outro = _assunto(cur, "t013-o")
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("UPDATE lucien_subjects SET parent_id = %s WHERE id = %s", (outro, pai))


def test_banco_sustentacao_coerente(cx):
    import psycopg

    cur = cx.cursor()
    cur.execute("SELECT id FROM lucien_claims LIMIT 1")
    r = cur.fetchone()
    if r is None:
        pytest.skip("banco sem afirmação")
    cur.execute("UPDATE lucien_claims SET support_from = 3, support_to = 9,"
                " support_quote = 'abc' WHERE id = %s", (r["id"],))
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute("UPDATE lucien_claims SET support_from = 3, support_to = NULL"
                    " WHERE id = %s", (r["id"],))


def test_banco_modo_marcacao_aceito(cx):
    cur = cx.cursor()
    cur.execute("INSERT INTO lucien_runs (mode) VALUES ('marcacao') RETURNING id")
    assert cur.fetchone()["id"]
