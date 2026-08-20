"""Cliente do banco do Kobe — e o ÚNICO arquivo que sabe qual banco é.

A `SUPABASE_KEY` esperada é a chave secreta server-side (`sb_secret_xxx` no
nome novo, ou o legado `service_role` JWT). A publishable/anon key respeita
RLS e não tem permissão pra escrever nas tabelas do Kobe.

──────────────────────────────────────────────────────────────────────────
RESILIÊNCIA DE CONEXÃO (2026-08-20) — camada 1 do "turno não morre calado"
──────────────────────────────────────────────────────────────────────────

A dor, com prova: 3 vezes em 30 dias de produção (14/08 18:51, 14/08 18:52,
19/08 17:08) uma mensagem do operador sumiu em silêncio. O log é sempre o
mesmo caminho — o operador volta depois de um tempo parado, manda algo, o
turno vai buscar o histórico e leva `httpx.RemoteProtocolError: Server
disconnected`. Clássico de conexão ociosa: o pool guarda o socket, o outro
lado derruba, e a PRIMEIRA requisição depois da ociosidade morre.

Três mecanismos, do mais barato pro mais caro:

1. **Reciclagem por ociosidade (prevenção, custo zero).** Se a última conversa
   bem-sucedida com o banco foi há mais de `DB_IDLE_RECYCLE_SECONDS`, o cliente
   é trocado ANTES de tentar. Construir o cliente não faz I/O de rede nenhum —
   é só descartar um pool que provavelmente já está morto do outro lado. Isto
   sozinho ataca a causa raiz das 3 falhas observadas.
2. **Repetição com espera crescente (o remédio).** Erro de TRANSPORTE (conexão
   caiu, tempo esgotado, servidor desconectou) → recicla e tenta de novo, até
   `_MAX_RETRIES` vezes. Erro de NEGÓCIO (dado inválido, permissão, constraint)
   **não** é repetido: repetir não adiantaria e só mascararia bug.
3. **Remontagem da consulta.** Ao trocar de cliente, a consulta já montada
   aponta pro pool velho. O proxy daqui GRAVA a cadeia de chamadas
   (`.table(...).select(...).eq(...)`) e a REMONTA no cliente novo. É isso que
   permite que os ~70 pontos do código que falam com o banco não saibam de nada.

Trade-off declarado (e aprovado pelo operador): repetir uma LEITURA é 100%
seguro. Repetir uma ESCRITA tem um risco teórico — se o servidor gravou mas a
resposta se perdeu, a repetição grava de novo (uma linha duplicada). Na prática
é remotíssimo (a conexão estava morta ANTES do pedido sair — é por isso que o
erro acontece) e a reciclagem por ociosidade praticamente elimina o cenário.
Uma linha duplicada no histórico é muito menos grave que uma mensagem perdida.
`DB_RETRY_WRITES=false` desliga só essa parte.

──────────────────────────────────────────────────────────────────────────
MIGRAÇÃO PRO POSTGRES LOCAL
──────────────────────────────────────────────────────────────────────────

Este arquivo é o **ponto único de isolamento do driver**. Nenhum outro arquivo
do Kobe sabe que o banco é Supabase — o código só usa `.table()`, verificado.
No dia da migração, este arquivo é reescrito pro Postgres local e **nada mais
muda**. A camada que garante que o turno não morre calado
(`bot/turn_guarantee.py`) é deliberadamente agnóstica e não encosta aqui.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

import httpx
from supabase import Client, create_client

from bot.config import Config


logger = logging.getLogger("kobe.db")

# Erros que significam "a conexão morreu", não "o pedido estava errado". Toda a
# família de transporte do httpx: RemoteProtocolError (Server disconnected — o
# caso observado), ReadError, ConnectError, timeouts, PoolTimeout.
TRANSPORT_ERRORS = (httpx.TransportError,)

# Tentativas EXTRAS após a primeira. 2 basta: se duas reconexões seguidas
# falham, o banco está fora de verdade e insistir só atrasa o aviso ao operador
# (que é o trabalho da camada 2, bot/turn_guarantee.py).
_MAX_RETRIES = 2
_BACKOFF_SECONDS = (0.2, 0.8)


def _idle_recycle_seconds() -> float:
    """Ociosidade a partir da qual o cliente é trocado preventivamente.

    2 minutos é conservador: bem abaixo do tempo em que um intermediário
    costuma derrubar socket ocioso, e alto o bastante pra não reciclar durante
    uma conversa normal (onde há tráfego a cada poucos segundos).
    """
    try:
        return float(os.getenv("DB_IDLE_RECYCLE_SECONDS", "120"))
    except ValueError:
        return 120.0


def _resilience_enabled() -> bool:
    return _env_bool("DB_RESILIENCE_ENABLED", True)


def _retry_writes() -> bool:
    return _env_bool("DB_RETRY_WRITES", True)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "on", "yes")


# ── Proxy que grava a cadeia de chamadas e a remonta no cliente novo ──────


class _Recorded:
    """Um passo da cadeia: nome do método + argumentos."""

    __slots__ = ("name", "args", "kwargs")

    def __init__(self, name: str, args: tuple, kwargs: dict) -> None:
        self.name, self.args, self.kwargs = name, args, kwargs


class _QueryProxy:
    """Espelha um builder de consulta, gravando a cadeia pra poder remontá-la.

    Tudo que não é `execute()` é delegado ao builder real e devolvido embrulhado
    num proxy novo (a cadeia cresce). `execute()` é o único ponto que de fato
    faz rede — e é ali que a resiliência entra.
    """

    __slots__ = ("_owner", "_chain", "_builder")

    def __init__(self, owner: "ResilientClient", chain: list[_Recorded], builder: Any) -> None:
        self._owner, self._chain, self._builder = owner, chain, builder

    def __getattr__(self, name: str) -> Any:
        # `execute` NÃO cai aqui — está definido na classe, e `__getattr__` só
        # roda quando a busca normal falha. Todo o resto (select, eq, order,
        # limit, single…) é delegado e re-embrulhado, fazendo a cadeia crescer.
        attr = getattr(self._builder, name)
        if not callable(attr):
            return attr  # propriedade simples do builder

        def _call(*args, **kwargs):
            return _QueryProxy(
                self._owner,
                self._chain + [_Recorded(name, args, kwargs)],
                attr(*args, **kwargs),
            )

        return _call

    def execute(self) -> Any:
        """Executa com reciclagem preventiva + repetição em erro de transporte."""
        return self._owner._run(self._chain)


class ResilientClient:
    """Cliente do banco com reciclagem por ociosidade e retry de transporte.

    Substituto direto do `Client` do supabase-py para o uso que o Kobe faz
    (`.table(...)`). Identidade estável: o objeto é passado adiante por todo o
    código e continua o mesmo — quem troca é o cliente INTERNO.

    Thread-safe: o Kobe chama o banco de dentro de `asyncio.to_thread`, então
    dois turnos podem bater aqui ao mesmo tempo. O lock protege a troca do
    cliente interno (a operação em si roda fora do lock, sem serializar o banco).
    """

    def __init__(self, factory: Callable[[], Client]) -> None:
        self._factory = factory
        self._lock = threading.Lock()
        self._inner: Client = factory()
        self._last_ok = time.monotonic()
        self._generation = 0

    # -- ciclo de vida do cliente interno --------------------------------

    def _recycle(self, expected_generation: int, motivo: str) -> None:
        """Troca o cliente interno. `expected_generation` evita que duas threads
        que falharam na mesma conexão morta reciclem duas vezes seguidas."""
        with self._lock:
            if self._generation != expected_generation:
                return  # outra thread já reciclou esta conexão
            self._generation += 1
            self._inner = self._factory()
            self._last_ok = time.monotonic()
        logger.info("db: cliente reciclado (%s) gen=%d", motivo, self._generation)

    def _client_for_call(self) -> tuple[Client, int]:
        """Devolve o cliente a usar, reciclando antes se estiver ocioso demais.

        Com a resiliência desligada isto é passe-livre TOTAL — nem reciclagem
        preventiva. `DB_RESILIENCE_ENABLED=false` tem que devolver exatamente o
        comportamento legado, senão não é rollback de verdade.
        """
        with self._lock:
            inner, generation = self._inner, self._generation
            idle = time.monotonic() - self._last_ok
        if _resilience_enabled() and idle > _idle_recycle_seconds():
            self._recycle(generation, f"ocioso há {idle:.0f}s")
            with self._lock:
                return self._inner, self._generation
        return inner, generation

    # -- execução --------------------------------------------------------

    @staticmethod
    def _is_write(chain: list[_Recorded]) -> bool:
        return any(
            step.name in ("insert", "update", "upsert", "delete") for step in chain
        )

    def _replay(self, client: Client, chain: list[_Recorded]) -> Any:
        target: Any = client
        for step in chain:
            target = getattr(target, step.name)(*step.args, **step.kwargs)
        return target.execute()

    def _run(self, chain: list[_Recorded]) -> Any:
        if not _resilience_enabled():
            # Rollback trivial: sem reciclagem, sem retry — comportamento legado.
            return self._replay(self._inner, chain)

        may_retry = _retry_writes() or not self._is_write(chain)
        client, generation = self._client_for_call()

        for attempt in range(_MAX_RETRIES + 1):
            try:
                result = self._replay(client, chain)
            except TRANSPORT_ERRORS as exc:
                last_step = chain[-1].name if chain else "?"
                if attempt >= _MAX_RETRIES or not may_retry:
                    logger.warning(
                        "db: erro de transporte em %s (tentativa %d) — desistindo: %s",
                        last_step, attempt + 1, exc,
                    )
                    raise
                logger.warning(
                    "db: erro de transporte em %s (tentativa %d/%d) — reciclando: %s",
                    last_step, attempt + 1, _MAX_RETRIES + 1, exc,
                )
                self._recycle(generation, "erro de transporte")
                time.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                with self._lock:
                    client, generation = self._inner, self._generation
            else:
                with self._lock:
                    self._last_ok = time.monotonic()
                return result

        raise AssertionError("inalcançável")  # pragma: no cover

    # -- superfície usada pelo Kobe --------------------------------------

    def table(self, *args, **kwargs) -> _QueryProxy:
        client, _ = self._client_for_call()
        return _QueryProxy(
            self, [_Recorded("table", args, kwargs)], client.table(*args, **kwargs)
        )

    def rpc(self, *args, **kwargs) -> _QueryProxy:
        client, _ = self._client_for_call()
        return _QueryProxy(
            self, [_Recorded("rpc", args, kwargs)], client.rpc(*args, **kwargs)
        )

    def __getattr__(self, name: str) -> Any:
        """Qualquer outra coisa (auth, storage…) vai direto ao cliente real,
        sem resiliência. O Kobe não usa nada disso hoje — mas se um dia usar,
        funciona, só não ganha retry."""
        return getattr(self._inner, name)


def build_client(config: Config) -> Client:
    """Cliente do banco pronto pra uso. Com `DB_RESILIENCE_ENABLED=false` ainda
    devolve o wrapper, mas ele vira passe-livre (nem recicla, nem repete)."""
    url, key = config.supabase_url, config.supabase_key
    return ResilientClient(lambda: create_client(url, key))  # type: ignore[return-value]


def build_raw_client(config: Config) -> Client:
    """Cliente cru, sem wrapper. Só pra teste/diagnóstico."""
    return create_client(config.supabase_url, config.supabase_key)
