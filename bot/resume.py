"""Boot-resume: re-situa o AGENTE após restart — não só pinga o operador.

Problema corrigido (bug-retomada-contexto, 2026-06-04)
------------------------------------------------------
Até aqui, no boot, `snapshot.render_resume_message` montava um template
fixo em Python ("⏯️ Voltei, você tinha mandado X", citando só a última
fala do operador) e o enviava direto pro Telegram. **O agente nunca era
invocado.** Ele só voltava a se "inserir no fluxo" SE/QUANDO o operador
mandasse uma mensagem nova — que aí sim passa pelo turno normal (onde a
camada imediata do Chat Manager já funciona). Resultado: numa retomada,
o contexto imediato (≈últimos 10 min) não chegava ao agente, e a mensagem
de volta era um template burro, não uma síntese real de onde a conversa
estava.

Correção
--------
Pra cada tópico com snapshot pendente, montamos o MESMO contexto de um
turno normal — camada imediata (`get_immediate_messages`), ponteiros do
Chat Manager (`render_chat_manager_section` + cronologia comprimida),
knowledge base do tópico, alertas/missão abertos — e invocamos o agente
com uma diretiva de retomada. Ele relê, entende onde estava e manda ao
operador UMA síntese de onde param. Uniforme com o turno normal: toda
retomada apresenta o contexto imediato ao agente.

Salvaguardas
------------
- **Lock por tópico**: a retomada roda sob o mesmo `asyncio.Lock` do
  `telegram_handler`, serializando com mensagens que cheguem logo após o
  boot (sem disparo duplo de agente no mesmo tópico).
- **Guarda de atividade**: se já houve mensagem no tópico DEPOIS do
  snapshot (operador voltou a falar antes da retomada rodar), pulamos —
  o turno normal já re-situou; não pingamos de novo.
- **Fallback gracioso**: se `claude.run` falhar/estourar timeout, caímos
  no template antigo (`render_resume_message`) — nunca regredimos a
  silêncio.
- **Persistência**: a síntese vira `messages` (role=assistant) na sessão
  ativa, então o próximo turno já a enxerga na janela imediata.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from supabase import Client
from telegram.ext import Application

from bot.alertas.context import render_alertas_abertos
from bot.chat_manager.context import render_chat_manager_section
from bot.memory import (
    get_immediate_messages,
    load_curated_core,
    render_grounding_signals,
)
from bot.claude_runner import ClaudeError, ClaudeRunner, build_prompt
from bot.config import Config
from bot.markdown import to_telegram_html
from bot.missoes import storage as missoes_storage
from bot.snapshot import drop_snapshot, render_resume_message
from bot.topic_manager import (
    GENERAL_THREAD_ID,
    consume_truncated_marker,
    ensure_active_session,
    get_active_conversation_for_topic,
    get_conversation_session_summaries,
    get_recent_messages,
    get_topic_slug,
    insert_message,
    load_topic_context,
)


logger = logging.getLogger("kobe.resume")


# Diretiva injetada como "mensagem nova" do turno de retomada. NÃO é uma
# fala do operador — é a instrução pro agente se re-situar e escrever a
# mensagem de volta. O contexto imediato (acima dela no prompt) é o que
# ele lê pra entender onde a conversa estava.
RESUME_DIRECTIVE = (
    "[RETOMADA APÓS REINÍCIO DO SERVIÇO]\n"
    "Você acabou de voltar ao ar depois de um restart (deploy/reinício). "
    "O bloco acima traz o contexto imediato desta conversa: os últimos "
    "minutos antes da interrupção e os ponteiros de assunto. Releia, "
    "entenda ONDE a conversa estava — qual assunto, que tarefa estava em "
    "andamento, o que ficou pendente — e mande ao operador UMA mensagem "
    "curta retomando o fio de onde vocês pararam. Mostre que se situou "
    "(ex.: \"voltei — a gente estava em X, você tinha pedido Y, sigo com "
    "Z\"). NÃO use um template fixo: sintetize o estado real a partir do "
    "contexto. Se a última fala foi sua (e não do operador), apenas "
    "sinalize que voltou e está pronto pra continuar. Direto, no seu tom "
    "de sempre. O texto que você responder agora é o que será enviado ao "
    "operador."
)


def _api_thread_id(thread_id: Optional[int]) -> Optional[int]:
    """Sentinela 0 (general) e None viram None pra API do Telegram."""
    if thread_id in (None, GENERAL_THREAD_ID):
        return None
    return thread_id


def _latest_created_at(messages: list[dict]) -> Optional[str]:
    """Maior `created_at` (ISO) entre as mensagens. None se vazio."""
    stamps = [m.get("created_at") for m in messages if m.get("created_at")]
    return max(stamps) if stamps else None


def has_activity_after(messages: list[dict], saved_at: Optional[str]) -> bool:
    """True se há mensagem mais nova que `saved_at` (atividade pós-restart).

    `saved_at` é o instante em que o snapshot foi gravado (no shutdown).
    Qualquer mensagem com `created_at` posterior significa que o operador
    já voltou a falar antes da retomada rodar — então o turno normal já
    re-situou o agente e não devemos pingar de novo.

    Comparação lexicográfica de ISO-8601 em UTC (ambos `…+00:00`/`Z`) é
    equivalente à cronológica.
    """
    if not saved_at:
        return False
    latest = _latest_created_at(messages)
    return latest is not None and latest > saved_at


def build_resume_prompt(
    *,
    thread_id: Optional[int],
    immediate_history: list[dict],
    chat_manager_section: Optional[str] = None,
    conversation_summaries: Optional[list[dict]] = None,
    topic_context: Optional[str] = None,
    missao_ativa_info: Optional[str] = None,
    alertas_abertos_info: Optional[str] = None,
    curated_core: Optional[str] = None,
    grounding_signals: Optional[str] = None,
) -> str:
    """Monta o prompt do turno de retomada.

    Reusa `build_prompt` — mesma estrutura do turno normal — com a
    `RESUME_DIRECTIVE` no lugar da mensagem do operador. Assim o agente
    recebe exatamente as mesmas camadas de contexto que receberia num
    turno comum, garantindo uniformidade entre retomada e fluxo normal.
    """
    return build_prompt(
        thread_id=thread_id,
        history=immediate_history,
        new_message=RESUME_DIRECTIVE,
        topic_context=topic_context,
        missao_ativa_info=missao_ativa_info,
        alertas_abertos_info=alertas_abertos_info,
        conversation_summaries=conversation_summaries,
        chat_manager_section=chat_manager_section,
        curated_core=curated_core,
        grounding_signals=grounding_signals,
    )


def _load_resume_context(
    db: Client, config: Config, snap: dict
) -> dict:
    """Carrega (síncrono, read-only) as camadas de contexto do tópico.

    Espelha o que `_handle_user_text` monta num turno normal. Roda em
    thread (via `asyncio.to_thread`) pra não travar o event loop no boot.
    """
    topic_id = snap["topic_id"]
    chat_id = snap.get("telegram_chat_id")
    thread_id = snap.get("telegram_thread_id")

    # Camada imediata: idêntica ao turno normal quando CM on. No legado
    # (flag off) caímos pro histórico da sessão arquivada do snapshot.
    if config.chat_manager_enabled:
        immediate = get_immediate_messages(db, topic_id)
    else:
        session_id = snap.get("session_id")
        immediate = (
            get_recent_messages(db, session_id, limit=config.recent_messages_limit)
            if session_id
            else []
        )

    chat_manager_section: Optional[str] = None
    conversation_summaries: list[dict] = []
    if config.chat_manager_enabled:
        try:
            chat_manager_section = render_chat_manager_section(db, topic_id)
            active_conv = get_active_conversation_for_topic(db, topic_id)
            if active_conv is not None:
                conversation_summaries = get_conversation_session_summaries(
                    db, active_conv["id"], except_session_id=snap.get("session_id")
                )
        except Exception:  # noqa: BLE001 — CM nunca derruba a retomada
            logger.warning("resume: load Chat Manager falhou", exc_info=True)

    # Knowledge base do tópico (prompt.md + knowledge/*).
    topic_context: Optional[str] = None
    if chat_id is not None:
        slug = get_topic_slug(db, chat_id, thread_id)
        raw = load_topic_context(config.kobe_home, slug) if slug else None
        topic_context, _truncated = consume_truncated_marker(raw)

    # Núcleo curado global (Highlander Frente 1.2): mesma camada do turno
    # normal, atrás da flag. None se off/ausente.
    curated_core: Optional[str] = (
        load_curated_core(config.kobe_home) if config.curated_core_enabled else None
    )

    # Sinais de grounding temporais (Frente 1.1): há quanto tempo foi a última
    # troca antes deste ping de retomada. Atrás da flag.
    grounding_signals: Optional[str] = (
        render_grounding_signals(immediate)
        if config.grounding_signals_enabled
        else None
    )

    # Alertas abertos e missão ativa — linhas baratas de ciência (sem LLM).
    alertas_abertos_info: Optional[str] = None
    missao_ativa_info: Optional[str] = None
    if chat_id is not None:
        alertas_abertos_info = render_alertas_abertos(
            config.kobe_home, chat_id, thread_id
        )
        ativa = missoes_storage.find_missao_ativa(
            config.kobe_home, chat_id, thread_id
        )
        if ativa is not None:
            objetivo_curto = (ativa.objetivo or "")[:80]
            missao_ativa_info = f'[Missão ativa: {ativa.id} — "{objetivo_curto}"]'

    return {
        "immediate": immediate,
        "chat_manager_section": chat_manager_section,
        "conversation_summaries": conversation_summaries,
        "topic_context": topic_context,
        "alertas_abertos_info": alertas_abertos_info,
        "missao_ativa_info": missao_ativa_info,
        "curated_core": curated_core,
        "grounding_signals": grounding_signals,
    }


async def _send_synthesis(app: Application, snap: dict, text: str) -> None:
    """Envia a síntese ao operador, fatiando no limite do Telegram (HTML)."""
    chat_id = snap["telegram_chat_id"]
    api_thread = _api_thread_id(snap.get("telegram_thread_id"))
    # Import tardio pra evitar ciclo no import-time (telegram_handler é
    # pesado e importa muita coisa; aqui só precisamos do split).
    from bot.telegram_handler import TELEGRAM_TEXT_LIMIT, _split_for_telegram

    for chunk in _split_for_telegram(text, TELEGRAM_TEXT_LIMIT):
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=to_telegram_html(chunk),
                message_thread_id=api_thread,
                parse_mode="HTML",
            )
        except Exception:  # noqa: BLE001 — HTML órfão → fallback plain
            logger.warning("resume: envio HTML falhou, plain", exc_info=True)
            await app.bot.send_message(
                chat_id=chat_id, text=chunk, message_thread_id=api_thread
            )


async def _send_fallback_ping(app: Application, snap: dict) -> None:
    """Último recurso: o template antigo (nunca regredir a silêncio)."""
    try:
        await app.bot.send_message(
            chat_id=snap["telegram_chat_id"],
            text=render_resume_message(snap),
            message_thread_id=_api_thread_id(snap.get("telegram_thread_id")),
        )
    except Exception:  # noqa: BLE001
        logger.exception("resume: fallback-ping também falhou")


async def resume_one_snapshot(app: Application, snap: dict) -> None:
    """Re-situa o agente pra um snapshot. Nunca levanta (fire-and-forget).

    Protegido pelo lock do tópico (serializa com o handler normal). Apaga
    o snapshot ao final em qualquer desfecho — uso único, sem replay.
    """
    db: Client = app.bot_data["db"]
    claude: ClaudeRunner = app.bot_data["claude"]
    config: Config = app.bot_data["config"]

    chat_id = snap.get("telegram_chat_id")
    if chat_id is None:
        return

    # Sequenciador FIFO por tópico — mesma instância usada pelo telegram_handler.
    # Serializa o resume com os handlers normais (e respeita a ordem de chegada).
    from bot.telegram_handler import _serve

    raw_thread = snap.get("telegram_thread_id")
    # A seção crítica do handler é chaveada pelo thread_id "de transporte" (None
    # pro general), não pela sentinela 0 do banco.
    artifact_id = snap.get("_artifact_id")
    async with _serve(chat_id, _api_thread_id(raw_thread)):
        try:
            ctx = await asyncio.to_thread(_load_resume_context, db, config, snap)

            # Guarda de atividade: operador já voltou a falar pós-restart?
            if has_activity_after(ctx["immediate"], snap.get("saved_at")):
                logger.info(
                    "resume: atividade pós-restart no topic=%s — pulando ping",
                    snap.get("topic_id"),
                )
                return

            prompt = build_resume_prompt(
                thread_id=_api_thread_id(raw_thread),
                immediate_history=ctx["immediate"],
                chat_manager_section=ctx["chat_manager_section"],
                conversation_summaries=ctx["conversation_summaries"],
                topic_context=ctx["topic_context"],
                missao_ativa_info=ctx["missao_ativa_info"],
                alertas_abertos_info=ctx["alertas_abertos_info"],
                curated_core=ctx["curated_core"],
                grounding_signals=ctx["grounding_signals"],
            )

            try:
                result = await claude.run(
                    prompt,
                    chat_id=chat_id,
                    thread_id=raw_thread if raw_thread != GENERAL_THREAD_ID else None,
                    bot_token=config.telegram_bot_token,
                )
                synthesis = (result.text or "").strip()
            except ClaudeError:
                logger.warning(
                    "resume: claude.run falhou topic=%s — fallback ping",
                    snap.get("topic_id"),
                    exc_info=True,
                )
                synthesis = ""

            if not synthesis:
                await _send_fallback_ping(app, snap)
                return

            await _send_synthesis(app, snap, synthesis)

            # Persiste a síntese pra entrar na janela imediata do próximo
            # turno (o agente "lembra" que já se situou).
            try:
                topic_id = snap["topic_id"]
                session_id = ensure_active_session(db, topic_id)
                insert_message(
                    db,
                    session_id=session_id,
                    topic_id=topic_id,
                    role="assistant",
                    content=synthesis,
                )
            except Exception:  # noqa: BLE001 — persistir é nice-to-have
                logger.warning("resume: persistir síntese falhou", exc_info=True)

            logger.info(
                "resume: agente re-situado topic=%s synthesis_len=%d",
                snap.get("topic_id"),
                len(synthesis),
            )
        except Exception:  # noqa: BLE001 — fire-and-forget: nunca derruba o boot
            logger.exception(
                "resume: erro inesperado topic=%s — fallback ping",
                snap.get("topic_id"),
            )
            await _send_fallback_ping(app, snap)
        finally:
            if artifact_id:
                drop_snapshot(db, artifact_id)


async def resume_pending_snapshots(app: Application, pending: list[dict]) -> None:
    """Dispara a retomada de cada snapshot como task de fundo.

    Background (não bloqueia o boot/polling): cada task pega o lock do seu
    tópico, então o handler normal e a retomada se serializam por tópico,
    mas tópicos distintos correm em paralelo. As tasks ficam referenciadas
    em `app.bot_data` pra não serem coletadas pelo GC.
    """
    tasks: set[asyncio.Task] = app.bot_data.setdefault("resume_tasks", set())
    for snap in pending:
        if snap.get("telegram_chat_id") is None:
            continue
        task = asyncio.create_task(
            resume_one_snapshot(app, snap),
            name=f"resume-{snap.get('topic_id')}",
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)
