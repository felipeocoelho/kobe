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
INTERVALO_AVISO_S = 6 * 3600.0

# O worker escreve aqui. Até 07/10/2026 ia para `/dev/null`, e o `logger.warning`
# de cada rodada que falhava ia junto — o journal do Keyko só mostrava "lote
# devido… disparando", 144 vezes, sem nunca dizer o que acontecia depois.
WORKER_LOG_MAX_BYTES = 5 * 1024 * 1024


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
        self._ultimo_codigo: Optional[int] = None

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
        if self._em_voo is not None:
            codigo = self._em_voo.poll()
            if codigo is None:
                return []
            self._ultimo_codigo = codigo
            self._em_voo = None

        try:
            cx = self._db_factory()
        except Exception as exc:  # noqa: BLE001
            logger.exception("lucien: falha ao abrir a ponte com o banco")
            self._avisar(f"não consigo abrir o banco ({type(exc).__name__})")
            return []

        # A saúde vem ANTES do disparo e em bloco próprio: falhar em olhar a
        # saúde não pode impedir a rodada, e a rodada não pode impedir o aviso.
        self._cuidar_da_saude(cx)

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
            self._marcar_disparo()
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
        log = self._abrir_log_do_worker()
        try:
            self._em_voo = subprocess.Popen(
                [sys.executable, "-m", "bot.lucien.worker", "--uma-rodada"],
                cwd=self._kobe_home,
                env=dict(os.environ),
                stdout=log if log is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if log is not None else subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            if log is not None:
                log.close()  # o filho tem a própria cópia do descritor

    def _abrir_log_do_worker(self):
        """`user-data/lucien/worker.log`, em append, com rotação simples (acima
        do teto vira `.1`). Não abrir o log não impede a rodada."""
        try:
            pasta = os.path.join(self._kobe_home, "user-data", "lucien")
            os.makedirs(pasta, exist_ok=True)
            p = os.path.join(pasta, "worker.log")
            try:
                if os.path.getsize(p) > WORKER_LOG_MAX_BYTES:
                    os.replace(p, p + ".1")
            except FileNotFoundError:
                pass
            return open(p, "ab")
        except Exception:  # noqa: BLE001
            logger.exception("lucien: não abri o worker.log — o worker sai sem log")
            return None

    # ── saúde (Etapa 0, 07/10/2026) ──────────────────────────────────────

    def _marcar_disparo(self) -> None:
        """Lembra QUANDO disparou, em disco: no próximo tick, se nenhuma rodada
        nova apareceu no banco desde então, o worker morreu sem registrar."""
        try:
            from bot.lucien import saude

            estado = saude.ler(self._kobe_home)
            estado["_ultimo_disparo"] = time.time()
            saude.gravar(self._kobe_home, estado)
        except Exception:  # noqa: BLE001
            logger.exception("lucien: não marquei o disparo no estado de saúde")

    def _cuidar_da_saude(self, cx) -> None:
        """Observa, decide pela regra de `saude.py` e, se for o caso, avisa.

        **Nunca levanta.** O que pode dar errado aqui é o aviso, não a rodada.
        """
        try:
            from bot.lucien import aviso, saude

            estado = saude.ler(self._kobe_home)
            obs = saude.observar(cx)

            disparo = estado.pop("_ultimo_disparo", None)
            sem_rodada = int(estado.get("_disparos_sem_rodada") or 0)
            if disparo is not None:
                ultima = saude.ultima_rodada_em(cx)
                # Folga de 1 s, só pra arredondamento: banco e Keyko estão no
                # mesmo host, e a linha da rodada nasce DEPOIS do disparo (o
                # worker ainda precisa subir e importar). Folga maior faria uma
                # rodada ANTERIOR parecer resposta a este disparo.
                if ultima is not None and ultima.timestamp() >= float(disparo) - 1:
                    sem_rodada = 0
                else:
                    sem_rodada += 1
            # NÃO zerar por "a última rodada do banco foi OK": no caso do worker
            # que morre sem registrar, a última linha É OK — e velha. Só uma
            # rodada NOVA depois do disparo prova que o worker está vivo.
            obs.disparos_sem_rodada = sem_rodada
            obs.ultimo_codigo_worker = self._ultimo_codigo

            from datetime import datetime, timezone

            from bot import lucien as cfg

            from bot.lucien import credencial

            agora = datetime.now(timezone.utc)
            novo, msg = saude.decidir(
                estado, obs, agora,
                limiar=cfg.falhas_seguidas_max(),
                lembrete_h=cfg.aviso_lembrete_h(),
                o_que_fazer_login=credencial.o_que_fazer_no_login(self._kobe_home),
            )
            novo["_disparos_sem_rodada"] = sem_rodada
            if msg:
                logger.warning("lucien: %s", msg)
                aviso.avisar(self._kobe_home, msg)

            # O aviso ANTECIPADO de vencimento (opção C), de HORA em hora. Lê só
            # datas — nunca o conteúdo de credencial nenhuma. Os avisos em si
            # saem uma vez por marco (ver `credencial.checar`); a cadência só
            # decide QUANDO o marco é percebido. Era 1x/24h até 07/10/2026, e
            # isso deixava o aviso de "falta 1 dia" sair, no pior caso, minutos
            # antes do vencimento (depende da hora em que o Keyko subiu).
            checada = float(novo.get("_validade_checada_em") or 0)
            if agora.timestamp() - checada >= 3600:
                novo["_validade_checada_em"] = agora.timestamp()
                novo, avisos = credencial.checar(
                    novo, credencial.validades(self._kobe_home), agora)
                for m in avisos:
                    logger.warning("lucien: %s", m)
                    aviso.avisar(self._kobe_home, m)
            if novo != estado or disparo is not None:
                saude.gravar(self._kobe_home, novo)
        except Exception:  # noqa: BLE001
            logger.exception("lucien: a checagem de saúde falhou (a rodada segue)")

    def _avisar(self, erro: str) -> None:
        """Falha da PRÓPRIA fonte (o `tick` não conseguiu nem perguntar).

        Até 07/10/2026 isto só escrevia no log, apesar do nome. Agora vai ao
        operador pelo canal de saúde, no máximo uma vez a cada 6 h — é a mesma
        regra de represamento: repetição soma, não repete.
        """
        agora = time.time()
        if agora - self._ultimo_aviso < INTERVALO_AVISO_S:
            return
        self._ultimo_aviso = agora
        msg = (
            f"não consigo nem verificar se há trabalho — {erro}. O registro de "
            f"estado parou de crescer; a busca por evidência continua funcionando. "
            f"Repito este aviso no máximo a cada 6h enquanto durar."
        )
        logger.warning("lucien: %s", msg)
        try:
            from bot.lucien import aviso

            aviso.avisar(self._kobe_home, msg)
        except Exception:  # noqa: BLE001 — avisar nunca derruba o tick
            logger.exception("lucien: falha ao avisar")


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
