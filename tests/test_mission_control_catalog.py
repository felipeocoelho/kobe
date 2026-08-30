#!/usr/bin/env python3
"""O dispatch do Mission Control declara sistema e subsistema — ou não abre sala.

Highlander v3, F1, cenário **C4** do plano de testes.

O QUE ESTES TESTES DE FATO PROVAM
----------------------------------
Não é que "a função devolveu um dicionário de erro". É que, depois de uma recusa,
**nada foi criado no disco**: nem pasta de missão, nem `sala.json`, nem processo.
A frase *"NENHUMA sala foi aberta"*, que a recusa imprime, tem que ser
literalmente verdade — do contrário o operador ficaria procurando uma sala que
não existe, ou pior, uma sala existiria sem linha nenhuma no catálogo, que é
exatamente o estado que esta fase veio corrigir.

Por isso a asserção central de quase todo teste aqui é uma contagem de arquivos
antes e depois, e não o conteúdo do retorno.

O caminho FELIZ é testado com o `_spawn_worker` substituído. Não é preguiça: abrir
uma sala de verdade dispara uma sessão Claude, e o recurso escasso desta campanha
é cota de assinatura. O que precisa ser provado aqui é a **ordem** (registra,
depois abre) e a passagem correta dos campos — e isso o dublê prova melhor, porque
deixa observar o que aconteceu antes do spawn.

    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test \\
        .venv/bin/python -m pytest -q tests/test_mission_control_catalog.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.mission_control import sala_dispatch  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    """Um `KOBE_HOME` descartável, com a sala e o catálogo ligados.

    Nada aqui encosta no `KOBE_HOME` de verdade: a pasta de missões que os testes
    contam é a deste `tmp_path`.
    """
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")

    monkeypatch.setenv("MISSION_CONTROL_SALA_ENABLED", "true")
    monkeypatch.setenv("WORK_CATALOG_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", _URL)
    monkeypatch.setenv("KOBE_HOME", str(tmp_path))
    # A faxina oportunista mexe em tmux de verdade; fora dela nos testes.
    monkeypatch.setattr(sala_dispatch.sala_cleanup, "cleanup_stale_salas",
                        lambda **kw: None)
    monkeypatch.setattr(sala_dispatch.sala_cleanup, "count_active",
                        lambda *a, **k: (0, []))
    return tmp_path


def _missoes(kobe_home: Path) -> list[Path]:
    raiz = kobe_home / "user-data" / "missoes"
    return sorted(p for p in raiz.glob("*") if p.is_dir()) if raiz.is_dir() else []


def _abrir(kobe_home, **kw):
    base = dict(kobe_home=kobe_home, objetivo="pensar sobre X",
                chat_id=-100_555_001, thread_id=77)
    base.update(kw)
    return sala_dispatch.abrir_sala(**base)


# ══════════════════════════════════════════════════════════════════════════
# C4 — a recusa, e o que ela NÃO deixa pra trás
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("kw,codigo", [
    ({}, "sistema_nao_declarado"),
    ({"system": "", "subsystem": "none"}, "sistema_nao_declarado"),
    ({"system": "Radar", "subsystem": "none"}, "sistema_desconhecido"),
    ({"system": "Kobe"}, "subsistema_nao_declarado"),
    ({"system": "Kobe", "subsystem": ""}, "subsistema_nao_declarado"),
    ({"system": "Flow", "subsystem": "Coder"}, "subsistema_desconhecido"),
])
def test_declaracao_invalida_recusa_e_nao_cria_nada(ambiente, monkeypatch, kw, codigo):
    """Seis formas de errar a declaração; nenhuma abre sala, nenhuma deixa rastro."""
    chamou_spawn = []
    monkeypatch.setattr(sala_dispatch, "_spawn_worker",
                        lambda *a, **k: chamou_spawn.append(a) or 1)

    antes = _missoes(ambiente)
    res = _abrir(ambiente, **kw)

    assert res.get("error") == codigo
    assert res.get("refusal") is True
    assert "NENHUMA sala foi aberta" in res["note"]
    assert res.get("ok") is not True

    assert _missoes(ambiente) == antes, "a recusa deixou pasta de missão pra trás"
    assert chamou_spawn == [], "a recusa chegou a disparar o worker"


def test_a_recusa_ensina_o_que_fazer(ambiente):
    """Quem lê a recusa é um agente decidindo o próximo passo, não um humano
    lendo um log. Dizer "o que fazer" vale mais que dizer "o que houve" — e uma
    mensagem só com "o que houve" faria o agente tentar outro nome até colar,
    que é como um erro de digitação vira sistema fantasma."""
    res = _abrir(ambiente, system="Radar", subsystem="none")
    assert "Kobe" in res["message"]
    assert "pergunte ao operador" in res["message"].lower()


def test_banco_fora_e_falha_de_instrumento_nao_recusa(ambiente, monkeypatch):
    """**A distinção que não pode se perder no caminho.**

    Banco fora e "você não declarou o sistema" têm o mesmo desfecho (a sala não
    nasce) e reações opostas: uma se resolve declarando direito, a outra
    consertando o serviço. Se as duas chegassem iguais ao agente, ele tentaria
    redeclarar contra um Postgres morto.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://ninguem@127.0.0.1:1/nada")
    antes = _missoes(ambiente)

    res = _abrir(ambiente, system="Kobe", subsystem="none")

    assert res["error"] == "catalogo_indisponivel"
    assert res.get("unavailable") is True
    assert res.get("refusal") is not True
    assert "FALHA DE INSTRUMENTO" in res["note"]
    assert "NENHUMA sala foi aberta" in res["note"]
    assert _missoes(ambiente) == antes


