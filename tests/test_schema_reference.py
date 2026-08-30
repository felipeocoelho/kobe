#!/usr/bin/env python3
"""A trava que impede a referência do portão de nascer velha.

O QUE ESTE ARQUIVO EXISTE PRA IMPEDIR
-------------------------------------
Na F1, a migration `006` entrou e a referência versionada
(`tests/fixtures/schema_expected.json`) **não foi regenerada**. A consequência
não foi um teste vermelho: foi um portão de compatibilidade acusando **4
divergências falsas** — "as tabelas `work_*` existem no alvo e não no schema
versionado" — nos **dois** ambientes, enquanto uma suíte de 691 testes ficava
verde. Um portão que vive vermelho deixa de ser sinal, e passa a ser ruído que
todo mundo aprende a ignorar. É exatamente o defeito que o portão nasceu pra
corrigir, reproduzido dentro dele.

O erro não foi de disciplina; foi de desenho. **Nada** obrigava a referência a
acompanhar uma migration nova.

POR QUE ESTE TESTE NÃO TOCA NO BANCO — e por que isso é o ponto
---------------------------------------------------------------
Ele compara duas listas de string: a que está gravada na referência e a dos
arquivos em `infra/migrations/`. Sem banco, sem rede, sem ambiente. Por isso
roda em **qualquer** máquina, em **todo** `pytest`, inclusive num clone limpo —
e não é do tipo que "pula", que é verde por ausência e foi assim que a `006`
passou.

Quem escreve uma migration nova vê vermelho **no mesmo pytest** em que a
escreveu, com a receita da regeneração na mensagem.

Rodar: .venv/bin/python -m pytest tests/test_schema_reference.py -q
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from infra.compat_gate import (  # noqa: E402
    DEFAULT_REFERENCE,
    cmp_referencia_vs_disco,
    load_reference,
    versoes_no_disco,
)


def _classes(achados) -> set[str]:
    return {f.classe for f in achados}


def _texto(achados) -> str:
    return "\n".join(str(f) for f in achados)


# ── A trava propriamente dita ─────────────────────────────────────────────


def test_referencia_conhece_exatamente_as_migrations_do_disco():
    """VERMELHO AQUI = você escreveu uma migration e não regenerou a referência.

    A receita está na própria mensagem do achado. Não silencie este teste: a
    referência velha é o que faz o portão mentir nos dois ambientes ao mesmo
    tempo.
    """
    achados = cmp_referencia_vs_disco(load_reference(DEFAULT_REFERENCE), versoes_no_disco())
    assert achados == [], _texto(achados)


def test_a_referencia_versionada_registra_a_lista():
    """Impressão digital versão 1 não tinha a chave. Se ela sumir, o teste
    acima viraria verde por ausência — que é o modo de falhar proibido aqui."""
    ref = load_reference(DEFAULT_REFERENCE)
    assert ref.get("migrations") is not None
    assert ref["migrations"] == sorted(ref["migrations"])


def test_o_disco_tem_pelo_menos_o_schema_base():
    """`000` é o `infra/schema.sql`. Se a descoberta voltar vazia, algo quebrou
    no caminho e o teste de cima passaria comparando dois vazios."""
    versoes = versoes_no_disco()
    assert "000" in versoes
    assert len(versoes) >= 2


# ── Os vermelhos injetados de propósito ───────────────────────────────────


def test_migration_nova_sem_regenerar_a_referencia_fica_vermelho():
    """É o caso da `006`, reproduzido: o disco andou, a referência não."""
    ref = load_reference(DEFAULT_REFERENCE)
    disco = versoes_no_disco() + ["999"]
    achados = cmp_referencia_vs_disco(ref, disco)
    assert _classes(achados) == {"referencia"}
    assert "999" in _texto(achados)
    assert "schema_fingerprint.py" in _texto(achados)


def test_migration_apagada_do_disco_tambem_fica_vermelho():
    """O contrário também é divergência: a referência conhece algo que sumiu."""
    ref = load_reference(DEFAULT_REFERENCE)
    disco = versoes_no_disco()[:-1]
    achados = cmp_referencia_vs_disco(ref, disco)
    assert _classes(achados) == {"referencia"}
    assert "nao existe(m) no disco" in _texto(achados)


def test_referencia_de_impressao_digital_antiga_fica_vermelho():
    """Sem a chave `migrations`, não há como julgar — e "não há como julgar"
    aqui é vermelho, não silêncio. Silêncio seria voltar ao estado da F1."""
    ref = copy.deepcopy(load_reference(DEFAULT_REFERENCE))
    ref.pop("migrations", None)
    achados = cmp_referencia_vs_disco(ref, versoes_no_disco())
    assert _classes(achados) == {"referencia"}
    assert "impressao digital" in _texto(achados)


def test_mesma_composicao_em_ordem_diferente_nao_passa():
    """Ordem é dado: é ela que define a sequência de aplicação."""
    ref = copy.deepcopy(load_reference(DEFAULT_REFERENCE))
    ref["migrations"] = list(reversed(ref["migrations"]))
    achados = cmp_referencia_vs_disco(ref, versoes_no_disco())
    assert _classes(achados) == {"referencia"}
    assert "fora de ordem" in _texto(achados)


# ── A referência não pode carregar caminho de máquina ─────────────────────


def test_a_referencia_continua_sem_caminho_absoluto():
    """O repositório é público (`tests/portability_guard.sh`). A regeneração
    passa por um banco de apoio cujo nome é da máquina de quem regenerou —
    nada disso pode vazar pro arquivo versionado."""
    bruto = DEFAULT_REFERENCE.read_text(encoding="utf-8")
    assert "/home/" not in bruto
    assert "/Users/" not in bruto
    # E o nome do banco de apoio não entra: a impressão digital não guarda
    # `datname`, de propósito.
    assert json.loads(bruto)["database"].get("name") is None
