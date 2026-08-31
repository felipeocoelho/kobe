"""A única peça do Kobe que fala com o modelo de embedding.

POR QUE ELA É UMA SÓ
--------------------
O indexador e o comando de busca **precisam** usar o mesmo modelo: vetor de um
modelo comparado com vetor de outro não erra por pouco, erra por completo, e
erra em silêncio — a consulta simplesmente devolve o vizinho errado com nota
plausível. Concentrar aqui é o que torna "o mesmo modelo dos dois lados" uma
propriedade do código, e não uma coisa pra lembrar.

O MODELO, E POR QUE ELE
------------------------
`text-embedding-3-small` (1536d), decisão do operador em 30/08/2026 sobre número
medido. O modelo local `multilingual-e5-small` foi indexado no mesmo acervo e
comparado nas mesmas 16 perguntas:

    similaridade do 1º resultado    com resposta      sem resposta      folga
    text-embedding-3-small          0,598 – 0,693     0,428 – 0,536     +0,061
    multilingual-e5-small           0,865 – 0,903     0,833 – 0,890     −0,025

Com o modelo local, *"o que a gente decidiu sobre integração com o Salesforce?"*
pontua **0,874** e a pergunta legítima sobre a arquitetura de borda pontua
**0,880**. Seis milésimos separam "não existe" de "existe" — não há piso
possível, e sem piso não há como dizer "não tenho registro" em vez de inventar.

A porta de saída está aberta e é barata: trocar de modelo custa reindexar o que
já foi indexado (42 s para o acervo inteiro, medido). Diferente do bank do
Hindsight, onde trocar embedding é migração.

A ARMADILHA QUE ESTA CLASSE EXISTE PRA NÃO REPETIR
---------------------------------------------------
Duas vezes neste sistema uma falha de instrumento virou, na boca do agente,
*"não há registro disso"*:

- **F0.5-B**: a chave de embedding do Hindsight caiu na chave do LLM ao trocar de
  provider, os embeddings passaram a tomar 401, e o `reflect` respondeu "não há
  registro" — latência ótima, resposta oca.
- **`kobe-reflect`, 29/08**: o cliente desistia aos 20 s de um servidor que
  respondia bem aos 28 s, e o timeout era indistinguível de acervo vazio.

Por isso **toda falha aqui vira `EmbeddingIndisponivel`**, uma exceção com nome
próprio, que o chamador é obrigado a distinguir de "procurei e não achei". Nunca
devolvemos lista vazia por erro: lista vazia significa "não havia nada pra
embeddar", e só isso.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence

MODEL = "text-embedding-3-small"

# Tem que casar com `VECTOR(1536)` da migration 007. Divergência aqui não é erro
# de digitação: é vetor entrando torto num índice que não reclama.
DIM = 1536

# 256 por chamada: medido, o acervo inteiro (7.706 trechos) foi embeddado em
# 42 s com esse lote. Lote maior não acelerou; menor multiplicou o custo fixo
# de rede por nada.
LOTE = 256

TIMEOUT_PADRAO = 60.0


class EmbeddingIndisponivel(RuntimeError):
    """Não deu pra saber — e isso NÃO é "não há registro".

    Quem captura tem a obrigação de dizer ao operador que a consulta não chegou
    a ser respondida. Relatar isto como ausência é o falso negativo silencioso
    que este sistema já cometeu duas vezes.
    """


def modelo() -> str:
    """Permite fixar outro modelo por ambiente, mas o default é o decidido.

    Existe para o dia da troca (e para teste), não para configuração de rotina:
    trocar sem reindexar mistura dois espaços vetoriais no mesmo índice.
    """
    return os.environ.get("SEARCH_EMBED_MODEL") or MODEL


def _timeout() -> float:
    bruto = os.environ.get("SEARCH_EMBED_TIMEOUT", "")
    try:
        return float(bruto) if bruto else TIMEOUT_PADRAO
    except ValueError:
        return TIMEOUT_PADRAO


def _client():
    """Cliente síncrono próprio.

    `bot/openai_client.py` é `AsyncOpenAI` e serve os caminhos assíncronos do
    bot. Aqui os dois consumidores são síncronos — o tick do Keyko e um comando
    de linha —, e embrulhar um loop de eventos só pra atravessar um `await`
    custaria mais linhas do que este bloco inteiro.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover — dependência declarada
        raise EmbeddingIndisponivel(f"biblioteca openai indisponível: {exc}") from exc

    chave = os.environ.get("OPENAI_API_KEY")
    if not chave:
        raise EmbeddingIndisponivel(
            "OPENAI_API_KEY não configurada — a busca por sentido não pode rodar"
        )
    return OpenAI(api_key=chave, timeout=_timeout())


