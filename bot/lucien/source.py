"""A fonte do Keyko que mantém o registro de estado em dia.

POR QUE ELA NUNCA ACORDA NINGUÉM — E POR QUE ELA TAMBÉM NÃO TRABALHA NO `tick()`
---------------------------------------------------------------------------------
São duas decisões diferentes, e cada uma tem uma razão própria.

**Nunca devolve `Despertar`.** O despertar do Keyko acorda um `claude -p` que
faria o trabalho *e escreveria o resultado* — o modelo com a caneta na mão, que é
exatamente o que a F3 inteira existe para evitar. LUCIEN inverte isso: o
processo é código, o código chama o modelo, e o código valida antes de gravar.

**Também não faz o trabalho dentro do `tick()`**, diferente do coletor de
transcripts e do indexador de busca. Aqueles copiam bytes e pedem vetor —
milissegundos. Uma chamada de modelo leva dezenas de segundos, e o Keyko é
single-threaded: LUCIEN travando o laço travaria os **Alertas**, onde atraso é
falha que o operador vê.

Então o `tick()` faz só a pergunta barata — *"há lote devido?"* — e, havendo,
dispara o worker **detached**. A pergunta é um `SELECT` agrupado; a resposta é um
processo que vive sozinho.

POR QUE A CADÊNCIA É DE CINCO MINUTOS
--------------------------------------
Não é a cadência das rodadas: é de quanto em quanto tempo se PERGUNTA. As rodadas
acontecem quando um lote fica devido (por acúmulo ou por idade), e têm teto por
hora. Cinco minutos é curto o bastante para que uma decisão dita agora entre no
registro em minutos, e longo o bastante para o `SELECT` não pesar.

O primeiro tick do Keyko é imediato, então reiniciar o daemon **causa** uma
passada em vez de adiá-la — a mesma propriedade que a F1 usou como remédio
contra falha de relógio.

QUANDO O MODELO ESTÁ FORA
--------------------------
O registro **para de crescer** e o erro vai para a linha da rodada
(`lucien_runs.error`) e para o log. Não há gravação parcial: o cursor não avança,
e o mesmo lote é lido na próxima passada. `kobe-lucien status` mostra a idade da
última rodada bem-sucedida — porque um agendador que para não produz erro,
produz **silêncio**, indistinguível de "não havia nada novo".
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import Optional

from bot import lucien as cfg
from bot.keyko.models import Despertar

logger = logging.getLogger("kobe.lucien.source")

# Não repetir o mesmo aviso a cada tick. Um alerta a cada cinco minutos vira
# ruído, e ruído é ignorado — mesmo destino do silêncio.
INTERVALO_AVISO_S = 3600.0


class LucienSource:
    """Pergunta se há lote devido e dispara o worker. Nunca devolve despertar."""

    def __init__(self, *, kobe_home, db_factory) -> None:
        # Recebe uma FÁBRICA, não a conexão pronta: o Keyko sobe antes de
        # qualquer turno e pode ficar horas ocioso, e uma conexão aberta desde a
        # inicialização é exatamente o socket morto que já fez mensagem do
        # operador sumir três vezes em 30 dias.
        self._kobe_home = str(kobe_home)
        self._db_factory = db_factory
        self._ultimo_aviso = 0.0
        self._em_voo: Optional[subprocess.Popen] = None

    @property
    def nome(self) -> str:
        return "lucien"

    @property
    def intervalo_s(self) -> float:
        return cfg.intervalo_s()

    def tick(self) -> list[Despertar]:
        """**Nunca levanta** — o Keyko é single-threaded e uma fonte bugada não
        pode derrubar os Alertas."""
        if not cfg.habilitado():
            return []

        # Um worker de cada vez por este caminho. O cadeado consultivo do banco
        # já protegeria a escrita, mas disparar processos que morrem no cadeado
        # gasta cota de sistema à toa.
        if self._em_voo is not None and self._em_voo.poll() is None:
            return []

        try:
            cx = self._db_factory()
        except Exception:  # noqa: BLE001
            logger.exception("lucien: falha ao abrir a ponte com o banco")
            return []

        try:
            from bot.lucien import store, worker

            if not worker.cota_disponivel(cx):
                return []
            devido = worker.escolher_topico(cx)
            if devido is None:
                return []
            logger.info(
                "lucien: lote devido em %s (%s mensagens pendentes) — disparando",
                devido["topico"], devido["pendentes"],
            )
            self._disparar()
        except Exception as exc:  # noqa: BLE001
            self._avisar(str(exc))
        finally:
            try:
                cx.close()
            except Exception:  # noqa: BLE001
                pass
        return []

    def _disparar(self) -> None:
        """O worker, detached. `start_new_session` para ele sobreviver a um
        restart do Keyko — uma rodada interrompida no meio não grava nada, mas
        gasta a chamada de modelo que já foi feita."""
        self._em_voo = subprocess.Popen(
            [sys.executable, "-m", "bot.lucien.worker", "--uma-rodada"],
            cwd=self._kobe_home,
            env=dict(os.environ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

    def _avisar(self, erro: str) -> None:
        agora = time.time()
        if agora - self._ultimo_aviso < INTERVALO_AVISO_S:
            return
        self._ultimo_aviso = agora
        logger.warning(
            "lucien: o registro de estado PAROU de crescer — %s. A busca por "
            "evidência continua funcionando; o que fica desatualizado é a camada "
            "de ESTADO do kobe-remember.",
            erro,
        )


def build(*, kobe_home=None, bot_token: str = "") -> Optional[LucienSource]:
    """A fonte, ou `None` com a chave desligada.

    Devolver `None` mantém o registro do Keyko honesto: uma fonte registrada que
    não faz nada aparece no log de inicialização como se estivesse trabalhando, e
    *"quem o Keyko está observando"* deixa de ser verdade.

    `bot_token` entra na assinatura por uniformidade (o registry chama todas as
    fontes do mesmo jeito); esta não usa — LUCIEN não fala com o operador. O
    relatório semanal é F5, e é do agente, não dele.
    """
    if not cfg.habilitado():
        return None

    from pathlib import Path

    home = Path(kobe_home) if kobe_home else Path(__file__).resolve().parents[2]

    def _abrir():
        from bot.lucien import store

        url = (os.environ.get("DATABASE_URL") or "").strip()
        if not url:
            raise RuntimeError("DATABASE_URL ausente")
        return store.conectar(url)

    return LucienSource(kobe_home=home, db_factory=_abrir)
