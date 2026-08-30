#!/usr/bin/env python3
"""O quebrador de trechos, o embedder e o indexador (Highlander v3, F2).

O QUE ESTES TESTES GUARDAM
--------------------------
Três promessas que, se quebrarem, quebram em SILÊNCIO — e silêncio é o modo de
falhar que esta fase inteira existe pra impedir:

1. **O quebrador não perde texto.** Se ele perdesse, a metade de baixo de uma
   mensagem longa simplesmente não estaria no índice, e a busca responderia
   "não tenho registro" sobre algo que está gravado. Não haveria erro nenhum
   na tela.
2. **Falha de instrumento não vira "não há registro".** Toda falha do embedder
   é `EmbeddingIndisponivel`, e o indexador a deixa subir em vez de gravar
   metade. Este sistema já cometeu esse falso negativo duas vezes.
3. **A chave desligada deixa a tabela inerte.** É o rollback nomeado no
   briefing; se o indexador escrevesse mesmo desligado, não haveria rollback.

O embedder é testado com um cliente de mentira. Testar contra a API de verdade
custaria dinheiro a cada `pytest` e transformaria a suíte em refém de rede — e o
que precisa ser guardado aqui é o CONTRATO (o que acontece quando dá errado),
não o serviço da OpenAI.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_search_indexer.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.search import embedder, indexer  # noqa: E402
from bot.search.chunker import MAX, chunk  # noqa: E402


# ── O quebrador ───────────────────────────────────────────────────────────


def test_mensagem_curta_vira_um_trecho_so():
    assert chunk("oi") == ["oi"]


def test_mensagem_vazia_nao_vira_trecho():
    assert chunk("") == []
    assert chunk("   \n  ") == []


def test_mensagem_longa_nao_perde_texto():
    """A promessa central. Comparo por caractere, ignorando o espaço em branco
    das emendas — é ele, e só ele, que o corte tem licença pra mexer."""
    texto = "\n\n".join(f"paragrafo {i} " + "x" * 400 for i in range(12))
    pedacos = chunk(texto)
    assert len(pedacos) > 1
    juntos = "".join(pedacos)
    assert len(juntos.replace(" ", "")) >= len(texto.replace(" ", "").replace("\n", ""))
    for i in range(12):
        assert f"paragrafo{i}".replace(" ", "") in juntos.replace(" ", "")


def test_paragrafo_unico_gigante_e_fatiado_com_sobreposicao():
    """Sem parágrafo pra cortar, o corte é cego — e a sobreposição existe pra
    que uma frase que caia na emenda ainda apareça inteira de um dos lados."""
    texto = "y" * (MAX * 3)
    pedacos = chunk(texto)
    assert len(pedacos) >= 3
    assert sum(len(p) for p in pedacos) > len(texto)  # há repetição


def test_o_corte_prefere_paragrafo():
    """Uma janela cega no meio de uma frase embaralha justamente a parte que o
    vetor vai representar."""
    a = "primeiro assunto. " * 30
    b = "segundo assunto. " * 30
    pedacos = chunk(a + "\n\n" + b)
    assert len(pedacos) == 2
    assert "segundo" not in pedacos[0]
    assert "primeiro" not in pedacos[1]


def test_nenhum_trecho_passa_do_teto():
    texto = "\n\n".join("z" * 700 for _ in range(6))
    assert all(len(p) <= MAX for p in chunk(texto))


# ── O embedder: o contrato de falha ───────────────────────────────────────


class _ClienteFake:
    def __init__(self, dim=embedder.DIM, erro=None):
        self.dim = dim
        self.erro = erro
        self.chamadas = 0
        self.embeddings = self

    def create(self, *, model, input):  # noqa: A002 — assinatura do provedor
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return type(
            "R", (), {"data": [type("D", (), {"embedding": [0.1] * self.dim})() for _ in input]}
        )()


def test_lista_vazia_e_o_unico_caminho_que_devolve_vazio():
    assert embedder.embed([], cliente=_ClienteFake()) == []


def test_falha_do_servico_vira_excecao_com_nome_proprio():
    """NUNCA lista vazia. Vazio significa "não havia o que embeddar", e quem lê
    a saída trataria isso como ausência de registro."""
    with pytest.raises(embedder.EmbeddingIndisponivel) as exc:
        embedder.embed(["oi"], cliente=_ClienteFake(erro=TimeoutError("estourou")))
    assert "TimeoutError" in str(exc.value)


def test_dimensao_errada_falha_alto_em_vez_de_gravar_torto():
    """Vetor de dimensão errada não é erro de digitação: é modelo trocado sem
    reindexar, e o sintoma seria resposta errada com nota plausível."""
    with pytest.raises(embedder.EmbeddingIndisponivel) as exc:
        embedder.embed(["oi"], cliente=_ClienteFake(dim=384))
    assert "384" in str(exc.value)
    assert "1536" in str(exc.value)


def test_o_lote_e_respeitado():
    cli = _ClienteFake()
    embedder.embed(["x"] * (embedder.LOTE * 2 + 1), cliente=cli)
    assert cli.chamadas == 3


def test_o_literal_do_pgvector_tem_precisao_suficiente():
    """A diferença de similaridade que decide o piso é da terceira casa."""
    s = embedder.para_sql([0.1234567, -0.9876543])
    assert s == "[0.123457,-0.987654]"


def test_sem_chave_o_embedder_diz_por_que(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    motivo = embedder.disponivel()
    assert motivo and "OPENAI_API_KEY" in motivo


# ── O indexador: a chave e o contrato de erro ─────────────────────────────


class _DBFake:
    """Ponte de mentira. Só o suficiente pra provar quem escreve e quem não."""

    def __init__(self):
        self.escritas: list[tuple] = []
        self.leituras: list[str] = []

    def query(self, sql, params=()):
        self.leituras.append(sql)
        return []

    def execute(self, sql, params=()):
        self.escritas.append((sql, params))
        return []

    def one(self, sql, params=()):
        return None

    def scalar(self, sql, params=()):
        return 0


def test_a_chave_desligada_deixa_a_tabela_inerte(monkeypatch):
    """O rollback nomeado no briefing. Se o indexador escrevesse desligado, não
    haveria rollback nenhum."""
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "false")
    db = _DBFake()
    r = indexer.tick(db)
    assert db.escritas == []
    assert db.leituras == []
    assert not r.fez_algo


@pytest.mark.parametrize("valor", ["1", "true", "TRUE", "on", "yes"])
def test_a_chave_aceita_as_formas_usuais(monkeypatch, valor):
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", valor)
    assert indexer.indexer_enabled()


@pytest.mark.parametrize("valor", ["", "0", "false", "off", "talvez"])
def test_qualquer_outra_coisa_e_desligado(monkeypatch, valor):
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", valor)
    assert not indexer.indexer_enabled()


def test_servico_de_embedding_fora_nao_derruba_o_tick(monkeypatch):
    """O Keyko é single-threaded: uma fonte que estoura derruba as outras. Mas o
    erro não some — ele volta no resultado e vai pro log."""
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "true")

    def _explode(*a, **k):
        raise embedder.EmbeddingIndisponivel("o serviço não respondeu")

    monkeypatch.setattr(indexer, "quebrar_pendentes", lambda db, **k: (0, 0))
    monkeypatch.setattr(indexer, "embeddar_pendentes", _explode)
    r = indexer.tick(_DBFake())
    assert r.erro and "não respondeu" in r.erro
    assert not r.fez_algo


def test_falha_inesperada_tambem_nao_derruba_o_tick(monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "true")

    def _explode(*a, **k):
        raise RuntimeError("banco sumiu")

    monkeypatch.setattr(indexer, "quebrar_pendentes", _explode)
    r = indexer.tick(_DBFake())
    assert r.erro and "RuntimeError" in r.erro


def test_a_carga_inicial_para_quando_nao_ha_mais_o_que_fazer(monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "true")
    chamadas = {"n": 0}

    def _quebra(db, **k):
        chamadas["n"] += 1
        return (1, 2) if chamadas["n"] <= 2 else (0, 0)

    monkeypatch.setattr(indexer, "quebrar_pendentes", _quebra)
    monkeypatch.setattr(indexer, "embeddar_pendentes", lambda db, **k: 0)
    monkeypatch.setattr(indexer, "df_esta_velha", lambda db, **k: False)
    r = indexer.carga_inicial(_DBFake(), log=lambda *_: None)
    assert r.mensagens_quebradas == 2
    assert chamadas["n"] == 3


def test_a_carga_inicial_para_no_primeiro_erro(monkeypatch):
    """Insistir contra um serviço fora só queima cota e enche o log."""
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "true")
    monkeypatch.setattr(indexer, "quebrar_pendentes", lambda db, **k: (1, 1))
    monkeypatch.setattr(
        indexer,
        "embeddar_pendentes",
        lambda db, **k: (_ for _ in ()).throw(embedder.EmbeddingIndisponivel("fora")),
    )
    r = indexer.carga_inicial(_DBFake(), log=lambda *_: None)
    assert r.erro == "fora"


# ── A fonte do Keyko ──────────────────────────────────────────────────────


def test_a_fonte_nao_e_registrada_com_a_chave_desligada(monkeypatch):
    """Uma fonte registrada que não faz nada aparece no log de inicialização
    como se estivesse trabalhando, e "quem o Keyko está observando" deixa de ser
    verdade."""
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "false")
    from bot.search.source import build

    assert build(kobe_home=RAIZ, bot_token="x") is None


def test_a_fonte_nunca_devolve_despertar(monkeypatch):
    """Quebrar texto e pedir vetor não precisa de um modelo. Acordar um `claude
    -p` pra isso gastaria cota — o recurso escasso — na tarefa mais burra."""
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "true")
    from bot.search.source import SearchIndexSource

    fonte = SearchIndexSource(db_factory=_DBFake)
    monkeypatch.setattr(indexer, "tick", lambda db, **k: indexer.Resultado())
    assert fonte.tick() == []
    assert fonte.nome == "search-index"


def test_a_fonte_nao_cai_se_a_ponte_nao_abre(monkeypatch):
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "true")
    from bot.search.source import SearchIndexSource

    def _explode():
        raise RuntimeError("sem banco")

    fonte = SearchIndexSource(db_factory=_explode)
    assert fonte.tick() == []


def test_o_intervalo_tem_piso(monkeypatch):
    """Um valor torto no `.env` não pode virar um laço que consulta o banco
    milhares de vezes por minuto."""
    from bot.search import source

    monkeypatch.setenv("SEARCH_INDEX_INTERVAL_S", "0.001")
    assert source._intervalo() >= source.INTERVALO_MINIMO_S
    monkeypatch.setenv("SEARCH_INDEX_INTERVAL_S", "abacaxi")
    assert source._intervalo() == source.INTERVALO_PADRAO_S
