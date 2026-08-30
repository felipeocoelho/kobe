#!/usr/bin/env python3
"""Todo roteiro versionado tem que ser executável pelo `dev_inject`.

POR QUE ISTO EXISTE
-------------------
Ao escrever a bateria da F2 descobri que `tests/roteiros/f1-dispatch.txt` — a
metade conversacional da fase anterior, versionada e citada no changelog —
**não parseava**. Ele foi escrito na sintaxe do briefing, que põe a espera
DEPOIS da mensagem (`mensagem` / `@25`), enquanto o `infra/dev_inject.py` a
espera ANTES, prefixando (`@25 mensagem`). São a mesma pausa vista de lados
diferentes, e o arquivo estava numa e a ferramenta na outra.

O sintoma é o pior tipo: o roteiro fica ali, parecendo pronto, e só falha na
hora em que alguém precisa dele — provavelmente sob pressão, provavelmente
meses depois, provavelmente sem o contexto de quem o escreveu.

Este teste não julga o conteúdo de bateria nenhuma. Ele guarda uma coisa só:
**um roteiro que está no repositório roda**. Roteiro é ferramenta de operador,
e ferramenta de operador que não abre é pior que ferramenta ausente.

Roda sem banco, sem rede e sem bot — só lê arquivo.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

ROTEIROS = sorted((RAIZ / "tests" / "roteiros").glob("*.txt"))


def _dev_inject():
    """Carrega `infra/dev_inject.py` sem importar o pacote `infra` inteiro."""
    spec = importlib.util.spec_from_file_location(
        "_dev_inject_para_o_teste", RAIZ / "infra" / "dev_inject.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    return mod


def test_ha_roteiros_versionados():
    """Se a pasta esvaziar, o teste abaixo passaria por vacuidade — que é o modo
    de falhar que este arquivo existe pra impedir."""
    assert ROTEIROS, "nenhum roteiro em tests/roteiros/"


@pytest.mark.parametrize("caminho", ROTEIROS, ids=lambda p: p.name)
def test_o_roteiro_parseia(caminho):
    """VERMELHO AQUI = o roteiro está escrito numa sintaxe que a ferramenta não
    lê. A espera vai PREFIXANDO a mensagem: `@25 texto da mensagem`."""
    passos = _dev_inject().ler_roteiro(caminho)
    assert passos, f"{caminho.name} não tem nenhuma mensagem"


@pytest.mark.parametrize("caminho", ROTEIROS, ids=lambda p: p.name)
def test_a_primeira_mensagem_nao_espera_a_toa(caminho):
    """Esperar antes da PRIMEIRA mensagem é tempo morto: não há turno anterior
    para aguardar. Não é erro, é desperdício — e num roteiro caro isso conta."""
    primeiro = _dev_inject().ler_roteiro(caminho)[0]
    assert primeiro[0] == 0.0, (
        f"{caminho.name}: a primeira mensagem espera {primeiro[0]}s sem ter o "
        "que aguardar"
    )
