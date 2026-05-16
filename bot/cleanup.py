"""Limpeza periódica de artefatos efêmeros em `.local/`.

Plugins do Kobe (atrus em particular) usam `.local/dispatched/<topic>/<job>/`
pra guardar state.json + stdout.log + stderr.log de cada job em background.
Sem rotação, isso acumula indefinidamente — bot rodando 6 meses encheria
o disco com lixo de jobs há muito terminados.

Estratégia:
- `dispatched/<topic>/<job>/`: TTL de 7 dias após `completed_at` em status
  `completed` ou `failed`. Jobs ainda `running` são intocados, mesmo
  antigos (pra não matar pipeline em curso).
- `atrus-*`, `pyannote-*`, scratch ad-hoc no topo de `.local/`: TTL de
  30 dias por mtime (sem state.json — usa atributo do filesystem).

O loop async vive junto com o bot: cleanup imediato no startup +
re-execução a cada 6h. Não precisa de cron externo nem systemd timer.

Política conservadora: erros em rm são logados mas não param o loop;
artefatos em uso ativo (PID vivo no state) são pulados em silêncio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


logger = logging.getLogger("kobe.cleanup")

# Intervalo padrão entre execuções do cleanup (em segundos).
DEFAULT_INTERVAL_SECONDS = 6 * 3600  # 6 horas

# TTLs em segundos.
DISPATCHED_TTL_SECONDS = 7 * 24 * 3600   # 7 dias após completed_at
ATRUS_CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 dias por mtime (touched ao usar)
ADHOC_TTL_SECONDS = 30 * 24 * 3600       # 30 dias por mtime

# Prefixos de pastas ad-hoc no topo de .local/ que entram na regra dos 30d.
# Pastas que NÃO batem com esses prefixos são preservadas (proteção contra
# rm acidental em scratch que o operador quer manter).
ADHOC_PREFIXES = ("atrus-", "pyannote-", "whisper-")

# `atrus-cache/` tem TTL próprio (7d). Excluímos do _cleanup_adhoc (que usaria
# 30d) e tratamos separado em _cleanup_atrus_cache. Cada subdir de cache
# (`atrus-cache/<sha1>/`) é uma entrada cacheada — TTL aplica por subdir, não
# pela pasta inteira.
_ATRUS_CACHE_DIR = "atrus-cache"


def _now_utc_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _parse_iso(s: str) -> Optional[float]:
    """Parse ISO-8601 com timezone → timestamp Unix. None em erro."""
    if not s:
        return None
    try:
        # `datetime.fromisoformat` aceita o que `_now_iso()` em kobe-dispatch produz.
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    """True se o processo `pid` existe e está vivo. False em qualquer dúvida."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def _dir_size_bytes(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
    except OSError:
        pass
    return total


def _cleanup_dispatched(local_root: Path, now_ts: float) -> dict:
    """Apaga jobs dispatchados com status terminal + idade > TTL.

    Layout: `.local/dispatched/<topic>/<job_id>/state.json`.
    Critérios pra apagar:
    - state.json carregável.
    - status em ("completed", "failed").
    - completed_at parseável e > TTL atrás.
    - PID não está mais vivo (defesa em profundidade — não deveria estar
      vivo se status é terminal, mas double-check é barato).

    Jobs em `running` ou com state.json malformado: pulados.
    """
    dispatched_root = local_root / "dispatched"
    stats = {"removed_jobs": 0, "kept_running": 0, "skipped_invalid": 0, "bytes_freed": 0}
    if not dispatched_root.is_dir():
        return stats

    for topic_dir in dispatched_root.iterdir():
        if not topic_dir.is_dir():
            continue
        for job_dir in topic_dir.iterdir():
            if not job_dir.is_dir():
                continue
            state_file = job_dir / "state.json"
            if not state_file.is_file():
                stats["skipped_invalid"] += 1
                continue
            try:
                state = json.loads(state_file.read_text())
            except (OSError, json.JSONDecodeError):
                stats["skipped_invalid"] += 1
                continue

            status = state.get("status", "")
            if status not in ("completed", "failed"):
                stats["kept_running"] += 1
                continue

            completed_at = _parse_iso(state.get("completed_at", ""))
            if completed_at is None:
                stats["skipped_invalid"] += 1
                continue
            age_seconds = now_ts - completed_at
            if age_seconds < DISPATCHED_TTL_SECONDS:
                continue  # ainda dentro do TTL

            pid = state.get("pid")
            if isinstance(pid, int) and _pid_alive(pid):
                # processo ainda vivo apesar do status terminal — não toca.
                # Cenário raro: estado escrito por engano, processo zombie, etc.
                stats["kept_running"] += 1
                continue

            size = _dir_size_bytes(job_dir)
            try:
                shutil.rmtree(job_dir)
                stats["removed_jobs"] += 1
                stats["bytes_freed"] += size
                logger.info(
                    "cleanup: removed dispatched job %s/%s (age=%.1fd, %d bytes)",
                    topic_dir.name, job_dir.name, age_seconds / 86400, size,
                )
            except OSError as exc:
                logger.warning("cleanup: falha removendo %s: %s", job_dir, exc)

        # Topic dir vazio após limpeza → remove pra não acumular dirs órfãs.
        try:
            if topic_dir.is_dir() and not any(topic_dir.iterdir()):
                topic_dir.rmdir()
        except OSError:
            pass

    return stats


