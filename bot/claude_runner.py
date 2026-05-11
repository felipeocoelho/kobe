"""Wrapper async do Claude Code CLI (`claude -p`).

O bot Python é uma camada de transporte: monta um prompt com contexto
(histórico da sessão + mensagem nova) e dispara `claude -p` no diretório
do Kobe. O Claude Code, lá dentro, faz auto-discovery do `CLAUDE.md` da
raiz — que carrega SOUL/USER/PREFERENCES — e responde com a personalidade
do agente.

O prompt é enviado via stdin (não via argumento) pra evitar limites de
tamanho de linha de comando. `--permission-mode bypassPermissions` é
necessário porque em modo `-p` (não-interativo) qualquer prompt de
permissão trava o processo até timeout.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


logger = logging.getLogger("kobe.claude")


class ClaudeError(Exception):
    """Falha ao invocar ou obter resposta do Claude Code."""


@dataclass(frozen=True)
class ClaudeRunner:
    cwd: Path
    timeout_seconds: int
    binary: str = "claude"

    async def run(self, prompt: str) -> str:
        """Manda `prompt` via stdin pro Claude Code e retorna a resposta crua."""
        cmd = [
            self.binary,
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "text",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise ClaudeError(
                f"CLI {self.binary!r} não encontrado no PATH."
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(prompt.encode("utf-8")),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            # `kill` aqui é seguro: o processo é nosso e não há side effects
            # externos por uma execução incompleta do Claude.
            proc.kill()
            await proc.wait()
            raise ClaudeError(
                f"Claude não respondeu em {self.timeout_seconds}s."
            ) from exc

        if proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            logger.warning(
                "claude exit=%s stderr=%s",
                proc.returncode,
                stderr[:500],
            )
            raise ClaudeError(stderr or f"claude exit code {proc.returncode}")

        return stdout_bytes.decode("utf-8", errors="replace").strip()


def build_prompt(
    *,
    thread_id: Optional[int],
    history: Iterable[dict],
    new_message: str,
) -> str:
    """Monta o prompt que vai pro `claude -p`.

    Mantemos minimal: identidade e regras vivem no `CLAUDE.md` (que o
    Claude Code lê via auto-discovery no `cwd`). Aqui só damos o contexto
    dinâmico — qual tópico, o histórico recente e a mensagem nova.
    """
    topic_label = (
        f"telegram_thread_id={thread_id}" if thread_id is not None else "geral"
    )
    parts: list[str] = [f"[Telegram] tópico: {topic_label}"]

    history_lines: list[str] = []
    for msg in history:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        history_lines.append(f"{role}: {content}")
    if history_lines:
        parts.append("")
        parts.append("[Histórico recente da sessão]")
        parts.extend(history_lines)

    parts.append("")
    parts.append("[Mensagem nova do operador]")
    parts.append(new_message)
    parts.append("")
    parts.append("Responda agora, em português, no estilo do Kobe.")
    return "\n".join(parts)
