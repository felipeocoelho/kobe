"""Handlers Telegram pros comandos do plugin Apolo (WhatsApp).

Esses handlers vivem no core do Kobe (não no plugin) porque:
1. O catálogo `contacts` é compartilhado (não exclusivo do Apolo).
2. Upload de `.vcf`/`.csv` precisa ser interceptado no MessageHandler do bot.
3. Comandos slash precisam ser registrados em `bot/main.py`.

A lógica pesada (busca, parsing, envio) mora em `plugins/public/apolo/scripts/`.
Aqui só fazemos wrap dos scripts via subprocess + formata resposta pro Telegram.

Pra não complicar a UX, retornamos texto formatado em Markdown HTML (subset
do Telegram). Quem chama escolhe verbosidade.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("kobe.apolo_handlers")


# ============================================================================
# Helpers
# ============================================================================


def _apolo_script(name: str) -> Path:
    """Resolve path absoluto pra um script do plugin Apolo."""
    kobe_home = Path(os.environ.get("KOBE_HOME", "/home/felipe/projetos/kobe"))
    return kobe_home / "plugins" / "public" / "apolo" / "scripts" / name


def _venv_python() -> Path:
    """Python do venv do Kobe."""
    kobe_home = Path(os.environ.get("KOBE_HOME", "/home/felipe/projetos/kobe"))
    return kobe_home / ".venv" / "bin" / "python"


async def _run_apolo_script(name: str, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    """Roda script do Apolo num subprocess async. Retorna (exit_code, stdout, stderr)."""
    cmd = [str(_venv_python()), str(_apolo_script(name)), *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", f"timeout após {timeout}s"
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


# ============================================================================
# /contatos_buscar <termo>
# ============================================================================


async def on_command_contatos_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(
            "Uso: <code>/contatos_buscar &lt;termo&gt;</code>\n"
            "Ex: <code>/contatos_buscar Pedro</code>",
            parse_mode="HTML",
        )
        return

    termo = " ".join(context.args)
    code, stdout, stderr = await _run_apolo_script("contacts_search.py", "--termo", termo)

    if code == 1:
        await update.effective_message.reply_text(f"Nenhum contato achado pra <i>{termo}</i>.", parse_mode="HTML")
        return
    if code != 0:
        await update.effective_message.reply_text(f"❌ Erro:\n<pre>{stderr[:500]}</pre>", parse_mode="HTML")
        return

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        await update.effective_message.reply_text(f"❌ Resposta inválida:\n<pre>{stdout[:400]}</pre>", parse_mode="HTML")
        return

    matches = data.get("matches", [])
    if not matches:
        await update.effective_message.reply_text(f"Nenhum match pra <i>{termo}</i>.", parse_mode="HTML")
        return

    lines = [f"<b>{len(matches)} resultado(s) pra '{termo}':</b>\n"]
    for i, c in enumerate(matches[:20], 1):
        line = f"<b>{i}.</b> {c['nome_canonico']}"
        if c.get("contexto"):
            line += f" — <i>{c['contexto']}</i>"
        if c.get("telefone_e164"):
            line += f"\n   📞 <code>{c['telefone_e164']}</code>"
        elif c.get("whatsapp_jid", "").endswith("@g.us"):
            line += "\n   👥 grupo"
        if c.get("email"):
            line += f"\n   ✉️ {c['email']}"
        lines.append(line)

    await update.effective_message.reply_text("\n\n".join(lines), parse_mode="HTML")


# ============================================================================
# /contatos_listar [pessoas|grupos] [oculto]
# ============================================================================


async def on_command_contatos_listar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = [a.lower() for a in (context.args or [])]
    tipo: Optional[str] = None
    include_hidden = False
    for a in args:
        if a in ("pessoas", "pessoa", "p"):
            tipo = "pessoa"
        elif a in ("grupos", "grupo", "g"):
            tipo = "grupo"
        elif a in ("oculto", "ocultos", "hidden"):
            include_hidden = True

    # Consulta direta no Supabase via SDK do bot
    supabase = context.bot_data.get("db")
    if not supabase:
        await update.effective_message.reply_text("❌ Supabase não disponível.")
        return

    q = supabase.table("contacts").select("id, tipo, nome_canonico, telefone_e164, whatsapp_jid, contexto, oculto").order("nome_canonico").limit(50)
    if tipo:
        q = q.eq("tipo", tipo)
    if not include_hidden:
        q = q.eq("oculto", False)
    try:
        rows = q.execute().data
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Erro consultando: {e}")
        return

    if not rows:
        await update.effective_message.reply_text(
            f"Nenhum contato no catálogo{' (incluindo ocultos)' if include_hidden else ''}."
        )
        return

    lines = [f"<b>{len(rows)} contato(s):</b>\n"]
    for c in rows[:50]:
        glyph = "👤" if c["tipo"] == "pessoa" else "👥"
        line = f"{glyph} {c['nome_canonico']}"
        if c.get("contexto"):
            line += f" — <i>{c['contexto']}</i>"
        if c.get("telefone_e164"):
            line += f" · <code>{c['telefone_e164']}</code>"
        if c.get("oculto"):
            line += " 🔕"
        lines.append(line)

    if len(rows) >= 50:
        lines.append("\n<i>(mostrando 50 — use /contatos_buscar &lt;termo&gt; pra filtrar)</i>")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


# ============================================================================
# /contatos_promover <nome-do-arquivo-de-peneira>
# ============================================================================


async def on_command_contatos_promover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(
            "Uso: <code>/contatos_promover &lt;nome-do-arquivo&gt;</code>\n"
            "Ex: <code>/contatos_promover google_vcard-2026-05-27.md</code>\n\n"
            "Arquivo deve estar em <code>$KOBE_HOME/user-data/imports/</code> "
            "(o bot salva ali quando você faz upload de .vcf/.csv).",
            parse_mode="HTML",
        )
        return

    filename = context.args[0]
    kobe_home = Path(os.environ.get("KOBE_HOME", "/home/felipe/projetos/kobe"))
    arquivo = kobe_home / "user-data" / "imports" / filename
    if not arquivo.is_file():
        await update.effective_message.reply_text(
            f"❌ Arquivo não existe: <code>{arquivo}</code>\n"
            f"Use <code>ls $KOBE_HOME/user-data/imports/</code> pra ver os disponíveis.",
            parse_mode="HTML",
        )
        return

    await update.effective_message.reply_text("⏳ Promovendo contatos…")
    code, stdout, stderr = await _run_apolo_script("contacts_promote.py", "--input", str(arquivo), timeout=300)
    if code != 0:
        await update.effective_message.reply_text(f"❌ Erro:\n<pre>{(stderr or stdout)[:600]}</pre>", parse_mode="HTML")
        return

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        data = {"status": "ok", "raw": stdout[:500]}

    msg = (
        f"✅ Promoção concluída.\n\n"
        f"• Origem: <code>{data.get('origem')}</code>\n"
        f"• Linhas processadas: {data.get('total_linhas')}\n"
        f"• Promovidos/atualizados: {data.get('promovidos')}\n"
        f"• Ignorados (sem dados): {data.get('ignorados_sem_dados')}\n"
    )
    if data.get("erros_total", 0) > 0:
        msg += f"⚠️ Erros: {data['erros_total']} (primeiros 10 no log)"
    await update.effective_message.reply_text(msg, parse_mode="HTML")


# ============================================================================
# Upload de .vcf / .csv → roda importador e devolve arquivo de peneira
# ============================================================================


async def on_document_for_apolo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Intercepta upload de documento. Retorna True se processou.

    Três rotas:
    1. `.vcf` / `.csv` → importação (gera arquivo de peneira `.md`).
    2. `.md` cujo nome bate com arquivo existente em `user-data/imports/`
       → re-upload de peneira curada (sobrescreve em `imports/`, NÃO vai
       pra KB do tópico).
    3. Qualquer outro caso → False (handler genérico assume).

    O telegram_handler.py chama esse helper antes do fluxo padrão. Se True,
    o documento já foi tratado e o handler padrão não precisa processar.
    """
    msg = update.effective_message
    doc = msg.document if msg else None
    if not doc:
        return False

    fname_original = doc.file_name or ""
    fname = fname_original.lower()
    kobe_home = Path(os.environ.get("KOBE_HOME", "/home/felipe/projetos/kobe"))
    imports_dir = kobe_home / "user-data" / "imports"

    # Rota 2: re-upload de peneira .md (curada pelo operador)
    if fname.endswith(".md"):
        # Match por nome bate com arquivo em imports/ → é re-upload
        candidato = imports_dir / fname_original
        if not candidato.is_file():
            return False  # .md aleatório → segue pro fluxo de KB normal
        try:
            file = await context.bot.get_file(doc.file_id)
            await file.download_to_drive(custom_path=str(candidato))
        except Exception as e:
            await msg.reply_text(f"❌ Falha sobrescrevendo peneira: {e}")
            return True
        await msg.reply_text(
            f"♻️ Peneira atualizada em <code>user-data/imports/{fname_original}</code>. "
            f"Roda <code>/contatos_promover {fname_original}</code> quando quiser efetivar.",
            parse_mode="HTML",
        )
        return True

    # Rota 1: import de .vcf / .csv
    if not (fname.endswith(".vcf") or fname.endswith(".csv")):
        return False

    inbox = imports_dir / "_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    saved = inbox / (fname_original or f"upload-{doc.file_id}.bin")

    await msg.reply_text("⏳ Recebendo arquivo e processando contatos…")
    try:
        file = await context.bot.get_file(doc.file_id)
        await file.download_to_drive(custom_path=str(saved))
    except Exception as e:
        await msg.reply_text(f"❌ Falha baixando o arquivo: {e}")
        return True

    code, stdout, stderr = await _run_apolo_script(
        "contacts_import.py", "--input", str(saved), timeout=300
    )
    if code != 0:
        await msg.reply_text(f"❌ Erro processando:\n<pre>{(stderr or stdout)[:600]}</pre>", parse_mode="HTML")
        return True

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        await msg.reply_text(f"❌ Resposta inválida:\n<pre>{stdout[:400]}</pre>", parse_mode="HTML")
        return True

    peneira_path = Path(data["peneira_path"])
    msg_text = (
        f"✅ Importação processada.\n\n"
        f"• Formato: <code>{data.get('format')}</code>\n"
        f"• Origem: <code>{data.get('origem')}</code>\n"
        f"• Total no arquivo: {data.get('total_no_arquivo')}\n"
        f"• Úteis (com tel ou email): {data.get('total_uteis')}\n"
        f"• Descartados (sem dados): {data.get('descartados_sem_dados')}\n\n"
        f"📎 Anexando arquivo de peneira: edita, apaga linhas dos que não quer, "
        f"e usa <code>/contatos_promover {peneira_path.name}</code>"
    )
    await msg.reply_text(msg_text, parse_mode="HTML")

    # Envia o arquivo de peneira de volta
    try:
        with open(peneira_path, "rb") as f:
            await msg.reply_document(document=f, filename=peneira_path.name)
    except Exception as e:
        await msg.reply_text(
            f"⚠️ Salvei o arquivo de peneira em <code>{peneira_path}</code> "
            f"mas não consegui anexar de volta: {e}",
            parse_mode="HTML",
        )
    return True


