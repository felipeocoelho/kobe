"""Entrypoint do bot Kobe.

Fase 7 (comandos especiais): texto/áudio segue o pipeline Claude da Fase 6,
e os comandos `/nova`, `/contexto`, `/salvar` e `/retomar` mexem direto na
memória persistente (sessions / saved_artifacts) sem invocar o Claude.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.claude_runner import ClaudeRunner
from bot.config import Config, ConfigError, load_config
from bot.db import build_client
from bot.plugins import discover_plugins, render_plugins_section, sync_agent_symlinks
from bot.snapshot import (
    cleanup_expired_snapshots,
    drop_snapshot,
    load_pending_snapshots,
    render_resume_message,
    save_pending_snapshots,
)
from bot.telegram_handler import (
    on_command_contexto,
    on_command_nova,
    on_command_retomar,
    on_command_salvar,
    on_text,
    on_unsupported,
    on_voice,
)
from bot.topic_manager import GENERAL_THREAD_ID
from bot.transcribe import Transcriber


logger = logging.getLogger("kobe.bot")


async def _on_startup(app: Application) -> None:
    """Pós-init, pré-polling: descoberta de plugins + consumo de snapshots.

    Sequência:
    1. Descobre plugins instalados e sincroniza os symlinks de subagentes
       — feito no startup pra refletir qualquer `install-plugin.sh` que
       tenha rodado desde o último boot.
    2. Limpa snapshots expirados (TTL excedido).
    3. Carrega os ainda válidos e manda uma mensagem proativa em cada
       tópico, sinalizando o retorno e citando a última fala do operador
       como gancho.
    4. Apaga cada snapshot após enviar — único uso, sem replay no
       próximo boot.
    """
    config: Config = app.bot_data["config"]
    plugins = discover_plugins(config.kobe_home)
    app.bot_data["plugins"] = plugins
    if plugins:
        linked = sync_agent_symlinks(config.kobe_home, plugins)
        logger.info(
            "startup: %d plugin(s) descoberto(s), %d symlink(s) de subagente",
            len(plugins),
            linked,
        )
    else:
        logger.info("startup: nenhum plugin instalado")

    db = app.bot_data["db"]
    expired = cleanup_expired_snapshots(db)
    if expired:
        logger.info("startup: %d snapshot(s) expirado(s) limpo(s)", expired)

    pending = load_pending_snapshots(db)
    if not pending:
        return

    logger.info("startup: %d snapshot(s) pendente(s) — restaurando", len(pending))
    for snap in pending:
        chat_id = snap.get("telegram_chat_id")
        if chat_id is None:
            continue
        # No banco GENERAL_THREAD_ID=0 é sentinela; Telegram API espera None.
        thread_id = snap.get("telegram_thread_id")
        if thread_id == GENERAL_THREAD_ID:
            thread_id = None
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=render_resume_message(snap),
                message_thread_id=thread_id,
            )
            drop_snapshot(db, snap["_artifact_id"])
        except Exception:  # noqa: BLE001 — não derrubar boot
            logger.exception(
                "falha enviando resume msg topic_id=%s", snap.get("topic_id")
            )


async def _on_shutdown(app: Application) -> None:
    """Pré-shutdown: salva snapshots das sessões ativas recentes.

    PTB invoca este hook ao receber SIGTERM/SIGINT (deploy, restart) —
    rodamos antes do polling fechar, com a conexão ao Supabase ainda
    viva. Falhas individuais são logadas dentro do snapshot e não
    abortam o shutdown.
    """
    db = app.bot_data["db"]
    saved = save_pending_snapshots(db)
    logger.info("shutdown: %d snapshot(s) gravado(s) pra próximo boot", saved)


def build_application(config: Config) -> Application:
    # Timeouts do PTB são 5s por padrão — curto demais pra get_file/download
    # de áudio: voice messages mais longas (3+ min) chegaram a estourar só
    # no metadata fetch. Subimos pra valores generosos, ainda dentro da boa
    # prática do PTB pra long-polling clients.
    app = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .connect_timeout(15)
        .read_timeout(30)
        .write_timeout(60)
        .pool_timeout(5)
        .media_write_timeout(120)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    app.bot_data["config"] = config
    app.bot_data["db"] = build_client(config)
    app.bot_data["transcriber"] = Transcriber(
        api_key=config.groq_api_key,
        hints_path=config.kobe_home / "user-data" / "transcription-hints.md",
    )
    app.bot_data["claude"] = ClaudeRunner(
        cwd=config.kobe_claude_cwd,
        timeout_seconds=config.claude_timeout_seconds,
    )
    app.add_handler(CommandHandler("nova", on_command_nova))
    app.add_handler(CommandHandler("contexto", on_command_contexto))
    app.add_handler(CommandHandler("salvar", on_command_salvar))
    app.add_handler(CommandHandler("retomar", on_command_retomar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.COMMAND, on_unsupported))
    return app


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"Configuração inválida: {exc}") from exc

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "kobe iniciando — usuários autorizados=%d home=%s",
        len(config.allowed_user_ids),
        config.kobe_home,
    )

    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
