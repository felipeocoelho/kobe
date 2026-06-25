"""Handlers do Telegram (camada de transporte).

Recebe updates, autoriza usuário, persiste no Supabase, dispara `claude -p`
com histórico da sessão e devolve a resposta no mesmo tópico. Áudio passa
por Groq Whisper antes — daí em diante o pipeline é igual ao texto.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from supabase import Client
from telegram import Audio, Document, Message, Update, Voice
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.artifacts import save_artifact_from_messages, search_artifacts
from bot.claude_runner import (
    ClaudeError,
    ClaudeExitError,
    ClaudeNotFoundError,
    ClaudeRunner,
    ClaudeTimeoutError,
    build_prompt,
)
from bot.compactor import compact_session
from bot.config import Config
from bot.handoff import (
    DestiladorError,
    active_handoff_path,
    archive_path_for_session,
    destilar_sessao,
    rotate_active_to_archive,
)
from bot.handoff.destilador import MIN_MESSAGES_FOR_HANDOFF
from bot.handoff.paths import ensure_topic_handoff_dirs
from bot.markdown import to_telegram_html
from bot.missoes import storage as missoes_storage
from bot.missoes import orquestrador as missoes_orquestrador
from bot.alertas.context import render_alertas_abertos
from bot.chat_manager import activity as cm_activity
from bot.chat_manager.context import render_chat_manager_section
from bot.memory import (
    get_immediate_messages,
    load_curated_core,
    render_background_state,
    render_grounding_signals,
)
from bot.plugins import Plugin, render_plugins_section
from bot.progress import ProgressReporter
from bot.turn_classifier import ROUTE_BACKGROUND, classify_turn
from bot.topic_manager import (
    TOPIC_CONTEXT_CHAR_LIMIT,
    archive_active_session,
    consume_truncated_marker,
    count_messages,
    ensure_active_session,
    ensure_topic,
    get_active_conversation_for_topic,
    get_active_session,
    get_conversation_session_summaries,
    get_recent_messages,
    get_topic_slug,
    insert_message,
    load_topic_context,
    mark_welcomed,
    rename_topic_dir,
    set_topic_name,
    set_topic_status,
    slugify,
    topic_knowledge_dir,
    unique_knowledge_path,
)
from bot.transcribe import Transcriber, TranscriptionError
from bot import hindsight_client


logger = logging.getLogger("kobe.handler")

# Tasks fire-and-forget (ex.: retain do Hindsight): guardamos a referência num
# set até concluírem, senão o GC pode coletar a task antes de ela rodar
# (asyncio só mantém referência fraca). best-effort — não bloqueiam o turno.
_BG_TASKS: set = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


# Telegram corta mensagens em 4096 caracteres. Mantemos margem pra prefixos
# de continuação ("…") e quebras de linha.
TELEGRAM_TEXT_LIMIT = 4000

# Tempo entre disparos de "digitando…" enquanto o Claude pensa. O efeito
# no Telegram dura ~5s, então 4s mantém a indicação visível sem flicker.
TYPING_INTERVAL_SECONDS = 4

# NB: streaming token-a-token da resposta pro Telegram foi REMOVIDO em
# 2026-06-01 (existiu na v0.15). Editar a mesma mensagem a cada ~1s
# rolava a tela e tirava o operador do ponto de leitura ("pior a emenda
# que o soneto"). O sinal de vida durante o processamento é o
# `ProgressReporter` (status por etapa: "lendo arquivo X", "rodando
# comando Y"); a resposta sai INTEIRA de uma vez quando fica pronta, via
# `_send_long_text` (com fatiamento no limite do Telegram).

# Texto da mensagem de boas-vindas (v0.11). Enviado uma vez por tópico,
# explicando como adicionar/consultar/atualizar a knowledge base. HTML
# porque o bot já usa parse_mode=HTML pro restante.
WELCOME_MESSAGE = (
    "👋 <b>Esse é um espaço de assunto separado</b>, com instruções e base "
    "de conhecimento próprias — você ensina aqui e eu uso aqui.\n"
    "\n"
    "<b>Pra adicionar conteúdo</b>:\n"
    "• Texto/áudio: <i>\"anota como instrução: …\"</i> ou "
    "<i>\"adiciona à base de conhecimento: …\"</i> (áudio é transcrito)\n"
    "• Arquivo: anexa um <code>.txt</code>, <code>.md</code>, <code>.pdf</code> "
    "ou <code>.docx</code> aqui e eu salvo na base\n"
    "\n"
    "<b>Pra consultar</b>: <i>\"o que tem aqui na base?\"</i>, "
    "<i>\"quais as instruções deste tópico?\"</i>\n"
    "\n"
    "<b>Pra atualizar/remover</b>: <i>\"atualiza a instrução sobre X\"</i>, "
    "<i>\"esquece o arquivo Y\"</i>"
)

# Limites do upload de anexo (v0.11). 5 MB cobre PDFs/DOCXs reais com
# folga; texto extraído acima de 50k chars é rejeitado pra não estourar
# o limite de 20k do prompt (TOPIC_CONTEXT_CHAR_LIMIT) com folga pra
# múltiplos arquivos por tópico.
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
UPLOAD_MAX_EXTRACTED_CHARS = 50_000

# Extensões aceitas. PDF/DOCX extraímos texto e salvamos como .md;
# TXT/MD passam quase intactos (só strip e header).
UPLOAD_ALLOWED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


# Sequenciador FIFO por tópico (chat_id, thread_id) — base do multitasking E,
# desde 2026-06-09, garantia de ORDEM DE CHEGADA. Com `concurrent_updates(True)`
# em main.py o PTB despacha updates em paralelo; mensagens de tópicos diferentes
# correm soltas, mas dentro de um mesmo tópico só roda uma seção crítica por vez
# (evita race em user-data/, inserção fora de ordem no Supabase, disparos duplos
# de compactação) E na ordem em que as mensagens CHEGARAM.
#
# Por que um asyncio.Lock não bastava (bug da rajada, card 8b04cf6a):
# o lock é FIFO entre quem JÁ chamou `acquire()`, mas a ORDEM de chamada do
# acquire dependia de quem terminava o "preparo" primeiro — e o preparo tem
# duração variável (baixar mídia, transcrever áudio: segundos). Uma voice nota
# que chega 1º mas leva 4s transcrevendo só chamava acquire() DEPOIS de um texto
# que chegou em 2º (preparo zero). Resultado: resposta fora de ordem (Defeito 1)
# e, como a citação da msg N era respondida no turno da msg N+1, citação órfã
# (Defeito 2). Ver bot/transcribe + on_voice: a transcrição roda FORA da seção
# crítica de propósito (latência), então o acquire fora de ordem era inevitável.
#
# A correção: cada handler tira um TICKET de forma SÍNCRONA logo na entrada
# (antes de qualquer await/preparo). O ticket carimba a ordem de chegada. O
# preparo pesado segue em paralelo; só a ENTRADA na seção crítica (insert +
# claude) espera a vez do ticket — `ticket.wait_turn()`. Assim a ordem de
# resposta = ordem de chegada, sem serializar o trabalho pesado (FIFO de
# verdade, sem custo de latência). O ticket é SEMPRE liberado (`complete()`
# idempotente, em finally), mesmo se o handler aborta antes da vez (transcrição
# falha, texto vazio) — senão o tópico travava.
#
# O dict cresce indefinidamente (uma entrada por tópico tocado). Em escala atual
# (poucos tópicos por operador) é irrelevante; se virar problema, vale TTL.


class _Ticket:
    """Posição de um handler na fila FIFO de um tópico. Ver `_TopicGate`."""

    __slots__ = ("_gate", "_n", "_completed")

    def __init__(self, gate: "_TopicGate", n: int) -> None:
        self._gate = gate
        self._n = n
        self._completed = False

    async def wait_turn(self) -> None:
        """Bloqueia até ser a vez deste ticket (todos os anteriores concluíram).

        Chamar uma única vez, imediatamente antes da seção crítica. O preparo
        pesado (download/transcrição) deve rodar ANTES desta chamada pra correr
        em paralelo com o preparo das outras mensagens do tópico.
        """
        await self._gate._wait_turn(self._n)

    async def complete(self) -> None:
        """Libera a vez. Idempotente — seguro chamar em `finally` mesmo que
        `wait_turn()` nunca tenha rodado (handler abortou antes da vez). Sem
        isto o sequenciador travava o tópico no primeiro abort."""
        if self._completed:
            return
        self._completed = True
        await self._gate._complete(self._n)


class _TopicGate:
    """Sequenciador FIFO de um tópico: serve um ticket por vez, na ordem em que
    foram tirados (= ordem de chegada das mensagens), independente de quando
    cada preparo termina. É também o mutex da seção crítica (um por vez)."""

    __slots__ = ("_next", "_serving", "_done", "_cond")

    def __init__(self) -> None:
        self._next = 0
        self._serving = 0
        self._done: set[int] = set()
        self._cond = asyncio.Condition()

    def take(self) -> _Ticket:
        """Tira o próximo ticket. SÍNCRONO de propósito: tem que rodar no
        primeiro passo do handler (antes do 1º await) pra fixar a chegada."""
        n = self._next
        self._next += 1
        return _Ticket(self, n)

    async def _wait_turn(self, n: int) -> None:
        async with self._cond:
            await self._cond.wait_for(lambda: self._serving == n)

    async def _complete(self, n: int) -> None:
        async with self._cond:
            self._done.add(n)
            # Avança a vez sobre todos os tickets contíguos já concluídos. Cobre
            # a conclusão FORA de ordem: um handler que abortou antes da vez
            # (ticket 2 falhou enquanto o 1 ainda roda) marca-se done; quando o
            # 1 concluir, o serving pula 1→2→3 de uma vez e acorda o 3.
            while self._serving in self._done:
                self._done.discard(self._serving)
                self._serving += 1
            self._cond.notify_all()


_topic_gates: dict[tuple[int, Optional[int]], _TopicGate] = {}


def _get_topic_gate(chat_id: int, thread_id: Optional[int]) -> _TopicGate:
    key = (chat_id, thread_id)
    gate = _topic_gates.get(key)
    if gate is None:
        gate = _TopicGate()
        _topic_gates[key] = gate
    return gate


def _take_ticket(chat_id: int, thread_id: Optional[int]) -> _Ticket:
    """Tira um ticket FIFO pro tópico. Chame LOGO NA ENTRADA do handler, antes
    de qualquer await/preparo, pra carimbar a ordem de chegada da mensagem."""
    return _get_topic_gate(chat_id, thread_id).take()


@contextlib.asynccontextmanager
async def _serve(chat_id: int, thread_id: Optional[int]):
    """Açúcar pra handlers SEM preparo pesado (texto, comandos, anexo, resume):
    tira o ticket, espera a vez, roda o corpo, libera. Drop-in do antigo
    `async with lock:`, mas com ordem de chegada garantida.

    on_voice NÃO usa isto: precisa tirar o ticket na entrada e transcrever
    ANTES de esperar a vez, então usa `_take_ticket()` + `ticket.wait_turn()`
    explícitos (o preparo fica fora da seção crítica, em paralelo)."""
    ticket = _get_topic_gate(chat_id, thread_id).take()
    try:
        await ticket.wait_turn()
        yield
    finally:
        await ticket.complete()


def _user_authorized(update: Update, allowed_ids: frozenset[int]) -> bool:
    user = update.effective_user
    return user is not None and user.id in allowed_ids


def _topic_label(thread_id: Optional[int]) -> str:
    return f"topic={thread_id}" if thread_id is not None else "topic=general"


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    claude: ClaudeRunner = context.application.bot_data["claude"]
    plugins: list[Plugin] = context.application.bot_data.get("plugins", [])
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    text = (message.text or "").strip()
    if not text:
        return

    user_id = update.effective_user.id if update.effective_user else None
    logger.info(
        "msg recebida user=%s %s len=%d",
        user_id,
        _topic_label(message.message_thread_id),
        len(text),
    )

    # Texto não tem preparo pesado: tira o ticket e espera a vez na própria
    # entrada da seção crítica. A ordem de chegada vira a ordem de resposta.
    async with _serve(message.chat_id, message.message_thread_id):
        await _handle_user_text(
            message=message,
            text=text,
            audio_transcribed=False,
            config=config,
            db=db,
            claude=claude,
            plugins=plugins,
        )


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recebe voice/audio, transcreve via Groq e processa como texto."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    claude: ClaudeRunner = context.application.bot_data["claude"]
    transcriber: Transcriber = context.application.bot_data["transcriber"]
    plugins: list[Plugin] = context.application.bot_data.get("plugins", [])
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    media = message.voice or message.audio
    if media is None:
        return

    thread_id = message.message_thread_id

    # Ticket FIFO tirado JÁ NA ENTRADA, antes do download/transcrição: carimba
    # a ordem de chegada do áudio. O preparo (download + transcrição) roda fora
    # da seção crítica, em PARALELO com o preparo das outras mensagens do tópico
    # (fix latência de áudio, 2026-06-04 — ver bloco abaixo). Só a ENTRADA na
    # seção crítica (`ticket.wait_turn()`, lá embaixo) respeita a vez. Assim um
    # áudio que chega 1º mas demora a transcrever NÃO perde a vez pra um texto
    # que chegou depois — FIFO de verdade. `complete()` em finally garante que
    # qualquer abort no preparo (transcrição falha, áudio vazio) libere a vez;
    # sem isso o tópico travaria no primeiro áudio problemático.
    ticket = _take_ticket(message.chat_id, thread_id)
    try:
        user_id = update.effective_user.id if update.effective_user else None
        filename = _audio_filename(media)
        logger.info(
            "áudio recebido user=%s %s file=%s dur=%ss",
            user_id,
            _topic_label(thread_id),
            filename,
            media.duration,
        )

        # Download + transcrição rodam FORA da seção crítica do tópico (fix
        # latência de áudio, 2026-06-04). Antes o lock era pego ANTES da
        # transcrição "pra preservar ordem voice+text" — mas o efeito colateral
        # era grave: como o lock só liberava quando o claude_run anterior do
        # mesmo tópico terminava (60–300s), cada áudio ficava ENFILEIRADO atrás
        # do LLM do áudio anterior antes de sequer poder ser transcrito. Nos
        # logs: áudio de 24s recebido às 22:42 só transcrito 241s depois, e
        # bursts de 5 voice notes em fila por ~5min — embora a transcrição em si
        # leve 3–4s.
        #
        # Transcrição é função pura (bytes → texto): sem estado compartilhado,
        # sem escrita no DB. Fora da seção crítica, transcrições de áudios em
        # fila no mesmo tópico rodam em PARALELO (cada uma em sua thread)
        # enquanto um turno anterior ainda processa no Claude. A seção crítica
        # (`_handle_user_text`: insert + claude) é o que serializa — e agora, com
        # o ticket tirado na entrada, ELA respeita a ordem de chegada (não mais a
        # ordem de conclusão da transcrição, que era a fonte da resposta fora de
        # ordem em rajada voice+text).
        #
        # Feedback imediato: dispara "digitando…" já aqui, antes do download —
        # sem isso o operador via silêncio total durante a transcrição (a voice
        # note some e nada acontece por segundos). É o "primeiro byte rápido" da
        # camada Tempo Real do manual.
        bot = message.get_bot()
        feedback_task = asyncio.create_task(
            _keep_typing(message.chat_id, thread_id, bot)
        )
        download_elapsed = transcribe_elapsed = 0.0
        engine = ""
        try:
            try:
                t0 = time.monotonic()
                tg_file = await media.get_file()
                audio_bytes = bytes(await tg_file.download_as_bytearray())
                download_elapsed = time.monotonic() - t0
                # Transcrição é HTTP síncrono (Groq/AssemblyAI) e leva segundos —
                # roda em thread pra não travar o event loop (e todos os outros
                # tópicos) durante a chamada (SPR P1 #5).
                t1 = time.monotonic()
                text, engine = await asyncio.to_thread(
                    transcriber.transcribe, audio_bytes, filename
                )
                transcribe_elapsed = time.monotonic() - t1
            except TranscriptionError:
                await message.reply_text(
                    "Não consegui transcrever esse áudio. Tenta de novo?",
                    message_thread_id=thread_id,
                )
                return
            except Exception:  # noqa: BLE001 — rede/IO do Telegram
                logger.exception("falha baixando áudio do Telegram")
                await message.reply_text(
                    "Tive um problema baixando o áudio. Tenta de novo?",
                    message_thread_id=thread_id,
                )
                return
        finally:
            feedback_task.cancel()
            try:
                await feedback_task
            except asyncio.CancelledError:
                pass

        logger.info(
            "audio_transcribe %s dur=%ss download=%.1fs transcribe=%.1fs engine=%s chars=%d",
            _topic_label(thread_id),
            media.duration,
            download_elapsed,
            transcribe_elapsed,
            engine or "?",
            len(text or ""),
        )

        if not text:
            await message.reply_text(
                "Não consegui entender nada nesse áudio.",
                message_thread_id=thread_id,
            )
            return

        # Aviso de fallback: se o Whisper falhou e AssemblyAI cobriu, operador
        # precisa saber pra contexto (qualidade pode diferir). Usa `engine` do
        # retorno (não o atributo compartilhado), seguro sob concorrência.
        if engine == "assemblyai-fallback":
            try:
                await message.reply_text(
                    "⚠️ Áudio transcrito via <b>AssemblyAI</b> (fallback — "
                    "Whisper/Groq indisponível). Pode haver pequenas "
                    "diferenças de transcrição em relação ao usual.",
                    message_thread_id=thread_id,
                    parse_mode="HTML",
                )
            except Exception:  # noqa: BLE001 — aviso é nice-to-have
                logger.warning("falha enviando aviso de fallback", exc_info=True)

        # Espera a vez do ticket — entra na seção crítica na ordem de chegada.
        # A transcrição (acima) já rodou em paralelo; aqui só serializa o
        # insert + claude, que é onde a ordem realmente importa.
        await ticket.wait_turn()
        await _handle_user_text(
            message=message,
            text=text,
            audio_transcribed=True,
            config=config,
            db=db,
            claude=claude,
            plugins=plugins,
        )
    finally:
        await ticket.complete()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rede de segurança global: qualquer exceção não-tratada num turno.

    Registrado via `app.add_error_handler`. Sem isto, uma exceção que escapa
    de `on_text`/`on_voice`/comandos (ex.: `httpx` Server disconnected no meio
    do turno, falha de DB ao montar o prompt) só ia pro log e o operador ficava
    no escuro — o turno morria calado, dando sensação de que o Hal ignora.

    Aqui não suprimimos nada: isto é ERRO, não o aviso enlatado de background.
    Mesmo que o Hal já tenha ackado no turno, vale avisar que travou e pedir
    reenvio — o ack prometeu uma resposta que não vai chegar. Best-effort: se
    nem o aviso sai (Telegram fora), só loga.
    """
    logger.error(
        "turno morreu com exceção não-tratada", exc_info=context.error
    )

    # Só dá pra avisar se o update é uma mensagem de um usuário autorizado.
    if not isinstance(update, Update):
        return
    message = update.effective_message
    if message is None:
        return
    config: Optional[Config] = context.application.bot_data.get("config")
    if config is not None and not _user_authorized(update, config.allowed_user_ids):
        return

    try:
        await message.reply_text(
            "🔴 Travei processando isso aqui — reenvia, por favor.",
            message_thread_id=message.message_thread_id,
        )
    except Exception:  # noqa: BLE001 — aviso é best-effort; não relança
        logger.warning("falha enviando aviso de turno travado", exc_info=True)


