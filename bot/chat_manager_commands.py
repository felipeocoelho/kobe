"""Comandos do Chat Manager (Fase 6).

Implementa handlers de comandos Telegram pra gerenciamento de conversations:

- /conversas — lista do topic atual com botões clicáveis
- /conversas-global — lista todas categorizadas
- /conversa <busca> — abre conversation específica
- /renomear <nome> — renomeia conversation ativa
- Callback handler `cm_retomar:<conv_id>` — clique nos botões

Todos os comandos requerem CHAT_MANAGER_ENABLED=true (senão respondem
mensagem explicativa). Sistema atual continua intacto com flag off.

Comportamento sem parâmetro (clique mobile no menu):
- /conversa, /conversas, /conversas-global, /retomar → listam
- /renomear → orienta a passar nome como argumento (MVP sem estado
  conversacional; v2 pode adicionar single-turn pending state)
"""

from __future__ import annotations

import logging
from typing import Optional

from supabase import Client
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from bot.config import Config
from bot.topic_manager import (
    archive_active_session,
    ensure_active_session,
    ensure_topic,
    get_active_conversation_for_topic,
    get_topic_slug,
    set_session_conversation,
)


logger = logging.getLogger("kobe.chat_manager_cmd")


# Telegram callback_data: max 64 bytes. UUID = 36 chars. Cabe.
_CB_RETOMAR_PREFIX = "cm_retomar:"

# Limite de botões por lista — Telegram tolera mais, mas ficar legível.
_MAX_BUTTONS_PER_LIST = 10


def _user_authorized(update: Update, allowed_user_ids: frozenset[int]) -> bool:
    user = update.effective_user
    return user is not None and user.id in allowed_user_ids


async def _require_enabled(update: Update, config: Config) -> bool:
    """Manda mensagem padrão se Chat Manager está off. True se OK pra prosseguir."""
    if config.chat_manager_enabled:
        return True
    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "Chat Manager está desabilitado. "
            "Ligue com `CHAT_MANAGER_ENABLED=true` no .env e reinicie o bot.",
            message_thread_id=message.message_thread_id,
        )
    return False


