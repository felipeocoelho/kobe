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

Saída em `stream-json` (linha por linha) — permite ao bot capturar
eventos de uso de ferramenta enquanto o Claude trabalha e devolver
"sinais de vida" pro operador no Telegram (typing + progresso textual)
em vez de só silêncio até a resposta final.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional
from zoneinfo import ZoneInfo


# Fuso do operador. Forçado explicitamente porque a VPS roda em UTC e
# "hoje/amanhã" do Felipe ancora no Brasil — caso contrário datas viram
# passado/futuro espelhado (bug observado: jogo "amanhã 12/05" lido como
# ontem porque o servidor já estava em 13/05 UTC).
OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")


logger = logging.getLogger("kobe.claude")


class ClaudeError(Exception):
    """Falha ao invocar ou obter resposta do Claude Code."""


class ClaudeTimeoutError(ClaudeError):
    """Claude não respondeu dentro de `timeout_seconds`."""


class ClaudeNotFoundError(ClaudeError):
    """O binário do Claude Code não está no PATH do serviço."""


class ClaudeExitError(ClaudeError):
    """Claude terminou com exit code != 0 (problema no próprio CLI)."""

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(stderr or f"claude exit code {returncode}")


# Callback recebe um dict com o evento parseado do stream-json. Pode ser
# síncrono ou assíncrono — o runner aguarda se for awaitable.
EventCallback = Callable[[dict], "Awaitable[None] | None"]


@dataclass(frozen=True)
class ClaudeRunner:
    cwd: Path
    timeout_seconds: int
    binary: str = "claude"

    async def run(
        self,
        prompt: str,
        *,
        on_event: Optional[EventCallback] = None,
    ) -> str:
        """Manda `prompt` via stdin pro Claude Code e retorna a resposta.

        Se `on_event` for fornecido, é chamado pra cada evento JSON do
        stream (system/assistant/user/result/etc.) — útil pra mostrar
        progresso ao usuário enquanto o Claude trabalha.

        O texto final retornado vem do evento `result` (campo `result`).
        Se por algum motivo não chegar um `result`, montamos a resposta
        concatenando os blocos de texto de eventos `assistant`.
        """
        cmd = [
            self.binary,
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--verbose",
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
            raise ClaudeNotFoundError(
                f"CLI {self.binary!r} não encontrado no PATH."
            ) from exc

        # Envia o prompt e fecha stdin pra Claude saber que terminou.
        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        result_text: Optional[str] = None
        assistant_texts: list[str] = []

        async def _consume_stdout() -> None:
            nonlocal result_text
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("linha não-JSON ignorada: %s", line[:200])
                    continue

                # Capta resultado final / fallbacks de texto.
                etype = event.get("type")
                if etype == "result":
                    result_text = (event.get("result") or "").strip() or None
                elif etype == "assistant":
                    msg = event.get("message") or {}
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "text":
                            txt = block.get("text") or ""
                            if txt:
                                assistant_texts.append(txt)

                if on_event is not None:
                    try:
                        maybe = on_event(event)
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    except Exception:  # noqa: BLE001 — callback não deve derrubar
                        logger.exception("on_event raised; seguindo")

        async def _consume_stderr() -> bytes:
            assert proc.stderr is not None
            return await proc.stderr.read()

        try:
            stderr_task = asyncio.create_task(_consume_stderr())
            await asyncio.wait_for(_consume_stdout(), timeout=self.timeout_seconds)
            stderr_bytes = await stderr_task
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ClaudeTimeoutError(
                f"Claude não respondeu em {self.timeout_seconds}s."
            ) from exc

        if proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            # Loga o stderr inteiro (sem truncar) — em diagnóstico de
            # erro do CLI a parte útil costuma vir no fim do output.
            logger.warning(
                "claude exit=%s stderr=%s",
                proc.returncode,
                stderr or "(vazio)",
            )
            raise ClaudeExitError(proc.returncode, stderr)

        if result_text:
            return result_text
        # Fallback: alguns paths não emitem `result` (errado/raro), mas
        # vimos blocos `text` em eventos `assistant`. Junta tudo.
        joined = "\n".join(t.strip() for t in assistant_texts if t.strip())
        return joined.strip()


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
    now_br = datetime.now(OPERATOR_TZ)
    parts: list[str] = [
        f"[Telegram] tópico: {topic_label}",
        f"[Agora (America/Sao_Paulo)] {now_br.isoformat(timespec='minutes')}",
    ]

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
