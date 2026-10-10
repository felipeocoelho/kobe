#!/usr/bin/env python3
"""A regra de quando o LUCIEN avisa que parou (Etapa 0, 07/10/2026).

O caso que deu origem a isto: de 04/10 20:45 UTC a 05/10 20:12 UTC, 144 rodadas
seguidas falharam por falta de login e o operador não recebeu NADA. O teste
central reproduz essa sequência e trava o número de mensagens — nem zero (o
defeito), nem 144 (a enxurrada).

    .venv/bin/python -m pytest -q tests/test_lucien_saude.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import saude  # noqa: E402
from bot.lucien.saude import Observacao  # noqa: E402

UTC = timezone.utc
ERRO_LOGIN = ("a CLI saiu com código 1: causa: Not logged in · Please run /login · "
              "terminal_reason=api_error · {\"duration_api_ms\":0}")


def _rodadas_de_04_05_outubro():
    """Os horários reais: lote devido a cada 5 min, teto de 6 rodadas por hora
    (:45 :50 :55 :00 :05 :10 e depois espera a janela abrir)."""
    t = datetime(2026, 10, 4, 20, 45, tzinfo=UTC)
    fim = datetime(2026, 10, 5, 20, 12, 30, tzinfo=UTC)
    rodadas = []
    while t <= fim:
        janela = [r for r in rodadas if r > t - timedelta(hours=1)]
        if len(janela) < 6:
            rodadas.append(t)
        t += timedelta(minutes=5)
    return rodadas


def test_a_sequencia_real_de_04_05_outubro_da_cinco_mensagens():
    falhas = _rodadas_de_04_05_outubro()
    assert 140 <= len(falhas) <= 150  # o banco registrou 144
    exito = datetime(2026, 10, 5, 20, 47, 15, tzinfo=UTC)

    estado, msgs = {}, []
    tick = falhas[0]
    while tick <= exito + timedelta(minutes=10):
        feitas = [f for f in falhas if f <= tick]
        if tick >= exito:
            obs = Observacao(falhas_seguidas=0, ultimo_exito_em=exito)
        else:
            obs = Observacao(falhas_seguidas=len(feitas),
                             primeira_falha_em=falhas[0], ultimo_erro=ERRO_LOGIN,
                             ultimo_exito_em=datetime(2026, 9, 24, tzinfo=UTC))
        estado, msg = saude.decidir(estado, obs, tick)
        if msg:
            msgs.append((tick, msg))
        tick += timedelta(minutes=5)

    assert len(msgs) == 5, [m for _, m in msgs]
    assert msgs[0][1].startswith("PAREI")
    assert "sem login" in msgs[0][1] and "/login" in msgs[0][1]
    assert all(m.startswith("continuo parado") for _, m in msgs[1:4])
    assert msgs[4][1].startswith("voltou a funcionar")
    assert "nada se perdeu" in msgs[4][1]
    assert estado["aberto"] is False
    # A abertura sai no 3º fracasso — não espera a hora fechar.
    assert msgs[0][0] == falhas[2]


def test_falha_isolada_nao_avisa():
    estado, msg = saude.decidir({}, Observacao(falhas_seguidas=2, ultimo_erro="x"),
                                datetime.now(UTC))
    assert msg is None and not estado.get("aberto")


def test_horario_da_mensagem_vem_no_fuso_do_operador():
    t0 = datetime(2026, 10, 4, 20, 45, tzinfo=UTC)  # 17:45 em Brasília
    _, msg = saude.decidir({}, Observacao(falhas_seguidas=3, primeira_falha_em=t0,
                                          ultimo_erro=ERRO_LOGIN), t0)
    assert "04/10 17:45" in msg


def test_causa_que_muda_no_meio_do_incidente_gera_mensagem():
    t = datetime(2026, 10, 4, 20, 45, tzinfo=UTC)
    estado, _ = saude.decidir({}, Observacao(falhas_seguidas=3, primeira_falha_em=t,
                                             ultimo_erro=ERRO_LOGIN), t)
    estado, msg = saude.decidir(
        estado, Observacao(falhas_seguidas=4, primeira_falha_em=t,
                           ultimo_erro="o modelo não respondeu em 180s"),
        t + timedelta(minutes=5))
    assert msg and "MUDOU" in msg and "modelo sem responder" in msg
    # e a mesma causa de novo, logo depois, não repete
    estado, msg = saude.decidir(
        estado, Observacao(falhas_seguidas=5, primeira_falha_em=t,
                           ultimo_erro="o modelo não respondeu em 180s"),
        t + timedelta(minutes=10))
    assert msg is None


def test_worker_que_morre_sem_registrar_tambem_abre_incidente():
    """O caminho que o banco não enxerga: o worker morre antes de abrir a linha
    da rodada (import quebrado, banco fora). Só a contagem de disparos vê."""
    t = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    estado, msg = saude.decidir({}, Observacao(disparos_sem_rodada=3,
                                               ultimo_codigo_worker=1), t)
    assert estado["aberto"] and msg.startswith("PAREI")
    assert "worker" in msg and "código 1" in msg and "worker.log" in msg


def test_classificacao():
    assert saude.classificar(ERRO_LOGIN) == "login"
    assert saude.classificar("OAuth token has expired") == "login"
    assert saude.classificar("API Error: 401 authentication_error") == "login"
    assert saude.classificar("o modelo não respondeu em 180s") == "tempo"
    assert saude.classificar("JSON inválido na resposta: parece TRUNCADO") == "ilegivel"
    assert saude.classificar("a CLI `claude` não foi encontrada") == "cli_ausente"
    assert saude.classificar("RuntimeError: qualquer") == "outro"
    assert saude.classificar(None) == "outro"


def test_mensagem_nunca_carrega_o_envelope_inteiro():
    envelope = "a CLI saiu com código 1: causa: Not logged in · " + "x" * 5000
    _, msg = saude.decidir({}, Observacao(falhas_seguidas=3, ultimo_erro=envelope),
                           datetime.now(UTC))
    assert len(msg) < 900


def test_estado_persiste_e_sobrevive_a_restart(tmp_path):
    t = datetime(2026, 10, 4, 20, 45, tzinfo=UTC)
    estado, _ = saude.decidir({}, Observacao(falhas_seguidas=3, primeira_falha_em=t,
                                             ultimo_erro=ERRO_LOGIN), t)
    saude.gravar(str(tmp_path), estado)
    # "restart": lê do disco; o incidente aberto não pode gerar outra abertura
    lido = saude.ler(str(tmp_path))
    _, msg = saude.decidir(lido, Observacao(falhas_seguidas=4, primeira_falha_em=t,
                                            ultimo_erro=ERRO_LOGIN),
                           t + timedelta(minutes=5))
    assert msg is None


def test_estado_ilegivel_vira_vazio_e_nao_levanta(tmp_path):
    p = Path(saude.caminho(str(tmp_path)))
    p.parent.mkdir(parents=True)
    p.write_text("{não é json", encoding="utf-8")
    assert saude.ler(str(tmp_path)) == {}
    p.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert saude.ler(str(tmp_path)) == {}


def _helper(tmp_path, codigo: int) -> str:
    home = tmp_path / f"home{codigo}"
    (home / "bot" / "bin").mkdir(parents=True)
    h = home / "bot" / "bin" / "kobe-notify"
    h.write_text(f"#!/bin/sh\necho 'telegram recusou' >&2\nexit {codigo}\n", encoding="utf-8")
    h.chmod(0o755)
    return str(home)


def test_aviso_so_conta_como_entregue_se_o_helper_disse_que_entregou(tmp_path, monkeypatch, caplog):
    """Até 07/10 devolvia True só por ter RODADO o helper (stderr em /dev/null)."""
    from bot.lucien import aviso

    monkeypatch.setenv("LUCIEN_ALERT_CHAT_ID", "123")
    assert aviso.avisar(_helper(tmp_path, 0), "x") is True
    with caplog.at_level("WARNING"):
        assert aviso.avisar(_helper(tmp_path, 1), "y") is False
    assert "NÃO entregue" in caplog.text and "telegram recusou" in caplog.text
