#!/usr/bin/env python3
"""Testes da correção de latência de áudio (2026-06-04).

Cobrem o contrato novo de `Transcriber.transcribe` (retorna `(text, engine)`
em vez de só `text` + atributo compartilhado) e a propriedade que torna a
correção válida: transcrições podem rodar CONCORRENTES, porque o handler
deixou de segurar o lock do tópico durante o download/transcrição.

Sem rede: o cliente Groq é substituído por um fake. Rodar:

    .venv/bin/python tests/test_transcribe_latency.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from groq import APIError

from bot.transcribe import Transcriber, TranscriptionResult


def _make_transcriber(sleep_s: float = 0.0, raise_api_error: bool = False):
    """Transcriber com cliente Groq fake (sem rede)."""
    t = Transcriber(api_key="fake")

    class _FakeCreate:
        def create(self, **kwargs):
            if sleep_s:
                time.sleep(sleep_s)  # simula latência HTTP do Whisper
            if raise_api_error:
                err = APIError.__new__(APIError)
                err.message = "boom"
                raise err
            return "olá mundo"

    class _FakeAudio:
        transcriptions = _FakeCreate()

    class _FakeClient:
        audio = _FakeAudio()

    t._client = _FakeClient()
    return t


def test_returns_text_and_engine():
    t = _make_transcriber()
    result = t.transcribe(b"bytes", "voice.ogg")
    assert isinstance(result, TranscriptionResult), type(result)
    text, engine = result  # desempacota como o handler faz
    assert text == "olá mundo", repr(text)
    assert engine == "groq-whisper", repr(engine)
    assert t.last_engine_used == "groq-whisper"  # compat mantida
    print("ok: retorno (text, engine) no caminho normal")


def test_fallback_engine_value():
    t = _make_transcriber(raise_api_error=True)
    t.assemblyai_api_key = "fake-aai"
    t._transcribe_assemblyai = lambda audio, fn: "texto via fallback"
    text, engine = t.transcribe(b"bytes", "voice.ogg")
    assert text == "texto via fallback", repr(text)
    assert engine == "assemblyai-fallback", repr(engine)
    print("ok: engine='assemblyai-fallback' quando Whisper falha e AAI cobre")


def test_no_assemblyai_raises():
    from bot.transcribe import TranscriptionError

    t = _make_transcriber(raise_api_error=True)  # sem assemblyai_api_key
    try:
        t.transcribe(b"bytes", "voice.ogg")
    except TranscriptionError:
        print("ok: sem fallback configurado, falha vira TranscriptionError")
        return
    raise AssertionError("esperava TranscriptionError")


def test_concurrent_transcriptions_overlap():
    """A correção depende de transcrições rodarem em paralelo fora do lock.

    Duas transcrições de ~0.3s cada, disparadas via asyncio.to_thread (como
    o handler faz), devem terminar em ~0.3s no total — não ~0.6s. Se algo
    as serializasse, o tempo dobraria.
    """
    SLEEP = 0.3
    t = _make_transcriber(sleep_s=SLEEP)

    async def run_two():
        start = time.monotonic()
        await asyncio.gather(
            asyncio.to_thread(t.transcribe, b"a", "voice.ogg"),
            asyncio.to_thread(t.transcribe, b"b", "voice.ogg"),
        )
        return time.monotonic() - start

    elapsed = asyncio.run(run_two())
    # Folga generosa: paralelo ~0.3s; serial seria ~0.6s. Falha só se serial.
    assert elapsed < SLEEP * 1.6, f"transcrições não rodaram em paralelo: {elapsed:.2f}s"
    print(f"ok: 2 transcrições concorrentes em {elapsed:.2f}s (serial seria ~{SLEEP*2:.1f}s)")


if __name__ == "__main__":
    test_returns_text_and_engine()
    test_fallback_engine_value()
    test_no_assemblyai_raises()
    test_concurrent_transcriptions_overlap()
    print("\nTODOS OS TESTES PASSARAM")
