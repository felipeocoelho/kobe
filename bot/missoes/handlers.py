"""Handlers de slash commands de Missão.

Registrados em `bot/main.py`. Padrão idêntico ao dos handlers existentes
(on_command_nova, on_command_salvar etc.) pra manter coerência.

Comandos:
- /missao <descrição>   → cria nova missão e acorda o orquestrador (planejar)
- /missao_status        → snapshot do painel da missão ativa do tópico
- /missao_abortar       → mata PIDs das tarefas rodando, marca abortada
- /missao_lista         → lista missões ativas + últimas N concluídas
"""

from __future__ import annotations

import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from supabase import Client
from telegram import Update
from telegram.ext import ContextTypes

from bot.config import Config
from bot.missoes import (
    Missao,
    StatusMissao,
    StatusTarefa,
    Tarefa,
    TipoEvento,
    storage,
)
from bot.missoes import orquestrador, painel


logger = logging.getLogger("kobe.missoes.handlers")

# Quantas concluídas/falhadas mostrar em /missao_lista, por tópico.
LISTA_MAX_HISTORICO = 5


def _user_authorized(update: Update, allowed_ids: frozenset[int]) -> bool:
    user = update.effective_user
    return user is not None and user.id in allowed_ids


# --- /missao -----------------------------------------------------------

async def on_command_missao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cria missão nova e dispara orquestrador pra planejar."""
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    descricao = " ".join(context.args).strip() if context.args else ""
    if not descricao:
        await message.reply_text(
            "Manda a descrição junto: /missao <o que você quer que eu coordene>",
            message_thread_id=message.message_thread_id,
        )
        return

    chat_id = message.chat_id
    thread_id = message.message_thread_id

    # Já tem missão ativa neste tópico? Fase 1: 1 missão ativa por tópico.
    ativa = storage.find_missao_ativa(config.kobe_home, chat_id, thread_id)
    if ativa is not None:
        await message.reply_text(
            f"Já tem uma missão rodando aqui: <b>{ativa.id}</b>\n"
            f"Status: {ativa.status} · {len([t for t in ativa.tarefas if t.status == StatusTarefa.CONCLUIDA.value])}/{len(ativa.tarefas)} tarefa(s)\n"
            f"\n"
            f"Aguarda ela fechar (ou /missao_abortar) antes de abrir outra.",
            message_thread_id=thread_id,
            parse_mode="HTML",
        )
        return

    # Cria id e missão esqueleto
    missao_id = storage.gerar_id(config.kobe_home, descricao)
    agora = storage.now_iso()
    missao = Missao(
        id=missao_id,
        objetivo=descricao,
        criado_em=agora,
        atualizado_em=agora,
        status=StatusMissao.PLANEJADA.value,
        chat_id=chat_id,
        thread_id=thread_id,
        narrativa="Planejando...",
        tarefas=[],
    )
    storage.salvar(config.kobe_home, missao)

    # Posta painel placeholder e guarda o message_id
    texto_inicial = painel.render(missao)
    try:
        painel_msg_id = painel.enviar_painel(
            bot_token=config.telegram_bot_token,
            chat_id=chat_id,
            thread_id=thread_id,
            texto=texto_inicial,
        )
    except painel.TelegramError:
        logger.exception("falha enviando painel inicial — abortando missão %s", missao_id)
        # Remove a missão pra não deixar lixo
        try:
            import shutil
            shutil.rmtree(storage.missao_dir(config.kobe_home, missao_id))
        except OSError:
            pass
        await message.reply_text(
            "Não consegui criar a missão (falha enviando painel). Tenta de novo?",
            message_thread_id=thread_id,
        )
        return

    with storage.mutar(config.kobe_home, missao_id) as m:
        m.painel_msg_id = painel_msg_id

    # Append evento missao-criada — Keyko vai ver e (na próxima volta)
    # ratificar o painel. Também conta como marco, mas como o orquestrador
    # já vai ser disparado aqui, o Keyko só vai detectar a tentativa
    # e respeitar o circuit breaker (chamada dupla controlada).
    storage.append_evento(
        config.kobe_home, missao_id, TipoEvento.MISSAO_CRIADA,
        dados={"objetivo": descricao, "chat_id": chat_id, "thread_id": thread_id},
    )

    # Dispara orquestrador (planejar). Fire-and-forget.
    try:
        orquestrador.acordar_orquestrador(
            kobe_home=config.kobe_home,
            missao_id=missao_id,
            motivo="planejar",
            bot_token=config.telegram_bot_token,
            chat_id=chat_id,
            thread_id=thread_id,
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha disparando orquestrador inicial")
        # Não desfazemos a missão — o operador pode tentar /missao_abortar
        # ou esperar o orquestrador ser re-acordado por outra via.
        await message.reply_text(
            "Missão criada mas falhei disparando o orquestrador. "
            "Veja o log do bot.",
            message_thread_id=thread_id,
        )
        return

    logger.info(
        "/missao user=%s chat=%s thread=%s missao=%s",
        update.effective_user.id if update.effective_user else None,
        chat_id, thread_id, missao_id,
    )
    # Sem reply explícito — o painel já foi postado e o operador vai vê-lo
    # ser atualizado pelo Keyko/orquestrador.


# --- /missao_status ---------------------------------------------------

async def on_command_missao_status(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Snapshot do painel — mensagem nova, NÃO edita o painel vivo."""
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    chat_id = message.chat_id
    thread_id = message.message_thread_id
    ativa = storage.find_missao_ativa(config.kobe_home, chat_id, thread_id)
    if ativa is None:
        await message.reply_text(
            "Nenhuma missão ativa neste tópico.",
            message_thread_id=thread_id,
        )
        return

    snapshot = painel.render(ativa)
    await message.reply_text(snapshot, message_thread_id=thread_id)


