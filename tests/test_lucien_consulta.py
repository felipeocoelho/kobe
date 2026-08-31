#!/usr/bin/env python3
"""A camada de ESTADO — e as três coisas que ela aprendeu apanhando.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
A camada de estado é a que o agente serve **como se fosse conhecido**. Um falso
positivo aqui é pior que um falso positivo na evidência: a evidência vem com a
fala junto e quem lê julga; o estado vem curado e pronto para ser repetido.

Os três testes centrais deste arquivo são defeitos REAIS, achados testando com
dado de verdade em 30/08/2026 — nenhum deles saiu de revisão de código:

1. **O piso herdado da F2 estava errado.** 0,57 foi calibrado sobre `messages`
   (texto longo e ruidoso); afirmações são curtas e densas e separam muito
   melhor. Medido: com resposta 0,570–0,773, sem resposta 0,253–0,289 — folga de
   **+0,281**, contra +0,061 da evidência. Com 0,57 o limiar caía EXATAMENTE em
   cima do verdadeiro-positivo mais fraco.
2. **A perna de palavra estava votando.** Regra da F2 que eu repliquei pela
   metade. *"o campeonato de xadrez"* — assunto que nunca existiu — voltava com
   uma preferência sobre anexo no Telegram, porque `campeonat` e `xadrez` são
   raros mas `decid` não é, e a consulta os unia por `OU`.
3. **A perna de origem herdava lixo da evidência.** Com veredito `MENÇÃO
   LITERAL`, a evidência diz *"não consigo confirmar que responde"* — e passar
   aqueles `seq` para cá transformava uma incerteza declarada em afirmação.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_lucien_consulta.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot import lucien as cfg  # noqa: E402
from bot.lucien import consulta  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


class _DbFalso:
    """Devolve o que lhe mandarem devolver, por trecho da consulta."""

    def __init__(self, por_trecho=None, existe=True):
        self.por_trecho = por_trecho or {}
        self._existe = existe
        self.consultas = []

    def scalar(self, sql, params=()):
        return self._existe

    def query(self, sql, params=()):
        self.consultas.append(sql)
        for marca, linhas in self.por_trecho.items():
            if marca in sql:
                return linhas
        return []


def _linha(**kw):
    base = dict(
        id="11111111-1111-1111-1111-111111111111", subject="assunto",
        statement="uma afirmação durável qualquer, com tamanho suficiente",
        kind="decision", status="vigente", confidence="media",
        valid_from="2026-07-14 10:00:00+00", valid_to=None, source_seq=3059,
        superseded_by=None, topico="Dev Kobe",
    )
    base.update(kw)
    return base


# ── O registro que não existe ─────────────────────────────────────────────


def test_banco_sem_a_migration_008_nao_derruba_o_comando():
    """A produção, hoje, cai exatamente aqui. Um `kobe-remember` quebrado é pior
    que um sem ESTADO."""
    r = consulta.buscar_estado(_DbFalso(existe=False), "qualquer coisa")
    assert not r.disponivel and r.vazio
    assert "migration 008" in (r.erro or "")


def test_banco_fora_nao_derruba_o_comando():
    class _Morto:
        def scalar(self, *a, **k):
            raise ConnectionError("banco fora")

    r = consulta.buscar_estado(_Morto(), "qualquer coisa")
    assert not r.disponivel and r.vazio and "ConnectionError" in (r.erro or "")


# ── Defeito 2: a perna de palavra não vota ────────────────────────────────


def test_a_perna_de_PALAVRA_sozinha_nao_elege():
    """O defeito do "campeonato de xadrez", em teste.

    `bot/search/__init__.py` documenta desde a F2: *ts_rank é uma nota LOCAL —
    mede o casamento dentro do documento e não sabe que o termo é banal no
    acervo inteiro*. Uma afirmação que só casou por palavra não responde à
    pergunta; ela apenas compartilha uma palavra com ela.
    """
    db = _DbFalso({"to_tsquery": [_linha()]})
    r = consulta.buscar_estado(db, "o que a gente decidiu sobre o campeonato de xadrez")
    assert r.vazio, "a perna de palavra elegeu sozinha — o defeito voltou"


def test_a_perna_de_palavra_continua_ORDENANDO():
    """Ela não elege, mas concordar com quem elegeu ainda vale: `_ordem` conta
    pernas distintas."""
    a = consulta.Afirmacao(
        id="1", topico="T", subject="s", statement="x", kind="decision",
        status="vigente", confidence="media", valid_from="2026-01-01",
        cos=0.5, pernas=["sentido", "palavra"],
    )
    b = consulta.Afirmacao(
        id="2", topico="T", subject="s", statement="x", kind="decision",
        status="vigente", confidence="media", valid_from="2026-01-01",
        cos=0.5, pernas=["sentido"],
    )
    assert consulta._ordem(a) > consulta._ordem(b)


@pytest.mark.parametrize("perna,sql", [
    ("literal", "ILIKE"),
    ("origem", "source_seq = ANY"),
])
def test_as_pernas_que_ELEGEM(perna, sql):
    db = _DbFalso({sql: [_linha()]})
    r = consulta.buscar_estado(db, "compat_gate", seqs_da_evidencia=[3059])
    assert len(r.vigentes) == 1 and perna in r.vigentes[0].pernas


# ── Defeito 1: o piso é próprio, e medido ─────────────────────────────────


def test_o_piso_do_estado_NAO_e_o_da_evidencia():
    """Se alguém "unificar" os dois um dia, isto acende. Eles medem
    distribuições diferentes: `messages` é texto longo e ruidoso, afirmação é
    curta e densa."""
    from bot.search import query as q

    assert cfg.piso_cos() != q.piso_cos()
    assert 0.30 < cfg.piso_cos() < 0.50, (
        "o piso saiu da faixa medida (sem resposta até 0,289; com resposta a "
        "partir de 0,570)"
    )


def test_o_piso_sai_do_env(monkeypatch):
    monkeypatch.setenv("LUCIEN_PISO_COS", "0.61")
    assert abs(cfg.piso_cos() - 0.61) < 1e-9


# ── A ordenação: similaridade antes de data ───────────────────────────────


def test_similaridade_vem_antes_da_data():
    """Medido: com a data no lugar da similaridade, *"onde fica a base de
    conhecimento do Kobe"* trazia em primeiro uma afirmação sobre orquestrador
    de missões — as duas empatadas em uma perna, e a mais nova ganhando. Data
    sozinha ordena por acaso."""
    velha_e_certa = consulta.Afirmacao(
        id="1", topico="T", subject="s", statement="a certa", kind="decision",
        status="vigente", confidence="media", valid_from="2026-05-14",
        cos=0.71, pernas=["sentido"],
    )
    nova_e_torta = consulta.Afirmacao(
        id="2", topico="T", subject="s", statement="a torta", kind="decision",
        status="vigente", confidence="media", valid_from="2026-08-01",
        cos=0.47, pernas=["sentido"],
    )
    ordenadas = sorted([nova_e_torta, velha_e_certa], key=consulta._ordem, reverse=True)
    assert ordenadas[0].statement == "a certa"


def test_vigente_vem_antes_de_encerrada():
    viva = consulta.Afirmacao(
        id="1", topico="T", subject="s", statement="vale", kind="decision",
        status="vigente", confidence="media", valid_from="2026-01-01",
        cos=0.5, pernas=["sentido"],
    )
    morta = consulta.Afirmacao(
        id="2", topico="T", subject="s", statement="não vale", kind="decision",
        status="superada", confidence="media", valid_from="2026-08-01",
        cos=0.9, pernas=["sentido"],
    )
    assert consulta._ordem(viva) > consulta._ordem(morta)


# ── O que a evidência empresta ────────────────────────────────────────────


def test_sem_seq_da_evidencia_a_perna_de_origem_nao_roda():
    """É `kobe-remember` quem decide passar os `seq`, e ele só passa quando o
    veredito da evidência é `ACHOU`. Aqui se prova que a camada respeita: sem
    `seq`, ela não inventa a consulta."""
    db = _DbFalso()
    consulta.buscar_estado(db, "qualquer coisa")
    assert not any("source_seq = ANY" in s for s in db.consultas)


# ── Contra o banco de verdade ─────────────────────────────────────────────


@pytest.fixture
def db_real():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida")
    from bot.db import KobeDB

    db = KobeDB(_URL)
    try:
        yield db
    finally:
        db.close()


def test_a_camada_responde_e_recusa_contra_dado_real(db_real):
    """As duas pontas na mesma asserção, porque uma sem a outra não diz nada:
    uma camada que nunca acha "nunca inventa", e uma que sempre acha "sempre
    responde"."""
    cur = db_real.scalar("SELECT COUNT(*) FROM lucien_claims WHERE status='vigente'")
    if not cur:
        pytest.skip("registro de estado vazio neste banco")

    inexistente = consulta.buscar_estado(
        db_real, "o que a gente decidiu sobre o campeonato de xadrez"
    )
    assert not inexistente.vigentes, (
        "inventou estado sobre assunto que nunca existiu — é a trava desta camada"
    )


