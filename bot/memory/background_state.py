"""Gate de estado de background vivo (Highlander v2, P1).

A dor (Auditoria da Verdade, caso da sala "esperando" que já tinha acabado): o
agente afirma o STATUS de um trabalho em background — sala/sessão do Coder, job do
Atrus — **de memória da conversa**, sem ler o estado vivo. O contrato já manda
"abra o arquivo primeiro, afirme depois", mas é instrução MOLE: depende do agente
lembrar. O conserto que serve pro usuário 2 (palavra do operador, 2026-06-22) é o
CÓDIGO empurrar o estado vivo pro prompt — igual o bot já injeta
`[Alertas aguardando confirmação]`.

Este módulo lê, NESTE turno, os arquivos de estado dos trabalhos de background do
TÓPICO atual, carimba a idade (mtime/last_activity), e monta um bloco com o fato
vivo + a regra dura "use ISTO, não memória". Se não há trabalho recente, devolve
None (e então o agente não tem o que afirmar).

Read-only, best-effort: qualquer erro de I/O → None, nunca derruba o turno.

Escopo: a camada simples do P1. A leitura de TELA de sala tmux (capture-pane), com
sanitização de ANSI e proteção contra input-fantasma, é o P7 (bloco residente de
sala) — deliberadamente fora daqui.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kobe.background_state")

# Só mostra trabalho cuja última atividade é recente — um job de dias atrás não é
# "estado vivo", é arqueologia. Janela generosa pra cobrir trabalho longo legítimo.
RECENT_WINDOW_SECONDS = 6 * 3600
MAX_JOBS = 6


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _humanize_age(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "menos de 1 min"
    if minutes < 90:
        return f"~{minutes} min"
    hours = seconds / 3600
    if hours < 36:
        return f"~{round(hours)} h"
    return f"~{round(hours / 24)} dia(s)"


def _coder_sessions_dir(kobe_home: Path, thread_id: Optional[int]) -> Optional[Path]:
    # Coder organiza por thread_id (ver CLAUDE.md). thread_id None (general/private)
    # raramente tem sessão de código — sem dir, sem bloco.
    if thread_id is None:
        return None
    d = kobe_home / "user-data" / "coder-sessions" / str(thread_id)
    return d if d.is_dir() else None


def _read_coder_jobs(kobe_home: Path, thread_id: Optional[int], now: datetime) -> list[dict]:
    d = _coder_sessions_dir(kobe_home, thread_id)
    if d is None:
        return []
    jobs: list[dict] = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except Exception:  # noqa: BLE001 — arquivo parcial/corrompido: ignora
            continue
        if not isinstance(data, dict):
            continue
        # Idade: prefere last_activity do estado; cai no mtime do arquivo.
        last = _parse_iso(str(data.get("last_activity") or ""))
        if last is None:
            try:
                last = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
        age = (now - last).total_seconds()
        if age > RECENT_WINDOW_SECONDS:
            continue
        jobs.append({
            "kind": "Coder",
            "id": f.stem[:8],
            "state": str(data.get("state") or "?"),
            "exit_code": data.get("exit_code"),
            "pid": data.get("pid"),
            "age": age,
        })
    return jobs


def render_background_state(
    kobe_home: Path, thread_id: Optional[int], *, now: Optional[datetime] = None
) -> Optional[str]:
    """Bloco `[Estado de background vivo]` com o estado LIDO AGORA dos trabalhos de
    background do tópico. None se não há trabalho recente (ou em erro de I/O)."""
    now = now or datetime.now(timezone.utc)
    try:
        jobs = _read_coder_jobs(kobe_home, thread_id, now)
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca derruba o turno
        logger.warning("background_state: leitura falhou (best-effort): %s", exc)
        return None
    if not jobs:
        return None
    jobs.sort(key=lambda j: j["age"])
    jobs = jobs[:MAX_JOBS]
    lines = [
        "[Estado de background vivo — LIDO AGORA, neste turno, dos arquivos de "
        "estado. Se for falar do status de algum trabalho em background, use "
        "EXATAMENTE estes dados, nunca a memória da conversa. Um trabalho que você "
        "lembrava e NÃO está aqui provavelmente terminou — não afirme que segue "
        "rodando/esperando sem reler.]"
    ]
    for j in jobs:
        extra = []
        if j.get("pid") is not None:
            extra.append(f"pid {j['pid']}")
        if j.get("exit_code") is not None:
            extra.append(f"exit_code {j['exit_code']}")
        tail = f" ({', '.join(extra)})" if extra else ""
        lines.append(
            f"- {j['kind']} {j['id']}: state={j['state']}, "
            f"última atividade há {_humanize_age(j['age'])}{tail}."
        )
    return "\n".join(lines)
