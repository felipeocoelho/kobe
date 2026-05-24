"""Loop principal do Keyko — itera sources, executa despertares.

Daemon single-threaded síncrono. Cada source tem seu próprio
`intervalo_s`; o loop dorme `MIN_INTERVAL_S` por iteração e chama
`tick()` na hora certa de cada source. Despertares vão pro circuit
breaker; se passar, dispara `claude -p` em background detached.

Não esperamos pelo claude — fire-and-forget. O efeito (mudança em
estado/eventos) é visto pelas sources no próximo tick.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from bot.keyko.breaker import CircuitBreaker
from bot.keyko.models import Despertar, Source


logger = logging.getLogger("kobe.keyko.loop")

# Loop dorme isso entre iterações. Min de granularidade do scheduler
# embutido. Sources com intervalo menor são chamadas no max desse.
MIN_INTERVAL_S = 0.5


class KeykoLoop:
    """Encapsula o loop pra teste programático (sem precisar fork/exec)."""

    def __init__(
        self,
        *,
        sources: list[Source],
        kobe_home: Path,
        bot_token: str,
    ):
        self._sources = sources
        self._kobe_home = kobe_home
        self._bot_token = bot_token
        self._breaker = CircuitBreaker(kobe_home=kobe_home)
        self._proximo_tick: dict[str, float] = {
            s.nome: 0.0 for s in sources
        }
        self._parar = False

    def parar(self, *_args) -> None:
        """Sinal handler-friendly — pode passar (signum, frame) e ignora."""
        logger.info("keyko: SIGTERM/SIGINT recebido — encerrando loop")
        self._parar = True

    def passo(self) -> int:
        """Roda UMA iteração do loop. Devolve quantos despertares executou.

        Exposto pra teste — em produção é chamado dentro de `run()`.
        """
        agora = time.monotonic()
        executados = 0
        for source in self._sources:
            if agora < self._proximo_tick.get(source.nome, 0.0):
                continue
            try:
                despertares = source.tick()
            except Exception:  # noqa: BLE001 — source bugada não derruba daemon
                logger.exception("source %s.tick() levantou — pulando", source.nome)
                despertares = []
            # Reagendar próximo tick mesmo se errou
            self._proximo_tick[source.nome] = agora + source.intervalo_s

            for despertar in despertares:
                permitido = self._breaker.permitir(
                    fonte=despertar.fonte,
                    chave=despertar.chave,
                    chat_id=despertar.chat_id,
                    thread_id=despertar.thread_id,
                    bot_token=self._bot_token,
                )
                if not permitido:
                    logger.info(
                        "despertar bloqueado pelo breaker fonte=%s chave=%s motivo=%s",
                        despertar.fonte, despertar.chave, despertar.motivo,
                    )
                    continue
                _disparar_despertar(despertar, self._bot_token, self._kobe_home)
                executados += 1
        return executados

    def run(self) -> None:
        """Loop infinito até receber SIGTERM/SIGINT."""
        signal.signal(signal.SIGTERM, self.parar)
        signal.signal(signal.SIGINT, self.parar)
        logger.info(
            "keyko: iniciando loop com %d source(s): %s",
            len(self._sources), [s.nome for s in self._sources],
        )
        while not self._parar:
            self.passo()
            # Sleep granular pra detectar parar rapidamente
            time.sleep(MIN_INTERVAL_S)
        logger.info("keyko: loop encerrado")


def _disparar_despertar(
    despertar: Despertar, bot_token: str, kobe_home: Path,
) -> Optional[subprocess.Popen]:
    """Dispara `claude -p` em background detached. Volta imediato."""
    env = dict(os.environ)
    env["KOBE_HOME"] = str(kobe_home)
    env["KOBE_TELEGRAM_BOT_TOKEN"] = bot_token
    env["KOBE_CHAT_ID"] = str(despertar.chat_id)
    if despertar.thread_id is not None:
        env["KOBE_THREAD_ID"] = str(despertar.thread_id)
    else:
        env.pop("KOBE_THREAD_ID", None)
    for k, v in despertar.env_extra.items():
        env[k] = v

    cwd = despertar.cwd or str(kobe_home)

    cmd = ["claude", "-p", "--permission-mode", "bypassPermissions"]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,    # source decide se quer log
            stderr=subprocess.DEVNULL,
            cwd=cwd,
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    except FileNotFoundError:
        logger.error("claude CLI não encontrado — não há como despertar")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("falha disparando despertar")
        return None

    try:
        assert proc.stdin is not None
        proc.stdin.write(despertar.prompt.encode("utf-8"))
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        logger.exception("falha escrevendo prompt no stdin do claude despertado")

    logger.info(
        "despertar disparado fonte=%s chave=%s motivo=%s pid=%s",
        despertar.fonte, despertar.chave, despertar.motivo, proc.pid,
    )
    return proc
