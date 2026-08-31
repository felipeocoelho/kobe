#!/usr/bin/env python3
"""Migration 008 — o registro de estado (Highlander v3, F3).

O QUE ESTES TESTES GUARDAM
--------------------------
A F3 é **a única fase em que um modelo escreve estado que o agente depois serve
como se fosse conhecido**. O briefing declara isso como o risco de verdade do
projeto, e a mitigação número um é *"origem obrigatória em toda linha"*.

Origem obrigatória, aqui, não é convenção nem item de revisão de código: é
`NOT NULL` com chave estrangeira. **O que estes testes provam é que o banco de
fato recusa** — porque uma garantia que ninguém exercita é uma esperança.

Duas famílias, com naturezas diferentes (mesmo desenho da 007):

1. **Sobre o ARQUIVO** — rodam sempre, sem banco nenhum, inclusive num clone
   limpo. Guardam as promessas do cabeçalho: que a migration é aditiva, que não
   encosta em `messages`, que a dimensão do vetor casa com a da 007.
2. **Sobre o BANCO** — pulados sem `KOBE_TEST_DATABASE_URL`, porque teste que
   exige banco num clone limpo vira "pulado", e pulado é verde por ausência.
   Cada um roda dentro de uma transação **revertida** no teardown; não há um
   único comando destrutivo aqui.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_state_registry_schema.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

MIGRATION = RAIZ / "infra" / "migrations" / "008_state_registry.sql"
_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")

TABELAS = (
    "lucien_runs",
    "lucien_claims",
    "lucien_claim_evidence",
    "lucien_events",
    "lucien_cursor",
)


@pytest.fixture
def cx():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    import psycopg

    conn = psycopg.connect(_URL)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def origem(cx):
    """Uma mensagem real do acervo, para servir de origem válida."""
    from psycopg.rows import dict_row

    cur = cx.cursor(row_factory=dict_row)
    cur.execute("SELECT id, seq, topic_id, created_at FROM messages ORDER BY seq DESC LIMIT 1")
    linha = cur.fetchone()
    if linha is None:
        pytest.skip("banco de teste sem mensagens — nada para servir de origem")
    return linha


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _sem_comentarios() -> str:
    """Só o SQL executável. O cabeçalho fala de DROP e de destruição em prosa;
    procurar a palavra no arquivo inteiro daria falso positivo."""
    return "\n".join(
        linha for linha in _sql().splitlines() if not linha.lstrip().startswith("--")
    )


def _inserir(cx, **campos):
    base = {
        "topic_id": None, "subject": "sonda", "subject_slug": "sonda",
        "statement": "uma afirmacao de sonda, longa o bastante", "kind": "decision",
        "status": "vigente", "valid_from": None, "valid_to": None,
        "superseded_by": None, "source_message_id": None, "source_seq": None,
    }
    base.update(campos)
    cols = ", ".join(base)
    marcas = ", ".join(["%s"] * len(base))
    cur = cx.cursor()
    cur.execute(
        f"INSERT INTO lucien_claims ({cols}) VALUES ({marcas}) RETURNING id",
        tuple(base.values()),
    )
    return cur.fetchone()[0]


# ── Sobre o arquivo: rodam sempre ─────────────────────────────────────────


def test_a_migration_existe_e_tem_o_numero_certo():
    assert MIGRATION.is_file()
    assert MIGRATION.name.startswith("008_")


def test_a_migration_e_estritamente_aditiva():
    """O cabeçalho promete que ela só cria. Promessa em comentário não segura
    nada — é isto que segura."""
    sql = _sem_comentarios().upper()
    for proibido in ("DROP TABLE", "DROP COLUMN", "DELETE FROM", "TRUNCATE", "ALTER COLUMN"):
        assert proibido not in sql, f"a 008 deixou de ser aditiva: achei {proibido}"


def test_a_migration_nao_encosta_em_messages():
    """`messages` é a tabela mais quente do Kobe e a 007 já mexeu nela. A 008 só
    a REFERENCIA (chave estrangeira) — se ela passar a alterá-la, isto acende."""
    sql = _sem_comentarios().upper()
    assert "ALTER TABLE MESSAGES" not in sql


@pytest.mark.parametrize("tabela", TABELAS)
def test_toda_tabela_nasce_idempotente(tabela: str):
    """A migration também é colada à mão em recuperação de incidente — foi assim
    que a produção foi remontada em junho —, e nesse caminho não há runner
    nenhum para impedir a segunda aplicação."""
    assert f"CREATE TABLE IF NOT EXISTS {tabela}" in _sql()


def test_o_vetor_tem_a_MESMA_dimensao_da_007():
    """Misturar dois espaços vetoriais no mesmo sistema não dá erro: dá resposta
    errada com nota plausível. A 007 fixou `text-embedding-3-small`, 1536d."""
    assert "VECTOR(1536)" in _sql()
    setecentos = (RAIZ / "infra" / "migrations" / "007_message_search.sql").read_text(
        encoding="utf-8"
    )
    assert "VECTOR(1536)" in setecentos


def test_a_origem_e_NOT_NULL_com_chave_estrangeira():
    """A mitigação número um da fase, no texto da migration."""
    sql = _sem_comentarios()
    assert "source_message_id UUID   NOT NULL REFERENCES messages(id)" in sql


def test_o_tsvector_e_coluna_gerada_e_usa_o_dicionario_portugues():
    sql = _sem_comentarios()
    assert "GENERATED ALWAYS AS" in sql and "to_tsvector('portuguese'" in sql


def test_nao_ha_indice_aproximado_de_vizinhanca():
    """Mesma decisão medida da 007: a exatidão é o que sustenta o piso do "não
    tenho registro", e um vizinho perdido pela busca aproximada viraria uma
    recusa falsa."""
    sql = _sem_comentarios().upper()
    assert "HNSW" not in sql and "IVFFLAT" not in sql


def test_os_indices_de_fila_e_de_vigencia_sao_parciais():
    """Sobre a tabela inteira, as duas perguntas quentes viram varredura
    crescente; sobre o índice parcial, o custo é proporcional ao que importa."""
    sql = _sem_comentarios()
    assert "WHERE status = 'vigente'" in sql
    assert "WHERE embedding IS NULL" in sql


# ── Sobre o banco: as travas de verdade ───────────────────────────────────


def test_afirmacao_SEM_origem_e_recusada(cx, origem):
    """A linha que dá nome à fase. Sem origem, não entra — e quem recusa é o
    banco, não a boa vontade de quem escreve o INSERT."""
    import psycopg

    with pytest.raises(psycopg.errors.NotNullViolation):
        _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                 source_message_id=None, source_seq=origem["seq"])


def test_afirmacao_com_origem_INVENTADA_e_recusada(cx, origem):
    """Uma origem plausível que não existe é o modo de falha mais provável de um
    modelo alucinando. A chave estrangeira o mata sem depender de revisão."""
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                 source_message_id="00000000-0000-0000-0000-000000000000",
                 source_seq=999999)


def test_vigente_e_encerrada_nao_podem_ser_a_mesma_linha(cx, origem):
    """Vigência e data de fim são a MESMA informação. Discordando, o registro
    não sabe responder "isto ainda vale?" — que é a única pergunta que ele
    existe para responder."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                 valid_to=origem["created_at"], status="vigente",
                 source_message_id=origem["id"], source_seq=origem["seq"])
    cx.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                 valid_to=None, status="superada",
                 source_message_id=origem["id"], source_seq=origem["seq"])


