#!/usr/bin/env python3
"""Os gatilhos taxativos do `CLAUDE.md` apontam para arquivos que EXISTEM.

POR QUE ESTE TESTE EXISTE
--------------------------
A faxina de setembro/2026 tirou o manual de mecanismo do prompt e deixou no lugar
uma linha de gatilho por mecanismo: *"antes de fazer X, carregue `docs/agente/Y`"*.

Isso troca um custo garantido (o texto no prompt, sempre) por uma promessa (o
agente vai abrir o arquivo quando a ação aparecer). **O modo de falha mais provável
dessa troca não é o agente se comportar pior — é o ponteiro apontar para o vazio**,
depois de alguém renomear ou mover um arquivo. E esse defeito é silencioso: o
agente lê a instrução, tenta abrir, não acha, e segue sem o manual.

Este teste fecha exatamente esse buraco. Ele é determinístico, não chama modelo,
não abre rede, e roda em clone limpo — o mesmo perfil do
`tests/test_schema_reference.py`.

O que ele NÃO faz: dizer se o agente de fato carrega o arquivo. Isso é
comportamento e se mede com sonda, não com `pytest`.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_claude_md_gatilhos.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CLAUDE_MD = RAIZ / "CLAUDE.md"
SECAO = "## Gatilhos taxativos"

# Caminho de satélite citado em qualquer lugar do arquivo, no formato
# `docs/agente/<algo>.md` dentro de crase.
_CAMINHO = re.compile(r"`(docs/agente/[\w.-]+\.md)`")


def _texto() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def _secao_gatilhos() -> str:
    t = _texto()
    assert SECAO in t, "a seção de gatilhos taxativos sumiu do CLAUDE.md"
    return t.split(SECAO, 1)[1].split("\n## ", 1)[0]


def test_a_secao_de_gatilhos_existe():
    """Sem ela, o manual desceu e ninguém ficou sabendo quando carregá-lo."""
    assert SECAO in _texto()


def test_todo_satelite_citado_existe_em_disco():
    """O invariante central: ponteiro que aponta pro vazio é pior que ausência,
    porque o agente acha que tem um caminho e não tem."""
    citados = sorted(set(_CAMINHO.findall(_texto())))
    assert citados, "nenhum satélite citado — a seção de gatilhos está vazia?"
    faltando = [c for c in citados if not (RAIZ / c).is_file()]
    assert not faltando, f"gatilho aponta para arquivo inexistente: {faltando}"


def test_todo_satelite_em_disco_tem_gatilho():
    """O inverso: satélite órfão é texto que saiu do prompt e nunca é carregado
    de volta — ou seja, informação perdida em silêncio."""
    pasta = RAIZ / "docs" / "agente"
    if not pasta.is_dir():
        pytest.skip("docs/agente/ ainda não existe nesta árvore")
    citados = set(_CAMINHO.findall(_texto()))
    orfaos = [
        f"docs/agente/{p.name}"
        for p in sorted(pasta.glob("*.md"))
        if f"docs/agente/{p.name}" not in citados
    ]
    assert not orfaos, f"satélite sem gatilho no CLAUDE.md: {orfaos}"


def test_o_gatilho_e_ancorado_em_acao_e_nao_em_assunto():
    """A distinção que faz o mecanismo funcionar, e que foi paga com uma falha
    observada: gatilho por ASSUNTO ("quando o projeto justificar") exige julgar
    relevância, e julgamento perde pro foco da tarefa. Gatilho por AÇÃO ("antes
    de rodar X") é binário. Se alguém reescrever a seção no formato antigo, aqui
    fica vermelho."""
    secao = _secao_gatilhos()
    assert "ANTES de" in secao, "a tabela precisa ancorar o gatilho numa AÇÃO"
    assert "OBRIGATORIAMENTE" in secao, "a carga não pode ser opcional"
    assert "não depende de você achar que precisa" in secao.lower()


def test_o_satelite_nao_e_apontado_como_leitura_opcional():
    """'Consulte se quiser' devolve o problema pro julgamento e anula o gatilho."""
    secao = _secao_gatilhos()
    for frouxo in ("se necessário", "se quiser", "quando julgar", "se achar"):
        assert frouxo not in secao.lower(), f"gatilho frouxo na seção: {frouxo!r}"


def test_o_primeiro_contato_manda_olhar_o_arquivo():
    """O único gatilho cuja condição NÃO chega no prompt: a ausência de
    `user-data/.onboarded`. Sem a instrução de conferir, ninguém olha — e o
    gatilho vira círculo. Medido na bateria da faxina (07/10/2026): sem esta
    frase o onboarding de uma instalação nova caiu de 5/5 para 1/5; com ela,
    voltou a 5/5. Guarda a instrução, não a redação."""
    secao = _secao_gatilhos()
    assert "verifique se `user-data/.onboarded` existe" in secao
