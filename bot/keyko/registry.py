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

    # TranscriptsSource — 3ª fonte (gatilho de tempo: o coletor diário da F1).
    # Diferente das outras, ela NUNCA devolve despertar: faz o trabalho dentro
    # do próprio tick e retorna lista vazia. Copiar bytes não precisa de um
    # modelo, e acordar um pra isso todo dia gastaria cota — o recurso escasso
    # desta campanha — na tarefa mais burra do sistema.
    #
    # `build` devolve None com a chave desligada, e a fonte não é registrada:
    # uma fonte que aparece no log de inicialização sem fazer nada faria "quem o
    # Keyko está observando" deixar de ser verdade.
    try:
        from bot.transcripts.source import build as build_transcripts
    except ImportError:
        logger.exception("TranscriptsSource indisponível — pacote bot.transcripts faltando?")
    else:
        transcripts = build_transcripts(kobe_home=kobe_home, bot_token=bot_token)
        if transcripts is not None:
            sources.append(transcripts)
            logger.info("source registrada: transcripts")
        else:
            logger.info(
                "source transcripts NÃO registrada (TRANSCRIPT_COLLECTOR_ENABLED off)"
            )

    return sources
