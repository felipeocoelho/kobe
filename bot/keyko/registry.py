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
    (Missões usa pra ler estado e editar painel via HTTP).
    """
    sources: list[Source] = []

    # MissoesSource — Fase 1, sempre ativa.
    try:
        from bot.missoes.source import MissoesSource
    except ImportError:
        logger.exception("MissoesSource indisponível — pacote bot.missoes faltando?")
    else:
        sources.append(MissoesSource(kobe_home=kobe_home, bot_token=bot_token))
        logger.info("source registrada: missoes")

    # AlertasSource — 2ª fonte (gatilho de tempo: cron/one-shot venceu).
    try:
        from bot.alertas.source import AlertasSource
    except ImportError:
        logger.exception("AlertasSource indisponível — pacote bot.alertas faltando?")
    else:
        sources.append(AlertasSource(kobe_home=kobe_home, bot_token=bot_token))
        logger.info("source registrada: alertas")

    # ClassifierSource — New Chat Manager (gatilho: debounce por silêncio).
    # Não acorda o Claude; faz o trabalho do bibliotecário atrás do turno.
    # Inerte enquanto CHAT_MANAGER_ENABLED=false (checa a flag no tick).
    try:
        from bot.chat_manager.source import ClassifierSource
    except ImportError:
        logger.exception("ClassifierSource indisponível — pacote bot.chat_manager faltando?")
    else:
        sources.append(ClassifierSource(kobe_home=kobe_home, bot_token=bot_token))
        logger.info("source registrada: chat_manager")

    return sources
