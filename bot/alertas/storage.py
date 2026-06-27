"""Persistência de Alertas no filesystem.

Layout (flat, em `user-data/alertas/`):

    <id>.yaml            definição + estado (seções demarcadas)
    <id>.eventos.jsonl   log append-only (confirmações, disparos)
    .<id>.lock           sentinel pro flock (criado vazio na 1ª vez)
    .keyko-alertas.json  estado interno da AlertasSource (offsets de leitura)

Concorrência (idêntico a `bot/mission_control/storage.py`):

- A `AlertasSource` (daemon Keyko) e os handlers de slash command podem
  escrever no mesmo `<id>.yaml`. Pra evitar lost update, toda escrita é
  envolvida em `flock(LOCK_EX)` no `.<id>.lock` (timeout 5s). Dentro da
  seção crítica a escrita é atômica: grava em tmp + `os.replace` (rename
  atômico POSIX no mesmo filesystem).
- `<id>.eventos.jsonl` é append-only com `open("a")`. Writes pequenos
  (<PIPE_BUF, ~4KB) são atômicos em POSIX — nossas linhas ficam bem
  abaixo. A source lê com offset persistido.

ID:

- slug derivado do título (via `topic_manager.slugify`), truncado em 5
  palavras significativas. Ex.: "Marcar barbearia" → `marcar-barbearia`.
  Colisão ganha sufixo `-2`, `-3`... Sem prefixo de data (alertas são
  longevos, não datados como Missões).

Fuso: America/Sao_Paulo em tudo — o operador fala em horário Brasil, o
servidor pode estar em UTC.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

from bot.alertas.models import Alerta, Evento, TipoEvento
from bot.topic_manager import slugify


logger = logging.getLogger("kobe.alertas.storage")

OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")

LOCK_TIMEOUT_SECONDS = 5.0
SLUG_MAX_PALAVRAS = 5

# Arquivo de estado interno da source (offsets). Mora na raiz de alertas.
ARQUIVO_SOURCE_STATE = ".keyko-alertas.json"


def now_iso() -> str:
    return datetime.now(OPERATOR_TZ).isoformat(timespec="seconds")


def hoje_str() -> str:
    return datetime.now(OPERATOR_TZ).strftime("%Y-%m-%d")


# --- paths --------------------------------------------------------------

def alertas_root(kobe_home: Path) -> Path:
    return kobe_home / "user-data" / "alertas"


def _path_yaml(kobe_home: Path, alerta_id: str) -> Path:
    return alertas_root(kobe_home) / f"{alerta_id}.yaml"


def _path_eventos(kobe_home: Path, alerta_id: str) -> Path:
    return alertas_root(kobe_home) / f"{alerta_id}.eventos.jsonl"


def _path_lock(kobe_home: Path, alerta_id: str) -> Path:
    return alertas_root(kobe_home) / f".{alerta_id}.lock"


# --- geração de id ------------------------------------------------------

def gerar_id(kobe_home: Path, titulo: str) -> str:
    """Slug do título com sufixo `-N` em colisão. Sem prefixo de data."""
    slug_full = slugify(titulo)
    palavras = [w for w in slug_full.split("-") if len(w) >= 3][:SLUG_MAX_PALAVRAS]
    slug = "-".join(palavras) if palavras else "alerta"

    candidato = slug
    n = 2
    while _path_yaml(kobe_home, candidato).exists():
        candidato = f"{slug}-{n}"
        n += 1
    return candidato


# --- lock ---------------------------------------------------------------

class LockTimeoutError(Exception):
    """Não conseguimos pegar o lock dentro do timeout."""


@contextmanager
def _file_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """Lock exclusivo POSIX via fcntl.flock, com polling até timeout.

    Espelha `bot/mission_control/storage._file_lock`. O fd é liberado no close;
    damos un-lock explícito por higiene.
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


def _monotonic() -> float:
    import time
    return time.monotonic()


def _sleep(s: float) -> None:
    import time
    time.sleep(s)


# --- <id>.yaml (CRUD com lock) -----------------------------------------

def existe(kobe_home: Path, alerta_id: str) -> bool:
    return _path_yaml(kobe_home, alerta_id).is_file()


def carregar(kobe_home: Path, alerta_id: str) -> Alerta:
    """Lê `<id>.yaml`. Sem lock — leitura pura é segura porque a escrita é
    atômica (rename) e um `read_text` vê sempre uma versão consistente."""
    text = _path_yaml(kobe_home, alerta_id).read_text(encoding="utf-8")
    return Alerta.from_yaml(text)


def salvar(kobe_home: Path, alerta: Alerta) -> None:
    """Escreve `<id>.yaml` em modo atômico (tempfile + rename).

    **Sem lock** — o caller é responsável por estar dentro de
    `with mutar(...)`, OU ser a criação inicial (handler monta o esqueleto
    e materializa, ninguém mais conhece o id ainda).
    """
    path = _path_yaml(kobe_home, alerta.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, alerta.to_yaml())


