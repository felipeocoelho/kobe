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

Formato de resposta: `verbose_json` (F0.6). O texto que sai é o mesmo
do formato `text`; o que muda é que a resposta passa a trazer
`segments` com `avg_logprob`, `compression_ratio` e `no_speech_prob`
— os sinais que o próprio Whisper usa pra detectar que degenerou. Eles
são **apenas registrados em log**: nenhuma decisão desta camada usa
esses números, porque julgar exige conhecer a faixa normal dos áudios
do operador, e isso só se aprende coletando em produção.

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
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, Optional

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

# Determinismo: recomendação da própria Groq pra transcrição. O Whisper ainda
# sobe a temperatura sozinho quando bate os thresholds internos de degeneração
# — o que se fixa aqui é o ponto de partida guloso, não o comportamento de
# fallback do decoder. F0.6: uma linha, reversível.
WHISPER_TEMPERATURE = 0

# O prompt de hints tem DOIS tetos, e vale o que vier primeiro.
#
# 1) BYTES — a Groq limita a 896 bytes UTF-8. A mensagem de erro da API diz
#    "characters" mas na prática conta bytes: em pt-BR cada acento custa 2, então
#    900 chars Unicode podem virar 925+ bytes. 850 dá folga pra texto com muito
#    diacrítico.
# 2) TOKENS — a Groq documenta o `prompt` em 224 tokens. Este teto NÃO existia no
#    código (F0.6: o guard estava inteiro na unidade errada), e é o que morde
#    primeiro num texto sem acento.
#
# Como se conta token sem tokenizer: NÃO se conta — estima-se, por cima. Não há
# tokenizer no ambiente, e o do Whisper é um BPE próprio; puxar dependência que
# ainda baixaria vocabulário pela rede DENTRO do caminho quente da transcrição
# seria pior que o problema. `chars/2` superestima ~1,5–2× (o real em pt-BR fica
# em 3–4 chars/token), então o erro é sempre pro lado seguro: corta cedo demais,
# nunca estoura o teto. Quando esse corte-por-palpite acontece, ele avisa em
# WARNING — quem lê o log merece saber que o limite era uma estimativa.
MAX_HINTS_BYTES = 850
MAX_HINTS_TOKENS = 224
CHARS_PER_TOKEN_ESTIMATE = 2


def _estimate_tokens(text: str) -> int:
    """Estimativa PESSIMISTA de tokens (ver bloco acima). Nunca subestima em pt-BR."""
    return math.ceil(len(text) / CHARS_PER_TOKEN_ESTIMATE)


def _cut_on_boundary(text: str, max_chars: int) -> str:
    """Corta em `max_chars` recuando até a última fronteira de item/palavra.

    Não é preciosismo: o arquivo de hints é uma LISTA DE VOCABULÁRIO, e cortá-la no
    meio de um nome injeta um fragmento sem sentido no `prompt` do Whisper — que é
    exatamente o vetor de prompt-bleeding que a F0.6 está combatendo (um áudio de
    11/06 voltou com o texto do arquivo de hints DENTRO da transcrição).

    O recuo só vale se sobrar a maior parte do que cabia; senão prefere-se o corte
    seco, pra não jogar fora meio prompt por causa de uma linha comprida.
    """
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    for sep in ("\n", ";", ",", " "):
        idx = head.rfind(sep)
        if idx > max_chars * 0.6:
            return head[:idx].rstrip(" ,;\n")
    return head.rstrip()


