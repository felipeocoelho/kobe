#!/usr/bin/env python3
"""O `init`, o marco e o cursor de reconstrução — os dois defeitos do alvo.

Este arquivo cobre DOIS defeitos da mesma família, e vale ler os dois juntos
porque eles são simétricos: o primeiro fazia o alvo da varredura **encolher** em
silêncio; o segundo o fazia **fugir para a frente**. Nos dois casos o intervalo a
reconstruir era decidido por um valor que não devia decidir nada.

O PRIMEIRO DEFEITO — O `init` QUE APAGAVA O PASSADO
-----------------------------------------------------
Até 31/08/2026, `fincar_marco()` punha DOIS cursores: o incremental no topo do
tópico e o de reconstrução onde o incremental estava. Na segunda execução isso
apagava o passado a reconstruir — com o incremental já no topo, o intervalo
`(C, M]` vira vazio e `planejar()` responde *"nada pendente"*, com a mesma cara
de quem terminou o trabalho. Nenhum modelo foi chamado, nada foi escrito, e o
backlog sumiu da vista.

Visto ao vivo em 30/08/2026: o `status` do dev dizia `3595 mensagens · ~95
lotes`, um `init` rodado como pré-condição de roteiro passou a dizer `nada
pendente`. A produção escapou por acidente de ordem — lá o cursor incremental
estava em zero e a guarda `if ja_lido > 0` segurou.

E ISSO É O QUE O TESTE 2 COBRE, QUE É O CASO DA PRODUÇÃO
---------------------------------------------------------
O conserto óbvio — *"só gravar o cursor de reconstrução se ele ainda não
existir"* — **não bastava**: na produção ele nunca chegou a ser criado, então a
regra por existência deixaria a bomba armada para o `init` seguinte. Por isso o
invariante aqui é mais forte, e é ele que os testes exercitam:

    o `init` NUNCA sobe o cursor de reconstrução; só `--refincar` faz isso.

O SEGUNDO DEFEITO — O TETO QUE FUGIA PARA A FRENTE
----------------------------------------------------
O limite de cima da varredura era o cursor **incremental**, lido na hora — e esse
anda com a conversa. Então cada mensagem que a leitura corrente processava
entrava também na conta do que faltava reconstruir: o fim da tabela fugia
enquanto a varredura lia, e o "pendente" nunca chegava a zero enquanto houvesse
conversa acontecendo. Custo de cota (a T8 segura a duplicata), nunca de dado.

O marco passou a ser GRAVADO (escopo `marco`, migration 009), fincado uma vez e
imóvel. O invariante que os testes daqui exercitam:

    o backlog não muda quando o cursor incremental anda;
    e o marco, uma vez fincado, não se move — nem para a frente.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_lucien_reconstrucao.py

Tudo dentro de uma transação **revertida** no teardown: o banco fica como estava.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import reconstrucao, store  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


@pytest.fixture
def cx():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    conn = store.conectar(_URL)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def topico(cx) -> str:
    """O tópico com mais conversa, com os dois cursores zerados.

    Zerar é o ponto de partida de uma instalação que acabou de ligar o LUCIEN —
    e é dentro da transação revertida, então nada disso sobrevive ao teste.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT topic_id, COUNT(*) AS n FROM messages"
        " WHERE role IN ('user','assistant') GROUP BY 1 ORDER BY 2 DESC LIMIT 1"
    )
    linha = cur.fetchone()
    if not linha or int(linha["n"]) < 2:
        pytest.skip("acervo de teste sem tópico com conversa suficiente")
    alvo = str(linha["topic_id"])
    cur.execute("DELETE FROM lucien_cursor WHERE topic_id = %s", (alvo,))
    return alvo


def _backlog(cx, topico: str) -> int:
    return reconstrucao.planejar(cx, topic_id=topico).mensagens


def _cursor(cx, topico: str, scope: str):
    cur = cx.cursor()
    cur.execute(
        "SELECT last_seq FROM lucien_cursor WHERE scope = %s AND topic_id = %s",
        (scope, topico),
    )
    linha = cur.fetchone()
    return None if linha is None else int(linha["last_seq"])


