"""Comandos do Chat Manager (Fase 6, refatorado em 2026-05-27).

Listagem usa **links de slash command clicáveis** em texto (não botões
inline) — Telegram destaca `/retomar_<id>` em azul e clicar dispara o
comando direto. Mais escalável que botões: muitos items continuam
legíveis, e cada link tem espaço próprio em vez de truncar.

Comandos:
- /conversas_topico — lista do topic atual
- /conversas_global — lista todos os topics
- /conversa <termo> — busca substring no title
- /renomear <nome> — renomeia ativa
- /retomar_<id_prefix> — link clicável gerado nas listagens (8 chars do UUID)

Todos requerem CHAT_MANAGER_ENABLED=true.

Sem parâmetro (clique mobile): cada comando tem comportamento gracioso —
/conversa cai pra /conversas_topico, /renomear orienta a passar nome.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from supabase import Client
from telegram import Update
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


# Limite de items na lista por mensagem — Telegram tem limite de 4096
# chars; com 50 chars/linha cabem ~80 items, mas nunca queremos isso.
_MAX_LIST_ITEMS = 15

# Quantos chars do UUID usar no /retomar_<id_curto>. 8 chars = 4 bilhões
# de combinações — improvável colisão em escala do Kobe.
_ID_PREFIX_LEN = 8


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


def _format_conversations_list(
    conversations: list[dict], *, show_topic: bool = False
) -> str:
    """Formata lista de conversations como texto com slash commands clicáveis.

    Cada item ocupa 2 linhas + 1 linha em branco pra respiração visual:

        • Nome da conversa
          /retomar_a1b2c3d4

        • Outro nome
          /retomar_e5f6g7h8

    Quando `show_topic=True`, prefixa com `[topic]`.
    """
    lines: list[str] = []
    for c in conversations[:_MAX_LIST_ITEMS]:
        title = c.get("title") or c.get("slug") or "(sem título)"
        prefix = ""
        if show_topic and c.get("topic_name"):
            prefix = f"[{c['topic_name']}] "
        short_id = c["id"][:_ID_PREFIX_LEN]
        lines.append(f"• {prefix}{title}")
        lines.append(f"  /retomar_{short_id}")
        lines.append("")  # espaçamento
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# /conversas_topico — lista do topic atual
# ---------------------------------------------------------------------------


async def on_command_conversas_topico(
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
    if len(convs) > _MAX_LIST_ITEMS:
        header += f" (mostrando {_MAX_LIST_ITEMS} mais recentes)"
    header += "\nClique em `/retomar_...` pra reabrir.\n\n"

    await message.reply_text(
        header + _format_conversations_list(convs),
        message_thread_id=thread_id,
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# /conversas_global — todas, categorizadas por topic
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

    topics_map = {
        t["id"]: t.get("current_name") or "?"
        for t in db.table("topics").select("id, current_name").execute().data
    }
    for c in convs:
        c["topic_name"] = topics_map.get(c["topic_id"], "?")

    convs.sort(
        key=lambda c: (
            0 if c["topic_id"] == current_topic_id else 1,
            -1 * _iso_to_epoch_seconds(c["last_activity_at"]),
        )
    )

    header = "🌐 *Todas as conversas* (tópico atual primeiro)"
    if args_text:
        header += f"\nFiltro: `{args_text}`"
    header += f"\nTotal: {len(convs)}"
    if len(convs) > _MAX_LIST_ITEMS:
        header += f" (mostrando {_MAX_LIST_ITEMS} mais recentes)"
    header += "\nClique em `/retomar_...` pra reabrir.\n\n"

    await message.reply_text(
        header + _format_conversations_list(convs, show_topic=True),
        message_thread_id=thread_id,
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
    """Sem param: equivalente a /conversas_topico. Com param: busca substring."""
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    if not await _require_enabled(update, config):
        return

    args_text = " ".join(context.args or []).strip()
    if not args_text:
        # Sem parâmetro: cai pra /conversas_topico
        await on_command_conversas_topico(update, context)
        return

    thread_id = message.message_thread_id
    topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)

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
            f"Tente /conversas\\_global pra buscar em todos os tópicos.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return

    if len(matches) == 1:
        await _activate_conversation(db, topic_id, matches[0])
        await message.reply_text(
            f"✅ Reabri a conversa *{matches[0]['title']}*. Próxima mensagem cai nela.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return

    header = f"🔍 {len(matches)} conversas com `{args_text}` no título — clique pra escolher:\n\n"
    await message.reply_text(
        header + _format_conversations_list(matches),
        message_thread_id=thread_id,
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
# /retomar_<id_curto> — link clicável gerado nas listagens
# ---------------------------------------------------------------------------


_RETOMAR_SHORT_RE = re.compile(r"^/retomar_([0-9a-f]{6,16})(?:@\w+)?\s*$", re.IGNORECASE)


async def on_command_retomar_short(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Match texto `/retomar_<id_prefix>` (com ou sem `@botname`).

    Resolve o id_prefix → UUID completo via client-side filter (escala
    atual permite, ~50 conversations max no banco). Ativa a conversation.
    """
    config: Config = context.application.bot_data["config"]
    db: Client = context.application.bot_data["db"]
    message = update.effective_message
    if message is None or not _user_authorized(update, config.allowed_user_ids):
        return
    if not await _require_enabled(update, config):
        return

    text = (message.text or "").strip()
    m = _RETOMAR_SHORT_RE.match(text)
    if not m:
        return
    id_prefix = m.group(1).lower()

    # Carrega todas conversations não-archived e filtra por prefix
    all_convs = (
        db.table("conversations")
        .select("id, title, topic_id, status")
        .in_("status", ["active", "dormant"])
        .execute()
        .data
    ) or []
    matches = [c for c in all_convs if c["id"].lower().startswith(id_prefix)]

    thread_id = message.message_thread_id
    if not matches:
        await message.reply_text(
            f"Não encontrei conversa com id começando em `{id_prefix}`. "
            f"Use `/conversas_topico` pra ver a lista atual.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return
    if len(matches) > 1:
        await message.reply_text(
            f"⚠️ {len(matches)} conversas têm id começando em `{id_prefix}` — "
            f"caso raro de colisão de prefix. Use `/conversas_global` pra ver a lista completa.",
            message_thread_id=thread_id,
            parse_mode="Markdown",
        )
        return

    conv = matches[0]
    current_topic_id = ensure_topic(db, thread_id, chat_id=message.chat_id)
    cross_topic = conv["topic_id"] != current_topic_id

    await _activate_conversation(db, conv["topic_id"], conv)

    suffix = ""
    if cross_topic:
        other_topic = db.table("topics").select("current_name").eq(
            "id", conv["topic_id"]
        ).limit(1).execute()
        other_name = (other_topic.data or [{}])[0].get("current_name") or "?"
        suffix = (
            f"\n\n⚠️ Essa conversa pertence ao tópico *{other_name}* — "
            f"vá pra lá pra continuar nela."
        )

    await message.reply_text(
        f"✅ Reabri *{conv['title']}*. Próxima mensagem cai nela.{suffix}",
        message_thread_id=thread_id,
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