def _segment_field(segment: Any, name: str) -> Optional[float]:
    """Lê um campo do segmento aceitando dict OU objeto (o SDK devolve os dois)."""
    value = segment.get(name) if isinstance(segment, dict) else getattr(segment, name, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _extract_segments(result: Any) -> list:
    """Segmentos do `verbose_json`, ou lista vazia. Nunca levanta.

    O modelo `Transcription` do SDK da Groq declara só `text`, mas é pydantic com
    `extra="allow"` — `segments` chega como campo extra. Três formas possíveis
    (atributo, dict, `model_dump()`), e a ausência é resposta válida: transcrição
    não pode cair porque um campo de telemetria não veio.
    """
    if isinstance(result, dict):
        segments = result.get("segments")
    else:
        segments = getattr(result, "segments", None)
        if segments is None and hasattr(result, "model_dump"):
            try:
                segments = (result.model_dump() or {}).get("segments")
            except Exception:  # noqa: BLE001 — telemetria nunca derruba transcrição
                segments = None
    return segments if isinstance(segments, list) else []


def _log_whisper_signals(result: Any) -> None:
    """Registra os três sinais nativos de degeneração do Whisper. SÓ COLHE.

    `avg_logprob`, `compression_ratio` e `no_speech_prob` são os sinais que o
    próprio Whisper usa internamente pra detectar que degenerou — vêm de graça no
    `verbose_json`, na mesma chamada. **Nada nesta fase decide coisa alguma com
    eles**: julgar exige conhecer a faixa normal dos áudios do operador, e isso só
    existe depois de coleta em produção (F0.6, decisão registrada).

    Duas propriedades que valem por si:
      - **Só números saem daqui.** O texto dos segmentos é conteúdo do operador, e
        log não é lugar de conteúdo — a linha carrega agregados, nunca fala.
      - **Best-effort absoluto.** Qualquer falha aqui é engolida: telemetria não
        tem o direito de derrubar uma transcrição que já deu certo.
    """
    try:
        segments = _extract_segments(result)
        if not segments:
            return
        logprobs = [v for v in (_segment_field(s, "avg_logprob") for s in segments) if v is not None]
        ratios = [v for v in (_segment_field(s, "compression_ratio") for s in segments) if v is not None]
        silences = [v for v in (_segment_field(s, "no_speech_prob") for s in segments) if v is not None]
        if not (logprobs or ratios or silences):
            return

        def _fmt(value: Optional[float]) -> str:
            return f"{value:.3f}" if value is not None else "n/a"

        logger.info(
            "whisper_signals segments=%d avg_logprob_min=%s avg_logprob_mean=%s "
            "compression_ratio_max=%s no_speech_max=%s",
            len(segments),
            _fmt(min(logprobs) if logprobs else None),
            _fmt(sum(logprobs) / len(logprobs) if logprobs else None),
            _fmt(max(ratios) if ratios else None),
            _fmt(max(silences) if silences else None),
        )
    except Exception:  # noqa: BLE001 — ver docstring: telemetria não derruba nada
        logger.debug("falha colhendo sinais do whisper (ignorado)", exc_info=True)


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
        # Teto 1 — BYTES (limite duro da API, contagem exata).
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_HINTS_BYTES:
            cortado = encoded[:MAX_HINTS_BYTES].decode("utf-8", errors="ignore")
            # O corte por byte cai onde cair (possivelmente no meio de um nome);
            # `max_chars = len-1` força o recuo até a fronteira mais próxima.
            text = _cut_on_boundary(cortado, max(len(cortado) - 1, 0))
            logger.info(
                "transcription hints truncados de %d → %d bytes (limite Groq)",
                len(encoded),
                len(text.encode("utf-8")),
            )
        # Teto 2 — TOKENS (limite duro da API, contagem ESTIMADA por cima).
        estimado = _estimate_tokens(text)
        if estimado > MAX_HINTS_TOKENS:
            original = len(text)
            text = _cut_on_boundary(text, MAX_HINTS_TOKENS * CHARS_PER_TOKEN_ESTIMATE)
            logger.warning(
                "transcription hints truncados de %d → %d chars (~%d → ~%d tokens, "
                "limite Groq de %d; contagem ESTIMADA — considere encurtar o arquivo)",
                original,
                len(text),
                estimado,
                _estimate_tokens(text),
                MAX_HINTS_TOKENS,
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
            # `verbose_json` custa ZERO a mais (mesma chamada, mesmo preço) e é o
            # que traz `segments` com os três sinais nativos de degeneração do
            # Whisper. O texto sai idêntico ao do formato `text`. F0.6: só colher.
            "response_format": "verbose_json",
            "temperature": WHISPER_TEMPERATURE,
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

        # `verbose_json` devolve objeto; o formato antigo (`text`) devolvia str.
        # Aceitar os dois mantém o caminho vivo se alguém reverter o formato.
        if isinstance(result, str):
            text = result
        elif isinstance(result, dict):
            text = result.get("text") or ""
        else:
            text = getattr(result, "text", "") or ""
        _log_whisper_signals(result)
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
