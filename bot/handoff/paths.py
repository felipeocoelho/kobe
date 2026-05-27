"""Convenções de path do handoff doc do Hal.

Layout:
    <kobe_home>/.local/handoffs/<topic-slug>/handoff.md
    <kobe_home>/.local/handoffs/<topic-slug>/arquivados/<YYYY-MM-DD>-<session_id>.md

`.local/` está no `.gitignore` do Kobe — handoffs ficam fora do repo
(é memória operacional, não código).
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


logger = logging.getLogger("kobe.handoff.paths")

OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")


def _topic_dir(kobe_home: Path, topic_slug: str) -> Path:
    return kobe_home / ".local" / "handoffs" / topic_slug


def active_handoff_path(kobe_home: Path, topic_slug: str) -> Path:
    """Path do handoff ativo do tópico (sobrescrito por `/handoff`)."""
    return _topic_dir(kobe_home, topic_slug) / "handoff.md"


def archive_path_for_session(
    kobe_home: Path, topic_slug: str, session_id: str
) -> Path:
    """Path do handoff arquivado de uma sessão específica.

    Nome com data em BRT — operador vai abrir esse arquivo manualmente
    pra retomar contexto, então o nome legível ganha do uuid puro.
    """
    today = datetime.now(OPERATOR_TZ).strftime("%Y-%m-%d")
    return (
        _topic_dir(kobe_home, topic_slug)
        / "arquivados"
        / f"{today}-{session_id}.md"
    )


def rotate_active_to_archive(
    kobe_home: Path, topic_slug: str, session_id: str
) -> Optional[Path]:
    """Se já existe `handoff.md` ativo no tópico, move pra `arquivados/`.

    Usado por `/handoff` antes de escrever o doc novo: o anterior é
    preservado em arquivados (com a `session_id` da sessão CORRENTE,
    porque é dela que o doc anterior trata — mesma sessão, snapshot
    mais antigo). Retorna o path do arquivado ou None se não havia
    nada pra rotacionar.
    """
    active = active_handoff_path(kobe_home, topic_slug)
    if not active.exists():
        return None
    target = archive_path_for_session(kobe_home, topic_slug, session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Se já existe um arquivado com esse nome (2x /handoff no mesmo dia,
    # mesma sessão), acrescenta sufixo de hora-minuto pra não sobrescrever.
    if target.exists():
        stamp = datetime.now(OPERATOR_TZ).strftime("%H%M")
        target = target.with_name(f"{target.stem}-{stamp}.md")
    try:
        active.rename(target)
    except OSError:
        logger.exception(
            "falha rotacionando handoff ativo %s → %s", active, target
        )
        return None
    return target


def ensure_topic_handoff_dirs(kobe_home: Path, topic_slug: str) -> None:
    """Garante que o diretório do tópico (e `arquivados/`) existe."""
    (_topic_dir(kobe_home, topic_slug) / "arquivados").mkdir(
        parents=True, exist_ok=True
    )