# ── O teste que o operador pediu por nome ─────────────────────────────────


def test_init_duas_vezes_nao_encolhe_o_backlog(cx, topico):
    """Rodar `init` de novo não pode apagar o passado a reconstruir.

    É o caso da PRODUÇÃO, e é o que o conserto por existência não cobria: na
    primeira fincada o cursor de reconstrução nem chega a ser criado.
    """
    reconstrucao.fincar_marco(cx)
    primeiro = _backlog(cx, topico)
    assert primeiro > 0, "sem backlog não há o que este teste possa provar"

    reconstrucao.fincar_marco(cx)
    assert _backlog(cx, topico) == primeiro

    # E uma terceira, porque o defeito era justamente da repetição.
    reconstrucao.fincar_marco(cx)
    assert _backlog(cx, topico) == primeiro
    assert _cursor(cx, topico, "reconstruction") is None


def test_init_preserva_progresso_parcial_da_reconstrucao(cx, topico):
    """Com metade do passado já varrido, `init` não pode declarar o resto lido."""
    reconstrucao.fincar_marco(cx)
    topo = _cursor(cx, topico, "incremental")
    assert topo and topo > 0
    meio = topo // 2
    cx.cursor().execute(
        "INSERT INTO lucien_cursor (scope, topic_id, last_seq)"
        " VALUES ('reconstruction', %s, %s)",
        (topico, meio),
    )
    parcial = _backlog(cx, topico)

    reconstrucao.fincar_marco(cx)

    assert _cursor(cx, topico, "reconstruction") == meio
    assert _backlog(cx, topico) == parcial


def test_refincar_e_o_unico_caminho_que_encolhe(cx, topico):
    """O efeito antigo continua disponível — mas só por ato explícito.

    Ele existe para o caso legítimo: alguém rodou uma passada da leitura
    corrente ANTES de fincar o marco, e sabe que aquele trecho já foi lido.
    """
    reconstrucao.fincar_marco(cx)
    antes = _backlog(cx, topico)
    assert antes > 0

    marcos = reconstrucao.fincar_marco(cx, refincar=True)

    meu = [m for m in marcos if str(m["topic_id"]) == topico]
    assert meu and meu[0]["refincado"] is True
    assert _backlog(cx, topico) == 0
    assert _cursor(cx, topico, "reconstruction") == _cursor(cx, topico, "incremental")


def test_o_cursor_incremental_continua_idempotente(cx, topico):
    """A perna que sempre esteve certa não pode ter sido quebrada no conserto."""
    reconstrucao.fincar_marco(cx)
    topo = _cursor(cx, topico, "incremental")
    reconstrucao.fincar_marco(cx)
    assert _cursor(cx, topico, "incremental") == topo


def test_fincar_marco_relata_o_estado_do_cursor_de_reconstrucao(cx, topico):
    """A saída do `init` precisa DIZER o que fez — o defeito era ser silencioso."""
    marcos = reconstrucao.fincar_marco(cx)
    meu = [m for m in marcos if str(m["topic_id"]) == topico][0]
    assert meu["tem_cursor_reconstrucao"] is False
    assert meu["refincado"] is False

    cx.cursor().execute(
        "INSERT INTO lucien_cursor (scope, topic_id, last_seq)"
        " VALUES ('reconstruction', %s, 7)",
        (topico,),
    )
    marcos = reconstrucao.fincar_marco(cx)
    meu = [m for m in marcos if str(m["topic_id"]) == topico][0]
    assert meu["tem_cursor_reconstrucao"] is True
    assert meu["reconstruido_ate"] == 7


# ── O marco gravado: o teto que fugia para a frente ───────────────────────


def _topo(cx, topico: str) -> int:
    cur = cx.cursor()
    cur.execute(
        "SELECT MAX(seq) AS topo FROM messages"
        " WHERE topic_id = %s AND role IN ('user','assistant')",
        (topico,),
    )
    return int(cur.fetchone()["topo"])


