"""Ponte pro Postgres — e o ÚNICO arquivo do Kobe que sabe qual banco é.

A conexão vem de `DATABASE_URL`, uma linha do `.env`. Trocar host, porta, banco
ou usuário é mudança de configuração e reinício — **nunca** de código. É isso
que faz "onde o banco mora" ser uma decisão de operação, e não de engenharia.

──────────────────────────────────────────────────────────────────────────
POR QUE DIRETO, SEM ADAPTADOR
──────────────────────────────────────────────────────────────────────────

Decisão do operador, 2026-07-25: *"não faz sentido colocar um código na frente
do outro código se eu posso ir direto pra ele."* Não há DAL, não há backend
plugável, não há emulação do construtor de consultas do PostgREST. Os pontos do
Kobe que falam com o banco mandam **SQL**.

A troca também apagou o pedaço mais delicado deste arquivo. Na versão anterior
havia um proxy de ~60 linhas que **gravava a cadeia de chamadas**
(a cadeia `table` -> `select` -> `eq`) só para poder remontá-la num cliente novo
depois de uma reconexão — porque a consulta já montada apontava para o pool
morto. Com SQL, "a cadeia" é um par `(sql, params)`. Ir direto deixou a ponte
menor que o invólucro que ela substituiu.

──────────────────────────────────────────────────────────────────────────
O CONTRATO DE TIPOS — e por que ele não é detalhe
──────────────────────────────────────────────────────────────────────────

O PostgREST devolvia JSON: `uuid` chegava como string e `timestamptz` como
texto ISO. O psycopg devolve os tipos nativos do Python — `UUID` e `datetime`.
Isso quebraria o Kobe em silêncio e em lugares distantes daqui:

- `bot/memory/working_set.py` compara `created_at` **como string** contra um
  corte também string. `datetime >= str` levanta `TypeError`, e a janela
  imediata de memória morre inteira.
- `bot/memory/aging.py`, `bot/claude_runner.py`, `bot/resume.py` e
  `bot/telegram_handler.py` tratam esses carimbos como texto ISO.
- Ids de `uuid` são carregados adiante como chave de dicionário e interpolados
  em texto.

Então a ponte **normaliza dois tipos, e só dois**: `UUID` vira `str`,
`datetime` vira `.isoformat()`. É contrato de fronteira, não conversão
decorativa — e está preso por teste.

`text[]` (o `saved_artifacts.tags`) já chega como lista nativa; `jsonb` já
chega como dicionário. Nada a fazer nos dois.

**O fuso é fixado na conexão, de propósito.** `timestamptz` guarda um instante
absoluto, mas o TEXTO que o driver devolve sai no fuso da sessão. O `initdb` do
Ubuntu deixa o cluster no fuso local da máquina, e todo banco criado nele nasce
herdando esse fuso — o mesmo instante sairia como `...T00:46:25-03:00` em vez
de `...T03:46:25+00:00`. Fixar `TimeZone=UTC` aqui torna a ponte imune ao que
estiver configurado no cluster ou no banco. `infra/compat_gate.py` vigia o
outro lado.

──────────────────────────────────────────────────────────────────────────
RESILIÊNCIA
──────────────────────────────────────────────────────────────────────────

A dor original: 3 vezes em 30 dias de produção uma mensagem do operador sumiu
em silêncio. Sempre o mesmo caminho — o operador volta depois de um tempo
parado, o pool guarda um socket que o outro lado já derrubou, e a PRIMEIRA
requisição depois da ociosidade morre.

**Nota honesta: com socket unix local, essa dor praticamente desaparece** — não
há intermediário para derrubar conexão ociosa. A camada fica por rigor, e
porque a decisão de onde o banco mora pode colocá-lo em outra máquina amanhã.

O *contrato* de configuração é o mesmo de antes, com a mesma semântica que o
operador aprovou:

- `DB_RESILIENCE_ENABLED` (padrão on) — desligado, uma tentativa e ponto.
- `DB_RETRY_WRITES` (padrão on) — repetir LEITURA é seguro sempre; repetir
  ESCRITA tem risco teórico (se o servidor gravou e a resposta se perdeu, a
  repetição grava de novo). Na prática é remotíssimo, porque a conexão estava
  morta ANTES do pedido sair. Uma linha duplicada é muito menos grave que uma
  mensagem perdida — mas quem quiser o outro lado do trade-off desliga aqui.
- `DB_IDLE_RECYCLE_SECONDS` (padrão 120) — vira o `max_idle` do pool: conexão
  parada mais que isso é descartada em vez de reaproveitada.

O que mudou foi só o *mecanismo*: onde havia remontagem de cadeia à mão, agora
há um pool testado (`psycopg_pool`) e um par `(sql, params)` que se reexecuta
sozinho. Erro de TRANSPORTE (`OperationalError` — conexão caiu, tempo esgotado)
é repetido; erro de NEGÓCIO (dado inválido, permissão, constraint) **não** é —
repetir não adiantaria e só mascararia bug.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Optional, Sequence
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from bot.config import Config


logger = logging.getLogger("kobe.db")

# Erros que significam "a conexão morreu", não "o pedido estava errado".
# `OperationalError` é a família de transporte do psycopg; `PoolTimeout` é o
# pool não ter conseguido entregar conexão a tempo.
TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (psycopg.OperationalError,)

try:  # psycopg_pool >= 3.1
    from psycopg_pool import PoolTimeout

    TRANSPORT_ERRORS = TRANSPORT_ERRORS + (PoolTimeout,)
except ImportError:  # pragma: no cover — versão antiga do pool
    pass

# Tentativas EXTRAS após a primeira. 2 basta: se duas reconexões seguidas
# falham, o banco está fora de verdade e insistir só atrasa o aviso ao operador
# (que é o trabalho de bot/turn_guarantee.py).
_MAX_RETRIES = 2
_BACKOFF_SECONDS = (0.2, 0.8)


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "on", "yes")


def _resilience_enabled() -> bool:
    return _env_bool("DB_RESILIENCE_ENABLED", True)


def _retry_writes() -> bool:
    return _env_bool("DB_RETRY_WRITES", True)


def _idle_recycle_seconds() -> float:
    """Ociosidade a partir da qual a conexão é descartada em vez de reusada.

    2 minutos é conservador: bem abaixo do tempo em que um intermediário
    costuma derrubar socket ocioso, e alto o bastante pra não reciclar durante
    uma conversa normal (onde há tráfego a cada poucos segundos).
    """
    try:
        return float(os.getenv("DB_IDLE_RECYCLE_SECONDS", "120"))
    except ValueError:
        return 120.0


def _pool_max_size() -> int:
    """Teto de conexões simultâneas.

    O Kobe fala com o banco de dentro de `asyncio.to_thread`, então dois turnos
    podem bater aqui ao mesmo tempo. 8 é folgado para o tráfego de um operador
    e educado com o `max_connections` do servidor.
    """
    try:
        return max(1, int(os.getenv("DB_POOL_MAX_SIZE", "8")))
    except ValueError:
        return 8


# ── Normalização da fronteira ─────────────────────────────────────────────


def _normalize(value: Any) -> Any:
    """`UUID` vira texto e `datetime` vira ISO 8601 — e mais nada.

    A lista é curta de propósito. Cada conversão a mais é uma forma de o
    contrato desta fronteira divergir do que o resto do Kobe espera, sem que
    nada acuse.
    """
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _normalize_row(row: dict) -> dict:
    return {k: _normalize(v) for k, v in row.items()}


class KobeDB:
    """A ponte. Quatro verbos, `(sql, params)` em todos.

    - `query`  — várias linhas
    - `one`    — a primeira linha, ou `None`
    - `scalar` — o primeiro valor da primeira linha, ou `None`
    - `execute`— escrita; devolve as linhas do `RETURNING`, ou lista vazia

    A separação entre os três primeiros e o quarto **não é estética**: é ela
    que diz se a operação pode ser repetida sem pensar (leitura) ou se precisa
    obedecer ao `DB_RETRY_WRITES` (escrita). Na ponte anterior isso era
    adivinhado farejando a cadeia de chamadas; agora é o verbo que a pessoa
    escolheu.
    """

    def __init__(self, conninfo: str, *, open_now: bool = True) -> None:
        self._conninfo = conninfo
        self._lock = threading.Lock()
        self._pool = ConnectionPool(
            conninfo=conninfo,
            min_size=1,
            max_size=_pool_max_size(),
            max_idle=_idle_recycle_seconds(),
            kwargs={
                "row_factory": dict_row,
                "autocommit": True,
                # Fuso fixado na sessão: a ponte não pode depender do que o
                # cluster ou o banco tenham configurado. Vide o cabeçalho.
                "options": "-c TimeZone=UTC",
            },
            open=open_now,
            name="kobe",
        )

    # -- execução --------------------------------------------------------

    def _run(self, sql: str, params: Sequence[Any], *, write: bool) -> list[dict]:
        if not _resilience_enabled():
            # Rollback trivial: uma tentativa, sem repetição — o comportamento
            # de quem não quer a camada.
            return self._attempt(sql, params)

        may_retry = _retry_writes() or not write

        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._attempt(sql, params)
            except TRANSPORT_ERRORS as exc:
                if attempt >= _MAX_RETRIES or not may_retry:
                    logger.warning(
                        "db: erro de transporte (tentativa %d) — desistindo: %s",
                        attempt + 1, exc,
                    )
                    raise
                logger.warning(
                    "db: erro de transporte (tentativa %d/%d) — reconectando: %s",
                    attempt + 1, _MAX_RETRIES + 1, exc,
                )
                time.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])

        raise AssertionError("inalcançável")  # pragma: no cover

    def _attempt(self, sql: str, params: Sequence[Any]) -> list[dict]:
        """Uma tentativa. Conexão quebrada é descartada pelo pool na devolução,
        então a tentativa seguinte já sai com uma nova."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params) if params else None)
                if cur.description is None:
                    return []  # comando sem resultado (UPDATE/DELETE sem RETURNING)
                return [_normalize_row(row) for row in cur.fetchall()]

    # -- os quatro verbos ------------------------------------------------

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        return self._run(sql, params, write=False)

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        rows = self._run(sql, params, write=False)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        return self._run(sql, params, write=True)

    # -- ciclo de vida ---------------------------------------------------

    def close(self) -> None:
        """Fecha o pool. O bot não chama em operação normal — o processo morre
        e o sistema operacional recolhe. Existe para teste e para scripts."""
        with self._lock:
            self._pool.close()

    def __repr__(self) -> str:  # pragma: no cover — diagnóstico
        # NUNCA imprime a conninfo: ela pode carregar senha.
        return f"<KobeDB pool={self._pool.name!r}>"


def build_client(config: Config) -> KobeDB:
    """A ponte pronta pra uso, a partir da configuração carregada."""
    return KobeDB(config.database_url)
