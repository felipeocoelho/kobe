#!/usr/bin/env python3
"""A divisão do lote — o que substituiu o descarte por posição.

O DEFEITO QUE ISTO CORRIGE
--------------------------
A primeira versão do teto cortava **as últimas afirmações da lista** e seguia.
Medido no piloto de 5 lotes (30/08/2026): o teto bateu em **5 de 5** e levou 20
afirmações embora — escolhidas por **posição**, não por qualidade.

É o pior comportamento possível numa trava: ela não escolhe, e o que se perde é
invisível. Palavra do operador: *"eu não queria que nada que fosse relevante
ficasse de fora"*.

O comportamento novo, e ele só é natural porque o cursor não tinha andado:
**nada é gravado, o lote é partido ao meio, e cada metade vira uma rodada
própria.** A divisão para no piso de 5 mensagens — abaixo disso não é lote
grande, é degeneração do modelo, e aí a recusa é barulhenta.

Nenhum teste aqui chama modelo nem banco: a divisão é aritmética sobre o lote.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_lucien_divisao.py -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot import lucien as cfg  # noqa: E402
from bot.lucien import worker  # noqa: E402
from bot.lucien.models import Lote, Mensagem  # noqa: E402


def _lote(n: int) -> Lote:
    msgs = [
        Mensagem(seq=1000 + i, id=f"{i:08d}-0000-0000-0000-000000000000",
                 role="user" if i % 2 else "assistant",
                 created_at=datetime(2026, 7, 14, 10, i % 60, tzinfo=timezone.utc),
                 content=f"mensagem {i}", audio=False)
        for i in range(n)
    ]
    return Lote(topic_id="t", topico_nome="T", mensagens=msgs)


def test_o_lote_parte_ao_meio():
    partes = worker._partir(_lote(40))
    assert partes is not None
    assert [len(p.mensagens) for p in partes] == [20, 20]


def test_lote_impar_nao_perde_mensagem():
    partes = worker._partir(_lote(35))
    assert sum(len(p.mensagens) for p in partes) == 35


def test_as_metades_sao_CONTIGUAS_e_em_ordem():
    """Se elas não se encaixassem, o cursor de uma passaria por cima da outra —
    e o buraco (ou a sobreposição) seria permanente."""
    a, b = worker._partir(_lote(40))
    assert a.ate_seq + 1 == b.de_seq
    assert a.de_seq < a.ate_seq < b.de_seq < b.ate_seq


def test_a_ordem_cronologica_e_preservada():
    """Superação só faz sentido lida na ordem em que aconteceu. Uma metade fora
    de ordem registraria a decisão de julho como se tivesse superado a de
    agosto — o inverso do que a fase conserta."""
    a, b = worker._partir(_lote(40))
    for parte in (a, b):
        seqs = [m.seq for m in parte.mensagens]
        assert seqs == sorted(seqs)


@pytest.mark.parametrize("n", [1, 2, 5, 9])
def test_abaixo_do_dobro_do_piso_NAO_divide(n):
    """O piso é de 5 mensagens por metade, então 10 é o menor lote divisível.
    Dividir abaixo disso produziria lotes sem contexto suficiente para o modelo
    julgar o que sustenta o quê."""
    assert worker._partir(_lote(n)) is None


def test_exatamente_no_dobro_do_piso_divide():
    partes = worker._partir(_lote(10))
    assert [len(p.mensagens) for p in partes] == [5, 5]


def test_o_estado_vigente_atravessa_a_divisao():
    """Ele não depende do recorte, e remontá-lo por metade custaria uma consulta
    a mais sem mudar uma linha do resultado."""
    lote = _lote(20)
    lote.estado = {"E1": {"id": "x", "subject": "s", "statement": "t",
                          "kind": "decision", "valid_from": None}}
    for parte in worker._partir(lote):
        assert parte.estado == lote.estado


def test_a_divisao_termina(monkeypatch):
    """40 → 20 → 10 → 5, e para. Sem o piso, a recursão desceria até lotes de
    uma mensagem; sem o teto de profundidade, um modelo em laço viraria uma
    árvore de chamadas — e cada nó dela custa cota."""
    n, passos = 40, 0
    while worker._partir(_lote(n)) is not None:
        n //= 2
        passos += 1
        assert passos <= 10, "a divisão não converge"
    assert passos == 3
    assert passos <= cfg.profundidade_maxima() + 1


def test_o_piso_e_o_teto_de_profundidade_saem_do_env(monkeypatch):
    monkeypatch.setenv("LUCIEN_LOTE_PISO", "8")
    monkeypatch.setenv("LUCIEN_DIVISOES_MAX", "1")
    assert cfg.lote_piso() == 8 and cfg.profundidade_maxima() == 1
    assert worker._partir(_lote(15)) is None, "o piso novo não foi respeitado"
    assert worker._partir(_lote(16)) is not None


def test_o_aviso_de_degeneracao_nao_derruba_a_rodada(monkeypatch, tmp_path):
    """Falhar em avisar nunca desfaz o que já foi gravado."""
    monkeypatch.delenv("LUCIEN_ALERT_CHAT_ID", raising=False)
    worker._gritar(str(tmp_path), "um motivo qualquer")  # sem destino: só loga

    monkeypatch.setenv("LUCIEN_ALERT_CHAT_ID", "-100123")
    worker._gritar("/caminho/que/nao/existe", "outro motivo")  # helper ausente


def test_o_aviso_so_sai_com_destino_DECLARADO(monkeypatch, tmp_path):
    """Mesma razão do coletor da F1: escolher um tópico por conta própria faria
    uma mensagem de saúde do sistema cair numa conversa qualquer, o que é pior
    que não mandar."""
    import subprocess

    chamadas = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: chamadas.append(a))
    helper = tmp_path / "bot" / "bin"
    helper.mkdir(parents=True)
    (helper / "kobe-notify").write_text("#!/bin/sh\n")

    monkeypatch.delenv("LUCIEN_ALERT_CHAT_ID", raising=False)
    worker._gritar(str(tmp_path), "motivo")
    assert not chamadas, "avisou sem destino declarado"

    monkeypatch.setenv("LUCIEN_ALERT_CHAT_ID", "-100123")
    worker._gritar(str(tmp_path), "motivo")
    assert len(chamadas) == 1


# ── O freio de falha repetida ─────────────────────────────────────────────


def test_a_varredura_para_depois_de_N_falhas_seguidas(monkeypatch):
    """O defeito que o uso real ensinou, em 30/08/2026.

    Um limite transitório do modelo derrubou **70 lotes seguidos em 4 minutos**,
    cada um falhando em ~3,5 s. O desenho segurou o que importava — nada foi
    gravado e o cursor não andou —, mas o laço **queimou o orçamento inteiro de
    lotes** contra uma falha que não ia se curar na iteração seguinte, três
    segundos depois. As vagas gastas eram as que a retomada precisaria.
    """
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setenv("LUCIEN_FALHAS_SEGUIDAS", "3")
    monkeypatch.setattr(reconstrucao.time, "sleep", lambda *_: None)
    monkeypatch.setattr(reconstrucao, "_proximo_topico", lambda cx: "t")
    monkeypatch.setattr(reconstrucao, "_falta", lambda cx, t: True)
    monkeypatch.setattr(reconstrucao.store, "conectar",
                        lambda url: type("C", (), {"close": lambda s: None})())

    tentativas = []

    def _sempre_falha(**kw):
        tentativas.append(1)
        return ResultadoDaRodada(erro="a CLI saiu com código 1")

    monkeypatch.setattr(reconstrucao.worker, "uma_rodada", _sempre_falha)
    reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=90, pausa_s=0)
    assert len(tentativas) == 3, (
        f"insistiu {len(tentativas)} vezes contra a mesma falha — o freio não pegou"
    )


def test_falha_ISOLADA_nao_interrompe_a_varredura(monkeypatch):
    """Falha esporádica é ruído. Parar nela transformaria um soluço de rede numa
    varredura pela metade."""
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setenv("LUCIEN_FALHAS_SEGUIDAS", "3")
    monkeypatch.setattr(reconstrucao.time, "sleep", lambda *_: None)
    monkeypatch.setattr(reconstrucao, "_proximo_topico", lambda cx: "t")
    monkeypatch.setattr(reconstrucao, "_falta", lambda cx, t: True)
    monkeypatch.setattr(reconstrucao.store, "conectar",
                        lambda url: type("C", (), {"close": lambda s: None})())

    n = {"i": 0}

    def _falha_alternada(**kw):
        n["i"] += 1
        if n["i"] % 2 == 0:
            return ResultadoDaRodada(erro="soluço")
        return ResultadoDaRodada(criadas=1)

    monkeypatch.setattr(reconstrucao.worker, "uma_rodada", _falha_alternada)
    rs = reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=10, pausa_s=0)
    assert len(rs) == 10, "uma falha isolada interrompeu a varredura"


def test_o_erro_da_CLI_carrega_o_stdout_quando_o_stderr_vem_vazio(monkeypatch):
    """Na rajada de falhas, a CLI saiu com código 1 e **stderr vazio**: a
    mensagem dizia só "(sem stderr)", que não diagnostica nada. O motivo vinha no
    stdout, no envelope de erro da própria CLI."""
    import subprocess

    from bot.lucien import brain

    class _Proc:
        returncode = 1
        stdout = b'{"type":"error","subtype":"rate_limit","is_error":true}'
        stderr = b""

    monkeypatch.setattr(brain.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(brain.CerebroIndisponivel) as exc:
        brain.chamar("oi", kobe_home="/tmp")
    assert "rate_limit" in str(exc.value), "o diagnóstico se perdeu de novo"
