"""Fábrica singleton do cliente OpenAI — endereço neutro, sem dono.

Isto morava dentro de `bot/conversation_detector.py` (o detector de conversa
do Chat Manager) por acidente de história: o judge do detector foi o primeiro
consumidor de OpenAI do Kobe, então o client nasceu lá. Com o tempo, DUAS
funções que não têm nada a ver com Chat Manager passaram a importar de lá, em
import tardio dentro da função:

- `bot/liveness.py` — o ack semântico da borda ("entendi, vou X, já te
  retorno"), escrito por um modelo barato quando o provedor é `openai`.
- `bot/turn_classifier.py` — o desempate de zona cinza (PESADO vs LEVE) que
  decide se o turno vai pra background.

Nenhuma das duas é do Chat Manager; as duas ficam vivas depois que ele morre.
Enquanto o client morasse no detector, apagar o detector quebrava o ack em
runtime (`ImportError` sobe) e degradava o classificador **em silêncio** (a
chamada dele está dentro de um `except` que devolve `None`). Nada disso
apareceria na suíte — os dois imports são tardios e um é engolido.

Por isso o client mudou de endereço ANTES da remoção, e este módulo não
depende de nada do Kobe: só do `OPENAI_API_KEY` no ambiente.
"""

from __future__ import annotations

import os
from typing import Optional

from openai import AsyncOpenAI


# Modelo barato usado pelos consumidores de julgamento binário (~$0.15/1M
# tokens de entrada, ~500ms-1s por chamada). Fora da cota do plano Max.
JUDGE_MODEL = "gpt-4o-mini"


_openai_client: Optional[AsyncOpenAI] = None


def _get_openai() -> AsyncOpenAI:
    """Client `AsyncOpenAI` singleton. Levanta se a chave não está no ambiente.

    Singleton de propósito: o client mantém pool de conexões, e recriá-lo a
    cada chamada joga fora o keep-alive — que é justamente o que segura a
    latência do ack da borda dentro do orçamento.
    """
    global _openai_client
    if _openai_client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY não configurada — judge não pode rodar")
        _openai_client = AsyncOpenAI(api_key=key)
    return _openai_client
