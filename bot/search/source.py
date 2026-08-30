"""A fonte do Keyko que mantém o índice de busca em dia.

POR QUE ELA NUNCA ACORDA NINGUÉM
---------------------------------
Mesmo desenho do coletor da F1 (`bot/transcripts/source.py`), e pela mesma
razão: o protocolo do Keyko prevê que uma fonte faça trabalho colateral e
devolva **lista vazia** de despertares. Quebrar texto em pedaço e pedir vetor a
uma API não precisa de um modelo, e acordar um `claude -p` pra isso gastaria o
recurso escasso da campanha — cota de assinatura — na tarefa mais burra do
sistema. **Custo de cota: zero.**

POR QUE A CADÊNCIA É DE UM MINUTO
----------------------------------
É o atraso máximo entre uma mensagem ser gravada e ela ficar buscável **por
sentido** (por palavra ela já é buscável no INSERT, porque a coluna é gerada).
Um minuto é o que faz "eu perguntei sobre o que a gente acabou de falar" ainda
funcionar, sem transformar o daemon num laço apertado.

O primeiro tick do Keyko é imediato, então reiniciar o daemon **causa** uma
passada em vez de adiá-la — a mesma propriedade que a F1 usou como remédio
contra falha de relógio.

O QUE ACONTECE QUANDO O SERVIÇO DE EMBEDDING CAI
-------------------------------------------------
O índice **para de crescer** e o erro vai pro log. Não há gravação parcial nem
vetor torto: `embeddar_pendentes` levanta antes de escrever qualquer coisa. E o
`kobe-remember` enxerga a fila parada por `indexer.pendencia()` e **avisa o
operador** de que a busca por sentido está atrasada — em vez de devolver menos
resultado e deixar isso passar por ausência de registro.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from bot.keyko.models import Despertar
from bot.search import indexer

logger = logging.getLogger("kobe.search.source")

INTERVALO_PADRAO_S = 60.0

# Piso: um valor torto no `.env` não pode transformar o indexador num laço que
# consulta o banco milhares de vezes por minuto.
INTERVALO_MINIMO_S = 10.0

# Não repetir o mesmo aviso de instrumento fora a cada tick. Um alerta por
# minuto vira ruído, e ruído é ignorado — mesmo destino do silêncio.
INTERVALO_AVISO_S = 3600.0


def _env(nome: str) -> str:
    return (os.environ.get(nome) or "").strip()


def _intervalo() -> float:
    bruto = _env("SEARCH_INDEX_INTERVAL_S")
    try:
        valor = float(bruto) if bruto else INTERVALO_PADRAO_S
    except ValueError:
        valor = INTERVALO_PADRAO_S
    return max(INTERVALO_MINIMO_S, valor)


class SearchIndexSource:
    """Mantém `message_chunks` em dia. Nunca devolve despertar."""

    def __init__(self, *, db_factory) -> None:
        # Recebe uma FÁBRICA, não a ponte pronta: o Keyko sobe antes de qualquer
        # turno e pode ficar horas ocioso, e uma conexão aberta desde a
        # inicialização é exatamente o socket morto que já fez mensagem do
        # operador sumir três vezes em 30 dias. A ponte é criada no 1º tick e
        # daí em diante tem a resiliência do `bot/db.py`.
        self._db_factory = db_factory
        self._db = None
        self._ultimo_aviso = 0.0

    @property
    def nome(self) -> str:
        return "search-index"

    @property
    def intervalo_s(self) -> float:
        return _intervalo()

    def _ponte(self):
        if self._db is None:
            self._db = self._db_factory()
        return self._db

    def tick(self) -> list[Despertar]:
        """Uma passada de indexação. **Nunca levanta** — o Keyko é single-threaded."""
        if not indexer.indexer_enabled():
            return []
        try:
            r = indexer.tick(self._ponte())
        except Exception:  # noqa: BLE001 — nem a criação da ponte pode derrubar
            logger.exception("search-index: falha ao abrir a ponte com o banco")
            self._db = None
            return []

        if r.fez_algo:
            logger.info(
                "search-index: %d mensagem(ns) quebrada(s), %d trecho(s) criado(s), "
                "%d embeddado(s)%s",
                r.mensagens_quebradas,
                r.trechos_criados,
                r.trechos_embeddados,
                ", estatística de radicais atualizada" if r.df_atualizado else "",
            )
        if r.erro:
            self._avisar(r.erro)
        return []

    def _avisar(self, erro: str) -> None:
        import time

        agora = time.time()
        if agora - self._ultimo_aviso < INTERVALO_AVISO_S:
            return
        self._ultimo_aviso = agora
        logger.warning(
            "search-index: o índice PAROU de crescer — %s. A busca por palavra "
            "continua funcionando; a busca por sentido fica atrasada até isto "
            "ser resolvido.",
            erro,
        )


def build(*, kobe_home=None, bot_token: str = "") -> Optional[SearchIndexSource]:
    """A fonte, ou `None` se o indexador está desligado.

    Devolver `None` com a chave off mantém o registro do Keyko honesto: uma
    fonte registrada que não faz nada aparece no log de inicialização como se
    estivesse trabalhando, e *"quem o Keyko está observando"* deixa de ser
    verdade.

    `kobe_home` e `bot_token` entram na assinatura por uniformidade com as
    outras fontes (o registry chama todas do mesmo jeito); esta não usa nenhum
    dos dois — ela fala só com o banco.
    """
    if not indexer.indexer_enabled():
        return None

    def _abrir():
        from bot.config import load_config
        from bot.db import build_client

        return build_client(load_config())

    return SearchIndexSource(db_factory=_abrir)
