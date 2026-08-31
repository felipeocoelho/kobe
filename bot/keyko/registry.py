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

    # SearchIndexSource — 4ª fonte (gatilho de tempo: o índice de busca da F2).
    # Como a de transcripts, NUNCA devolve despertar: quebrar texto em pedaço e
    # pedir vetor a uma API não precisa de um modelo. É ela que mantém a busca
    # por SENTIDO em dia — a busca por palavra não depende de fonte nenhuma,
    # porque `messages.search_tsv` é coluna gerada e fica pronta no INSERT.
    try:
        from bot.search.source import build as build_search
    except ImportError:
        logger.exception("SearchIndexSource indisponível — pacote bot.search faltando?")
    else:
        busca = build_search(kobe_home=kobe_home, bot_token=bot_token)
        if busca is not None:
            sources.append(busca)
            logger.info("source registrada: search-index")
        else:
            logger.info(
                "source search-index NÃO registrada (SEARCH_INDEX_ENABLED off)"
            )

    # LucienSource — 5ª fonte (gatilho por acúmulo: o registro de estado da F3).
    # Como as duas anteriores, NUNCA devolve despertar — e aqui isso é decisão
    # de arquitetura, não de economia: o despertar acordaria um `claude -p` que
    # ESCREVERIA ele mesmo no registro, e a F3 inteira existe para que o modelo
    # proponha e o código decida. LUCIEN dispara um worker detached, que chama o
    # modelo e valida a resposta antes de gravar.
    #
    # Ela também não faz o trabalho dentro do `tick()`, diferente das fontes de
    # transcript e de busca: uma chamada de modelo leva dezenas de segundos, e o
    # Keyko é single-threaded — travar o laço travaria os ALERTAS, onde atraso é
    # falha que o operador vê.
    try:
        from bot.lucien.source import build as build_lucien
    except ImportError:
        logger.exception("LucienSource indisponível — pacote bot.lucien faltando?")
    else:
        lucien = build_lucien(kobe_home=kobe_home, bot_token=bot_token)
        if lucien is not None:
            sources.append(lucien)
            logger.info("source registrada: lucien")
        else:
            logger.info("source lucien NÃO registrada (LUCIEN_ENABLED off)")

    return sources