def test_superada_nunca_aparece_como_vigente(db_real):
    linhas = db_real.query(
        "SELECT status FROM lucien_claims WHERE status <> 'vigente' LIMIT 1"
    )
    if not linhas:
        pytest.skip("sem afirmação encerrada neste banco")
    r = consulta.buscar_estado(db_real, "qualquer assunto", seqs_da_evidencia=[])
    assert all(a.status == "vigente" for a in r.vigentes)


# ── O memo do vetor da pergunta ───────────────────────────────────────────


def test_a_mesma_pergunta_nao_e_embeddada_duas_vezes(monkeypatch):
    """A F3 acrescentou uma segunda busca por sentido sobre a MESMA pergunta.
    Medido no `kobe-remember`: a ida e volta extra custava ~0,3 s por comando,
    para um resultado bit a bit idêntico."""
    from bot.search import embedder

    embedder._CACHE_PERGUNTA.clear()
    chamadas = []

    def _falso(textos, cliente=None):
        chamadas.append(tuple(textos))
        return [[0.1] * embedder.DIM]

    monkeypatch.setattr(embedder, "embed", _falso)
    a = embedder.embed_um("a mesma pergunta")
    b = embedder.embed_um("a mesma pergunta")
    assert a == b and len(chamadas) == 1
    embedder.embed_um("outra pergunta")
    assert len(chamadas) == 2
    embedder._CACHE_PERGUNTA.clear()


def test_falha_de_embedding_NAO_e_cacheada(monkeypatch):
    """Senão um tropeço de rede no primeiro uso condenaria o comando inteiro —
    e "não deu pra saber" viraria "não há", que é o erro que este sistema já
    cometeu duas vezes."""
    from bot.search import embedder

    embedder._CACHE_PERGUNTA.clear()
    estado = {"falhar": True}

    def _instavel(textos, cliente=None):
        if estado["falhar"]:
            estado["falhar"] = False
            raise embedder.EmbeddingIndisponivel("rede tropeçou")
        return [[0.2] * embedder.DIM]

    monkeypatch.setattr(embedder, "embed", _instavel)
    with pytest.raises(embedder.EmbeddingIndisponivel):
        embedder.embed_um("pergunta")
    assert embedder.embed_um("pergunta")[0] == 0.2
    embedder._CACHE_PERGUNTA.clear()
