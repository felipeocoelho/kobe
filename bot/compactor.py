"""Compactação automática de sessões longas (v0.12).

Quando uma session ativa atinge `COMPACT_THRESHOLD_MESSAGES`, este módulo:

1. Lê todas as mensagens da sessão e monta um prompt curto pedindo summary
   ao Claude (chamada extra ao Claude — única chamada nova de custo, mas
   amortizada pela redução drástica do tamanho dos prompts subsequentes).
2. Arquiva a sessão atual com `status='compacted'` e `summary=<texto>`.
3. Cria uma sessão nova via `ensure_active_session`.
4. Insere o summary como `messages` com `role='system'` na sessão nova —
   próximas chamadas a `get_recent_messages` já vão trazer o summary como
   primeira "fala", e `build_prompt` o renderiza no histórico normalmente.

A compactação é disparada DENTRO do `_handle_user_text`, antes de tudo —
quando o operador manda mensagem nova num tópico cuja sessão já está cheia.
A nova mensagem é processada na sessão nova (com o summary como base),
não na velha (que está sendo arquivada).

Falhas na compactação NÃO devem derrubar o fluxo principal — o caller
trata graceful: se compactação falha, segue na sessão antiga (próxima
mensagem tenta de novo).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from supabase import Client

from bot.claude_runner import ClaudeRunner
from bot.topic_manager import (
    archive_active_session,
    ensure_active_session,
    get_recent_messages,
    insert_message,
)


logger = logging.getLogger("kobe.compactor")


SUMMARY_PROMPT_TEMPLATE = """\
Você é um assistente compactando uma conversa longa pra preservar contexto.
Abaixo está o histórico completo de uma sessão entre operador e agente,
em ordem cronológica.

Sua tarefa: gere um resumo em até 800 palavras que permita ao agente
RETOMAR essa conversa numa próxima sessão sem perder o que importa.

Foque em:
- Decisões tomadas (e por quê)
- Fatos novos sobre o operador, projetos, contexto
- Tarefas/promessas em aberto
- Preferências expressas
- Conclusões e próximos passos pendentes

NÃO inclua:
- Saudações, conversa fiada, agradecimentos
- Repetições do que está em `user-data/identity/*`
- Detalhes técnicos que o agente pode reler nos arquivos

Estilo: prosa direta, em português, sem markdown elaborado. Pode usar
bullets simples se ajudar a estruturar. Não inicie com "Resumo:" — apenas
escreva o conteúdo.

═══════════════════════════════════════════════════════════════
HISTÓRICO DA SESSÃO
═══════════════════════════════════════════════════════════════
{transcript}
═══════════════════════════════════════════════════════════════
"""


def _format_transcript(messages: list[dict]) -> str:
    """Renderiza mensagens em texto plano legível pelo Claude."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)


async def compact_session(
    *,
    db: Client,
    claude: ClaudeRunner,
    topic_id: str,
    session_id: str,
    chat_id: int,
    thread_id: Optional[int],
    bot_token: str,
    on_start: Optional[Callable[[], Awaitable[None]]] = None,
) -> Optional[str]:
    """Compacta a sessão `session_id` e devolve o id da nova sessão.

    Retorna `None` se a compactação falhar (caller deve seguir na sessão
    antiga e tentar de novo na próxima mensagem). Logs estruturados
    incluem `claude_run summary=true` pra distinguir das chamadas normais.

    `chat_id`/`thread_id`/`bot_token` são passados ao runner por uniformidade
    com a chamada principal — os helpers de progresso/anexo não são usados
    aqui (não há `on_event`).

    `on_start`: callback opcional disparado UMA vez, assim que a compactação
    de fato começa (já confirmado que há histórico, antes da chamada de
    summary ao Claude). Serve pra avisar o operador em tempo real — o resumo
    leva alguns segundos e ele não pode achar que o agente travou ou que
    perdeu contexto. Best-effort: falha no aviso não derruba a compactação.
    """
    # Pega tudo da sessão atual (sem limite — o objetivo é compactar tudo).
    messages = get_recent_messages(db, session_id, limit=10_000)
    if not messages:
        logger.warning("compact: sessão %s vazia, pulando", session_id)
        return None

    # Aviso de "estou compactando" sai AGORA — antes do summary (que custa
    # alguns segundos de Claude), pra o operador ver em tempo real o que
    # está acontecendo e ficar tranquilo de que nada se perde. Uma vez por
    # evento de compactação (este método roda 1x por cruzamento de limiar).
    if on_start is not None:
        try:
            await on_start()
        except Exception:  # noqa: BLE001 — aviso é best-effort, nunca trava
            logger.warning("compact: on_start (aviso ao operador) falhou", exc_info=True)

    transcript = _format_transcript(messages)
    prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript)

    logger.info(
        "compact: gerando summary session=%s msgs=%d prompt_len=%d",
        session_id,
        len(messages),
        len(prompt),
    )

    try:
        result = await claude.run(
            prompt,
            chat_id=chat_id,
            thread_id=thread_id,
            bot_token=bot_token,
        )
    except Exception:  # noqa: BLE001 — qualquer falha do runner volta None
        logger.exception("compact: claude.run falhou")
        return None

    summary = (result.text or "").strip()
    if not summary:
        logger.warning("compact: summary vazio, pulando compactação")
        return None

    logger.info(
        "claude_run summary=true session=%s tokens_in=%d tokens_out=%d "
        "cache_read=%d cache_create=%d cost_usd=%.5f summary_len=%d",
        session_id,
        result.input_tokens,
        result.output_tokens,
        result.cache_read_tokens,
        result.cache_creation_tokens,
        result.cost_usd,
        len(summary),
    )

    # Arquiva a sessão atual e abre uma nova.
    archived_id = archive_active_session(
        db, topic_id, summary=summary, status="compacted"
    )
    if archived_id is None:
        # Sessão deixou de ser ativa entre a leitura e o update — race
        # raro (outro caller também compactando?). Aborta sem criar nova.
        logger.warning("compact: sessão %s não era mais ativa, pulando", session_id)
        return None

    new_session_id = ensure_active_session(db, topic_id)

    # Persiste o summary como primeira "fala" da nova sessão. role='system'
    # diferencia dos turnos reais e o build_prompt monta como histórico normal.
    insert_message(
        db,
        session_id=new_session_id,
        topic_id=topic_id,
        role="system",
        content=f"[Resumo da sessão anterior]\n{summary}",
    )
    logger.info(
        "compact: rotacionado old=%s new=%s msgs_compactadas=%d",
        archived_id,
        new_session_id,
        len(messages),
    )
    return new_session_id
