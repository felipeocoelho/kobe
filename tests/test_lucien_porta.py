#!/usr/bin/env python3
"""A porta de entrada deliberada da tabela de fatos — `bot/lucien/porta.py`.

O que estes testes guardam, e por quê:

1. **A origem continua obrigatória e REAL.** A porta existe para o agente gravar
   proativamente, não para afrouxar a trava que sustenta o registro. Se um dia
   alguém "resolver" o problema do fato-que-nasce-em-arquivo relaxando a chave
   estrangeira, aqui fica vermelho.
2. **`valid_from` é a data do FATO, não a da gravação.** Registrar hoje algo dito
   ontem não pode fazer a afirmação nascer mentindo sobre quando passou a valer.
3. **A marca `created_by='operador'`**, que é o que a trava T5 usa para impedir o
   LUCIEN de revogar a palavra do operador.
4. **Confiança cai quando a origem é áudio.** Transcrição erra, e erro de
   transcrição virando fato permanente já aconteceu neste acervo.
5. **Revogar data o fim, não apaga.**
6. **As recusas**, que são metade do valor: registro ruim é pior que registro
   ausente, porque entra no boletim com cara de verdade apurada.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_lucien_porta.py

Tudo roda dentro de uma transação **revertida** no teardown — os testes que
gravam chamam a camada interna, que não commita. Não há comando destrutivo aqui.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import porta, store  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")

# Assunto único por execução. Sem isto, uma linha que sobreviva de uma execução
# anterior faz o dedupe da porta disparar e o teste mede a coisa errada — foi o
# que aconteceu na primeira rodada destes testes, quando a porta ainda commitava
# por dentro e a transação revertida não isolava nada.
_SUFIXO = uuid.uuid4().hex[:8]


def _assunto(base: str) -> str:
    return f"{base} {_SUFIXO}"


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
    """Um tópico que tenha mensagem de operador com `seq` — a porta precisa de
    origem real, então o teste também."""
    cur = cx.cursor()
    cur.execute(
        "SELECT topic_id FROM messages WHERE role = 'user' AND seq IS NOT NULL"
        " ORDER BY seq DESC LIMIT 1"
    )
    linha = cur.fetchone()
    if linha is None:
        pytest.skip("acervo sem mensagem de operador com seq")
    return str(linha["topic_id"])


# ── o caminho feliz ───────────────────────────────────────────────────────

def test_registra_com_origem_real_e_marca_do_operador(cx, topico):
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste porta"),
        afirmacao="Afirmação de teste da porta deliberada, longa o bastante para passar no piso.",
        tipo="preferencia",
    )
    assert not r["duplicata"]
    cur = cx.cursor()
    cur.execute(
        "SELECT created_by, kind, status, source_seq, source_message_id, valid_from"
        "  FROM lucien_claims WHERE id = %s", (r["id"],),
    )
    linha = cur.fetchone()
    assert linha["created_by"] == "operador", "a marca é o que a trava T5 usa"
    assert linha["kind"] == "preference"
    assert linha["status"] == "vigente"
    assert linha["source_message_id"] is not None, "origem é obrigatória"
    assert int(linha["source_seq"]) == r["seq"]


def test_valid_from_e_a_data_do_fato_e_nao_a_da_gravacao(cx, topico):
    """Se `valid_from` fosse `NOW()`, uma afirmação registrada hoje sobre algo
    dito ontem nasceria dizendo que passou a valer hoje — que é a mesma classe
    de mentira que o registro inteiro existe para não contar."""
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste data"),
        afirmacao="Afirmação para conferir que a vigência vem da mensagem de origem.",
        tipo="fato",
    )
    cur = cx.cursor()
    cur.execute(
        "SELECT c.valid_from, m.created_at FROM lucien_claims c"
        "  JOIN messages m ON m.id = c.source_message_id WHERE c.id = %s", (r["id"],),
    )
    linha = cur.fetchone()
    assert linha["valid_from"] == linha["created_at"]


def test_origem_de_audio_derruba_a_confianca(cx, topico):
    cur = cx.cursor()
    cur.execute(
        "SELECT seq FROM messages WHERE topic_id = %s AND role = 'user'"
        "   AND audio_transcribed = true AND seq IS NOT NULL"
        " ORDER BY seq DESC LIMIT 1", (topico,),
    )
    linha = cur.fetchone()
    if linha is None:
        pytest.skip("tópico sem mensagem de áudio no acervo")
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste audio"),
        afirmacao="Afirmação cuja origem veio de transcrição, para conferir a confiança.",
        tipo="fato", origem_seq=int(linha["seq"]),
    )
    assert r["confianca"] == "media", "transcrição erra; fato de áudio não nasce 'alta'"


def test_repetir_a_mesma_afirmacao_nao_duplica(cx, topico):
    """Sem isto, um retry do agente duplica a linha no boletim."""
    args = dict(
        topic_id=topico, assunto=_assunto("teste dedupe"),
        afirmacao="Afirmação repetida de propósito para exercitar a deduplicação da porta.",
        tipo="decisao",
    )
    a = porta.registrar(cx, **args)
    b = porta.registrar(cx, **args)
    assert b["duplicata"] is True
    assert b["id"] == a["id"]


# ── revogar data o fim, não apaga ─────────────────────────────────────────

def test_revogar_data_o_fim_e_a_linha_continua_legivel(cx, topico):
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste revogar"),
        afirmacao="Afirmação que vai ser revogada, para conferir que ela não some do registro.",
        tipo="preferencia",
    )
    porta.revogar(cx, r["id"], motivo="teste")
    cur = cx.cursor()
    cur.execute("SELECT status, valid_to, statement FROM lucien_claims WHERE id = %s", (r["id"],))
    linha = cur.fetchone()
    assert linha["status"] == "abandonada"
    assert linha["valid_to"] is not None, "revogar sem datar o fim perde o 'quando'"
    assert linha["statement"], "a afirmação continua legível — revogar não apaga"


def test_revogar_sem_motivo_e_recusado(cx, topico):
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste motivo"),
        afirmacao="Afirmação para conferir que revogação exige motivo declarado.",
        tipo="fato",
    )
    with pytest.raises(porta.PortaRecusou):
        porta.revogar(cx, r["id"], motivo="   ")


# ── as recusas ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "afirmacao,motivo",
    [
        ("curta", "abaixo do piso"),
        ("x" * 401, "acima do teto de 400"),
    ],
)
def test_recusa_por_tamanho(cx, topico, afirmacao, motivo):
    with pytest.raises(porta.PortaRecusou):
        porta.registrar(cx, topic_id=topico, assunto=_assunto("teste recusa"), afirmacao=afirmacao, tipo="fato")


def test_recusa_tipo_inexistente(cx, topico):
    with pytest.raises(porta.PortaRecusou):
        porta.registrar(
            cx, topic_id=topico, assunto=_assunto("teste recusa"),
            afirmacao="Afirmação válida em tudo menos no tipo, que não existe no schema.",
            tipo="lembrete",
        )


def test_recusa_assunto_vazio(cx, topico):
    with pytest.raises(porta.PortaRecusou):
        porta.registrar(
            cx, topic_id=topico, assunto="   ",
            afirmacao="Afirmação sem assunto — sem ele não há como agrupar nem deduplicar.",
            tipo="fato",
        )


def test_origem_inexistente_e_recusada_e_nao_inventada(cx, topico):
    """O ponto da porta inteira: ela não afrouxa a trava de origem. Pedir um
    `seq` que não existe tem que RECUSAR, nunca cair no fallback silencioso de
    'pega qualquer mensagem'."""
    with pytest.raises(porta.PortaRecusou):
        porta.registrar(
            cx, topic_id=topico, assunto=_assunto("teste recusa"),
            afirmacao="Afirmação apontando para uma origem que não existe no acervo.",
            tipo="fato", origem_seq=999_999_999,
        )


# ── a trava T5: o LUCIEN não mexe no que é do operador ────────────────────

def test_imagem_traz_created_by(cx, topico):
    """A T5 lê `created_by` de `_imagem`. Se a coluna sumir do SELECT, a trava
    passa a ler `None` e **nunca dispara** — falha silenciosa, e por isso este
    teste existe separado."""
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste t5"),
        afirmacao="Afirmação do operador que o LUCIEN não pode superar nem encerrar.",
        tipo="decisao",
    )
    img = store._imagem(cx, r["id"])
    assert img is not None
    assert "created_by" in img, "sem esta coluna a trava T5 vira no-op silencioso"
    assert img["created_by"] == "operador"


# ── o vetor de busca gerado ao gravar ─────────────────────────────────────
#
# Sem isto a perna de sentido só alcançava o fato recém-registrado na próxima
# passada do curador. O serviço de embedding é simulado: os testes não gastam
# chamada paga e não dependem de rede.

def _vetor_marcado(indice: int = 7) -> list[float]:
    from bot.search import embedder
    v = [0.0] * embedder.DIM
    v[indice] = 1.0
    return v


@pytest.fixture
def embedding_ok(monkeypatch):
    from bot.search import embedder
    chamadas: list[list[str]] = []

    def _falso(textos, *, cliente=None):
        chamadas.append(list(textos))
        return [_vetor_marcado() for _ in textos]

    monkeypatch.setattr(embedder, "embed", _falso)
    embedder._CACHE_PERGUNTA.clear()
    yield chamadas
    embedder._CACHE_PERGUNTA.clear()


@pytest.fixture
def embedding_quebrado(monkeypatch):
    from bot.search import embedder

    def _explode(textos, *, cliente=None):
        raise embedder.EmbeddingIndisponivel("serviço fora (simulado)")

    monkeypatch.setattr(embedder, "embed", _explode)


def _embedding_de(cx, claim_id):
    cur = cx.cursor()
    cur.execute("SELECT embedding IS NOT NULL AS tem, statement FROM lucien_claims"
                " WHERE id = %s", (claim_id,))
    return cur.fetchone()


def test_a_registro_com_embedding_ok_sai_com_vetor(cx, topico, embedding_ok):
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste vetor ok"),
        afirmacao="Afirmação que tem que sair da porta já com o vetor de busca preenchido.",
        tipo="fato",
    )
    v = porta.gerar_vetor(cx, r["id"], cliente=object())
    assert v["ok"] is True and v["gerados"] == 1, v
    assert _embedding_de(cx, r["id"])["tem"] is True


def test_b_embedding_quebrado_ainda_grava_e_diz_por_que(cx, topico, embedding_quebrado):
    """A lei: a afirmação não pode deixar de ser gravada porque o serviço de
    embedding estava fora. E a falha não pode ser muda."""
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste vetor quebrado"),
        afirmacao="Afirmação gravada com o serviço de embedding fora do ar, de propósito.",
        tipo="fato",
    )
    v = porta.gerar_vetor(cx, r["id"], cliente=object())
    assert v["ok"] is False
    assert "serviço fora" in v["motivo"], "o motivo tem que chegar a quem chamou"
    linha = _embedding_de(cx, r["id"])
    assert linha is not None, "a afirmação sobreviveu à falha do vetor"
    assert linha["tem"] is False
    # e a conexão continua utilizável (o savepoint desfez só o vetor)
    cx.cursor().execute("SELECT 1")


def test_b_cliente_que_nao_monta_tambem_nao_levanta(cx, topico, monkeypatch):
    """Sem OPENAI_API_KEY o cliente nem nasce — e a porta não pode levantar."""
    from bot.search import embedder

    def _sem_chave():
        raise embedder.EmbeddingIndisponivel("OPENAI_API_KEY não configurada")

    monkeypatch.setattr(embedder, "_client", _sem_chave)
    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste vetor sem chave"),
        afirmacao="Afirmação gravada sem chave do serviço de embedding configurada.",
        tipo="fato",
    )
    v = porta.gerar_vetor(cx, r["id"])
    assert v["ok"] is False and "OPENAI_API_KEY" in v["motivo"]
    assert _embedding_de(cx, r["id"]) is not None


class _Db:
    """O mínimo da interface que `buscar_estado` usa, sobre a conexão do teste
    — assim a busca enxerga a transação ainda não commitada."""

    def __init__(self, cx):
        self.cx = cx

    def query(self, sql, params=None):
        cur = self.cx.cursor()
        cur.execute(sql, params)
        return cur.fetchall()

    def scalar(self, sql, params=None):
        cur = self.cx.cursor()
        cur.execute(sql, params)
        linha = cur.fetchone()
        return None if linha is None else next(iter(linha.values()))


def test_c_afirmacao_gravada_e_achada_pela_perna_de_sentido(cx, topico, embedding_ok):
    from bot.lucien import consulta

    r = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste vetor sentido"),
        afirmacao="Afirmação que a busca por paráfrase tem que achar logo depois de gravada.",
        tipo="fato",
    )
    assert porta.gerar_vetor(cx, r["id"], cliente=object())["ok"]
    # pergunta sem nenhuma palavra em comum: só a perna de sentido pode achar
    res = consulta.buscar_estado(_Db(cx), "zzqx wvkj", topic_id=topico, janela_eco=0)
    assert res.sentido_ativo
    achada = [a for a in res.vigentes if str(a.id) == r["id"]]
    assert achada, "a afirmação recém-gravada não apareceu na busca"
    assert "sentido" in achada[0].pernas


def test_d_mira_so_na_afirmacao_pedida(cx, topico, embedding_ok):
    """Com fila de pendentes, 'as N primeiras sem vetor' vêm em ordem de UUID —
    a recém-gravada podia ficar de fora. O `ids` mira nela e só nela."""
    outra = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste vetor fila"),
        afirmacao="Afirmação pendente que NÃO foi pedida e deve continuar sem vetor.",
        tipo="fato",
    )
    alvo = porta.registrar(
        cx, topic_id=topico, assunto=_assunto("teste vetor alvo"),
        afirmacao="Afirmação pedida, a única que deve ganhar o vetor nesta chamada.",
        tipo="fato",
    )
    v = porta.gerar_vetor(cx, alvo["id"], cliente=object())
    assert v["gerados"] == 1
    assert _embedding_de(cx, alvo["id"])["tem"] is True
    assert _embedding_de(cx, outra["id"])["tem"] is False
    assert len(embedding_ok) == 1 and len(embedding_ok[0]) == 1


def _carregar_kobe_fato():
    import importlib.machinery
    import importlib.util
    caminho = str(RAIZ / "bot" / "bin" / "kobe-fato")
    loader = importlib.machinery.SourceFileLoader("kobe_fato_cli", caminho)
    spec = importlib.util.spec_from_loader("kobe_fato_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _CxSemCommit:
    """A conexão do teste, com `commit`/`close` neutralizados: o comando roda
    de ponta a ponta e a transação ainda é revertida no teardown."""

    def __init__(self, cx):
        self._cx = cx

    def commit(self):
        pass

    def close(self):
        pass

    def __getattr__(self, nome):
        return getattr(self._cx, nome)


def test_b_comando_sai_0_e_avisa_em_uma_linha(cx, topico, embedding_quebrado,
                                              monkeypatch, capsys):
    """O que o agente lê é a SAÍDA do comando. Gravou sem vetor → sai 0, diz
    que gravou, e diz numa linha por que a busca por sentido ainda não alcança."""
    from bot.lucien import boletim

    cur = cx.cursor()
    cur.execute("SELECT telegram_chat_id, telegram_thread_id FROM topics WHERE id = %s",
                (topico,))
    t = cur.fetchone()
    monkeypatch.setenv("KOBE_CHAT_ID", str(t["telegram_chat_id"]))
    monkeypatch.setenv("KOBE_THREAD_ID", str(t["telegram_thread_id"] or 0))
    monkeypatch.setenv("DATABASE_URL", "postgresql:///nao-usado-pelo-teste")
    monkeypatch.setattr(store, "conectar", lambda url: _CxSemCommit(cx))
    monkeypatch.setattr(boletim, "gerar", lambda *a, **k: None)

    cli = _carregar_kobe_fato()
    rc = cli.main(["registrar", "--tipo", "fato", "--assunto", _assunto("teste cli vetor"),
                   "Afirmação gravada pelo comando com o serviço de embedding fora do ar."])
    out = capsys.readouterr().out
    assert rc == 0, "falha do vetor NUNCA faz a porta sair com erro"
    assert "registrei na tabela de fatos" in out
    avisos = [l for l in out.splitlines() if "busca por sentido" in l]
    assert len(avisos) == 1, out
    assert "serviço fora" in avisos[0]
