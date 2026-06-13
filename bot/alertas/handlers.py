"""Handlers de slash commands de Alerta.

Registrados em `bot/main.py`. Padrão idêntico aos handlers de Missão.

IMPORTANTE: a CRIAÇÃO de alerta NÃO é slash command — é conversacional
(o operador pede em linguagem natural e o Hal traduz → chama
`bot/bin/kobe-alerta criar`). Estes comandos são só gestão do que já
existe:

- /alerta_lista              → lista alertas deste tópico
- /alerta_pausar <id>        → suspende sem apagar
- /alerta_retomar <id>       → reativa um pausado
- /alerta_apagar <id>        → remove de vez
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import Config
from bot.alertas import StatusAlerta, storage


logger = logging.getLogger("kobe.alertas.handlers")

_GLYPH_STATUS = {
    "ativo": "🟢",
    "aberto": "🔔",
    "confirmado": "✅",
    "expirado": "⌛",
    "pausado": "⏸️",
    "concluido": "🏁",
}


def _user_authorized(update: Update, allowed_ids: frozenset[int]) -> bool:
    user = update.effective_user
    return user is not None and user.id in allowed_ids


def _resumo_agenda(alerta) -> str:
    """Linha curta descrevendo quando o alerta dispara."""
    ag = alerta.agenda
    if ag.is_one_shot:
        return f"uma vez em {ag.quando}"
    partes = [f"abre `{ag.abertura}`"]
    if ag.cobranca:
        partes.append(f"cobra `{ag.cobranca}`")
    if ag.limite:
        partes.append(f"limite `{ag.limite}`")
    return " · ".join(partes)


# --- /alerta_lista -----------------------------------------------------

async def on_command_alerta_lista(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Lista alertas deste tópico (vivos), com status e próximo disparo."""
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return

    chat_id = message.chat_id
    thread_id = message.message_thread_id
    alertas = storage.listar_alertas(
        config.kobe_home, apenas_vivos=True, chat_id=chat_id, thread_id=thread_id,
    )
    if not alertas:
        await message.reply_text(
            "Nenhum alerta neste tópico. Pra criar é só pedir em linguagem "
            "natural — ex.: \"me lembra todo dia 7h de checar a agenda\".",
            message_thread_id=thread_id,
        )
        return

    linhas = ["🔔 <b>Alertas deste tópico:</b>"]
    for a in alertas:
        glyph = _GLYPH_STATUS.get(a.estado.status, "•")
        prox = a.estado.proximo_disparo or "—"
        linhas.append(
            f"\n{glyph} <b>{a.id}</b> · {a.estado.status}"
            f"\n   {a.titulo}"
            f"\n   {_resumo_agenda(a)}"
            f"\n   próximo: {prox}"
        )
    await message.reply_text(
        "\n".join(linhas), message_thread_id=thread_id, parse_mode="HTML",
    )


# --- /alerta_pausar ----------------------------------------------------

async def on_command_alerta_pausar(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    thread_id = message.message_thread_id

    alerta_id = " ".join(context.args).strip() if context.args else ""
    if not alerta_id:
        await message.reply_text(
            "Uso: /alerta_pausar <id>  (veja os ids em /alerta_lista)",
            message_thread_id=thread_id,
        )
        return
    if not storage.existe(config.kobe_home, alerta_id):
        await message.reply_text(
            f"Não achei alerta com id <b>{alerta_id}</b>.",
            message_thread_id=thread_id, parse_mode="HTML",
        )
        return

    try:
        with storage.mutar(config.kobe_home, alerta_id) as a:
            if a.estado.status == StatusAlerta.PAUSADO.value:
                ja_pausado = True
            else:
                ja_pausado = False
                a.estado.status_antes_pausa = a.estado.status
                a.estado.status = StatusAlerta.PAUSADO.value
                a.estado.proximo_disparo = None
                a.estado.proxima_acao = None
    except storage.LockTimeoutError:
        await message.reply_text(
            "Alerta ocupado agora (lock). Tenta de novo em 1s.",
            message_thread_id=thread_id,
        )
        return

    if ja_pausado:
        await message.reply_text(
            f"⏸️ <b>{alerta_id}</b> já estava pausado.",
            message_thread_id=thread_id, parse_mode="HTML",
        )
    else:
        storage.append_evento(config.kobe_home, alerta_id, "pausado")
        await message.reply_text(
            f"⏸️ Alerta <b>{alerta_id}</b> pausado. Retoma com "
            f"/alerta_retomar {alerta_id}.",
            message_thread_id=thread_id, parse_mode="HTML",
        )


# --- /alerta_retomar ---------------------------------------------------

async def on_command_alerta_retomar(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    thread_id = message.message_thread_id

    alerta_id = " ".join(context.args).strip() if context.args else ""
    if not alerta_id:
        await message.reply_text(
            "Uso: /alerta_retomar <id>", message_thread_id=thread_id,
        )
        return
    if not storage.existe(config.kobe_home, alerta_id):
        await message.reply_text(
            f"Não achei alerta com id <b>{alerta_id}</b>.",
            message_thread_id=thread_id, parse_mode="HTML",
        )
        return

    try:
        with storage.mutar(config.kobe_home, alerta_id) as a:
            if a.estado.status != StatusAlerta.PAUSADO.value:
                nao_pausado = True
            else:
                nao_pausado = False
                # Restaura o estado de antes da pausa (default ativo).
                a.estado.status = (
                    a.estado.status_antes_pausa or StatusAlerta.ATIVO.value
                )
                a.estado.status_antes_pausa = None
                # Zera o agendamento — a source recalcula no próximo tick.
                a.estado.proximo_disparo = None
                a.estado.proxima_acao = None
    except storage.LockTimeoutError:
        await message.reply_text(
            "Alerta ocupado agora (lock). Tenta de novo em 1s.",
            message_thread_id=thread_id,
        )
        return

    if nao_pausado:
        await message.reply_text(
            f"<b>{alerta_id}</b> não está pausado.",
            message_thread_id=thread_id, parse_mode="HTML",
        )
    else:
        storage.append_evento(config.kobe_home, alerta_id, "retomado")
        await message.reply_text(
            f"🟢 Alerta <b>{alerta_id}</b> retomado.",
            message_thread_id=thread_id, parse_mode="HTML",
        )


# --- /alerta_apagar ----------------------------------------------------

async def on_command_alerta_apagar(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    config: Config = context.application.bot_data["config"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    thread_id = message.message_thread_id

    alerta_id = " ".join(context.args).strip() if context.args else ""
    if not alerta_id:
        await message.reply_text(
            "Uso: /alerta_apagar <id>  (veja os ids em /alerta_lista)",
            message_thread_id=thread_id,
        )
        return
    if not storage.existe(config.kobe_home, alerta_id):
        await message.reply_text(
            f"Não achei alerta com id <b>{alerta_id}</b>.",
            message_thread_id=thread_id, parse_mode="HTML",
        )
        return

    # Auditoria antes de remover (o jsonl some junto, mas o log do bot fica).
    logger.info(
        "/alerta_apagar user=%s alerta=%s",
        update.effective_user.id if update.effective_user else None, alerta_id,
    )
    try:
        storage.apagar(config.kobe_home, alerta_id)
    except storage.LockTimeoutError:
        await message.reply_text(
            "Alerta ocupado agora (lock). Tenta de novo em 1s.",
            message_thread_id=thread_id,
        )
        return
    await message.reply_text(
        f"🗑️ Alerta <b>{alerta_id}</b> apagado.",
        message_thread_id=thread_id, parse_mode="HTML",
    )