# --- /missao_abortar --------------------------------------------------

async def on_command_missao_abortar(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Mata PIDs das tarefas rodando, marca missão como abortada."""
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    chat_id = message.chat_id
    thread_id = message.message_thread_id
    ativa = storage.find_missao_ativa(config.kobe_home, chat_id, thread_id)
    if ativa is None:
        await message.reply_text(
            "Nenhuma missão ativa neste tópico pra abortar.",
            message_thread_id=thread_id,
        )
        return

    # Mata os PIDs das tarefas rodando
    pids_mortos: list[int] = []
    pids_zumbis: list[int] = []
    for t in ativa.tarefas:
        if t.status == StatusTarefa.RODANDO.value and t.pid:
            try:
                os.kill(t.pid, signal.SIGTERM)
                pids_mortos.append(t.pid)
            except ProcessLookupError:
                pids_zumbis.append(t.pid)
            except PermissionError:
                logger.warning("sem permissão pra matar pid %s", t.pid)

    # Marca como abortada
    with storage.mutar(config.kobe_home, ativa.id) as m:
        m.status = StatusMissao.ABORTADA.value
        m.narrativa = "Missão abortada pelo operador."
        for t in m.tarefas:
            if t.status == StatusTarefa.RODANDO.value:
                t.status = StatusTarefa.FALHOU.value
                t.erro = "abortada pelo operador"
                t.terminado_em = storage.now_iso()

    storage.append_evento(
        config.kobe_home, ativa.id, TipoEvento.MISSAO_ABORTADA,
        dados={"pids_mortos": pids_mortos, "pids_zumbis": pids_zumbis},
    )

    msg = f"⏸️ Missão <b>{ativa.id}</b> abortada."
    if pids_mortos:
        msg += f"\nMatei {len(pids_mortos)} processo(s) em execução."
    if pids_zumbis:
        msg += f"\n{len(pids_zumbis)} PID(s) já tinham terminado."
    await message.reply_text(msg, message_thread_id=thread_id, parse_mode="HTML")
    logger.info(
        "/missao_abortar user=%s missao=%s pids_mortos=%s pids_zumbis=%s",
        update.effective_user.id if update.effective_user else None,
        ativa.id, pids_mortos, pids_zumbis,
    )


# --- /missao_lista ----------------------------------------------------

async def on_command_missao_lista(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Lista missões ativas neste tópico + últimas N concluídas/falhadas."""
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    chat_id = message.chat_id
    thread_id = message.message_thread_id
    todas = storage.listar_missoes(
        config.kobe_home, chat_id=chat_id, thread_id=thread_id,
    )
    ativas = [m for m in todas if not m.is_terminal()]
    terminais = [m for m in todas if m.is_terminal()]
    # Ordena terminais por atualizado_em desc, pega últimas N
    terminais.sort(key=lambda m: m.atualizado_em, reverse=True)
    historico = terminais[:LISTA_MAX_HISTORICO]

    glyph_status = {
        "planejada": "🟡", "em-andamento": "▶️",
        "concluida": "🟢", "falhou": "🔴", "abortada": "⏸️",
    }

    linhas: list[str] = []
    if ativas:
        linhas.append("🎯 Ativas:")
        for m in ativas:
            c = sum(1 for t in m.tarefas if t.status == "concluida")
            total = len(m.tarefas)
            linhas.append(
                f"  {glyph_status.get(m.status, '•')} {m.id} — {c}/{total} · "
                f"{m.objetivo[:60]}"
            )
    if historico:
        linhas.append("")
        linhas.append(f"📜 Últimas {len(historico)} encerradas:")
        for m in historico:
            linhas.append(
                f"  {glyph_status.get(m.status, '•')} {m.id} — {m.objetivo[:60]}"
            )
    if not linhas:
        linhas.append("Nenhuma missão neste tópico ainda.")

    await message.reply_text("\n".join(linhas), message_thread_id=thread_id)
