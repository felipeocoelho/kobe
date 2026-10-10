#!/usr/bin/env python3
"""A credencial do LUCIEN e o aviso antecipado de vencimento (Etapa 0, B + C).

    .venv/bin/python -m pytest -q tests/test_lucien_credencial.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import brain, credencial, saude  # noqa: E402
from bot.lucien.saude import Observacao  # noqa: E402

UTC = timezone.utc
TOKEN = "sk-ant-oat01-de-teste-nao-e-real"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "kobe"
    (h / "user-data" / "secrets").mkdir(parents=True)
    monkeypatch.delenv("LUCIEN_OAUTH_TOKEN_FILE", raising=False)
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(tmp_path / "nao-existe.json"))
    return h


def _instala(home: Path, modo: int = 0o600) -> Path:
    p = Path(credencial.caminho_token(str(home)))
    p.write_text(TOKEN + "\n", encoding="utf-8")
    p.chmod(modo)
    return p


# ── B: o token próprio ───────────────────────────────────────────────────

def test_sem_token_nada_muda(home):
    env = brain._ambiente(str(home), "/tmp/cfg")
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert credencial.modo(str(home)) == "compartilhada"


def test_com_token_ele_entra_SO_no_subprocesso_e_tira_a_chave_de_api(home, monkeypatch):
    """A escolha do operador foi não pagar API pelo LUCIEN: uma chave herdada
    do daemon não pode passar na frente do token."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "chave-herdada")
    _instala(home)
    env = brain._ambiente(str(home), "/tmp/cfg")
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == TOKEN
    assert "ANTHROPIC_API_KEY" not in env
    assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") != TOKEN  # o processo não muda


def test_token_com_permissao_aberta_e_recusado(home, caplog):
    _instala(home, 0o644)
    with caplog.at_level("WARNING"):
        assert credencial.ler_token(str(home)) is None
    assert "RECUSADO" in caplog.text
    assert TOKEN not in caplog.text  # nunca o conteúdo


def test_instrucao_de_login_depende_da_credencial(home):
    assert "/login" in credencial.o_que_fazer_no_login(str(home))
    _instala(home)
    dica = credencial.o_que_fazer_no_login(str(home))
    assert "setup-token" in dica and "kobe-lucien token instalar" in dica
    _, msg = saude.decidir({}, Observacao(falhas_seguidas=3, ultimo_erro="401 OAuth"),
                           datetime.now(UTC), o_que_fazer_login=dica)
    assert "setup-token" in msg and "use `/login`" not in msg


# ── C: o aviso antecipado ────────────────────────────────────────────────

def _creds(tmp_path, vence: datetime) -> Path:
    p = tmp_path / "creds.json"
    p.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "SEGREDO", "refreshToken": "SEGREDO",
        "refreshTokenExpiresAt": int(vence.timestamp() * 1000)}}), encoding="utf-8")
    return p


def test_le_so_a_data_da_credencial_compartilhada(home, tmp_path, monkeypatch):
    vence = datetime(2026, 11, 1, 21, 58, tzinfo=UTC)
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(_creds(tmp_path, vence)))
    vals = credencial.validades(str(home))
    assert len(vals) == 1 and vals[0].vence_em == vence and not vals[0].estimada
    _, msgs = credencial.checar({}, vals, vence - timedelta(days=3))
    assert msgs and all("SEGREDO" not in m for m in msgs)


def test_avisa_7_dias_e_1_dia_antes_uma_vez_cada(home, tmp_path, monkeypatch):
    vence = datetime(2026, 11, 1, 21, 58, tzinfo=UTC)
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(_creds(tmp_path, vence)))
    vals = credencial.validades(str(home))
    estado, enviados = {}, []
    t = vence - timedelta(days=25)
    while t < vence + timedelta(days=2):  # um "tick" por dia
        estado, msgs = credencial.checar(estado, vals, t)
        enviados += [(t, m) for m in msgs]
        t += timedelta(days=1)
    assert len(enviados) == 3, enviados  # 7 dias, 1 dia, vencido
    assert "faltam 7 dia(s)" in enviados[0][1]  # arredonda pra CIMA
    assert "01/11/2026 às 18:58" in enviados[0][1]  # fuso do operador
    assert "faltam 1 dia(s)" in enviados[1][1] and enviados[1][0] >= vence - timedelta(days=1)
    assert "VENCEU" in enviados[2][1]
    assert "ANTES" not in enviados[2][1].upper().split("VENCEU")[1]  # depois de vencer, "antes" não faz sentido