def test_uma_afirmacao_nao_substitui_a_si_mesma(cx, origem):
    """Um ciclo de um elemento seria um registro que se explica por si e não
    leva a lugar nenhum — o operador seguiria o ponteiro para sempre."""
    import psycopg

    novo = _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                    source_message_id=origem["id"], source_seq=origem["seq"])
    cur = cx.cursor()
    with pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "UPDATE lucien_claims SET status='superada', valid_to=NOW(),"
            " superseded_by=id WHERE id=%s",
            (novo,),
        )


@pytest.mark.parametrize(
    "campo,valor",
    [("kind", "chute"), ("status", "talvez"), ("confidence", "altissima"),
     ("created_by", "alguem")],
)
def test_os_enums_sao_fechados(cx, origem, campo: str, valor: str):
    """Um vocabulário aberto vira, em seis meses, seis grafias da mesma coisa —
    e aí "o que está vigente?" deixa de ter resposta única."""
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                 source_message_id=origem["id"], source_seq=origem["seq"],
                 **{campo: valor})


def test_a_afirmacao_some_junto_com_a_mensagem_de_origem(cx, origem):
    """`ON DELETE CASCADE`: se a fala que a sustenta for apagada, a afirmação
    perde a origem — e afirmação sem origem é justamente o que não pode existir
    aqui. Melhor sumir junto que virar órfã."""
    cur = cx.cursor()
    cur.execute(
        "SELECT confdeltype FROM pg_constraint"
        " WHERE conrelid='lucien_claims'::regclass AND contype='f'"
        "   AND conname LIKE '%source_message%'"
    )
    assert cur.fetchone()[0] == "c"  # 'c' = CASCADE


def test_o_caminho_feliz_funciona(cx, origem):
    """Sem isto, todos os testes acima passariam com uma tabela impossível de
    escrever — o oposto do que se quer."""
    novo = _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                    source_message_id=origem["id"], source_seq=origem["seq"])
    cur = cx.cursor()
    cur.execute(
        "SELECT status, valid_to, search_tsv IS NOT NULL, embedding IS NULL"
        "  FROM lucien_claims WHERE id=%s",
        (novo,),
    )
    status, valid_to, tem_tsv, sem_vetor = cur.fetchone()
    assert status == "vigente" and valid_to is None
    assert tem_tsv, "o tsvector é coluna gerada — tem que nascer preenchido"
    assert sem_vetor, "o vetor é preenchido ATRÁS, nunca no INSERT"


def test_evidencia_nao_aceita_mensagem_inexistente(cx, origem):
    """A tabela de evidência existe em vez de um array justamente por isto."""
    import psycopg

    novo = _inserir(cx, topic_id=origem["topic_id"], valid_from=origem["created_at"],
                    source_message_id=origem["id"], source_seq=origem["seq"])
    cur = cx.cursor()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        cur.execute(
            "INSERT INTO lucien_claim_evidence (claim_id, message_id, seq)"
            " VALUES (%s, %s, %s)",
            (novo, "00000000-0000-0000-0000-000000000000", 1),
        )
