"""Wrappers finos do `tmux` + helpers PUROS de leitura de pane.

Os wrappers fazem subprocess (não-testáveis sem tmux real); os helpers puros
(`pane_busy`, `extract_pane_last`) operam sobre o texto capturado e são a parte
testável da lógica de sala. Mantidos juntos porque andam juntos.
"""

from __future__ import annotations

import subprocess
from typing import Optional


# --- wrappers (IO) ------------------------------------------------------

def run(*args: str) -> subprocess.CompletedProcess:
    """`tmux <args>` capturando stdout/stderr como texto."""
    return subprocess.run(["tmux", *args], capture_output=True, text=True)


def has_session(name: str) -> bool:
    return run("has-session", "-t", name).returncode == 0


def capture_pane(name: str) -> str:
    """Conteúdo visível do pane da sala (stdout do `capture-pane -p`).
    Vazio se a sala não existe / erro — o caller decide o que isso significa."""
    return run("capture-pane", "-t", name, "-p").stdout


def kill_session(name: str) -> subprocess.CompletedProcess:
    return run("kill-session", "-t", name)


def list_sessions() -> list[str]:
    """Nomes de todas as sessões tmux. Lista vazia se tmux não responde."""
    r = run("list-sessions", "-F", "#{session_name}")
    if r.returncode != 0:
        return []
    return [s for s in r.stdout.split() if s.strip()]


def new_session(name: str, cwd: str, command: str, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """Abre uma sala tmux detached (`-d`) na `cwd`, rodando `command`. `env` é
    injetado via `-e KEY=VAL` (a sala não herda o env do worker de outra forma —
    é como a sala recebe KOBE_CHAT_ID/THREAD_ID/TOKEN pra kobe-notify/attach)."""
    cmd = ["tmux", "new-session", "-d", "-s", name, "-c", str(cwd)]
    for k, v in (env or {}).items():
        if v:
            cmd += ["-e", f"{k}={v}"]
    cmd += [command]
    return subprocess.run(cmd, capture_output=True, text=True)


def send_keys_literal(name: str, text: str) -> None:
    """Digita `text` literalmente na sala (sem interpretar como atalho) e Enter."""
    run("send-keys", "-t", name, "-l", text)
    run("send-keys", "-t", name, "Enter")


def claude_pid_for(sala: str) -> Optional[int]:
    """PID do `claude` que serve a sala (pra presença/status). Casa pela linha
    de comando `remote-control <sala>`."""
    r = subprocess.run(
        ["pgrep", "-f", f"remote-control {sala}"], capture_output=True, text=True
    )
    pids = [int(p) for p in r.stdout.split() if p.strip().isdigit()]
    return pids[0] if pids else None


# --- helpers PUROS (testáveis) ------------------------------------------

def pane_busy(pane: str) -> bool:
    """A TUI do claude mostra "esc to interrupt" na status bar enquanto processa
    um turno; ociosa (esperando input) NÃO mostra. Sinal robusto observado nos
    testes do Coder. Pane vazio → não-busy (a sala pode ter caído; quem chama
    distingue via has_session)."""
    return "esc to interrupt" in pane


def extract_pane_last(pane: str) -> Optional[str]:
    """Best-effort: a última fala do claude no pane (linhas com "●"). A TUI é
    ruidosa — isto é só um preview grosso pro status; a fonte real é a própria
    sala + os kobe-notify dela."""
    bullets = [l.strip() for l in pane.splitlines() if l.lstrip().startswith("●")]
    return (bullets[-1].lstrip("● ").strip() or None) if bullets else None