def _cleanup_adhoc(local_root: Path, now_ts: float) -> dict:
    """Apaga pastas ad-hoc no topo de .local/ com mtime > 30d.

    Considera só pastas que casam com `ADHOC_PREFIXES` — evita apagar
    scratch que o operador (ou outro plugin) deixou propositalmente.
    `atrus-cache/` é excluído (tratado por _cleanup_atrus_cache com TTL próprio).
    """
    stats = {"removed_dirs": 0, "bytes_freed": 0}
    if not local_root.is_dir():
        return stats

    for entry in local_root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name in ("dispatched", _ATRUS_CACHE_DIR):
            continue  # tratados separadamente
        if not any(entry.name.startswith(p) for p in ADHOC_PREFIXES):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        age_seconds = now_ts - mtime
        if age_seconds < ADHOC_TTL_SECONDS:
            continue

        size = _dir_size_bytes(entry)
        try:
            shutil.rmtree(entry)
            stats["removed_dirs"] += 1
            stats["bytes_freed"] += size
            logger.info(
                "cleanup: removed ad-hoc dir %s (mtime age=%.1fd, %d bytes)",
                entry.name, age_seconds / 86400, size,
            )
        except OSError as exc:
            logger.warning("cleanup: falha removendo %s: %s", entry, exc)

    return stats


def _cleanup_atrus_cache(local_root: Path, now_ts: float) -> dict:
    """Apaga entradas de cache do atrus em `atrus-cache/<sha1>/` com mtime > 7d.

    Cada subdir é uma entrada (chave = hash de URL+diarize). O atrus dá touch
    no dir cada vez que o cache é reusado, então cache em uso ativo nunca
    expira. Cache órfão (transcrição feita uma vez, nunca repetida) some em
    7 dias.
    """
    cache_root = local_root / _ATRUS_CACHE_DIR
    stats = {"removed_entries": 0, "bytes_freed": 0}
    if not cache_root.is_dir():
        return stats

    for entry in cache_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        age_seconds = now_ts - mtime
        if age_seconds < ATRUS_CACHE_TTL_SECONDS:
            continue

        size = _dir_size_bytes(entry)
        try:
            shutil.rmtree(entry)
            stats["removed_entries"] += 1
            stats["bytes_freed"] += size
            logger.info(
                "cleanup: removed cache entry %s (mtime age=%.1fd, %d bytes)",
                entry.name, age_seconds / 86400, size,
            )
        except OSError as exc:
            logger.warning("cleanup: falha removendo cache %s: %s", entry, exc)

    # Se cache root ficou vazia, deixa quieta — atrus recria quando precisar.
    return stats


def cleanup_local_artifacts(kobe_home: Path) -> dict:
    """Roda uma rodada de cleanup. Retorna stats agregadas.

    Síncrona — caller que rodar em thread pra não bloquear o event loop
    (usar `asyncio.to_thread` ou similar).
    """
    local_root = kobe_home / ".local"
    if not local_root.is_dir():
        return {"skipped": "no .local dir"}

    now_ts = _now_utc_ts()
    started = time.monotonic()
    dispatched = _cleanup_dispatched(local_root, now_ts)
    atrus_cache = _cleanup_atrus_cache(local_root, now_ts)
    adhoc = _cleanup_adhoc(local_root, now_ts)
    elapsed = time.monotonic() - started

    total_bytes = (
        dispatched["bytes_freed"]
        + atrus_cache["bytes_freed"]
        + adhoc["bytes_freed"]
    )
    logger.info(
        "cleanup: jobs=%d, cache=%d, adhoc=%d, kept_running=%d, invalid=%d, "
        "bytes_freed=%d, elapsed=%.2fs",
        dispatched["removed_jobs"], atrus_cache["removed_entries"],
        adhoc["removed_dirs"], dispatched["kept_running"],
        dispatched["skipped_invalid"], total_bytes, elapsed,
    )
    return {
        "dispatched": dispatched,
        "atrus_cache": atrus_cache,
        "adhoc": adhoc,
        "elapsed_seconds": elapsed,
        "bytes_freed_total": total_bytes,
    }


async def cleanup_loop(
    kobe_home: Path, interval_seconds: int = DEFAULT_INTERVAL_SECONDS
) -> None:
    """Background task: roda cleanup imediato + repete a cada `interval_seconds`.

    Vive enquanto o bot estiver de pé. Não cancela em exceção — só loga e
    segue (defensivo: nunca deixar o loop morrer e perder a rotação).
    Use `asyncio.to_thread` pra não bloquear o event loop com filesystem I/O.
    """
    logger.info(
        "cleanup loop iniciado (intervalo=%ds, dispatched TTL=%dd, "
        "atrus-cache TTL=%dd, adhoc TTL=%dd)",
        interval_seconds,
        DISPATCHED_TTL_SECONDS // 86400,
        ATRUS_CACHE_TTL_SECONDS // 86400,
        ADHOC_TTL_SECONDS // 86400,
    )
    # Primeira rodada: imediata, pra recuperar de uptime anterior.
    try:
        await asyncio.to_thread(cleanup_local_artifacts, kobe_home)
    except Exception:  # noqa: BLE001
        logger.exception("cleanup inicial falhou")

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(cleanup_local_artifacts, kobe_home)
        except asyncio.CancelledError:
            logger.info("cleanup loop cancelado — shutdown")
            return
        except Exception:  # noqa: BLE001
            logger.exception("cleanup periódico falhou — seguindo loop")
