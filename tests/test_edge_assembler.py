#!/usr/bin/env python3
"""Testes da Peça A (Message Assembler) — agregação por debounce.

Trava:
- fragmentos dentro da janela → UM flush concatenado, na ORDEM de reserva.
- fragmentos separados por > janela → flushes separados.
- ORDEM preservada mesmo com fill fora de ordem (voz lenta + texto rápido).
- release libera slot abortado sem travar o flush.
- flag de áudio agregada (any); isolamento entre tópicos.
- teto de espera (max_wait) força flush.
- flush_now força flush imediato.
- o callback COMPLETA nos três caminhos de flush (timer, flush_now, poll de
  pendente) mesmo suspendendo no meio — regressão do auto-cancelamento.
- flush_now preserva o cancelamento LEGÍTIMO do timer pendente.

Rodar:
    .venv/bin/python tests/test_edge_assembler.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.assembler import MessageAssembler


def _collector():
    flushes: list[tuple[str, bool]] = []

    async def cb(text: str, audio: bool, message: object) -> None:
        flushes.append((text, audio))

    return flushes, cb


def _mk(**kw) -> MessageAssembler:
    base = dict(quiet_ms=50, quiet_terminated_ms=50, max_wait_ms=5000, pending_poll_ms=15)
    base.update(kw)
    return MessageAssembler(**base)


def test_aggregates_fragments_in_window() -> None:
    async def scenario() -> None:
        a = _mk()
        flushes, cb = _collector()
        for frag in ["oi Hal", "sabe aquele problema", "então é o seguinte"]:
            idx = a.reserve(1, None)
            await a.fill(1, None, idx, frag, audio=False, message=object(), flush_cb=cb)
            await asyncio.sleep(0.01)  # < janela → não flusha entre fragmentos
        await asyncio.sleep(0.12)
        assert len(flushes) == 1, f"esperava 1 flush, veio {len(flushes)}"
        assert flushes[0][0] == "oi Hal\n\nsabe aquele problema\n\nentão é o seguinte"
        assert flushes[0][1] is False

    asyncio.run(scenario())


def test_separate_bursts_two_flushes() -> None:
    async def scenario() -> None:
        a = _mk()
        flushes, cb = _collector()
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "primeiro pensamento", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.12)  # deixa flushar
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "outro assunto depois", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.12)
        assert len(flushes) == 2, f"esperava 2 flushes, veio {len(flushes)}"

    asyncio.run(scenario())


def test_order_preserved_with_out_of_order_fill() -> None:
    """Voz lenta (reservada 1º, preenchida por último) + texto rápido."""
    async def scenario() -> None:
        a = _mk()
        flushes, cb = _collector()
        i_voice = a.reserve(1, None)  # chegou 1º
        i_text = a.reserve(1, None)   # chegou 2º
        # texto preenche primeiro (transcrição da voz ainda em voo)
        await a.fill(1, None, i_text, "texto rapido", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.02)
        # voz preenche depois
        await a.fill(1, None, i_voice, "voz lenta", audio=True, message=object(), flush_cb=cb)
        await asyncio.sleep(0.15)
        assert len(flushes) == 1
        assert flushes[0][0] == "voz lenta\n\ntexto rapido", flushes[0][0]
        assert flushes[0][1] is True, "any(audio) → True"

    asyncio.run(scenario())


def test_release_frees_slot_without_blocking_flush() -> None:
    async def scenario() -> None:
        a = _mk()
        flushes, cb = _collector()
        i_voice = a.reserve(1, None)
        i_text = a.reserve(1, None)
        await a.fill(1, None, i_text, "só o texto", audio=False, message=object(), flush_cb=cb)
        await a.release(1, None, i_voice)  # transcrição da voz falhou
        await asyncio.sleep(0.15)
        assert len(flushes) == 1
        assert flushes[0][0] == "só o texto"

    asyncio.run(scenario())


def test_release_all_does_not_leak_or_flush() -> None:
    async def scenario() -> None:
        a = _mk()
        flushes, cb = _collector()
        i = a.reserve(1, None)
        await a.release(1, None, i)  # único fragmento abortou
        await asyncio.sleep(0.1)
        assert flushes == [], "nada pra flushar; buffer não deve vazar turno"
        assert (1, None) not in a._buffers, "buffer deve ter sido limpo"

    asyncio.run(scenario())


def test_topic_isolation() -> None:
    async def scenario() -> None:
        a = _mk()
        flushes, cb = _collector()
        ia = a.reserve(1, 10)
        ib = a.reserve(1, 20)
        await a.fill(1, 10, ia, "tópico A", audio=False, message=object(), flush_cb=cb)
        await a.fill(1, 20, ib, "tópico B", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.15)
        texts = sorted(t for t, _ in flushes)
        assert texts == ["tópico A", "tópico B"], f"cada tópico flusha sozinho: {texts}"

    asyncio.run(scenario())


def test_max_wait_forces_flush() -> None:
    async def scenario() -> None:
        # janela longa (1s) mas teto de 80ms: um fluxo contínuo de fragmentos
        # tem que flushar pelo teto, não represar pra sempre.
        a = _mk(quiet_ms=1000, quiet_terminated_ms=1000, max_wait_ms=80)
        flushes, cb = _collector()
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "frag 1", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.05)
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "frag 2", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.05)  # elapsed ~100ms > 80ms teto
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "frag 3", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.05)
        assert len(flushes) == 1, f"teto deveria dar 1 flush; veio {len(flushes)}"
        assert flushes[0][0] == "frag 1\n\nfrag 2\n\nfrag 3"

    asyncio.run(scenario())


def test_flush_now_forces_immediate() -> None:
    async def scenario() -> None:
        a = _mk(quiet_ms=5000, quiet_terminated_ms=5000, max_wait_ms=5000)
        flushes, cb = _collector()
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "acumulado", audio=False, message=object(), flush_cb=cb)
        # sem flush_now esperaria 5s; forçamos agora
        await a.flush_now(1, None)
        await asyncio.sleep(0.01)
        assert len(flushes) == 1
        assert flushes[0][0] == "acumulado"

    asyncio.run(scenario())


def test_terminated_punctuation_flushes_faster() -> None:
    async def scenario() -> None:
        # janela base longa; terminada curta. Uma frase com pontuação final
        # dispara pela janela CURTA.
        a = _mk(quiet_ms=2000, quiet_terminated_ms=40, max_wait_ms=5000)
        flushes, cb = _collector()
        idx = a.reserve(1, None)
        await a.fill(
            1, None, idx, "essa é a pergunta completa?", audio=False,
            message=object(), flush_cb=cb,
        )
        await asyncio.sleep(0.12)  # > janela curta (40ms), << janela base (2s)
        assert len(flushes) == 1, "frase terminada em '?' deve flushar pela janela curta"

    asyncio.run(scenario())


def _suspending_collector():
    """Callback que SUSPENDE no meio, como o de produção.

    Por que isto importa (e por que a suite acima não pegava o bug do
    auto-cancelamento): `_collector` é um `async def` que nunca suspende, e um
    `await` sobre corrotina que não suspende NÃO devolve o controle ao event
    loop. Sem ponto de suspensão, um `CancelledError` marcado no task corrente
    nunca chega a ser entregue — então o flush "passava" no teste e morria em
    produção, onde o callback real faz I/O e suspende.

    `started` é registrado ANTES do await e `done` DEPOIS: só `done` prova que o
    callback atravessou a suspensão e completou. Asserir `started` seria repetir
    o falso verde.
    """
    started: list[str] = []
    done: list[str] = []

    async def cb(text: str, audio: bool, message: object) -> None:
        started.append(text)
        await asyncio.sleep(0)  # ponto de suspensão — igual ao I/O do despacho
        done.append(text)

    return started, done, cb


def test_timer_flush_completes_callback() -> None:
    """Flush pelo TIMER de debounce — o caminho de toda mensagem normal.

    Regressão do bug de produção (2026-07-14): `_flush` cancelava `buf.timer`,
    que é o PRÓPRIO task rodando `_flush`; o CancelledError era entregue no
    `await cb(...)` e o turno morria calado.
    """
    async def scenario() -> None:
        a = _mk()
        started, done, cb = _suspending_collector()
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "mensagem do operador", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.15)  # deixa o TIMER disparar o flush
        assert started == ["mensagem do operador"], f"callback nem começou: {started}"
        assert done == ["mensagem do operador"], (
            "callback começou mas NÃO completou — flush morreu na suspensão "
            "(auto-cancelamento do timer)"
        )

    asyncio.run(scenario())


def test_flush_now_completes_callback() -> None:
    """Flush por flush_now (comando slash) — chamado de FORA do timer.

    Este caminho nunca teve o bug (ali `buf.timer` é outro task e o cancel é
    legítimo). Trava de não-regressão: o fix não pode quebrá-lo.
    """
    async def scenario() -> None:
        a = _mk(quiet_ms=5000, quiet_terminated_ms=5000, max_wait_ms=5000)
        started, done, cb = _suspending_collector()
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "acumulado", audio=False, message=object(), flush_cb=cb)
        await a.flush_now(1, None)
        assert done == ["acumulado"], f"flush_now não completou o callback: {done}"

    asyncio.run(scenario())


def test_pending_poll_flush_completes_callback() -> None:
    """Flush pelo POLL de fragmento pendente — o 3º caminho que tocava o cancel.

    Voz reservada 1º e abortada (release) enquanto o texto já flushava: o flush
    do timer encontra pendente, re-arma o poll, e é o POLL quem despacha.
    """
    async def scenario() -> None:
        a = _mk(quiet_ms=20, pending_poll_ms=15)
        started, done, cb = _suspending_collector()
        i_voice = a.reserve(1, None)
        i_text = a.reserve(1, None)
        await a.fill(1, None, i_text, "só o texto", audio=False, message=object(), flush_cb=cb)
        await asyncio.sleep(0.05)  # timer dispara, acha i_voice pendente, re-arma o poll
        await a.release(1, None, i_voice)  # transcrição falhou → poll resolve e flusha
        await asyncio.sleep(0.1)
        assert done == ["só o texto"], f"poll de pendente não completou o callback: {done}"

    asyncio.run(scenario())


def test_flush_now_cancels_pending_timer() -> None:
    """O cancelamento LEGÍTIMO não pode se perder no fix.

    Quando o flush vem de fora, o timer armado é outro task e TEM que ser
    cancelado — senão ele acorda depois à toa. Guarda contra um fix preguiçoso
    que só apagasse o cancel.
    """
    async def scenario() -> None:
        a = _mk(quiet_ms=30, quiet_terminated_ms=30)
        started, done, cb = _suspending_collector()
        idx = a.reserve(1, None)
        await a.fill(1, None, idx, "acumulado", audio=False, message=object(), flush_cb=cb)
        timer = a._buffers[(1, None)].timer
        assert timer is not None, "fill deveria ter armado um timer"
        await a.flush_now(1, None)
        await asyncio.sleep(0.1)  # tempo de sobra pro timer ter disparado, se vivo
        assert timer.cancelled(), "timer pendente deveria ter sido cancelado pelo flush_now"
        assert done == ["acumulado"], f"esperava 1 flush completo, veio {done}"

    asyncio.run(scenario())


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
