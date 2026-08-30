"""Quebra uma mensagem em trechos indexáveis.

POR QUE ISTO EXISTE (e não é refinamento)
------------------------------------------
Medido no acervo real: **30% das mensagens passam de 1.500 caracteres** e o p99 é
**6.322**. Todo modelo de embedding tem um teto de entrada e, passando dele,
**descarta o resto em silêncio** — a metade de baixo de 1 em cada 3 mensagens
ficaria fora do índice sem ninguém perceber. Não é degradação visível; é buraco
mudo, que é a categoria de falha que este projeto inteiro existe pra evitar.

A citação continua sendo da **mensagem** (com data e `seq`); o trecho é só o
pedaço que se mostra e o que carrega o vetor.

AS TRÊS REGRAS DO CORTE
-----------------------
1. **Mensagem curta é um trecho só.** A esmagadora maioria (a mediana é 676
   caracteres) nem passa por aqui.
2. **Corta em parágrafo quando dá.** Uma janela cega no meio de uma frase
   embaralha o sentido justamente da parte que o vetor vai representar.
3. **Quando não dá, corta com sobreposição.** Um parágrafo único maior que a
   janela é fatiado com `OVERLAP` de repetição, para que uma frase que caia
   exatamente na emenda ainda apareça inteira em um dos lados.

TAMANHO
-------
`MAX = 900` caracteres. Escolhido com a régua do modelo, não por gosto: o teto
prático de `text-embedding-3-small` é muito maior, mas um trecho grande demais
**dilui** — o vetor vira a média de vários assuntos e o topo da busca fica
morno. 900 é o que a bancada usou para produzir a separação medida em
`bot/search/query.py`; mudar isso invalida a calibragem do piso.
"""

from __future__ import annotations

import re

MAX = 900
OVERLAP = 150

_PARAGRAFO = re.compile(r"(\n\n+)")


def chunk(texto: str, *, maximo: int = MAX, overlap: int = OVERLAP) -> list[str]:
    """Os trechos de uma mensagem, em ordem. Nunca perde texto.

    A garantia "nunca perde texto" é testada: a concatenação dos trechos cobre
    100% do original. É o inverso exato do defeito que este módulo evita.
    """
    texto = (texto or "").strip()
    if not texto:
        return []
    if len(texto) <= maximo:
        return [texto]

    partes = _PARAGRAFO.split(texto)
    buffer, saida = "", []
    for parte in partes:
        if len(buffer) + len(parte) <= maximo:
            buffer += parte
            continue
        if buffer.strip():
            saida.append(buffer.strip())
        # Parágrafo único maior que a janela: fatia com sobreposição.
        while maximo < len(parte):
            saida.append(parte[:maximo].strip())
            parte = parte[maximo - overlap:]
        buffer = parte
    if buffer.strip():
        saida.append(buffer.strip())
    return [c for c in saida if c]
