"""Destilador — pega histórico de mensagens e produz handoff doc.

Chama `claude -p` com o prompt do `bot.handoff.prompts` e devolve o
texto Markdown. Caller escolhe onde gravar (ativo ou arquivado).

Custo/latência (estimativa): $0,01-0,05, 5-30s. Por isso o caller
sempre roda em background — handler responde no Telegram em <1s
("destilando...") e `kobe-notify` confirma quando termina.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from bot.claude_runner import (
    ClaudeError,
    ClaudeRunner,
)
from bot.handoff.prompts import build_destilador_prompt


logger = logging.getLogger("kobe.handoff.destilador")


# Mínimo de mensagens pra valer destilação. Abaixo disso, caller
# decide o que fazer (`/handoff` avisa, `/nova` skipa silencioso).
MIN_MESSAGES_FOR_HANDOFF = 5


class DestiladorError(Exception):
    """Falha ao destilar (Claude erro, timeout, resposta vazia)."""


@dataclass(frozen=True)
class HandoffResult:
    markdown: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _format_transcript(messages: list[dict]) -> str:
    """`role: content` por linha — mesma convenção do `build_prompt`."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def destilar_sessao(
    *,
    messages: list[dict],
    runner: ClaudeRunner,
) -> HandoffResult:
    """Recebe lista de mensagens (`role`+`content`) e devolve Markdown.

    Não escreve em disco — caller decide path. Levanta `DestiladorError`
    em falha (timeout, exit não-zero, resposta vazia após retry).

    Não passa `chat_id`/`thread_id`/`bot_token` pro subprocess: o
    destilador NÃO deve chamar `kobe-notify` por conta própria. Quem
    notifica o operador é o handler que invocou esta função.
    """
    if len(messages) < MIN_MESSAGES_FOR_HANDOFF:
        raise DestiladorError(
            f"sessão tem só {len(messages)} mensagens; mínimo é "
            f"{MIN_MESSAGES_FOR_HANDOFF}"
        )

    transcript = _format_transcript(messages)
    if not transcript.strip():
        raise DestiladorError("transcript vazio após filtrar mensagens")

    prompt = build_destilador_prompt(transcript)

    # Retry simples: tentativa única + 1 retry em falha. Não é loop
    # genérico porque destilador é caro e timeout costuma indicar erro
    # estrutural, não flake transitória.
    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            result = await runner.run(prompt)
        except ClaudeError as exc:
            last_err = exc
            logger.warning(
                "destilador tentativa %d falhou: %s", attempt, exc
            )
            continue
        text = (result.text or "").strip()
        if not text:
            last_err = DestiladorError("destilador devolveu resposta vazia")
            logger.warning("destilador tentativa %d vazia", attempt)
            continue
        logger.info(
            "destilador ok tokens_in=%d tokens_out=%d cost=$%.4f",
            result.input_tokens,
            result.output_tokens,
            result.cost_usd,
        )
        return HandoffResult(
            markdown=text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
        )

    raise DestiladorError(
        f"destilador falhou após 2 tentativas: {last_err}"
    ) from last_err
