"""Circuit breaker do Keyko — anti-loop por (fonte, chave).

Cenário a evitar: orquestrador termina, escreve evento, Keyko vê o
evento, acorda orquestrador de novo, que termina, escreve outro evento,
e assim por diante. Loop quente queima tokens da Anthropic.

Estratégia:
- Mantemos um dict `{(fonte, chave): deque[timestamp]}` em memória.
- Antes de cada despertar, registramos o timestamp e checamos se
  ultrapassou MAX_ACORDADAS na janela WINDOW_S.
- Acima do limite, bloqueia novos despertares pra essa chave por
  COOLDOWN_S. Emite `kobe-notify` UMA vez avisando o operador.
- Restart do Keyko zera o estado (intencional — restart é sinal de
  intervenção).

Sem persistência (Fase 1). Se virar problema, fácil portar pra JSON
em disco depois.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional


logger = logging.getLogger("kobe.keyko.breaker")

MAX_ACORDADAS = 10
WINDOW_S = 5 * 60        # 5 minutos
COOLDOWN_S = 30 * 60     # 30 minutos de bloqueio depois do trip


class CircuitBreaker:
    def __init__(self, *, kobe_home: Path):
        # Histórico por chave: deque de timestamps das acordadas recentes
        self._historico: dict[tuple[str, str], deque[float]] = {}
        # Quando uma chave está em cooldown — só desbloqueia após o ts
        self._cooldown_ate: dict[tuple[str, str], float] = {}
        # Já avisamos o operador desse trip? evita spam
        self._avisado: set[tuple[str, str]] = set()
        self._kobe_home = kobe_home

    def permitir(
        self, *, fonte: str, chave: str, chat_id: int, thread_id: Optional[int],
        bot_token: str,
    ) -> bool:
        """Devolve True se pode despertar; False se está em cooldown.

        Registra o timestamp se permitir. Se este despertar fizer
        ultrapassar o limite, dispara cooldown E manda kobe-notify
        no chat informando.
        """
        agora = time.monotonic()
        chave_full = (fonte, chave)

        # Em cooldown ativo?
        cd = self._cooldown_ate.get(chave_full)
        if cd is not None and agora < cd:
            return False
        if cd is not None and agora >= cd:
            # Cooldown expirou — limpa estado pra dar nova chance
            self._cooldown_ate.pop(chave_full, None)
            self._historico.pop(chave_full, None)
            self._avisado.discard(chave_full)

        # Registra esta acordada
        hist = self._historico.setdefault(chave_full, deque())
        # Limpa entradas fora da janela
        limite = agora - WINDOW_S
        while hist and hist[0] < limite:
            hist.popleft()
        hist.append(agora)

        if len(hist) > MAX_ACORDADAS:
            # Trip!
            self._cooldown_ate[chave_full] = agora + COOLDOWN_S
            if chave_full not in self._avisado:
                self._avisado.add(chave_full)
                _notify_trip(
                    fonte=fonte, chave=chave,
                    n=len(hist), window_s=WINDOW_S, cooldown_s=COOLDOWN_S,
                    chat_id=chat_id, thread_id=thread_id,
                    bot_token=bot_token, kobe_home=self._kobe_home,
                )
            logger.warning(
                "circuit breaker trip fonte=%s chave=%s acordadas=%d janela=%ds",
                fonte, chave, len(hist), WINDOW_S,
            )
            return False

        return True


def _notify_trip(
    *,
    fonte: str, chave: str, n: int, window_s: int, cooldown_s: int,
    chat_id: int, thread_id: Optional[int], bot_token: str,
    kobe_home: Path,
) -> None:
    """Manda mensagem no Telegram avisando do trip. Best-effort."""
    cooldown_min = cooldown_s // 60
    msg = (
        f"🔴 [keyko] circuit breaker disparado: fonte={fonte} chave={chave}\n"
        f"{n} acordadas em <{window_s // 60}min — bloqueado por {cooldown_min}min.\n"
        f"Investigue: orquestrador em loop? Tarefa flapping? "
        f"Veja `journalctl --user -u keyko` e o `eventos.jsonl` da chave."
    )
    notify_bin = kobe_home / "bot" / "bin" / "kobe-notify"
    env = dict(os.environ)
    env["KOBE_TELEGRAM_BOT_TOKEN"] = bot_token
    env["KOBE_CHAT_ID"] = str(chat_id)
    if thread_id is not None:
        env["KOBE_THREAD_ID"] = str(thread_id)
    else:
        env.pop("KOBE_THREAD_ID", None)
    try:
        subprocess.run(
            [str(notify_bin), msg],
            env=env,
            timeout=15,
            check=False,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception("falha notificando trip do circuit breaker")
