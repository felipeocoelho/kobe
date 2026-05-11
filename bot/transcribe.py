"""Transcrição de áudio via Groq Whisper.

Wrapper fino sobre o cliente Groq. Recebe o conteúdo binário do áudio
(já baixado pelo handler do Telegram) e devolve a transcrição em texto.

Telegram envia voice messages em OGG/Opus e audio messages em formatos
diversos (mp3, m4a, etc.). Whisper aceita todos eles direto, então não
precisamos converter via ffmpeg pra esse caso de uso.

Modelo: whisper-large-v3 (multilíngue, autodetect). Não passamos o
parâmetro `language` — fixar idioma faz o Whisper *forçar* a saída
naquele idioma (efetivamente traduzindo se o áudio for em outra
língua), e na prática o autodetect do v3 não confunde pt-BR com es.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from groq import APIError, Groq


logger = logging.getLogger("kobe.transcribe")

WHISPER_MODEL = "whisper-large-v3"


class TranscriptionError(Exception):
    """Falha ao transcrever áudio (rede, formato, quota, etc.)."""


@dataclass
class Transcriber:
    api_key: str
    model: str = WHISPER_MODEL

    def __post_init__(self) -> None:
        self._client = Groq(api_key=self.api_key)

    def transcribe(self, audio_bytes: bytes, filename: str) -> str:
        """Manda bytes pro Whisper e devolve o texto cru, sem trim.

        `filename` precisa ter extensão coerente com o conteúdo (ex.
        `voice.ogg` pra voice messages do Telegram) — a Groq usa pra
        decidir o decoder.
        """
        try:
            result = self._client.audio.transcriptions.create(
                file=(filename, audio_bytes),
                model=self.model,
                response_format="text",
            )
        except APIError as exc:
            logger.warning("groq transcription falhou: %s", exc)
            raise TranscriptionError(str(exc)) from exc

        text = result if isinstance(result, str) else getattr(result, "text", "")
        return text.strip()
