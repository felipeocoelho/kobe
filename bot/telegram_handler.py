"""Handlers do Telegram (camada de transporte).

Recebe updates, autoriza usuário, persiste no Supabase, dispara `claude -p`
com histórico da sessão e devolve a resposta no mesmo tópico. Áudio passa
por Groq Whisper antes — daí em diante o pipeline é igual ao texto.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
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
from bot.plugins import Plugin, render_plugins_section
from bot.progress import ProgressReporter
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
    set_session_conversation,
    set_topic_name,
    set_topic_status,
    slugify,
    topic_knowledge_dir,
    unique_knowledge_path,
)
from bot.transcribe import Transcriber, TranscriptionError


logger = logging.getLogger("kobe.handler")

# Telegram corta mensagens em 4096 caracteres. Mantemos margem pra prefixos
# de continuação ("…") e quebras de linha.
TELEGRAM_TEXT_LIMIT = 4000

# Tempo entre disparos de "digitando…" enquanto o Claude pensa. O efeito
# no Telegram dura ~5s, então 4s mantém a indicação visível sem flicker.
TYPING_INTERVAL_SECONDS = 4

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


# Locks por tópico (chat_id, thread_id) — base do multitasking. Com
# `concurrent_updates(True)` em main.py, o PTB despacha updates em
# paralelo; o lock garante que dentro de um mesmo tópico só roda um
# handler por vez (preserva ordem das mensagens, evita race em
# user-data/, inserção fora de ordem no Supabase e disparos duplos de
# compactação). Mensagens em tópicos diferentes correm em paralelo.
#
# O dict cresce indefinidamente (uma entrada por tópico tocado). Em
# escala atual (poucos tópicos por operador) é irrelevante; se virar
# problema, vale TTL.
_topic_locks: dict[tuple[int, Optional[int]], asyncio.Lock] = {}


def _get_topic_lock(chat_id: int, thread_id: Optional[int]) -> asyncio.Lock:
    key = (chat_id, thread_id)
    lock = _topic_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _topic_locks[key] = lock
    return lock


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

    lock = _get_topic_lock(message.chat_id, message.message_thread_id)
    async with lock:
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
    user_id = update.effective_user.id if update.effective_user else None
    filename = _audio_filename(media)
    logger.info(
        "áudio recebido user=%s %s file=%s dur=%ss",
        user_id,
        _topic_label(thread_id),
        filename,
        media.duration,
    )

    # Lock pego ANTES da transcrição: se chegam voice+text rápidos no
    # mesmo tópico, sem isso o text poderia passar à frente do voice
    # (transcrição leva segundos). Com lock, a ordem de updates do PTB
    # é preservada dentro do tópico.
    lock = _get_topic_lock(message.chat_id, thread_id)
    async with lock:
        try:
            tg_file = await media.get_file()
            audio_bytes = bytes(await tg_file.download_as_bytearray())
            text = transcriber.transcribe(audio_bytes, filename)
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

        if not text:
            await message.reply_text(
                "Não consegui entender nada nesse áudio.",
                message_thread_id=thread_id,
            )
            return

        # Aviso de fallback: se o Whisper falhou e AssemblyAI cobriu,
        # operador precisa saber pra contexto (qualidade pode diferir).
        if getattr(transcriber, "last_engine_used", "") == "assemblyai-fallback":
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

        await _handle_user_text(
            message=message,
            text=text,
            audio_transcribed=True,
            config=config,
            db=db,
            claude=claude,
            plugins=plugins,
        )


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

    # Chat Manager (Fase 4): se habilitado, detector decide a qual
    # conversation a msg pertence. Trocar de conversation = arquiva
    # session atual e cria nova vinculada à conversation alvo.
    # Falha aqui é silenciosa — bot continua funcionando como antes.
    conversation_active_info: Optional[dict] = None
    conversation_summaries: list[dict] = []
    if config.chat_manager_enabled:
        try:
            from bot.conversation_detector import detect as _detect_conversation
            detector_result = await _detect_conversation(
                db, topic_id=topic_id, message_text=text
            )
            if detector_result.action != "continue":
                # Arquiva session atual + cria nova vinculada à conversation alvo
                archive_active_session(db, topic_id, status="archived")
                session_id = ensure_active_session(db, topic_id)
            set_session_conversation(db, session_id, detector_result.conversation_id)
            conversation_active_info = get_active_conversation_for_topic(db, topic_id)
            conversation_summaries = get_conversation_session_summaries(
                db, detector_result.conversation_id, except_session_id=session_id
            )
            if detector_result.notice_text:
                try:
                    await message.reply_text(
                        detector_result.notice_text, message_thread_id=thread_id
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "chat_manager: falha enviando notice", exc_info=True
                    )
            logger.info(
                "chat_manager action=%s conv_id=%s confidence=%.3f judge=%s",
                detector_result.action,
                detector_result.conversation_id[:8],
                detector_result.confidence,
                detector_result.judge_used,
            )
        except Exception:  # noqa: BLE001
            logger.exception("chat_manager falhou; seguindo sem ele")

    # Compactação (v0.12): se sessão atingiu limite de mensagens, gera
    # summary via Claude, arquiva como 'compacted' e abre nova com o
    # summary como role='system' inicial. Falha aqui é silenciosa —
    # seguimos na sessão antiga e tentamos de novo na próxima msg.
    msg_count = count_messages(db, session_id)
    if msg_count >= config.compact_threshold_messages:
        logger.info(
            "compact: trigger session=%s count=%d threshold=%d",
            session_id,
            msg_count,
            config.compact_threshold_messages,
        )
        new_session = await compact_session(
            db=db,
            claude=claude,
            topic_id=topic_id,
            session_id=session_id,
            chat_id=message.chat_id,
            thread_id=thread_id,
            bot_token=config.telegram_bot_token,
        )
        if new_session is not None:
            session_id = new_session
            try:
                await message.reply_text(
                    (
                        "📦 Sessão compactada — gerei um resumo do que conversamos "
                        "até aqui e abri sessão nova. Pode seguir."
                    ),
                    message_thread_id=thread_id,
                )
            except Exception:  # noqa: BLE001 — aviso é nice-to-have
                logger.warning("falha enviando aviso de compactação", exc_info=True)

    # Snapshot do histórico ANTES de inserir a nova mensagem — assim ela
    # não aparece duplicada no prompt (uma vez como histórico, outra como
    # "mensagem nova"). É também o ponto natural pra cortar a janela.
    history = get_recent_messages(db, session_id, limit=config.recent_messages_limit)

    # Knowledge base do tópico (v0.10): se `user-data/topics/<slug>/`
    # existir, lê prompt.md + knowledge/* e injeta no prompt. Slug é
    # derivado de topics.current_name; quando vazio, retorna None e
    # seguimos sem KB (operador ainda não rotulou o tópico).
    slug = get_topic_slug(db, message.chat_id, thread_id)
    raw_context = load_topic_context(config.kobe_home, slug) if slug else None
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

    prompt = build_prompt(
        thread_id=thread_id,
        history=history,
        new_message=text,
        plugins_section=render_plugins_section(plugins),
        topic_context=topic_context,
        missao_ativa_info=missao_ativa_info,
        conversation_active=conversation_active_info,
        conversation_summaries=conversation_summaries,
    )

    bot = message.get_bot()
    typing_task = asyncio.create_task(_keep_typing(message.chat_id, thread_id, bot))
    reporter = ProgressReporter(
        chat_id=message.chat_id,
        thread_id=thread_id,
        bot=bot,
        reply_to_message_id=message.message_id,
    )
    await reporter.start()
    claude_started_at = time.monotonic()
    claude_status = "ok"
    error_class = ""
    # Tokens/custo (v0.12): só preenchidos no caminho de sucesso. Em
    # exceção continuam zero — naturalmente diferenciável no log.
    reply_text = ""
    tok_in = tok_out = cache_read = cache_create = 0
    cost_usd = 0.0
    try:
        try:
            result = await claude.run(
                prompt,
                on_event=reporter.on_event,
                chat_id=message.chat_id,
                thread_id=message.message_thread_id,
                bot_token=config.telegram_bot_token,
            )
            reply_text = result.text
            tok_in = result.input_tokens
            tok_out = result.output_tokens
            cache_read = result.cache_read_tokens
            cache_create = result.cache_creation_tokens
            cost_usd = result.cost_usd
        except ClaudeTimeoutError as exc:
            claude_status = "timeout"
            error_class = "ClaudeTimeoutError"
            logger.warning("claude timeout: %s", exc)
            reply_text = (
                "Estourei o tempo limite processando isso. A tarefa era pesada — "
                "tenta quebrar em pedaços menores, ou aumenta CLAUDE_TIMEOUT_SECONDS "
                "no .env e me reinicia."
            )
        except ClaudeNotFoundError as exc:
            # Indica problema de instalação/PATH — não adianta o operador
            # retentar; precisa de intervenção no host.
            claude_status = "not_found"
            error_class = "ClaudeNotFoundError"
            logger.error("claude CLI ausente: %s", exc)
            reply_text = (
                "O CLI do Claude não está disponível pro serviço — provavelmente "
                "PATH do systemd ou instalação. Dá uma olhada no log."
            )
        except ClaudeExitError as exc:
            # stderr já foi logado dentro do runner com detalhe completo.
            claude_status = f"exit_{exc.returncode}"
            error_class = "ClaudeExitError"
            logger.warning("claude exit=%s", exc.returncode)
            reply_text = (
                "O Claude saiu com erro processando isso. Stderr completo no log "
                "(journalctl --user -u kobe). Tenta de novo?"
            )
        except ClaudeError as exc:
            # Catch-all pra qualquer subclasse futura ou caso raro.
            claude_status = "error"
            error_class = type(exc).__name__
            logger.warning("claude falhou: %s", exc)
            reply_text = (
                "Tive um problema te respondendo agora. Tenta de novo em uns segundos?"
            )
    finally:
        elapsed = time.monotonic() - claude_started_at
        # Métrica única por chamada — chave-valor pra grep fácil no journal.
        # tokens/custo só populados no caminho de sucesso; error_class só
        # em falha. Strings sempre presentes pra grep não falhar.
        logger.info(
            "claude_run status=%s elapsed=%.1fs prompt_len=%d "
            "history_msgs=%d tool_calls=%d reply_len=%d "
            "tokens_in=%d tokens_out=%d cache_read=%d cache_create=%d "
            "cost_usd=%.5f error_class=%s",
            claude_status,
            elapsed,
            len(prompt),
            len(history),
            reporter.tool_call_count,
            len(reply_text or ""),
            tok_in,
            tok_out,
            cache_read,
            cache_create,
            cost_usd,
            error_class or "-",
        )
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
        await reporter.finish(delete=True)

    if not reply_text:
        # Claude saiu com sucesso mas sem texto. O runner já dumpou o
        # stream cru pra /tmp/kobe-claude-empty-*.jsonl e logou os
        # tipos de evento vistos — basta um grep pra investigar.
        reply_text = (
            "Resposta vazia do Claude — o stream foi salvo pra diagnóstico. "
            "Procura no log: `journalctl --user -u kobe | grep claude_empty | tail -1` "
            "pra ver onde o dump caiu. Tenta reformular a mensagem?"
        )

    sent_message_id = await _send_long_text(message, reply_text)

    insert_message(
        db,
        session_id=session_id,
        topic_id=topic_id,
        role="assistant",
        content=reply_text,
        telegram_message_id=sent_message_id,
    )


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
    lock = _get_topic_lock(message.chat_id, thread_id)
    async with lock:
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
    lock = _get_topic_lock(message.chat_id, thread_id)
    async with lock:
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
    lock = _get_topic_lock(message.chat_id, thread_id)
    async with lock:
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
    lock = _get_topic_lock(message.chat_id, thread_id)
    async with lock:
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
    lock = _get_topic_lock(message.chat_id, thread_id)
    async with lock:
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

    lock = _get_topic_lock(message.chat_id, thread_id)
    async with lock:
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