@contextmanager
def mutar(kobe_home: Path, alerta_id: str) -> Iterator[Alerta]:
    """Context manager read-modify-write com lock fcntl.

    Uso:

        with storage.mutar(kobe_home, "barbearia") as alerta:
            alerta.estado.status = StatusAlerta.CONFIRMADO.value
            # ao sair, escreve atomicamente

    Levanta FileNotFoundError se o alerta não existe, LockTimeoutError se
    o lock estourar.
    """
    lock_path = _path_lock(kobe_home, alerta_id)
    with _file_lock(lock_path):
        alerta = carregar(kobe_home, alerta_id)
        yield alerta
        salvar(kobe_home, alerta)


def apagar(kobe_home: Path, alerta_id: str) -> None:
    """Remove o `<id>.yaml`, `<id>.eventos.jsonl` e `.<id>.lock`. Pega o
    lock antes pra não correr com uma escrita em curso."""
    lock_path = _path_lock(kobe_home, alerta_id)
    with _file_lock(lock_path):
        for p in (
            _path_yaml(kobe_home, alerta_id),
            _path_eventos(kobe_home, alerta_id),
        ):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
    # O lockfile em si fica (sentinel barato); remover fora da seção crítica.
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _write_atomic(path: Path, content: str) -> None:
    """Escrita atômica via tempfile no mesmo diretório + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- <id>.eventos.jsonl (append-only) ----------------------------------

def append_evento(
    kobe_home: Path,
    alerta_id: str,
    tipo: TipoEvento | str,
    *,
    dados: Optional[dict] = None,
) -> Evento:
    """Append-only, sem lock. Writes pequenos em modo 'a' são atômicos em
    POSIX (até ~4KB) — nossas linhas ficam bem abaixo. Retorna o Evento."""
    tipo_str = tipo.value if isinstance(tipo, TipoEvento) else tipo
    evento = Evento(ts=now_iso(), tipo=tipo_str, dados=dados or {})
    path = _path_eventos(kobe_home, alerta_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = evento.to_json_line() + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
    return evento


def ler_eventos_a_partir(
    kobe_home: Path,
    alerta_id: str,
    *,
    offset_bytes: int = 0,
) -> tuple[list[Evento], int]:
    """Lê eventos a partir de `offset_bytes`. Devolve (eventos, novo_offset).

    Usado pela AlertasSource pra processar só linhas novas a cada tick.
    Linha incompleta no fim (write em curso) é descartada do processamento
    mas NÃO consumida do offset — próxima leitura pega de novo.
    """
    path = _path_eventos(kobe_home, alerta_id)
    if not path.is_file():
        return [], offset_bytes

    eventos: list[Evento] = []
    with path.open("rb") as fh:
        fh.seek(offset_bytes)
        buf = fh.read()
        novo_offset = offset_bytes
        consumido = 0
        for raw_line in buf.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                break  # incompleta — para aqui
            consumido += len(raw_line)
            try:
                evento = Evento.from_json_line(raw_line.decode("utf-8").rstrip())
                eventos.append(evento)
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
                logger.warning(
                    "evento mal-formado em %s offset~%d — pulando",
                    path, novo_offset + consumido,
                )
        novo_offset += consumido
    return eventos, novo_offset


# --- listagem ----------------------------------------------------------

def listar_alertas(
    kobe_home: Path,
    *,
    apenas_vivos: bool = False,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
) -> list[Alerta]:
    """Varre `user-data/alertas/*.yaml` e devolve Alertas parseados.

    Filtros opcionais:
    - `apenas_vivos`: exclui alertas terminais (concluido).
    - `chat_id`+`thread_id`: só alertas deste tópico.

    Ignora arquivos ocultos (.keyko-alertas.json, .lock) e os `.eventos.jsonl`.
    """
    root = alertas_root(kobe_home)
    if not root.is_dir():
        return []
    out: list[Alerta] = []
    for sub in sorted(root.iterdir()):
        if sub.name.startswith("."):
            continue
        if not sub.name.endswith(".yaml") or sub.name.endswith(".eventos.jsonl"):
            continue
        alerta_id = sub.name[: -len(".yaml")]
        try:
            alerta = carregar(kobe_home, alerta_id)
        except Exception:  # noqa: BLE001 — yaml inválido/corrompido não derruba listagem
            logger.warning("yaml inválido em %s — pulando", sub, exc_info=True)
            continue
        if apenas_vivos and alerta.is_terminal():
            continue
        if chat_id is not None and alerta.chat_id != chat_id:
            continue
        if thread_id is not None and alerta.thread_id != thread_id:
            continue
        out.append(alerta)
    return out
