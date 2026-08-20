#!/usr/bin/env python3
"""Testes de "datar o que envelhece" — bug 3 das 4 correções (2026-08-20).

O caso real: o agente afirmou que uma sala de missão "segue rodando, te reporto
quando entregar". A sala estava `idle` havia 12 dias e já tinha entregado. Três
causas materiais, todas confirmadas no código e cobertas aqui:

1. o histórico injetado no prompt não tinha timestamp (o `created_at` vinha do
   banco e era DESCARTADO) — 12 dias ficava idêntico a agora;
2. a linha da sala entrava sem estado e sem data, com "ativa" no presente,
   porque `idle` conta como status ativo;
3. o bloco `[Estado de background vivo]`, que era o antídoto declarado, só
   olhava sessões do Coder — sala de missão nunca entrava.

Rodar: .venv/bin/python -m pytest tests/test_prompt_aging.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.claude_runner import build_prompt
from bot.memory import aging, background_state


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _iso(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


# ── 1. Carimbo no histórico ───────────────────────────────────────────────


def test_toda_linha_do_historico_sai_datada() -> None:
    """Decisão do operador: carimbo em TODAS as linhas, não só nos saltos."""
    hist = [
        {"role": "user", "content": "primeira", "created_at": _iso(days=12)},
        {"role": "assistant", "content": "segunda", "created_at": _iso(days=12, minutes=-1)},
        {"role": "user", "content": "terceira", "created_at": _iso(minutes=3)},
    ]
    p = build_prompt(thread_id=1, history=hist, new_message="e aí?")
    linhas = [l for l in p.splitlines() if "primeira" in l or "segunda" in l or "terceira" in l]
    assert len(linhas) == 3
    for l in linhas:
        assert l.startswith("[") and "/" in l[:8], f"linha sem carimbo: {l}"


def test_idade_relativa_na_primeira_linha_e_nos_saltos() -> None:
    """A idade relativa é o que faz 'isso é velho' bater no olho. Aparece na 1ª
    linha e quando há salto grande — não em todas (seria ruído e token à toa)."""
    hist = [
        {"role": "user", "content": "velha", "created_at": _iso(days=12)},
        {"role": "assistant", "content": "colada", "created_at": _iso(days=12, minutes=-1)},
        {"role": "user", "content": "nova", "created_at": _iso(minutes=3)},
    ]
    p = build_prompt(thread_id=1, history=hist, new_message="x")
    linha = {c: next(l for l in p.splitlines() if c in l) for c in ("velha", "colada", "nova")}
    assert "há ~12 dias" in linha["velha"], "1ª linha traz a idade"
    assert "há ~" not in linha["colada"], "linha colada na anterior não repete a idade"
    assert "há ~" in linha["nova"], "salto de 12 dias → idade de volta"


def test_cabecalho_avisa_que_historico_e_passado() -> None:
    hist = [{"role": "user", "content": "oi", "created_at": _iso(days=1)}]
    p = build_prompt(thread_id=1, history=hist, new_message="x")
    assert "America/Sao_Paulo" in p
    assert "PASSADO" in p


def test_linha_sem_timestamp_sai_sem_carimbo_e_nao_quebra() -> None:
    """Na dúvida, nada — nunca uma data inventada."""
    hist = [
        {"role": "user", "content": "sem data"},
        {"role": "user", "content": "data ruim", "created_at": "não é data"},
        {"role": "user", "content": "com data", "created_at": _iso(minutes=5)},
    ]
    p = build_prompt(thread_id=1, history=hist, new_message="x")
    assert "user: sem data" in p
    assert "user: data ruim" in p
    assert next(l for l in p.splitlines() if "com data" in l).startswith("[")


def test_tag_de_audio_convive_com_o_carimbo() -> None:
    hist = [{
        "role": "user", "content": "falei isso",
        "created_at": _iso(minutes=2), "audio_transcribed": True,
    }]
    p = build_prompt(thread_id=1, history=hist, new_message="x")
    linha = next(l for l in p.splitlines() if "falei isso" in l)
    assert linha.startswith("[") and "áudio transcrito" in linha


def test_flag_off_volta_ao_historico_sem_data() -> None:
    """Rollback trivial: PROMPT_AGING_ENABLED=false."""
    hist = [{"role": "user", "content": "oi", "created_at": _iso(days=3)}]
    with mock.patch.dict(os.environ, {"PROMPT_AGING_ENABLED": "false"}):
        p = build_prompt(thread_id=1, history=hist, new_message="x")
    assert "user: oi" in p
    assert "PASSADO" not in p
    assert not any(l.startswith("[0") or l.startswith("[1") or l.startswith("[2")
                   for l in p.splitlines() if "user: oi" in l)


def test_carimbo_usa_fuso_do_operador() -> None:
    """A VPS roda em UTC; 'ontem/hoje' do operador ancora no Brasil."""
    dt = datetime(2026, 8, 20, 2, 30, tzinfo=timezone.utc)  # 23:30 do dia 19 no BR
    assert aging.carimbo(dt) == "[19/08 23:30]"


def test_humanizar_idade_e_grosseiro_de_proposito() -> None:
    assert aging.humanizar_idade(30) == "agora há pouco"
    assert aging.humanizar_idade(300) == "há ~5 min"
    assert aging.humanizar_idade(3 * 3600) == "há ~3 h"
    # Até 36h fala em horas (mesma convenção do grounding e do background_state):
    # "há ~24 h" é mais informativo que "há ~1 dia" nessa faixa.
    assert aging.humanizar_idade(24 * 3600) == "há ~24 h"
    assert aging.humanizar_idade(40 * 3600) == "há ~2 dias"
    assert aging.humanizar_idade(12 * 24 * 3600) == "há ~12 dias"


# ── 2. A linha da sala ────────────────────────────────────────────────────


def _sala(home: Path, *, status: str, idade_dias: float, chat_id=-100, thread_id=475):
    d = home / "user-data" / "missoes" / "m-abc123"
    d.mkdir(parents=True, exist_ok=True)
    (d / "sala.json").write_text(json.dumps({
        "missao_id": "m-abc123",
        "objetivo": "analisar a pesquisa dos alunos",
        "status": status,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "last_activity": (
            datetime.now(timezone.utc) - timedelta(days=idade_dias)
        ).isoformat(timespec="seconds"),
    }), encoding="utf-8")
    return home


def test_sala_idle_e_apresentada_como_OCIOSA_com_idade() -> None:
    """O caso real: a linha dizia 'ativa' pra uma sala parada havia 12 dias."""
    from bot.mission_control import sala_dispatch

    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-sala-")), status="idle", idade_dias=12)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        linha = sala_dispatch.render_sala_ativa(home, -100, 475)

    assert linha is not None
    assert "OCIOSA" in linha and "idle" in linha
    assert "há ~12 dias" in linha
    assert "NÃO está trabalhando agora" in linha
    assert "segue rodando" in linha, "nomeia explicitamente a frase proibida"
    assert "Sala de missão ativa" not in linha, "a palavra que mentia saiu"


def test_sala_running_e_apresentada_como_RODANDO() -> None:
    from bot.mission_control import sala_dispatch

    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-sala-")), status="running", idade_dias=0)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        linha = sala_dispatch.render_sala_ativa(home, -100, 475)
    assert "RODANDO" in linha and "trabalhando AGORA" in linha


def test_sala_preserva_a_regra_de_roteamento() -> None:
    """A correção é sobre datar, não sobre mudar comportamento: a sala continua
    NÃO capturando o canal."""
    from bot.mission_control import sala_dispatch

    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-sala-")), status="idle", idade_dias=1)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        linha = sala_dispatch.render_sala_ativa(home, -100, 475)
    assert "NÃO repasse" in linha and "PERGUNTE antes de repassar" in linha
    assert "sala_dispatch retomar" in linha


def test_sala_de_outro_topico_nao_vaza() -> None:
    from bot.mission_control import sala_dispatch

    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-sala-")), status="idle",
                 idade_dias=1, thread_id=999)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        assert sala_dispatch.render_sala_ativa(home, -100, 475) is None


def test_estado_com_idade_sem_timestamp_e_honesto() -> None:
    assert "desconhecida" in aging.estado_com_idade("idle", None)


# ── 3. O bloco de estado de background ────────────────────────────────────


def test_bloco_de_background_passa_a_ver_salas() -> None:
    """O antídoto declarado contra narrar status de memória não olhava salas —
    era o buraco por onde o caso real passou."""
    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-bg-")), status="idle", idade_dias=12)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        bloco = background_state.render_background_state(home, 475, chat_id=-100)
    assert bloco is not None, "a sala tem que aparecer no bloco"
    assert "Sala de missão" in bloco and "state=idle" in bloco
    assert "12 dia" in bloco
    assert "idle` significa PARADO" in bloco or "PARADO esperando" in bloco


def test_sala_velha_nao_e_escondida_pela_janela_de_recencia() -> None:
    """Sala só fecha por ato do operador — 12 dias é fato VIVO, não arqueologia.
    O que ela precisa é do carimbo de idade, não do sumiço."""
    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-bg-")), status="idle", idade_dias=45)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        bloco = background_state.render_background_state(home, 475, chat_id=-100)
    assert bloco is not None and "Sala de missão" in bloco


def test_sala_encerrada_nao_entra() -> None:
    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-bg-")), status="encerrada", idade_dias=1)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        bloco = background_state.render_background_state(home, 475, chat_id=-100)
    assert bloco is None, "sala fechada não é estado vivo"


def test_bloco_de_background_ve_jobs_despachados() -> None:
    home = Path(tempfile.mkdtemp(prefix="kobe-bg-"))
    d = home / "user-data" / "dispatched"
    d.mkdir(parents=True)
    (d / "job-1234abcd.json").write_text(json.dumps({
        "state": "running",
        "last_activity": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pid": 4242,
    }), encoding="utf-8")
    bloco = background_state.render_background_state(home, 475, chat_id=-100)
    assert bloco is not None and "Job despachado" in bloco and "pid 4242" in bloco


def test_uma_fonte_quebrada_nao_apaga_as_outras() -> None:
    """O bloco existe pra o agente não ficar sem o fato vivo — uma leitura que
    falha não pode levar as outras junto."""
    home = _sala(Path(tempfile.mkdtemp(prefix="kobe-bg-")), status="running", idade_dias=0)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        with mock.patch.object(
            background_state, "_read_coder_jobs", side_effect=OSError("disco")
        ):
            bloco = background_state.render_background_state(home, 475, chat_id=-100)
    assert bloco is not None and "Sala de missão" in bloco


def _sala_extra(home: Path, nome: str, *, status: str, idade_dias: float):
    d = home / "user-data" / "missoes" / nome
    d.mkdir(parents=True, exist_ok=True)
    (d / "sala.json").write_text(json.dumps({
        "missao_id": nome, "objetivo": "obj", "status": status,
        "chat_id": -100, "thread_id": 475,
        "last_activity": (
            datetime.now(timezone.utc) - timedelta(days=idade_dias)
        ).isoformat(timespec="seconds"),
    }), encoding="utf-8")


def test_salas_antigas_nao_expulsam_trabalho_rodando_agora() -> None:
    """Achado com dado REAL de produção: 6 salas idle de julho enchiam o bloco
    inteiro. Sem teto próprio pras salas, uma sessão do Coder RODANDO agora —
    informação bem mais urgente — seria empurrada pra fora."""
    home = Path(tempfile.mkdtemp(prefix="kobe-bg-"))
    for i in range(6):
        _sala_extra(home, f"2026-07-0{i}-sala-antiga", status="idle", idade_dias=30 + i)

    d = home / "user-data" / "coder-sessions" / "475"
    d.mkdir(parents=True)
    (d / "aaaabbbb-cccc-dddd.json").write_text(json.dumps({
        "state": "running", "pid": 1234,
        "last_activity": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }), encoding="utf-8")

    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        bloco = background_state.render_background_state(home, 475, chat_id=-100)

    assert "Coder aaaabbbb: state=running" in bloco, "o que roda AGORA não pode sumir"
    assert bloco.count("Sala de missão") <= background_state.MAX_SALAS
    assert "Sala de missão" in bloco, "mas a sala mais recente continua visível"


def test_id_da_sala_nao_e_truncado_a_ponto_de_colidir() -> None:
    """Também veio do dado real: truncar em 12 fazia três salas de julho virarem
    todas '2026-07-09-d' — o bloco ficava inútil pra identificar qual é qual."""
    home = Path(tempfile.mkdtemp(prefix="kobe-bg-"))
    _sala_extra(home, "2026-07-09-arquitetura-borda-2", status="idle", idade_dias=2)
    _sala_extra(home, "2026-07-09-arquitetura-borda-3", status="idle", idade_dias=1)
    with mock.patch.dict(os.environ, {"MISSION_CONTROL_SALA_ENABLED": "true"}):
        bloco = background_state.render_background_state(home, 475, chat_id=-100)
    assert "arquitetura-borda-2" in bloco and "arquitetura-borda-3" in bloco


def test_linguagem_de_idade_e_a_mesma_em_todo_o_prompt() -> None:
    """Antes cada bloco tinha o seu formato ('~13 dia(s)' no background, 'há ~13
    dias' no grounding). O agente aprende um padrão só."""
    assert background_state._humanize_age(13 * 24 * 3600) == aging.humanizar_idade(13 * 24 * 3600)
    assert "dia(s)" not in background_state._humanize_age(13 * 24 * 3600)


def test_sem_chat_id_nao_quebra() -> None:
    """Compatibilidade: chamador antigo sem chat_id continua funcionando."""
    home = Path(tempfile.mkdtemp(prefix="kobe-bg-"))
    assert background_state.render_background_state(home, 475) is None


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
