"""Transcrição de áudio via Groq Whisper, com fallback automático pra AssemblyAI.

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

Fallback pra AssemblyAI: se `assemblyai_api_key` for fornecido e o
Whisper falhar (rate limit 429, indisponibilidade etc.), tentamos
AssemblyAI antes de levantar TranscriptionError. Quando o fallback é
usado, `last_engine_used` fica como "assemblyai-fallback" — o handler
do Telegram pode avisar o operador. Sem a key configurada, comportamento
é o original (falha = TranscriptionError direto).
"""

from __future__ import annotations

import io
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, Optional

from groq import APIError, Groq


logger = logging.getLogger("kobe.transcribe")


class TranscriptionResult(NamedTuple):
    """Texto transcrito + engine usada na chamada.

    Devolver a engine no retorno (em vez de só no atributo compartilhado
    `last_engine_used`) é o que torna `transcribe()` seguro pra rodar
    concorrente: várias transcrições em paralelo não competem por um
    único campo mutável. `last_engine_used` continua setado pra
    compatibilidade, mas o caller deve preferir `result.engine`.
    """

    text: str
    engine: str

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
    # Quando setada, Whisper falhando cai pra AssemblyAI antes de
    # propagar TranscriptionError. Nice-to-have — sem isso, comportamento
    # é o original (falha = exception).
    assemblyai_api_key: Optional[str] = None
    # Engine usada na ÚLTIMA chamada `transcribe()`. Valores possíveis:
    # "groq-whisper" (caminho normal), "assemblyai-fallback" (Whisper
    # falhou, AssemblyAI cobriu), "" (antes da primeira chamada).
    # Handler do Telegram lê após `transcribe()` pra avisar o operador.
    last_engine_used: str = field(default="", init=False)

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

    def transcribe(self, audio_bytes: bytes, filename: str) -> TranscriptionResult:
        """Manda bytes pro Whisper e devolve `(texto, engine)`, sem trim no texto.

        `filename` precisa ter extensão coerente com o conteúdo (ex.
        `voice.ogg` pra voice messages do Telegram) — a Groq usa pra
        decidir o decoder.

        `engine` é "groq-whisper" (caminho normal) ou "assemblyai-fallback"
        (Whisper falhou, AssemblyAI cobriu) — o caller usa pra avisar o
        operador quando o fallback foi acionado. `self.last_engine_used`
        também é atualizado pra compatibilidade, mas como `transcribe()`
        agora pode rodar concorrente (fora do lock do tópico), o caller
        deve ler `result.engine` — não o atributo compartilhado.
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
            if not self.assemblyai_api_key:
                raise TranscriptionError(str(exc)) from exc
            logger.info("tentando fallback pra AssemblyAI…")
            try:
                text = self._transcribe_assemblyai(audio_bytes, filename)
            except Exception as fallback_exc:  # noqa: BLE001
                logger.warning("assemblyai fallback também falhou: %s", fallback_exc)
                raise TranscriptionError(
                    f"Whisper falhou ({exc}) e fallback AssemblyAI também ({fallback_exc})"
                ) from fallback_exc
            self.last_engine_used = "assemblyai-fallback"
            return TranscriptionResult(text.strip(), "assemblyai-fallback")

        text = result if isinstance(result, str) else getattr(result, "text", "")
        self.last_engine_used = "groq-whisper"
        return TranscriptionResult(text.strip(), "groq-whisper")

    def _transcribe_assemblyai(self, audio_bytes: bytes, filename: str) -> str:
        """Fallback: usa AssemblyAI (sem speakers) quando Whisper falha.

        SDK importado lazy — só puxa quando o fallback acontece. Áudio é
        gravado num arquivo temporário porque o SDK aceita path (não
        bytes-em-memória) na assinatura `transcribe`.
        """
        try:
            import assemblyai as aai  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "assemblyai SDK não instalado — fallback indisponível"
            ) from exc

        aai.settings.api_key = self.assemblyai_api_key  # type: ignore[assignment]
        # `speech_models` (plural) é exigido pelo backend atual da AssemblyAI.
        # "universal-2" é o modelo multilíngue padrão (suporta PT-BR).
        config = aai.TranscriptionConfig(
            speaker_labels=False,
            language_code="pt",
            punctuate=True,
            format_text=True,
            speech_models=["universal-2"],
        )
        suffix = Path(filename).suffix or ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            transcriber = aai.Transcriber(config=config)
            transcript = transcriber.transcribe(tmp.name)
        if transcript.status == aai.TranscriptStatus.error:
            raise RuntimeError(f"AssemblyAI status=error: {transcript.error}")
        text = (transcript.text or "").strip()
        if not text:
            raise RuntimeError("AssemblyAI retornou texto vazio")
        return text
