"""A fonte do Keyko que mantém o índice de documentos em dia.

Mesmo desenho da fonte de busca da conversa (`bot/search/source.py`): nunca devolve
despertar (varrer disco e pedir vetor não precisa de um modelo), nunca levanta (o
Keyko é single-threaded), e erro de embedding vira aviso no log — no máximo um por
hora, porque aviso por minuto vira ruído, e ruído é ignorado.

Intervalo padrão de 10 minutos: documento muda devagar, e um tick varre só
metadados (stat) até achar o que mudou.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

from bot.docs import indexer
from bot.keyko.models import Despertar

logger = logging.getLogger("kobe.docs.source")

INTERVALO_PADRAO_S = 600.0
INTERVALO_MINIMO_S = 30.0
INTERVALO_AVISO_S = 3600.0


def _intervalo() -> float:
    bruto = (os.environ.get("DOC_INDEX_INTERVAL_S") or "").strip()
    try:
        valor = float(bruto) if bruto else INTERVALO_PADRAO_S
    except ValueError:
        valor = INTERVALO_PADRAO_S
    return max(INTERVALO_MINIMO_S, valor)


class DocIndexSource:
    def __init__(self, *, kobe_home: Path, db_factory) -> None:
        self._kobe_home = Path(kobe_home)
        self._db_factory = db_factory
        self._db = None
        self._ultimo_aviso = 0.0

    @property
    def nome(self) -> str:
        return "doc-index"

    @property
    def intervalo_s(self) -> float:
        return _intervalo()

    def tick(self) -> list[Despertar]:
        if not indexer.indexer_enabled():
            return []
        try:
            if self._db is None:
                self._db = self._db_factory()
            r = indexer.tick(self._db, self._kobe_home)
        except Exception:  # noqa: BLE001
            logger.exception("doc-index: falha ao abrir a ponte com o banco")
            self._db = None
            return []
        if r.fez_algo:
            logger.info(
                "doc-index: %d vistos, %d novos, %d mudados, %d ausentes, %d trechos, "
                "%d embeddados, %d produções",
                r.vistos, r.novos, r.mudados, r.ausentes, r.trechos_criados,
                r.trechos_embeddados, r.producoes,
            )
        for aviso in r.avisos[:5]:
            logger.warning("doc-index: %s", aviso)
        if r.erro and time.time() - self._ultimo_aviso >= INTERVALO_AVISO_S:
            self._ultimo_aviso = time.time()
            logger.warning(
                "doc-index: o índice de documentos PAROU de crescer — %s. A busca por "
                "palavra nos documentos continua; a por sentido fica atrasada.", r.erro)
        return []


def build(*, kobe_home=None, bot_token: str = "") -> Optional[DocIndexSource]:
    if not indexer.indexer_enabled():
        return None

    def _abrir():
        from bot.config import load_config
        from bot.db import build_client

        return build_client(load_config())

    if kobe_home is None:
        from bot.config import load_config

        kobe_home = load_config().kobe_home
    return DocIndexSource(kobe_home=Path(kobe_home), db_factory=_abrir)