# ============================================================================
# /whatsapp_grupos [filtro] — lista grupos do backend
# ============================================================================


async def on_command_whatsapp_grupos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = list(context.args or [])
    filtro: Optional[str] = None

    # Subcomando "refresh"?
    if args and args[0].lower() == "refresh":
        await update.effective_message.reply_text("⚠️ <code>/whatsapp_grupos refresh</code> ainda não implementado.", parse_mode="HTML")
        return

    if args:
        filtro = " ".join(args)

    cli_args = ["--limit", "30"]
    if filtro:
        cli_args += ["--filtro", filtro]

    code, stdout, stderr = await _run_apolo_script("grupos_buscar.py", *cli_args, timeout=60)
    if code != 0:
        await update.effective_message.reply_text(f"❌ Erro:\n<pre>{(stderr or stdout)[:600]}</pre>", parse_mode="HTML")
        return

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        await update.effective_message.reply_text(f"❌ Resposta inválida:\n<pre>{stdout[:400]}</pre>", parse_mode="HTML")
        return

    groups = data.get("groups", [])
    if not groups:
        suffix = f" com '{filtro}'" if filtro else ""
        await update.effective_message.reply_text(f"Nenhum grupo achado{suffix}.")
        return

    lines = [f"<b>{len(groups)} grupo(s){' com ' + repr(filtro) if filtro else ''}:</b>\n"]
    for i, g in enumerate(groups, 1):
        mark = "✅" if g.get("in_catalog") else "·"
        members = f" ({g['participants_count']} membros)" if g.get("participants_count") else ""
        lines.append(f"{i}. {mark} {g['name']}{members}")
    lines.append("\n<i>✅ = já no catálogo</i>")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


