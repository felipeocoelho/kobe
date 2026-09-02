"""A metade de ESCRITA do boletim quente — o registro projetado em disco.

Highlander v3, F4. O que o turno lê está em `bot/memory/boletim.py`; aqui está
quem escreve. A direção da dependência é só esta — **`lucien` importa `memory`,
nunca o contrário** —, e é ela que impede o caminho quente de aprender a
consultar o banco.

QUEM CHAMA, E POR QUE NÃO É UMA FONTE NOVA DO KEYKO
-----------------------------------------------------
O boletim é **função pura** de `lucien_claims` + `lucien_events` daquele tópico.
A única coisa capaz de mudá-lo é uma rodada do LUCIEN gravando ali. Então o
gatilho natural é o fim da rodada: o processo já está de pé, já é detached, e
acabou de gastar dezenas de segundos falando com um modelo. Duas consultas e um
`write()` são ruído nessa conta.

Uma fonte própria no Keyko faria o daemon perguntar "mudou?" em todos os tópicos
a cada tique para quase sempre não fazer nada — e acrescentaria superfície de
falha no processo de que os **Alertas** dependem, onde atraso é falha que o
operador vê.

O QUE ISTO NÃO FAZ: CHAMAR MODELO
-----------------------------------
Nenhuma chamada de modelo acontece aqui — nem pela CLI, nem por API. Quem
escolhe as linhas é **código**: duas consultas e formatação de string. O modelo
já foi chamado antes, quando LUCIEN escreveu a afirmação; o boletim só
**projeta** o que já está gravado.

Isso responde, com fato, a uma preocupação real do operador em 31/08/2026 — *"o
LUCIEN vai ficar trabalhando igual um doido"*. Ele não trabalha mais do que hoje:
o custo marginal de assinatura é **zero**, não "baixo". É a mesma linha que o
`CLAUDE.md` já traça para os Alertas: *a lógica determinística é do CÓDIGO; o
modelo só é invocado para LINGUAGEM*. Aqui não há linguagem a produzir.

IDEMPOTÊNCIA POR CONSTRUÇÃO, E FOI ELA QUE DISPENSOU A MIGRATION
------------------------------------------------------------------
O critério 5 da fase exige que gerar duas vezes sem conversa nova **não mude o
arquivo**. A forma ingênua de conseguir isso — gravar `gerado_em = now()` e
comparar depois — quebra o critério: na segunda passada o delta apareceria vazio
e o arquivo mudaria sozinho.

O conserto foi fazer **cada byte ser função do banco**. Em particular, o
cabeçalho diz `apurado até <marca d'água do registro>` — o `max` sobre as datas
das próprias afirmações — e não o relógio de quem gerou. Mesma tabela, mesmos
bytes. E, como efeito colateral que vale mais que o próprio critério, **a F4
inteira não precisou de coluna, tabela nem migration**: não há estado de geração
para guardar.

A escrita ainda compara bytes antes de gravar, para que nem o `mtime` mude à
toa — quem inspeciona a pasta merece que a data do arquivo signifique alguma
coisa.

FALHAR AQUI NUNCA DESFAZ UMA RODADA
-------------------------------------
A geração roda **depois** do commit, e o chamador a embrulha. É a mesma regra
que o embedder já segue no `worker`: uma afirmação não pode deixar de valer
porque um arquivo de conveniência não pôde ser escrito.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from bot.memory import boletim as fmt

logger = logging.getLogger("kobe.lucien.boletim")

# Quantas linhas se lê de cada bloco. O teto de verdade é o orçamento em chars,
# aplicado no `montar`; isto é só para a consulta não trazer o acervo inteiro
# quando o tópico tem centenas de afirmações. Folgado de propósito: se um dia o
# orçamento subir, o corte continua sendo do orçamento e não deste número.
POR_BLOCO = 40

_KINDS_VIGENTES = ("decision", "preference", "fact")
_ACOES_SAIDA = ("superseded", "closed", "abandoned", "reverted")


def habilitado() -> bool:
    """A mesma chave do lado leitura — uma só para os dois lados."""
    return fmt.habilitado()


def _dia(valor) -> str:
    try:
        return valor.strftime("%d/%m")
    except AttributeError:
        return str(valor)[:10]


def _data_longa(valor) -> str:
    try:
        return valor.strftime("%d/%m/%Y")
    except AttributeError:
        return str(valor)[:10] or "(sem data)"


def _linhas(cur, topic_id: str, kinds) -> list[fmt.Linha]:
    cur.execute(
        "SELECT statement, kind, valid_from, source_seq FROM lucien_claims"
        " WHERE topic_id = %s AND status = 'vigente' AND kind = ANY(%s)"
        " ORDER BY valid_from DESC LIMIT %s",
        (topic_id, list(kinds), POR_BLOCO),
    )
    return [
        fmt.Linha(kind=r["kind"], dia=_dia(r["valid_from"]),
                  texto=r["statement"], seq=int(r["source_seq"]))
        for r in cur.fetchall()
    ]


def _saidas(cur, topic_id: str) -> list[fmt.Saida]:
    """O que deixou de valer — e por que este bloco NÃO lista `created`.

    Toda afirmação criada gera um evento `created`, e as recém-criadas já estão
    no topo dos outros dois blocos, que são ordenados por recência. Listá-las
    aqui gastaria orçamento repetindo o que já está na tela. O que os outros
    blocos estruturalmente **não podem** mostrar é o que saiu de cena — e é
    justamente esse o sinal de "a gente mudou de ideia", que é metade da dor
    que a fase existe para curar.
    """
    cur.execute(
        "SELECT e.action, e.at, c.statement, c.source_seq"
        "  FROM lucien_events e JOIN lucien_claims c ON c.id = e.claim_id"
        " WHERE c.topic_id = %s AND e.action = ANY(%s)"
        " ORDER BY e.at DESC LIMIT %s",
        (topic_id, list(_ACOES_SAIDA), POR_BLOCO),
    )
    return [
        fmt.Saida(acao=r["action"], dia=_dia(r["at"]),
                  texto=r["statement"], seq=int(r["source_seq"]))
        for r in cur.fetchall()
    ]


def montar_texto(cx, topic_id: str) -> Optional[str]:
    """O texto do boletim de um tópico, ou `None` se não há o que dizer.

    Só leitura — não escreve nada. Existe separado de `gerar` para que a
    inspeção (`kobe-lucien boletim --ver`) e o teste possam olhar o conteúdo sem
    tocar no disco.
    """
    cur = cx.cursor()

    cur.execute("SELECT COALESCE(current_name, '(sem nome)') AS nome"
                "  FROM topics WHERE id = %s", (topic_id,))
    linha = cur.fetchone()
    if not linha:
        return None
    nome = linha["nome"]

    # A marca d'água do registro: a data mais recente entre nascimento e
    # encerramento de qualquer afirmação do tópico. É ela que vai no cabeçalho,
    # NÃO `now()` — ver o cabeçalho do módulo. Sem isto, o arquivo mudaria a
    # cada geração e o critério 5 cairia.
    cur.execute(
        "SELECT MAX(GREATEST(created_at, COALESCE(valid_to, created_at))) AS marca,"
        "       COUNT(*) FILTER (WHERE status = 'vigente') AS vigentes"
        "  FROM lucien_claims WHERE topic_id = %s",
        (topic_id,),
    )
    r = cur.fetchone()
    if not r or not r["marca"]:
        return None

    return fmt.montar(
        topico=nome,
        apurado_ate=_data_longa(r["marca"]),
        pendencias=_linhas(cur, topic_id, ("open",)),
        vigentes=_linhas(cur, topic_id, _KINDS_VIGENTES),
        saiu_de_cena=_saidas(cur, topic_id),
        total_vigentes=int(r["vigentes"] or 0),
    )


def gerar(cx, topic_id: str, *, kobe_home) -> bool:
    """Escreve o boletim do tópico. Devolve `True` se o arquivo MUDOU.

    `False` cobre dois casos que não vale a pena distinguir aqui: não havia o
    que escrever, ou o conteúdo é idêntico ao que já está lá. Nos dois o disco
    fica intocado — inclusive o `mtime`.

    **Nunca levanta.** Quem chama é uma rodada que já comitou; um arquivo de
    conveniência não pode desfazer estado que já vale.
    """
    try:
        texto = montar_texto(cx, topic_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("boletim: falhou montando o de %s — %s", topic_id, exc)
        return False

    alvo = fmt.caminho(Path(kobe_home), topic_id)
    try:
        if texto is None:
            return False
        if alvo.is_file() and alvo.read_text(encoding="utf-8") == texto:
            return False  # idempotência: nem o mtime se mexe
        alvo.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atômica: o turno pode estar lendo este arquivo agora. Um
        # `write()` direto exporia meio boletim a quem lesse no meio.
        temporario = alvo.with_suffix(".md.tmp")
        temporario.write_text(texto, encoding="utf-8")
        os.replace(temporario, alvo)
        logger.info("boletim: %s atualizado (%d chars)", alvo.name, len(texto))
        return True
    except OSError as exc:
        logger.warning("boletim: falhou gravando %s — %s", alvo, exc)
        return False


def gerar_todos(cx, *, kobe_home) -> tuple[int, int]:
    """Todos os tópicos que têm afirmação. Devolve (mudados, vistos).

    Serve ao backfill da primeira instalação e ao smoke — e é barato porque o
    caso comum é não mudar nada.
    """
    cur = cx.cursor()
    cur.execute("SELECT DISTINCT topic_id::text AS t FROM lucien_claims")
    topicos = [r["t"] for r in cur.fetchall()]
    mudados = sum(1 for t in topicos if gerar(cx, t, kobe_home=kobe_home))
    return mudados, len(topicos)