# ══════════════════════════════════════════════════════════════════════════
# O caminho feliz, e a ORDEM
# ══════════════════════════════════════════════════════════════════════════

def test_declaracao_valida_registra_ANTES_de_abrir(ambiente, monkeypatch):
    """**A ordem é o desenho inteiro.**

    Se a sala subisse antes do registro, uma falha no meio deixaria exatamente o
    que a F1 veio corrigir: sala trabalhando sem linha em lugar nenhum. O teste
    olha o banco de dentro do dublê do spawn — no instante em que a sala estaria
    nascendo, a linha já tem que existir.
    """
    from bot import work_catalog as wc

    visto: dict = {}

    def spawn_espiao(*a, **k):
        db = wc.connect()
        try:
            visto["linhas"] = db.query(
                "SELECT id FROM work_sessions WHERE title = 'pensar sobre X'")
        finally:
            db.close()
        return 4242

    monkeypatch.setattr(sala_dispatch, "_spawn_worker", spawn_espiao)

    res = _abrir(ambiente, system="Kobe", subsystem="Coder")

    assert res["ok"] is True
    assert visto["linhas"], "a sala foi disparada ANTES de a linha existir"
    assert res["session_id"] in [l["id"] for l in visto["linhas"]]


def test_o_retorno_diz_o_que_foi_declarado(ambiente, monkeypatch):
    monkeypatch.setattr(sala_dispatch, "_spawn_worker", lambda *a, **k: 1)
    res = _abrir(ambiente, system="kobe", subsystem="CODER")   # caixa trocada
    assert (res["system"], res["subsystem"]) == ("Kobe", "Coder")
    assert res["catalogado"] is True


def test_subsystem_none_e_aceito_e_grava_nulo(ambiente, monkeypatch):
    from bot import work_catalog as wc

    monkeypatch.setattr(sala_dispatch, "_spawn_worker", lambda *a, **k: 1)
    res = _abrir(ambiente, system="Kobe", subsystem="none")

    assert res["ok"] is True and res["subsystem"] is None
    db = wc.connect()
    try:
        linha = wc.get_session(db, res["session_id"])
    finally:
        db.close()
    assert linha["subsystem_id"] is None
    assert linha["kind"] == "mission"


def test_cwd_e_gravado_como_metadado(ambiente, monkeypatch):
    """`cwd` entra no catálogo, mas não decide nada — o sistema veio da
    declaração. É o §6.1: um desenho que derivasse o sistema da pasta erraria
    exatamente no caso do plugin."""
    from bot import work_catalog as wk

    monkeypatch.setattr(sala_dispatch, "_spawn_worker", lambda *a, **k: 1)
    pasta = ambiente / "algum" / "lugar"
    pasta.mkdir(parents=True)
    res = _abrir(ambiente, system="Flow", subsystem="none", cwd=pasta)

    db = wk.connect()
    try:
        linha = wk.get_session(db, res["session_id"])
    finally:
        db.close()
    assert linha["cwd"] == str(pasta)
    assert linha["system_name"] == "Flow"


# ══════════════════════════════════════════════════════════════════════════
# O rollback
# ══════════════════════════════════════════════════════════════════════════

def test_com_o_catalogo_desligado_a_sala_abre_sem_declaracao(ambiente, monkeypatch):
    """**O rollback nomeado no briefing**, exercitado.

    *"Chave desliga o coletor e o registro; os dispatchers voltam a aceitar
    abertura sem declaração."* Se este teste quebrar, desligar a chave deixou de
    ser um rollback — e uma implantação que chegasse antes da migration 006
    derrubaria toda abertura de sala, que é o pior modo de falha desta fase.
    """
    monkeypatch.setenv("WORK_CATALOG_ENABLED", "false")
    monkeypatch.setattr(sala_dispatch, "_spawn_worker", lambda *a, **k: 1)

    res = _abrir(ambiente)          # sem system, sem subsystem

    assert res["ok"] is True
    assert res["catalogado"] is False
    assert res["system"] is None


def test_com_a_sala_desligada_o_catalogo_nem_entra(ambiente, monkeypatch):
    """A flag da própria sala continua sendo a primeira porta — o catálogo não
    pode transformar "Mission Control desligado" em "sistema não declarado"."""
    monkeypatch.setenv("MISSION_CONTROL_SALA_ENABLED", "false")
    res = _abrir(ambiente)
    assert res["error"] == "sala_disabled"
