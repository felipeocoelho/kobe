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
