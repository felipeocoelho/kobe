#!/usr/bin/env python3
"""O diagnóstico de 'quem manda' — e a trava contra ele virar ficção.

O `kobe-memoria diagnostico` responde qual parâmetro está de fato cortando a
janela imediata. Ele é útil por um motivo medido em 31/08/2026: a janela PARECE
ser "os últimos 10 minutos" e não é — o piso de 8 mensagens corta em 87% dos
turnos, e quem mexesse no relógio não sentiria nada.

**O risco desta ferramenta é o de toda cópia:** ela reimplementa a lógica de
`bot/memory/working_set.py` para poder aplicá-la a turnos passados. Se as duas
divergirem, o diagnóstico passa a mentir **com autoridade** — e um diagnóstico
errado é pior que nenhum, porque leva a calibrar na direção errada com
confiança. Este arquivo existe para que a cópia não vire ficção: roda as duas
sobre o mesmo dado e exige o mesmo resultado.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot.memory import working_set as w

RAIZ = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_loader(
    "kobe_memoria",
    importlib.machinery.SourceFileLoader("kobe_memoria", str(RAIZ / "bot/bin/kobe-memoria")),
)
KM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(KM)

T0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _conversa(espacamentos_s, tamanho=400):
    """Mensagens alternando operador/agente, com os intervalos pedidos."""
    msgs, t = [], T0
    for i, gap in enumerate(espacamentos_s):
        t = t + timedelta(seconds=gap)
        msgs.append({"role": "user" if i % 2 == 0 else "assistant",
                     "ts": t, "n": tamanho,
                     "content": "x" * tamanho,
                     "created_at": t.isoformat(),
                     "audio_transcribed": False})
    return msgs


class _Db:
    def __init__(self, msgs):
        self._m = msgs

    def query(self, sql, params):
        limite = params[-1]
        return list(reversed(self._m[-limite:]))


@pytest.mark.parametrize("cenario", [
    [5] * 40,                       # rajada: tudo dentro dos 10 min -> manda o tempo
    [3600] * 40,                    # esparsa: nada dentro dos 10 min -> manda o piso
    [5] * 200,                      # rajada longa -> encosta no teto de mensagens
    [30, 5, 5, 900, 5, 5, 5, 7200] * 5,   # mistura, que é o padrão real
])
def test_o_diagnostico_reproduz_a_janela_de_verdade(cenario):
    """A trava contra a divergência silenciosa entre a cópia e o original."""
    msgs = _conversa(cenario)
    real = w.get_immediate_messages(_Db(msgs), "t")

    # o diagnóstico, ancorado no MESMO turno (a última mensagem da conversa)
    toks, nmsgs, _manda = KM._simular(
        msgs, [len(msgs) - 1],
        w.IMMEDIATE_WINDOW_SECONDS, w.IMMEDIATE_MIN_COUNT,
        w.IMMEDIATE_HARD_CAP, w.IMMEDIATE_TOKEN_CAP, w._CHARS_PER_TOKEN,
    )
    assert nmsgs[0] == len(real), (
        f"o diagnóstico diz {nmsgs[0]} mensagens e a janela real entrega {len(real)}")


def test_o_teto_de_token_tambem_bate():
    """O corte por tamanho é o mais fácil de reimplementar torto — ele anda de
    trás para frente e tem a regra de 'garante ao menos a última'."""
    msgs = _conversa([5] * 60, tamanho=4000)   # rajada de mensagens grandes
    real = w.get_immediate_messages(_Db(msgs), "t")
    _toks, nmsgs, manda = KM._simular(
        msgs, [len(msgs) - 1],
        w.IMMEDIATE_WINDOW_SECONDS, w.IMMEDIATE_MIN_COUNT,
        w.IMMEDIATE_HARD_CAP, w.IMMEDIATE_TOKEN_CAP, w._CHARS_PER_TOKEN)
    assert nmsgs[0] == len(real)
    assert manda["teto_tokens"] == 1, "o teto de token deveria ser o limitante aqui"


# ── A conferência de invariante ───────────────────────────────────────────

def test_a_configuracao_de_hoje_e_coerente():
    assert w.conferir() == []


def test_piso_maior_que_teto_e_acusado(monkeypatch):
    """Cada parâmetro sozinho é válido; o CONJUNTO é impossível. Faixa por
    parâmetro não pega isso — só a relação entre eles pega."""
    monkeypatch.setenv("WORKING_MEMORY_MIN_COUNT", "80")
    monkeypatch.setenv("WORKING_MEMORY_HARD_CAP", "60")
    import importlib
    recarregado = importlib.reload(w)
    try:
        problemas = recarregado.conferir()
        assert problemas and "MIN_COUNT" in problemas[0]
        assert "letra morta" in problemas[0]
    finally:
        monkeypatch.undo()
        importlib.reload(w)


def test_valor_fora_da_faixa_e_preso_e_nao_ignorado(monkeypatch):
    monkeypatch.setenv("WORKING_MEMORY_MIN_COUNT", "99999")
    monkeypatch.setenv("WORKING_MEMORY_CHARS_PER_TOKEN", "40")
    import importlib
    recarregado = importlib.reload(w)
    try:
        assert recarregado.IMMEDIATE_MIN_COUNT == 200
        assert recarregado._CHARS_PER_TOKEN == 5.0
    finally:
        monkeypatch.undo()
        importlib.reload(w)


def test_os_defaults_sao_os_valores_de_hoje():
    """A entrega expõe os botões e NÃO gira nenhum: mudar o comportamento da
    janela é decisão do operador, em fase própria. Se este teste quebrar, uma
    entrega que se declarou de risco zero mudou o prompt de todo turno."""
    assert w.IMMEDIATE_WINDOW_SECONDS == 600
    assert w.IMMEDIATE_MIN_COUNT == 8
    assert w.IMMEDIATE_HARD_CAP == 60
    assert w.IMMEDIATE_TOKEN_CAP == 8000
    assert w._CHARS_PER_TOKEN == 4.0
