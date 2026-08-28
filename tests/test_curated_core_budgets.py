"""Bloco A do plano de testes da F0 — tetos SEPARADOS do núcleo curado (E10).

O defeito que estes testes travam: antes havia um teto só, de 6.000 chars, e o
`USER.md` entrava inteiro deixando pro `MEMORY.md` **a sobra**. Com o `USER.md`
real do operador (3.367 chars), a sobra era 2.633 — a memória durável nascia
espremida por um número que não tem nada a ver com ela.

Os enchimentos sao PILCROW (USER) e SECTION (MEMORY) de proposito: contar "U"
ou "M" contaria tambem as letras dos cabecalhos do proprio bloco, e o teste
mediria o texto da moldura em vez do conteudo.

A3 é o teste que prova o conserto: com o `USER.md` estourado, o `MEMORY.md`
continua recebendo o orçamento próprio INTEIRO.
"""

from __future__ import annotations

import importlib

import pytest

from bot.memory import curated_core


@pytest.fixture
def identity(tmp_path):
    """Devolve um escritor de `user-data/identity/<nome>` sob um KOBE_HOME falso."""
    d = tmp_path / "user-data" / "identity"
    d.mkdir(parents=True)

    def escrever(nome: str, conteudo: str) -> None:
        (d / nome).write_text(conteudo, encoding="utf-8")

    escrever.home = tmp_path  # type: ignore[attr-defined]
    return escrever


def test_a1_os_dois_cabem_inteiros(identity):
    """A1 — dentro dos tetos, nada é truncado."""
    identity("USER.md", "\u00b6" * 3500)
    identity("MEMORY.md", "\u00a7" * 5000)

    bloco = curated_core.load_curated_core(identity.home)

    assert bloco is not None
    assert "\u00b6" * 3500 in bloco
    assert "\u00a7" * 5000 in bloco
    assert "truncado" not in bloco


def test_a2_memory_estourado_nao_afeta_o_user(identity):
    """A2 — `MEMORY.md` acima do teto dele é truncado; `USER.md` fica intacto."""
    identity("USER.md", "\u00b6" * 3000)
    identity("MEMORY.md", "\u00a7" * 9000)

    bloco = curated_core.load_curated_core(identity.home)

    assert "\u00b6" * 3000 in bloco
    assert curated_core._TRUNCATED_MARKER in bloco
    # O MEMORY entrou até o teto próprio, nem um char a mais.
    assert bloco.count("\u00a7") == curated_core.CURATED_CORE_MEMORY_CHAR_LIMIT


def test_a3_user_estourado_nao_come_o_orcamento_do_memory(identity):
    """A3 — **o conserto**: `USER.md` inflado não espreme mais o `MEMORY.md`.

    No comportamento antigo (teto único de 6.000), um `USER.md` de 9.000 chars
    consumia o teto inteiro e o `MEMORY.md` era simplesmente descartado, com um
    WARNING que ninguém lê. Agora cada um responde pelo próprio teto.
    """
    identity("USER.md", "\u00b6" * 9000)
    identity("MEMORY.md", "\u00a7" * 5000)

    bloco = curated_core.load_curated_core(identity.home)

    assert curated_core._USER_TRUNCATED_MARKER in bloco
    assert bloco.count("\u00b6") == curated_core.CURATED_CORE_USER_CHAR_LIMIT
    # E o MEMORY.md entra INTEIRO — é isto que não acontecia.
    assert "\u00a7" * 5000 in bloco
    assert curated_core._TRUNCATED_MARKER not in bloco


def test_a4_sem_arquivo_nenhum_e_no_op(tmp_path):
    """A4 — instalação nova: nada existe, nada é injetado."""
    assert curated_core.load_curated_core(tmp_path) is None


def test_a5_empurrao_de_consolidacao_fala_do_memory(identity):
    """A5 — o aviso de ~80% mede o `MEMORY.md` contra o teto DELE.

    Antes o gatilho somava os dois, então um `USER.md` grande fazia o agente ser
    cobrado a consolidar uma memória durável que talvez estivesse vazia.
    """
    memory_limit = curated_core.CURATED_CORE_MEMORY_CHAR_LIMIT
    identity("USER.md", "\u00b6" * 3900)  # sozinho, já passaria de 80% do agregado
    identity("MEMORY.md", "\u00a7" * int(memory_limit * 0.5))

    bloco = curated_core.load_curated_core(identity.home)
    assert "consolide" not in bloco  # memória durável folgada: não cobra nada

    identity("MEMORY.md", "\u00a7" * int(memory_limit * 0.85))
    bloco = curated_core.load_curated_core(identity.home)
    assert "MEMORY.md em" in bloco
    assert "consolide" in bloco


def test_a5b_tetos_sao_configuraveis_por_env(monkeypatch, identity):
    """Rollback numa linha: as duas envs mandam, e o agregado é a soma."""
    monkeypatch.setenv("CURATED_CORE_USER_LIMIT", "100")
    monkeypatch.setenv("CURATED_CORE_MEMORY_LIMIT", "200")
    recarregado = importlib.reload(curated_core)
    try:
        assert recarregado.CURATED_CORE_USER_CHAR_LIMIT == 100
        assert recarregado.CURATED_CORE_MEMORY_CHAR_LIMIT == 200
        assert recarregado.CURATED_CORE_CHAR_LIMIT == 300
    finally:
        monkeypatch.undo()
        importlib.reload(recarregado)


def test_so_memory_sem_user(identity):
    """Instalação que tem fato durável mas ainda não tem identidade preenchida."""
    identity("MEMORY.md", "fato durável")
    bloco = curated_core.load_curated_core(identity.home)
    assert "## MEMORY.md" in bloco
    assert "## USER.md" not in bloco
