"""Entrypoint do daemon Keyko: `python -m bot.keyko`.

Carrega config (mesmo .env do bot principal), monta sources via
registry, roda o loop até SIGTERM.
"""

from __future__ import annotations

import logging
import sys

from bot.config import ConfigError, load_config
from bot.keyko.loop import KeykoLoop
from bot.keyko.registry import build_sources


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"keyko: configuração inválida: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("kobe.keyko")
    logger.info(
        "keyko iniciando — kobe_home=%s log_level=%s",
        config.kobe_home, config.log_level,
    )

    sources = build_sources(
        kobe_home=config.kobe_home,
        bot_token=config.telegram_bot_token,
    )
    if not sources:
        logger.warning("nenhuma source registrada — keyko vai ficar parado")

    loop = KeykoLoop(
        sources=sources,
        kobe_home=config.kobe_home,
        bot_token=config.telegram_bot_token,
    )
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
