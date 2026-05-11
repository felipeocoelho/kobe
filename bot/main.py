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
from bot.telegram_handler import (
    on_command_contexto,
    on_command_nova,
    on_command_retomar,
    on_command_salvar,
    on_text,
    on_unsupported,
    on_voice,
)
from bot.transcribe import Transcriber


logger = logging.getLogger("kobe.bot")


def build_application(config: Config) -> Application:
    app = ApplicationBuilder().token(config.telegram_bot_token).build()
    app.bot_data["config"] = config
    app.bot_data["db"] = build_client(config)
    app.bot_data["transcriber"] = Transcriber(api_key=config.groq_api_key)
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
