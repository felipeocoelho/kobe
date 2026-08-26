#!/usr/bin/env python3
"""Runner de migrations versionado (Sessão #3, ponte pro Postgres).

O que estes testes protegem, em ordem de gravidade:

1. **Ordem determinística** — a armadilha aqui é sutil e clássica: com 10+
   migrations, a ordem alfabética do filesystem põe `010` ANTES de `002`. O
   teste planta exatamente esse caso e exige a ordem numérica.
2. **Recusa de aplicar fora de ordem** — dois branches criam `006` e `007`, o
   `007` entra primeiro, e o `006` chega atrasado. Aplicá-lo agora produz um
   banco que nenhuma outra instalação tem.
3. **Detecção de drift** — alguém edita uma migration já aplicada. O banco tem
   o SQL antigo, o repo tem o novo, e ninguém mais sabe qual é a verdade.
4. **Falha alta em vez de silêncio** — arquivo `.sql` sem prefixo numérico e
   versão duplicada são ERRO. "Ignorado em silêncio" é como uma migration
   deixa de ser aplicada sem ninguém notar.

Os testes de lógica não tocam banco nenhum — plantam árvores sintéticas em
`tmp_path`. O teste ao vivo (no fim) roda contra `KOBE_TEST_DATABASE_URL` e é
pulado quando ela não existe, para que um clone limpo siga verde.

Rodar: .venv/bin/python -m pytest tests/test_migrate.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from infra import migrate
from infra.migrate import Migration, MigrationError


# ── Árvore sintética ──────────────────────────────────────────────────────


@pytest.fixture
def arvore(tmp_path, monkeypatch):
    """Substitui schema.sql + infra/migrations/ por uma árvore de mentira.

    Devolve uma função `plantar(nome, conteudo)`. O schema base (versão 000)
    já vem plantado, porque ele é o alicerce e sempre existe.
    """
    schema = tmp_path / "schema.sql"
    schema.write_text("-- schema base\nSELECT 1;\n", encoding="utf-8")
    migs = tmp_path / "migrations"
    migs.mkdir()

    monkeypatch.setattr(migrate, "SCHEMA_FILE", schema)
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", migs)

    def plantar(nome: str, conteudo: str = "SELECT 1;\n") -> Path:
        caminho = migs / nome
        caminho.write_text(conteudo, encoding="utf-8")
        return caminho

    plantar.schema = schema  # type: ignore[attr-defined]
    return plantar


# ── 1. Ordem determinística ───────────────────────────────────────────────


def test_schema_base_e_sempre_a_versao_000(arvore):
    migs = migrate.discover()
    assert migs[0].version == "000"
    assert migs[0].filename == "schema.sql"


def test_ordem_e_numerica_nao_alfabetica(arvore):
    """A armadilha do dia em que o projeto passar de 9 migrations."""
    for nome in ("010_dez.sql", "002_dois.sql", "001_um.sql", "100_cem.sql"):
        arvore(nome)

    versoes = [m.version for m in migrate.discover()]

    assert versoes == ["000", "001", "002", "010", "100"]
    # A prova de que é numérica: alfabeticamente, "010" viria antes de "002".
    assert versoes.index("002") < versoes.index("010")


def test_zeros_a_esquerda_sao_preservados_na_versao(arvore):
    """`001` gravado como `"1"` no controle não casaria com o arquivo depois."""
    arvore("001_um.sql")
    assert [m.version for m in migrate.discover()] == ["000", "001"]


# ── 2. Falha alta em vez de silêncio ──────────────────────────────────────


def test_sql_sem_prefixo_numerico_e_erro(arvore):
    arvore("adiciona_coluna.sql")
    with pytest.raises(MigrationError, match="prefixo numerico"):
        migrate.discover()


def test_versao_duplicada_e_erro(arvore):
    arvore("006_um_caminho.sql")
    arvore("006_outro_caminho.sql")
    with pytest.raises(MigrationError, match="duplicada"):
        migrate.discover()


def test_arquivo_nao_sql_e_ignorado(arvore):
    """README ou .bak na pasta não pode derrubar o runner."""
    arvore("001_um.sql")
    (arvore.schema.parent / "migrations" / "README.md").write_text("nota\n")
    assert [m.version for m in migrate.discover()] == ["000", "001"]


def test_schema_base_ausente_e_erro(arvore, monkeypatch):
    monkeypatch.setattr(migrate, "SCHEMA_FILE", Path("/nao/existe/schema.sql"))
    with pytest.raises(MigrationError, match="schema base ausente"):
        migrate.discover()


# ── 3. Plano: pendentes, ordem e drift ────────────────────────────────────


def _checksums(migs: list[Migration], versoes: list[str]) -> dict[str, str]:
    return {m.version: m.checksum() for m in migs if m.version in versoes}


def test_banco_vazio_aplica_tudo(arvore):
    arvore("001_um.sql")
    arvore("002_dois.sql")
    migs = migrate.discover()

    pendentes = migrate.plan(migs, applied={})

    assert [m.version for m in pendentes] == ["000", "001", "002"]


def test_idempotencia_nada_pendente_quando_tudo_aplicado(arvore):
    arvore("001_um.sql")
    migs = migrate.discover()
    aplicadas = _checksums(migs, ["000", "001"])

    assert migrate.plan(migs, aplicadas) == []


def test_so_as_pendentes_entram_no_plano(arvore):
    arvore("001_um.sql")
    arvore("002_dois.sql")
    migs = migrate.discover()
    aplicadas = _checksums(migs, ["000", "001"])

    assert [m.version for m in migrate.plan(migs, aplicadas)] == ["002"]


def test_migration_atrasada_e_recusada(arvore):
    """O `006` que chega depois do `007` já ter entrado."""
    arvore("006_de_um_branch.sql")
    arvore("007_de_outro_branch.sql")
    migs = migrate.discover()
    aplicadas = _checksums(migs, ["000", "007"])  # o 006 ficou pra trás

    with pytest.raises(MigrationError, match="fora de ordem"):
        migrate.plan(migs, aplicadas)


def test_mensagem_de_fora_de_ordem_nomeia_a_atrasada(arvore):
    arvore("006_de_um_branch.sql")
    arvore("007_de_outro_branch.sql")
    migs = migrate.discover()
    aplicadas = _checksums(migs, ["000", "007"])

    with pytest.raises(MigrationError) as exc:
        migrate.plan(migs, aplicadas)

    assert "006_de_um_branch.sql" in str(exc.value)


def test_drift_em_migration_aplicada_e_recusado(arvore):
    caminho = arvore("001_um.sql", "SELECT 1;\n")
    migs = migrate.discover()
    aplicadas = _checksums(migs, ["000", "001"])

    caminho.write_text("SELECT 2;  -- alguem editou depois de aplicada\n")

    with pytest.raises(MigrationError, match="drift"):
        migrate.plan(migrate.discover(), aplicadas)


def test_drift_e_checado_antes_da_ordem(arvore):
    """Com os dois problemas juntos, o drift é o que se reporta — ele é o
    perigoso (o banco mente sobre o que rodou); a ordem é só um bloqueio."""
    caminho = arvore("001_um.sql")
    arvore("006_atrasada.sql")
    arvore("007_ja_aplicada.sql")
    migs = migrate.discover()
    aplicadas = _checksums(migs, ["000", "001", "007"])

    caminho.write_text("SELECT 99;\n")

    with pytest.raises(MigrationError, match="drift"):
        migrate.plan(migrate.discover(), aplicadas)


def test_checksum_muda_com_o_conteudo(arvore):
    caminho = arvore("001_um.sql", "SELECT 1;\n")
    antes = migrate.discover()[1].checksum()
    caminho.write_text("SELECT 1; -- comentario novo\n")
    assert migrate.discover()[1].checksum() != antes


# ── 4. Resolução do alvo ──────────────────────────────────────────────────


def test_alvo_ausente_e_erro(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(MigrationError, match="alvo ausente"):
        migrate.resolve_url(None)


def test_alvo_explicito_vence_a_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql:///da_env")
    assert migrate.resolve_url("postgresql:///explicito") == "postgresql:///explicito"


def test_alvo_cai_na_env_quando_nao_ha_explicito(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql:///da_env")
    assert migrate.resolve_url(None) == "postgresql:///da_env"


def test_alvo_em_branco_conta_como_ausente(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "   ")
    with pytest.raises(MigrationError, match="alvo ausente"):
        migrate.resolve_url(None)


# ── 4-bis. Baseline: registrar sem executar ───────────────────────────────


def test_baseline_exige_through_explicito(monkeypatch):
    """Sem `--through`, o default natural seria "marca tudo" — e "tudo" inclui
    as destrutivas, que passariam a NUNCA rodar naquele banco, em silêncio."""
    monkeypatch.setenv("DATABASE_URL", "postgresql:///nao_usado")

    assert migrate.main(["baseline"]) == 1


def test_baseline_recusa_versao_inexistente(arvore, monkeypatch):
    arvore("001_um.sql")

    def _sem_banco(_url):
        raise AssertionError("não devia chegar a conectar")

    monkeypatch.setattr(migrate, "_connect", _sem_banco)

    with pytest.raises(MigrationError, match="nao existe"):
        migrate.cmd_baseline("postgresql:///x", "099", dry_run=True)


# ── 5. As migrations REAIS do repositório ─────────────────────────────────


def test_migrations_reais_do_repo_sao_descobertas_em_ordem():
    """Sem monkeypatch: a árvore de verdade tem que passar pelas regras."""
    migs = migrate.discover()
    versoes = [m.version for m in migs]

    assert versoes[0] == "000"
    assert versoes == sorted(versoes, key=int)
    assert len(set(versoes)) == len(versoes)


# ── 6. Ao vivo (pulado sem banco) ─────────────────────────────────────────

_TEST_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")

pytestmark_live = pytest.mark.skipif(
    not _TEST_URL, reason="KOBE_TEST_DATABASE_URL não definida — sem banco de teste"
)


@pytestmark_live
def test_ao_vivo_up_e_idempotente_e_registra_o_controle():
    """Aplica (ou confirma aplicado) e assevera o estado do controle.

    Repetível de propósito: rodar duas vezes tem que dar o mesmo resultado,
    que é a garantia nº 2 do runner.
    """
    assert migrate.cmd_up(_TEST_URL, dry_run=False) == 0
    assert migrate.cmd_up(_TEST_URL, dry_run=False) == 0  # segunda vez: no-op

    conn = migrate._connect(_TEST_URL)
    try:
        aplicadas = migrate.applied_map(conn)
    finally:
        conn.close()

    esperadas = {m.version for m in migrate.discover()}
    assert set(aplicadas) == esperadas


@pytestmark_live
def test_ao_vivo_o_schema_resultante_e_o_pos_aposentadoria():
    """As tabelas do Chat Manager não podem nascer num banco novo."""
    migrate.cmd_up(_TEST_URL, dry_run=False)

    conn = migrate._connect(_TEST_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
            tabelas = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    assert {"topics", "sessions", "messages", "saved_artifacts",
            "contacts", "topic_name_history"} <= tabelas
    assert "conversations" not in tabelas
    assert "conversation_tags" not in tabelas


@pytestmark_live
def test_ao_vivo_baseline_recusa_banco_que_ja_tem_historico():
    """Carimbar por cima de um histórico existente esconderia exatamente a
    divergência que o runner existe pra mostrar."""
    migrate.cmd_up(_TEST_URL, dry_run=False)  # garante histórico

    with pytest.raises(MigrationError, match="ja tem"):
        migrate.cmd_baseline(_TEST_URL, "004", dry_run=True)
