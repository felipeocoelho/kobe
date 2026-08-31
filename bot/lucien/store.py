"""A única porta de escrita do registro de estado.

POR QUE EXISTE UMA PORTA SÓ
---------------------------
A F3 é a fase em que um modelo escreve estado que o agente depois serve como se
fosse conhecido. Se houvesse dois caminhos de escrita, um deles acabaria sem
alguma das travas — e a que faltasse seria descoberta seis meses depois, por
uma afirmação errada no meio de uma conversa.

Então: **`aplicar()` é o único lugar do Kobe que insere em `lucien_claims`.**
O `kobe-lucien reverter` também passa por aqui. O modelo nunca toca no banco.

AS NOVE TRAVAS
--------------
Nenhuma delas confia no modelo, e todas são contáveis — o que uma trava recusa
vira `Recusa` no resultado e `claims_rejected` na linha da rodada.

    T1  `source_seq` TEM que estar no lote mostrado ao modelo.
    T2  superação/encerramento só de apelido MOSTRADO e ainda vigente.
    T3  superação sem motivo escrito é recusada.
    T4  vocabulário fechado e tamanho de texto conferido.
    T5  `valid_from` = `created_at` da origem. Nunca `NOW()`.
    T6  a confiança sai de CORROBORAÇÃO, e o modelo só consegue REBAIXAR.
    T7  teto de afirmações por lote — **manda dividir, não descarta**.
    T8  dedupe contra o que já vale.
    T9  (em `worker.py`) resposta torta descarta a rodada inteira e NÃO avança
        o cursor.

A T1 é a que sustenta a fase. O banco já garante que a mensagem citada existe
(chave estrangeira); a T1 garante que **o modelo a viu**. Sem ela, uma citação
plausível e inventada — `#3059` num assunto que o modelo conhece de outro lugar
— entraria com cara de origem conferida.

POR QUE UMA CONEXÃO PRÓPRIA, E NÃO A PONTE DO BOT
--------------------------------------------------
`bot/db.py` é um pool em **autocommit**: um comando por conexão emprestada. Isso
é o certo para o caminho do turno e é o errado aqui — a escrita de uma rodada é
uma coisa só. Criar uma afirmação nova, fechar a que ela supera, gravar os dois
eventos e avançar o cursor **ou acontece inteiro, ou não acontece**: um registro
com a superação gravada e a substituta ausente diria que uma decisão foi
revogada por nada.

Mesmo motivo do `kobe-await-response` ("uma conexão curta, sem pool"): o worker
é um processo de vida curta.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from bot import lucien as cfg
from bot.lucien.models import (
    ClaimProposta,
    Encerramento,
    Lote,
    Mensagem,
    Proposta,
    Recusa,
    ResultadoDaRodada,
)

logger = logging.getLogger("kobe.lucien.store")

# Uma rodada de cada vez, no banco inteiro. Duas escrevendo o mesmo tópico
# fariam a T8 (dedupe) enxergar um estado que a outra ainda não gravou, e o
# registro ganharia a mesma afirmação duas vezes. O número é arbitrário e fixo —
# é só um nome para o cadeado.
LOCK_ID = 0x1AC1E4


def conectar(conninfo: str):
    """Conexão própria, **sem autocommit** — ver o cabeçalho."""
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(
        conninfo, row_factory=dict_row, autocommit=False, options="-c TimeZone=UTC"
    )


def travar(cx) -> bool:
    """Cadeado consultivo, não-bloqueante. Falhar em pegar não é erro: é outra
    rodada acontecendo, e a próxima passada do relógio tenta de novo."""
    cur = cx.cursor()
    cur.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_ID,))
    return bool(cur.fetchone()["ok"])


def destravar(cx) -> None:
    try:
        cx.cursor().execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    except Exception:  # noqa: BLE001 — soltar cadeado não derruba nada
        logger.exception("lucien: falha soltando o cadeado")


# ── Normalização ─────────────────────────────────────────────────────────


def slug(texto: str) -> str:
    """`"Arquitetura de Borda"` e `"arquitetura da borda"` têm que cair no mesmo
    balde, senão o registro tem três assuntos onde há um."""
    t = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    # Palavras de ligação não distinguem assunto e são o que faz "de" virar "da".
    partes = [p for p in t.split("-") if p and p not in _LIGACAO]
    return "-".join(partes)[:80] or "sem-assunto"


_LIGACAO = {"de", "da", "do", "das", "dos", "a", "o", "as", "os", "e", "em", "no",
            "na", "nos", "nas", "para", "pra", "com", "sobre", "um", "uma"}


def _chave_dedupe(statement: str) -> str:
    t = unicodedata.normalize("NFKD", (statement or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# ── Leitura: o que se mostra ao modelo ───────────────────────────────────


def topicos_pendentes(cx, *, scope: str = "incremental") -> list[dict]:
    """Tópicos com mensagem além do cursor, com quantas e a idade da mais velha.

    Tópico **sem mensagem nenhuma não aparece** — e isso não é só otimização:
    um tópico fantasma (criado por um `ensure_topic` de teste, por exemplo) não
    tem o que catalogar, e uma rodada sobre o vazio seria uma chamada de modelo
    gasta para nada.
    """
    cur = cx.cursor()
    cur.execute(
        """
        SELECT t.id AS topic_id,
               COALESCE(t.current_name, '(sem nome)') AS topico,
               COUNT(m.id)                            AS pendentes,
               MIN(m.created_at)                      AS mais_antiga,
               COALESCE(c.last_seq, 0)                AS last_seq
          FROM topics t
          JOIN messages m
            ON m.topic_id = t.id
           AND m.role IN ('user', 'assistant')
           AND m.seq > COALESCE(
                 (SELECT last_seq FROM lucien_cursor
                   WHERE scope = %(scope)s AND topic_id = t.id), 0)
          LEFT JOIN lucien_cursor c
            ON c.scope = %(scope)s AND c.topic_id = t.id
         GROUP BY t.id, t.current_name, c.last_seq
         ORDER BY MIN(m.created_at)
        """,
        {"scope": scope},
    )
    return cur.fetchall()


def lote_devido(linha: dict, *, agora: Optional[datetime] = None) -> bool:
    """Os dois gatilhos: acúmulo OU idade. Ver o porquê de cada um em
    `bot/lucien/__init__.py`."""
    if int(linha["pendentes"] or 0) >= cfg.lote_minimo():
        return True
    mais_antiga = linha.get("mais_antiga")
    if mais_antiga is None:
        return False
    agora = agora or datetime.now(timezone.utc)
    if mais_antiga.tzinfo is None:
        mais_antiga = mais_antiga.replace(tzinfo=timezone.utc)
    return (agora - mais_antiga).total_seconds() >= cfg.idade_maxima_s()


def marco_incremental(cx, topic_id: str) -> Optional[int]:
    """Onde o cursor incremental foi fincado neste tópico — o `kobe-lucien init`.

    É o limite superior da reconstrução: ela varre o passado ATÉ aqui e para. Sem
    esse teto, a varredura passaria por cima do que a leitura corrente já está
    fazendo, e as duas gastariam cota no mesmo trecho.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT last_seq FROM lucien_cursor WHERE scope='incremental' AND topic_id=%s",
        (topic_id,),
    )
    linha = cur.fetchone()
    return int(linha["last_seq"]) if linha else None


