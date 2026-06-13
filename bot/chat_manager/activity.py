"""Sinal de debounce e watermark do New Chat Manager.

Dois tipos de arquivo, ambos sob `<kobe_home>/user-data/chat-manager/`:

- `activity/<topic_id>.json` — tocado pelo BOT toda vez que grava uma
  mensagem do operador. Carrega `last_user_msg_at` (ISO) e o canal
  (chat_id/thread_id). É o relógio que o daemon lê pra debounce por
  silêncio: "ficou quieto > N segundos? então o operador parou — pode
  classificar o lote".

- `state/<topic_id>.json` — gerido pelo DAEMON. Carrega o watermark
  (`classified_through_at`, ISO) = created_at da última mensagem que já
  recebeu conversation_id. Mensagens com created_at > watermark e
  conversation_id NULL são o lote pendente. Guarda também
  `last_classified_run_at` (disjuntor de teto) e `active_conversation_id`.

Arquivo em user-data/ é suficiente (doc §5.6) — não precisa banco pro
relógio do debounce. Escrita atômica (tmp + rename) pra o daemon nunca
ler um JSON pela metade.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


logger = logging.getLogger("kobe.chat_manager.activity")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_dir(kobe_home: Path) -> Path:
    return Path(kobe_home) / "user-data" / "chat-manager"


def activity_dir(kobe_home: Path) -> Path:
    return _base_dir(kobe_home) / "activity"


def state_dir(kobe_home: Path) -> Path:
    return _base_dir(kobe_home) / "state"


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Activity (bot escreve, daemon lê)
# ---------------------------------------------------------------------------


def touch_activity(
    kobe_home: Path,
    *,
    topic_id: str,
    chat_id: int,
    thread_id: Optional[int],
) -> None:
    """Marca atividade do operador num topic. Best-effort — nunca levanta.

    Chamado pelo bot logo após gravar a mensagem do operador. Barato:
    um write de ~100 bytes. Falha aqui não pode derrubar o turno.
    """
    try:
        _atomic_write(
            activity_dir(kobe_home) / f"{topic_id}.json",
            {
                "topic_id": topic_id,
                "chat_id": chat_id,
                "thread_id": thread_id,
                "last_user_msg_at": _now_iso(),
            },
        )
    except OSError:
        logger.warning("touch_activity falhou topic=%s", topic_id, exc_info=True)


def list_activity(kobe_home: Path) -> list[dict]:
    """Lista todos os sinais de atividade conhecidos (um por topic tocado)."""
    d = activity_dir(kobe_home)
    if not d.is_dir():
        return []
    out: list[dict] = []
    for f in d.glob("*.json"):
        data = _read_json(f)
        if data and data.get("topic_id"):
            out.append(data)
    return out


# ---------------------------------------------------------------------------
# State / watermark (daemon escreve)
# ---------------------------------------------------------------------------


def read_state(kobe_home: Path, topic_id: str) -> dict:
    """Estado do daemon pra um topic. Dict vazio se nunca classificado."""
    return _read_json(state_dir(kobe_home) / f"{topic_id}.json") or {}


def write_state(
    kobe_home: Path,
    topic_id: str,
    *,
    classified_through_at: Optional[str],
    active_conversation_id: Optional[str],
) -> None:
    """Persiste watermark + ponteiro do quente. Best-effort."""
    try:
        _atomic_write(
            state_dir(kobe_home) / f"{topic_id}.json",
            {
                "topic_id": topic_id,
                "classified_through_at": classified_through_at,
                "active_conversation_id": active_conversation_id,
                "last_classified_run_at": _now_iso(),
            },
        )
    except OSError:
        logger.warning("write_state falhou topic=%s", topic_id, exc_info=True)
