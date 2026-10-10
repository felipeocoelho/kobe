"""A porta de entrada deliberada da tabela de fatos.

O LUCIEN escreve lendo a conversa, atrás do turno, por acúmulo. Isso cobre bem o
que é dito de passagem — mas **não cobre o ato deliberado**: o operador diz *"minha
preferência é X"* e o agente precisa poder responder *"registrei na tabela de fatos,
vigente desde hoje, origem #N"* em vez de *"salvei num arquivo"*.

Até este módulo existir, o único caminho era o arquivo `.md` — que não tem
vigência, não tem origem e não tem revogação. Foi assim que uma preferência do
operador foi parar numa pasta de knowledge sem carimbo nenhum, e é essa a bagunça
que este módulo fecha.

O QUE ESTE MÓDULO **NÃO** RELAXA
---------------------------------
**A origem continua obrigatória.** `lucien_claims.source_message_id` é
`NOT NULL REFERENCES messages(id)` — e isso é a trava que sustenta a confiança do
registro inteiro. A porta não contorna: ela **resolve** a mensagem real do
operador no tópico corrente e amarra a afirmação nela. Uma afirmação registrada
por aqui aponta para uma fala que existe e pode ser aberta com
`kobe-remember --ver <n>`.

Consequência aceita de propósito: **não dá para registrar por aqui um fato que
nunca foi dito.** Fato que só existe dentro de um arquivo continua sem entrada —
e a saída para ele é dizê-lo, não afrouxar a chave estrangeira.

A DATA É A DO FATO, NÃO A DA GRAVAÇÃO
--------------------------------------
`valid_from` recebe o `created_at` da mensagem de origem, igual ao que o LUCIEN
faz. Se fosse `NOW()`, uma afirmação registrada hoje sobre algo dito ontem
nasceria mentindo sobre quando passou a valer.

QUEM DÁ O COMMIT É QUEM CHAMA — E ISSO NÃO É DETALHE
-----------------------------------------------------
`registrar` e `revogar` **não commitam**. Quem commita é o chamador (o
`kobe-fato`). Duas razões, e a segunda foi paga com erro:

1. Uma função de biblioteca que commita não compõe — não dá para gravar duas
   afirmações na mesma transação nem desfazer a segunda sem desfazer a primeira.
2. **Teste que roda dentro de transação revertida deixa de isolar** se a função
   commita por dentro. Aconteceu: a primeira versão deste módulo commitava, e a
   suíte gravou sete linhas de teste no banco de desenvolvimento — exatamente o
   tipo de poluição que a porta existe para evitar.


CONFIANÇA: ALTA, EXCETO QUANDO A ORIGEM É ÁUDIO
------------------------------------------------
Fala direta do operador é a evidência mais forte que existe — nasce `alta`. Mas
se a mensagem de origem veio de transcrição, cai para `media`, pela mesma razão
que o LUCIEN aplica: transcrição erra, e erro de transcrição virando fato
permanente já aconteceu neste acervo.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from bot.lucien import store

logger = logging.getLogger("kobe.lucien.porta")

# Os quatro tipos são os do schema. O vocabulário de fora é em português porque
# quem chama é o agente, em conversa — traduzir aqui evita que o jargão do banco
# vaze para o único lugar onde ele não ajuda.
TIPOS = {
    "decisao": "decision",
    "aberto": "open",
    "preferencia": "preference",
    "fato": "fact",
}

STATEMENT_MIN = 20
STATEMENT_MAX = 400

# Quanto o comando espera pelo vetor antes de desistir e deixar para o curador.
TETO_VETOR_S = 10.0


class PortaRecusou(Exception):
    """A porta recusou a gravação, e a mensagem diz por quê.

    Recusa é resposta legítima: registro ruim é pior que registro ausente,
    porque ele entra no boletim com cara de verdade apurada.
    """


def _mensagem_de_origem(cx, topic_id: str, *, seq: Optional[int]) -> dict:
    """A fala do operador que origina a afirmação.

    Sem `seq`, pega a **última mensagem do operador** naquele tópico — que, no
    caminho normal (o agente chamando durante o turno), é exatamente a mensagem
    que acabou de chegar e que contém o que ele disse.
    """
    cur = cx.cursor()
    if seq is not None:
        cur.execute(
            "SELECT id, seq, created_at, audio_transcribed, content"
            "  FROM messages WHERE topic_id = %s AND seq = %s",
            (topic_id, int(seq)),
        )
    else:
        cur.execute(
            "SELECT id, seq, created_at, audio_transcribed, content"
            "  FROM messages WHERE topic_id = %s AND role = 'user'"
            " ORDER BY seq DESC LIMIT 1",
            (topic_id,),
        )
    linha = cur.fetchone()
    if linha is None:
        raise PortaRecusou(
            "não achei mensagem de origem neste tópico. A porta não grava "
            "afirmação sem origem real — é a trava que sustenta o registro."
        )
    if linha["seq"] is None:
        raise PortaRecusou(
            f"a mensagem de origem não tem `seq` (mensagem antiga, anterior à "
            f"migration 007). Passe --origem com um seq válido."
        )
    return linha


def _conferir(assunto: str, afirmacao: str, tipo: str) -> None:
    if tipo not in TIPOS:
        raise PortaRecusou(
            f"tipo {tipo!r} não existe. Os quatro são: {', '.join(sorted(TIPOS))}."
        )
    if not assunto.strip():
        raise PortaRecusou("o assunto não pode ser vazio — é ele que agrupa e deduplica.")
    n = len(afirmacao.strip())
    if n < STATEMENT_MIN:
        raise PortaRecusou(
            f"a afirmação tem {n} caracteres e o mínimo é {STATEMENT_MIN}. "
            "Afirmação curta demais não se sustenta sozinha daqui a três meses."
        )
    if n > STATEMENT_MAX:
        raise PortaRecusou(
            f"a afirmação tem {n} caracteres e o teto é {STATEMENT_MAX}. "
            "O teto não é burocracia: é o que mantém o boletim plural. "
            "Se não cabe numa frase, provavelmente são duas afirmações — ou é "
            "documento, e documento vai para arquivo."
        )


def registrar(
    cx,
    *,
    topic_id: str,
    assunto: str,
    afirmacao: str,
    tipo: str,
    origem_seq: Optional[int] = None,
) -> dict[str, Any]:
    """Grava uma afirmação como **do operador**, com origem, data e vigência.

    Devolve o que o agente precisa para relatar de volta: o id, o `seq` da origem
    e a data de vigência — para a frase ser *"registrei na tabela de fatos, vigente desde
    <data>, origem #<seq>"* e não *"salvei"*.
    """
    _conferir(assunto, afirmacao, tipo)
    origem = _mensagem_de_origem(cx, topic_id, seq=origem_seq)

    assunto, afirmacao = assunto.strip(), afirmacao.strip()
    chave = store.slug(assunto)

    cur = cx.cursor()
    # Dedupe barato: mesma afirmação, mesmo assunto, ainda vigente. Sem isto, o
    # agente que repete a chamada num retry duplica a linha no boletim.
    cur.execute(
        "SELECT id FROM lucien_claims"
        " WHERE topic_id = %s AND subject_slug = %s AND status = 'vigente'"
        "   AND lower(statement) = lower(%s) LIMIT 1",
        (topic_id, chave, afirmacao),
    )
    ja = cur.fetchone()
    if ja:
        return {"id": str(ja["id"]), "duplicata": True, "seq": int(origem["seq"])}

    confianca = "media" if origem["audio_transcribed"] else "alta"
    cur.execute(
        "INSERT INTO lucien_claims (topic_id, subject, subject_slug, statement,"
        " kind, status, confidence, valid_from, source_message_id, source_seq,"
        " created_by)"
        " VALUES (%s,%s,%s,%s,%s,'vigente',%s,%s,%s,%s,'operador') RETURNING id",
        (topic_id, assunto, chave, afirmacao, TIPOS[tipo], confianca,
         origem["created_at"], origem["id"], origem["seq"]),
    )
    novo = str(cur.fetchone()["id"])
    store._evento(
        cx, novo, "created", None,
        {"source_seq": int(origem["seq"]), "audio": bool(origem["audio_transcribed"]),
         "confianca": confianca, "porta": "deliberada"},
        None, actor="operador",
    )
    return {
        "id": novo,
        "duplicata": False,
        "seq": int(origem["seq"]),
        "valid_from": origem["created_at"],
        "confianca": confianca,
    }


def gerar_vetor(cx, claim_id: str, *, cliente=None) -> dict[str, Any]:
    """Gera o vetor de busca da afirmação recém-gravada. **Nunca levanta.**

    Chamar SÓ depois do commit do `registrar` — é a lei do
    `consulta.embeddar_pendentes`: a afirmação não pode deixar de ser gravada
    porque o serviço de embedding estava fora. O trabalho roda num bloco de
    transação próprio (`cx.transaction()`): dentro de uma transação aberta vira
    savepoint e quem commita é o chamador — é o que mantém os testes isolados;
    fora dela, o bloco abre e fecha a sua. Se falhar, desfaz só o vetor, e a
    afirmação fica como sempre ficou (sem vetor, achável por palavra e
    identificador, completada pelo curador).

    Por que existe: sem isto a perna de sentido só alcançava o fato na próxima
    passada do curador — medido, 2 de 8 fatos recém-gravados achados contra 6 de
    8 com o vetor na hora. E a falha **não pode ser muda**: o retorno traz o
    motivo, para o comando dizer em uma linha.

    Teto de espera de `TETO_VETOR_S`, sem nova tentativa: a porta é caminho
    interativo — o operador está esperando a resposta. O curador segue com o
    teto dele.
    """
    from bot.lucien import consulta

    inicio = time.monotonic()
    try:
        cli = cliente
        if cli is None:
            from bot.search import embedder
            cli = embedder._client().with_options(
                timeout=TETO_VETOR_S, max_retries=0)
        with cx.transaction():  # savepoint: a falha desfaz só o vetor
            n = consulta.embeddar_pendentes(cx, limite=1, ids=[claim_id],
                                            cliente=cli)
        return {"ok": True, "gerados": n, "motivo": None,
                "segundos": time.monotonic() - inicio}
    except Exception as exc:  # noqa: BLE001 — qualquer falha aqui é a mesma: sem vetor por ora
        logger.warning("porta: vetor de %s não saiu: %s", claim_id, exc)
        return {"ok": False, "gerados": 0, "motivo": f"{type(exc).__name__}: {exc}",
                "segundos": time.monotonic() - inicio}


def revogar(cx, claim_id: str, *, motivo: str) -> dict[str, Any]:
    """Tira uma afirmação de vigência. **Não apaga** — data o fim.

    Só o operador revoga o que é do operador (ver a trava no `store`), e revogar
    tem que ser tão barato quanto registrar: se sair for difícil, ninguém entra.
    """
    if not motivo.strip():
        raise PortaRecusou("revogação sem motivo não entra — o motivo é o que se lê depois.")
    antes = store._imagem(cx, claim_id)
    if antes is None:
        raise PortaRecusou(f"afirmação {claim_id} não existe.")
    if antes["status"] != "vigente":
        raise PortaRecusou(
            f"afirmação {claim_id} já está com status {antes['status']!r} — "
            "nada a revogar."
        )
    cur = cx.cursor()
    cur.execute(
        "UPDATE lucien_claims SET status = 'abandonada', valid_to = %s,"
        " updated_at = NOW() WHERE id = %s",
        (datetime.now(timezone.utc), claim_id),
    )
    store._evento(cx, claim_id, "abandoned", antes,
                  {"motivo": motivo.strip()[:300], "porta": "deliberada"},
                  None, actor="operador")
    return {"id": claim_id, "statement": antes["statement"]}


def listar(cx, topic_id: str, *, limite: int = 30) -> list[dict]:
    """O que o operador registrou por esta porta, mais recente primeiro."""
    cur = cx.cursor()
    cur.execute(
        "SELECT id, subject, statement, kind, status, valid_from, source_seq"
        "  FROM lucien_claims WHERE topic_id = %s AND created_by = 'operador'"
        " ORDER BY valid_from DESC LIMIT %s",
        (topic_id, limite),
    )
    return list(cur.fetchall())
