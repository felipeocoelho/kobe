"""A leitura do registro de estado — a camada ESTADO do `kobe-remember` v2.

O QUE ELA DEVOLVE, E POR QUE NÃO É "MAIS RESULTADOS"
------------------------------------------------------
A camada de EVIDÊNCIA (a F2) responde *"o que foi dito?"*. Esta responde outra
pergunta, e é a que faltava: ***"o que disso ainda vale?"***.

São graus de confiança diferentes e por isso saem separadas na tela. A evidência
é verdade bruta — aquilo foi dito, ponto. O estado é **curado**: um modelo leu a
evidência e concluiu. Misturar as duas faria o julgamento passar por transcrição.

AS TRÊS PERNAS, E UMA QUARTA QUE SÓ EXISTE AQUI
------------------------------------------------
As três primeiras são as mesmas da F2, pelo mesmo motivo medido lá:

    literal   identificador (`compat_gate`, `working_set.py`) — o dicionário
              `portuguese` destrói nome de arquivo, então é substring por trigrama
    palavra   flexão, via `search_tsv` — mas só com os radicais RAROS, senão
              "decidiu" e "sobre" casam com tudo
    sentido   paráfrase, via vetor, com piso PRÓPRIO (ver abaixo)

A quarta é a que aproveita a arquitetura: **as afirmações cuja ORIGEM está entre
as mensagens que a evidência achou**. É a mais precisa das quatro e custa uma
cláusula `IN` — se a busca literal do histórico achou a mensagem #3059, e há uma
afirmação nascida de #3059, ela responde à pergunta por construção. Nenhuma das
outras três pernas garante isso: a afirmação pode estar escrita com palavras que
não aparecem na pergunta.

**E A PERNA DE PALAVRA NÃO VOTA** — regra herdada da F2, e eu a quebrei aqui uma
vez antes de reencontrá-la. A pergunta *"o que a gente decidiu sobre o campeonato
de xadrez"* (assunto que nunca existiu) voltava com uma **preferência sobre envio
de documento no Telegram**: `campeonat` e `xadrez` são raríssimos, mas `decid`
não é, e a consulta os unia por `OU`. Um radical morno casando já bastava para
uma afirmação entrar como se respondesse.

É exatamente o que `bot/search/__init__.py` documenta desde a F2: *ts_rank é uma
nota LOCAL — mede o casamento dentro do documento e não sabe que o termo é banal
no acervo inteiro.* Então a palavra **pontua** (ordena) e **não elege**: só
sentido, literal e origem colocam uma afirmação na resposta.

O PISO NÃO É O MESMO DA F2 — E A DIFERENÇA FOI MEDIDA
-------------------------------------------------------
A primeira versão reusava o piso da busca de evidência (0,57). **Estava errado**,
e o teste com dado real mostrou por quê: aquele piso foi calibrado sobre
`messages`, que é texto conversacional, longo e ruidoso. As afirmações são o
oposto — curtas, densas, distintas —, e separam muito melhor:

    com resposta no registro   0,570 – 0,773
    sobre assunto inexistente  0,253 – 0,289
    folga                      +0,281   (a da evidência é +0,061)

Com 0,57 o limiar caía **exatamente em cima** do verdadeiro-positivo mais fraco,
e a pergunta *"o que a gente decidiu sobre o nome dos ambientes"* voltava vazia
com a resposta certa a 0,570 no banco. O piso próprio, 0,43, fica no meio dos
dois lados medidos. Ver `bot/lucien.piso_cos`.

Continua valendo o princípio: um ESTADO errado é **pior** que uma evidência
errada — a evidência vem com a fala junto e quem lê julga; o estado vem curado e
o agente serve como se fosse conhecido. Na dúvida, esta camada mostra de menos.

A JANELA DE ECO, E POR QUE ELA TAMBÉM VALE AQUI — 31/08/2026
--------------------------------------------------------------
A camada de EVIDÊNCIA ignora por padrão os últimos 90 s (`JANELA_ECO_S`, com
`--agora` para desligar) por um motivo mecânico: **o bot grava a mensagem do
operador em `messages` ANTES de rodar o turno**. Sem a janela, a busca acha a
própria pergunta e responde com ela.

Esta camada não tinha esse mecanismo, e o buraco é pior aqui do que lá: uma
afirmação nascida da mensagem que o operador acabou de mandar voltava, no mesmo
turno, dentro do bloco *"o que vale hoje"*, **com carimbo de curado e origem
citada**. Uma dúvida de trinta segundos atrás parecendo decisão vigente e
conferível é exatamente o falso positivo com mais autoridade que este sistema
pode produzir.

Agora a janela é a mesma da evidência — literalmente, `bot.search.query.
JANELA_ECO_S`, uma fonte só — e `--agora` desliga as duas juntas. O filtro é
pela data da **mensagem de origem** (`source_message_id` é `NOT NULL`, então
sempre há data), e a saída **diz quantas afirmações a janela cobre**: esconder
em silêncio é o mesmo defeito visto do outro lado.

QUANDO O REGISTRO NÃO EXISTE
-----------------------------
Um banco anterior à migration 008 (a produção, hoje) não tem estas tabelas. Isso
**não pode derrubar o `kobe-remember`** — um comando quebrado é pior que um sem
ESTADO. A falha vira `disponivel=False`, o comando avisa em voz alta e segue com
a evidência.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger("kobe.lucien.consulta")

# Quantas superadas se mostra por vigente. Duas é o que responde *"teve alguma
# decisão que a gente voltou atrás?"* sem transformar a saída num histórico.
SUPERADAS_POR_VIGENTE = 2

# As pernas que ELEGEM uma afirmação. A de palavra não está aqui — ver o
# cabeçalho: ela ordena, não elege.
VOTAM = {"sentido", "literal", "origem"}


@dataclass
class Afirmacao:
    id: str
    topico: str
    subject: str
    statement: str
    kind: str
    status: str
    confidence: str
    valid_from: str
    valid_to: Optional[str] = None
    source_seq: int = 0
    superseded_by: Optional[str] = None
    # Preenchidos na montagem, para a linha de "↑ substitui" / "→ substituída por".
    substituta: Optional[str] = None
    substituta_data: Optional[str] = None
    substituta_seq: Optional[int] = None
    # Metadado, NÃO confiança. A origem ter vindo de áudio é fato do canal e
    # continua registrado; ele só deixou de SER a régua de confiança (que agora
    # mede corroboração). Derivado de `messages.audio_transcribed` na leitura,
    # em vez de copiado para uma coluna — a chave estrangeira já garante o
    # caminho, e dado copiado é dado que envelhece.
    origem_audio: bool = False
    cos: Optional[float] = None
    pernas: list[str] = field(default_factory=list)


@dataclass
class ResultadoEstado:
    vigentes: list[Afirmacao] = field(default_factory=list)
    superadas: list[Afirmacao] = field(default_factory=list)
    disponivel: bool = True
    erro: Optional[str] = None
    sentido_ativo: bool = True
    # A janela de eco aplicada (0 = desligada, `--agora`) e quantas afirmações
    # ela cobre. O segundo número sai na tela: esconder em silêncio é o mesmo
    # defeito de outro lado.
    janela_eco_s: float = 0.0
    ignoradas_pelo_eco: int = 0

    @property
    def vazio(self) -> bool:
        return not self.vigentes and not self.superadas


_CAMPOS = (
    "c.id, c.subject, c.statement, c.kind, c.status, c.confidence,"
    " c.valid_from, c.valid_to, c.source_seq, c.superseded_by,"
    " COALESCE(t.current_name, '?') AS topico,"
    " COALESCE(m.audio_transcribed, false) AS origem_audio"
)
_DE = (" FROM lucien_claims c"
       " LEFT JOIN topics t ON t.id = c.topic_id"
       " LEFT JOIN messages m ON m.id = c.source_message_id")


def _afirmacao(linha: dict, perna: str, cos: Optional[float] = None) -> Afirmacao:
    return Afirmacao(
        id=str(linha["id"]),
        topico=linha.get("topico") or "?",
        subject=linha["subject"],
        statement=linha["statement"],
        kind=linha["kind"],
        status=linha["status"],
        confidence=linha["confidence"],
        valid_from=str(linha["valid_from"]),
        valid_to=str(linha["valid_to"]) if linha.get("valid_to") else None,
        source_seq=int(linha["source_seq"]),
        superseded_by=str(linha["superseded_by"]) if linha.get("superseded_by") else None,
        origem_audio=bool(linha.get("origem_audio")),
        cos=cos,
        pernas=[perna],
    )


def existe(db) -> bool:
    """O registro está montado neste banco? (produção, hoje, ainda não).

    **LEVANTA quando não dá pra saber**, e isso não é descuido: `to_regclass`
    não estoura com tabela ausente — ela devolve `NULL`. Então qualquer exceção
    aqui é o BANCO fora, não a tabela faltando.

    Engolir a exceção e devolver `False` fazia o comando dizer *"o registro de
    estado ainda não existe neste banco (migration 008)"* com o Postgres
    derrubado. É a mesma mentira que a fase inteira combate — "não deu pra
    saber" servido como "não existe" —, e é a que este sistema já cometeu duas
    vezes (a F0.5-B e o `kobe-reflect` em 29/08). Quem separa os dois casos é
    `buscar_estado`.
    """
    return bool(db.scalar("SELECT to_regclass('public.lucien_claims') IS NOT NULL"))


def buscar_estado(
    db,
    pergunta: str,
    *,
    topic_id=None,
    seqs_da_evidencia: Optional[Iterable[int]] = None,
    limite: int = 8,
    janela_eco: Optional[float] = None,
) -> ResultadoEstado:
    """As afirmações que respondem à pergunta. **Nunca levanta.**

    `janela_eco` em segundos: afirmações cuja mensagem de ORIGEM é mais nova que
    isso ficam de fora (ver o cabeçalho). `None` usa a mesma janela da camada de
    evidência — uma fonte só, para as duas camadas não divergirem em silêncio.
    `0` desliga, que é o que `kobe-remember --agora` faz.
    """
    r = ResultadoEstado()
    try:
        montado = existe(db)
    except Exception as exc:  # noqa: BLE001
        # NÃO deu pra saber. Diferente de "não existe", e a distinção é a razão
        # de ser deste comando.
        r.disponivel = False
        r.erro = (f"não deu pra consultar o registro de estado — "
                  f"{type(exc).__name__}: {exc}")
        return r
    if not montado:
        r.disponivel = False
        r.erro = "o registro de estado ainda não existe neste banco (migration 008)"
        return r

    from bot import lucien as cfg
    from bot.search import embedder, query as q

    achados: dict[str, Afirmacao] = {}

    def _juntar(linhas, perna, cos_de=None):
        for l in linhas:
            chave = str(l["id"])
            cos = float(l[cos_de]) if cos_de and l.get(cos_de) is not None else None
            if chave in achados:
                achados[chave].pernas.append(perna)
                if cos is not None and (achados[chave].cos or 0) < cos:
                    achados[chave].cos = cos
            else:
                achados[chave] = _afirmacao(l, perna, cos)

    filtro_topico = " AND c.topic_id = %s" if topic_id else ""
    p_topico = [topic_id] if topic_id else []

    # ── A janela de eco, nas quatro pernas ─────────────────────────────────
    # Filtro no SQL (e não depois, em Python) porque o relógio que decide é o do
    # banco, o mesmo que a camada de evidência usa. O que se perde ao filtrar
    # cedo — saber QUAIS caíram — a contagem abaixo devolve, e é a mesma
    # semântica de `ignoradas_pelo_eco` de lá: quantas a janela cobre.
    r.janela_eco_s = float(q.JANELA_ECO_S if janela_eco is None else (janela_eco or 0.0))
    filtro_eco = ""
    p_recorte = list(p_topico)
    if r.janela_eco_s > 0:
        filtro_eco = " AND m.created_at <= NOW() - make_interval(secs => %s)"
        p_recorte.append(r.janela_eco_s)
        try:
            r.ignoradas_pelo_eco = int(db.scalar(
                "SELECT COUNT(*) FROM lucien_claims c"
                " JOIN messages m ON m.id = c.source_message_id"
                " WHERE m.created_at > NOW() - make_interval(secs => %s)"
                + filtro_topico,
                [r.janela_eco_s, *p_topico],
            ) or 0)
        except Exception:  # noqa: BLE001 — diagnóstico não derruba a consulta
            r.ignoradas_pelo_eco = 0
    filtro = filtro_topico + filtro_eco

    # ── Perna 4 primeiro: a mais precisa, e a que não depende das palavras ──
    #
    # ⚠️ Ela HERDA a força do veredito da evidência, e é quem chama que decide se
    # passa os `seq` ou não (ver `kobe-remember`). O motivo, achado testando: a
    # pergunta sobre o "campeonato de xadrez" — assunto que nunca existiu — dava
    # `MENÇÃO LITERAL` na evidência (as palavras existem soltas no acervo), e os
    # trechos que ela devolvia carregavam para cá uma preferência sobre envio de
    # documento no Telegram, com cara de resposta. Evidência que não afirma
    # relevância não pode ELEGER estado.
    seqs = sorted({int(s) for s in (seqs_da_evidencia or [])})
    if seqs:
        try:
            _juntar(db.query(
                f"SELECT {_CAMPOS}{_DE}"
                f" WHERE c.source_seq = ANY(%s){filtro}"
                " ORDER BY c.valid_from DESC LIMIT %s",
                [seqs, *p_recorte, limite * 2],
            ), "origem")
        except Exception:  # noqa: BLE001
            logger.exception("estado: a perna de origem falhou")

    # ── Literal (identificador) ────────────────────────────────────────────
    try:
        toks = q.literais(pergunta)
        for t in toks[:5]:
            _juntar(db.query(
                f"SELECT {_CAMPOS}{_DE}"
                f" WHERE (c.statement ILIKE %s OR c.subject ILIKE %s){filtro}"
                " ORDER BY c.valid_from DESC LIMIT %s",
                [f"%{t}%", f"%{t}%", *p_recorte, limite],
            ), "literal")
    except Exception:  # noqa: BLE001
        logger.exception("estado: a perna literal falhou")

    # ── Palavra (só os radicais raros) ─────────────────────────────────────
    try:
        pesos, _banais, _ausentes = q.radicais(db, pergunta)
        if pesos:
            consulta = " | ".join(sorted(pesos, key=lambda w: -pesos[w])[:8])
            _juntar(db.query(
                f"SELECT {_CAMPOS}{_DE}"
                f" WHERE c.search_tsv @@ to_tsquery('portuguese', %s){filtro}"
                " ORDER BY c.valid_from DESC LIMIT %s",
                [consulta, *p_recorte, limite],
            ), "palavra")
    except Exception:  # noqa: BLE001
        logger.exception("estado: a perna de palavra falhou")

    # ── Sentido (paráfrase) ────────────────────────────────────────────────
    try:
        vetor = embedder.embed_um(pergunta)
        _juntar(db.query(
            f"SELECT {_CAMPOS}, 1 - (c.embedding <=> %s::vector) AS cos{_DE}"
            f" WHERE c.embedding IS NOT NULL"
            f"   AND 1 - (c.embedding <=> %s::vector) >= %s{filtro}"
            " ORDER BY cos DESC LIMIT %s",
            [embedder.para_sql(vetor), embedder.para_sql(vetor), cfg.piso_cos(),
             *p_recorte, limite],
        ), "sentido", cos_de="cos")
    except embedder.EmbeddingIndisponivel as exc:
        r.sentido_ativo = False
        logger.info("estado: sem a perna de sentido — %s", exc)
    except Exception:  # noqa: BLE001
        r.sentido_ativo = False
        logger.exception("estado: a perna de sentido falhou")

    # ── A eleição: só três pernas votam ────────────────────────────────────
    # A de palavra fica de fora, e o motivo está no cabeçalho: ela não distingue
    # "achei" de "não achei". Ela continua contando na ORDENAÇÃO (`_ordem` conta
    # pernas), então concordar com as outras ainda vale — só não elege sozinha.
    eleitas = [a for a in achados.values() if VOTAM & set(a.pernas)]
    todas = sorted(eleitas, key=_ordem, reverse=True)
    r.vigentes = [a for a in todas if a.status == "vigente"][:limite]
    encerradas = [a for a in todas if a.status != "vigente"]

    ids_vigentes = [a.id for a in r.vigentes]
    if ids_vigentes:
        try:
            # A janela de eco vale aqui também: a superada aparece na tela com o
            # mesmo peso de curada, e uma nascida há trinta segundos teria o
            # mesmo problema. (Sem o filtro de tópico: a substituída pode ser de
            # outro tópico, e escondê-la deixaria a linha "→ substituída por"
            # sem o par.)
            ligadas = db.query(
                f"SELECT {_CAMPOS}{_DE} WHERE c.superseded_by = ANY(%s)"
                + (filtro_eco if r.janela_eco_s > 0 else "")
                + " ORDER BY c.valid_to DESC LIMIT %s",
                [ids_vigentes,
                 *([r.janela_eco_s] if r.janela_eco_s > 0 else []),
                 limite * SUPERADAS_POR_VIGENTE],
            )
            for l in ligadas:
                if str(l["id"]) not in {a.id for a in encerradas}:
                    encerradas.append(_afirmacao(l, "ligada"))
        except Exception:  # noqa: BLE001
            logger.exception("estado: falha buscando as superadas ligadas")

    r.superadas = encerradas[: limite * SUPERADAS_POR_VIGENTE]
    _ligar_substitutas(db, r)
    return r


def _ordem(a: Afirmacao):
    """Vigente antes de encerrada; depois quantas pernas concordam; depois a
    SIMILARIDADE; e só então a data.

    A contagem de pernas vem antes de tudo porque uma afirmação achada por três
    caminhos responde melhor que uma que casou por um. **A similaridade vem
    antes da data**, e isso foi corrigido medindo: com a data no lugar dela, a
    pergunta *"onde fica a base de conhecimento do Kobe"* trazia em primeiro uma
    afirmação sobre orquestrador de missões — as duas empatadas em uma perna, e
    a mais recente ganhando. A data só desempata quem já empatou em relevância;
    sozinha, ela ordena por acaso.
    """
    return (a.status == "vigente", len(set(a.pernas)), a.cos or 0.0, a.valid_from)


def _ligar_substitutas(db, r: ResultadoEstado) -> None:
    """Preenche o "→ substituída por" — sem isso, a linha superada aparece na
    tela sem dizer o que ficou no lugar, que é metade da resposta."""
    alvos = [a.superseded_by for a in r.superadas if a.superseded_by]
    if not alvos:
        return
    try:
        linhas = db.query(
            "SELECT id, statement, valid_from, source_seq FROM lucien_claims"
            " WHERE id = ANY(%s)",
            [alvos],
        )
    except Exception:  # noqa: BLE001
        return
    por_id = {str(l["id"]): l for l in linhas}
    for a in r.superadas:
        alvo = por_id.get(a.superseded_by or "")
        if alvo:
            a.substituta = alvo["statement"]
            a.substituta_data = str(alvo["valid_from"])
            a.substituta_seq = int(alvo["source_seq"])


# ── O vetor das afirmações ───────────────────────────────────────────────


def embeddar_pendentes(db_ou_cx, *, limite: int = 200) -> int:
    """Preenche o vetor das afirmações que ainda não têm.

    Roda DEPOIS da escrita, nunca dentro dela: uma afirmação não pode deixar de
    ser gravada porque um serviço de embedding estava fora. Sem vetor ela ainda
    é achável por palavra e por identificador — perde só a perna da paráfrase, e
    só até a próxima passada.
    """
    from bot.search import embedder

    cur = db_ou_cx.cursor() if hasattr(db_ou_cx, "cursor") else None
    if cur is not None:
        cur.execute(
            "SELECT id, subject, statement FROM lucien_claims"
            " WHERE embedding IS NULL ORDER BY id LIMIT %s",
            (limite,),
        )
        linhas = cur.fetchall()
    else:
        linhas = db_ou_cx.query(
            "SELECT id, subject, statement FROM lucien_claims"
            " WHERE embedding IS NULL ORDER BY id LIMIT %s",
            (limite,),
        )
    if not linhas:
        return 0

    textos = [f"{l['subject']}: {l['statement']}" for l in linhas]
    vetores = embedder.embed(textos)
    modelo = embedder.modelo()
    for linha, vetor in zip(linhas, vetores):
        sql = ("UPDATE lucien_claims SET embedding = %s::vector, model = %s,"
               " embedded_at = NOW() WHERE id = %s")
        params = (embedder.para_sql(vetor), modelo, linha["id"])
        if cur is not None:
            cur.execute(sql, params)
        else:
            db_ou_cx.execute(sql, params)
    return len(linhas)
