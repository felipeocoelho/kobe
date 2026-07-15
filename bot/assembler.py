"""Message Assembler — agregação de mensagens picadas (Peça A da borda nova).

A borda antiga colava 1 mensagem = 1 turno = 1 chamada do modelo. Se o operador
manda o pedido em 4 envios curtos, viram 4 turnos e 4 respostas a fragmentos.
Este módulo insere um **buffer de debounce por tópico ANTES do sequenciador FIFO**:
a mensagem que chega não dispara turno na hora — entra no buffer, a borda espera
um **período de silêncio curto** e então junta os fragmentos daquele intervalo
num INTENT único, e só aí processa (um flush = um turno = um ticket FIFO).

Invariantes preservados (§7 do handoff):
- **Ordem de chegada não regride.** Cada fragmento RESERVA um índice de ordem de
  forma SÍNCRONA na chegada (antes de qualquer preparo/await), e o flush concatena
  por esse índice. Assim uma nota de voz que chega 1º mas leva segundos pra
  transcrever não perde a posição pra um texto que chegou depois (mesmo bug que o
  ticket FIFO resolveu, Defeito 1 — aqui resolvido no mesmo espírito).
- **Isolamento entre tópicos:** buffer por (chat_id, thread_id); um tópico nunca
  interfere no outro.

Constraint dura (honestidade): o Telegram NÃO entrega "usuário digitando" pro bot.
O debounce é 100% por TEMPO (inferência do ritmo), não observação real.

Atrás da flag `EDGE_ASSEMBLER_ENABLED` — ver bot/config.py e a fiação em
bot/telegram_handler.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional


logger = logging.getLogger("kobe.assembler")

# Pontuação que sinaliza "frase terminada" → dispara quase na hora (janela curta).
_TERMINAL_PUNCT = (".", "?", "!", "…", ":", ";")
# Abaixo deste tamanho, mesmo terminada em pontuação, tratamos como fragmento
# curto (ex.: "ok.", "sim!") e mantemos a janela normal — evita disparar cedo
# demais no meio de um pensamento picado.
_MIN_TERMINATED_LEN = 12

# Callback do flush: recebe (texto_agregado, audio_transcribed, message_representante).
FlushCallback = Callable[[str, bool, object], "Awaitable[None]"]


@dataclass
class _Slot:
    """Uma posição de fragmento no buffer, na ordem de chegada (índice)."""

    index: int
    text: Optional[str] = None
    audio: bool = False
    resolved: bool = False  # preenchido (fill) ou abortado (release)


@dataclass
class _Buffer:
    """Acúmulo de um ciclo de debounce de um tópico."""

    slots: list[_Slot] = field(default_factory=list)
    next_index: int = 0
    message: object = None
    flush_cb: Optional[FlushCallback] = None
    first_at: float = 0.0
    timer: Optional[asyncio.Task] = None


class MessageAssembler:
    """Debounce por tópico. Ver docstring do módulo.

    Fluxo de uso no handler:
      slot = assembler.reserve(chat_id, thread_id)      # SÍNCRONO, na chegada
      ... preparo (transcrição de áudio, etc.) ...
      await assembler.fill(chat_id, thread_id, slot, text, audio=..., message=..., flush_cb=...)
      # em caso de abort no preparo (transcrição falhou):
      await assembler.release(chat_id, thread_id, slot)
    """

    def __init__(
        self,
        *,
        quiet_ms: float,
        quiet_terminated_ms: float,
        max_wait_ms: float,
        pending_poll_ms: float = 300.0,
    ) -> None:
        self._quiet = quiet_ms / 1000.0
        self._quiet_term = quiet_terminated_ms / 1000.0
        self._max_wait = max_wait_ms / 1000.0
        self._pending_poll = pending_poll_ms / 1000.0
        self._buffers: dict[tuple[int, Optional[int]], _Buffer] = {}

    # ── API ───────────────────────────────────────────────────────────────

    def reserve(self, chat_id: int, thread_id: Optional[int]) -> int:
        """Reserva um índice de ordem de chegada. SÍNCRONO — chamar no 1º passo
        do handler, antes de qualquer await/preparo, pra carimbar a ordem."""
        key = (chat_id, thread_id)
        buf = self._buffers.get(key)
        if buf is None:
            buf = _Buffer(first_at=time.monotonic())
            self._buffers[key] = buf
        idx = buf.next_index
        buf.next_index += 1
        buf.slots.append(_Slot(index=idx))
        return idx

    async def fill(
        self,
        chat_id: int,
        thread_id: Optional[int],
        index: int,
        text: str,
        *,
        audio: bool,
        message: object,
        flush_cb: FlushCallback,
    ) -> None:
        """Preenche o slot reservado com o texto pronto e (re)arma o debounce."""
        key = (chat_id, thread_id)
        buf = self._buffers.get(key)
        if buf is None:
            # Buffer já foi flushado (cap estourou antes do preparo terminar):
            # processa este fragmento sozinho, não perde a mensagem.
            logger.info("assembler: slot %d chegou após flush; turno solo", index)
            try:
                await flush_cb(text, audio, message)
            except Exception:  # noqa: BLE001
                logger.exception("assembler: flush_cb solo levantou")
            return
        slot = self._slot(buf, index)
        if slot is not None:
            slot.text = text
            slot.audio = audio
            slot.resolved = True
        buf.message = message
        buf.flush_cb = flush_cb
        self._arm(key, buf, self._delay_for(text))

    async def release(
        self, chat_id: int, thread_id: Optional[int], index: int
    ) -> None:
        """Libera um slot que abortou no preparo (ex.: transcrição falhou). Sem
        isto o flush esperaria indefinidamente por um fragmento que nunca vem."""
        key = (chat_id, thread_id)
        buf = self._buffers.get(key)
        if buf is None:
            return
        slot = self._slot(buf, index)
        if slot is not None:
            slot.resolved = True
            slot.text = None
        # Se nada mais está pendente e há um ciclo aberto, deixa o timer existente
        # resolver; se não há timer (todos os fragmentos eram áudio e um abortou),
        # força um flush pra não vazar o buffer.
        if buf.timer is None and all(s.resolved for s in buf.slots):
            self._arm(key, buf, 0.0)

    async def flush_now(self, chat_id: int, thread_id: Optional[int]) -> None:
        """Força o flush imediato do que estiver acumulado (ex.: chegou um
        comando slash e não faz sentido debounce-ar)."""
        await self._flush((chat_id, thread_id))

    # ── Interno ───────────────────────────────────────────────────────────

    @staticmethod
    def _slot(buf: _Buffer, index: int) -> Optional[_Slot]:
        for s in buf.slots:
            if s.index == index:
                return s
        return None

    def _delay_for(self, text: str) -> float:
        stripped = (text or "").rstrip()
        if stripped.endswith(_TERMINAL_PUNCT) and len(stripped) >= _MIN_TERMINATED_LEN:
            return self._quiet_term
        return self._quiet

    def _arm(self, key, buf: _Buffer, delay: float) -> None:
        """(Re)arma o timer de debounce. Cancela o anterior (padrão debounce)."""
        if buf.timer is not None:
            buf.timer.cancel()
        # Cap de espera máxima desde o 1º fragmento: se já passou, dispara já.
        if time.monotonic() - buf.first_at >= self._max_wait:
            delay = 0.0
        buf.timer = asyncio.create_task(self._flush_after(key, delay))

    async def _flush_after(self, key, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._flush(key)

    async def _flush(self, key) -> None:
        buf = self._buffers.get(key)
        if buf is None:
            return
        # Ainda há fragmento em preparo (ex.: transcrição em voo)? Espera, mas só
        # até o cap de espera máxima — depois flusha com o que tem (não trava).
        pending = [s for s in buf.slots if not s.resolved]
        if pending and (time.monotonic() - buf.first_at) < self._max_wait:
            if buf.timer is not None:
                buf.timer.cancel()
            buf.timer = asyncio.create_task(
                self._flush_after(key, self._pending_poll)
            )
            return

        # Pronto: remove o buffer (novos fragmentos abrem ciclo novo) e processa.
        self._buffers.pop(key, None)
        if buf.timer is not None:
            buf.timer.cancel()

        ordered = sorted(buf.slots, key=lambda s: s.index)
        texts = [s.text for s in ordered if s.resolved and s.text and s.text.strip()]
        if not texts:
            return
        agg_text = "\n\n".join(t.strip() for t in texts).strip()
        audio = any(s.audio for s in ordered if s.resolved)
        cb = buf.flush_cb
        message = buf.message
        if cb is None or message is None:
            logger.warning("assembler: flush sem cb/message; descartando %d frags", len(texts))
            return
        try:
            await cb(agg_text, audio, message)
        except Exception:  # noqa: BLE001 — cb não deve derrubar o assembler
            logger.exception("assembler: flush_cb levantou")
