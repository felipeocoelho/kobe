#!/usr/bin/env python3
"""A fonte do LUCIEN no Keyko — e as três coisas que ela nunca pode fazer.

O KEYKO É SINGLE-THREADED, E ISSO É O CONTEXTO DE TODO TESTE AQUI
------------------------------------------------------------------
O mesmo laço que roda LUCIEN roda os **Alertas** — a peça em que atraso é falha
que o operador vê ("você não me lembrou"). Daí as três proibições, e cada teste
abaixo é uma delas:

1. **Nunca devolver `Despertar`.** Não é economia de cota: o despertar acorda um
   `claude -p` que escreveria ele mesmo no registro, e a F3 inteira existe para
   que o modelo proponha e o código decida.
2. **Nunca bloquear o `tick()`.** Uma chamada de modelo leva dezenas de
   segundos. O `tick()` faz só a pergunta barata e dispara um processo.
3. **Nunca levantar.** Fonte bugada não derruba daemon.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_lucien_source.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot import lucien as cfg  # noqa: E402
from bot.lucien import source  # noqa: E402


@pytest.fixture(autouse=True)
def _sem_heranca(monkeypatch):
    """O ambiente da sessão não pode decidir o resultado do teste."""
    for k in ("LUCIEN_ENABLED", "LUCIEN_BATCH_MIN", "LUCIEN_MAX_AGE_S",
              "LUCIEN_MODEL", "LUCIEN_MODEL_RECONSTRUCAO"):
        monkeypatch.delenv(k, raising=False)


class _CxFalso:
    def __init__(self, erro=None):
        self.erro = erro
        self.fechada = False

    def close(self):
        self.fechada = True


# ── A chave ───────────────────────────────────────────────────────────────


def test_desligada_a_fonte_nem_e_construida(monkeypatch):
    """Devolver `None` mantém o registro do Keyko honesto: uma fonte registrada
    que não faz nada aparece no log de inicialização como se estivesse
    trabalhando, e "quem o Keyko está observando" deixa de ser verdade."""
    monkeypatch.setenv("LUCIEN_ENABLED", "false")
    assert source.build(kobe_home=RAIZ, bot_token="x") is None


def test_ligada_a_fonte_existe_e_se_chama_lucien(monkeypatch):
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    s = source.build(kobe_home=RAIZ, bot_token="x")
    assert s is not None and s.nome == "lucien"


def test_a_chave_e_desligada_no_env_de_exemplo():
    """*"Nasce atrás de chave, desligada"* — regra dura da fase. Ligar é ato do
    operador, separado de publicar o código."""
    env = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "LUCIEN_ENABLED=false" in env


def test_a_chave_desligada_faz_o_tick_nao_tocar_no_banco(monkeypatch):
    """Se a fonte for registrada por engano com a chave off (um `.env` mudado
    sem restart, por exemplo), ela ainda tem que ser inerte."""
    monkeypatch.setenv("LUCIEN_ENABLED", "false")
    chamou = []
    s = source.LucienSource(kobe_home=RAIZ, db_factory=lambda: chamou.append(1))
    assert s.tick() == []
    assert not chamou, "tick com a chave off abriu conexão com o banco"


# ── As três proibições ────────────────────────────────────────────────────


def test_o_tick_NUNCA_devolve_despertar(monkeypatch):
    """A proibição 1, e é decisão de arquitetura: o despertar acordaria um
    modelo com a caneta na mão."""
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    disparos = []
    s = source.LucienSource(kobe_home=RAIZ, db_factory=_CxFalso)
    monkeypatch.setattr(s, "_disparar", lambda: disparos.append(1))
    monkeypatch.setattr("bot.lucien.worker.cota_disponivel", lambda cx: True)
    monkeypatch.setattr(
        "bot.lucien.worker.escolher_topico",
        lambda cx, **k: {"topic_id": "x", "topico": "T", "pendentes": 20},
    )
    assert s.tick() == []
    assert disparos == [1], "havia lote devido e o worker não foi disparado"


def test_o_tick_nao_dispara_se_ja_ha_worker_em_voo(monkeypatch):
    """O cadeado do banco já protegeria a escrita, mas disparar processos que
    morrem no cadeado gasta recurso à toa."""
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    s = source.LucienSource(kobe_home=RAIZ, db_factory=_CxFalso)

    class _Vivo:
        def poll(self):
            return None

    s._em_voo = _Vivo()
    chamou = []
    monkeypatch.setattr(s, "_db_factory", lambda: chamou.append(1))
    assert s.tick() == []
    assert not chamou


def test_o_tick_nao_levanta_quando_o_banco_esta_fora(monkeypatch):
    """A proibição 3. Fonte bugada não derruba daemon — e o daemon é o mesmo dos
    Alertas."""
    monkeypatch.setenv("LUCIEN_ENABLED", "true")

    def _explodir():
        raise ConnectionError("banco fora")

    s = source.LucienSource(kobe_home=RAIZ, db_factory=_explodir)
    assert s.tick() == []


def test_o_tick_nao_levanta_quando_a_consulta_falha(monkeypatch):
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    s = source.LucienSource(kobe_home=RAIZ, db_factory=_CxFalso)
    monkeypatch.setattr(
        "bot.lucien.worker.cota_disponivel",
        lambda cx: (_ for _ in ()).throw(RuntimeError("tabela sumiu")),
    )
    assert s.tick() == []


def test_a_conexao_e_fechada_mesmo_quando_da_erro(monkeypatch):
    """Processo de vida curta pode vazar; um daemon que roda por semanas, não."""
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    cx = _CxFalso()
    s = source.LucienSource(kobe_home=RAIZ, db_factory=lambda: cx)
    monkeypatch.setattr(
        "bot.lucien.worker.cota_disponivel",
        lambda c: (_ for _ in ()).throw(RuntimeError("erro")),
    )
    s.tick()
    assert cx.fechada


def test_sem_lote_devido_nao_dispara_nada(monkeypatch):
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    disparos = []
    s = source.LucienSource(kobe_home=RAIZ, db_factory=_CxFalso)
    monkeypatch.setattr(s, "_disparar", lambda: disparos.append(1))
    monkeypatch.setattr("bot.lucien.worker.cota_disponivel", lambda cx: True)
    monkeypatch.setattr("bot.lucien.worker.escolher_topico", lambda cx, **k: None)
    assert s.tick() == [] and not disparos


def test_cota_estourada_nao_dispara(monkeypatch):
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    disparos = []
    s = source.LucienSource(kobe_home=RAIZ, db_factory=_CxFalso)
    monkeypatch.setattr(s, "_disparar", lambda: disparos.append(1))
    monkeypatch.setattr("bot.lucien.worker.cota_disponivel", lambda cx: False)
    assert s.tick() == [] and not disparos


# ── O registro no Keyko ───────────────────────────────────────────────────


def test_o_keyko_registra_lucien_quando_ligada(monkeypatch):
    monkeypatch.setenv("LUCIEN_ENABLED", "true")
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "false")
    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "false")
    from bot.keyko.registry import build_sources

    nomes = [s.nome for s in build_sources(kobe_home=RAIZ, bot_token="x")]
    assert "lucien" in nomes


def test_o_keyko_NAO_registra_lucien_quando_desligada(monkeypatch):
    monkeypatch.setenv("LUCIEN_ENABLED", "false")
    monkeypatch.setenv("SEARCH_INDEX_ENABLED", "false")
    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "false")
    from bot.keyko.registry import build_sources

    nomes = [s.nome for s in build_sources(kobe_home=RAIZ, bot_token="x")]
    assert "lucien" not in nomes


# ── A configuração ────────────────────────────────────────────────────────


def test_o_modelo_sai_do_env_e_vazio_significa_o_padrao(monkeypatch):
    """O Kobe não fixa modelo em código. Vazio = o padrão da CLI."""
    assert cfg.modelo() == ""
    assert cfg.modelo(reconstrucao=True) == ""
    monkeypatch.setenv("LUCIEN_MODEL_RECONSTRUCAO", "sonnet")
    assert cfg.modelo(reconstrucao=True) == "sonnet"
    assert cfg.modelo() == "", "o incremental não herda o modelo da reconstrução"


def test_valor_torto_no_env_cai_no_padrao_em_vez_de_estourar(monkeypatch):
    """Um `.env` com `LUCIEN_BATCH_MIN=doze` não pode derrubar o daemon."""
    monkeypatch.setenv("LUCIEN_BATCH_MIN", "doze")
    assert cfg.lote_minimo() == 12


def test_valor_absurdo_e_contido_pelo_piso(monkeypatch):
    """`LUCIEN_INTERVAL_S=0` transformaria o tick num laço apertado."""
    monkeypatch.setenv("LUCIEN_INTERVAL_S", "0")
    assert cfg.intervalo_s() >= 30