def montar_lote(cx, topic_id: str, *, scope: str = "incremental",
                teto_seq: Optional[int] = None) -> Lote:
    """As mensagens novas do tópico, respeitando os dois tetos.

    O corte por caracteres acontece DEPOIS do corte por quantidade e sempre
    deixa ao menos uma mensagem: um lote vazio por causa de uma única mensagem
    gigante travaria o cursor para sempre naquele ponto.

    `teto_seq` é o limite superior — a reconstrução o usa para parar no marco.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT COALESCE(t.current_name, '(sem nome)') AS topico,"
        "       COALESCE(c.last_seq, 0) AS last_seq"
        "  FROM topics t"
        "  LEFT JOIN lucien_cursor c ON c.scope = %s AND c.topic_id = t.id"
        " WHERE t.id = %s",
        (scope, topic_id),
    )
    cab = cur.fetchone()
    if cab is None:
        return Lote(topic_id=topic_id, topico_nome="(tópico inexistente)")

    cur.execute(
        "SELECT id, seq, role, content, created_at, audio_transcribed"
        "  FROM messages"
        " WHERE topic_id = %s AND seq > %s AND role IN ('user', 'assistant')"
        "   AND (%s::bigint IS NULL OR seq <= %s::bigint)"
        " ORDER BY seq LIMIT %s",
        (topic_id, cab["last_seq"], teto_seq, teto_seq, cfg.lote_maximo()),
    )
    mensagens: list[Mensagem] = []
    total = 0
    teto = cfg.lote_maximo_chars()
    for linha in cur.fetchall():
        corpo = linha["content"] or ""
        if mensagens and total + len(corpo) > teto:
            break
        total += len(corpo)
        mensagens.append(
            Mensagem(
                seq=int(linha["seq"]),
                id=str(linha["id"]),
                role=linha["role"],
                created_at=linha["created_at"],
                content=corpo,
                audio=bool(linha["audio_transcribed"]),
            )
        )

    lote = Lote(topic_id=topic_id, topico_nome=cab["topico"], mensagens=mensagens)
    lote.estado = estado_vigente(cx, topic_id, lote=lote)
    return lote


def estado_vigente(cx, topic_id: str, *, lote: Optional[Lote] = None) -> dict[str, dict]:
    """As afirmações vigentes que o modelo pode contradizer, por apelido.

    **Apelido (`E1`, `E2`…) e não UUID**, de propósito. Um UUID de 36 caracteres
    gasta contexto e convida a erro de digitação; pior, um UUID quase-certo é
    difícil de distinguir de um certo. Um apelido inexistente (`E9` quando só
    houve `E1`–`E5`) é recusado sem ambiguidade nenhuma.

    A seleção é **recência ∪ relevância**, e a segunda parte é a que faz a
    superação existir. Só recência erraria exatamente no caso que mais importa:
    uma decisão de MAIO sendo revertida em JUNHO, num tópico que acumulou
    centenas de afirmações no meio — ela cai fora da janela justamente quando é
    ela que precisa ser vista, e a reversão nunca acontece.

    ⚠️ **A primeira versão desta função tinha a perna de relevância MORTA**, e o
    defeito era invisível de fora: ela usava `plainto_tsquery` sobre o texto do
    lote inteiro. Esse construtor liga os termos por **E**, então um lote de
    8.000 caracteres virava uma consulta de **859 lexemas em conjunção** — que
    nenhuma afirmação do mundo casa. Medido em 30/08/2026: **zero** resultados,
    enquanto havia **15** afirmações vigentes falando do assunto daquele lote.

    A consequência foi a régua da fase falhando no exemplo mais gritante do
    acervo: a decisão *"a sincronização dev VPS → prod VPS deve ser feita via
    rsync"* (16/05) **continuou vigente** depois de a varredura atravessar o
    incidente de 12–13/06 que a proibiu. E o modelo não errou — **ela nunca lhe
    foi mostrada**.

    As duas pernas que substituíram aquilo:

    - **por palavra**, com os radicais RAROS do lote ligados por OU (a mesma
      estatística `search_lexeme_df` da F2, que existe porque `ts_rank` é uma
      nota local e não sabe que "decidiu" está em todo lugar);
    - **por sentido**, com o vetor do lote — a única perna que enxerga a
      afirmação escrita com outras palavras.
    """
    cur = cx.cursor()
    limite = cfg.estado_maximo()
    campos = "id, subject, statement, kind, valid_from, confidence, source_seq"

    cur.execute(
        f"SELECT {campos} FROM lucien_claims"
        " WHERE topic_id = %s AND status = 'vigente'"
        " ORDER BY valid_from DESC LIMIT %s",
        (topic_id, limite),
    )
    linhas = {str(r["id"]): r for r in cur.fetchall()}

    texto = " ".join(m.content for m in lote.mensagens)[:8000] if lote else ""
    if not texto.strip():
        ordenadas = sorted(linhas.values(), key=lambda r: r["valid_from"], reverse=True)
        return {f"E{i}": r for i, r in enumerate(ordenadas[: limite * 2], start=1)}

    # ── Relevância por PALAVRA (radicais raros, ligados por OU) ───────────
    try:
        consulta = _radicais_raros(cx, texto)
        if consulta:
            cur.execute(
                f"SELECT {campos} FROM lucien_claims"
                " WHERE topic_id = %s AND status = 'vigente'"
                "   AND search_tsv @@ to_tsquery('portuguese', %s)"
                " ORDER BY valid_from DESC LIMIT %s",
                (topic_id, consulta, limite),
            )
            for r in cur.fetchall():
                linhas.setdefault(str(r["id"]), r)
    except Exception:  # noqa: BLE001 — relevância a menos não derruba a rodada
        logger.exception("lucien: a perna de palavra do estado vigente falhou")

    # ── Relevância por SENTIDO (o vetor do lote) ──────────────────────────
    # Custa uma chamada de embedding por rodada — centésimos de centavo contra a
    # diferença entre superar e não superar uma decisão.
    try:
        from bot.search import embedder

        vetor = embedder.para_sql(embedder.embed_um(texto[:6000]))
        cur.execute(
            f"SELECT {campos} FROM lucien_claims"
            " WHERE topic_id = %s AND status = 'vigente' AND embedding IS NOT NULL"
            " ORDER BY embedding <=> %s::vector LIMIT %s",
            (topic_id, vetor, limite),
        )
        for r in cur.fetchall():
            linhas.setdefault(str(r["id"]), r)
    except Exception:  # noqa: BLE001
        logger.info("lucien: sem a perna de sentido no estado vigente")

    ordenadas = sorted(linhas.values(), key=lambda r: r["valid_from"], reverse=True)
    return {f"E{i}": r for i, r in enumerate(ordenadas[: limite * 3], start=1)}


_RE_LEXEMA = re.compile(r"\'([^\']+)\':(\d+(?:,\d+)*)")


def _radicais_raros(cx, texto: str, *, teto: int = 25) -> str:
    """Os radicais do lote que interessam, ligados por OU.

    DUAS LISTAS, E A PRIMEIRA É A QUE FALTAVA. A versão anterior pegava só os
    radicais mais RAROS do acervo — e isso escolhe *hapax*, não assunto. Medido
    no lote do apagão de 12/06: os 25 mais raros eram `cifr`, `restoring`,
    `crypt`, `pem`, `decompiled`… e **`rsync` não entrava**, porque com 116
    mensagens ele não é raro o bastante. O lote era inteiro sobre rsync.

    O que um lote **repete** é do que ele trata. Então:

    - **por frequência no lote** — os termos que o lote martela, desde que não
      sejam banais no acervo. É esta lista que traz `rsync`;
    - **por raridade no acervo** — identificador, nome próprio, sigla. Aparecem
      uma vez e valem por dez.

    O corte de banalidade continua vindo de `search_lexeme_df` (a estatística da
    F2), porque sem ele o OU traz o acervo inteiro e a relevância vira ruído —
    que é o outro jeito de esta perna morrer.
    """
    cur = cx.cursor()
    # O denominador é o NÚMERO DE DOCUMENTOS, como em `bot/search/query.radicais`
    # — não o `MAX(ndoc)`. Usar o máximo aperta o corte sem querer: medido, ele
    # dava 109 num acervo de 3.600 mensagens, e `rsync` (136 documentos, 3,8% do
    # acervo) caía fora. O termo que o lote inteiro martelava era descartado por
    # um corte calculado sobre a palavra mais comum do acervo.
    cur.execute("SELECT COUNT(*) AS n FROM messages")
    total = int(cur.fetchone()["n"] or 0)
    if total <= 0:
        return ""
    corte = max(3, int(total * 0.05))

    # A representação textual do `tsvector` traz as POSIÇÕES de cada lexema
    # (`'rsync':12,45,88`), e contá-las dá a frequência dentro do lote — que o
    # `tsvector_to_array` joga fora.
    cur.execute("SELECT to_tsvector('portuguese', %s)::text AS v", (texto,))
    vetor = cur.fetchone()["v"] or ""
    freq = {
        m.group(1): m.group(2).count(",") + 1
        for m in _RE_LEXEMA.finditer(vetor)
        if m.group(1).isalnum()
    }
    if not freq:
        return ""

    cur.execute(
        "SELECT word, ndoc FROM search_lexeme_df WHERE word = ANY(%s) AND ndoc <= %s",
        (list(freq), corte),
    )
    uteis = {r["word"]: int(r["ndoc"]) for r in cur.fetchall()}
    if not uteis:
        return ""

    metade = max(1, teto // 2)
    por_frequencia = sorted(uteis, key=lambda w: (-freq.get(w, 0), uteis[w]))[:metade]
    por_raridade = sorted(uteis, key=lambda w: uteis[w])[:metade]

    escolhidos: list[str] = []
    for w in [*por_frequencia, *por_raridade]:
        if w not in escolhidos:
            escolhidos.append(w)
    return " | ".join(escolhidos[:teto])


# ── Escrita: a única porta ───────────────────────────────────────────────


def abrir_rodada(cx, *, mode: str, topic_id: Optional[str], lote: Optional[Lote],
                 model: str) -> str:
    cur = cx.cursor()
    cur.execute(
        "INSERT INTO lucien_runs (mode, topic_id, from_seq, to_seq, messages_seen,"
        " model) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (mode, topic_id,
         lote.de_seq if lote else None,
         lote.ate_seq if lote else None,
         len(lote.mensagens) if lote else 0,
         model or None),
    )
    return str(cur.fetchone()["id"])


def fechar_rodada(cx, run_id: str, r: ResultadoDaRodada) -> None:
    cx.cursor().execute(
        "UPDATE lucien_runs SET finished_at = NOW(), ok = %s, error = %s,"
        " claims_created = %s, claims_superseded = %s, claims_rejected = %s"
        " WHERE id = %s",
        (r.ok, r.erro, r.criadas, r.superadas + r.encerradas, r.rejeitadas, run_id),
    )


def aplicar(cx, lote: Lote, proposta: Proposta, *, run_id: str,
            avancar_cursor: bool = True, scope: str = "incremental",
            truncar: bool = False) -> ResultadoDaRodada:
    """Valida e grava. Chamador é responsável por `commit`/`rollback`.

    A ordem importa: **primeiro cria a afirmação nova, depois fecha a que ela
    supera.** O contrário deixaria, no meio da transação, uma linha superada
    apontando para nada — e se algo estourasse ali, o `rollback` salvaria, mas o
    código teria escrito uma revogação sem substituta, que é o estado que o
    registro nunca pode representar.

    **T7: passar do teto NÃO descarta nada.** A função devolve `excedeu=True`
    sem escrever, e quem chamou parte o lote ao meio (`worker._processar`). O
    comportamento antigo — cortar as últimas da lista — é o pior possível numa
    trava: ela não escolhe, e o que se perde é invisível. Medido no piloto: o
    teto bateu em 5 de 5 lotes e levou 20 afirmações embora, por posição.

    `truncar=True` é o fim da linha: o lote já está no piso e não dá mais para
    dividir. Aí grava o que couber e a recusa sai **ruidosa**, porque nesse ponto
    não é lote grande, é degeneração do modelo.
    """
    r = ResultadoDaRodada(run_id=run_id, mensagens_vistas=len(lote.mensagens))
    teto = cfg.claims_maximo()

    if len(proposta.claims) > teto and not truncar:
        r.excedeu = True
        return r

    por_seq = lote.por_seq
    ja_vale = _chaves_vigentes(cx, lote.topic_id)
    consumidos: set[str] = set()   # apelidos já superados/encerrados nesta rodada

    for prop in proposta.claims[:teto]:
        novo_id = _criar(cx, lote, prop, por_seq, ja_vale, r, run_id)
        if novo_id is None:
            continue
        r.criadas += 1
        for apelido in prop.supersedes:
            if _superar(cx, lote, apelido, novo_id, prop, por_seq, consumidos, r, run_id):
                r.superadas += 1

    excedente = len(proposta.claims) - teto
    if excedente > 0:
        r.recusas.append(Recusa(
            trava="T7",
            motivo=f"DEGENERAÇÃO: o modelo devolveu {len(proposta.claims)} afirmações "
                   f"de um lote de {len(lote.mensagens)} mensagem(ns), que já é o piso "
                   f"de divisão. O teto é {teto} e as {excedente} últimas foram "
                   "descartadas — este é o único caminho em que algo se perde, e "
                   "por isso ele é barulhento.",
        ))

    for fim in proposta.closures:
        if _encerrar(cx, lote, fim, por_seq, consumidos, r, run_id):
            r.encerradas += 1

    if avancar_cursor and lote.ate_seq is not None:
        _avancar_cursor(cx, scope, lote.topic_id, lote.ate_seq)
        r.cursor_avancou_para = lote.ate_seq
    return r


# ── As travas, uma a uma ─────────────────────────────────────────────────


def _criar(cx, lote: Lote, p: ClaimProposta, por_seq: dict[int, Mensagem],
           ja_vale: dict[str, str], r: ResultadoDaRodada, run_id: str) -> Optional[str]:
    # T1 — a trava que sustenta a fase.
    origem = por_seq.get(int(p.source_seq)) if _inteiro(p.source_seq) else None
    if origem is None:
        r.recusas.append(Recusa(
            trava="T1",
            motivo=f"origem #{p.source_seq} não estava no lote mostrado ao modelo "
                   f"(o lote vai de #{lote.de_seq} a #{lote.ate_seq})",
            trecho=p.statement[:120],
        ))
        return None

    # T4 — vocabulário e tamanho.
    erro = _conferir_forma(p)
    if erro:
        r.recusas.append(Recusa(trava="T4", motivo=erro, trecho=p.statement[:120]))
        return None

    # T3 — superação sem motivo é recusada. A afirmação em si até poderia
    # entrar, mas entraria carregando uma superação sem justificativa, e é
    # justamente a justificativa que torna a superação auditável.
    if p.supersedes and not (p.supersede_reason or "").strip():
        r.recusas.append(Recusa(
            trava="T3",
            motivo="superação proposta sem motivo escrito",
            trecho=p.statement[:120],
        ))
        return None

    # T8 — dedupe. Rodar de novo sobre o mesmo assunto não pode duplicar.
    chave = (slug(p.subject), _chave_dedupe(p.statement))
    if chave in ja_vale:
        r.recusas.append(Recusa(
            trava="T8",
            motivo="já existe uma afirmação vigente igual a esta neste tópico",
            trecho=p.statement[:120],
        ))
        return None

    # T5 — a data do FATO.
    valid_from = origem.created_at

    # A evidência extra: só os `seq` que estavam no lote. Calculada ANTES do
    # INSERT porque é ela que define a confiança (T6).
    extras = {
        int(s) for s in p.evidence_seqs
        if _inteiro(s) and int(s) in por_seq and int(s) != origem.seq
    }

    # T6 — a confiança sai de CORROBORAÇÃO, não do canal.
    #
    # A versão anterior fazia `"baixa" if origem.audio else "media"`, o que media
    # o CANAL e não a confiabilidade da afirmação. O operador usa áudio como
    # canal principal, então "baixa" saía em 27 de 40 linhas do piloto — e um
    # sinal que aparece em dois terços das linhas não distingue nada. Pior: ou o
    # agente hedgeia tudo, e a fase não cura a doença que existe pra curar, ou
    # ignora a flag, e a mitigação vira teatro.
    #
    # O rebaixamento por ilegibilidade é o único julgamento que o modelo dá, e
    # ele **só desce**. Nunca sobe: um modelo não pode se auto-promover a "alta"
    # dizendo que está seguro. O princípio da 008 continua valendo onde importa.
    confianca = "alta" if extras else "media"
    if p.legibility_doubt:
        confianca = "baixa"

    cur = cx.cursor()
    cur.execute(
        "INSERT INTO lucien_claims (topic_id, subject, subject_slug, statement,"
        " kind, status, confidence, valid_from, source_message_id, source_seq,"
        " created_by, run_id)"
        " VALUES (%s,%s,%s,%s,%s,'vigente',%s,%s,%s,%s,'lucien',%s) RETURNING id",
        (lote.topic_id, p.subject.strip(), slug(p.subject), p.statement.strip(),
         p.kind, confianca, valid_from, origem.id, origem.seq, run_id),
    )
    novo_id = str(cur.fetchone()["id"])
    ja_vale[chave] = novo_id

    # `seq` de evidência fora do lote é descartado em silêncio de propósito:
    # evidência a mais não é afirmação a mais, e recusar a afirmação inteira por
    # causa de um número extra seria desperdício.
    for s in sorted(extras):
        cur.execute(
            "INSERT INTO lucien_claim_evidence (claim_id, message_id, seq)"
            " VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (novo_id, por_seq[s].id, s),
        )

    _evento(cx, novo_id, "created", None,
            {"source_seq": origem.seq, "audio": origem.audio,
             "evidencia": sorted(extras), "confianca": confianca,
             "legibility_doubt": bool(p.legibility_doubt),
             "legibility_reason": (p.legibility_reason or "").strip()[:300]},
            run_id)
    return novo_id


def _superar(cx, lote: Lote, apelido: str, novo_id: str, p: ClaimProposta,
             por_seq: dict[int, Mensagem], consumidos: set[str],
             r: ResultadoDaRodada, run_id: str) -> bool:
    # T2 — só apelido MOSTRADO.
    alvo = lote.estado.get(str(apelido).strip())
    if alvo is None:
        r.recusas.append(Recusa(
            trava="T2",
            motivo=f"superação de {apelido!r}, que não estava entre as afirmações "
                   "mostradas ao modelo",
            trecho=p.statement[:120],
        ))
        return False
    if str(apelido) in consumidos:
        r.recusas.append(Recusa(
            trava="T2",
            motivo=f"{apelido} já havia sido superada/encerrada nesta mesma rodada",
            trecho=p.statement[:120],
        ))
        return False

    antes = _imagem(cx, str(alvo["id"]))
    if antes is None or antes["status"] != "vigente":
        r.recusas.append(Recusa(
            trava="T2",
            motivo=f"{apelido} não está mais vigente — outra rodada já a mudou",
            trecho=p.statement[:120],
        ))
        return False

    origem = por_seq[int(p.source_seq)]
    cur = cx.cursor()
    cur.execute(
        "UPDATE lucien_claims SET status='superada', valid_to=%s, superseded_by=%s,"
        " updated_at=NOW() WHERE id=%s AND status='vigente'",
        (origem.created_at, novo_id, alvo["id"]),
    )
    if cur.rowcount != 1:
        r.recusas.append(Recusa(
            trava="T2", motivo=f"{apelido} mudou de estado no meio da rodada"))
        return False

    consumidos.add(str(apelido))
    _evento(cx, str(alvo["id"]), "superseded", antes,
            {"por": novo_id, "motivo": p.supersede_reason.strip(),
             "source_seq": origem.seq}, run_id)
    return True


def _encerrar(cx, lote: Lote, fim: Encerramento, por_seq: dict[int, Mensagem],
              consumidos: set[str], r: ResultadoDaRodada, run_id: str) -> bool:
    if fim.action not in cfg.ENCERRAMENTOS:
        r.recusas.append(Recusa(trava="T4", motivo=f"ação de encerramento inválida: {fim.action!r}"))
        return False
    alvo = lote.estado.get(str(fim.apelido).strip())
    if alvo is None or str(fim.apelido) in consumidos:
        r.recusas.append(Recusa(
            trava="T2",
            motivo=f"encerramento de {fim.apelido!r}, que não estava mostrada "
                   "(ou já foi mudada nesta rodada)",
        ))
        return False
    origem = por_seq.get(int(fim.source_seq)) if _inteiro(fim.source_seq) else None
    if origem is None:
        r.recusas.append(Recusa(
            trava="T1",
            motivo=f"encerramento citando #{fim.source_seq}, fora do lote mostrado",
        ))
        return False
    if not (fim.reason or "").strip():
        r.recusas.append(Recusa(
            trava="T3", motivo=f"encerramento de {fim.apelido} sem motivo escrito"))
        return False

    antes = _imagem(cx, str(alvo["id"]))
    if antes is None or antes["status"] != "vigente":
        r.recusas.append(Recusa(trava="T2", motivo=f"{fim.apelido} não está mais vigente"))
        return False

    novo_status = "fechada" if fim.action == "closed" else "abandonada"
    cur = cx.cursor()
    cur.execute(
        "UPDATE lucien_claims SET status=%s, valid_to=%s, updated_at=NOW()"
        " WHERE id=%s AND status='vigente'",
        (novo_status, origem.created_at, alvo["id"]),
    )
    if cur.rowcount != 1:
        r.recusas.append(Recusa(trava="T2", motivo=f"{fim.apelido} mudou no meio da rodada"))
        return False

    consumidos.add(str(fim.apelido))
    _evento(cx, str(alvo["id"]), fim.action, antes,
            {"motivo": fim.reason.strip(), "source_seq": origem.seq}, run_id)
    return True


# ── Reversão: o caminho de volta é um comando ────────────────────────────


def reverter(cx, event_id: str) -> dict:
    """Desfaz UM evento, devolvendo a linha à imagem que `before` guardou.

    Existe porque superação errada é o modo de falha mais caro desta fase: ela
    **esconde** uma decisão que continua valendo, e o operador não tem como
    saber que ela sumiu. Um `UPDATE` à mão desfaria — e não deixaria rastro de
    que foi desfeito.
    """
    cur = cx.cursor()
    cur.execute("SELECT * FROM lucien_events WHERE id = %s", (event_id,))
    ev = cur.fetchone()
    if ev is None:
        raise LookupError(f"evento {event_id} não existe")
    if ev["action"] == "reverted":
        raise ValueError("este evento já é uma reversão — não se reverte reversão")
    if not ev["before"]:
        raise ValueError(
            f"o evento {event_id} é um '{ev['action']}' e não tem imagem anterior "
            "(uma afirmação criada se desfaz por encerramento, não por reversão)"
        )

    antes = ev["before"]
    agora = _imagem(cx, str(ev["claim_id"]))
    cur.execute(
        "UPDATE lucien_claims SET status=%s, valid_to=%s, superseded_by=%s,"
        " updated_at=NOW() WHERE id=%s",
        (antes["status"], antes.get("valid_to"), antes.get("superseded_by"),
         ev["claim_id"]),
    )
    _evento(cx, str(ev["claim_id"]), "reverted", agora,
            {"evento_revertido": str(event_id)}, None, actor="operador")
    return antes


# ── Miudezas ─────────────────────────────────────────────────────────────


def _conferir_forma(p: ClaimProposta) -> Optional[str]:
    if p.kind not in cfg.KINDS:
        return f"kind {p.kind!r} fora do vocabulário {cfg.KINDS}"
    s = (p.statement or "").strip()
    if not (cfg.STATEMENT_MIN <= len(s) <= cfg.STATEMENT_MAX):
        return (f"afirmação com {len(s)} caracteres (limite "
                f"{cfg.STATEMENT_MIN}–{cfg.STATEMENT_MAX})")
    a = (p.subject or "").strip()
    if not (cfg.SUBJECT_MIN <= len(a) <= cfg.SUBJECT_MAX):
        return (f"assunto com {len(a)} caracteres (limite "
                f"{cfg.SUBJECT_MIN}–{cfg.SUBJECT_MAX})")
    return None


def _inteiro(v: Any) -> bool:
    try:
        int(v)
        return True
    except (TypeError, ValueError):
        return False


def _chaves_vigentes(cx, topic_id: str) -> dict[tuple, str]:
    cur = cx.cursor()
    cur.execute(
        "SELECT id, subject_slug, statement FROM lucien_claims"
        " WHERE topic_id = %s AND status = 'vigente'",
        (topic_id,),
    )
    return {
        (r["subject_slug"], _chave_dedupe(r["statement"])): str(r["id"])
        for r in cur.fetchall()
    }


def _imagem(cx, claim_id: str) -> Optional[dict]:
    """A linha como ela está agora, em JSON — é o que vira `before`."""
    cur = cx.cursor()
    cur.execute(
        "SELECT id, status, valid_to, superseded_by, statement, subject, kind,"
        "       confidence, valid_from, source_seq"
        "  FROM lucien_claims WHERE id = %s",
        (claim_id,),
    )
    linha = cur.fetchone()
    if linha is None:
        return None
    return json.loads(json.dumps(linha, default=str))


def _evento(cx, claim_id: str, action: str, before: Optional[dict],
            detail: Optional[dict], run_id: Optional[str],
            actor: str = "lucien") -> None:
    import psycopg

    cx.cursor().execute(
        "INSERT INTO lucien_events (claim_id, action, before, detail, actor, run_id)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
        (claim_id, action,
         psycopg.types.json.Jsonb(before) if before else None,
         psycopg.types.json.Jsonb(detail) if detail else None,
         actor, run_id),
    )


def _avancar_cursor(cx, scope: str, topic_id: str, last_seq: int) -> None:
    cx.cursor().execute(
        "INSERT INTO lucien_cursor (scope, topic_id, last_seq) VALUES (%s,%s,%s)"
        " ON CONFLICT (scope, topic_id) DO UPDATE"
        "   SET last_seq = GREATEST(lucien_cursor.last_seq, EXCLUDED.last_seq),"
        "       updated_at = NOW()",
        (scope, topic_id, last_seq),
    )
