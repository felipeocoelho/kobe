"""Registry de Sources do Keyko.

Lista hardcoded — sources vivem no core, sem plugin system. Quando
Alertas chegar, basta importar `AlertasSource` e adicionar na lista.

Centralizado pra ficar fácil saber "quem o Keyko está observando".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from bot.keyko.models import Source


logger = logging.getLogger("kobe.keyko.registry")


def build_sources(
    *, kobe_home: Path, bot_token: str
) -> list[Source]:
    """Instancia e devolve as sources ativas. Cada uma é responsável
    por toda a lógica de SUA fonte (Keyko só executa despertares).

    `kobe_home` e `bot_token` são passados pra sources que precisam
    (a fonte de alertas usa pra ler estado e responder via HTTP).
    """
    sources: list[Source] = []

    # AlertasSource — 2ª fonte (gatilho de tempo: cron/one-shot venceu).
    try:
        from bot.alertas.source import AlertasSource
    except ImportError:
        logger.exception("AlertasSource indisponível — pacote bot.alertas faltando?")
    else:
        sources.append(AlertasSource(kobe_home=kobe_home, bot_token=bot_token))
        logger.info("source registrada: alertas")

    return sources
