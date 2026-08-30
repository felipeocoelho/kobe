#!/usr/bin/env python3
"""A fonte do Keyko — o relógio do coletor, e o que ele NÃO custa.

Highlander v3, F1, cenário **A6** do plano de testes.

Duas coisas precisam estar presas por teste aqui, e nenhuma delas é "o coletor
roda":

1. **Que a fonte nunca acorda ninguém.** Ela devolve sempre lista vazia. Se um
   dia alguém "melhorar" isso devolvendo um `Despertar`, o Kobe passaria a
   disparar um `claude -p` por dia pra copiar bytes de um arquivo pra outro —
   gastando cota, que é o recurso escasso desta campanha, na tarefa mais burra
   do sistema. É uma regressão silenciosa e cara, e por isso tem teste próprio.

2. **Que a fonte nunca levanta.** O Keyko é single-threaded: uma exceção que
   escape aqui é tratada pelo loop, mas depender disso é deixar o tratamento pra
   quem não tem contexto pra tratar. Um coletor que morre calado é exatamente a
   lacuna L4 de volta.

O que este arquivo NÃO prova, e é honesto dizer: que a coleta **acontece toda
madrugada**. Isso exigiria esperar até a madrugada. O agendamento se verifica por
inspeção da configuração; o que se testa aqui é a função, disparada à mão — que é
o que o próprio briefing manda (§9.5, L4).

    .venv/bin/python -m pytest -q tests/test_transcript_source.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.transcripts import collector as col  # noqa: E402
from bot.transcripts import source as src  # noqa: E402
from bot.transcripts import state as st  # noqa: E402


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Origem e destino próprios, e o coletor ligado só dentro do teste."""
    origem = tmp_path / "projects" / "-proj"
    origem.mkdir(parents=True)
    destino = tmp_path / "colhidos"
    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("TRANSCRIPT_SOURCE_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("TRANSCRIPT_DEST", str(destino))
    monkeypatch.delenv("TRANSCRIPT_ALERT_CHAT_ID", raising=False)
    monkeypatch.delenv("WORK_CATALOG_ENABLED", raising=False)
    monkeypatch.setenv("WORK_CATALOG_ENABLED", "false")
    return origem, destino


def _transcript(origem: Path, linhas: int = 3) -> Path:
    p = origem / f"{uuid.uuid4()}.jsonl"
    p.write_bytes(b"".join(
        b'{"type":"assistant","uuid":"u%d"}\n' % i for i in range(linhas)
    ))
    return p


def _fonte(tmp_path) -> src.TranscriptsSource:
    return src.TranscriptsSource(kobe_home=tmp_path, bot_token="token-de-teste")


# ══════════════════════════════════════════════════════════════════════════
# O custo: zero de cota
# ══════════════════════════════════════════════════════════════════════════

def test_a_fonte_nunca_devolve_despertar(ambiente, tmp_path):
    """**A garantia de custo da fase.**

    Um `Despertar` devolvido aqui viraria um `claude -p` por dia. O protocolo do
    Keyko prevê explicitamente que uma fonte faça só trabalho colateral e retorne
    lista vazia — é o encaixe certo, não um contorno.
    """
    origem, destino = ambiente
    _transcript(origem)
    assert _fonte(tmp_path).tick() == []


def test_a_fonte_coletou_de_verdade_no_proprio_tick(ambiente, tmp_path):
    """Devolver lista vazia não pode virar "não fez nada"."""
    origem, destino = ambiente
    t = _transcript(origem, linhas=5)

    _fonte(tmp_path).tick()

    colhido = destino / "-proj" / t.name
    assert colhido.is_file()
    assert colhido.read_bytes() == t.read_bytes()


def test_com_a_chave_desligada_o_tick_nao_faz_nada(ambiente, tmp_path, monkeypatch):
    origem, destino = ambiente
    _transcript(origem)
    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "false")

    assert _fonte(tmp_path).tick() == []
    assert not destino.exists()


def test_com_a_chave_desligada_a_fonte_nem_e_construida(ambiente, tmp_path, monkeypatch):
    """`build` devolve `None`, e o registro do Keyko fica limpo.

    Uma fonte registrada que não faz nada aparece no log de inicialização como se
    estivesse trabalhando — e "quem o Keyko está observando" deixa de ser verdade.
    """
    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "false")
    assert src.build(kobe_home=tmp_path, bot_token="x") is None

    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "true")
    assert src.build(kobe_home=tmp_path, bot_token="x") is not None


# ══════════════════════════════════════════════════════════════════════════
# A fonte não pode levantar — nem morrer calada
# ══════════════════════════════════════════════════════════════════════════

def test_coleta_com_erro_nao_levanta(ambiente, tmp_path, monkeypatch):
    origem, destino = ambiente

    def explode(**_kw):
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(col, "collect_once", explode)
    assert _fonte(tmp_path).tick() == []      # não propagou


def test_coleta_ja_em_andamento_nao_levanta(ambiente, tmp_path):
    """A trava é não-bloqueante: o tick que cai numa coleta em curso desiste.

    Relógio que espera acumula fila — bastaria uma passada lenta pra o disparo
    seguinte ficar pendurado, e o seguinte atrás dele.
    """
    origem, destino = ambiente
    _transcript(origem)
    with st.exclusive_lock(destino):
        assert _fonte(tmp_path).tick() == []


