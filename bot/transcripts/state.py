"""O estado do coletor — onde ele parou, e quando rodou pela última vez.

Duas responsabilidades, e a segunda é a que quase ninguém lembra de construir:

1. **Onde parou.** Por sessão: até que byte já foi copiado, e as impressões
   digitais da origem (hash do começo, tamanho, inode). É o que torna a cópia
   incremental e idempotente.

2. **Quando rodou.** `last_success_at`, no nível do coletor inteiro. Existe por
   causa da lacuna L4 do briefing, que merece ser citada porque é a única lacuna
   do plano de testes que o autor classificou como *"a com maior chance de virar
   surpresa"*:

   > *O relógio. Duas peças rodam por tempo. Testar "acontece toda sexta"
   > exigiria esperar até sexta. (…) Um erro de agendamento — roda no dia
   > errado, ou não roda — **não é pego por nenhum teste desta lista**.*

   Um coletor que simplesmente pare de rodar não produz erro nenhum: produz
   **silêncio**, e o silêncio é indistinguível de "não havia nada novo". Meses
   depois se descobre que o acervo parou num dia qualquer. A marca de execução é
   o que transforma esse silêncio em algo observável.

A TRAVA, E POR QUE ELA É NÃO-BLOQUEANTE
----------------------------------------
Duas passadas simultâneas escreveriam o mesmo destino a partir do mesmo
deslocamento, duplicando linha. `flock` exclusivo resolve — e é o mesmo remédio
que o merge de worktree do Coder já usa.

Ela é **não-bloqueante** de propósito: se já há uma passada rodando, a segunda
**desiste na hora** em vez de esperar. Quem chama isto é um relógio, e relógio
que espera acumula fila — bastaria uma passada lenta pra o disparo seguinte ficar
pendurado, e o seguinte atrás dele. Desistir é correto porque a passada que já
está rodando vai fazer exatamente o mesmo trabalho.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

STATE_VERSION = 1
STATE_FILENAME = ".collector-state.json"
LOCK_FILENAME = ".collector.lock"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_hours(value: Optional[str]) -> Optional[float]:
    dt = parse_iso(value)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


class LockBusy(RuntimeError):
    """Já há uma passada rodando. Não é erro — é a trava funcionando."""


@contextlib.contextmanager
def exclusive_lock(dest_root: Path) -> Iterator[None]:
    """Trava exclusiva, não-bloqueante, sobre um arquivo dedicado.

    A trava fica num arquivo SEPARADO do estado, e não no próprio estado, porque
    o estado é reescrito por substituição atômica (`rename`) — e travar um
    arquivo que vai ser substituído trava um inode que deixou de ser o arquivo.
    O erro seria silencioso: duas passadas achando que têm a trava.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    lock_path = dest_root / LOCK_FILENAME
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LockBusy(
                "já há uma coleta em andamento — esta passada foi dispensada."
            ) from exc
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "last_run_at": None,
        "last_success_at": None,
        "last_stale_notice_at": None,
        "sessions": {},
    }


def load(dest_root: Path) -> dict[str, Any]:
    """Lê o estado. Um arquivo corrompido NÃO derruba o coletor.

    O pior caso de um estado ilegível é recopiar tudo — que é caro em disco e
    barato em consequência, porque a recópia preserva o que existia (ver
    `collector`). O pior caso de *abortar* seria parar de colher, e aí o dado
    perecível continua evaporando. Entre os dois, recopiar ganha sempre.
    """
    path = dest_root / STATE_FILENAME
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict) or "sessions" not in data:
        return empty_state()
    base = empty_state()
    base.update(data)
    if not isinstance(base.get("sessions"), dict):
        base["sessions"] = {}
    return base


def save(dest_root: Path, state: dict[str, Any]) -> None:
    """Grava por substituição atômica: escreve ao lado e renomeia.

    Sem isto, um desligamento no meio da escrita deixaria um JSON truncado — e
    o estado do coletor é justamente a peça cuja perda faz o coletor duplicar ou
    reprocessar. `rename` no mesmo sistema de arquivos é atômico.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    path = dest_root / STATE_FILENAME
    fd, tmp = tempfile.mkstemp(dir=str(dest_root), prefix=".collector-state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def mark_run(state: dict[str, Any], *, success: bool) -> None:
    """`last_run_at` marca a tentativa; `last_success_at`, o êxito.

    São dois campos e não um porque a diferença entre eles é o diagnóstico: se
    os dois envelhecem juntos, o relógio parou; se só o de êxito envelhece, o
    relógio está batendo e o coletor está falhando. Um campo só não distingue.
    """
    state["last_run_at"] = now_iso()
    if success:
        state["last_success_at"] = now_iso()