# ============================================================================
# /whatsapp_inbox [nao-lidas]
# ============================================================================


async def on_command_whatsapp_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = [a.lower() for a in (context.args or [])]
    apenas_nao_lidas = any(a in ("nao-lidas", "nao_lidas", "unread", "nao") for a in args)

    supabase = context.bot_data.get("db")
    if not supabase:
        await update.effective_message.reply_text("❌ Supabase não disponível.")
        return

    q = supabase.table("whatsapp_messages") \
        .select("id, jid_chat, jid_remetente, tipo, conteudo, timestamp, lida, midia_path") \
        .eq("direcao", "in") \
        .order("timestamp", desc=True) \
        .limit(20)
    if apenas_nao_lidas:
        q = q.eq("lida", False)
    try:
        rows = q.execute().data
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Erro: {e}")
        return

    if not rows:
        await update.effective_message.reply_text(
            "Inbox vazia." if not apenas_nao_lidas else "Nenhuma mensagem não-lida."
        )
        return

    lines = [f"<b>📥 Inbox ({len(rows)}{' não-lidas' if apenas_nao_lidas else ''}):</b>\n"]
    for r in rows:
        glyph = "👥" if r["jid_chat"].endswith("@g.us") else "👤"
        flag = "•" if not r["lida"] else "✓"
        preview = (r.get("conteudo") or f"[{r['tipo']}]")[:80]
        chat_short = r["jid_chat"].split("@", 1)[0][-6:]  # últimos 6 dígitos pra economia visual
        lines.append(f"{flag} {glyph} <code>...{chat_short}</code> — {preview}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
