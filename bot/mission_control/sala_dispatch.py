"""Dispatcher da SALA ESTRATEGISTA do Mission Control (forma b).

Abre/retoma uma sala estrategista: cria a pasta da missão + `sala.json` +
`workspace/`, e lança o `sala_worker.py` detached. Reusa o núcleo de core
`bot.sala` pra limites/faxina. Roda atrás da feature flag
`MISSION_CONTROL_SALA_ENABLED` (default off — rollback trivial: flag off).

NÃO cria `estado.json` (Missao) nesta fase: isso ligaria a triagem headless
antiga pra qualquer msg no tópico. O roteamento das mensagens do tópico pra sala
(resume) é o commit 5; aqui a sala é localizável por `sala.json`
(`find_sala_ativa`).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from bot.sala import cleanup as sala_cleanup
from bot.sala import state as sala_state
from bot.sala import tmux as sala_tmux
from bot.mission_control import sala_prompt, storage


logger = logging.getLogger("kobe.mission_control.sala_dispatch")


# Limite (decisão 5 + ressalva do operador). Sala parada custa ~$0 em tokens, e
# a sala SÓ fecha por ato explícito do operador — então NÃO há faxina por idade.
# O guard é só o teto de salas ATIVAS (turnos rodando) + a faxina reaproveitando
# processos tmux de salas JÁ encerradas/mortas (ttl_hours=None em should_kill).
_DEFAULT_MAX_SALAS = 2


# --- flag + tuning ------------------------------------------------------

def sala_enabled() -> bool:
    raw = os.environ.get("MISSION_CONTROL_SALA_ENABLED", "").strip().lower()
    return raw in ("1", "true", "on", "yes")


def _max_salas() -> int:
    raw = os.environ.get("MISSION_CONTROL_MAX_SALAS", "").strip()
    if not raw:
        return _DEFAULT_MAX_SALAS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_SALAS


# --- localização de salas ----------------------------------------------

def list_sala_jsons(kobe_home: Path) -> list[Path]:
    """Todos os `sala.json` (uma por missão com sala)."""
    root = storage.missoes_root(kobe_home)
    if not root.is_dir():
        return []
    return sorted(root.glob("*/sala.json"))


_ACTIVE_STATUSES = ("starting", "running", "idle")


def find_sala_ativa(kobe_home: Path, chat_id: int,
                    thread_id: Optional[int]) -> Optional[dict]:
    """Primeira sala NÃO-terminal deste tópico (chat+thread), ou None. Usado pelo
    roteamento (commit 5) pra decidir resume vs nova."""
    for sp in list_sala_jsons(kobe_home):
        try:
            st = sala_state.read_state(sp)
        except Exception:  # noqa: BLE001
            continue
        if st.get("chat_id") != chat_id:
            continue
        if st.get("thread_id") != thread_id:
            continue
        if st.get("status") in _ACTIVE_STATUSES:
            return st
    return None


def render_sala_ativa(kobe_home: Path, chat_id: int,
                      thread_id: Optional[int]) -> Optional[str]:
    """Linha de CIÊNCIA (read-only) pro prompt do Hal quando há sala ativa no
    tópico — espelha o `[Missão ativa: …]` do headless. NÃO é roteamento: é
    contexto pra o Hal reconhecer endereçamento explícito/implícito e decidir
    (com confirmação, se incerto) se repassa pra sala (§10b). None se a flag está
    off ou não há sala ativa aqui."""
    if not sala_enabled():
        return None
    st = find_sala_ativa(kobe_home, chat_id, thread_id)
    if st is None:
        return None
    obj = (st.get("objetivo") or "")[:80]
    return (
        f'[Sala de missão ativa neste tópico: {st.get("missao_id")} — "{obj}". '
        f'Por padrão NÃO repasse pra ela — a conversa é contigo. Só repasse via '
        f'`.venv/bin/python -m bot.mission_control.sala_dispatch retomar '
        f'--missao {st.get("missao_id")} --texto "..."` quando o operador for '
        f'EXPLÍCITO ("manda pra sala/missão"); se você só DESCONFIA que é pra '
        f'sala, PERGUNTE antes de repassar.]'
    )


# --- spawn do worker ----------------------------------------------------

def _spawn_worker(kobe_home: Path, sala_json: Path, mode: str, log_path: Path) -> int:
    venv_python = kobe_home / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.is_file() else "python3"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = log_path.open("a", encoding="utf-8")
    log_fh.write(f"\n# --- sala_worker spawn mode={mode} ---\n")
    log_fh.flush()
    worker_env = os.environ.copy()
    worker_env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(
        [python, "-m", "bot.mission_control.sala_worker",
         "--sala-json", str(sala_json), "--mode", mode],
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,   # detach — não morre ao sair daqui
        cwd=str(kobe_home),       # pra `bot.*` resolver
        env=worker_env,
    )
    return proc.pid


# --- API ----------------------------------------------------------------

def abrir_sala(*, kobe_home: Path, objetivo: str, chat_id: int,
               thread_id: Optional[int], cwd: Optional[Path] = None) -> dict:
    """Abre uma sala estrategista nova. Retorna um dict-resultado (ok/erro)."""
    if not sala_enabled():
        return {"error": "sala_disabled",
                "message": "Mission Control sala desligada (MISSION_CONTROL_SALA_ENABLED)."}

    objetivo = (objetivo or "").strip()
    if not objetivo:
        return {"error": "objetivo_vazio"}

    # Faxina oportunista (SÓ reaproveita tmux de salas já encerradas/mortas —
    # ttl_hours=None: nunca fecha sala viva por idade, ressalva do operador) +
    # teto de salas ativas.
    sala_cleanup.cleanup_stale_salas(
        prefix=sala_prompt.MISSION_SALA_PREFIX, ttl_hours=None,
        state_files=list_sala_jsons(kobe_home))
    max_salas = _max_salas()
    if max_salas > 0:
        n, ativas = sala_cleanup.count_active(list_sala_jsons(kobe_home))
        if n >= max_salas:
            return {
                "error": "limite_salas",
                "message": (f"limite de {max_salas} sala(s) ativa(s) atingido. "
                            f"Encerre uma ou ajuste MISSION_CONTROL_MAX_SALAS."),
                "active_count": n,
            }

    import uuid
    session_id = str(uuid.uuid4())
    short = session_id[:8]
    missao_id = storage.gerar_id(kobe_home, objetivo)
    slug = missao_id.split("-", 3)[-1] if missao_id.count("-") >= 3 else ""
    sala = sala_prompt.sala_name(short, slug)
    run_cwd = Path(cwd).expanduser().resolve() if cwd else kobe_home

    storage.missao_dir(kobe_home, missao_id).mkdir(parents=True, exist_ok=True)
    storage.ensure_workspace(kobe_home, missao_id)

    sala_json = storage.path_sala_json(kobe_home, missao_id)
    log_path = storage.path_sala_log(kobe_home, missao_id)
    st = {
        "missao_id": missao_id,
        "session_id": session_id,
        "short_id": short,
        "slug": slug,
        "sala_name": sala,
        "cwd": str(run_cwd),
        "objetivo": objetivo,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "status": "starting",
        "pid": None,
        "worker_pid": None,
        "turn_count": 0,
        "last_text": None,
        "pending_input": None,
        "created_at": sala_state.now_iso(),
        "last_activity": sala_state.now_iso(),
        "log_path": str(log_path),
    }
    sala_state.write_state(sala_json, st)

    worker_pid = _spawn_worker(kobe_home, sala_json, "start", log_path)
    sala_state.patch_state(sala_json, worker_pid=worker_pid)

    logger.info("sala aberta missao=%s sala=%s worker_pid=%s", missao_id, sala, worker_pid)
    return {"ok": True, "missao_id": missao_id, "session_id": session_id,
            "short_id": short, "sala_name": sala, "worker_pid": worker_pid}


def retomar_sala(*, kobe_home: Path, missao_id: str, texto: str) -> dict:
    """Retoma uma sala viva: grava o input pendente e lança o worker em resume.

    É o caminho do REPASSE do Hal pro sala (decisão de roteamento §10b): o Hal só
    chama isto quando o operador é explícito OU confirmou. A sala (estrategista)
    também recebe input direto na sessão tmux — os dois canais são equivalentes."""
    sala_json = storage.path_sala_json(kobe_home, missao_id)
    if not sala_json.is_file():
        return {"error": "sala_inexistente", "missao_id": missao_id}
    sala_state.patch_state(sala_json, pending_input=(texto or "").strip())
    log_path = storage.path_sala_log(kobe_home, missao_id)
    worker_pid = _spawn_worker(kobe_home, sala_json, "resume", log_path)
    sala_state.patch_state(sala_json, worker_pid=worker_pid)
    return {"ok": True, "missao_id": missao_id, "worker_pid": worker_pid}


def encerrar_sala(*, kobe_home: Path, missao_id: str) -> dict:
    """Encerra a sala — ato EXPLÍCITO do operador (ressalva). Marca `sala.json`
    `encerrada` e mata o tmux. Idempotente. Chamável pelos DOIS canais:
    - via Hal/Telegram: o Hal roda este comando;
    - direto na sala: o estrategista pode rodar isto (ou só marcar encerrada e o
      monitor sai quieto — o status terminal evita o aviso de morte).
    A faxina nunca chama isto: a sala só fecha quando o operador manda."""
    sala_json = storage.path_sala_json(kobe_home, missao_id)
    if not sala_json.is_file():
        return {"error": "sala_inexistente", "missao_id": missao_id}
    st = sala_state.read_state(sala_json)
    sala = st.get("sala_name") or sala_prompt.sala_name(st.get("short_id", ""), st.get("slug", ""))
    sala_state.patch_state(sala_json, status="encerrada")
    killed = sala_tmux.kill_session(sala).returncode == 0
    return {"ok": True, "missao_id": missao_id, "sala_name": sala, "tmux_morta": killed}


# --- CLI (debug/manual) -------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse, json
    # chat/thread default do env (KOBE_CHAT_ID/KOBE_THREAD_ID) — o Hal não precisa
    # passar: as envs já estão no ambiente dele.
    def _env_int(name: str):
        raw = (os.environ.get(name) or "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None

    p = argparse.ArgumentParser(description="Dispatcher da sala estrategista (Mission Control)")
    sub = p.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("abrir")
    pa.add_argument("--objetivo", required=True)
    pa.add_argument("--chat-id", type=int, default=_env_int("KOBE_CHAT_ID"))
    pa.add_argument("--thread-id", type=int, default=_env_int("KOBE_THREAD_ID"))
    pr = sub.add_parser("retomar")
    pr.add_argument("--missao", required=True)
    pr.add_argument("--texto", required=True)
    pe = sub.add_parser("encerrar")
    pe.add_argument("--missao", required=True)
    args = p.parse_args(argv)

    kobe_home = Path(os.environ.get("KOBE_HOME", "")).expanduser().resolve()
    if not str(kobe_home):
        print("KOBE_HOME ausente", file=sys.stderr)
        return 2
    if args.cmd == "abrir":
        if args.chat_id is None:
            print(json.dumps({"error": "chat_id ausente (passe --chat-id ou KOBE_CHAT_ID)"}))
            return 1
        res = abrir_sala(kobe_home=kobe_home, objetivo=args.objetivo,
                         chat_id=args.chat_id, thread_id=args.thread_id)
    elif args.cmd == "retomar":
        res = retomar_sala(kobe_home=kobe_home, missao_id=args.missao, texto=args.texto)
    else:
        res = encerrar_sala(kobe_home=kobe_home, missao_id=args.missao)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