# Sentinela retornada por `_triagem_missao_se_ativa` quando o
# orquestrador respondeu à msg do operador — caller deve encerrar
# sem chamar o Hal. Valor escolhido pra não colidir com nenhum prompt
# legítimo (contém NUL).
_TRIAGEM_RESPONDEU = "\x00MISSAO_RESPONDEU\x00"


async def _triagem_missao_se_ativa(
    *,
    kobe_home: Path,
    bot_token: str,
    chat_id: int,
    thread_id: Optional[int],
    texto: str,
) -> Optional[str]:
    """Decide o destino da msg quando há missão ativa no tópico (v0.13).

    Retorno:
    - `None` — não há missão ativa, segue fluxo normal pro Hal.
    - `_TRIAGEM_RESPONDEU` — orquestrador tratou; encerra turno.
    - string `[Missão ativa: <id> — "<obj>"]` — rouea pro Hal com essa
      linha extra de ciência (msg não era sobre a missão).
    """
    ativa = missoes_storage.find_missao_ativa(kobe_home, chat_id, thread_id)
    if ativa is None:
        return None

    # Chamada síncrona ao orquestrador. Vai bloquear o handler do
    # tópico por até TIMEOUT_TRIAGEM_S (90s) — aceitável: lock por
    # tópico já serializa, então é só um delay. Rodamos em thread
    # pra não pendurar o loop asyncio.
    loop = asyncio.get_running_loop()
    try:
        decisao = await loop.run_in_executor(
            None,
            lambda: missoes_orquestrador.triar_mensagem_sincrono(
                kobe_home=kobe_home,
                missao_id=ativa.id,
                mensagem_operador=texto,
                bot_token=bot_token,
                chat_id=chat_id,
                thread_id=thread_id,
            ),
        )
    except Exception:  # noqa: BLE001 — fail-safe: deixa o Hal responder
        logger.exception("triagem missao falhou — fallback pro Hal")
        objetivo_curto = (ativa.objetivo or "")[:80]
        return f'[Missão ativa: {ativa.id} — "{objetivo_curto}"]'

    if decisao == "related":
        return _TRIAGEM_RESPONDEU
    # decisao == "not_related"
    objetivo_curto = (ativa.objetivo or "")[:80]
    return f'[Missão ativa: {ativa.id} — "{objetivo_curto}"]'


