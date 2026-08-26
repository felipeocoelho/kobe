#!/usr/bin/env python3
"""O instalador provisiona o banco — ele não o pressupõe.

A DIRETRIZ QUE ESTES TESTES TRAVAM
-----------------------------------
O instalador do Kobe não parte do princípio de que existe um banco em algum
lugar: ele descobre a situação, cria o que faltar, e executa o DDL em tempo de
execução. Nada de "crie o banco antes", nada de "cole este SQL num painel".

O TESTE QUE MAIS IMPORTA AQUI é o dos **parâmetros de criação**, e ele não é
sobre estética. Um banco criado com o default do `initdb` nasce divergente de
dois jeitos que ninguém percebe no dia:

- **Collation**: `C.UTF-8` ordena por byte cru; `en_US.UTF-8` não. O dado é o
  mesmo e a ordem de `ORDER BY <texto>` muda — e o Kobe ordena a lista de
  contatos por nome. Pior: **collation não se troca depois** sem recriar o
  banco.
- **Fuso**: todo banco herda o `TimeZone` do cluster, que fica no fuso local da
  máquina. Isso muda o TEXTO que o driver devolve para `timestamptz`.

Se o criador e o juiz (`infra/compat_gate.py`) discordassem, toda instalação
nova acenderia o portão no primeiro dia. Por isso os dois leem a **mesma**
referência, e há teste exigindo exatamente isso.

Rodar: .venv/bin/python -m pytest tests/test_provision_db.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from infra import provision_db as prov
from infra.compat_gate import DEFAULT_REFERENCE, load_reference


# ── Os parâmetros de criação: a parte que não pode divergir ───────────────


def test_parametros_saem_da_referencia_versionada():
    p = prov.parametros_de_criacao()
    ref = load_reference(DEFAULT_REFERENCE)["database"]

    assert p["encoding"] == ref["encoding"]
    assert p["collate"] == ref["collate"]
    assert p["ctype"] == ref["ctype"]
    assert p["timezone"] == ref["timezone"]


def test_o_criador_e_o_juiz_leem_o_MESMO_arquivo():
    """Duas fontes divergiriam com o tempo, e o sintoma seria toda instalação
    nova acendendo o portão. Uma fonte, dois consumidores."""
    assert prov.REFERENCE == DEFAULT_REFERENCE


def test_um_banco_criado_com_estes_parametros_passaria_no_portao():
    """Fecha o círculo sem tocar em banco: os valores que o provisionador usaria
    são exatamente os que o portão exige."""
    from infra.compat_gate import _cmp_ambiente

    p = prov.parametros_de_criacao()
    ref = load_reference(DEFAULT_REFERENCE)
    alvo = {"database": {**ref["database"], **{
        "encoding": p["encoding"], "collate": p["collate"],
        "ctype": p["ctype"], "timezone": p["timezone"],
    }}}

    assert _cmp_ambiente(ref, alvo) == []


def test_referencia_ilegivel_cai_num_default_sao_e_nao_no_do_initdb(tmp_path):
    """Uma cópia parcial do repo não pode fazer o banco nascer em `C.UTF-8`."""
    vazio = tmp_path / "nao_existe.json"

    p = prov.parametros_de_criacao(referencia=vazio)

    assert p["collate"] == "en_US.UTF-8"
    assert p["timezone"] == "UTC"
    assert p["collate"] != "C.UTF-8"


def test_referencia_corrompida_tambem_cai_no_default(tmp_path):
    ruim = tmp_path / "ruim.json"
    ruim.write_text("{ isto nao e json", encoding="utf-8")

    assert prov.parametros_de_criacao(referencia=ruim)["collate"] == "en_US.UTF-8"


def test_referencia_sem_a_chave_esperada_cai_no_default(tmp_path):
    parcial = tmp_path / "parcial.json"
    parcial.write_text(json.dumps({"tables": {}}), encoding="utf-8")

    assert prov.parametros_de_criacao(referencia=parcial)["encoding"] == "UTF8"


# ── Leitura da string de conexão ──────────────────────────────────────────


@pytest.mark.parametrize(
    "url,esperado",
    [
        ("postgresql:///kobe", "kobe"),
        ("postgresql://usuario:senha@localhost:5432/kobe_prod", "kobe_prod"),
        ("postgresql://localhost/meu_banco", "meu_banco"),
        ("dbname=kobe host=/tmp", "kobe"),
    ],
)
def test_nome_do_banco_e_extraido_das_formas_usuais(url, esperado):
    assert prov.nome_do_banco(url) == esperado


def test_url_sem_nome_de_banco_e_erro_com_explicacao():
    """Sem nome não há o que criar nem onde aplicar o schema — e o erro tem que
    dizer isso, não estourar um KeyError."""
    with pytest.raises(prov.ProvisionError, match="nao nomeia um banco"):
        prov.nome_do_banco("postgresql://localhost:5432")


def test_url_malformada_e_erro_tratado():
    with pytest.raises(prov.ProvisionError, match="invalida"):
        prov.nome_do_banco("isto nao e uma url ==== nada")


def test_a_url_de_manutencao_aponta_pro_banco_postgres():
    """Não dá pra criar um banco estando conectado a ele."""
    import psycopg

    info = psycopg.conninfo.conninfo_to_dict(
        prov.url_de_manutencao("postgresql://alguem@servidor:5433/kobe")
    )

    assert info["dbname"] == "postgres"


def test_a_url_de_manutencao_preserva_host_porta_e_usuario():
    """Trocar só o banco — mudar o host por engano provisionaria no lugar errado."""
    import psycopg

    info = psycopg.conninfo.conninfo_to_dict(
        prov.url_de_manutencao("postgresql://alguem@servidor:5433/kobe")
    )

    assert info["host"] == "servidor"
    assert info["port"] == "5433"
    assert info["user"] == "alguem"


# ── Linha de comando ──────────────────────────────────────────────────────


def test_alvo_ausente_sai_com_erro(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert prov.main([]) == 1
    assert "alvo ausente" in capsys.readouterr().err


def test_servidor_inalcancavel_sai_com_codigo_proprio(capsys):
    """`2` distingue "não há servidor" de "há, mas algo deu errado" — o
    instalador diz coisas diferentes nos dois casos."""
    codigo = prov.main(
        ["--database-url", "postgresql://naoexiste.invalido:5499/kobe"]
    )

    assert codigo == 2
    assert "servidor PostgreSQL" in capsys.readouterr().err


# ── A fronteira declarada, para a próxima sessão herdar ───────────────────


def test_a_fronteira_do_que_falta_esta_escrita_no_modulo():
    """Trava documental. O que este arquivo NÃO faz (detectar cluster, decidir
    entre clusters, criar role, instalar o Postgres) é escopo da sessão do
    instalador público. Se alguém apagar a lista, a próxima sessão herda um
    arquivo que parece completo e não é."""
    texto = (RAIZ / "infra" / "provision_db.py").read_text(encoding="utf-8")

    assert "FRONTEIRA" in texto
    for palavra in ("DETECTAR", "DECIDIR", "CRIAR ROLE", "INSTALAR"):
        assert palavra in texto, f"a fronteira perdeu o item {palavra!r}"


# ── Ao vivo: criar de verdade (pulado sem banco) ──────────────────────────

_TEST_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


@pytest.mark.skipif(not _TEST_URL, reason="KOBE_TEST_DATABASE_URL não definida")
def test_ao_vivo_servidor_alcancavel_reporta_a_versao():
    alcancavel, detalhe = prov.servidor_alcancavel(_TEST_URL)

    assert alcancavel
    assert "PostgreSQL" in detalhe


@pytest.mark.skipif(not _TEST_URL, reason="KOBE_TEST_DATABASE_URL não definida")
def test_ao_vivo_banco_existente_e_reconhecido_e_nao_recriado():
    """Idempotência: rodar o instalador de novo não pode tocar num banco cheio."""
    assert prov.banco_existe(_TEST_URL) is True
    assert prov.garantir(_TEST_URL) is False, "disse que criou um banco que já existia"


@pytest.mark.skipif(not _TEST_URL, reason="KOBE_TEST_DATABASE_URL não definida")
def test_ao_vivo_banco_inexistente_e_detectado():
    import psycopg

    info = psycopg.conninfo.conninfo_to_dict(_TEST_URL)
    info["dbname"] = "kobe_banco_que_nao_existe_xyzzy"
    inexistente = psycopg.conninfo.make_conninfo(**info)

    assert prov.banco_existe(inexistente) is False


@pytest.mark.skipif(not _TEST_URL, reason="KOBE_TEST_DATABASE_URL não definida")
def test_ao_vivo_dry_run_nao_cria_nada():
    import psycopg

    info = psycopg.conninfo.conninfo_to_dict(_TEST_URL)
    info["dbname"] = "kobe_dry_run_xyzzy"
    inexistente = psycopg.conninfo.make_conninfo(**info)

    assert prov.garantir(inexistente, dry_run=True) is False
    assert prov.banco_existe(inexistente) is False, "o dry-run criou o banco"
