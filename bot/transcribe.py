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

Hints de transcrição: se `hints_path` for fornecido e o arquivo
existir, lemos o conteúdo a cada chamada e passamos como `prompt` pro
Whisper. Isso biasa a transcrição pra reconhecer nomes próprios e
gírias específicas do operador (ex.: "HAL" sendo transcrito como
"Raul" em sotaque carioca). Releitura a cada chamada é intencional:
o onboarding pode criar/editar o arquivo a qualquer momento, e o
custo de I/O é desprezível diante de uma chamada HTTP pra Groq.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from groq import APIError, Groq


logger = logging.getLogger("kobe.transcribe")

WHISPER_MODEL = "whisper-large-v3"

# Whisper (Groq) limita o prompt a 896 BYTES UTF-8. A mensagem de erro
# da API diz "characters" mas na prática conta bytes — em pt-BR cada
# acento custa 2 bytes, então 900 chars Unicode podem virar 925+ bytes.
# Margem conservadora de 850 bytes garante folga pra textos com muitos
# diacríticos. Truncamento opera em bytes; decode(errors="ignore")
# descarta byte residual caso o corte caia no meio de um multibyte.
MAX_HINTS_BYTES = 850


class TranscriptionError(Exception):
    """Falha ao transcrever áudio (rede, formato, quota, etc.)."""


@dataclass
class Transcriber:
    api_key: str
    hints_path: Optional[Path] = None
    model: str = WHISPER_MODEL

    def __post_init__(self) -> None:
        self._client = Groq(api_key=self.api_key)

    def _read_hints(self) -> Optional[str]:
        if self.hints_path is None:
            return None
        try:
            text = self.hints_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("falha lendo transcription hints: %s", exc)
            return None
        if not text:
            return None
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_HINTS_BYTES:
            text = encoded[:MAX_HINTS_BYTES].decode("utf-8", errors="ignore")
            logger.info(
                "transcription hints truncados de %d → %d bytes (limite Groq)",
                len(encoded),
                len(text.encode("utf-8")),
            )
        return text

    def transcribe(self, audio_bytes: bytes, filename: str) -> str:
        """Manda bytes pro Whisper e devolve o texto cru, sem trim.

        `filename` precisa ter extensão coerente com o conteúdo (ex.
        `voice.ogg` pra voice messages do Telegram) — a Groq usa pra
        decidir o decoder.
        """
        kwargs: dict = {
            "file": (filename, audio_bytes),
            "model": self.model,
            "response_format": "text",
        }
        hints = self._read_hints()
        if hints:
            kwargs["prompt"] = hints

        try:
            result = self._client.audio.transcriptions.create(**kwargs)
        except APIError as exc:
            logger.warning("groq transcription falhou: %s", exc)
            raise TranscriptionError(str(exc)) from exc

        text = result if isinstance(result, str) else getattr(result, "text", "")
        return text.strip()
