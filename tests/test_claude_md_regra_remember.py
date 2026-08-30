#!/usr/bin/env python3
"""A regra dura do `kobe-remember` no `CLAUDE.md` (Highlander v3, F2).

POR QUE UM TESTE SOBRE UM ARQUIVO DE PROSA
-------------------------------------------
O `CLAUDE.md` é o cérebro do agente: o que estiver escrito ali governa o
comportamento em cada turno. A F2 entrega duas coisas — o comando e **a regra
que obriga a usá-lo**. Sem a segunda, a primeira é um utilitário que ninguém
chama, e a dor original (responder sobre o passado de memória) continua igual.

Este arquivo não julga a redação. Ele guarda **os invariantes**: que a regra
existe, que ela nomeia o comando certo, e que os quatro desfechos estão
descritos com a conduta de cada um. Se alguém enxugar a seção e levar embora a
distinção entre *"não há registro"* e *"não deu pra saber"*, aqui fica vermelho
— e é essa distinção, exatamente, que já foi perdida uma vez neste sistema.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_claude_md_regra_remember.py -q
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CLAUDE_MD = RAIZ / "CLAUDE.md"


def _texto() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def test_o_claude_md_existe():
    assert CLAUDE_MD.is_file()


def test_a_regra_dura_esta_escrita():
    """O entregável nº 3 do §5-F2 do briefing, textual: *"regra dura no
    CLAUDE.md: não responder sobre passado sem rodar o comando"*."""
    t = _texto()
    assert "kobe-remember" in t
    assert "REGRA DURA" in t
    assert "não responda sobre o passado sem rodar" in t.lower() or (
        "exige rodar o `kobe-remember` ANTES de responder" in t
    )


def test_resposta_sem_citacao_e_declarada_violacao():
    """"Mesmo que o conteúdo esteja certo" é a metade que importa: sem ela, a
    regra vira "acerte", e acertar de memória é justamente o que não dá pra
    distinguir de confabular."""
    t = _texto()
    assert "sem citação é violação" in t
    assert "mesmo que o conteúdo esteja certo" in t


def test_os_quatro_desfechos_estao_descritos():
    t = _texto()
    for marca in (
        "SEM REGISTRO",
        "MENÇÃO LITERAL",
        "FALHA DO INSTRUMENTO",
        "SEM REGISTRO PARCIAL",
    ):
        assert marca in t, f"desfecho ausente do CLAUDE.md: {marca}"


def test_falha_do_instrumento_e_explicitamente_separada_de_ausencia():
    """O falso negativo silencioso. Já custou meses no `kobe-reflect`: dois
    desfechos no código, um texto na tela."""
    t = _texto()
    assert 'Isto NÃO é "não há registro"' in t
    assert "você não sabe se há registro ou não" in t


def test_mencao_literal_manda_nao_costurar():
    """`Japão` dá 7 ocorrências soltas no acervo, sobre Copa do Mundo. Sem esta
    linha, sete menções viram uma resposta sobre uma viagem que não existe."""
    t = _texto()
    assert "NUNCA costure as menções numa resposta" in t


def test_mencao_literal_nao_afirma_que_nada_responde():
    """O overclaim que a 3ª execução da bateria pegou: o texto dizia "nada
    responde à pergunta" e foi impresso sobre um conjunto que respondia.
    Afirmar não-relevância é mais do que a evidência sustenta — o mesmo erro
    que esta seção inteira existe pra impedir."""
    linha = next(
        l for l in _texto().splitlines()
        if l.startswith("| **`MENÇÃO LITERAL`**")
    )
    # A conferência é na LINHA DE INSTRUÇÃO, não no arquivo inteiro: a nota
    # histórica logo abaixo cita a frase velha de propósito, para que ninguém a
    # reintroduza achando que é melhoria.
    assert "nada responde" not in linha
    assert "nem que não respondem" in linha
    assert "Leia e julgue" in linha


def test_a_diferenca_para_o_kobe_reflect_esta_dita():
    """Os dois se completam e não se substituem. Sem isto escrito, o agente usa
    um no lugar do outro e a resposta fica com a forma certa e a fonte errada."""
    t = _texto()
    assert "kobe-remember` × `kobe-reflect" in t or "kobe-remember × kobe-reflect" in t
    assert "destilado" in t


def test_a_nota_velha_de_que_o_comando_nao_existe_foi_removida():
    """Até 30/08 o `CLAUDE.md` dizia que o `kobe-remember` "ainda não existe".
    Doc que descreve o passado como presente é pior que doc ausente: ela
    ensina o agente a não tentar."""
    t = _texto()
    assert "que ainda não existe" not in t
