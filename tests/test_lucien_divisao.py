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


# ── A varredura que morria calada ─────────────────────────────────────────
#
# O freio acima está certo, e o defeito era outro: ela parava e **não avisava**.
# Aconteceu duas vezes em 31/08/2026 (às 11:28 no lote 48, e às 14:32 no lote
# 29) e nas duas o operador só soube porque perguntou outra coisa. Uma varredura
# de horas que para em silêncio é indistinguível de uma que está rodando.
#
# A linha que estes testes defendem é a distinção: **parada normal é muda,
# parada anormal fala**. Um alarme que dispara ao terminar o trabalho é ruído, e
# ruído tem o mesmo destino do silêncio — ser ignorado.


@pytest.fixture
def varredura(monkeypatch):
    """A varredura com o mundo externo desligado, e os avisos capturados."""
    from bot.lucien import reconstrucao

    avisos = []
    monkeypatch.setattr(reconstrucao.time, "sleep", lambda *_: None)
    monkeypatch.setattr(reconstrucao.store, "conectar",
                        lambda url: type("C", (), {"close": lambda s: None})())
    monkeypatch.setattr(reconstrucao.aviso, "avisar",
                        lambda home, motivo: avisos.append(motivo) or True)
    # O "quanto falta" do aviso é opcional e tem caminho próprio de falha —
    # aqui ele sai de cena para que os testes falem só sobre o gatilho.
    monkeypatch.setattr(reconstrucao, "planejar",
                        lambda cx, topic_id=None: reconstrucao.Plano())
    monkeypatch.setattr(reconstrucao, "_proximo_topico", lambda cx: "t")
    monkeypatch.setattr(reconstrucao, "_falta", lambda cx, t: True)
    return avisos


def test_o_freio_de_falhas_seguidas_AVISA_o_operador(monkeypatch, varredura):
    """O caso dos dois incidentes de 31/08. Parar está certo; parar calado, não."""
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setenv("LUCIEN_FALHAS_SEGUIDAS", "3")
    monkeypatch.setattr(reconstrucao.worker, "uma_rodada",
                        lambda **kw: ResultadoDaRodada(erro="rate_limit"))

    reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=90, pausa_s=0)

    assert len(varredura) == 1, "a varredura parou pelo freio e não avisou ninguém"
    texto = varredura[0]
    assert "PAROU" in texto
    assert "3 falhas seguidas" in texto, "o aviso não diz POR QUE parou"
    assert "rate_limit" in texto, "o aviso não carrega o erro que causou a parada"
    assert "3 lote(s)" in texto, "o aviso não diz quantos lotes foram feitos"
    assert "RETOMÁVEL" in texto, "o aviso não diz que rodar de novo continua daqui"


def test_bater_o_teto_de_lotes_NAO_avisa(monkeypatch, varredura):
    """Terminar o trabalho não é ocorrência. Alarme aqui viraria ruído diário —
    a varredura roda com teto por desenho, então ela bate no teto quase sempre."""
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setattr(reconstrucao.worker, "uma_rodada",
                        lambda **kw: ResultadoDaRodada(criadas=1))

    rs = reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=4, pausa_s=0)

    assert len(rs) == 4
    assert varredura == [], "avisou numa parada normal — isso é ruído"


def test_backlog_esgotado_NAO_avisa(monkeypatch, varredura):
    """Acabar o passado a reconstruir é o objetivo da coisa, não um incidente."""
    from bot.lucien import reconstrucao

    monkeypatch.setattr(reconstrucao, "_proximo_topico", lambda cx: None)

    rs = reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=10, pausa_s=0)

    assert rs == []
    assert varredura == [], "avisou porque o trabalho acabou"


def test_falha_ISOLADA_nao_avisa_nem_interrompe(monkeypatch, varredura):
    """Soluço de rede não é sintoma. Avisar a cada um deles ensinaria o operador
    a ignorar o canal — e aí o aviso que importa também é ignorado."""
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setenv("LUCIEN_FALHAS_SEGUIDAS", "3")
    n = {"i": 0}

    def _alternada(**kw):
        n["i"] += 1
        return ResultadoDaRodada(erro="soluço") if n["i"] % 2 == 0 else ResultadoDaRodada(criadas=1)

    monkeypatch.setattr(reconstrucao.worker, "uma_rodada", _alternada)

    rs = reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=10, pausa_s=0)

    assert len(rs) == 10
    assert varredura == []


def test_o_cadeado_tomado_AVISA(monkeypatch, varredura):
    """Outra rodada segurando o cadeado interrompe a passada por causa externa.

    Não é falha e não é fim de trabalho: é uma varredura que fez menos do que
    foi mandada fazer, e quem mandou precisa saber disso.
    """
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setattr(reconstrucao.worker, "uma_rodada",
                        lambda **kw: ResultadoDaRodada(erro="outra rodada já está acontecendo"))

    reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=10, pausa_s=0)

    assert len(varredura) == 1
    assert "cadeado" in varredura[0]


