#!/usr/bin/env python3
"""A regra de destino: fato do operador vai para a TABELA, não para arquivo.

POR QUE ESTE TESTE EXISTE
--------------------------
Em 04/09/2026 o operador contou uma preferência nova e o agente a gravou num `.md`
da knowledge base — e **reportou isso como se fosse o caminho certo**. A reação
dele: *"você registrou um fato num arquivo, que não tem governança nenhuma"*.

O defeito não foi o arquivo (era o único caminho existente na hora). Foi não haver
**nem porta, nem regra**. A porta virou `bot/bin/kobe-fato`; a regra virou a seção
do `CLAUDE.md` que este arquivo guarda.

O QUE ESTE TESTE PROTEGE, E O QUE ELE NÃO CONSEGUE
---------------------------------------------------
Ele guarda que a **regra existe, nomeia a porta certa, e não oferece o arquivo
como plano B silencioso**. É verificação determinística: sem modelo, sem rede,
roda em clone limpo.

Ele **não** prova que o agente obedece — isso é comportamento e se mede com sonda.
Mas ele fecha o modo de falha mais provável e mais silencioso: alguém enxuga o
`CLAUDE.md` numa faxina futura, a regra sai junto, e a bagunça volta sem nada
acusar. Foi exatamente assim que a instrução de analogia sobreviveu em seis
arquivos enquanto a correção existia num só.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_regra_fato_vai_para_tabela.py -q
"""

from __future__ import annotations

from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CLAUDE_MD = RAIZ / "CLAUDE.md"
SECAO = "## Fato do operador vai para a TABELA"


def _texto() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def _secao() -> str:
    t = _texto()
    assert SECAO in t, (
        "a regra de destino sumiu do CLAUDE.md. Sem ela, fato volta a ser gravado "
        "em arquivo sem vigência, sem origem e sem revogação."
    )
    return t.split(SECAO, 1)[1].split("\n## ", 1)[0]


def test_a_regra_existe():
    assert SECAO in _texto()


def test_a_regra_nomeia_a_porta_e_o_comando():
    """Regra que não diz COMO fazer a coisa certa é regra que não pega."""
    s = _secao()
    assert "kobe-fato" in s, "a regra tem que nomear o comando, não só o princípio"
    assert "registrar" in s
    for tipo in ("preferencia", "decisao", "aberto", "fato"):
        assert tipo in s, f"o tipo {tipo!r} não está documentado na regra"


def test_a_regra_exige_o_relato_com_origem():
    """A frase de volta é metade do mecanismo: 'registrei na tabela de fatos, origem #N'
    é conferível; 'salvei' não é."""
    s = _secao()
    assert "registrei na tabela de fatos" in s
    assert "origem" in s
    assert "salvei num arquivo" in s, (
        "a regra precisa nomear o comportamento ERRADO para poder proibi-lo"
    )


def test_a_regra_da_o_criterio_entre_linha_e_arquivo():
    """Fronteira sem régua não é fronteira. A régua é: pode ser revogado um dia?"""
    s = _secao()
    assert "pode ser revogado" in s, "falta o teste operável entre linha e arquivo"
    assert "documento" in s.lower(), "falta dizer o que legitimamente continua arquivo"


def test_a_regra_proibe_o_arquivo_como_plano_B_silencioso():
    """O modo de falha real não é o agente escolher o arquivo de propósito — é
    ele cair no arquivo quando a porta falha, sem dizer nada. O silêncio é como
    a bagunça voltou da última vez."""
    s = _secao()
    assert "plano B" in s
    assert "silêncio" in s.lower()


def test_a_porta_existe_em_disco():
    """Regra que aponta pra comando inexistente é pior que ausência de regra."""
    assert (RAIZ / "bot" / "bin" / "kobe-fato").is_file(), (
        "a regra nomeia `bot/bin/kobe-fato` e o arquivo não existe"
    )
    assert (RAIZ / "bot" / "lucien" / "porta.py").is_file()


def test_a_trava_T5_esta_no_store():
    """A porta só é confiável se o LUCIEN não puder revogar o que o operador
    registrou. Se a T5 sumir, a palavra dele volta a poder ser sobrescrita por um
    modelo, em silêncio — que é o pior desfecho que este registro comporta."""
    store = (RAIZ / "bot" / "lucien" / "store.py").read_text(encoding="utf-8")
    assert store.count('trava="T5"') >= 2, (
        "a T5 tem que guardar os DOIS caminhos que mudam status: superar e encerrar"
    )
    assert 'created_by, created_by' not in store
    # E a coluna precisa estar no SELECT de `_imagem`, senão a trava lê None e
    # vira um no-op silencioso — foi assim que ela nasceu quebrada.
    assert "source_seq, created_by" in store, (
        "`_imagem` precisa trazer `created_by`, senão a T5 nunca dispara"
    )


def test_a_regua_de_nomenclatura_esta_escrita():
    """Highlander é o sistema; LUCIEN é o curador; a tabela de fatos é o destino.

    Isto é teste de palavra, e existe por um motivo declarado pelo operador em
    04/09/2026: é erro pequeno hoje que vira erro grande depois, quando "LUCIEN"
    começar a significar "o sistema de memória" pra quem ler o código daqui a
    seis meses. A régua nasceu porque a própria sala escreveu "registrei no
    LUCIEN" em código, prompt e teste antes de ser corrigida.
    """
    s = _secao()
    assert "Highlander" in s, "falta dizer quem é o sistema"
    assert "curador" in s, "falta dizer que LUCIEN é peça, não sistema"
    assert "registrei na tabela de fatos" in s, "falta a forma certa"
    assert 'Nunca diga "registrei no LUCIEN"' in s, "falta proibir a forma errada"


def test_nenhum_arquivo_do_projeto_diz_registrei_no_lucien():
    """A forma errada não pode voltar por descuido — nem em código, nem em texto.

    Cobre o corpo do projeto (não o CHANGELOG, que é histórico e registra a
    correção citando a forma errada de propósito).
    """
    alvos = [RAIZ / "CLAUDE.md", RAIZ / "bot" / "lucien" / "porta.py",
             RAIZ / "bot" / "bin" / "kobe-fato"]
    for p in alvos:
        texto = p.read_text(encoding="utf-8")
        secao_regra = texto.split(SECAO, 1)[1] if SECAO in texto else texto
        # a única ocorrência tolerada é a PROIBIÇÃO, dentro de aspas
        sobras = secao_regra.replace('Nunca diga "registrei no LUCIEN"', "")
        assert "registrei no LUCIEN" not in sobras, (
            f"{p.name} voltou a usar a forma errada — o destino é a tabela de fatos, "
            "não o curador"
        )