def _build_keyboard(conversations: list[dict], *, show_topic: bool = False) -> InlineKeyboardMarkup:
    """Inline keyboard com 1 botão por conversation."""
    rows: list[list[InlineKeyboardButton]] = []
    for c in conversations[:_MAX_BUTTONS_PER_LIST]:
        title = (c.get("title") or c.get("slug") or "(sem título)")[:40]
        label = title
        if show_topic and c.get("topic_name"):
            label = f"[{c['topic_name'][:10]}] {title}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"{_CB_RETOMAR_PREFIX}{c['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# /conversas — lista do topic atual
# ---------------------------------------------------------------------------


async def on_command_conversas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    if not await _require_enabled(update, config):
        return

    thread_id = message.message_thread_id
    topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
    args_text = " ".join(context.args or []).strip()

    q = (
        db.table("conversations")
        .select("id, title, slug, status, last_activity_at")
        .eq("topic_id", topic_id)
        .in_("status", ["active", "dormant"])
        .order("last_activity_at", desc=True)
    )
    if args_text:
        # Filtro substring case-insensitive no title
        q = q.ilike("title", f"%{args_text}%")
    res = q.execute()
    convs = res.data or []

    if not convs:
        text = (
            f"Nenhuma conversa encontrada{f' com filtro `{args_text}`' if args_text else ''} "
            f"neste tópico ainda."
        )
        await message.reply_text(text, message_thread_id=thread_id, parse_mode="Markdown")
        return

    slug = get_topic_slug(db, message.chat_id, thread_id) or "(?)"
    header = f"📂 *Conversas do tópico `{slug}`*"
    if args_text:
        header += f" (filtro: `{args_text}`)"
    header += f"\nTotal: {len(convs)}"
    if len(convs) > _MAX_BUTTONS_PER_LIST:
        header += f" (mostrando {_MAX_BUTTONS_PER_LIST} mais recentes)"
    header += "\n\nClique pra retomar:"

    await message.reply_text(
        header,
        message_thread_id=thread_id,
        reply_markup=_build_keyboard(convs),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /conversas-global — todas, categorizadas por topic
# ---------------------------------------------------------------------------


async def on_command_conversas_global(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    if not await _require_enabled(update, config):
        return

    thread_id = message.message_thread_id
    current_topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
    args_text = " ".join(context.args or []).strip()

    # Carrega tudo
    q = (
        db.table("conversations")
        .select("id, title, slug, status, topic_id, last_activity_at")
        .in_("status", ["active", "dormant"])
        .order("last_activity_at", desc=True)
    )
    if args_text:
        q = q.ilike("title", f"%{args_text}%")
    res = q.execute()
    convs = res.data or []

    if not convs:
        await message.reply_text(
            f"Nenhuma conversa encontrada{f' com filtro `{args_text}`' if args_text else ''}.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return

    # Carrega names dos topics
    topics_map = {
        t["id"]: t.get("current_name") or "?"
        for t in db.table("topics").select("id, current_name").execute().data
    }
    for c in convs:
        c["topic_name"] = topics_map.get(c["topic_id"], "?")

    # Prioriza topic atual
    convs.sort(key=lambda c: (c["topic_id"] != current_topic_id, c["last_activity_at"]), reverse=False)
    # ^ tuple: topic atual primeiro (False < True), depois mais recente primeiro
    convs.sort(
        key=lambda c: (
            0 if c["topic_id"] == current_topic_id else 1,
            -1 * int(_iso_to_epoch_seconds(c["last_activity_at"])),
        )
    )

    header = "🌐 *Todas as conversas* (priorizando este tópico)"
    if args_text:
        header += f"\nFiltro: `{args_text}`"
    header += f"\nTotal: {len(convs)}"
    if len(convs) > _MAX_BUTTONS_PER_LIST:
        header += f" (mostrando {_MAX_BUTTONS_PER_LIST} primeiros)"

    await message.reply_text(
        header,
        message_thread_id=thread_id,
        reply_markup=_build_keyboard(convs, show_topic=True),
        parse_mode="Markdown",
    )


def _iso_to_epoch_seconds(iso: str) -> float:
    try:
        from datetime import datetime
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# /conversa <busca> — abre conversation por busca
# ---------------------------------------------------------------------------


async def on_command_conversa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sem param: equivalente a /conversas. Com param: busca semântica."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    if not await _require_enabled(update, config):
        return

    args_text = " ".join(context.args or []).strip()
    if not args_text:
        # Sem parâmetro: cai pra /conversas
        await on_command_conversas(update, context)
        return

    thread_id = message.message_thread_id
    topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)

    # Busca substring em title (MVP — busca semântica via embedding fica
    # pra v2; o detector já faz isso quando operador manda msg natural).
    res = (
        db.table("conversations")
        .select("id, title, slug, status")
        .eq("topic_id", topic_id)
        .in_("status", ["active", "dormant"])
        .ilike("title", f"%{args_text}%")
        .order("last_activity_at", desc=True)
        .execute()
    )
    matches = res.data or []

    if not matches:
        await message.reply_text(
            f"Nenhuma conversa do tópico atual com `{args_text}` no título. "
            f"Tente /conversas-global pra buscar em todos os tópicos.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return

    if len(matches) == 1:
        # Match único: ativa direto
        await _activate_conversation(db, topic_id, matches[0])
        await message.reply_text(
            f"✅ Reabri a conversa *{matches[0]['title']}*. Próxima mensagem cai nela.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return

    # Múltiplos matches: lista pra operador escolher
    await message.reply_text(
        f"🔍 {len(matches)} conversas com `{args_text}` no título. Clique pra escolher:",
        message_thread_id=thread_id,
        reply_markup=_build_keyboard(matches),
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /renomear <nome> — renomeia conversation ativa
# ---------------------------------------------------------------------------


async def on_command_renomear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    if not await _require_enabled(update, config):
        return

    thread_id = message.message_thread_id
    new_name = " ".join(context.args or []).strip().strip('"').strip("'")

    if not new_name:
        await message.reply_text(
            "Manda `/renomear <novo nome>` pra renomear a conversa ativa deste tópico. "
            "Exemplo: `/renomear Bug 5 da v0.14`. Você também pode mandar "
            "\"Hal, renomeia essa conversa pra X\" em linguagem natural.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return

    topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
    active = get_active_conversation_for_topic(db, topic_id)
    if active is None:
        await message.reply_text(
            "Nenhuma conversa ativa neste tópico ainda. Manda alguma mensagem primeiro "
            "pra criar uma; depois você pode renomear.",
            message_thread_id=thread_id,
        )
        return

    old_title = active["title"]
    db.table("conversations").update({"title": new_name[:80]}).eq("id", active["id"]).execute()
    logger.info(
        "/renomear conv=%s old=%r new=%r",
        active["id"][:8], old_title, new_name[:80],
    )
    await message.reply_text(
        f"✏️ Renomeado: *{old_title}* → *{new_name[:80]}*",
        message_thread_id=thread_id,
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback handler — clique nos botões "Reabrir conversa X"
# ---------------------------------------------------------------------------


async def on_callback_retomar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler de callback_query pra prefixo `cm_retomar:<conv_id>`."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    query = update.callback_query
    if query is None:
        return
    if not _user_authorized(update, config.allowed_user_ids):
        await query.answer("Não autorizado.", show_alert=True)
        return

    data = query.data or ""
    if not data.startswith(_CB_RETOMAR_PREFIX):
        return  # outro handler

    conv_id = data[len(_CB_RETOMAR_PREFIX):]
    res = (
        db.table("conversations")
        .select("id, title, topic_id, status")
        .eq("id", conv_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        await query.answer("Conversa não encontrada.", show_alert=True)
        return
    conv = res.data[0]

    # Detecta se a conversation está em OUTRO topic — confirma com operador
    message = query.message
    if message is None:
        await query.answer("Erro: sem contexto de mensagem.", show_alert=True)
        return
    current_thread_id = message.message_thread_id
    current_topic_id = ensure_topic(db, current_thread_id, chat_id=message.chat_id)
    cross_topic = conv["topic_id"] != current_topic_id

    await _activate_conversation(db, conv["topic_id"], conv)
    suffix = ""
    if cross_topic:
        # Conversation pertence a outro topic — operador precisa ir pro topic certo
        other_topic = db.table("topics").select("current_name").eq(
            "id", conv["topic_id"]
        ).limit(1).execute()
        other_name = (other_topic.data or [{}])[0].get("current_name") or "?"
        suffix = (
            f"\n\n⚠️ Essa conversa pertence ao tópico *{other_name}* — "
            f"vá pra lá pra continuar nela."
        )

    await query.answer("Reabri a conversa.")
    await query.edit_message_text(
        f"✅ Reabri *{conv['title']}*.{suffix}",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Helpers compartilhados
# ---------------------------------------------------------------------------


async def _activate_conversation(db: Client, topic_id: str, conv: dict) -> None:
    """Marca conversation como active no topic, arquiva session atual,
    cria session nova vinculada. Não envia notice — caller faz isso.
    """
    # Marca outras conversations do topic como dormant
    other_active = (
        db.table("conversations")
        .select("id")
        .eq("topic_id", topic_id)
        .eq("status", "active")
        .neq("id", conv["id"])
        .execute()
    )
    for o in other_active.data or []:
        db.table("conversations").update({"status": "dormant"}).eq("id", o["id"]).execute()

    # Reativa alvo (se era dormant)
    if conv.get("status") != "active":
        db.table("conversations").update({"status": "active"}).eq("id", conv["id"]).execute()

    # Arquiva session atual + cria nova vinculada
    archive_active_session(db, topic_id, status="archived")
    new_session_id = ensure_active_session(db, topic_id)
    set_session_conversation(db, new_session_id, conv["id"])