def _conferir(vetores: list[list[float]]) -> list[list[float]]:
    for v in vetores:
        if len(v) != DIM:
            raise EmbeddingIndisponivel(
                f"o modelo devolveu vetor de {len(v)} dimensões e a coluna é "
                f"VECTOR({DIM}) — modelo trocado sem reindexar?"
            )
    return vetores


def embed(textos: Sequence[str], *, cliente=None) -> list[list[float]]:
    """Vetores para uma lista de textos, na mesma ordem.

    Lista vazia na entrada devolve lista vazia — e é o ÚNICO caminho que
    devolve vazio. Qualquer falha levanta `EmbeddingIndisponivel`.
    """
    itens = list(textos)
    if not itens:
        return []
    cli = cliente or _client()
    saida: list[list[float]] = []
    for i in range(0, len(itens), LOTE):
        pedaco = itens[i : i + LOTE]
        try:
            r = cli.embeddings.create(model=modelo(), input=pedaco)
        except Exception as exc:  # noqa: BLE001 — a origem varia; o efeito não
            raise EmbeddingIndisponivel(
                f"o serviço de embedding não respondeu: {type(exc).__name__}: {exc}"
            ) from exc
        saida.extend(d.embedding for d in r.data)
    if len(saida) != len(itens):  # pragma: no cover — contrato do provedor
        raise EmbeddingIndisponivel(
            f"o serviço devolveu {len(saida)} vetores para {len(itens)} textos"
        )
    return _conferir(saida)


# Memória de processo para o vetor da PERGUNTA. Pequena de propósito: o que ela
# existe para evitar é a mesma pergunta ser embeddada duas vezes no mesmo
# comando, não guardar acervo.
#
# Por que ela apareceu: a F3 acrescentou uma segunda busca por sentido — a do
# ESTADO — sobre a MESMA pergunta que a busca de evidência já tinha embeddado.
# Medido no `kobe-remember`: a ida e volta extra à API custava ~0,3 s por
# comando, e o resultado era bit a bit idêntico. É a mesma função pura chamada
# duas vezes.
#
# A chave inclui o MODELO porque ele sai do ambiente e pode mudar entre
# processos; dentro de um processo, dois vetores de modelos diferentes no mesmo
# índice seriam o defeito silencioso que a 007 documenta.
_CACHE_PERGUNTA: dict[tuple, list[float]] = {}
_CACHE_MAX = 32


def embed_um(texto: str, *, cliente=None) -> list[float]:
    """O vetor de uma pergunta. Levanta se não der pra saber.

    Memoizado por processo — ver `_CACHE_PERGUNTA`. Continua levantando
    `EmbeddingIndisponivel` normalmente: **falha não é cacheada**, senão um
    tropeço de rede no primeiro uso condenaria o comando inteiro.
    """
    chave = (texto, modelo())
    if chave in _CACHE_PERGUNTA:
        return _CACHE_PERGUNTA[chave]
    v = embed([texto], cliente=cliente)
    if not v:
        raise EmbeddingIndisponivel("texto vazio não tem vetor")
    if len(_CACHE_PERGUNTA) >= _CACHE_MAX:
        _CACHE_PERGUNTA.clear()
    _CACHE_PERGUNTA[chave] = v[0]
    return v[0]


def para_sql(vetor: Sequence[float]) -> str:
    """O literal que o `pgvector` aceita.

    Seis casas: o vetor vem normalizado em torno de ±0,1 e a diferença de
    similaridade que decide o piso é da terceira casa. Formatar com menos
    economizaria bytes e comeria o sinal.
    """
    return "[" + ",".join(f"{x:.6f}" for x in vetor) + "]"


def disponivel() -> Optional[str]:
    """`None` se dá pra embeddar; o motivo, em texto, se não dá.

    Serve pro comando dizer ao operador *por que* a perna de sentido está fora,
    em vez de simplesmente devolver menos resultado e deixar parecer ausência.
    """
    try:
        _client()
    except EmbeddingIndisponivel as exc:
        return str(exc)
    return None
