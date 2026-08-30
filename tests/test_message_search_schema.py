#!/usr/bin/env python3
"""Migration 007 — o índice de busca sobre a conversa (Highlander v3, F2).

O QUE ESTES TESTES GUARDAM
--------------------------
A 007 é a única migration da F2, e ela toca a tabela mais quente do Kobe
(`messages`). Duas famílias de teste, com naturezas diferentes:

1. **Sobre o ARQUIVO** — rodam sempre, sem banco nenhum, inclusive num clone
   limpo. Guardam as promessas que o cabeçalho da migration faz: que ela é
   aditiva, que as colunas novas ficam no FIM, e que a dimensão do vetor é a
   que o operador decidiu.
2. **Sobre o BANCO** — pulados sem `KOBE_TEST_DATABASE_URL`, porque um teste
   que exige banco num clone limpo vira "pulado", e pulado é verde por
   ausência. Cada um roda dentro de uma transação revertida no teardown; não há
   um único comando destrutivo aqui.

POR QUE A IDEMPOTÊNCIA É TESTADA, E NÃO PROMETIDA
--------------------------------------------------
O runner recusa reaplicar uma migration já aplicada, então na prática ela roda
uma vez. Mas ela também é colada à mão em recuperação de incidente — foi assim
que a produção foi remontada em junho — e nesse caminho não há runner nenhum
para proteger. Aplicar duas vezes tem que ser inofensivo, e o `DO $$` que guarda
o backfill do `seq` é justamente o pedaço em que "rodar de novo" poderia
renumerar tudo e estourar o UNIQUE no meio.

COMO RODAR
----------
    createdb kobe_test   # uma vez
    .venv/bin/python infra/migrate.py up --database-url postgresql:///kobe_test
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test .venv/bin/python -m pytest -q \\
        tests/test_message_search_schema.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

MIGRATION = RAIZ / "infra" / "migrations" / "007_message_search.sql"
_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


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


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _sem_comentarios() -> str:
    """Só o SQL executável. O cabeçalho da 007 fala de DROP e de destruição em
    prosa; procurar a palavra no arquivo inteiro daria falso positivo."""
    return "\n".join(
        linha for linha in _sql().splitlines() if not linha.lstrip().startswith("--")
    )


# ── Sobre o arquivo: rodam sempre ─────────────────────────────────────────


def test_a_migration_existe_e_tem_o_numero_certo():
    assert MIGRATION.exists()
    assert MIGRATION.name.startswith("007_")


def test_a_migration_e_estritamente_aditiva():
    """O cabeçalho promete "não é destrutiva". Aqui a promessa é verificada.

    `DELETE` e `TRUNCATE` incluídos: a 007 preenche uma coluna nova e não tem
    negócio nenhum apagando linha de `messages`.
    """
    corpo = _sem_comentarios().upper()
    for proibido in ("DROP TABLE", "DROP COLUMN", "DROP INDEX", "TRUNCATE", "DELETE FROM"):
        assert proibido not in corpo, f"a 007 não pode conter {proibido}"


def test_as_colunas_novas_entram_no_fim_por_alter_e_nao_no_meio():
    """A ORDEM FÍSICA das colunas é comparada pelo portão de compatibilidade, e
    é a divergência que nenhum diff por nome enxerga. `ADD COLUMN` sempre põe no
    fim; qualquer outra forma faria uma instalação nova divergir de uma migrada.
    """
    corpo = _sem_comentarios()
    assert re.search(r"ALTER TABLE messages\s+ADD COLUMN IF NOT EXISTS seq", corpo)
    assert re.search(r"ALTER TABLE messages\s+ADD COLUMN IF NOT EXISTS search_tsv", corpo)


def test_o_vetor_tem_a_dimensao_que_o_operador_decidiu():
    """1536 = `text-embedding-3-small`. A dimensão não é detalhe: mudá-la depois
    obriga a reindexar tudo, e é por isso que ela foi decisão do operador."""
    assert "VECTOR(1536)" in _sql()
    assert "VECTOR(384)" not in _sql()


def test_o_tsvector_e_coluna_gerada_e_usa_o_dicionario_portugues():
    """Gerada é o ponto: nenhum caminho de código pode esquecer de atualizá-la."""
    corpo = _sem_comentarios()
    assert "GENERATED ALWAYS AS (to_tsvector('portuguese', content)) STORED" in corpo


def test_o_backfill_do_seq_tem_guarda():
    """Sem a guarda, rodar duas vezes renumeraria uma tabela parcialmente
    numerada e estouraria o UNIQUE no meio da migration."""
    corpo = _sem_comentarios()
    assert "IF NOT EXISTS (SELECT 1 FROM messages WHERE seq IS NOT NULL)" in corpo


def test_a_sequencia_comeca_em_um_na_tabela_vazia():
    """`is_called = false` faz o próximo nextval devolver EXATAMENTE o valor
    dado. Com `true`, uma instalação nova começaria em 2 e o número 1 nunca
    existiria — e é o número que o operador vê citado."""
    assert "+ 1, false)" in _sem_comentarios()


def test_o_indice_de_pendentes_e_parcial():
    """A pergunta do indexador é "o que ainda não tem vetor?". Sobre a tabela
    inteira isso vira varredura crescente."""
    assert "WHERE embedding IS NULL" in _sem_comentarios()


def test_nao_ha_indice_aproximado_de_vizinhanca():
    """Decisão medida: a varredura exata leva 67 ms no acervo de hoje e devolve
    o mesmo topo que o HNSW. Trocar exatidão por 64 ms transformaria um vizinho
    perdido pela busca aproximada numa recusa falsa de "não tenho registro"."""
    corpo = _sem_comentarios().lower()
    assert "hnsw" not in corpo
    assert "ivfflat" not in corpo


def test_o_schema_sql_continua_congelado_como_migration_000():
    """`infra/schema.sql` É a migration `000`, e migration aplicada é imutável —
    o runner recusa por drift de checksum. Estrutura nova vai SÓ em migration
    nova. (Escrevi a 007 espelhando em `schema.sql` na primeira tentativa; foi o
    runner que me corrigiu, e este teste existe pra corrigir o próximo.)"""
    schema = (RAIZ / "infra" / "schema.sql").read_text(encoding="utf-8")
    assert "message_chunks" not in schema
    assert "search_lexeme_df" not in schema
    assert "search_tsv" not in schema


# ── Sobre o banco: pulados sem KOBE_TEST_DATABASE_URL ─────────────────────


def test_aplicar_duas_vezes_e_inofensivo(cx):
    """A2/A9 do plano de testes. Dentro de uma transação, revertida no fim."""
    with cx.cursor() as cur:
        antes = cur.execute("select count(*), max(seq) from messages").fetchone()
        cur.execute(_sql())
        cur.execute(_sql())
        depois = cur.execute("select count(*), max(seq) from messages").fetchone()
    assert antes == depois
    cx.rollback()


def test_toda_mensagem_tem_seq_e_ele_e_unico(cx):
    with cx.cursor() as cur:
        total, com_seq, distintos = cur.execute(
            "select count(*), count(seq), count(distinct seq) from messages"
        ).fetchone()
    assert total == com_seq == distintos


def test_o_seq_segue_a_ordem_cronologica(cx):
    """É o que faz "mensagem #3059, de 13/07" ser conferível: quem tem número
    maior veio depois."""
    with cx.cursor() as cur:
        fora_de_ordem = cur.execute(
            "select count(*) from ("
            "  select seq, created_at,"
            "         lag(created_at) over (order by seq) as anterior"
            "    from messages) t"
            " where anterior is not null and created_at < anterior"
        ).fetchone()[0]
    assert fora_de_ordem == 0


def test_mensagem_nova_ganha_seq_sozinha(cx):
    """Nenhuma linha de código do bot foi alterada pra isso — é default de
    coluna. Se dependesse do código, um caminho de INSERT esquecido gravaria
    NULL e a citação perderia o número."""
    with cx.cursor() as cur:
        base = cur.execute(
            "select session_id, topic_id from messages order by seq desc limit 1"
        ).fetchone()
        if base is None:
            pytest.skip("banco de teste sem mensagens")
        maior = cur.execute("select max(seq) from messages").fetchone()[0]
        novo = cur.execute(
            "insert into messages (session_id, topic_id, role, content)"
            " values (%s, %s, 'system', 'teste de seq') returning seq",
            base,
        ).fetchone()[0]
    assert novo > maior
    cx.rollback()


def test_o_tsvector_se_mantem_sozinho_no_insert_e_no_update(cx):
    with cx.cursor() as cur:
        base = cur.execute(
            "select session_id, topic_id from messages order by seq desc limit 1"
        ).fetchone()
        if base is None:
            pytest.skip("banco de teste sem mensagens")
        mid = cur.execute(
            "insert into messages (session_id, topic_id, role, content)"
            " values (%s, %s, 'system', 'a produção rodava versões diferentes')"
            " returning id",
            base,
        ).fetchone()[0]
        tsv = cur.execute(
            "select search_tsv::text from messages where id = %s", (mid,)
        ).fetchone()[0]
        # O texto acentuado vai de propósito: o dicionário `portuguese` reduz
        # "produção" a `produçã`, e é essa forma que a busca por palavra vai
        # procurar. Um teste com texto sem acento provaria menos.
        assert "produçã" in tsv, tsv
        cur.execute(
            "update messages set content = 'salesforce' where id = %s", (mid,)
        )
        tsv2 = cur.execute(
            "select search_tsv::text from messages where id = %s", (mid,)
        ).fetchone()[0]
    assert "salesforc" in tsv2, tsv2
    cx.rollback()


def test_a_busca_literal_usa_o_indice_trigrama_e_nao_varredura(cx):
    """É a perna que acha `compat_gate` e `working_set.py`. Sem índice, ela
    varreria a tabela inteira a cada pergunta."""
    with cx.cursor() as cur:
        cur.execute("set enable_seqscan = off")
        plano = "\n".join(
            linha[0]
            for linha in cur.execute(
                "explain select id from messages where content ilike %s limit 5",
                ("%compat_gate%",),
            ).fetchall()
        )
    assert "idx_messages_content_trgm" in plano, plano


def test_message_chunks_apaga_junto_com_a_mensagem(cx):
    """`ON DELETE CASCADE`: trecho órfão viraria citação apontando pro nada."""
    with cx.cursor() as cur:
        base = cur.execute(
            "select session_id, topic_id from messages order by seq desc limit 1"
        ).fetchone()
        if base is None:
            pytest.skip("banco de teste sem mensagens")
        mid = cur.execute(
            "insert into messages (session_id, topic_id, role, content)"
            " values (%s, %s, 'system', 'x') returning id",
            base,
        ).fetchone()[0]
        cur.execute(
            "insert into message_chunks (message_id, idx, body) values (%s, 0, 'x')",
            (mid,),
        )
        cur.execute("delete from messages where id = %s", (mid,))
        sobrou = cur.execute(
            "select count(*) from message_chunks where message_id = %s", (mid,)
        ).fetchone()[0]
    assert sobrou == 0
    cx.rollback()


def test_um_trecho_nao_pode_repetir_indice_na_mesma_mensagem(cx):
    import psycopg

    with cx.cursor() as cur:
        base = cur.execute(
            "select session_id, topic_id from messages order by seq desc limit 1"
        ).fetchone()
        if base is None:
            pytest.skip("banco de teste sem mensagens")
        mid = cur.execute(
            "insert into messages (session_id, topic_id, role, content)"
            " values (%s, %s, 'system', 'x') returning id",
            base,
        ).fetchone()[0]
        cur.execute(
            "insert into message_chunks (message_id, idx, body) values (%s, 0, 'a')",
            (mid,),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "insert into message_chunks (message_id, idx, body) values (%s, 0, 'b')",
                (mid,),
            )
    cx.rollback()
