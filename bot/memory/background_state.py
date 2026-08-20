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

from bot.memory.aging import humanizar_idade

logger = logging.getLogger("kobe.background_state")

# Só mostra trabalho cuja última atividade é recente — um job de dias atrás não é
# "estado vivo", é arqueologia. Janela generosa pra cobrir trabalho longo legítimo.
RECENT_WINDOW_SECONDS = 6 * 3600
MAX_JOBS = 6

# Teto SEPARADO pras salas de missão. Elas não são filtradas por idade (só
# fecham por ato do operador, então uma sala de 26 dias é fato vivo) — mas sem
# um teto próprio um histórico de salas antigas ocuparia o bloco inteiro e
# expulsaria uma sessão do Coder RODANDO agora, que é informação mais urgente.
# Observado com dado real de produção: 6 salas idle de julho enchiam o bloco.
MAX_SALAS = 2


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _humanize_age(seconds: float) -> str:
    """Idade em linguagem humana. Delega pro formato ÚNICO do `aging` pra o
    prompt falar a mesma língua em todo lugar (histórico, sala, background) —
    antes cada bloco tinha o seu ("~13 dia(s)" aqui, "há ~13 dias" lá)."""
    return humanizar_idade(seconds)


def _read_salas(kobe_home: Path, chat_id: Optional[int],
                thread_id: Optional[int], now: datetime) -> list[dict]:
    """Salas de missão do tópico (Mission Control).

    CORREÇÃO 2026-08-20: este bloco era o antídoto declarado contra "narrar
    status de background de memória" — e não olhava salas de missão. Uma sala
    parada havia 12 dias nunca aparecia aqui; o único sinal que o agente tinha
    era a linha `[Sala de missão ativa…]`, que dizia "ativa" no presente. Foi
    exatamente esse buraco que produziu o "segue rodando, te reporto".

    Salas NÃO são escondidas pela janela de recência: elas só fecham por ato
    explícito do operador, então uma sala de 12 dias é fato VIVO, não
    arqueologia — o que ela precisa é do carimbo de idade, não do sumiço.
    """
    if chat_id is None:
        return []
    try:
        from bot.mission_control import sala_dispatch
        from bot.sala import state as sala_state
    except Exception:  # noqa: BLE001 — Mission Control ausente/quebrado: sem bloco
        return []

    jobs: list[dict] = []
    for sp in sala_dispatch.list_sala_jsons(kobe_home):
        try:
            st = sala_state.read_state(sp)
        except Exception:  # noqa: BLE001 — arquivo parcial/corrompido: ignora
            continue
        if st.get("chat_id") != chat_id or st.get("thread_id") != thread_id:
            continue
        status = str(st.get("status") or "?")
        if status not in sala_dispatch._ACTIVE_STATUSES:
            continue  # encerrada/morta: não é estado vivo
        last = _parse_iso(str(st.get("last_activity") or ""))
        if last is None:
            try:
                last = datetime.fromtimestamp(sp.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
        jobs.append({
            # ID inteiro: o do Coder é um UUID (8 chars bastam pra distinguir),
            # mas o da sala é um slug com data — truncar em 12 fazia três salas
            # de julho virarem "2026-07-09-d" e o bloco ficar inútil.
            "kind": "Sala de missão",
            "id": str(st.get("missao_id") or sp.parent.name),
            "state": status,
            "exit_code": None,
            "pid": None,
            "age": (now - last).total_seconds(),
            "is_sala": True,
        })
    jobs.sort(key=lambda j: j["age"])
    return jobs[:MAX_SALAS]


def _read_dispatched_jobs(kobe_home: Path, now: datetime) -> list[dict]:
    """Jobs despachados em background (convenção `kobe-dispatch`).

    Não são por tópico no disco, então entram só quando recentes — aí a janela
    de recência é o filtro certo (job de dias atrás é arqueologia mesmo).
    """
    d = kobe_home / "user-data" / "dispatched"
    if not d.is_dir():
        return []
    jobs: list[dict] = []
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
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
            "kind": "Job despachado",
            "id": f.stem[:8],
            "state": str(data.get("state") or data.get("status") or "?"),
            "exit_code": data.get("exit_code"),
            "pid": data.get("pid"),
            "age": age,
        })
    return jobs


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
    kobe_home: Path,
    thread_id: Optional[int],
    *,
    chat_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Bloco `[Estado de background vivo]` com o estado LIDO AGORA dos trabalhos de
    background do tópico. None se não há trabalho vivo (ou em erro de I/O).

    `chat_id` é opcional só por compatibilidade com chamadores antigos — sem ele
    as salas de missão não entram (elas são localizadas por chat+thread).
    """
    now = now or datetime.now(timezone.utc)
    jobs: list[dict] = []
    # Cada fonte é independente: uma que falhe não pode apagar as outras — o
    # bloco existe justamente pra o agente não ficar sem o fato vivo.
    for leitura in (
        lambda: _read_coder_jobs(kobe_home, thread_id, now),
        lambda: _read_salas(kobe_home, chat_id, thread_id, now),
        lambda: _read_dispatched_jobs(kobe_home, now),
    ):
        try:
            jobs.extend(leitura())
        except Exception as exc:  # noqa: BLE001 — best-effort, nunca derruba o turno
            logger.warning("background_state: uma leitura falhou: %s", exc)
    if not jobs:
        return None
    # Salas já vêm limitadas por MAX_SALAS; o resto (Coder, jobs) preenche o
    # que sobra do teto, mais recente primeiro. Assim uma sessão do Coder
    # rodando AGORA nunca é expulsa por salas antigas, e uma sala idle nunca
    # some do bloco (que é o buraco que produziu o "segue rodando").
    salas = [j for j in jobs if j.get("is_sala")]
    resto = sorted((j for j in jobs if not j.get("is_sala")), key=lambda j: j["age"])
    jobs = sorted(salas + resto[: max(0, MAX_JOBS - len(salas))], key=lambda j: j["age"])
    lines = [
        "[Estado de background vivo — LIDO AGORA, neste turno, dos arquivos de "
        "estado. Se for falar do status de algum trabalho em background, use "
        "EXATAMENTE estes dados, nunca a memória da conversa. Um trabalho que você "
        "lembrava e NÃO está aqui provavelmente terminou — não afirme que segue "
        "rodando/esperando sem reler. `state=idle` significa PARADO esperando, "
        "NÃO trabalhando: não prometa retorno de algo idle.]"
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
            f"última atividade {_humanize_age(j['age'])}{tail}."
        )
    return "\n".join(lines)