def _quoted_media_label(reply: Message) -> str:
    """Rótulo curto pra uma mensagem citada que é mídia sem texto."""
    for attr, label in (
        ("photo", "[foto]"),
        ("voice", "[áudio]"),
        ("audio", "[áudio]"),
        ("video", "[vídeo]"),
        ("video_note", "[vídeo]"),
        ("document", "[documento]"),
        ("sticker", "[sticker]"),
        ("animation", "[GIF]"),
        ("location", "[localização]"),
    ):
        if getattr(reply, attr, None):
            return label
    return "[mídia]"


def _extract_quoted_message(message: Message) -> Optional[str]:
    """Conteúdo da mensagem CITADA (reply do Telegram), pronto pro prompt.

    Quando o operador responde citando uma mensagem anterior e emenda texto
    novo, a citada é o tema principal do turno — o bot precisa injetá-la no
    prompt (vide build_prompt). Retorna None quando não há reply real.

    Guarda contra o falso-positivo de forum topic: a mensagem-raiz de criação
    do tópico aparece como `reply_to_message` em alguns clientes; o id dela é o
    próprio `message_thread_id`. Esse caso não é citação — é só o tópico.
    """
    reply = message.reply_to_message
    if reply is None:
        return None
    if getattr(reply, "forum_topic_created", None):
        return None
    if (
        message.message_thread_id is not None
        and reply.message_id == message.message_thread_id
    ):
        return None

    content = (reply.text or reply.caption or "").strip()
    if not content:
        content = _quoted_media_label(reply)
    if not content:
        return None

    # Trunca citada gigante pra não estourar o prompt (a emenda nova é o foco).
    max_len = 2000
    if len(content) > max_len:
        content = content[:max_len].rstrip() + " […citação truncada]"

    # De quem é a citada: o próprio agente (Hal) ou o operador/terceiro? Ajuda
    # o agente a se situar ("você disse X" vs "operador disse X").
    author = reply.from_user
    if author is not None and author.is_bot:
        return f'(mensagem sua, anterior) "{content}"'
    return f'"{content}"'