def test_o_backlog_nao_cresce_quando_a_leitura_corrente_anda(cx, topico):
    """O TESTE DO DEFEITO. Com o teto derivado do cursor incremental, ele crescia.

    O cenário é o da vida real: o marco foi fincado num ponto, e desde então a
    leitura corrente processou conversa nova — o cursor incremental subiu. Antes
    da migration 009 o teto era esse cursor, então tudo que a leitura corrente
    leu voltava para a fila da varredura, e o número de pendente subia junto com
    a conversa. Cota gasta relendo trecho já lido, e um backlog que nunca
    converge.
    """
    topo = _topo(cx, topico)
    meio = topo // 2
    assert meio > 0, "acervo de teste pequeno demais para partir ao meio"

    store.fincar_marco_cursor(cx, topico, meio)
    store._avancar_cursor(cx, "incremental", topico, meio)
    antes = _backlog(cx, topico)
    assert antes > 0, "sem backlog não há o que este teste possa provar"

    # A leitura corrente anda — é o trabalho normal dela, todo dia.
    store._avancar_cursor(cx, "incremental", topico, topo)

    assert _backlog(cx, topico) == antes, (
        "o teto da varredura andou junto com a leitura corrente — é exatamente o "
        "defeito que o marco gravado existe para impedir"
    )


def test_o_marco_e_fincado_uma_vez_e_nao_se_move(cx, topico):
    """Fincar de novo não empurra a fronteira para o topo de hoje.

    O simétrico do achado 1 da F3: lá o `init` ENCOLHIA o backlog em silêncio,
    aqui ele o INFLARIA — jogando de volta na varredura tudo que a leitura
    corrente já leu desde a primeira fincada. Os dois são a mesma doença, e a
    cura é a mesma: o alvo não se mexe sozinho.
    """
    topo = _topo(cx, topico)
    meio = topo // 2
    store.fincar_marco_cursor(cx, topico, meio)

    marcos = reconstrucao.fincar_marco(cx)

    assert _cursor(cx, topico, "marco") == meio
    meu = [m for m in marcos if str(m["topic_id"]) == topico][0]
    assert meu["marco_ja_existia"] is True
    assert meu["marco_fincado_agora"] is False
    assert meu["marco_seq"] == meio


def test_init_finca_o_marco_e_relata_qual_e(cx, topico):
    """Na primeira execução o marco nasce no topo, e o `init` tem que DIZER isso.

    A lição do achado 1: comando que mexe em cursor e não relata é comando cujo
    modo de falha é silencioso.
    """
    marcos = reconstrucao.fincar_marco(cx)
    meu = [m for m in marcos if str(m["topic_id"]) == topico][0]

    assert meu["marco_ja_existia"] is False
    assert meu["marco_fincado_agora"] is True
    assert meu["marco_seq"] == _topo(cx, topico)
    assert _cursor(cx, topico, "marco") == meu["marco_seq"]


def test_sem_marco_nao_ha_backlog_mesmo_com_cursor_incremental(cx, topico):
    """Quem responde pela fronteira é o marco — o incremental não fala por ele.

    Um tópico com leitura corrente rodando e nenhum marco declarado é um tópico
    onde o `init` nunca rodou (ou que nasceu depois da fincada). Ele não tem
    passado a reconstruir, e o código velho diria que tem: o backlog inteiro,
    derivado de um cursor que só fala de progresso.
    """
    store._avancar_cursor(cx, "incremental", topico, _topo(cx, topico))

    assert _cursor(cx, topico, "marco") is None
    assert _backlog(cx, topico) == 0


def test_o_teto_que_o_worker_usa_vem_do_marco(cx, topico):
    """E sem marco ele é ZERO, nunca `None`.

    `montar_lote` trata `teto_seq=None` como "sem limite superior". Devolver
    `None` aqui faria a varredura de um tópico sem marco sair lendo a conversa
    inteira, inclusive a de hoje — falha ABERTA. Zero faz o lote sair vazio.
    """
    assert store.marco_reconstrucao(cx, topico) == 0

    topo = _topo(cx, topico)
    store.fincar_marco_cursor(cx, topico, topo)
    assert store.marco_reconstrucao(cx, topico) == topo
