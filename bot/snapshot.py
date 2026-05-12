"""Snapshot-de-continuação: preserva contexto através de restarts.

Quando o bot recebe SIGTERM (deploy via `systemctl restart`), persiste
as sessões ativas recentes como `saved_artifacts` com tag `auto-resume`.
No boot seguinte, antes de processar mensagens novas, manda uma mensagem
proativa no tópico ("⏯️ Voltei…") e apaga o artefato.

Decisões:

- **Por tópico, não global.** Cada conversa ativa precisa do seu próprio
  fio recuperado — agrupar tudo num só snapshot global perderia o
  endereçamento por `chat_id` + `thread_id`.
- **Recorte por atividade.** Só salvamos sessões com mensagem nos
  últimos `RECENT_ACTIVITY_WINDOW_MINUTES`. Sem isso, todo restart
  ressuscitaria conversas que o operador já esqueceu.
- **Conteúdo cru.** Guardamos as últimas `SNAPSHOT_RECENT_COUNT`
  mensagens em texto. Não chamamos o LLM pra resumir no SIGTERM porque
  só temos ~10s antes do SIGKILL do systemd.
- **TTL curto.** Snapshot vence em `SNAPSHOT_TTL_MINUTES`. Se o bot
  demorar pra subir (debug, falha de deploy), descartamos em vez de
  acordar contexto morto.
- **Reuso do schema existente.** `saved_artifacts.tags` já existe e
  filtra via PostgREST `contains` — não precisa de tabela nova.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client


logger = logging.getLogger("kobe.snapshot")

# Sessões "vivas" pra fins de snapshot: atividade nos últimos N minutos.
RECENT_ACTIVITY_WINDOW_MINUTES = 30

# Quantas mensagens cruas entram no snapshot por sessão.
SNAPSHOT_RECENT_COUNT = 6

# Snapshot vence após N minutos sem ter sido consumido.
SNAPSHOT_TTL_MINUTES = 10

# Tag em saved_artifacts.tags que distingue snapshots de auto-resume dos
# artefatos comuns gerados por /salvar.
SNAPSHOT_TAG = "auto-resume"


def save_pending_snapshots(db: Client) -> int:
    """Salva snapshots de todas as sessões ativas recentes elegíveis.

    Retorna a contagem gravada. Não levanta — falhas individuais por
    tópico são logadas e ignoradas (não queremos abortar o shutdown
    porque um tópico deu ruim).
    """
    now = datetime.now(timezone.utc)
    threshold = (now - timedelta(minutes=RECENT_ACTIVITY_WINDOW_MINUTES)).isoformat()

    try:
        topics_res = (
            db.table("topics")
            .select("id, telegram_thread_id, telegram_chat_id")
            .eq("status", "active")
            .gte("last_activity_at", threshold)
            .execute()
        )
    except Exception:  # noqa: BLE001 — qualquer falha de rede/DB
        logger.exception("falha listando topics pra snapshot")
        return 0

    saved = 0
    for topic in topics_res.data or []:
        # Sem chat_id, mensagem proativa no boot é impossível — pular.
        if topic.get("telegram_chat_id") is None:
            continue
        try:
            if _save_one_topic_snapshot(db, topic):
                saved += 1
        except Exception:  # noqa: BLE001 — não derrubar shutdown
            logger.exception("falha snapshot topic_id=%s", topic.get("id"))
    return saved


def _save_one_topic_snapshot(db: Client, topic: dict) -> bool:
    """Grava o snapshot de uma sessão ativa do tópico. Retorna True se gravou."""
    topic_id = topic["id"]

    sess_res = (
        db.table("sessions")
        .select("id")
        .eq("topic_id", topic_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not sess_res.data:
        return False
    session_id = sess_res.data[0]["id"]

    msgs_res = (
        db.table("messages")
        .select("role, content, created_at")
        .eq("session_id", session_id)
        .order("created_at", desc=True)
        .limit(SNAPSHOT_RECENT_COUNT)
        .execute()
    )
    msgs = list(reversed(msgs_res.data or []))
    if not msgs:
        return False

    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "topic_id": topic_id,
        "telegram_chat_id": topic["telegram_chat_id"],
        "telegram_thread_id": topic.get("telegram_thread_id"),
        "session_id": session_id,
        "messages": msgs,
        "saved_at": saved_at,
    }

    db.table("saved_artifacts").insert(
        {
            "topic_id": topic_id,
            "title": f"auto-resume {saved_at}",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
            "tags": [SNAPSHOT_TAG],
        }
    ).execute()
    logger.info(
        "snapshot gravado topic_id=%s session_id=%s msgs=%d",
        topic_id,
        session_id,
        len(msgs),
    )
    return True


def load_pending_snapshots(db: Client) -> list[dict]:
    """Lista snapshots ainda válidos (criados dentro do TTL).

    Se houver mais de um pro mesmo tópico (não deveria acontecer mas é
    defensivo), mantemos só o mais recente. Cada payload retornado
    inclui `_artifact_id` pra `drop_snapshot` depois.
    """
    threshold = (
        datetime.now(timezone.utc) - timedelta(minutes=SNAPSHOT_TTL_MINUTES)
    ).isoformat()

    try:
        res = (
            db.table("saved_artifacts")
            .select("id, content, created_at, topic_id")
            .contains("tags", [SNAPSHOT_TAG])
            .gte("created_at", threshold)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha carregando snapshots pendentes")
        return []

    seen_topics: set[str] = set()
    snapshots: list[dict] = []
    for row in res.data or []:
        topic_id = row.get("topic_id")
        if topic_id in seen_topics:
            continue
        seen_topics.add(topic_id)
        try:
            payload = json.loads(row["content"])
        except (json.JSONDecodeError, TypeError):
            logger.warning("snapshot %s tem content inválido — ignorando", row["id"])
            continue
        payload["_artifact_id"] = row["id"]
        snapshots.append(payload)
    return snapshots


def drop_snapshot(db: Client, artifact_id: str) -> None:
    """Remove um snapshot após ele ter sido consumido pela mensagem proativa."""
    try:
        db.table("saved_artifacts").delete().eq("id", artifact_id).execute()
    except Exception:  # noqa: BLE001 — não crítico; TTL coleta no próximo boot
        logger.exception("falha apagando snapshot %s", artifact_id)


def cleanup_expired_snapshots(db: Client) -> int:
    """Limpa snapshots vencidos. Idempotente — bom rodar no boot."""
    threshold = (
        datetime.now(timezone.utc) - timedelta(minutes=SNAPSHOT_TTL_MINUTES)
    ).isoformat()
    try:
        res = (
            db.table("saved_artifacts")
            .delete()
            .contains("tags", [SNAPSHOT_TAG])
            .lt("created_at", threshold)
            .execute()
        )
    except Exception:  # noqa: BLE001
        logger.exception("falha limpando snapshots expirados")
        return 0
    return len(res.data or [])


def render_resume_message(payload: dict) -> str:
    """Formata o snapshot pra mensagem proativa de retomada.

    Estratégia: cabeçalho fixo + última mensagem do operador como gancho
    visual. Sem repetir as N mensagens cruas: o Kobe relê do banco quando
    o operador responder, o snapshot serve só pro "estou de volta".
    """
    msgs = payload.get("messages") or []
    user_msgs = [m.get("content", "") for m in msgs if m.get("role") == "user"]
    last_user = user_msgs[-1].strip() if user_msgs else ""
    if len(last_user) > 220:
        last_user = last_user[:220].rstrip() + "…"

    if last_user:
        return f'⏯️ Voltei. Antes do restart você tinha mandado: "{last_user}"'
    return (
        "⏯️ Voltei. A conversa estava ativa, mas a última mensagem foi minha — "
        "pode continuar de onde paramos."
    )
