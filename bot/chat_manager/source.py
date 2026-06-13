"""ClassifierSource — ofício do New Chat Manager dentro do Keyko.

Diferente das outras sources (Missões, Alertas), esta NÃO acorda o Claude
(não produz Despertar). Ela faz o trabalho do bibliotecário diretamente,
atrás, pós-resposta: lê os sinais de atividade, aplica debounce por
silêncio + disjuntor de teto, e dispara a classificação do lote.

Mecânica do gatilho (doc §4.1):
- Debounce por silêncio (principal): só classifica um topic quando o
  operador ficou quieto >= SILENCE_WINDOW_S. No frenesi de N áudios,
  acorda UMA vez, quando ele respira.
- Disjuntor de teto (proteção): se há lote pendente e já faz >
  CEILING_S desde a última passada, classifica mesmo sem pausa (sessão
  longa contínua não pode inflar sem limite).

A classificação roda num ÚNICO worker thread com event loop próprio e
persistente — assim o loop síncrono do Keyko nunca bloqueia, e o cliente
async da OpenAI (embedding) é reusado num loop só (evita "event loop is
closed" de reusar httpx.AsyncClient entre loops diferentes).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.chat_manager import activity
from bot.chat_manager.classifier import classify_topic, knobs_from_env
from bot.config import _parse_bool
from bot.keyko.models import Despertar


logger = logging.getLogger("kobe.chat_manager.source")

SILENCE_WINDOW_S = 60.0   # debounce: silêncio do operador antes de classificar
FLUSH_AFTER_SILENCE_S = 180.0  # silêncio prolongado: força resolução do tail órfão
CEILING_S = 600.0         # disjuntor de teto: força passada em sessão contínua
TICK_INTERVAL_S = 5.0     # de quanto em quanto o source checa os sinais


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ClassifierSource:
    """Source do Keyko que mantém o catálogo do Chat Manager atrás do turno."""

    def __init__(self, *, kobe_home: Path, bot_token: str) -> None:
        self._kobe_home = kobe_home
        self._bot_token = bot_token
        self._db = None  # lazy
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker: Optional[threading.Thread] = None
        self._in_progress: set[str] = set()
        self._lock = threading.Lock()

    @property
    def nome(self) -> str:
        return "chat_manager"

    @property
    def intervalo_s(self) -> float:
        return TICK_INTERVAL_S

    # -- worker thread (event loop persistente) -----------------------------

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            loop.run_forever()

        t = threading.Thread(target=_run, name="cm-classifier", daemon=True)
        t.start()
        self._loop = loop
        self._worker = t

    def _get_db(self):
        if self._db is None:
            from bot.config import load_config
            from bot.db import build_client

            self._db = build_client(load_config())
        return self._db

    # -- tick ----------------------------------------------------------------

    def tick(self) -> list[Despertar]:
        import os

        if not _parse_bool(os.getenv("CHAT_MANAGER_ENABLED")):
            return []

        signals = activity.list_activity(self._kobe_home)
        if not signals:
            return []

        self._ensure_worker()
        now = time.time()

        for sig in signals:
            topic_id = sig.get("topic_id")
            if not topic_id:
                continue
            with self._lock:
                if topic_id in self._in_progress:
                    continue

            last_msg_iso = sig.get("last_user_msg_at")
            last_msg_dt = _parse_iso(last_msg_iso)
            if last_msg_dt is None:
                continue

            state = activity.read_state(self._kobe_home, topic_id)
            watermark = state.get("classified_through_at")

            # Nada novo: já classificou até a última msg do operador.
            if watermark and last_msg_iso and watermark >= last_msg_iso:
                continue

            silence = now - last_msg_dt.timestamp()
            last_run = _parse_iso(state.get("last_classified_run_at"))
            ceiling_due = (
                last_run is not None
                and (now - last_run.timestamp()) >= CEILING_S
            )
            if silence < SILENCE_WINDOW_S and not ceiling_due:
                continue

            # Silêncio prolongado (> debounce): força a resolução do tail órfão
            # que a histerese seguraria esperando "a próxima msg" (item 3 do bug
            # — sem isso o watermark congela quando o operador troca de assunto
            # e silencia). Tunável por env sem deploy.
            try:
                flush_after = float(
                    os.getenv("CM_TAIL_FLUSH_S") or FLUSH_AFTER_SILENCE_S
                )
            except ValueError:
                flush_after = FLUSH_AFTER_SILENCE_S
            self._submit(topic_id, watermark, silence >= flush_after)

        return []

    def _submit(
        self, topic_id: str, watermark: Optional[str], force_resolve_tail: bool
    ) -> None:
        with self._lock:
            self._in_progress.add(topic_id)
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(
            self._classify(topic_id, watermark, force_resolve_tail), self._loop
        )

    async def _classify(
        self, topic_id: str, watermark: Optional[str], force_resolve_tail: bool
    ) -> None:
        try:
            db = self._get_db()
            knobs = knobs_from_env()
            result = await classify_topic(
                db,
                topic_id,
                watermark=watermark,
                knobs=knobs,
                force_resolve_tail=force_resolve_tail,
            )
            # Avança o watermark só se algo foi commitado; senão preserva o
            # anterior (mas atualiza last_classified_run_at pro disjuntor).
            effective = result.watermark or watermark
            # Preserva o ponteiro do quente quando a passada não devolve um
            # active (passada sem commit não pode ZERAR o active_conversation_id
            # — era o que corrompia o state e sumia o bloco do prompt).
            effective_active = result.active_conversation_id or activity.read_state(
                self._kobe_home, topic_id
            ).get("active_conversation_id")
            activity.write_state(
                self._kobe_home,
                topic_id,
                classified_through_at=effective,
                active_conversation_id=effective_active,
            )
            if result.processed or result.borders:
                logger.info(
                    "chat_manager classify topic=%s processed=%d borders=%d "
                    "pending=%d active=%s",
                    topic_id[:8],
                    result.processed,
                    result.borders,
                    result.pending,
                    (result.active_conversation_id or "-")[:8],
                )
            # Aviso discreto ao operador a cada transição de assunto (borda que
            # abriu conversation nova). Best-effort, fora do caminho do turno.
            for conv in result.new_conversations:
                self._notify_new_conversation(db, topic_id, conv.get("title"))
        except Exception:  # noqa: BLE001 — daemon não pode morrer por topic bugado
            logger.exception("chat_manager: classify falhou topic=%s", topic_id)
        finally:
            with self._lock:
                self._in_progress.discard(topic_id)

    def _notify_new_conversation(
        self, db, topic_id: str, title: Optional[str]
    ) -> None:
        """Manda UMA linha no Telegram avisando que um assunto novo foi
        detectado. Best-effort: erro aqui nunca afeta a classificação. Usa o
        mesmo padrão do circuit breaker — subprocess kobe-notify com as envs
        KOBE_* injetadas, chat/thread vindos do topic."""
        try:
            row = (
                db.table("topics")
                .select("telegram_chat_id, telegram_thread_id")
                .eq("id", topic_id)
                .limit(1)
                .execute()
            )
            if not row.data:
                return
            chat_id = row.data[0].get("telegram_chat_id")
            thread_id = row.data[0].get("telegram_thread_id")
            if chat_id is None:
                return

            # Uma linha, discreto. Com o título por tema (item 2), inclui o
            # nome do assunto; se vier vazio/genérico, fica só a linha base.
            t = (title or "").strip()
            if t and t.lower() not in ("conversa nova", "(sem título)"):
                msg = f"📑 Novo assunto detectado — abri uma conversa nova: “{t}”."
            else:
                msg = "📑 Novo assunto detectado — abri uma conversa nova pra isso."

            notify_bin = self._kobe_home / "bot" / "bin" / "kobe-notify"
            import os
            import subprocess

            env = dict(os.environ)
            env["KOBE_TELEGRAM_BOT_TOKEN"] = self._bot_token
            env["KOBE_CHAT_ID"] = str(chat_id)
            if thread_id is not None:
                env["KOBE_THREAD_ID"] = str(thread_id)
            else:
                env.pop("KOBE_THREAD_ID", None)
            subprocess.run(
                [str(notify_bin), msg],
                env=env,
                timeout=15,
                check=False,
                capture_output=True,
            )
        except Exception:  # noqa: BLE001 — aviso é best-effort
            logger.warning("chat_manager: falha avisando conversa nova", exc_info=True)
