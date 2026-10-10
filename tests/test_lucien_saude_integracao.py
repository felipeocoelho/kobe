#!/usr/bin/env python3
"""LUCIEN que falha AVISA — a corrente inteira, com banco e worker de verdade.

O que é real aqui: o worker (`uma_rodada`), o brain montando o comando, o banco
(`lucien_runs`), a fonte do Keyko observando e decidindo. O que é falso: a CLI
`claude` — um script no PATH que devolve o envelope REAL de "sem login"
capturado em 07/10/2026 e sai com código 1 — e o envio ao Telegram, capturado.

Precisa de `KOBE_TEST_DATABASE_URL` apontando para um banco DESCARTÁVEL: cada
execução cria um tópico próprio e, no fim, apaga só o que criou.

    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test_etapa0 \\
        .venv/bin/python -m pytest -q tests/test_lucien_saude_integracao.py
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import aviso, saude, source, store, worker  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")

_ENVELOPE = (
    '{"duration_api_ms":0,"stop_reason":"stop_sequence","total_cost_usd":0,'
    '"terminal_reason":"api_error","is_error":true,"subtype":"success",'
    '"api_error_status":null,"result":"Not logged in · Please run /login",'
    '"type":"result"}'
)


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    cx = store.conectar(_URL)
    cur = cx.cursor()
    # Um tópico NOVO por execução, com mensagens próprias. Assim o teste não
    # depende de banco semeado nem precisa apagar nada no fim: o cursor que a
    # rodada bem-sucedida avança é o deste tópico, e o próximo teste usa outro.
    cur.execute("INSERT INTO topics (current_name) VALUES ('teste saude LUCIEN')"
                " RETURNING id")
    topico = cur.fetchone()["id"]
    cur.execute("INSERT INTO sessions (topic_id) VALUES (%s) RETURNING id", (topico,))
    sessao = cur.fetchone()["id"]
    # Papel `assistant` de propósito: outras suítes (test_lucien_store) pegam a
    # mensagem mais recente DO OPERADOR do banco inteiro como fixture, e um
    # tópico de teste nosso com fala de usuário passaria a ser ela.
    for conteudo in ("o deploy é sempre via git", "a migration vai no mesmo commit"):
        cur.execute("INSERT INTO messages (session_id, topic_id, role, content)"
                    " VALUES (%s, %s, 'assistant', %s)", (sessao, topico, conteudo))
    cx.commit()

    bindir = tmp_path / "bin"
    bindir.mkdir()
    falso = bindir / "claude"
    modo = tmp_path / "modo"
    modo.write_text("sem-login", encoding="utf-8")
    falso.write_text(
        "#!/bin/sh\ncat >/dev/null\n"
        f'if [ "$(cat {modo})" = "ok" ]; then\n'
        """  echo '{"type":"result","result":"{\\"nothing_durable\\": true}"}'\n"""
        "  exit 0\nfi\n"
        f"echo '{_ENVELOPE}'\nexit 1\n",
        encoding="utf-8",
    )
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("DATABASE_URL", _URL)
    # Isola das credenciais reais da máquina (o aviso de vencimento lê a data).
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(tmp_path / "sem-creds.json"))
    monkeypatch.setenv("LUCIEN_OAUTH_TOKEN_FILE", str(tmp_path / "sem-token"))

    enviados: list[str] = []
    monkeypatch.setattr(aviso, "avisar",
                        lambda home, motivo: enviados.append(motivo) or True)

    home = tmp_path / "home"
    (home / "user-data" / "lucien").mkdir(parents=True)
    fonte = source.LucienSource(kobe_home=home,
                                db_factory=lambda: store.conectar(_URL))
    try:
        yield {"cx": cx, "topic": str(topico), "home": home,
               "fonte": fonte, "enviados": enviados, "modo": modo}
    finally:
        # Limpa SÓ o que este teste criou (pelo id do tópico dele). Deixar
        # rastro quebrava fixtures de outras suítes, que tomam "a mensagem mais
        # recente do banco" como dado de teste. Mesma convenção de
        # test_lucien_reconstrucao.py.
        cx.rollback()
        c = cx.cursor()
        c.execute("DELETE FROM lucien_events WHERE run_id IN"
                  " (SELECT id FROM lucien_runs WHERE topic_id = %s)", (topico,))
        c.execute("DELETE FROM lucien_runs WHERE topic_id = %s", (topico,))
        c.execute("DELETE FROM lucien_cursor WHERE topic_id = %s", (topico,))
        c.execute("DELETE FROM messages WHERE topic_id = %s", (topico,))
        c.execute("DELETE FROM sessions WHERE topic_id = %s", (topico,))
        c.execute("DELETE FROM topics WHERE id = %s", (topico,))
        cx.commit()
        cx.close()


def _rodada(amb):
    return worker.uma_rodada(conninfo=_URL, kobe_home=str(amb["home"]),
                             topic_id=amb["topic"], respeitar_cota=False)


def test_a_corrente_inteira_sem_login_avisa_uma_vez_e_depois_avisa_que_voltou(ambiente):
    amb = ambiente
    for _ in range(2):
        r = _rodada(amb)
        assert not r.ok and "Not logged in" in r.erro
        amb["fonte"]._cuidar_da_saude(amb["cx"])
    assert amb["enviados"] == [], "duas falhas ainda são ruído"

    _rodada(amb)
    amb["fonte"]._cuidar_da_saude(amb["cx"])
    assert len(amb["enviados"]) == 1
    assert amb["enviados"][0].startswith("PAREI") and "sem login" in amb["enviados"][0]

    # o erro gravado no banco agora DIZ a causa (era cortado antes dela)
    cur = amb["cx"].cursor()
    cur.execute("SELECT error FROM lucien_runs ORDER BY started_at DESC LIMIT 1")
    assert cur.fetchone()["error"].split(": ", 1)[1].startswith("causa: Not logged in")
    amb["cx"].commit()

    for _ in range(5):  # repetição soma, não repete
        _rodada(amb)
        amb["fonte"]._cuidar_da_saude(amb["cx"])
    assert len(amb["enviados"]) == 1
    assert saude.ler(str(amb["home"]))["falhas"] == 8

    amb["modo"].write_text("ok", encoding="utf-8")  # o operador fez /login
    r = _rodada(amb)
    assert r.ok, r.erro
    amb["fonte"]._cuidar_da_saude(amb["cx"])
    assert len(amb["enviados"]) == 2
    assert amb["enviados"][1].startswith("voltou a funcionar")
    assert "8 rodada(s) falharam" in amb["enviados"][1]
    assert saude.ler(str(amb["home"]))["aberto"] is False


def test_worker_que_morre_sem_registrar_e_pego_pela_contagem_de_disparos(ambiente):
    """Disparo sem rodada nova no banco, três vezes seguidas = parado."""
    import time

    amb = ambiente
    fonte = amb["fonte"]
    time.sleep(1.5)  # rodadas de outros testes não podem cair na folga de 1 s
    for i in range(3):
        fonte._marcar_disparo()
        fonte._ultimo_codigo = 1
        fonte._cuidar_da_saude(amb["cx"])
    assert len(amb["enviados"]) == 1
    assert "worker" in amb["enviados"][0] and "código 1" in amb["enviados"][0]


def test_saude_que_quebra_nao_derruba_o_tick(ambiente, monkeypatch):
    amb = ambiente

    def _explode(*a, **k):
        raise RuntimeError("banco sumiu no meio")

    monkeypatch.setattr(saude, "observar", _explode)
    amb["fonte"]._cuidar_da_saude(amb["cx"])  # não levanta
    assert amb["enviados"] == []


def test_aviso_antecipado_de_vencimento_sai_da_fonte_uma_vez_por_marco(
        ambiente, tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone

    vence = datetime.now(timezone.utc) + timedelta(days=3)
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"claudeAiOauth": {
        "refreshTokenExpiresAt": int(vence.timestamp() * 1000)}}), encoding="utf-8")
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(creds))

    amb = ambiente
    amb["fonte"]._cuidar_da_saude(amb["cx"])
    assert len(amb["enviados"]) == 1 and "vence em" in amb["enviados"][0]
    amb["fonte"]._cuidar_da_saude(amb["cx"])  # mesma hora, mesmo marco: nada
    assert len(amb["enviados"]) == 1


def test_a_fonte_rechecha_o_vencimento_de_hora_em_hora(ambiente, tmp_path, monkeypatch):
    """Com a cadência antiga (24h), o marco de 1 dia só seria percebido no dia
    seguinte — no pior caso, minutos antes de vencer."""
    import json
    import time
    from datetime import datetime, timedelta, timezone

    creds = tmp_path / "creds.json"

    def _vence_em(delta):
        v = datetime.now(timezone.utc) + delta
        creds.write_text(json.dumps({"claudeAiOauth": {
            "refreshTokenExpiresAt": int(v.timestamp() * 1000)}}), encoding="utf-8")

    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(creds))
    amb = ambiente
    _vence_em(timedelta(days=3))
    amb["fonte"]._cuidar_da_saude(amb["cx"])
    assert len(amb["enviados"]) == 1  # marco de 7 dias

    _vence_em(timedelta(hours=20))  # o tempo "passou": agora falta menos de 1 dia
    est = saude.ler(str(amb["home"]))
    est["_validade_checada_em"] = time.time() - 3601  # última checagem há 1h
    saude.gravar(str(amb["home"]), est)
    amb["fonte"]._cuidar_da_saude(amb["cx"])
    assert len(amb["enviados"]) == 2 and "hora(s)" in amb["enviados"][1]