async def _handle_user_text(
    *,
    message: Message,
    text: str,
    audio_transcribed: bool,
    config: Config,
    db: Client,
    claude: ClaudeRunner,
    plugins: list[Plugin],
) -> None:
    """Caminho comum: persiste user msg, chama Claude, persiste e responde."""
    thread_id = message.message_thread_id
    topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
    session_id = ensure_active_session(db, topic_id)

    # New Chat Manager (2026-06-01): o turno NÃO classifica mais. O
    # detector síncrono saiu do caminho crítico (era a fornalha de
    # latência). Toda a inteligência (embedding, detecção de borda, tags)
    # roda atrás, no daemon Keyko (bot.chat_manager.source.ClassifierSource),
    # disparada por debounce de silêncio. Aqui, com a flag ligada, o turno
    # só: (1) toca o sinal de atividade pro daemon saber que há lote novo;
    # (2) lê os ponteiros residentes que o daemon já mastigou. Isso tudo é
    # síncrono e barato — sem rede de LLM/embedding antes do 1º byte.
    chat_manager_section: Optional[str] = None
    conversation_summaries: list[dict] = []
    if config.chat_manager_enabled:
        try:
            cm_activity.touch_activity(
                config.kobe_home,
                topic_id=topic_id,
                chat_id=message.chat_id,
                thread_id=thread_id,
            )
        except Exception:  # noqa: BLE001 — sinal é best-effort
            logger.warning("chat_manager: touch_activity falhou", exc_info=True)

    # Compactação (v0.12) — APENAS no modo de memória legado. Com a memória de
    # trabalho ligada (working_memory), o histórico é reconstruído cru do banco
    # a cada turno (janela imediata, limitada por IMMEDIATE_HARD_CAP), então a
    # compactação é desnecessária E nociva: ela gera um summary via Claude e o
    # injeta como role='system' ([Resumo da sessão anterior]) na cronologia do
    # tópico, que o get_immediate_messages puxa de volta e o agente lê como se
    # fosse fala do operador. Princípio: ponteiro, nunca resumo. Contexto
    # profundo vem do recall sob demanda, não de destilado injetado. (Decisão de
    # MEMÓRIA — desacoplada da flag de CONVERSAS na Frente 0.)
    if not config.working_memory_enabled:
        msg_count = count_messages(db, session_id)
        if msg_count >= config.compact_threshold_messages:
            logger.info(
                "compact: trigger session=%s count=%d threshold=%d",
                session_id,
                msg_count,
                config.compact_threshold_messages,
            )
            async def _notify_compacting() -> None:
                # Sai TÃO LOGO a compactação começa (dentro do compact_session,
                # antes do summary). Tranquiliza o operador: nada se perde, a
                # conversa segue de onde estava — ele só não fica no escuro
                # enquanto o resumo é gerado.
                await message.reply_text(
                    (
                        "📦 A sessão encheu, então tô compactando ela aqui "
                        "rapidinho — gerando um resumo do que a gente já "
                        "conversou. Não perco nada: já volto continuando "
                        "exatamente de onde a gente parou."
                    ),
                    message_thread_id=thread_id,
                )

            new_session = await compact_session(
                db=db,
                claude=claude,
                topic_id=topic_id,
                session_id=session_id,
                chat_id=message.chat_id,
                thread_id=thread_id,
                bot_token=config.telegram_bot_token,
                on_start=_notify_compacting,
            )
            if new_session is not None:
                session_id = new_session

    # Snapshot do histórico ANTES de inserir a nova mensagem — assim ela
    # não aparece duplicada no prompt (uma vez como histórico, outra como
    # "mensagem nova"). É também o ponto natural pra cortar a janela.
    # SPR P1 #5: histórico (Supabase) e contexto do tópico (slug no
    # Supabase + leitura de arquivos) são independentes e só-leitura —
    # rodam em paralelo, em threads, pra não serializar nem travar o loop.
    # supabase-py usa httpx.Client (thread-safe pra requests concorrentes).
    async def _load_history() -> list[dict]:
        # Camada IMEDIATA (working_memory on): últimos ~10 min OU N msgs DESTE
        # tópico, verbatim — reconstruída do disco a cada turno, então a
        # compactação vira não-evento (doc §6). Off: histórico da session
        # (legado). Decisão de MEMÓRIA, desacoplada da flag de CONVERSAS (Frente 0).
        if config.working_memory_enabled:
            return await asyncio.to_thread(get_immediate_messages, db, topic_id)
        return await asyncio.to_thread(
            get_recent_messages, db, session_id, limit=config.recent_messages_limit
        )

    async def _load_chat_manager() -> tuple[Optional[str], list[dict]]:
        # Ponteiros residentes (quente/frio/relações) + cronologia comprimida
        # das sessions arquivadas da conversation ativa. Tudo já mastigado
        # pelo daemon — o turno só lê e cola. Read-only, barato.
        if not config.chat_manager_enabled:
            return None, []
        try:
            section = await asyncio.to_thread(
                render_chat_manager_section, db, topic_id
            )
            active_conv = await asyncio.to_thread(
                get_active_conversation_for_topic, db, topic_id
            )
            summaries: list[dict] = []
            if active_conv is not None:
                summaries = await asyncio.to_thread(
                    get_conversation_session_summaries,
                    db,
                    active_conv["id"],
                    except_session_id=session_id,
                )
            return section, summaries
        except Exception:  # noqa: BLE001 — CM nunca derruba o turno
            logger.warning("chat_manager: load de contexto falhou", exc_info=True)
            return None, []

    async def _load_topic_ctx() -> tuple[Optional[str], Optional[str]]:
        # Knowledge base do tópico (v0.10): se `user-data/topics/<slug>/`
        # existir, lê prompt.md + knowledge/* e injeta no prompt. Slug é
        # derivado de topics.current_name; quando vazio, seguimos sem KB.
        slug = await asyncio.to_thread(
            get_topic_slug, db, message.chat_id, thread_id
        )
        raw = (
            await asyncio.to_thread(load_topic_context, config.kobe_home, slug)
            if slug
            else None
        )
        return slug, raw

    history, (chat_manager_section, conversation_summaries), (slug, raw_context) = (
        await asyncio.gather(
            _load_history(), _load_chat_manager(), _load_topic_ctx()
        )
    )
    topic_context, truncated = consume_truncated_marker(raw_context)
    if truncated:
        try:
            await message.reply_text(
                (
                    f"⚠️ Conhecimento do tópico <code>{slug}</code> excede o limite "
                    f"({TOPIC_CONTEXT_CHAR_LIMIT} chars) — truncado pra caber no "
                    f"contexto. Considere mover algo pra saved_artifacts via /salvar."
                ),
                message_thread_id=thread_id,
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001 — aviso é nice-to-have, não derrubar fluxo
            logger.warning("falha enviando aviso de truncagem", exc_info=True)

    insert_message(
        db,
        session_id=session_id,
        topic_id=topic_id,
        role="user",
        content=text,
        telegram_message_id=message.message_id,
        audio_transcribed=audio_transcribed,
    )

    # Retain durável (Highlander Frente 2.3): destila fato da mensagem DO
    # OPERADOR (ground truth — ele disse), não da resposta gerada (que pode
    # alucinar). Fire-and-forget: async no servidor + best-effort, não bloqueia
    # nem derruba o turno. Fonte rastreável na metadata (tópico + message_id).
    if config.hindsight_enabled and config.hindsight_retain_enabled:
        # F2 (Highlander v2): agrupa por document_id ESTÁVEL (= sessão) com append —
        # a conversa vira UM documento que cresce, não N memórias soltas (conserta o
        # anti-padrão "UUID aleatório duplica documento"). context/tags melhoram a
        # extração e o isolamento. Conservador: só a msg DO OPERADOR (ground truth).
        _topic = slug or "general"
        _fire_and_forget(
            hindsight_client.retain(
                config.hindsight_base_url,
                hindsight_client.bank_id_for_topic(slug),
                text,
                document_id=hindsight_client.document_id_for_session(session_id),
                context=f"Conversa Telegram, tópico {_topic}",
                tags=[f"topic:{_topic}", "source:telegram"],
                metadata={
                    "topic": _topic,
                    "message_id": message.message_id,
                    "source": "telegram",
                },
                timeout=config.hindsight_timeout_seconds,
            )
        )

    # Triagem de missão (v0.13, decisão 4.1=A): se há missão ativa no
    # tópico, o orquestrador peneira a msg ANTES de chamar o Hal. Se a
    # msg é sobre a missão, o orquestrador já respondeu via kobe-notify
    # e a gente encerra aqui. Se não é, vem com a linha extra de
    # ciência pro Hal saber que existe missão rolando.
    missao_ativa_info = await _triagem_missao_se_ativa(
        kobe_home=config.kobe_home,
        bot_token=config.telegram_bot_token,
        chat_id=message.chat_id,
        thread_id=thread_id,
        texto=text,
    )
    if missao_ativa_info == _TRIAGEM_RESPONDEU:
        # Orquestrador cuidou. A msg do operador já está persistida
        # como 'user'; a resposta do orquestrador foi via kobe-notify
        # direto pro Telegram, não passa pelo histórico — aceitável na
        # Fase 1 (operador vê resposta, sessão fica sem trace dela).
        return

    # Alertas abertos (aguardando confirmação) deste tópico — pro Hal
    # captar um "já marquei" na conversa normal e fechar o ciclo via
    # kobe-alerta. Best-effort; None quando não há alerta aberto aqui.
    alertas_abertos_info = render_alertas_abertos(
        config.kobe_home, message.chat_id, thread_id,
    )

    # Núcleo curado global (Highlander Frente 1.2): identidade + fatos duráveis
    # auto-injetados, atrás da flag. Off = comportamento de hoje. Read-only,
    # best-effort — None se a flag está off ou os arquivos não existem.
    curated_core = (
        load_curated_core(config.kobe_home)
        if config.curated_core_enabled
        else None
    )

    # Sinais de grounding temporais (Highlander Frente 1.1): há quanto tempo foi
    # a última troca neste tópico, computado do histórico já carregado. Atrás da
    # flag; best-effort (None se off ou gap curto).
    grounding_signals = (
        render_grounding_signals(history)
        if config.grounding_signals_enabled
        else None
    )

    # Estado de background vivo (Highlander v2, P1): lê AGORA os arquivos de estado
    # dos trabalhos de background DESTE tópico e injeta o fato vivo + a regra dura —
    # pra o agente não narrar status de sala/job de memória. Read-only, best-effort.
    background_state = (
        render_background_state(config.kobe_home, thread_id)
        if config.background_state_gate_enabled
        else None
    )

    # Memória durável (Highlander Frente 2.3): recall dos fatos relevantes pra
    # esta mensagem, por tópico. Best-effort (None se off, serviço fora, ou nada
    # relevante) — nunca derruba o turno. Adiciona latência só quando ligado.
    durable_memory: Optional[str] = None
    if config.hindsight_enabled and config.hindsight_recall_enabled:
        _recall = await hindsight_client.recall(
            config.hindsight_base_url,
            hindsight_client.bank_id_for_topic(slug),
            text,
            limit=config.hindsight_recall_limit,
            timeout=config.hindsight_timeout_seconds,
        )
        durable_memory = hindsight_client.render_recall_section(_recall)

    prompt = build_prompt(
        thread_id=thread_id,
        history=history,
        new_message=text,
        plugins_section=render_plugins_section(plugins),
        topic_context=topic_context,
        missao_ativa_info=missao_ativa_info,
        alertas_abertos_info=alertas_abertos_info,
        conversation_summaries=conversation_summaries,
        chat_manager_section=chat_manager_section,
        curated_core=curated_core,
        grounding_signals=grounding_signals,
        background_state=background_state,
        durable_memory=durable_memory,
        audio_transcribed=audio_transcribed,
        # Citação extraída da PRÓPRIA mensagem deste turno. Antes do fix de
        # rajada FIFO (card 8b04cf6a), o race do Defeito 1 fazia a msg N+1 ser
        # processada antes da N: a citação grudada na N só chegava no turno da
        # N+1, e o agente respondia "não chegou print nenhum" (citação órfã,
        # Defeito 2). Com o sequenciador FIFO (_TopicGate), a msg N é processada
        # no seu próprio turno, em ordem — e `message` aqui é sempre a msg certa,
        # então a citação cai no turno que de fato a responde. `reply_to_message`
        # é imutável no objeto Message, então extrair aqui (no turno) == extrair
        # na entrada: o que faltava era a ORDEM, não o momento da extração.
        quoted_message=_extract_quoted_message(message),
    )

    bot = message.get_bot()
    run_kwargs = {
        "chat_id": message.chat_id,
        "thread_id": message.message_thread_id,
        "bot_token": config.telegram_bot_token,
    }
    # Com despacho pesado ligado, QUALQUER turno que rode além de
    # `heavy_promote_after_seconds` (≈12s) é promovido pra background — ou seja,
    # todo turno que de fato corre longo termina no caminho pesado. Por isso o
    # teto maior vale pra TODA run aqui (foreground-que-pode-promover e bg de
    # previsão): turno leve termina antes do teto e nunca o toca; turno pesado
    # precisa do fôlego pra não chegar truncado. Flag off → sem override
    # (default clássico de 300s).
    if config.heavy_dispatch_enabled:
        run_kwargs["timeout_override"] = config.heavy_timeout_seconds

    # ── Despacho de turno pesado (2026-06-04) ─────────────────────────────
    # Com a flag ligada, classifica na ENTRADA se o pedido vai gerar trabalho
    # pesado. Se sim (previsão), avisa o operador e despacha o `claude -p` em
    # background FORA do lock — a linha fica livre pro próximo pedido na hora.
    # Flag off → caminho clássico (foreground inline, lock segurado o turno
    # inteiro), rollback trivial. Ver bot/turn_classifier.py.
    if config.heavy_dispatch_enabled:
        decision = await classify_turn(
            text,
            score_high=config.heavy_score_high,
            score_low=config.heavy_score_low,
        )
        logger.info(
            "heavy_dispatch %s route=%s score=%d mini=%s reason=%s",
            _topic_label(thread_id),
            decision.route,
            decision.score,
            decision.used_mini,
            decision.reason,
        )
        if decision.route == ROUTE_BACKGROUND:
            # Previsão (Fase C): o ACK preferido é o do próprio Hal, na voz
            # dele — a run de bg recebe uma nota de handoff que a manda abrir
            # com um `kobe-notify` que NOMEIA a ação e reler a janela fresca
            # antes de agir. Como isso depende do modelo ackar cedo, armamos um
            # watchdog (ack_watchdog_seconds): se o Hal não ackar a tempo, o
            # código manda o enlatado de piso — ACK confiável garantido sem
            # matar a voz do Hal no caminho feliz. O boundary é o instante do
            # despacho (mensagens daqui pra frente são follow-up).
            bg_prompt = _background_handoff_note(_now_utc_iso()) + "\n\n" + prompt
            asyncio.create_task(
                _run_heavy_in_background(
                    message=message,
                    prompt=bg_prompt,
                    claude=claude,
                    run_kwargs=run_kwargs,
                    db=db,
                    session_id=session_id,
                    topic_id=topic_id,
                    history_len=len(history),
                    ack_watchdog_seconds=config.heavy_ack_fallback_seconds,
                ),
                name=f"heavy-{session_id}",
            )
            return

    # ── Foreground (inline) ───────────────────────────────────────────────
    # Roda o claude como task pra poder aplicar a RETAGUARDA (teto de tempo).
    # O "digitando…" vive DENTRO do context manager: ele GARANTE o cancelamento
    # do loop na saída do bloco — caminho feliz, `return` da promoção OU qualquer
    # exceção — fechando a assimetria com o caminho background, que já tinha
    # essa proteção (bug do "digitando…" fantasma, 2026-06-25).
    async with _typing_indicator(message.chat_id, thread_id, bot):
        reporter = ProgressReporter(
            chat_id=message.chat_id,
            thread_id=thread_id,
            bot=bot,
            reply_to_message_id=message.message_id,
        )
        await reporter.start()
        claude_started_at = time.monotonic()
        claude_task = asyncio.create_task(
            claude.run(prompt, on_event=reporter.on_event, **run_kwargs)
        )

        if config.heavy_dispatch_enabled:
            # Retaguarda: espera o claude até o teto. `shield` garante que o
            # estouro do wait_for NÃO cancela o claude — ele continua rodando.
            try:
                await asyncio.wait_for(
                    asyncio.shield(claude_task),
                    timeout=config.heavy_promote_after_seconds,
                )
            except asyncio.TimeoutError:
                # Estourou o teto segurando o lock → PROMOVE pra background. O
                # claude (que continua em voo, NÃO recomeça) entrega o resultado
                # async quando terminar. Encerra o sinal foreground e libera o
                # lock retornando — o `_typing_indicator` cancela o typing no
                # `return` (o background recria o seu próprio).
                logger.info(
                    "heavy_dispatch %s PROMOVIDO após %.1fs (retaguarda)",
                    _topic_label(thread_id),
                    config.heavy_promote_after_seconds,
                )
                await reporter.finish(delete=True)
                # Aviso de promoção (Fase C, decisão item 0): só mandamos o aviso
                # enlatado se o Hal NÃO avisou nada nesse turno. No caminho normal
                # do b2-ii ele já emitiu o ack ("vou olhar X, já volto") ANTES das
                # ferramentas lentas que estouraram o teto — mandar o enlatado por
                # cima seria aviso duplo. Sem ack (Hal não previu a demora), o
                # enlatado é a rede que evita o operador ficar no escuro. Aqui a
                # run em voo NÃO recomeça (Design X), então ela não tem nota de
                # handoff nem relê janela — só termina e entrega.
                if not reporter.acked:
                    await _send_background_notice(message, promoted=True)
                else:
                    logger.info(
                        "heavy_dispatch %s aviso de promoção SUPRIMIDO (Hal já ackou)",
                        _topic_label(thread_id),
                    )
                asyncio.create_task(
                    _run_heavy_in_background(
                        message=message,
                        prompt=prompt,
                        claude=claude,
                        run_kwargs=run_kwargs,
                        db=db,
                        session_id=session_id,
                        topic_id=topic_id,
                        history_len=len(history),
                        claude_task=claude_task,
                        started_at=claude_started_at,
                        tool_count_fn=lambda: reporter.tool_call_count,
                    ),
                    name=f"heavy-promoted-{session_id}",
                )
                return
            except Exception:  # noqa: BLE001 — erro do claude cai no _resolve abaixo
                pass

        # Concluído dentro do teto (ou flag off): consome, encerra sinais,
        # entrega e persiste — caminho inline de sempre.
        reply_text = await _resolve_claude(
            claude_task,
            started_at=claude_started_at,
            prompt_len=len(prompt),
            history_len=len(history),
            tool_count_fn=lambda: reporter.tool_call_count,
            label="fg",
        )
        # Encerra a status do reporter, mas MANTÉM o "digitando…" vivo DURANTE a
        # entrega. A entrega (conversão markdown→HTML + envio, multi-chunk se a
        # resposta é longa) leva alguns segundos; cancelar o typing antes do envio
        # deixava um buraco de "nada acontecendo" entre a status sumir e a msg
        # chegar (relato Felipe 2026-06-05). O typing renovado cobre essa janela —
        # o `_typing_indicator` só cancela na saída do bloco (após a entrega).
        await reporter.finish(delete=True)

        # Resposta final inteira, de uma vez (sem streaming). A status já foi
        # apagada (delete=True); o "digitando…" segue aceso até o envio terminar.
        sent_message_id = await _send_long_text(message, reply_text)
        insert_message(
            db,
            session_id=session_id,
            topic_id=topic_id,
            role="assistant",
            content=reply_text,
            telegram_message_id=sent_message_id,
        )


# ── Despacho de turno pesado: helpers ─────────────────────────────────────
#
# Modelo de execução (decidido 2026-06-04): trabalho pesado do Hal roda como
# `asyncio.create_task` IN-PROCESS, fora do lock do tópico — mesmo padrão de
# handoff/compactor/resume. Continua sendo um `claude -p` separado rodando
# fora do lock; só não é setsid-detached (como Atrus/Coder via kobe-dispatch).
# Esse modelo é o único coerente com "o promovido não recomeça" (na promoção
# o claude já está em voo — destacar via setsid exigiria matar+relançar) e
# preserva a persistência do reply + log de tokens, que o bot faz no tail
# (kobe-dispatch só captura stdout). Custo aceito: se o bot reiniciar no meio,
# o pesado morre (igual handoff/compactor; o snapshot/resume re-situa no boot).


class _ToolCounter:
    """Contador leve de tool_use pro turno em background (sem UI).

    O ProgressReporter inline manda status pro Telegram; em background a
    gente NÃO quer poluir o chat com status (o aviso de despacho + a resposta
    final são o sinal de vida). Mas ainda queremos o número de tool calls pro
    log `claude_run` — e detectar se o Hal já ackou (chamou `kobe-notify`), pro
    watchdog de ACK do background decidir se manda o aviso enlatado de piso.
    Este callback só conta/detecta — não toca o Telegram.
    """

    def __init__(self) -> None:
        self.count = 0
        # True assim que a run de bg emite um `kobe-notify` — o ack na voz do
        # Hal. Mesma detecção do ProgressReporter.acked (substring cobre
        # `bot/bin/kobe-notify`, path absoluto, `python …`).
        self.acked = False

    def on_event(self, event: dict) -> None:
        if event.get("type") != "assistant":
            return
        msg = event.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            self.count += 1
            if block.get("name") == "Bash":
                cmd = (block.get("input") or {}).get("command") or ""
                if "kobe-notify" in cmd:
                    self.acked = True


async def _resolve_claude(
    claude_task: "asyncio.Task",
    *,
    started_at: float,
    prompt_len: int,
    history_len: int,
    tool_count_fn,
    label: str,
) -> str:
    """Aguarda o `claude.run` (já em voo), mapeia erro → resposta amigável,
    loga a métrica `claude_run`. NUNCA levanta — sempre devolve um texto
    pronto pra enviar (com fallback de resposta vazia). Compartilhado pelos
    caminhos inline (foreground) e background.
    """
    claude_status = "ok"
    error_class = ""
    reply_text = ""
    tok_in = tok_out = cache_read = cache_create = 0
    cost_usd = 0.0
    try:
        result = await claude_task
        reply_text = result.text
        tok_in = result.input_tokens
        tok_out = result.output_tokens
        cache_read = result.cache_read_tokens
        cache_create = result.cache_creation_tokens
        cost_usd = result.cost_usd
    except ClaudeTimeoutError as exc:
        claude_status = "timeout"
        error_class = "ClaudeTimeoutError"
        logger.warning("claude timeout: %s (partial=%d chars)", exc, len(exc.partial_text))
        if exc.partial_text:
            reply_text = (
                exc.partial_text
                + "\n\n_(⚠️ resposta interrompida — estourei o tempo limite no "
                "meio. Me peça pra continuar de onde parei, se quiser.)_"
            )
        else:
            reply_text = (
                "Estourei o tempo limite processando isso. A tarefa era pesada — "
                "tenta quebrar em pedaços menores, ou aumenta CLAUDE_TIMEOUT_SECONDS "
                "no .env e me reinicia."
            )
    except ClaudeNotFoundError as exc:
        claude_status = "not_found"
        error_class = "ClaudeNotFoundError"
        logger.error("claude CLI ausente: %s", exc)
        reply_text = (
            "O CLI do Claude não está disponível pro serviço — provavelmente "
            "PATH do systemd ou instalação. Dá uma olhada no log."
        )
    except ClaudeExitError as exc:
        claude_status = f"exit_{exc.returncode}"
        error_class = "ClaudeExitError"
        logger.warning("claude exit=%s", exc.returncode)
        reply_text = (
            "O Claude saiu com erro processando isso. Stderr completo no log "
            "(journalctl --user -u kobe). Tenta de novo?"
        )
    except ClaudeError as exc:
        claude_status = "error"
        error_class = type(exc).__name__
        logger.warning("claude falhou: %s", exc)
        reply_text = (
            "Tive um problema te respondendo agora. Tenta de novo em uns segundos?"
        )
    finally:
        elapsed = time.monotonic() - started_at
        try:
            tool_calls = tool_count_fn()
        except Exception:  # noqa: BLE001
            tool_calls = 0
        logger.info(
            "claude_run status=%s label=%s elapsed=%.1fs prompt_len=%d "
            "history_msgs=%d tool_calls=%d reply_len=%d "
            "tokens_in=%d tokens_out=%d cache_read=%d cache_create=%d "
            "cost_usd=%.5f error_class=%s",
            claude_status,
            label,
            elapsed,
            prompt_len,
            history_len,
            tool_calls,
            len(reply_text or ""),
            tok_in,
            tok_out,
            cache_read,
            cache_create,
            cost_usd,
            error_class or "-",
        )

    if not reply_text:
        reply_text = (
            "Resposta vazia do Claude — o stream foi salvo pra diagnóstico. "
            "Procura no log: `journalctl --user -u kobe | grep claude_empty | tail -1` "
            "pra ver onde o dump caiu. Tenta reformular a mensagem?"
        )
    return reply_text


def _now_utc_iso() -> str:
    """Instante atual em ISO 8601 UTC — o boundary da janela de frescor.

    Vira o `since_iso` de `kobe-recall-since` (compara `messages.created_at >
    boundary` no Postgres, timestamptz). O offset explícito `+00:00` torna a
    comparação inequívoca independente do fuso em que cada msg foi gravada.
    """
    return datetime.now(timezone.utc).isoformat()


def _background_handoff_note(boundary_iso: str) -> str:
    """Nota injetada no TOPO do prompt da run de background (previsão, Fase C).

    A run despachada na entrada é um `claude -p` fresco: não participou da
    decisão de ir pro background e não sabe que a linha já está livre pro
    operador. Esta nota é a única forma de ela saber onde está e como se portar
    — avisar na própria voz (não o aviso enlatado; quem narra é o Hal) e reler
    só o delta de mensagens que entrou DEPOIS do despacho antes de agir.

    `boundary_iso` é o instante do despacho (de `_now_utc_iso`); o agente copia
    a string pro `kobe-recall-since`, não inventa data.
    """
    return (
        "[VOCÊ ESTÁ RODANDO EM BACKGROUND — leia isto antes de tudo]\n"
        "\n"
        "Este turno foi roteado pra rodar em segundo plano porque o pedido tem "
        "cara de trabalho pesado (varredura, pesquisa, vários passos). Você é "
        "uma run nova: não participou dessa decisão, e a linha já está livre — "
        "o operador pode mandar outras mensagens enquanto você trabalha.\n"
        "\n"
        "Antes de agir, nesta ordem:\n"
        "\n"
        "1. Avise o operador NA SUA VOZ que pegou o pedido e já vai atrás — um "
        "`bot/bin/kobe-notify` curto que NOMEIA o que você vai fazer (não um "
        "\"ok\" genérico). É o sinal de que o trabalho começou; sem ele o "
        "operador fica no escuro.\n"
        "   Ex.: bot/bin/kobe-notify \"Vou varrer o repo e cruzar com X — já te volto.\"\n"
        "\n"
        "2. Releia a janela de frescor — o que o operador disse DEPOIS que este "
        "pedido foi despachado (follow-up, correção, \"deixa pra lá\") — rodando "
        "exatamente:\n"
        f"   bot/bin/kobe-recall-since '{boundary_iso}'\n"
        "   Copie esse timestamp como está; não invente data. Se vier algo que "
        "muda ou cancela o pedido, respeite antes de seguir.\n"
        "\n"
        "3. Faça o trabalho e entregue a resposta completa normalmente — sua "
        "resposta final é enviada ao operador quando você terminar."
    )


async def _send_background_notice(message: Message, *, promoted: bool) -> None:
    """Avisa o operador que o turno foi pro background. Best-effort.

    `promoted=False` — previsão (a cascata cravou pesado na entrada).
    `promoted=True` — retaguarda (estourou o teto de tempo segurando a linha).
    Mesmo tom do Atrus/Coder: operador nunca fica no escuro, e a linha já
    está livre pro próximo pedido.
    """
    if promoted:
        texto = (
            "⏳ Isso aqui tá rendendo mais que o normal — passei pra rodar em "
            "background pra não te segurar. Já te respondo aqui assim que ficar "
            "pronto; pode mandar o próximo nesse meio tempo."
        )
    else:
        texto = (
            "⏳ Isso aqui vai demorar um pouco, então coloquei pra rodar em "
            "background. Já te respondo por aqui quando terminar — a linha tá "
            "livre, pode seguir mandando."
        )
    try:
        await message.reply_text(texto, message_thread_id=message.message_thread_id)
    except Exception:  # noqa: BLE001 — aviso é nice-to-have, não derruba o despacho
        logger.warning("falha enviando aviso de background", exc_info=True)


async def _run_heavy_in_background(
    *,
    message: Message,
    prompt: str,
    claude: ClaudeRunner,
    run_kwargs: dict,
    db: Client,
    session_id: str,
    topic_id: str,
    history_len: int,
    claude_task: "Optional[asyncio.Task]" = None,
    started_at: Optional[float] = None,
    tool_count_fn=None,
    ack_watchdog_seconds: Optional[float] = None,
) -> None:
    """Roda (previsão) ou consome (promoção) o turno pesado fora do lock e
    entrega o resultado de forma assíncrona. NUNCA levanta (fire-and-forget).

    - Previsão: `claude_task` é None → lança um `claude.run` novo agora, com
      um contador de tools sem UI. `ack_watchdog_seconds` arma o piso de ACK.
    - Promoção: `claude_task` é o claude JÁ EM VOO → consome (não recomeça);
      `tool_count_fn` lê o contador do reporter inline que ficou pra trás. O
      aviso enlatado já foi resolvido no handler (supressão por reporter.acked),
      então aqui `ack_watchdog_seconds` é None.
    """
    detector: Optional[_ToolCounter] = None
    if claude_task is None:
        detector = _ToolCounter()
        started_at = time.monotonic()
        claude_task = asyncio.create_task(
            claude.run(prompt, on_event=detector.on_event, **run_kwargs)
        )
        tool_count_fn = lambda: detector.count  # noqa: E731

    # "digitando…" vivo durante TODO o trabalho em background + a entrega.
    # Sem isso, o tail do bg fica sem nenhum sinal de vida — e no promote com
    # aviso suprimido (Hal já ackou) o operador via ~minuto de silêncio total
    # entre o promote e a resposta (relato Felipe 2026-06-05). O typing faz a
    # ponte de ponta a ponta, mantendo a supressão correta (sem aviso duplo).
    bot = message.get_bot()
    typing_task = asyncio.create_task(
        _keep_typing(message.chat_id, message.message_thread_id, bot)
    )

    # Watchdog de ACK (previsão): o ACK na voz do Hal é o caminho preferido —
    # ele nomeia a ação ("vou varrer o repo e cruzar com X"), coisa que aviso
    # enlatado não faz. Mas como depende do modelo chamar `kobe-notify` cedo, às
    # vezes não sai. O watchdog é a rede: se em `ack_watchdog_seconds` o Hal não
    # ackou e a run ainda corre, manda o enlatado de piso — operador nunca fica
    # no escuro. Se o Hal ackou (detector.acked) ou a run já terminou, suprime
    # (sem aviso duplo). Mirror da supressão por reporter.acked do caminho de
    # promoção.
    watchdog_task: Optional[asyncio.Task] = None
    if ack_watchdog_seconds is not None and detector is not None:
        async def _ack_watchdog() -> None:
            try:
                await asyncio.sleep(ack_watchdog_seconds)
            except asyncio.CancelledError:
                return
            if not detector.acked and not claude_task.done():
                await _send_background_notice(message, promoted=False)

        watchdog_task = asyncio.create_task(_ack_watchdog())

    try:
        try:
            reply_text = await _resolve_claude(
                claude_task,
                started_at=started_at or time.monotonic(),
                prompt_len=len(prompt),
                history_len=history_len,
                tool_count_fn=tool_count_fn or (lambda: 0),
                label="bg",
            )
        except Exception:  # noqa: BLE001 — _resolve já não levanta, mas defensivo
            logger.exception("heavy bg: _resolve_claude levantou inesperadamente")
            return
        finally:
            # Resposta pronta (ou erro): cancela o watchdog ANTES de entregar.
            # Se a run terminou rápido (< janela do watchdog), não faz sentido
            # mandar o enlatado de piso por cima da resposta que já vai sair.
            if watchdog_task is not None:
                watchdog_task.cancel()

        try:
            sent_message_id = await _send_long_text(message, reply_text)
            insert_message(
                db,
                session_id=session_id,
                topic_id=topic_id,
                role="assistant",
                content=reply_text,
                telegram_message_id=sent_message_id,
            )
        except Exception:  # noqa: BLE001 — não deixa task morrer sem trace
            logger.exception("heavy bg: falha entregando/persistindo reply")
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass


@contextlib.asynccontextmanager
async def _typing_indicator(chat_id: int, thread_id: Optional[int], bot):
    """Mantém o "digitando…" vivo dentro do bloco e GARANTE o cancelamento na
    saída — sucesso OU exceção. Espelha a proteção try/finally que o caminho
    background (`_run_heavy_in_background`) já tinha; sem isso, um crash no
    foreground (ex.: `_resolve_claude` re-levantando um erro não-`ClaudeError`,
    ou falha em `_send_long_text`) deixava o loop `_keep_typing` órfão,
    reemitindo "digitando…" pra sempre até o bot reiniciar (bug 2026-06-25)."""
    task = asyncio.create_task(_keep_typing(chat_id, thread_id, bot))
    try:
        yield task
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def _keep_typing(chat_id: int, thread_id: Optional[int], bot) -> None:
    """Reemite "digitando…" a cada poucos segundos até ser cancelada."""
    try:
        while True:
            try:
                await bot.send_chat_action(
                    chat_id=chat_id,
                    action=ChatAction.TYPING,
                    message_thread_id=thread_id,
                )
            except Exception:  # noqa: BLE001 — rede do Telegram, não crítico
                logger.debug("falha enviando chat_action; ignorando", exc_info=True)
            await asyncio.sleep(TYPING_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        return


async def _send_long_text(message: Message, text: str) -> Optional[int]:
    """Envia texto possivelmente longo, fatiando no limite do Telegram.

    Converte markdown padrão → HTML supported pelo Telegram antes de
    enviar, e usa `parse_mode="HTML"`. Texto sem markdown passa quase
    intacto (só com escape de `&<>`).

    O split ocorre ANTES da conversão pra HTML — assim o limite de
    bytes é avaliado no texto fonte (markdown), o que evita estourar o
    limite por causa de tags HTML adicionadas. Se um chunk cortar
    exatamente no meio de `**bold**`, vira texto plain naquele chunk
    (sem renderização) — perda aceitável, raro acontecer porque o
    split prefere quebras de linha.

    Retorna o `message_id` do ÚLTIMO chunk enviado (referência mais útil
    pra rastrear a resposta no banco).
    """
    thread_id = message.message_thread_id
    chunks = _split_for_telegram(text, TELEGRAM_TEXT_LIMIT)
    last_id: Optional[int] = None
    for chunk in chunks:
        html_chunk = to_telegram_html(chunk)
        try:
            sent = await message.reply_text(
                html_chunk,
                message_thread_id=thread_id,
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001 — HTML inválido (tag órfã etc) → fallback plain
            logger.warning(
                "falha enviando como HTML; caindo pra texto plain",
                exc_info=True,
            )
            sent = await message.reply_text(chunk, message_thread_id=thread_id)
        if sent is not None:
            last_id = sent.message_id
    return last_id


def _split_for_telegram(text: str, limit: int) -> list[str]:
    """Quebra `text` em pedaços <= `limit`, preferindo quebras de linha."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _audio_filename(media: Voice | Audio) -> str:
    """Nome com extensão coerente — Whisper usa pra escolher decoder.

    A Groq valida a extensão case-sensitive (`.MP3` é rejeitado), então
    forçamos lowercase no sufixo antes de mandar.
    """
    if isinstance(media, Voice):
        return "voice.ogg"
    if media.file_name:
        stem, dot, ext = media.file_name.rpartition(".")
        return f"{stem}.{ext.lower()}" if dot else media.file_name
    if media.mime_type and "/" in media.mime_type:
        ext = media.mime_type.split("/", 1)[1].lower()
        return f"audio.{ext}"
    return "audio.m4a"


async def on_command_nova(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Arquiva sessão ativa do tópico. Próxima mensagem cria uma nova.

    Dispara também um handoff doc em background (fire-and-forget): a
    sessão arquivada vira `<kobe_home>/.local/handoffs/<slug>/arquivados/
    <data>-<session_id>.md`. `kobe-notify`-like confirma quando termina.
    Skip silencioso pra sessões com < MIN_MESSAGES_FOR_HANDOFF.
    """
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    claude: ClaudeRunner = context.application.bot_data["claude"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    thread_id = message.message_thread_id
    async with _serve(message.chat_id, thread_id):
        topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
        # Captura referências da sessão ANTES de arquivar — o destilador
        # vai precisar de `session_id` e `topic_slug` (que persistem) pra
        # ler messages e gravar no path certo.
        active_before = get_active_session(db, topic_id)
        topic_slug = get_topic_slug(db, message.chat_id, thread_id)

        archived = archive_active_session(db, topic_id)

        # Chat Manager (Fase 7): se habilitado, também marca conversation
        # ativa do topic como dormant. Próxima msg dispara detector que
        # pode reabrir a antiga (se tema continua) ou criar nova.
        conv_dormant_title: Optional[str] = None
        if config.chat_manager_enabled:
            active_conv = get_active_conversation_for_topic(db, topic_id)
            if active_conv is not None:
                db.table("conversations").update({"status": "dormant"}).eq(
                    "id", active_conv["id"]
                ).execute()
                conv_dormant_title = active_conv["title"]

        if archived is None and conv_dormant_title is None:
            reply = "Nada pra arquivar aqui — já está zerado. Manda a próxima."
        elif conv_dormant_title:
            reply = (
                f"Sessão arquivada e conversa '{conv_dormant_title}' fechada. "
                "Próxima mensagem abre nova conversation/session."
            )
        else:
            reply = (
                "Sessão arquivada. Memória ativa zerada — a próxima mensagem abre uma nova."
            )
        logger.info(
            "/nova user=%s %s archived=%s conv_closed=%s",
            update.effective_user.id if update.effective_user else None,
            _topic_label(thread_id),
            archived,
            conv_dormant_title or "-",
        )
        await message.reply_text(reply, message_thread_id=thread_id)

        # Background destilação. Skip se: nada arquivado, sem slug do
        # tópico, ou sessão pequena (essa última checagem ocorre dentro
        # do helper — aqui só evitamos disparar task à toa).
        if archived and topic_slug and active_before is not None:
            session_id = active_before["id"]
            target = archive_path_for_session(
                config.kobe_home, topic_slug, session_id
            )
            asyncio.create_task(
                _destilar_e_gravar_handoff(
                    bot=context.application.bot,
                    db=db,
                    claude=claude,
                    chat_id=message.chat_id,
                    thread_id=thread_id,
                    topic_slug=topic_slug,
                    session_id=session_id,
                    kobe_home=config.kobe_home,
                    target_path=target,
                    confirm_when_done=False,  # /nova é silencioso se pequena
                ),
                name=f"nova-handoff-{session_id}",
            )


async def on_command_contexto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume o estado da memória ativa do tópico (sem chamar LLM)."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    thread_id = message.message_thread_id
    async with _serve(message.chat_id, thread_id):
        topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
        session = get_active_session(db, topic_id)
        if session is None:
            await message.reply_text(
                "Nenhuma sessão ativa neste tópico — a próxima mensagem abre uma.",
                message_thread_id=thread_id,
            )
            return

        session_id = session["id"]
        started_at = session.get("started_at") or "?"
        total = count_messages(db, session_id)
        recent = get_recent_messages(db, session_id, limit=3)

        snippets = []
        for msg in recent:
            role = msg.get("role", "?")
            content = (msg.get("content") or "").strip().replace("\n", " ")
            if len(content) > 140:
                content = content[:140].rstrip() + "…"
            snippets.append(f"• {role}: {content}")

        lines = [
            f"Tópico: {_topic_label(thread_id)}",
            f"Sessão ativa desde {started_at} — {total} mensagem(ns).",
        ]

        # Chat Manager (Fase 7): se habilitado e há conversation ativa,
        # inclui meta (título, idade, qty sessions arquivadas).
        if config.chat_manager_enabled:
            active_conv = get_active_conversation_for_topic(db, topic_id)
            if active_conv is not None:
                conv_started = (active_conv.get("started_at") or "")[:10]
                arquivadas = get_conversation_session_summaries(
                    db, active_conv["id"], except_session_id=session_id
                )
                lines.append("")
                lines.append(
                    f"Conversa: '{active_conv['title']}' (desde {conv_started}, "
                    f"{len(arquivadas)} session(s) arquivada(s))"
                )
            else:
                lines.append("")
                lines.append("Sem conversa ativa ainda — próxima msg pode criar.")

        if snippets:
            lines.append("")
            lines.append("Últimas:")
            lines.extend(snippets)
        await message.reply_text("\n".join(lines), message_thread_id=thread_id)


async def on_command_salvar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consolida a sessão ativa em saved_artifacts. Título = args do comando."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    title = " ".join(context.args).strip() if context.args else ""
    if not title:
        await message.reply_text(
            "Manda o título junto: /salvar <título do artefato>",
            message_thread_id=message.message_thread_id,
        )
        return

    thread_id = message.message_thread_id
    async with _serve(message.chat_id, thread_id):
        topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
        session = get_active_session(db, topic_id)
        if session is None:
            await message.reply_text(
                "Sem sessão ativa pra salvar neste tópico.",
                message_thread_id=thread_id,
            )
            return

        # Buscamos um histórico mais largo que o do prompt — o artefato é a
        # consolidação da sessão inteira (até o teto), não só a janela viva.
        messages = get_recent_messages(db, session["id"], limit=500)
        artifact_id = save_artifact_from_messages(
            db,
            topic_id=topic_id,
            title=title,
            messages=messages,
        )
        if artifact_id is None:
            await message.reply_text(
                "A sessão está vazia — nada pra salvar ainda.",
                message_thread_id=thread_id,
            )
            return

        logger.info(
            "/salvar user=%s %s artifact=%s title=%r",
            update.effective_user.id if update.effective_user else None,
            _topic_label(thread_id),
            artifact_id,
            title,
        )
        await message.reply_text(
            f"Salvo: “{title}”.",
            message_thread_id=thread_id,
        )


async def _destilar_e_gravar_handoff(
    *,
    bot,
    db: Client,
    claude: ClaudeRunner,
    chat_id: int,
    thread_id: Optional[int],
    topic_slug: str,
    session_id: str,
    kobe_home: Path,
    target_path: Path,
    confirm_when_done: bool,
) -> None:
    """Roda destilador em background e grava o resultado em `target_path`.

    Usado pelos dois gatilhos (/handoff e /nova). Notifica via Telegram
    quando termina (se `confirm_when_done`) ou em caso de erro. NUNCA
    levanta exceção (é fire-and-forget) — falhas viram log + msg curta.
    """
    # API do Telegram quer None pro chat raiz (sentinela 0 do banco
    # também vira None aqui — espelha padrão de send_welcome).
    api_thread_id = None if thread_id in (None, 0) else thread_id
    try:
        messages = get_recent_messages(db, session_id, limit=500)
    except Exception:  # noqa: BLE001
        logger.exception("handoff: falha buscando mensagens session=%s", session_id)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text="🔴 Handoff falhou — não consegui ler o histórico da sessão.",
                message_thread_id=api_thread_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("handoff: falha enviando erro pro Telegram")
        return

    if len(messages) < MIN_MESSAGES_FOR_HANDOFF:
        logger.info(
            "handoff skip session=%s msgs=%d (< %d) confirm=%s",
            session_id, len(messages), MIN_MESSAGES_FOR_HANDOFF, confirm_when_done,
        )
        if confirm_when_done:
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"ℹ️ Sessão tem só {len(messages)} mensagem(ns) — "
                        f"sem substância pra destilar (mínimo: {MIN_MESSAGES_FOR_HANDOFF})."
                    ),
                    message_thread_id=api_thread_id,
                )
            except Exception:  # noqa: BLE001
                logger.exception("handoff: falha enviando skip-info")
        return

    try:
        result = await destilar_sessao(messages=messages, runner=claude)
    except DestiladorError as exc:
        logger.exception("handoff: destilador falhou session=%s", session_id)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"🔴 Handoff falhou ao destilar: {exc}",
                message_thread_id=api_thread_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("handoff: falha enviando erro pro Telegram")
        return
    except Exception:  # noqa: BLE001 — fire-and-forget defensivo
        logger.exception("handoff: erro inesperado destilando session=%s", session_id)
        return

    try:
        ensure_topic_handoff_dirs(kobe_home, topic_slug)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(result.markdown, encoding="utf-8")
    except OSError:
        logger.exception("handoff: falha gravando %s", target_path)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"🔴 Handoff destilado mas não consegui gravar em {target_path}.",
                message_thread_id=api_thread_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("handoff: falha enviando erro de IO")
        return

    logger.info(
        "handoff ok session=%s msgs=%d path=%s tokens_in=%d tokens_out=%d cost=$%.4f",
        session_id, len(messages), target_path,
        result.input_tokens, result.output_tokens, result.cost_usd,
    )

    if confirm_when_done:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🟢 Handoff destilado ({len(messages)} msgs, "
                    f"${result.cost_usd:.4f}).\n"
                    f"<code>{target_path}</code>"
                ),
                message_thread_id=api_thread_id,
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001
            logger.exception("handoff: falha enviando confirmação")


async def on_command_handoff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Destila a sessão ativa em um handoff doc em `.local/handoffs/`.

    Roda assíncrono: handler responde "destilando..." imediato e
    `kobe-notify`-like confirma quando termina (via `bot.send_message`
    direto, sem precisar do helper CLI).
    """
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    claude: ClaudeRunner = context.application.bot_data["claude"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    thread_id = message.message_thread_id
    async with _serve(message.chat_id, thread_id):
        topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
        session = get_active_session(db, topic_id)
        if session is None:
            await message.reply_text(
                "Sem sessão ativa neste tópico — nada pra destilar.",
                message_thread_id=thread_id,
            )
            return

        session_id = session["id"]
        total = count_messages(db, session_id)
        if total < MIN_MESSAGES_FOR_HANDOFF:
            await message.reply_text(
                f"Sessão tem só {total} mensagem(ns) — sem substância pra "
                f"destilar (mínimo: {MIN_MESSAGES_FOR_HANDOFF}).",
                message_thread_id=thread_id,
            )
            return

        topic_slug = get_topic_slug(db, message.chat_id, thread_id)
        if not topic_slug:
            await message.reply_text(
                "Tópico sem nome registrado — renomeie no Telegram pra eu "
                "saber onde gravar o handoff.",
                message_thread_id=thread_id,
            )
            return

        kobe_home = config.kobe_home
        # Se já existia handoff ativo, move pra arquivados antes do novo.
        rotated = rotate_active_to_archive(kobe_home, topic_slug, session_id)
        if rotated:
            logger.info("handoff: rotacionou anterior pra %s", rotated)

        target = active_handoff_path(kobe_home, topic_slug)

        logger.info(
            "/handoff user=%s %s session=%s total=%d → %s",
            update.effective_user.id if update.effective_user else None,
            _topic_label(thread_id),
            session_id,
            total,
            target,
        )

        await message.reply_text(
            f"🟡 Destilando {total} mensagens — te aviso quando ficar pronto.",
            message_thread_id=thread_id,
        )

        # Fire-and-forget: handler retorna já, destilador roda em paralelo.
        asyncio.create_task(
            _destilar_e_gravar_handoff(
                bot=context.application.bot,
                db=db,
                claude=claude,
                chat_id=message.chat_id,
                thread_id=thread_id,
                topic_slug=topic_slug,
                session_id=session_id,
                kobe_home=kobe_home,
                target_path=target,
                confirm_when_done=True,
            ),
            name=f"handoff-{session_id}",
        )


async def on_command_retomar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Busca em saved_artifacts (ILIKE — busca semântica fica pro pós-MVP)."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        hint = "Manda o termo: /retomar <palavra-chave>"
        if config.chat_manager_enabled:
            hint += (
                "\n\nDica: pra retomar uma *conversa* (não um artefato salvo), "
                "use /conversa <termo> ou /conversas pra listar com botões."
            )
        await message.reply_text(
            hint,
            message_thread_id=message.message_thread_id,
            parse_mode="Markdown",
        )
        return

    thread_id = message.message_thread_id
    async with _serve(message.chat_id, thread_id):
        results = search_artifacts(db, query)
        if not results:
            text = f"Não achei nenhum *artefato salvo* com “{query}”."
            if config.chat_manager_enabled:
                text += (
                    f" Tenta /conversa {query} pra buscar entre conversas, "
                    f"ou /conversas-global pra ver todas."
                )
            await message.reply_text(
                text,
                message_thread_id=thread_id,
                parse_mode="Markdown",
            )
            return

        lines = [f"Encontrei {len(results)} artefato(s) com “{query}”:", ""]
        for art in results:
            title = art.get("title") or "(sem título)"
            created = art.get("created_at") or ""
            snippet = (art.get("content") or "").strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200].rstrip() + "…"
            lines.append(f"• {title} — {created}")
            if snippet:
                lines.append(f"  {snippet}")
        await message.reply_text("\n".join(lines), message_thread_id=thread_id)


async def send_welcome(
    bot, db: Client, *, chat_id: int, thread_id: Optional[int], topic_id: str
) -> bool:
    """Envia a msg de boas-vindas no tópico e marca `welcomed_at`.

    Idempotente em caller: chame só se `welcomed_at` ainda for NULL.
    Retorna True se enviou, False em caso de falha (já fica logado).
    Pra Telegram API, `thread_id=0` (sentinela do General) vira `None`.
    """
    api_thread_id = None if thread_id in (None, 0) else thread_id
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=WELCOME_MESSAGE,
            message_thread_id=api_thread_id,
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001 — não derrubar o fluxo principal
        logger.exception(
            "falha enviando welcome chat=%s thread=%s topic=%s",
            chat_id,
            thread_id,
            topic_id,
        )
        return False
    try:
        mark_welcomed(db, topic_id)
    except Exception:  # noqa: BLE001
        logger.exception("falha marcando welcomed_at topic=%s", topic_id)
        # msg foi enviada — não desfaz. Operador vê de novo no próximo
        # restart, pior caso. Melhor que perder a msg.
        return True
    return True


async def on_forum_topic_created(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Captura nome do tópico quando ele é criado no Telegram e dispara
    a mensagem de boas-vindas/instruções (v0.11).

    Sem captura, `topics.current_name` fica NULL e a knowledge base por
    tópico (v0.10) não consegue derivar o slug. A msg de boas-vindas é
    enviada uma única vez (controlada por `welcomed_at`).
    """
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or message.forum_topic_created is None:
        return
    thread_id = message.message_thread_id
    if thread_id is None:
        # Telegram sempre manda thread_id pra eventos de tópico — mas
        # se cair sem (formato exótico), pula em silêncio em vez de
        # gravar com a sentinela de "general".
        return
    name = (message.forum_topic_created.name or "").strip()
    if not name:
        return
    try:
        set_topic_name(db, chat_id=message.chat_id, thread_id=thread_id, name=name)
        logger.info(
            "forum_topic_created chat=%s thread=%s name=%r",
            message.chat_id,
            thread_id,
            name,
        )
    except Exception:  # noqa: BLE001 — Supabase indisponível não derruba o bot
        logger.exception("falha gravando nome de tópico criado")
        return

    # Dispara welcome no mesmo evento (tópico recém-criado nunca tem
    # welcomed_at ≠ NULL — não precisa checar). Reusamos ensure_topic
    # pra obter o `topics.id` que set_topic_name acabou de tocar.
    try:
        topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
        await send_welcome(
            message.get_bot(),
            db,
            chat_id=message.chat_id,
            thread_id=thread_id,
            topic_id=topic_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha disparando welcome de tópico criado")


def _extract_text(suffix: str, raw: bytes) -> str:
    """Extrai texto plano de bytes de arquivo, conforme extensão.

    - `.txt`/`.md`: decode UTF-8 (errors='replace')
    - `.pdf`: pypdf concatena page.extract_text() de todas as páginas
    - `.docx`: python-docx concatena texto de parágrafos
    Outras extensões: raise ValueError — o caller pré-filtra.
    """
    if suffix in {".txt", ".md"}:
        return raw.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — alguma página pode ter glyph quebrado
                logger.warning("pypdf: falha extraindo página, pulando", exc_info=True)
        return "\n\n".join(p.strip() for p in parts if p.strip())
    if suffix == ".docx":
        import docx

        doc = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs if p.text and p.text.strip())
    raise ValueError(f"extensão não suportada: {suffix}")


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recebe anexo (.txt/.md/.pdf/.docx) e salva na KB do tópico (v0.11).

    Fluxo:
    1. Valida usuário, extensão, tamanho.
    2. Resolve slug do tópico via `get_topic_slug`. Se NULL, rejeita
       (tópico ainda não tem nome — o operador renomeia/cria primeiro).
    3. Baixa, extrai texto, valida tamanho extraído.
    4. Grava em `user-data/topics/<slug>/knowledge/<basename>.md` com
       header citando origem (filename, data).
    5. Responde no chat com path relativo e tamanho.
    """
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    doc: Optional[Document] = message.document
    if doc is None:
        return

    # Apolo intercept: .vcf / .csv viram importação de contatos (não vão pra KB)
    _fname_lower = (doc.file_name or "").lower()
    if _fname_lower.endswith(".vcf") or _fname_lower.endswith(".csv"):
        from bot.apolo_handlers import on_document_for_apolo
        if await on_document_for_apolo(update, context):
            return

    thread_id = message.message_thread_id
    filename = (doc.file_name or "anexo").strip()
    suffix = Path(filename).suffix.lower()

    if suffix not in UPLOAD_ALLOWED_SUFFIXES:
        await message.reply_text(
            (
                f"Esse tipo de arquivo (<code>{suffix or 'sem extensão'}</code>) "
                f"ainda não rola na base. Aceito: "
                f"<code>.txt</code>, <code>.md</code>, <code>.pdf</code>, "
                f"<code>.docx</code>."
            ),
            message_thread_id=thread_id,
            parse_mode="HTML",
        )
        return

    if doc.file_size and doc.file_size > UPLOAD_MAX_BYTES:
        await message.reply_text(
            (
                f"Arquivo grande demais ({doc.file_size:,} bytes; teto "
                f"{UPLOAD_MAX_BYTES:,}). Divide ele em pedaços e me manda "
                f"separado."
            ),
            message_thread_id=thread_id,
        )
        return

    async with _serve(message.chat_id, thread_id):
        slug = get_topic_slug(db, message.chat_id, thread_id)
        if not slug:
            await message.reply_text(
                (
                    "Esse tópico ainda não tem nome registrado — manda um texto "
                    "primeiro pra eu reconhecer, ou renomeia o tópico no Telegram."
                ),
                message_thread_id=thread_id,
            )
            return

        user_id = update.effective_user.id if update.effective_user else None
        logger.info(
            "anexo recebido user=%s %s file=%r size=%s",
            user_id,
            _topic_label(thread_id),
            filename,
            doc.file_size,
        )

        try:
            tg_file = await doc.get_file()
            raw = bytes(await tg_file.download_as_bytearray())
        except Exception:  # noqa: BLE001 — rede/IO do Telegram
            logger.exception("falha baixando anexo")
            await message.reply_text(
                "Não consegui baixar esse anexo do Telegram. Tenta de novo?",
                message_thread_id=thread_id,
            )
            return

        try:
            text = _extract_text(suffix, raw).strip()
        except Exception:  # noqa: BLE001 — PDF corrompido, DOCX inválido, etc.
            logger.exception("falha extraindo texto do anexo")
            await message.reply_text(
                (
                    "Não consegui ler o conteúdo desse arquivo (corrompido ou "
                    "formato não suportado internamente). Tenta exportar como "
                    "<code>.md</code> ou <code>.txt</code> e me mandar de novo."
                ),
                message_thread_id=thread_id,
                parse_mode="HTML",
            )
            return

        if not text:
            await message.reply_text(
                (
                    "O arquivo veio, mas não tinha texto extraível dentro "
                    "(talvez PDF de imagem escaneada?). Se for isso, OCR antes "
                    "ou me manda como texto/áudio."
                ),
                message_thread_id=thread_id,
            )
            return

        if len(text) > UPLOAD_MAX_EXTRACTED_CHARS:
            await message.reply_text(
                (
                    f"O texto extraído tem {len(text):,} chars (limite "
                    f"{UPLOAD_MAX_EXTRACTED_CHARS:,}). Quebra em pedaços menores "
                    f"e me manda como vários arquivos."
                ),
                message_thread_id=thread_id,
            )
            return

        target = unique_knowledge_path(config.kobe_home, slug, filename)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            body = (
                f"<!-- origem: {filename} (upload via Telegram) -->\n"
                f"\n"
                f"{text}\n"
            )
            target.write_text(body, encoding="utf-8")
        except OSError:
            logger.exception("falha gravando anexo em %s", target)
            await message.reply_text(
                "Não consegui gravar o arquivo na base agora. Olha o log do bot.",
                message_thread_id=thread_id,
            )
            return

        rel = target.relative_to(config.kobe_home)
        await message.reply_text(
            (
                f"✅ Salvo em <code>{rel}</code> ({len(text):,} chars). Já "
                f"entra no contexto deste tópico na próxima mensagem que "
                f"você mandar aqui."
            ),
            message_thread_id=thread_id,
            parse_mode="HTML",
        )


async def on_forum_topic_edited(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Atualiza nome do tópico quando renomeado no Telegram e move a
    pasta da KB pra acompanhar o novo nome (v0.11, Proposta A).

    Renomear no Telegram é a forma natural de o operador "ligar" um
    tópico existente à knowledge base (v0.10) E de "rebatizar" um já
    com KB existente. Pasta `user-data/topics/<old>/` é movida pra
    `<new>/` automaticamente; conflito de slug é detectado e o
    operador é notificado.
    """
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or message.forum_topic_edited is None:
        return
    thread_id = message.message_thread_id
    if thread_id is None:
        return
    name = (message.forum_topic_edited.name or "").strip()
    if not name:
        # Edição que não mudou o nome (mudou ícone, etc.) — Telegram pode
        # omitir o campo. Não há o que persistir.
        return

    try:
        previous_name = set_topic_name(
            db, chat_id=message.chat_id, thread_id=thread_id, name=name
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha gravando nome de tópico editado")
        return

    logger.info(
        "forum_topic_edited chat=%s thread=%s name=%r previous=%r",
        message.chat_id,
        thread_id,
        name,
        previous_name,
    )

    if previous_name is None:
        return  # rename "no-op" (mesmo nome) ou tópico novo — sem pasta antiga pra mover

    old_slug = slugify(previous_name)
    new_slug = slugify(name)
    if not old_slug or not new_slug or old_slug == new_slug:
        return

    status = rename_topic_dir(config.kobe_home, old_slug, new_slug)
    if status == "renamed":
        await message.reply_text(
            (
                f"📁 Pasta da KB renomeada: <code>{old_slug}</code> → "
                f"<code>{new_slug}</code>. Conteúdo preservado."
            ),
            message_thread_id=thread_id,
            parse_mode="HTML",
        )
    elif status == "conflict":
        await message.reply_text(
            (
                f"⚠️ Pasta <code>{new_slug}</code> já existe com conteúdo — "
                f"não movi <code>{old_slug}</code> pra evitar perda. Resolve "
                f"manualmente (merge ou rm) e me avisa."
            ),
            message_thread_id=thread_id,
            parse_mode="HTML",
        )
    elif status == "error":
        await message.reply_text(
            (
                f"⚠️ Falha movendo pasta <code>{old_slug}</code> → "
                f"<code>{new_slug}</code>. Olha o log do bot."
            ),
            message_thread_id=thread_id,
            parse_mode="HTML",
        )
    # "no_source" e "same" são silenciosos — não há o que comunicar


async def on_forum_topic_closed(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Marca tópico como `archived` quando o operador fecha no Telegram.

    Telegram não emite evento de "delete real" — close é o sinal mais
    próximo. Operador reabrir (`forum_topic_reopened`) volta pra 'active'.
    """
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or message.forum_topic_closed is None:
        return
    thread_id = message.message_thread_id
    if thread_id is None:
        return
    try:
        topic_id = set_topic_status(
            db, chat_id=message.chat_id, thread_id=thread_id, status="archived"
        )
        logger.info(
            "forum_topic_closed chat=%s thread=%s topic=%s",
            message.chat_id,
            thread_id,
            topic_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha marcando tópico como archived")


async def on_forum_topic_reopened(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Marca tópico como `active` quando o operador reabre no Telegram."""
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or message.forum_topic_reopened is None:
        return
    thread_id = message.message_thread_id
    if thread_id is None:
        return
    try:
        topic_id = set_topic_status(
            db, chat_id=message.chat_id, thread_id=thread_id, status="active"
        )
        logger.info(
            "forum_topic_reopened chat=%s thread=%s topic=%s",
            message.chat_id,
            thread_id,
            topic_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha marcando tópico como active")


# on_unsupported foi removido: comandos "desconhecidos" agora caem em
# on_text e o agente Claude decide o que fazer (rotear pra subagente de
# plugin que reconhece o comando, ou tratar como texto livre). Os
# CommandHandler registrados primeiro em main.py continuam interceptando
# /nova, /contexto, /salvar, /retomar antes do on_text — então a função
# precisava sumir só pra commands desconhecidos não emitirem "Comando
# desconhecido" e bloquearem plugins como /transcrever.