def test_pior_caso_de_horario_o_aviso_de_1_dia_tem_folga_de_23h(home, tmp_path, monkeypatch):
    """O defeito que a simulação da produção mostrou em 07/10/2026: com
    checagem 1x/24h ancorada na hora do restart, um Keyko que subiu 1 minuto
    antes da hora do vencimento mandava o "falta 1 dia" com 1 MINUTO de folga.
    Com checagem de hora em hora, a folga mínima é 23h."""
    vence = datetime(2026, 11, 1, 21, 58, tzinfo=UTC)
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(_creds(tmp_path, vence)))
    vals = credencial.validades(str(home))
    for ancora_min in (-1, 0, 1, 17, 59):  # várias horas de "restart"
        estado, quando = {}, {}
        t = vence - timedelta(days=10) + timedelta(minutes=ancora_min)
        while t < vence + timedelta(hours=2):
            estado, msgs = credencial.checar(estado, vals, t)
            for m in msgs:
                quando[m.split(" — ")[-1][:20] if "VENCEU" not in m else "vencido"] = t
            t += timedelta(hours=1)  # a cadência da fonte
        marcos = sorted(quando.values())
        assert len(marcos) == 3, (ancora_min, quando)
        assert vence - marcos[1] >= timedelta(hours=23), (ancora_min, marcos[1])
        assert vence - marcos[0] >= timedelta(days=6, hours=23)


def test_abaixo_de_um_dia_a_conta_e_em_horas(home):
    assert credencial._quanto_falta(timedelta(hours=22, minutes=10)) == "23 hora(s)"
    assert credencial._quanto_falta(timedelta(minutes=5)) == "1 hora(s)"
    assert credencial._quanto_falta(timedelta(days=6, hours=22)) == "7 dia(s)"
    assert credencial._quanto_falta(timedelta(hours=23, minutes=11)) == "1 dia(s)"


def test_renovar_muda_a_data_e_os_marcos_voltam_a_valer(home, tmp_path, monkeypatch):
    v1 = datetime(2026, 11, 1, tzinfo=UTC)
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(_creds(tmp_path, v1)))
    estado, msgs = credencial.checar({}, credencial.validades(str(home)), v1 - timedelta(days=5))
    assert len(msgs) == 1
    v2 = v1 + timedelta(days=30)  # o operador fez /login
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(_creds(tmp_path, v2)))
    estado, msgs = credencial.checar(estado, credencial.validades(str(home)), v1)
    assert msgs == []  # a 30 dias, nada
    estado, msgs = credencial.checar(estado, credencial.validades(str(home)), v2 - timedelta(days=6))
    assert len(msgs) == 1


def test_quem_so_soube_a_1_dia_nao_recebe_tambem_o_de_7(home, tmp_path, monkeypatch):
    vence = datetime(2026, 11, 1, tzinfo=UTC)
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(_creds(tmp_path, vence)))
    vals = credencial.validades(str(home))
    estado, msgs = credencial.checar({}, vals, vence - timedelta(hours=12))
    assert len(msgs) == 1
    estado, msgs = credencial.checar(estado, vals, vence - timedelta(hours=6))
    assert msgs == []


def test_validade_do_token_e_ESTIMADA_e_a_mensagem_diz(home):
    _instala(home)
    vals = [v for v in credencial.validades(str(home)) if v.chave == "token"]
    assert vals and vals[0].estimada
    _, msgs = credencial.checar({}, vals, vals[0].vence_em - timedelta(days=2))
    assert "ESTIMADA" in msgs[0] and "setup-token" in msgs[0]
    assert TOKEN not in msgs[0]


def test_credencial_compartilhada_ilegivel_nao_levanta(home, tmp_path, monkeypatch):
    p = tmp_path / "torto.json"
    p.write_text("{não", encoding="utf-8")
    monkeypatch.setenv("LUCIEN_CREDENCIAL_COMPARTILHADA", str(p))
    assert credencial.validades(str(home)) == []
