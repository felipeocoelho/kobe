"""Handlers do Telegram (camada de transporte).

Recebe updates, autoriza usuário, persiste no Supabase, dispara `claude -p`
com histórico da sessão e devolve a resposta no mesmo tópico. Áudio passa
por Groq Whisper antes — daí em diante o pipeline é igual ao texto.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from supabase import Client
from telegram import Audio, Message, Update, Voice
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
from bot.config import Config
from bot.progress import ProgressReporter
from bot.topic_manager import (
    archive_active_session,
    count_messages,
    ensure_active_session,
    ensure_topic,
    get_active_session,
    get_recent_messages,
    insert_message,
)
from bot.transcribe import Transcriber, TranscriptionError


logger = logging.getLogger("kobe.handler")

# Telegram corta mensagens em 4096 caracteres. Mantemos margem pra prefixos
# de continuação ("…") e quebras de linha.
TELEGRAM_TEXT_LIMIT = 4000

# Tempo entre disparos de "digitando…" enquanto o Claude pensa. O efeito
# no Telegram dura ~5s, então 4s mantém a indicação visível sem flicker.
TYPING_INTERVAL_SECONDS = 4


def _user_authorized(update: Update, allowed_ids: frozenset[int]) -> bool:
    user = update.effective_user
    return user is not None and user.id in allowed_ids


def _topic_label(thread_id: Optional[int]) -> str:
    return f"topic={thread_id}" if thread_id is not None else "topic=general"


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    claude: ClaudeRunner = context.application.bot_data["claude"]
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

    await _handle_user_text(
        message=message,
        text=text,
        audio_transcribed=False,
        config=config,
        db=db,
        claude=claude,
    )


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recebe voice/audio, transcreve via Groq e processa como texto."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    claude: ClaudeRunner = context.application.bot_data["claude"]
    transcriber: Transcriber = context.application.bot_data["transcriber"]
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

    await _handle_user_text(
        message=message,
        text=text,
        audio_transcribed=True,
        config=config,
        db=db,
        claude=claude,
    )


async def _handle_user_text(
    *,
    message: Message,
    text: str,
    audio_transcribed: bool,
    config: Config,
    db: Client,
    claude: ClaudeRunner,
) -> None:
    """Caminho comum: persiste user msg, chama Claude, persiste e responde."""
    thread_id = message.message_thread_id
    topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
    session_id = ensure_active_session(db, topic_id)

    # Snapshot do histórico ANTES de inserir a nova mensagem — assim ela
    # não aparece duplicada no prompt (uma vez como histórico, outra como
    # "mensagem nova"). É também o ponto natural pra cortar a janela.
    history = get_recent_messages(db, session_id, limit=config.recent_messages_limit)

    insert_message(
        db,
        session_id=session_id,
        topic_id=topic_id,
        role="user",
        content=text,
        telegram_message_id=message.message_id,
        audio_transcribed=audio_transcribed,
    )

    prompt = build_prompt(
        thread_id=thread_id,
        history=history,
        new_message=text,
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
    try:
        try:
            reply_text = await claude.run(prompt, on_event=reporter.on_event)
        except ClaudeTimeoutError as exc:
            claude_status = "timeout"
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
            logger.error("claude CLI ausente: %s", exc)
            reply_text = (
                "O CLI do Claude não está disponível pro serviço — provavelmente "
                "PATH do systemd ou instalação. Dá uma olhada no log."
            )
        except ClaudeExitError as exc:
            # stderr já foi logado dentro do runner com detalhe completo.
            claude_status = f"exit_{exc.returncode}"
            logger.warning("claude exit=%s", exc.returncode)
            reply_text = (
                "O Claude saiu com erro processando isso. Stderr completo no log "
                "(journalctl --user -u kobe). Tenta de novo?"
            )
        except ClaudeError as exc:
            # Catch-all pra qualquer subclasse futura ou caso raro.
            claude_status = "error"
            logger.warning("claude falhou: %s", exc)
            reply_text = (
                "Tive um problema te respondendo agora. Tenta de novo em uns segundos?"
            )
    finally:
        elapsed = time.monotonic() - claude_started_at
        # Métrica única por chamada — chave-valor pra grep fácil no journal.
        logger.info(
            "claude_run status=%s elapsed=%.1fs prompt_len=%d "
            "history_msgs=%d tool_calls=%d reply_len=%d",
            claude_status,
            elapsed,
            len(prompt),
            len(history),
            reporter.tool_call_count,
            len(reply_text or ""),
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

    Retorna o `message_id` do ÚLTIMO chunk enviado (referência mais útil
    pra rastrear a resposta no banco).
    """
    thread_id = message.message_thread_id
    chunks = _split_for_telegram(text, TELEGRAM_TEXT_LIMIT)
    last_id: Optional[int] = None
    for chunk in chunks:
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
    """Arquiva sessão ativa do tópico. Próxima mensagem cria uma nova."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    thread_id = message.message_thread_id
    topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
    archived = archive_active_session(db, topic_id)
    if archived is None:
        reply = "Nada pra arquivar aqui — já está zerado. Manda a próxima."
    else:
        reply = "Sessão arquivada. Memória ativa zerada — a próxima mensagem abre uma nova."
    logger.info(
        "/nova user=%s %s archived=%s",
        update.effective_user.id if update.effective_user else None,
        _topic_label(thread_id),
        archived,
    )
    await message.reply_text(reply, message_thread_id=thread_id)


async def on_command_contexto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Resume o estado da memória ativa do tópico (sem chamar LLM)."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    thread_id = message.message_thread_id
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


async def on_command_retomar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Busca em saved_artifacts (ILIKE — busca semântica fica pro pós-MVP)."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await message.reply_text(
            "Manda o termo: /retomar <palavra-chave do que você salvou>",
            message_thread_id=message.message_thread_id,
        )
        return

    thread_id = message.message_thread_id
    results = search_artifacts(db, query)
    if not results:
        await message.reply_text(
            f"Não achei nada com “{query}”.",
            message_thread_id=thread_id,
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


async def on_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all pra comandos não reconhecidos."""
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    await message.reply_text(
        "Comando desconhecido. Disponíveis: /nova, /contexto, /salvar, /retomar.",
        message_thread_id=message.message_thread_id,
    )
