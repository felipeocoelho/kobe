"""Persistencia em filesystem das SALAS DE MISSAO (Mission Control).

Layout por missao (`user-data/missoes/<id>/`):

    sala.json           estado de runtime da sala (status, pid, turn_count...)
    sala.sysprompt.txt  system prompt de estrategista
    sala-launch.sh      script que vira o comando da sala tmux
    sala.log            log do worker que monitora a sala
    workspace/          scratch da missao (raciocinio.md, rascunhos/, brief)

NOTA HISTORICA (2026-08-25). Este modulo servia DOIS sistemas que dividiam
esta mesma pasta: o Sistema de Missoes v0.13 (formato `estado.json`, comandos
`/missao*`) e as salas de missao (formato `sala.json`, abertas por linguagem
natural). O v0.13 foi aposentado; as salas ficaram. A camada `estado.json`
inteira saiu daqui — `carregar`/`salvar`/`mutar`, o log append-only de
`eventos.jsonl`, `listar_missoes` e `find_missao_ativa`. O que sobrou e a
camada de PATHS, de geracao de id e de escrita atomica: e dela que as salas
vivem. A separacao entre os dois sistemas sempre foi por ARQUIVO, nunca por
pasta — quem mexer aqui precisa saber disso antes de apagar qualquer linha.

Concorrencia: escrita atomica via `_write_atomic` (tempfile + `os.rename` no
mesmo diretorio, que POSIX garante atomico) e `_file_lock` (flock exclusivo
com timeout) para quem precisar serializar leitura-modificacao-escrita.

ID: `YYYY-MM-DD-<slug>`, com slug vindo do objetivo via `topic_manager.slugify`
truncado em 5 palavras significativas. Colisao no mesmo dia ganha sufixo
`-2`, `-3`...
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

from bot.topic_manager import slugify


logger = logging.getLogger("kobe.missoes.storage")

# Mesmo fuso usado em claude_runner — o operador fala em horário Brasil,
# servidor pode estar em UTC. Manter consistência.
OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")

LOCK_TIMEOUT_SECONDS = 5.0
SLUG_MAX_PALAVRAS = 5


def now_iso() -> str:
    return datetime.now(OPERATOR_TZ).isoformat(timespec="seconds")


# --- paths --------------------------------------------------------------

def missoes_root(kobe_home: Path) -> Path:
    return kobe_home / "user-data" / "missoes"


def missao_dir(kobe_home: Path, missao_id: str) -> Path:
    return missoes_root(kobe_home) / missao_id


# --- paths da sala estrategista (forma b — sala-única visível) ----------
# Convivem com estado.json/eventos.jsonl na MESMA pasta da missão. A sala é a
# camada de runtime (processo/tmux); o estado.json segue sendo a metadata da
# missão. Layout aprovado no plano (decisão 9 / §3 pend.2).

def path_sala_json(kobe_home: Path, missao_id: str) -> Path:
    """State de runtime da sala (status/pid/turn_count/...). Gerido pelo
    worker via `bot.sala.state`."""
    return missao_dir(kobe_home, missao_id) / "sala.json"


def path_sala_sysprompt(kobe_home: Path, missao_id: str) -> Path:
    """System prompt de estrategista, file-based (`--append-system-prompt-file`)."""
    return missao_dir(kobe_home, missao_id) / "sala.sysprompt.txt"


def path_sala_launcher(kobe_home: Path, missao_id: str) -> Path:
    """Script bash que vira o comando da sala tmux."""
    return missao_dir(kobe_home, missao_id) / "sala-launch.sh"


def path_sala_log(kobe_home: Path, missao_id: str) -> Path:
    """Log do worker da sala (stdout/stderr do processo de monitor)."""
    return missao_dir(kobe_home, missao_id) / "sala.log"


def workspace_dir(kobe_home: Path, missao_id: str) -> Path:
    """Scratch bruto da missão (raciocinio.md, rascunhos/, handoff-brief.md).
    Separado da KB curada — a destilação durável é passo explícito ao fim."""
    return missao_dir(kobe_home, missao_id) / "workspace"


def ensure_workspace(kobe_home: Path, missao_id: str) -> Path:
    """Cria o `workspace/` (e `rascunhos/`) da missão. Idempotente."""
    ws = workspace_dir(kobe_home, missao_id)
    (ws / "rascunhos").mkdir(parents=True, exist_ok=True)
    return ws


# --- geração de id ------------------------------------------------------

def gerar_id(kobe_home: Path, objetivo: str, *, hoje: Optional[str] = None) -> str:
    """`YYYY-MM-DD-<slug>` com sufixo `-N` em colisão.

    `hoje` parametrizado pra teste; default = data atual no fuso do operador.
    """
    if hoje is None:
        hoje = datetime.now(OPERATOR_TZ).strftime("%Y-%m-%d")
    slug_full = slugify(objetivo)
    # Pega primeiras N palavras significativas (>= 3 chars) pra evitar id
    # gigante. Se nem o slug deu, usa "missao" como fallback.
    palavras = [w for w in slug_full.split("-") if len(w) >= 3][:SLUG_MAX_PALAVRAS]
    slug = "-".join(palavras) if palavras else "missao"

    base = f"{hoje}-{slug}"
    candidato = base
    n = 2
    while missao_dir(kobe_home, candidato).exists():
        candidato = f"{base}-{n}"
        n += 1
    return candidato


# --- lock ---------------------------------------------------------------

class LockTimeoutError(Exception):
    """Não conseguimos pegar o lock dentro do timeout."""


@contextmanager
def _file_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Lock exclusivo POSIX via fcntl.flock, com polling até timeout.

    `fcntl.flock` em modo não-bloqueante (LOCK_NB) retorna BlockingIOError
    se não conseguir; a gente faz busy-wait com sleep curto. fcntl libera
    automaticamente quando o file descriptor é fechado, mas damos un-lock
    explícito por higiene.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    deadline = _monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if _monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"timeout {timeout}s pegando lock {lock_path}"
                    )
                _sleep(0.05)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


# Indireção pra facilitar mock em teste (e evitar import circular pesado).
def _monotonic() -> float:
    import time
    return time.monotonic()


def _sleep(s: float) -> None:
    import time
    time.sleep(s)


def _write_atomic(path: Path, content: str) -> None:
    """Escrita atômica via tempfile no mesmo diretório + os.rename.

    Diretório precisa ser o mesmo do destino pra `rename` funcionar
    atomicamente (POSIX só garante atomicidade dentro do mesmo filesystem).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # delete=False porque vamos renomear; o tempfile vira o arquivo final.
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)  # rename atômico
    except Exception:
        # Se algo deu errado, limpa o tempfile pra não acumular lixo.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
