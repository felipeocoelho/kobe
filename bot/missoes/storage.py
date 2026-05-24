"""Persistência de Missões no filesystem.

Layout por missão (`user-data/missoes/<id>/`):

    estado.json         view materializada (Missao serializada)
    eventos.jsonl       log append-only de tudo que aconteceu
    .lock               sentinel pro flock (criado vazio na 1ª vez)
    logs/T<n>.log       stdout/stderr de cada subtarefa (caso 'ad-hoc-prompt')
    orquestrador.log    stdout/stderr de cada invocação do orquestrador
    outputs/T<n>.<ext>  saída final de cada subtarefa (Markdown, JSON, etc.)

Concorrência:

- Tanto o orquestrador (Claude rodando em background) quanto o Keyko
  (daemon Python) escrevem em `estado.json`. Pra evitar lost update,
  toda escrita é envolvida em `flock(LOCK_EX)` no `.lock` da missão
  (timeout 5s). Dentro da seção crítica, escrita é atômica: grava em
  `<arquivo>.tmp` e dá `os.rename` — POSIX garante que outro leitor vê
  ou o estado velho ou o novo, nunca um meio-termo.
- `eventos.jsonl` é append-only com seek pro fim. Sem lock — appends
  POSIX em arquivo aberto em modo "a" são atômicos pra writes < PIPE_BUF
  (4KB), e nossas linhas de evento ficam bem abaixo disso.

ID:

- `YYYY-MM-DD-<slug>` onde slug vem do objetivo via `topic_manager.slugify`,
  truncado em 5 palavras significativas. Colisão no mesmo dia ganha
  sufixo `-2`, `-3`...
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional
from zoneinfo import ZoneInfo

from bot.missoes.models import Evento, Missao, TipoEvento
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


def _path_estado(kobe_home: Path, missao_id: str) -> Path:
    return missao_dir(kobe_home, missao_id) / "estado.json"


def _path_eventos(kobe_home: Path, missao_id: str) -> Path:
    return missao_dir(kobe_home, missao_id) / "eventos.jsonl"


def _path_lock(kobe_home: Path, missao_id: str) -> Path:
    return missao_dir(kobe_home, missao_id) / ".lock"


def path_log_tarefa(kobe_home: Path, missao_id: str, tarefa_id: str) -> Path:
    return missao_dir(kobe_home, missao_id) / "logs" / f"{tarefa_id}.log"


def path_output_tarefa(
    kobe_home: Path, missao_id: str, tarefa_id: str, ext: str = "md"
) -> Path:
    return missao_dir(kobe_home, missao_id) / "outputs" / f"{tarefa_id}.{ext}"


def path_log_orquestrador(kobe_home: Path, missao_id: str) -> Path:
    return missao_dir(kobe_home, missao_id) / "orquestrador.log"


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


# --- estado.json (CRUD com lock) ---------------------------------------

def existe(kobe_home: Path, missao_id: str) -> bool:
    return _path_estado(kobe_home, missao_id).is_file()


def carregar(kobe_home: Path, missao_id: str) -> Missao:
    """Lê `estado.json`. Não pega lock — leitura pura é segura porque
    escrita é atômica (rename) e Python `open().read()` numa única
    syscall vê sempre uma versão consistente."""
    text = _path_estado(kobe_home, missao_id).read_text(encoding="utf-8")
    return Missao.from_json(text)


def salvar(kobe_home: Path, missao: Missao) -> None:
    """Escreve `estado.json` em modo atômico (tempfile + rename).

    **Sem lock** — o caller responsável é quem deve estar dentro de
    `with mutar(...)`. Função separada porque às vezes a gente quer só
    materializar um Missao construído fora (ex.: handler do /missao
    cria o esqueleto inicial).
    """
    estado = _path_estado(kobe_home, missao.id)
    estado.parent.mkdir(parents=True, exist_ok=True)
    # Atualiza timestamp em toda escrita — vira "last write" pra UI.
    missao.atualizado_em = now_iso()
    _write_atomic(estado, missao.to_json())


@contextmanager
def mutar(kobe_home: Path, missao_id: str) -> Iterator[Missao]:
    """Context manager read-modify-write com lock fcntl.

    Uso:

        with storage.mutar(kobe_home, "2026-05-23-...") as missao:
            missao.status = StatusMissao.EM_ANDAMENTO.value
            # ao sair, escreve atomicamente

    Se a missão não existir, levanta FileNotFoundError. Se o lock estourar,
    levanta LockTimeoutError.
    """
    lock_path = _path_lock(kobe_home, missao_id)
    with _file_lock(lock_path):
        missao = carregar(kobe_home, missao_id)
        yield missao
        salvar(kobe_home, missao)


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


# --- eventos.jsonl (append-only) --------------------------------------

def append_evento(
    kobe_home: Path,
    missao_id: str,
    tipo: TipoEvento | str,
    *,
    tarefa_id: Optional[str] = None,
    dados: Optional[dict] = None,
) -> Evento:
    """Append-only, sem lock. Writes pequenos em modo 'a' são atômicos
    em POSIX (até PIPE_BUF, ~4KB) — nossas linhas ficam muito abaixo.

    Retorna o Evento que foi gravado pra caller poder logar.
    """
    tipo_str = tipo.value if isinstance(tipo, TipoEvento) else tipo
    evento = Evento(
        ts=now_iso(),
        tipo=tipo_str,
        tarefa_id=tarefa_id,
        dados=dados or {},
    )
    path = _path_eventos(kobe_home, missao_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = evento.to_json_line() + "\n"
    # Modo 'a' com encoding explícito. `os.write` no fd seria mais atômico,
    # mas perderia o handling de encoding — a janela de risco é mínima
    # (write único de <200 bytes) e o impacto é nulo (linha mal-formada
    # vira erro no Keyko, que loga e segue na próxima).
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
    return evento


def ler_eventos_a_partir(
    kobe_home: Path,
    missao_id: str,
    *,
    offset_bytes: int = 0,
) -> tuple[list[Evento], int]:
    """Lê eventos a partir de `offset_bytes`. Devolve (eventos, novo_offset).

    Usado pelo Keyko (vide MissoesSource.tick) pra processar só linhas
    novas a cada ciclo. Linha incompleta no fim (write em progresso) é
    descartada do processamento mas NÃO consumida do offset — próxima
    leitura pega de novo do começo dela.
    """
    path = _path_eventos(kobe_home, missao_id)
    if not path.is_file():
        return [], offset_bytes

    eventos: list[Evento] = []
    with path.open("rb") as fh:
        fh.seek(offset_bytes)
        buf = fh.read()
        novo_offset = offset_bytes
        # Processa linha a linha; só avança o offset depois de cada \n
        # encontrado. Se o arquivo terminou no meio de uma linha (write
        # em curso), o resto vira pendência pra próxima leitura.
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
                # Linha foi consumida (avançamos o offset) — não vamos
                # ficar travados num evento corrompido pra sempre.
        novo_offset += consumido
    return eventos, novo_offset


# --- listagem (usada pelo handler e por /missao_lista) ----------------

def listar_missoes(
    kobe_home: Path,
    *,
    apenas_ativas: bool = False,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
) -> list[Missao]:
    """Varre `user-data/missoes/*/estado.json` e devolve Missoes parseadas.

    Filtros opcionais:
    - `apenas_ativas`: só status planejada/em-andamento.
    - `chat_id`+`thread_id`: só missões deste tópico (None pra thread_id
      casa com chat raiz / general).

    IO trivial — pasta raramente tem mais que dezenas de subdirs. Sem
    cache na Fase 1.
    """
    root = missoes_root(kobe_home)
    if not root.is_dir():
        return []
    out: list[Missao] = []
    for sub in sorted(root.iterdir()):
        # Ignora arquivos soltos (ex.: .keyko-state.json do Keyko)
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        estado_path = sub / "estado.json"
        if not estado_path.is_file():
            continue
        try:
            missao = carregar(kobe_home, sub.name)
        except (OSError, json.JSONDecodeError, KeyError):
            logger.warning("estado.json inválido em %s — pulando", sub, exc_info=True)
            continue
        if apenas_ativas and missao.is_terminal():
            continue
        if chat_id is not None and missao.chat_id != chat_id:
            continue
        if thread_id is not None and missao.thread_id != thread_id:
            continue
        out.append(missao)
    return out


def find_missao_ativa(
    kobe_home: Path, chat_id: int, thread_id: Optional[int]
) -> Optional[Missao]:
    """Devolve a primeira missão NÃO-terminal deste tópico, ou None.

    Pra Fase 1 assumimos no máximo 1 missão ativa por tópico — o handler
    do /missao rejeita criar outra se já tem uma rodando.
    """
    ativas = listar_missoes(
        kobe_home, apenas_ativas=True, chat_id=chat_id, thread_id=thread_id
    )
    return ativas[0] if ativas else None
