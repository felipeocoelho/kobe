#!/usr/bin/env python3
"""Migration 012 — o assunto como unidade (Highlander v3, F7.1).

O QUE ESTES TESTES GUARDAM
--------------------------
A F7 é a única frente do Highlander v3 que mexe em layout de banco numa tabela
que o agente serve como verdade. A promessa do cabeçalho da 012 é dupla: (1) ela
é ADITIVA e **não toca nas travas de origem da 008**; (2) o pouco que o banco
consegue garantir sobre assunto — um slug por assunto, fusão que aponta para
quem absorveu — ele garante de fato.

Mesmo desenho da 007/008: uma família sobre o ARQUIVO (roda sempre, sem banco) e
uma sobre o BANCO (pulada sem `KOBE_TEST_DATABASE_URL`, dentro de transação
revertida — nenhum comando destrutivo).

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_assuntos_schema.py
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

MIGRATION = RAIZ / "infra" / "migrations" / "012_assuntos.sql"
_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


def _sql_sem_comentarios() -> str:
    return "\n".join(
        linha.split("--", 1)[0] for linha in MIGRATION.read_text().splitlines()
    )


# ── Sobre o arquivo ──────────────────────────────────────────────────────


def test_a_migration_existe_e_e_a_012():
    assert MIGRATION.exists()


def test_e_aditiva_nao_apaga_nada():
    sql = _sql_sem_comentarios().upper()
    for proibido in ("DROP TABLE", "DROP COLUMN", "TRUNCATE", "DELETE FROM",
                     "ALTER COLUMN", "RENAME"):
        assert proibido not in sql, f"a 012 promete ser aditiva e contém {proibido}"
    # O único DROP permitido é o par DROP/ADD CONSTRAINT que ALARGA o modo da
    # rodada — a mesma técnica idempotente da 009 e da 011.
    drops = re.findall(r"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+(\w+)", sql)
    assert drops == ["LUCIEN_RUNS_MODE_CHECK"]


def test_nao_toca_nas_travas_de_origem_da_008():
    """A trava que sustenta o registro de estado é `source_message_id NOT NULL`.
    A revisão do operador de 13/10/2026 foi literal: ela NÃO afrouxa."""
    sql = _sql_sem_comentarios()
    assert "source_message_id" not in sql
    assert "source_seq" not in sql
    assert "lucien_claims_vigencia_check" not in sql


def test_a_coluna_nova_em_lucien_claims_e_nula():
    sql = _sql_sem_comentarios()
    m = re.search(r"ADD COLUMN IF NOT EXISTS subject_id ([^;]+);", sql)
    assert m, "a coluna subject_id tem que ser ADD COLUMN IF NOT EXISTS"
    assert "NOT NULL" not in m.group(1).upper()


def test_o_vetor_tem_a_mesma_dimensao_da_008():
    assert "VECTOR(1536)" in _sql_sem_comentarios()


def test_sem_indice_aproximado():
    """Mesma razão da 008: um vizinho perdido vira um 'não tenho registro' falso."""
    sql = _sql_sem_comentarios().lower()
    assert "hnsw" not in sql and "ivfflat" not in sql


# ── Sobre o banco ────────────────────────────────────────────────────────


@pytest.fixture
def cx():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(_URL, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _assunto(cur, titulo: str, slug: str, **extra):
    agora = datetime.now(timezone.utc)
    cols = {"title": titulo, "slug": slug, "opened_at": agora,
            "last_touched_at": agora, **extra}
    nomes = ", ".join(cols)
    marcas = ", ".join(["%s"] * len(cols))
    cur.execute(f"INSERT INTO lucien_subjects ({nomes}) VALUES ({marcas}) RETURNING id",
                list(cols.values()))
    return cur.fetchone()["id"]


def test_banco_slug_e_unico(cx):
    import psycopg

    cur = cx.cursor()
    _assunto(cur, "Teste F7 slug", "teste-f7-slug-unico")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _assunto(cur, "Teste F7 slug outro", "teste-f7-slug-unico")


def test_banco_fundido_exige_destino(cx):
    import psycopg

    cur = cx.cursor()
    with pytest.raises(psycopg.errors.CheckViolation):
        _assunto(cur, "Fundido sem destino", "teste-f7-fundido", status="fundido")


def test_banco_ativo_nao_pode_ter_destino(cx):
    import psycopg

    cur = cx.cursor()
    alvo = _assunto(cur, "Destino", "teste-f7-destino")
    with pytest.raises(psycopg.errors.CheckViolation):
        _assunto(cur, "Ativo com destino", "teste-f7-ativo-destino", merged_into=alvo)


def test_banco_subsistema_tem_que_ser_do_sistema(cx):
    """A guarda do PAR, herdada da 006: Flow (sistema) + Coder (subsistema do
    Kobe) é um par impossível."""
    import psycopg

    cur = cx.cursor()
    cur.execute("SELECT id FROM work_systems WHERE slug = 'flow'")
    flow = cur.fetchone()
    cur.execute(
        "SELECT ss.id FROM work_subsystems ss JOIN work_systems s ON s.id = ss.system_id"
        " WHERE s.slug = 'kobe' AND ss.slug = 'coder'")
    coder = cur.fetchone()
    if not flow or not coder:
        pytest.skip("sementes da 006 ausentes neste banco")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _assunto(cur, "Par impossível", "teste-f7-par", system_id=flow["id"],
                 subsystem_id=coder["id"])


def test_banco_afirmacao_continua_exigindo_origem(cx):
    """A 012 não mexe na 008: afirmação sem mensagem de origem continua recusada
    pelo banco, com ou sem assunto."""
    import psycopg

    cur = cx.cursor()
    cur.execute("SELECT id FROM topics LIMIT 1")
    t = cur.fetchone()
    if not t:
        pytest.skip("banco sem tópico")
    sid = _assunto(cur, "Origem obrigatória", "teste-f7-origem")
    with pytest.raises(psycopg.errors.NotNullViolation):
        cur.execute(
            "INSERT INTO lucien_claims (topic_id, subject, subject_slug, statement,"
            " kind, valid_from, source_seq, subject_id)"
            " VALUES (%s,'x','x','uma afirmação qualquer de teste','fact',NOW(),1,%s)",
            (t["id"], sid))


def test_banco_rodada_aceita_modo_assuntos(cx):
    cur = cx.cursor()
    cur.execute("INSERT INTO lucien_runs (mode) VALUES ('assuntos') RETURNING"
                " subjects_created, subjects_reused, subjects_avoided")
    assert cur.fetchone() == {"subjects_created": 0, "subjects_reused": 0,
                              "subjects_avoided": 0}
