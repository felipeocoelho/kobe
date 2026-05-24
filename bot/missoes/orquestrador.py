"""Orquestrador — Claude rodando em background, hibernando entre eventos.

Não é um processo vivo. É invocação de `claude -p` em subprocess detached,
com prompt específico montado a partir do estado atual + motivo.

API principal:

    acordar_orquestrador(
        kobe_home, missao_id, motivo,
        bot_token, chat_id, thread_id,
        mensagem_operador=None,  # só pra motivo=triar-mensagem
    ) -> subprocess.Popen

A função volta IMEDIATO com o Popen — chamador não espera nada.

Chamado por:
- `bot/missoes/handlers.py` (slash /missao → motivo=planejar)
- `bot/keyko/...` MissoesSource (marco → motivo=reagir-marco)
- `bot/telegram_handler.py` (msg em tópico com missão ativa → triar-mensagem)

Stdout do claude -p:
- Em modo `triar-mensagem`, o orquestrador pode imprimir
  `KOBE_TRIAGE_RESULT: not_related` (vide prompts.TRIAGE_NOT_RELATED_MARKER).
  Pros outros motivos, stdout é log informativo só.
- Tudo redirecionado pra `<missao_dir>/orquestrador.log` (append, rotação
  manual se virar grande — fora do escopo da Fase 1).

triagem síncrona vs assíncrona:
- Pra `triar-mensagem`, o telegram_handler PRECISA esperar o resultado
  (synchronous) pra decidir se rotear pro Hal. Por isso há também
  `triar_mensagem_sincrono(...)` que espera e retorna ("related"/"not_related").
- Pros outros motivos, é fire-and-forget (Popen.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from bot.missoes import storage
from bot.missoes.prompts import PROMPTS, TRIAGE_NOT_RELATED_MARKER


logger = logging.getLogger("kobe.missoes.orquestrador")

# Quanto esperar pelo `claude -p` no modo síncrono (triar-mensagem).
# Triagem é decisão rápida (uma pergunta sim/não), 60s é folga.
TIMEOUT_TRIAGEM_S = 90

# Timeout absoluto da invocação background — só pra evitar zumbi se
# claude pendurar. Detached, não bloqueia ninguém esperando.
TIMEOUT_ORQUESTRADOR_S = 600


def _build_prompt(
    *,
    missao_id: str,
    kobe_home: Path,
    motivo: str,
    mensagem_operador: Optional[str] = None,
) -> str:
    """Monta o prompt a partir do template + estado atual."""
    template = PROMPTS.get(motivo)
    if template is None:
        raise ValueError(f"motivo desconhecido: {motivo!r}")
    missao = storage.carregar(kobe_home, missao_id)
    fmt_kwargs = {
        "missao_id": missao_id,
        "kobe_home": str(kobe_home),
        "motivo": motivo,
        "objetivo": missao.objetivo,
        "estado_json": missao.to_json(indent=2),
        "mensagem_operador": mensagem_operador or "(sem mensagem)",
    }
    return template.format(**fmt_kwargs)


def _build_env(
    *,
    kobe_home: Path,
    bot_token: str,
    chat_id: int,
    thread_id: Optional[int],
) -> dict[str, str]:
    """Env pro subprocess — herda do parent + injeta KOBE_* + KOBE_HOME.

    Importante: quando `thread_id=None`, REMOVEMOS `KOBE_THREAD_ID` do env
    (não só "não setar"). O env do parent pode já ter essa var (ex.: bot
    rodando em sessão Coder), e queremos que o subprocess use SEMPRE o
    contexto desta invocação — não o do parent.
    """
    env = dict(os.environ)
    env["KOBE_HOME"] = str(kobe_home)
    env["KOBE_TELEGRAM_BOT_TOKEN"] = bot_token
    env["KOBE_CHAT_ID"] = str(chat_id)
    if thread_id is not None:
        env["KOBE_THREAD_ID"] = str(thread_id)
    else:
        env.pop("KOBE_THREAD_ID", None)
    return env


def acordar_orquestrador(
    *,
    kobe_home: Path,
    missao_id: str,
    motivo: str,
    bot_token: str,
    chat_id: int,
    thread_id: Optional[int],
    mensagem_operador: Optional[str] = None,
) -> subprocess.Popen:
    """Dispara `claude -p` em background. Volta imediato.

    Não use pra `triar-mensagem` se precisa do resultado — use
    `triar_mensagem_sincrono`.
    """
    prompt = _build_prompt(
        missao_id=missao_id,
        kobe_home=kobe_home,
        motivo=motivo,
        mensagem_operador=mensagem_operador,
    )
    env = _build_env(
        kobe_home=kobe_home, bot_token=bot_token,
        chat_id=chat_id, thread_id=thread_id,
    )
    log_path = storage.path_log_orquestrador(kobe_home, missao_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "claude", "-p",
        "--permission-mode", "bypassPermissions",
    ]
    # stdout vai pro log de forma append (sobreviva a múltiplas invocações).
    log_fh = log_path.open("a", encoding="utf-8")
    log_fh.write(f"\n=== orquestrador motivo={motivo} missao={missao_id} ===\n")
    log_fh.flush()

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(kobe_home),
        env=env,
        start_new_session=True,  # sobrevive ao parent
        close_fds=True,
    )
    # Envia prompt pelo stdin, fecha. log_fh fica aberto até proc terminar
    # — Popen não fecha; quem fecha é o Python quando proc é coletado.
    assert proc.stdin is not None
    try:
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        logger.exception("falha escrevendo prompt no stdin do claude -p")

    logger.info(
        "orquestrador disparado missao=%s motivo=%s pid=%s",
        missao_id, motivo, proc.pid,
    )
    return proc


def triar_mensagem_sincrono(
    *,
    kobe_home: Path,
    missao_id: str,
    mensagem_operador: str,
    bot_token: str,
    chat_id: int,
    thread_id: Optional[int],
    timeout_s: int = TIMEOUT_TRIAGEM_S,
) -> str:
    """Triagem síncrona — espera resposta do claude e retorna decisão.

    Retorna:
    - `"not_related"` se o orquestrador imprimiu o marker
      `KOBE_TRIAGE_RESULT: not_related` → telegram_handler deve rotear
      pro Hal.
    - `"related"` caso contrário → orquestrador já respondeu via
      kobe-notify, telegram_handler NÃO chama o Hal.

    Em caso de erro/timeout, retorna `"related"` por segurança
    (fail-safe: melhor deixar o orquestrador "ter respondido" e o operador
    cobrar de novo do que vazar uma msg sobre a missão pro Hal sem
    contexto).
    """
    prompt = _build_prompt(
        missao_id=missao_id,
        kobe_home=kobe_home,
        motivo="triar-mensagem",
        mensagem_operador=mensagem_operador,
    )
    env = _build_env(
        kobe_home=kobe_home, bot_token=bot_token,
        chat_id=chat_id, thread_id=thread_id,
    )
    log_path = storage.path_log_orquestrador(kobe_home, missao_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "claude", "-p",
        "--permission-mode", "bypassPermissions",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
            cwd=str(kobe_home),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "triagem timeout missao=%s — fail-safe pra 'related'", missao_id,
        )
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== triagem TIMEOUT missao={missao_id} ===\n")
        return "related"
    except FileNotFoundError:
        logger.error("claude CLI não encontrado — fail-safe pra 'related'")
        return "related"

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")

    # Log da triagem (sucinto — só último bloco pra não inflar arquivo).
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== triagem missao={missao_id} exit={proc.returncode} ===\n")
            fh.write(f"mensagem: {mensagem_operador!r}\n")
            fh.write(f"stdout (últimos 1500 chars):\n{stdout[-1500:]}\n")
            if stderr.strip():
                fh.write(f"stderr (últimos 500 chars):\n{stderr[-500:]}\n")
    except OSError:
        pass

    if TRIAGE_NOT_RELATED_MARKER in stdout:
        logger.info("triagem missao=%s → not_related (rotear pro Hal)", missao_id)
        return "not_related"
    logger.info("triagem missao=%s → related (orquestrador respondeu)", missao_id)
    return "related"
