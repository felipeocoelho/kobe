"""State JSON de uma sala — leitura/escrita atômica com flock.

Espelha a disciplina provada no incidente do Coder (2026-06-23): dois workers
concorrentes (o monitor de um turno anterior ainda vivo + o resume novo) faziam
read-modify-write intercalado e um clobberava o campo do outro — foi o que
travou `turn_count` em 1. O `patch_state` toma um flock EXCLUSIVO no
read-modify-write inteiro, num lockfile SEPARADO (sufixo `.lock`, fora de
qualquer glob `*.json`), serializando os patches: cada um lê o estado já com a
escrita anterior aplicada.

A escrita é atômica (tmp + rename) pra um leitor concorrente nunca ver um JSON
truncado — sempre o estado velho ou o novo, nunca um meio-termo.

Campos do state são livres (dict) — cada caller (Coder, Mission Control) põe os
que precisa. O núcleo só garante `last_activity` em todo patch.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    """ISO 8601 em UTC, segundos — mesma convenção do worker do Coder."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_state(state_path: Path) -> dict:
    return json.loads(Path(state_path).read_text(encoding="utf-8"))


def write_state(state_path: Path, state: dict) -> None:
    """Escrita atômica: tmp + rename. Evita state vazio/truncado se o processo
    morrer no meio da gravação (read concorrente do agente principal)."""
    state_path = Path(state_path)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def patch_state(state_path: Path, **fields) -> dict:
    """Read-modify-write serializado por flock exclusivo. Atualiza
    `last_activity` automaticamente (a menos que o caller passe o seu).
    Devolve o state já mesclado."""
    state_path = Path(state_path)
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    with open(lock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX)
            state = read_state(state_path)
            state.update(fields)
            state.setdefault("last_activity", now_iso())
            if "last_activity" not in fields:
                state["last_activity"] = now_iso()
            write_state(state_path, state)
        finally:
            with contextlib.suppress(Exception):
                fcntl.flock(lf, fcntl.LOCK_UN)
    return state
