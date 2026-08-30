#!/usr/bin/env python3
"""T4 — o portão de compatibilidade de ambiente (Sessão #3).

O QUE ESTES TESTES SÃO
----------------------
Três divergências de ambiente entre dev e produção atravessaram 100% de uma
suíte de 456 testes sem acender nada. Este arquivo é a resposta: para cada uma
delas há um teste que **injeta a armadilha de propósito** e exige o portão
vermelho. Se o portão parar de pegar uma delas, aqui é que fica vermelho.

As três originais são `C1` (collation), `C2` (ordem física das colunas) e `C3`
(`data_checksums`). A quarta, `TimeZone`, foi encontrada pelo próprio portão na
primeira execução — o cluster do Ubuntu fica no fuso local da máquina e todo
banco criado nele nasce herdando esse fuso, enquanto a produção está em UTC.

POR QUE OS TESTES DE LÓGICA USAM FINGERPRINT SINTÉTICO
------------------------------------------------------
Um teste que dependesse de um banco de verdade seria pulado num clone limpo, e
"pulado" é verde por ausência — que é exatamente o modo de falhar que o portão
existe pra impedir. A lógica do portão é testada sempre, sem banco nenhum. O
teste ao vivo (no fim) é o valor operacional, e ele sim é pulado sem banco.

Rodar: .venv/bin/python -m pytest tests/test_compat_gate.py -q
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from infra.compat_gate import DEFAULT_REFERENCE, compare, load_reference, scan_pgvector


# ── Fingerprint sintético mínimo ──────────────────────────────────────────


def _base() -> dict:
    """Um banco de mentira, saudável, com o suficiente de cada classe."""
    return {
        "fingerprint_version": 1,
        "database": {
            "encoding": "UTF8",
            "collate": "en_US.UTF-8",
            "ctype": "en_US.UTF-8",
            "data_checksums": "on",
            "timezone": "UTC",
            "server_version": "16.15 (Ubuntu)",
            "server_version_major": 16,
        },
        "extensions": {"uuid-ossp": "1.1", "vector": "0.6.0"},
        "tables": {
            "topics": {
                "columns": [
                    {
                        "position": 1, "attnum": 1, "name": "id", "type": "uuid",
                        "nullable": False, "default": "uuid_generate_v4()",
                        "identity": None, "collation": None,
                    },
                    {
                        "position": 2, "attnum": 2, "name": "current_name", "type": "text",
                        "nullable": True, "default": None,
                        "identity": None, "collation": None,
                    },
                    {
                        "position": 3, "attnum": 3, "name": "telegram_chat_id",
                        "type": "bigint", "nullable": True, "default": None,
                        "identity": None, "collation": None,
                    },
                ],
                "indexes": [
                    {"name": "topics_pkey", "definition": "CREATE UNIQUE INDEX topics_pkey ON topics USING btree (id)"}
                ],
                "constraints": [
                    {"name": "topics_pkey", "definition": "PRIMARY KEY (id)"}
                ],
            }
        },
    }


def _classes(achados) -> set[str]:
    return {f.classe for f in achados}


def _texto(achados) -> str:
    return "\n".join(str(f) for f in achados)


# ── O caso verde ──────────────────────────────────────────────────────────


def test_bancos_identicos_nao_geram_achado():
    assert compare(_base(), _base(), scan_repo=False) == []


# ── C1 — collation. A ordem de ORDER BY em texto muda; o dado, não. ───────


def test_c1_collation_divergente_acende_o_portao():
    """`initdb` do Ubuntu cria em C.UTF-8 (ordena por byte cru); prod é en_US."""
    alvo = _base()
    alvo["database"]["collate"] = "C.UTF-8"

    achados = compare(_base(), alvo, scan_repo=False)

    assert "ambiente" in _classes(achados)
    assert "collation" in _texto(achados)
    assert "C.UTF-8" in _texto(achados)


def test_c1_ctype_divergente_acende_o_portao():
    alvo = _base()
    alvo["database"]["ctype"] = "C.UTF-8"
    assert "ambiente" in _classes(compare(_base(), alvo, scan_repo=False))


# ── C2 — ordem FÍSICA das colunas. A que nenhum diff por nome enxerga. ────


def test_c2_ordem_fisica_divergente_acende_o_portao():
    """Mesmo nome, mesmo tipo, mesma nulabilidade — só a POSIÇÃO muda.

    É o caso real: duas colunas entraram por migration na produção (e foram
    parar no fim) e estão no meio no `infra/schema.sql`. Foi o que derrubou a
    primeira tentativa de COPY.
    """
    alvo = _base()
    cols = alvo["tables"]["topics"]["columns"]
    cols[1], cols[2] = cols[2], cols[1]  # troca current_name <-> telegram_chat_id
    for i, c in enumerate(cols, start=1):
        c["position"] = i

    achados = compare(_base(), alvo, scan_repo=False)

    assert "ordem-de-coluna" in _classes(achados)
    assert "ordem FISICA" in _texto(achados)


def test_c2_e_invisivel_para_um_diff_por_nome_tipo_e_nulo():
    """A prova de que o teste acima não é redundante: por nome/tipo/nulo, os
    dois bancos são idênticos. Só a posição os separa."""
    ref, alvo = _base(), _base()
    cols = alvo["tables"]["topics"]["columns"]
    cols[1], cols[2] = cols[2], cols[1]

    por_nome = {c["name"]: (c["type"], c["nullable"]) for c in ref["tables"]["topics"]["columns"]}
    por_nome_alvo = {c["name"]: (c["type"], c["nullable"]) for c in cols}
    assert por_nome == por_nome_alvo  # um diff ingênuo diria "tudo igual"

    assert "ordem-de-coluna" in _classes(compare(ref, alvo, scan_repo=False))


def test_c2_mensagem_mostra_as_duas_ordens():
    """Sem as duas listas na mensagem, quem lê não sabe o que arrumar."""
    alvo = _base()
    cols = alvo["tables"]["topics"]["columns"]
    cols[1], cols[2] = cols[2], cols[1]

    msg = _texto(compare(_base(), alvo, scan_repo=False))

    assert "esperada:" in msg and "encontrada:" in msg
    assert "silencio" in msg.lower()  # a mensagem avisa que quebra calado


# ── C3 — data_checksums. Ligar depois exige parar o cluster. ──────────────


def test_c3_data_checksums_divergente_acende_o_portao():
    alvo = _base()
    alvo["database"]["data_checksums"] = "off"

    achados = compare(_base(), alvo, scan_repo=False)

    assert "ambiente" in _classes(achados)
    assert "data_checksums" in _texto(achados)


# ── A quarta, achada pelo próprio portão ──────────────────────────────────


def test_timezone_divergente_acende_o_portao():
    """O banco novo herda o fuso do cluster; a produção está em UTC.

    O valor guardado é o mesmo (timestamptz é absoluto), mas o TEXTO que o
    driver devolve muda de `+00:00` pro deslocamento local — e o Kobe compara
    `created_at` como string em pelo menos um caminho.
    """
    alvo = _base()
    alvo["database"]["timezone"] = "America/Sao_Paulo"

    achados = compare(_base(), alvo, scan_repo=False)

    assert "ambiente" in _classes(achados)
    assert "TimeZone" in _texto(achados)


def test_encoding_divergente_acende_o_portao():
    alvo = _base()
    alvo["database"]["encoding"] = "LATIN1"
    assert "ambiente" in _classes(compare(_base(), alvo, scan_repo=False))


def test_versao_maior_do_servidor_divergente_acende_o_portao():
    alvo = _base()
    alvo["database"]["server_version_major"] = 17
    assert "ambiente" in _classes(compare(_base(), alvo, scan_repo=False))


def test_versao_menor_do_servidor_nao_acende():
    """16.15 -> 16.16 é atualização de segurança, não classe de
    incompatibilidade. Alarmar aqui treinaria todo mundo a ignorar o portão."""
    alvo = _base()
    alvo["database"]["server_version"] = "16.16 (Ubuntu)"
    assert compare(_base(), alvo, scan_repo=False) == []


# ── Migrations: o banco está em dia com a referência? (2026-08-30) ────────
#
# Antes desta classe, um banco atrasado numa migration não se anunciava: ele
# aparecia como "tabela faltando", e o operador tinha que INFERIR a causa. Foi
# assim que a defasagem da `006` ficou quatro dias na tela dos dois ambientes
# disfarçada de outra coisa.


def _com_migrations(versoes):
    fp = _base()
    fp["migrations"] = list(versoes)
    return fp


def test_migrations_iguais_nao_acendem():
    ref = _com_migrations(["000", "001", "002"])
    assert compare(ref, _com_migrations(["000", "001", "002"]), scan_repo=False) == []


def test_banco_atrasado_numa_migration_acende_o_portao():
    ref = _com_migrations(["000", "001", "002"])
    alvo = _com_migrations(["000", "001"])
    achados = compare(ref, alvo, scan_repo=False)
    assert "migration" in _classes(achados)
    assert "ATRASADO" in _texto(achados)
    assert "002" in _texto(achados)
    assert "migrate.py up" in _texto(achados)


def test_banco_adiantado_aponta_a_referencia_velha():
    """É EXATAMENTE o caso da `006`: o banco andou, a referência não. A
    mensagem tem que mandar regenerar a referência, não 'consertar' o banco."""
    ref = _com_migrations(["000", "001"])
    alvo = _com_migrations(["000", "001", "002"])
    achados = compare(ref, alvo, scan_repo=False)
    assert "migration" in _classes(achados)
    assert "referencia esta VELHA" in _texto(achados)


def test_a_causa_vem_antes_do_sintoma():
    """'Falta a migration 002' é a CAUSA de 'falta a tabela X'. Quem lê o
    portão tem que bater o olho na causa primeiro — senão vai consertar o
    sintoma à mão e deixar o banco fora de versão."""
    ref = _com_migrations(["000", "001", "002"])
    ref["tables"]["work_sessions"] = {"columns": [], "indexes": [], "constraints": []}
    alvo = _com_migrations(["000", "001"])
    achados = compare(ref, alvo, scan_repo=False)
    classes = [f.classe for f in achados]
    assert classes.index("migration") < classes.index("tabela")


def test_banco_sem_tabela_de_controle_nao_acende_migration():
    """`None` é "não dá pra julgar", não "está zerado". Silêncio por ignorância
    declarada — um banco nunca tocado pelo runner não é um banco atrasado."""
    ref = _com_migrations(["000", "001"])
    alvo = _base()  # sem a chave: fingerprint de banco sem `schema_migrations`
    achados = compare(ref, alvo, scan_repo=False)
    assert "migration" not in _classes(achados)


# ── Extensões ─────────────────────────────────────────────────────────────


def test_extensao_ausente_acende_o_portao():
    alvo = _base()
    del alvo["extensions"]["vector"]
    achados = compare(_base(), alvo, scan_repo=False)
    assert "extensao" in _classes(achados)
    assert "ausente" in _texto(achados)


def test_extensao_sobrando_acende_o_portao():
    alvo = _base()
    alvo["extensions"]["postgis"] = "3.4"
    assert "extensao" in _classes(compare(_base(), alvo, scan_repo=False))


def test_versao_de_extensao_divergente_acende_o_portao():
    alvo = _base()
    alvo["extensions"]["vector"] = "0.8.0"
    achados = compare(_base(), alvo, scan_repo=False)
    assert "extensao" in _classes(achados)
    assert "0.8.0" in _texto(achados)


def test_divergencia_de_vector_traz_mensagem_propria():
    """`vector` não é uma extensão qualquer: a concordância entre versões foi
    conferida à mão, uma vez. Trocar de versão pede refazer a conferência."""
    alvo = _base()
    alvo["extensions"]["vector"] = "0.8.0"
    assert "conferencia" in _texto(compare(_base(), alvo, scan_repo=False))


# ── Tabelas e colunas ─────────────────────────────────────────────────────


def test_tabela_ausente_acende_o_portao():
    alvo = _base()
    del alvo["tables"]["topics"]
    achados = compare(_base(), alvo, scan_repo=False)
    assert "tabela" in _classes(achados)
    assert "ausente" in _texto(achados)


def test_tabela_sobrando_aponta_para_o_runner():
    """Tabela a mais quase sempre é banco atrasado numa migration — a mensagem
    tem que dizer onde olhar, não só que divergiu."""
    alvo = _base()
    alvo["tables"]["conversations"] = {"columns": [], "indexes": [], "constraints": []}
    achados = compare(_base(), alvo, scan_repo=False)
    assert "tabela" in _classes(achados)
    assert "migrate.py status" in _texto(achados)


def test_coluna_ausente_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["columns"] = alvo["tables"]["topics"]["columns"][:2]
    achados = compare(_base(), alvo, scan_repo=False)
    assert "coluna" in _classes(achados)
    assert "telegram_chat_id" in _texto(achados)


def test_coluna_sobrando_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["columns"].append(
        {"position": 4, "attnum": 4, "name": "embedding", "type": "vector(1536)",
         "nullable": True, "default": None, "identity": None, "collation": None}
    )
    achados = compare(_base(), alvo, scan_repo=False)
    assert "coluna" in _classes(achados)
    assert "embedding" in _texto(achados)


def test_tipo_de_coluna_divergente_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["columns"][2]["type"] = "integer"  # bigint -> integer
    achados = compare(_base(), alvo, scan_repo=False)
    assert "coluna" in _classes(achados)
    assert "tipo" in _texto(achados)


def test_nulabilidade_divergente_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["columns"][1]["nullable"] = False
    assert "coluna" in _classes(compare(_base(), alvo, scan_repo=False))


def test_default_divergente_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["columns"][0]["default"] = "gen_random_uuid()"
    achados = compare(_base(), alvo, scan_repo=False)
    assert "coluna" in _classes(achados)
    assert "gen_random_uuid" in _texto(achados)


def test_collation_de_coluna_divergente_acende_o_portao():
    """Collation por COLUNA sobrepõe a do banco — e some num diff que só olha
    o banco."""
    alvo = _base()
    alvo["tables"]["topics"]["columns"][1]["collation"] = "C"
    assert "coluna" in _classes(compare(_base(), alvo, scan_repo=False))


# ── Índices e restrições ──────────────────────────────────────────────────


def test_indice_ausente_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["indexes"] = []
    assert "indice" in _classes(compare(_base(), alvo, scan_repo=False))


def test_definicao_de_indice_divergente_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["indexes"][0]["definition"] = "CREATE INDEX topics_pkey ON topics USING hash (id)"
    achados = compare(_base(), alvo, scan_repo=False)
    assert "indice" in _classes(achados)
    assert "hash" in _texto(achados)


def test_restricao_ausente_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["constraints"] = []
    assert "restricao" in _classes(compare(_base(), alvo, scan_repo=False))


def test_restricao_sobrando_acende_o_portao():
    alvo = _base()
    alvo["tables"]["topics"]["constraints"].append(
        {"name": "topics_conversation_fkey", "definition": "FOREIGN KEY (x) REFERENCES conversations(id)"}
    )
    assert "restricao" in _classes(compare(_base(), alvo, scan_repo=False))


# ── Versão da própria impressão digital ───────────────────────────────────


def test_versao_de_fingerprint_incompativel_para_a_comparacao():
    """Comparar formatos diferentes produziria achados falsos em massa — o
    portão tem que dizer 'regenere', não despejar ruído."""
    alvo = _base()
    alvo["fingerprint_version"] = 2
    achados = compare(_base(), alvo, scan_repo=False)
    assert _classes(achados) == {"referencia"}
    assert "regenere" in _texto(achados)


# ── pgvector: a lista de proibidos ────────────────────────────────────────


def test_recurso_de_pgvector_acima_da_versao_fixada_acende_o_portao(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "schema.sql").write_text(
        "CREATE TABLE t (v halfvec(768));\n", encoding="utf-8"
    )

    achados = scan_pgvector(_base(), raiz=tmp_path)

    assert [f.classe for f in achados] == ["pgvector"]
    assert "halfvec" in _texto(achados)
    assert "0.7.0" in _texto(achados)


def test_scan_de_pgvector_reporta_arquivo_e_linha(tmp_path):
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "schema.sql").write_text(
        "-- linha 1\n-- linha 2\nSELECT binary_quantize(v) FROM t;\n", encoding="utf-8"
    )
    achados = scan_pgvector(_base(), raiz=tmp_path)
    assert "infra/schema.sql:3" in _texto(achados)


def test_scan_de_pgvector_varre_o_python_tambem(tmp_path):
    (tmp_path / "bot").mkdir()
    (tmp_path / "bot" / "x.py").write_text('SQL = "SELECT sparsevec(v)"\n', encoding="utf-8")
    assert scan_pgvector(_base(), raiz=tmp_path)


def test_scan_de_pgvector_e_silencioso_quando_a_versao_ja_alcanca(tmp_path):
    """Com 0.8 instalada, `halfvec` é legítimo — não pode alarmar."""
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "schema.sql").write_text("CREATE TABLE t (v halfvec(768));\n")

    ref = _base()
    ref["extensions"]["vector"] = "0.8.0"

    assert scan_pgvector(ref, raiz=tmp_path) == []


def test_scan_de_pgvector_nao_casa_substring(tmp_path):
    """`halfvector_util` não é `halfvec`. Falso positivo treina gente a ignorar."""
    (tmp_path / "bot").mkdir()
    (tmp_path / "bot" / "x.py").write_text("halfvecuador = 1\nmeu_sparsevecx = 2\n")
    assert scan_pgvector(_base(), raiz=tmp_path) == []


def test_repositorio_real_nao_usa_recurso_de_pgvector_acima_do_fixado():
    """O estado de hoje: nada no repo passa da 0.6."""
    assert scan_pgvector(load_reference(DEFAULT_REFERENCE), raiz=RAIZ) == []


# ── A referência versionada ───────────────────────────────────────────────


def test_referencia_versionada_existe_e_e_json_valido():
    ref = load_reference(DEFAULT_REFERENCE)
    assert ref["fingerprint_version"] == 2
    assert ref["database"]["collate"] == "en_US.UTF-8"
    assert ref["database"]["timezone"] == "UTC"
    assert ref["database"]["data_checksums"] == "on"


def test_o_chat_manager_continua_aposentado_na_referencia():
    """A referência é gerada de `schema.sql` + migrations. Se `conversations`
    reaparecer aqui, alguém ressuscitou o Chat Manager sem querer.

    Este teste asseverava a lista FECHADA de seis tabelas, e por isso ficou
    vermelho quando a referência foi regenerada com o catálogo da F1. A lista
    fechada era assertiva errada: ela transformava "entrou tabela nova" —
    que é o trabalho normal — em falha, e o preço de conviver com isso teria
    sido alguém frouxar o teste. Quem vigia tabela entrando/saindo é o portão,
    contra a referência; o que este teste guarda é só o que ele nomeia."""
    ref = load_reference(DEFAULT_REFERENCE)
    tabelas = set(ref["tables"])
    assert {"conversations", "conversation_tags"}.isdisjoint(tabelas)
    assert {"contacts", "messages", "saved_artifacts",
            "sessions", "topic_name_history", "topics"} <= tabelas
    assert "conversation_id" not in {
        c["name"] for c in ref["tables"]["messages"]["columns"]
    }


def test_referencia_confere_consigo_mesma():
    ref = load_reference(DEFAULT_REFERENCE)
    assert compare(ref, copy.deepcopy(ref)) == []


def test_referencia_nao_inclui_a_tabela_de_controle_do_runner():
    """`schema_migrations` tem `applied_at`; incluí-la faria a impressão digital
    mudar a cada aplicação, e o portão viraria ruído."""
    assert "schema_migrations" not in load_reference(DEFAULT_REFERENCE)["tables"]


# ── Ao vivo (pulado sem banco) ────────────────────────────────────────────

_TEST_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


@pytest.mark.skipif(not _TEST_URL, reason="KOBE_TEST_DATABASE_URL não definida")
def test_ao_vivo_banco_construido_pelo_runner_passa_no_portao():
    """O fecho do item 'schema versionado × banco real': um banco erguido do
    repositório pelo runner tem que conferir com a referência, sem exceção."""
    from infra.schema_fingerprint import from_url

    achados = compare(load_reference(DEFAULT_REFERENCE), from_url(_TEST_URL))

    assert achados == [], "\n".join(str(f) for f in achados)