def test_falha_no_aviso_nao_impede_a_coleta(ambiente, tmp_path, monkeypatch):
    """O aviso é acessório; a coleta é o que salva dado perecível.

    Se avisar quebrar, colher tem que acontecer assim mesmo — a ordem de
    importância entre os dois não é negociável.
    """
    origem, destino = ambiente
    t = _transcript(origem)

    monkeypatch.setattr(
        col, "staleness_warning",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("aviso quebrado")),
    )
    _fonte(tmp_path).tick()
    assert (destino / "-proj" / t.name).is_file()


# ══════════════════════════════════════════════════════════════════════════
# A6 — o aviso de relógio
# ══════════════════════════════════════════════════════════════════════════

def test_envelhecido_avisa_uma_vez_por_dia(ambiente, tmp_path, monkeypatch):
    """Um alerta que se repete a cada tick vira ruído — e ruído é ignorado, que
    é o mesmo destino do silêncio que ele veio combater."""
    origem, destino = ambiente
    _transcript(origem)
    fonte = _fonte(tmp_path)

    enviados: list[str] = []
    monkeypatch.setattr(fonte, "_notificar", lambda t: enviados.append(t))

    # 1ª passada: nunca coletou ⇒ envelhecido ⇒ avisa
    fonte.tick()
    assert len(enviados) == 1
    assert "NUNCA" in enviados[0]

    # força o estado a parecer velho de novo; o anti-repetição segura
    estado = st.load(destino)
    estado["last_success_at"] = "2026-08-20T03:00:00+00:00"
    st.save(destino, estado)
    fonte.tick()
    assert len(enviados) == 1, "o mesmo aviso foi repetido no mesmo dia"


def test_a_idade_e_medida_ANTES_de_coletar(ambiente, tmp_path, monkeypatch):
    """A ordem é o ponto.

    Depois de coletar, a marca está sempre fresca e não haveria nada a avisar —
    o aviso nunca dispararia. É olhando antes que se enxerga o buraco, e o
    momento em que se enxerga é justamente o instante em que ele terminou.
    """
    origem, destino = ambiente
    _transcript(origem)
    fonte = _fonte(tmp_path)
    ordem: list[str] = []

    monkeypatch.setattr(fonte, "_notificar", lambda t: ordem.append("avisou"))
    original = col.collect_once
    monkeypatch.setattr(
        col, "collect_once",
        lambda **kw: (ordem.append("coletou"), original(**kw))[1],
    )

    fonte.tick()
    assert ordem == ["avisou", "coletou"]


def test_sem_destino_declarado_o_aviso_fica_no_log(ambiente, tmp_path, monkeypatch):
    """**Não adivinhar para qual conversa mandar.**

    Uma mensagem de saúde do sistema caindo num tópico qualquer é pior que não
    mandar mensagem nenhuma. Sem `TRANSCRIPT_ALERT_CHAT_ID`, fica no log — e o
    terceiro degrau da mitigação (o dispatcher do Coder) cobre a visibilidade.
    """
    origem, destino = ambiente
    monkeypatch.delenv("TRANSCRIPT_ALERT_CHAT_ID", raising=False)

    chamou: list[tuple] = []
    monkeypatch.setattr(src.subprocess, "run",
                        lambda *a, **k: chamou.append((a, k)))
    _fonte(tmp_path)._notificar("qualquer coisa")
    assert chamou == []


def test_com_destino_declarado_o_aviso_chama_o_kobe_notify(ambiente, tmp_path, monkeypatch):
    origem, destino = ambiente
    monkeypatch.setenv("TRANSCRIPT_ALERT_CHAT_ID", "-100999")
    monkeypatch.setenv("TRANSCRIPT_ALERT_THREAD_ID", "7")

    helper = tmp_path / "bot" / "bin" / "kobe-notify"
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    chamadas: list[tuple] = []
    monkeypatch.setattr(src.subprocess, "run",
                        lambda *a, **k: chamadas.append((a, k)))

    _fonte(tmp_path)._notificar("⚠️ relógio parado")

    assert len(chamadas) == 1
    args, kwargs = chamadas[0]
    assert args[0] == [str(helper), "⚠️ relógio parado"]
    assert kwargs["env"]["KOBE_CHAT_ID"] == "-100999"
    assert kwargs["env"]["KOBE_THREAD_ID"] == "7"
    assert kwargs["env"]["KOBE_TELEGRAM_BOT_TOKEN"] == "token-de-teste"


# ══════════════════════════════════════════════════════════════════════════
# Cadência
# ══════════════════════════════════════════════════════════════════════════

def test_intervalo_padrao_e_diario_e_configuravel(monkeypatch):
    monkeypatch.delenv("TRANSCRIPT_COLLECT_INTERVAL_S", raising=False)
    assert src._intervalo() == src.INTERVALO_PADRAO_S == 24 * 60 * 60

    monkeypatch.setenv("TRANSCRIPT_COLLECT_INTERVAL_S", "3600")
    assert src._intervalo() == 3600


@pytest.mark.parametrize("valor", ["0", "-5", "banana", "0.1"])
def test_intervalo_torto_cai_num_piso(monkeypatch, valor):
    """Um valor torto no `.env` não pode transformar o coletor num laço apertado
    lendo dezenas de MB de cabeçalho por segundo."""
    monkeypatch.setenv("TRANSCRIPT_COLLECT_INTERVAL_S", valor)
    assert src._intervalo() >= 60.0


def test_a_fonte_satisfaz_o_protocolo_do_keyko(tmp_path):
    """`Source` é `runtime_checkable` — a conformidade é verificável, então
    verificá-la é barato e pega uma renomeação distraída."""
    from bot.keyko.models import Source

    fonte = _fonte(tmp_path)
    assert isinstance(fonte, Source)
    assert fonte.nome == "transcripts"
    assert fonte.intervalo_s > 0