def test_excecao_nao_tratada_AVISA_e_continua_subindo(monkeypatch, varredura):
    """O silêncio mais completo dos três: quem rodou de um shell já fechado não
    vê nem o traceback. O aviso sai, e a exceção continua sendo exceção — engolir
    o erro para poder avisar trocaria um defeito por outro."""
    from bot.lucien import reconstrucao

    def _explode(**kw):
        raise RuntimeError("o banco sumiu no meio")

    monkeypatch.setattr(reconstrucao.worker, "uma_rodada", _explode)

    with pytest.raises(RuntimeError):
        reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=10, pausa_s=0)

    assert len(varredura) == 1
    assert "erro não tratado" in varredura[0]
    assert "o banco sumiu no meio" in varredura[0]


def test_o_aviso_sai_mesmo_se_o_quanto_falta_estourar(monkeypatch, varredura):
    """O "quanto falta" é enfeite útil, não pré-requisito.

    Um aviso de parada que estoura ao montar a própria mensagem seria uma segunda
    falha em cima da primeira — e a que o operador precisa ver é a primeira.
    """
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setenv("LUCIEN_FALHAS_SEGUIDAS", "1")

    def _sem_banco(cx, topic_id=None):
        raise RuntimeError("conexão morta")

    monkeypatch.setattr(reconstrucao, "planejar", _sem_banco)
    monkeypatch.setattr(reconstrucao.worker, "uma_rodada",
                        lambda **kw: ResultadoDaRodada(erro="timeout"))

    reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=5, pausa_s=0)

    assert len(varredura) == 1, "o aviso morreu junto com a medição do que falta"
    assert "PAROU" in varredura[0]
    assert "Falta" not in varredura[0], "prometeu um número que não conseguiu medir"


# ── O canal do aviso: destino declarado, e a falta dele é caso esperado ────


def test_o_aviso_nao_despeja_o_envelope_de_erro_inteiro_no_chat(monkeypatch, varredura):
    """Achado no smoke de 31/08/2026, rodando a varredura de verdade.

    O erro da CLI do modelo vem com o envelope JSON junto — 700 caracteres de
    `usage`, `session_id` e `cache_creation` para dizer *"modelo não
    reconhecido"*. O aviso é uma mensagem de chat: despejar isso ali é o mesmo
    que não avisar, porque ninguém lê. O motivo inteiro continua no log, que é
    onde trace pertence.
    """
    from bot.lucien import reconstrucao
    from bot.lucien.models import ResultadoDaRodada

    monkeypatch.setenv("LUCIEN_FALHAS_SEGUIDAS", "1")
    envelope = "unrecognized_model " + ("x" * 900)
    monkeypatch.setattr(reconstrucao.worker, "uma_rodada",
                        lambda **kw: ResultadoDaRodada(erro=envelope))

    reconstrucao.rodar(conninfo="x", kobe_home="/tmp", max_lotes=5, pausa_s=0)

    texto = varredura[0]
    assert len(texto) < 400, f"o aviso saiu com {len(texto)} caracteres — é um log, não um aviso"
    assert "unrecognized_model" in texto, "cortou tanto que perdeu o diagnóstico"
    assert "…" in texto, "cortou sem dizer que cortou"


def test_sem_destino_declarado_o_aviso_vai_so_pro_log(monkeypatch, tmp_path):
    """Sem `LUCIEN_ALERT_CHAT_ID` não há para onde mandar — e isso NÃO pode virar
    uma segunda fonte de crash em cima da falha que se queria relatar.

    Escolher um tópico por conta própria seria pior que não mandar: a mensagem
    de saúde do sistema cairia numa conversa qualquer, e quem devia receber
    continuaria sem saber.
    """
    from bot.lucien import aviso

    monkeypatch.delenv("LUCIEN_ALERT_CHAT_ID", raising=False)
    chamadas = []
    monkeypatch.setattr(aviso.subprocess, "run", lambda *a, **k: chamadas.append(a))

    assert aviso.avisar(str(tmp_path), "parei") is False
    assert chamadas == [], "tentou mandar sem destino declarado"


def test_o_telegram_fora_do_ar_nao_derruba_quem_chamou(monkeypatch, tmp_path):
    """O que foi gravado já está gravado."""
    from bot.lucien import aviso

    monkeypatch.setenv("LUCIEN_ALERT_CHAT_ID", "-100123")
    (tmp_path / "bot" / "bin").mkdir(parents=True)
    (tmp_path / "bot" / "bin" / "kobe-notify").write_text("#!/bin/sh\n")

    def _estoura(*a, **k):
        raise OSError("rede fora")

    monkeypatch.setattr(aviso.subprocess, "run", _estoura)

    assert aviso.avisar(str(tmp_path), "parei") is False
