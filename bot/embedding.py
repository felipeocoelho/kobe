"""Wrapper async pro OpenAI text-embedding-3-small.

Usado pelo Chat Manager (vide ~/.claude/plans/claude-sobre-o-chat-noble-dawn.md)
pra calcular vetores semânticos de:
- mensagens novas (compara contra centroide de conversations ativas/dormentes)
- summaries de sessions (classificação retroativa de Fase 5)
- centroides de conversations (média móvel ao longo do tempo)

Custo: ~$0.02 / 1M tokens (~$0.01/mês em uso típico do Kobe). Cobrado
direto da conta OpenAI do operador, fora do plano Max da Anthropic.

Cache LRU em memória pra evitar re-embed do mesmo texto na mesma sessão
de processo — útil quando a mesma mensagem é avaliada por múltiplos
detectores (improvável hoje mas barato de manter).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from functools import lru_cache
from typing import Sequence

from openai import AsyncOpenAI, APIError


logger = logging.getLogger("kobe.embedding")

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536  # bate com VECTOR(1536) no schema

_client: AsyncOpenAI | None = None
_cache: dict[str, list[float]] = {}
_CACHE_MAX = 512


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY não configurada no .env")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


async def embed(text: str, *, retries: int = 2) -> list[float]:
    """Retorna vetor 1536-dim do texto. Empty/whitespace → ValueError.

    Cache em memória por texto exato. Cache evict naïve: quando passa do
    limite, descarta o mais antigo inserido (Python dict mantém ordem
    de inserção desde 3.7).
    """
    if not text or not text.strip():
        raise ValueError("texto vazio não pode ser embedado")

    if text in _cache:
        return _cache[text]

    client = _get_client()
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = await client.embeddings.create(
                model=EMBED_MODEL,
                input=text,
            )
            vec = resp.data[0].embedding
            _cache_put(text, vec)
            return vec
        except APIError as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning(
                "embedding: tentativa %d/%d falhou (%s); aguardando %ds",
                attempt + 1, retries + 1, exc, wait,
            )
            if attempt < retries:
                await asyncio.sleep(wait)
    raise RuntimeError(f"embedding falhou após {retries + 1} tentativas: {last_exc}")


def _cache_put(text: str, vec: list[float]) -> None:
    if len(_cache) >= _CACHE_MAX:
        # Descarta o mais antigo (FIFO via ordem de inserção)
        oldest = next(iter(_cache))
        del _cache[oldest]
    _cache[text] = vec


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Similaridade cosseno entre dois vetores. Range [-1, 1], onde:
    - 1.0  = direção idêntica (mesmo tema)
    - 0.0  = ortogonal (sem relação)
    - -1.0 = oposto

    Pra OpenAI embeddings, valores típicos de "mesmo tema" ficam
    >= 0.5, e "tema diferente" <= 0.2. Calibração de thresholds é parte
    da Fase 3 (detector).
    """
    if len(v1) != len(v2):
        raise ValueError(f"dimensões diferentes: {len(v1)} vs {len(v2)}")
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def update_centroid(
    centroid: Sequence[float] | None,
    new_vector: Sequence[float],
    *,
    weight_new: float = 0.1,
) -> list[float]:
    """Atualiza o centroide de uma conversation incorporando vetor novo.

    Média móvel exponencial: centroide_novo = (1-w) * centroide_atual + w * vetor_novo.
    Peso default 0.1 = centroide "esquece" devagar (90% do velho permanece).

    Quando centroide é None (primeira mensagem da conversation), retorna
    o vetor novo direto. Garante normalização L2 — vetores OpenAI já vêm
    normalizados, mas a média móvel desnormalizaria sem isso.
    """
    if centroid is None:
        return list(new_vector)

    if len(centroid) != len(new_vector):
        raise ValueError(f"dimensões diferentes: {len(centroid)} vs {len(new_vector)}")

    combined = [
        (1 - weight_new) * c + weight_new * n
        for c, n in zip(centroid, new_vector)
    ]
    # Re-normaliza L2
    norm = math.sqrt(sum(x * x for x in combined))
    if norm == 0:
        return combined
    return [x / norm for x in combined]
