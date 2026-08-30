"""As três pernas da busca, a fusão, e o piso do "não tenho registro".

LEIA `bot/search/__init__.py` PRIMEIRO — ele explica por que são três pernas e,
principalmente, **por que a perna de palavra não vota sobre existir**. O resumo
que importa aqui:

    existe = (sentido acima do piso) OU (a perna literal achou o identificador)

A perna de palavra ordena e não vota. Isso não é conservadorismo: foi medido.
Sobre 16 perguntas (8 com resposta, 8 sobre assuntos que nunca existiram), a
massa de IDF da perna de palavra devolveu **zero** para duas perguntas legítimas
e **entre 7,5 e 9** para quatro perguntas sobre assuntos inexistentes — porque
"Japão", "piano" e "maratona" existem no acervo, soltos, em contextos que não
têm nada a ver. Raridade não é relevância.

OS QUATRO DESFECHOS
-------------------
`ACHOU`                     — a busca por sentido passou do piso.
`MENCAO_LITERAL`            — o token exato aparece, e o sentido não passou do
                              piso. A perna literal responde *"a palavra
                              aparece"*, não *"existe decisão sobre isso"*:
                              `Japão` dá 7 ocorrências soltas no acervo.
                              **É só isto que este desfecho afirma.** Ele NÃO
                              afirma que nada responde — essa versão existiu, e
                              a 3ª execução da bateria a pegou dizendo "nada
                              aqui responde" sobre um conjunto que respondia.
                              Quem lê recebe os trechos com a nota de cada um e
                              julga; e não costura menção solta em resposta.
`SEM_REGISTRO`              — procurou e não há. É a trava anti-invenção.
`FALHA`                     — **não deu pra saber**. Banco fora, ou o serviço de
                              embedding não respondeu. Nunca é "não há registro".

O quarto desfecho existe porque este sistema já cometeu o falso negativo
silencioso duas vezes (F0.5-B e o `kobe-reflect` de 29/08): uma falha de
instrumento virou, na boca do agente, *"não há registro disso"*.

OS PISOS SÃO CONFIGURÁVEIS DE PROPÓSITO
----------------------------------------
Eles envelhecem conforme o acervo cresce — a folga medida entre "achou" e "não
achou" é de 0,061, e é honesto dizer que ela é apertada. Por isso os dois moram
no `.env` e há `bot/search/calibrar.py` para remedi-los sobre o acervo do dia.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from bot.search import embedder

# Piso de similaridade da busca por sentido. Medido: perguntas COM resposta
# ficam em 0,598–0,693 e perguntas SEM resposta em 0,428–0,536. Qualquer valor
# entre 0,537 e 0,597 acerta as 16; 0,57 é o meio da folga.
PISO_COS_PADRAO = 0.57

# Acima desta fração do acervo, um radical é banal e sai da consulta. Medido no
# acervo real, por mensagem: "a gente" 24%, "sobre" 23%, "conversa" 16%.
DF_MAX_PADRAO = 0.05

# ...mas nenhuma pergunta pode ficar SEM termo. Duas perguntas legítimas da
# bancada tiraram zero por terem todos os radicais acima do corte. Os N mais
# raros entram sempre; como a pontuação é por IDF, um termo marginal que entre
# por esta porta pesa pouco de qualquer forma.
RAROS_SEMPRE = 3

# Até quantas vezes o corte um termo pode passar e ainda ser repescado. Com 2,
# o corte de 5% repesca até 10% — o que salva "arquitetura" (5,2%) e continua
# barrando "a gente" (24%) e "sobre" (23%).
FATOR_REPESCA = 2.0

# Quantos candidatos cada perna traz antes da fusão.
K_PERNA = 30

# Tamanho mínimo de uma palavra crua para ela virar busca literal. Abaixo disso
# ("dia", "sim") o casamento por substring vira ruído dentro de outras palavras.
MIN_LITERAL = 4

# Quanto tempo, em segundos, uma mensagem precisa ter para contar como PASSADO.
#
# Isto não é conservadorismo: o bot grava a mensagem do operador em `messages`
# ANTES de rodar o turno. Sem esta janela, a pergunta que ele acabou de fazer é
# encontrada pela busca literal e a ferramenta responde com **a própria
# pergunta** — visto ao vivo na bateria da F2, no cenário do `compat_gate`. O
# agente daquele turno percebeu sozinho e disse "o único trecho é a tua própria
# mensagem de agora, que obviamente não conta" — mas depender de ele perceber é
# exatamente o tipo de garantia que este projeto não aceita.
JANELA_ECO_S = 90.0

# Acima desta similaridade, o "resultado" é a PRÓPRIA PERGUNTA e não uma
# resposta a ela.
#
# A janela de eco acima cobre a pergunta do turno atual. Ela NÃO cobre o caso
# estrutural, que a 3ª execução da bateria expôs: uma pergunta já feita antes
# — dez minutos ou dez meses — está gravada em `messages`, e a semelhança de
# uma pergunta com ela mesma é ~1. Medido naquele turno: as duas repetições da
# pergunta vieram em 1º e 2º lugar com **0,825**, contra **0,614** do melhor
# resultado de verdade, e sobraram 3 vagas úteis de 8.
#
# O teto é **0,75**, e ele fica entre dois números medidos, não escolhido a olho:
#
#   melhor resultado VERDADEIRO em todo o acervo, nas 16 perguntas ...  0,693
#   ---------------------------------- 0,75 ----------------------------------
#   eco observado (a mesma pergunta, feita antes, se reencontrando) ...  0,825
#
# A primeira versão deste teto foi 0,90 — escolhida "com margem de sobra" sobre
# o 0,693 e **sem olhar o número do outro lado**. Não teria pego o caso real.
# Um limiar sem os dois lados medidos é chute com aparência de critério.
TETO_ECO_COS = 0.75

# Identificador: tem separador interno (`compat_gate`, `working_set.py`,
# `kobe-recall-since`, `bot/db.py`) ou é uma sigla em caixa alta
# (`HINDSIGHT_RECALL`, `SQL`).
_RE_IDENT = re.compile(
    r"[A-Za-zÀ-ÿ0-9]+(?:[_./-][A-Za-zÀ-ÿ0-9]+)+"
    r"|\b[A-Z][A-Z0-9_]{2,}\b"
)

# Nome próprio: capitalizado no MEIO da frase. No começo não serve — toda frase
# começa com maiúscula, e "Quando" viraria nome próprio.
_RE_PROPRIO = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-ZÀ-Þ][a-zà-ÿ]{3,})\b")

_APOS = chr(39)


def piso_cos() -> float:
    try:
        return float(os.environ.get("SEARCH_PISO_COS") or PISO_COS_PADRAO)
    except ValueError:
        return PISO_COS_PADRAO


def df_max() -> float:
    try:
        return float(os.environ.get("SEARCH_DF_MAX") or DF_MAX_PADRAO)
    except ValueError:
        return DF_MAX_PADRAO


# ── O que sai da pergunta ─────────────────────────────────────────────────


def literais(pergunta: str) -> list[str]:
    """Os pedaços que têm que ser procurados LETRA POR LETRA.

    São os que o dicionário `portuguese` destrói: `kobe-recall-since` vira
    `kobe-recall-sinc` + `recall` + `sinc`, e aí `sinc` casa com "sincronizar".
    """
    achados: list[str] = []
    for m in _RE_IDENT.finditer(pergunta):
        achados.append(m.group(0))
    for m in _RE_PROPRIO.finditer(pergunta):
        achados.append(m.group(1))
    # Sem duplicata, preservando a ordem em que apareceram na pergunta.
    vistos: set[str] = set()
    saida = []
    for t in achados:
        if t.lower() not in vistos:
            vistos.add(t.lower())
            saida.append(t)
    return saida


_RE_PALAVRA = re.compile(r"[A-Za-zÀ-ÿ0-9]{%d,}" % MIN_LITERAL)


def literais_raros(db, pergunta: str) -> list[str]:
    """Palavras CRUAS da pergunta que são raras no acervo — busca literal.

    POR QUE ISTO EXISTE (e é conserto, não refinamento)
    ---------------------------------------------------
    A perna de palavra foi desenhada para **não votar** sobre existência, com
    medição boa: ela dá falso positivo em "Japão", "piano" e "maratona". Mas
    todas as 16 perguntas com que eu medi isso eram **frases**.

    Quando a busca é um **termo cru** — `kobe-remember "rsync"` — o termo *é* a
    pergunta. Uma frase de uma palavra embeda mal, a busca por sentido fica
    abaixo do piso, e não sobrava ninguém para votar: o comando respondia
    **`SEM REGISTRO`** para um termo que está em **116 mensagens**. E `SEM
    REGISTRO` é justamente o carimbo que o `CLAUDE.md` manda tratar como
    afirmável — ou seja, o selo mais forte da ferramenta estava mentindo. Achado
    ao vivo, pelo próprio agente, durante a bateria da F2.

    O conserto é uniforme, sem modo especial para consulta curta: **toda palavra
    da pergunta que for rara no acervo entra na busca literal**. O corte de
    raridade é o mesmo da perna de palavra, e é ele que impede "a gente" (24%) e
    "sobre" (23%) de virarem busca literal casando com tudo.

    O preço, dito na cara: perguntas cujo assunto não existe mas que contêm uma
    palavra rara que existe solta ("piano", 2 mensagens) passam de `SEM
    REGISTRO` para `MENÇÃO LITERAL SEM APOIO`. É uma recusa mais fraca, porém
    **honesta** — e um `SEM REGISTRO` falso é muito pior que uma menção literal
    conservadora, porque só o primeiro autoriza o agente a afirmar.
    """
    cruas = sorted({m.group(0) for m in _RE_PALAVRA.finditer(pergunta)})
    if not cruas:
        return []
    n = int(db.scalar("SELECT COUNT(*) FROM messages") or 0)
    if n <= 0:
        return []
    # Uma consulta só mapeia cada palavra crua ao radical dela — é o radical que
    # tem estatística, e é a palavra crua que a busca literal precisa.
    linhas = db.query(
        "SELECT w, regexp_replace(to_tsvector('portuguese', w)::text,"
        "                         ':[0-9,A-D]+', '', 'g') AS lex"
        "  FROM unnest(%s::text[]) AS w",
        (cruas,),
    )
    lex_de = {
        l["w"]: (l["lex"] or "").strip().strip(_APOS)
        for l in linhas
        if (l["lex"] or "").strip()
    }
    if not lex_de:
        return []
    dfs = {
        l["word"]: int(l["ndoc"])
        for l in db.query(
            "SELECT word, ndoc FROM search_lexeme_df WHERE word = ANY(%s)",
            (sorted(set(lex_de.values())),),
        )
    }
    teto = max(1.0, n * df_max())
    return [w for w in cruas if w in lex_de and dfs.get(lex_de[w], 0) <= teto]


def radicais(db, pergunta: str) -> tuple[dict[str, float], list[str], list[str]]:
    """Os radicais úteis (com o peso de cada um), os BANAIS e os AUSENTES.

    O peso é IDF — `ln(N/df)` —, o mesmo conceito de seletividade que decide se
    o planejador do Postgres usa índice ou faz varredura. Um radical presente em
    24% do acervo tem seletividade péssima e não ajuda a achar nada.

    **Banal e ausente são coisas diferentes, e a distinção não é cosmética.**
    Um radical que sai por ser comum demais ("a gente") diz *"isto não ajuda a
    discriminar"*; um que sai por não existir no acervo ("salesforc") diz *"isto
    nunca foi dito"* — que é quase a resposta à pergunta. Juntar os dois num
    balde só faz a saída afirmar que "Salesforce é comum demais no acervo", o
    oposto exato da verdade.
    """
    linhas = db.query(
        "SELECT unnest(string_to_array(regexp_replace("
        "  to_tsvector('portuguese', %s)::text, ':[0-9,A-D]+', '', 'g'), ' ')) AS w",
        (pergunta,),
    )
    termos = sorted({(l["w"] or "").strip(_APOS) for l in linhas if (l["w"] or "").strip(_APOS)})
    if not termos:
        return {}, [], []

    n = int(db.scalar("SELECT COUNT(*) FROM messages") or 0)
    if n <= 0:
        return {}, termos, []

    dfs = {
        l["word"]: int(l["ndoc"])
        for l in db.query(
            "SELECT word, ndoc FROM search_lexeme_df WHERE word = ANY(%s)", (termos,)
        )
    }
    # Radical ausente da estatística é raríssimo (ou o acervo mudou depois do
    # último recálculo). Tratar como df=1 é o certo: ele É seletivo.
    presentes = {t: dfs[t] for t in termos if t in dfs}
    ausentes = [t for t in termos if t not in dfs]
    if not presentes:
        return {}, [], ausentes

    teto = max(1.0, n * df_max())
    uteis = {t: d for t, d in presentes.items() if d <= teto}
    if uteis:
        # Repesca os N mais raros que ficaram de fora POR POUCO. O caso que
        # motiva: "arquitetura" está em 5,2% do acervo e o corte é 5% — ela é
        # sinal legítimo e cairia por dois décimos.
        #
        # O teto da repesca (`FATOR_REPESCA`) não é enfeite: sem ele, "os N mais
        # raros" de uma pergunta curta são simplesmente "os N termos que ela
        # tem", e `a gente` (24%) e `sobre` (23%) voltavam pela porta dos
        # fundos — desfazendo exatamente o corte que acabou de acontecer.
        limite_repesca = teto * FATOR_REPESCA
        for t, d in sorted(presentes.items(), key=lambda kv: kv[1])[:RAROS_SEMPRE]:
            if d <= limite_repesca:
                uteis.setdefault(t, d)
    # E se NENHUM termo passou do corte, a perna de palavra fica de fora desta
    # pergunta — de propósito. A tentação é repescar os "menos comuns" mesmo
    # assim, e ela produz o oposto do que se quer: em *"o que a gente falou
    # sobre o working_set.py"* os três menos comuns são `sobre`, `a gente` e
    # `falou`, que estão em 23%, 24% e 14% do acervo. Isso reintroduz
    # exatamente o ruído que o corte existe pra remover. Perna vazia é a
    # resposta honesta: quem carrega a pergunta é a literal e a de sentido, e
    # nenhuma das duas depende desta.

    pesos = {t: math.log(n / max(1, d)) for t, d in uteis.items()}
    banais = [t for t in presentes if t not in pesos]
    return pesos, banais, ausentes


# ── Os resultados ─────────────────────────────────────────────────────────


@dataclass
class Achado:
    seq: int
    message_id: str
    role: str
    topico: str
    created_at: str
    corpo: str
    cos: Optional[float] = None
    idf: float = 0.0
    literal: bool = False
    rrf: float = 0.0
    pernas: list[str] = field(default_factory=list)


@dataclass
class Resultado:
    veredito: str                     # ACHOU | MENCAO_LITERAL | SEM_REGISTRO | FALHA
    achados: list[Achado] = field(default_factory=list)
    radicais_uteis: list[str] = field(default_factory=list)
    radicais_banais: list[str] = field(default_factory=list)
    radicais_ausentes: list[str] = field(default_factory=list)
    literais: list[str] = field(default_factory=list)
    literais_ausentes: list[str] = field(default_factory=list)
    cos_topo: Optional[float] = None
    janela_eco_s: float = 0.0
    ignoradas_pelo_eco: int = 0
    ecos_descartados: int = 0
    sentido_ativo: bool = True
    motivo_sentido_fora: Optional[str] = None
    pendencia: dict = field(default_factory=dict)
    erro: Optional[str] = None

    @property
    def parcial(self) -> bool:
        """Um "não achei" SEM a árbitra é parcial, e tem que ser dito assim."""
        return not self.sentido_ativo


# ── As três pernas ────────────────────────────────────────────────────────

_SELECT = (
    "SELECT m.seq, m.id AS message_id, m.role, m.created_at,"
    "       COALESCE(t.current_name, '?') AS topico"
)


def _filtros(
    topic_id: Optional[str] = None,
    desde: Optional[str] = None,
    ate: Optional[str] = None,
    janela_eco: float = JANELA_ECO_S,
) -> tuple[str, list]:
    """Os predicados opcionais, iguais nas três pernas.

    Montados num lugar só de propósito: um recorte de data que valesse para duas
    pernas e não para a terceira produziria um resultado que mistura períodos
    diferentes — e ninguém perceberia, porque cada linha continua verdadeira
    sozinha.
    """
    sql, params = "", []
    if topic_id:
        sql += " AND m.topic_id = %s"
        params.append(topic_id)
    if desde:
        sql += " AND m.created_at >= %s"
        params.append(desde)
    if ate:
        sql += " AND m.created_at <= %s"
        params.append(ate)
    if janela_eco and janela_eco > 0:
        sql += " AND m.created_at <= NOW() - make_interval(secs => %s)"
        params.append(float(janela_eco))
    return sql, params


def buscar_literal(
    db, tokens: list[str], *, k=K_PERNA, **recorte
) -> tuple[list[dict], list[str]]:
    """A perna que acha o token escrito, letra por letra. Usa o índice trigrama.

    Devolve `(linhas, tokens_sem_nenhum_acerto)`. A segunda metade não é
    diagnóstico decorativo — é ela que decide se esta perna tem voto (§ abaixo).
    """
    if not tokens:
        return [], []
    onde, params = _filtros(**recorte)
    saida: list[dict] = []
    vistos: set[int] = set()
    sem_acerto: list[str] = []
    for token in tokens:
        linhas = db.query(
            f"{_SELECT} FROM messages m LEFT JOIN topics t ON t.id = m.topic_id"
            f" WHERE m.content ILIKE %s{onde}"
            f" ORDER BY m.seq DESC LIMIT %s",
            ["%" + token + "%", *params, k],
        )
        if not linhas:
            sem_acerto.append(token)
            continue
        for l in linhas:
            if l["seq"] in vistos:
                continue
            vistos.add(l["seq"])
            saida.append({**l, "token": token})
    return saida, sem_acerto


def buscar_palavra(db, pesos: dict[str, float], *, k=K_PERNA, **recorte) -> list[dict]:
    """A perna por palavra, pontuada por raridade. ORDENA; não vota."""
    if not pesos:
        return []
    tq = " | ".join(f"{_APOS}{w}{_APOS}" for w in pesos)
    soma = " + ".join(
        f"(CASE WHEN m.search_tsv @@ {_APOS}{w}{_APOS}::tsquery THEN {idf:.4f} ELSE 0 END)"
        for w, idf in pesos.items()
    )
    onde, params = _filtros(**recorte)
    return db.query(
        f"{_SELECT}, ({soma}) AS idf,"
        f"       ts_rank_cd(m.search_tsv, %s::tsquery, 32) AS tsr"
        f"  FROM messages m LEFT JOIN topics t ON t.id = m.topic_id"
        f" WHERE m.search_tsv @@ %s::tsquery{onde}"
        f" ORDER BY idf DESC, tsr DESC LIMIT %s",
        [tq, tq, *params, k],
    )


def buscar_sentido(db, vetor: list[float], *, k=K_PERNA, **recorte) -> list[dict]:
    """A perna por sentido — a ÚNICA que vota sobre existir.

    Varredura exata, sem índice aproximado: no acervo de hoje custa ~67 ms e
    devolve o mesmo topo que o HNSW. É a exatidão que sustenta o piso — um
    vizinho perdido pela busca aproximada viraria uma recusa falsa.
    """
    v = embedder.para_sql(vetor)
    onde, params = _filtros(**recorte)
    return db.query(
        f"{_SELECT}, c.body, 1 - (c.embedding <=> %s::vector) AS cos"
        f"  FROM message_chunks c"
        f"  JOIN messages m ON m.id = c.message_id"
        f"  LEFT JOIN topics t ON t.id = m.topic_id"
        f" WHERE c.embedding IS NOT NULL{onde}"
        f" ORDER BY c.embedding <=> %s::vector LIMIT %s",
        [v, *params, v, k],
    )


# ── Fusão ─────────────────────────────────────────────────────────────────

RRF_K = 60


def _fundir(listas: list[tuple[str, list[dict]]]) -> dict[int, Achado]:
    """Reciprocal Rank Fusion, no nível da MENSAGEM.

    RRF em vez de somar notas normalizadas porque as três pernas produzem
    grandezas incomparáveis — cosseno, massa de IDF e "achou/não achou". Somar
    isso exigiria calibrar três escalas; RRF só precisa da ORDEM, que é o que
    cada perna sabe produzir bem.
    """
    por_msg: dict[int, Achado] = {}
    for nome, linhas in listas:
        for posicao, l in enumerate(linhas, start=1):
            seq = l["seq"]
            a = por_msg.get(seq)
            if a is None:
                a = Achado(
                    seq=seq,
                    message_id=str(l["message_id"]),
                    role=l["role"],
                    topico=l["topico"],
                    created_at=str(l["created_at"]),
                    corpo=(l.get("body") or "").strip(),
                )
                por_msg[seq] = a
            a.rrf += 1.0 / (RRF_K + posicao)
            if nome not in a.pernas:
                a.pernas.append(nome)
            if nome == "sentido":
                cos = float(l["cos"])
                if a.cos is None or cos > a.cos:
                    a.cos = cos
                    a.corpo = (l.get("body") or a.corpo).strip()
            elif nome == "palavra":
                a.idf = max(a.idf, float(l.get("idf") or 0.0))
            elif nome == "literal":
                a.literal = True
    return por_msg


def _corpo_de_exibicao(db, achados: list[Achado], pesos: dict[str, float]) -> None:
    """Preenche o trecho a mostrar para quem veio sem um (palavra e literal).

    Mostra o trecho que casa com a consulta, não o começo da mensagem: numa
    mensagem de 6 mil caracteres o começo raramente é o que interessa.
    """
    faltando = [a for a in achados if not a.corpo]
    if not faltando:
        return
    ids = [a.message_id for a in faltando]
    tq = " | ".join(f"{_APOS}{w}{_APOS}" for w in pesos) if pesos else None
    if tq:
        linhas = db.query(
            "SELECT DISTINCT ON (c.message_id) c.message_id, c.body"
            "  FROM message_chunks c"
            " WHERE c.message_id = ANY(%s)"
            " ORDER BY c.message_id,"
            "          (to_tsvector('portuguese', c.body) @@ %s::tsquery) DESC, c.idx",
            (ids, tq),
        )
    else:
        linhas = db.query(
            "SELECT DISTINCT ON (c.message_id) c.message_id, c.body"
            "  FROM message_chunks c WHERE c.message_id = ANY(%s)"
            " ORDER BY c.message_id, c.idx",
            (ids,),
        )
    mapa = {str(l["message_id"]): (l["body"] or "").strip() for l in linhas}

    # Mensagem ainda SEM trecho (o indexador não passou por ela) não pode sair
    # com citação vazia — uma citação em branco é pior que citação nenhuma: ela
    # ocupa a vaga de um resultado e não diz nada. Cai no conteúdo cru.
    orfas = [a.message_id for a in faltando if not mapa.get(a.message_id)]
    if orfas:
        cruas = db.query(
            "SELECT id, content FROM messages WHERE id = ANY(%s)", (orfas,)
        )
        for l in cruas:
            mapa[str(l["id"])] = (l["content"] or "").strip()

    for a in faltando:
        a.corpo = mapa.get(a.message_id, "")


# ── A busca ───────────────────────────────────────────────────────────────


def buscar(
    db,
    pergunta: str,
    *,
    topic_id=None,
    desde: Optional[str] = None,
    ate: Optional[str] = None,
    janela_eco: float = JANELA_ECO_S,
    limite: int = 8,
) -> Resultado:
    """A busca inteira, com o veredito. Nunca levanta por falha de instrumento.

    Falha vira `veredito="FALHA"` com o motivo — porque quem lê isto é um agente
    que, diante de vazio, diria "não há registro". A distinção entre *não tem* e
    *não deu pra saber* é o produto principal desta função.
    """
    from bot.search import indexer

    r = Resultado(veredito="SEM_REGISTRO")

    try:
        toks = literais(pergunta)
        vistos = {t.lower() for t in toks}
        toks += [w for w in literais_raros(db, pergunta) if w.lower() not in vistos]
        pesos, banais, ausentes = radicais(db, pergunta)
        r.literais = toks
        r.radicais_uteis = sorted(pesos, key=lambda w: -pesos[w])
        r.radicais_banais = banais
        r.radicais_ausentes = ausentes

        recorte = {
            "topic_id": topic_id,
            "desde": desde,
            "ate": ate,
            "janela_eco": janela_eco,
        }
        lit, lit_sem_acerto = buscar_literal(db, toks, **recorte)
        r.literais_ausentes = lit_sem_acerto
        pal = buscar_palavra(db, pesos, **recorte)
    except Exception as exc:  # noqa: BLE001 — banco fora não é "não há registro"
        r.veredito = "FALHA"
        r.erro = f"o banco não respondeu: {type(exc).__name__}: {exc}"
        return r

    sem: list[dict] = []
    try:
        vetor = embedder.embed_um(pergunta)
        sem = buscar_sentido(db, vetor, **recorte)
    except embedder.EmbeddingIndisponivel as exc:
        r.sentido_ativo = False
        r.motivo_sentido_fora = str(exc)
    except Exception as exc:  # noqa: BLE001
        r.veredito = "FALHA"
        r.erro = f"a busca por sentido falhou: {type(exc).__name__}: {exc}"
        return r

    try:
        r.pendencia = indexer.pendencia(db)
    except Exception:  # noqa: BLE001 — diagnóstico não pode derrubar a busca
        r.pendencia = {}

    # Quantas mensagens a janela de eco escondeu. Sai na saída: esconder algo em
    # silêncio é o mesmo defeito, de outro lado.
    r.janela_eco_s = float(janela_eco or 0.0)
    if r.janela_eco_s > 0:
        try:
            r.ignoradas_pelo_eco = int(
                db.scalar(
                    "SELECT COUNT(*) FROM messages m"
                    " WHERE m.created_at > NOW() - make_interval(secs => %s)",
                    (r.janela_eco_s,),
                )
                or 0
            )
        except Exception:  # noqa: BLE001
            r.ignoradas_pelo_eco = 0

    # Fora os que são a própria pergunta (vide TETO_ECO_COS). Feito ANTES de
    # calcular o topo: senão a pergunta repetida vira o `cos_topo` e o veredito
    # passa a ser decidido pelo eco.
    if sem and janela_eco:
        antes = len(sem)
        sem = [l for l in sem if float(l["cos"]) < TETO_ECO_COS]
        r.ecos_descartados = antes - len(sem)
    if sem:
        r.cos_topo = max(float(l["cos"]) for l in sem)

    por_msg = _fundir([("literal", lit), ("palavra", pal), ("sentido", sem)])
    achados = sorted(por_msg.values(), key=lambda a: -a.rrf)

    # ── O veredito. Vide `bot/search/__init__.py`: a perna de palavra NÃO vota.
    passou_sentido = r.cos_topo is not None and r.cos_topo >= piso_cos()

    # A perna literal só vota se TODO token que o operador nomeou tiver acerto.
    #
    # Medido, e é o que derrubou a regra anterior: em *"o que a gente decidiu
    # sobre integração com o Salesforce?"*, `integração` (radical `integr`, em
    # 79 mensagens) é MAIS RARO no acervo que `rsync` (116) — então nenhum
    # limiar de raridade separa os dois casos. O que separa é outra coisa:
    # `Salesforce` **não existe** no histórico. Deixar a palavra genérica
    # carregar o voto enquanto a específica está ausente transformaria a recusa
    # do cenário que reprova a fase inteira numa "menção literal".
    #
    # A regra em uma frase: **se alguma coisa específica que o operador nomeou
    # não está no histórico, não dá pra afirmar que o assunto existe.** Ela só
    # torna esta perna mais conservadora — a de sentido continua votando
    # sozinha, então uma pergunta longa com uma palavra inédita no meio ainda é
    # respondida pelo caminho do sentido.
    tem_literal = bool(lit) and not lit_sem_acerto

    if passou_sentido:
        r.veredito = "ACHOU"
        # Fora do piso, a perna de palavra sozinha não sustenta um resultado:
        # ela traria as mensagens de "Japão" e "piano" junto com as certas.
        achados = [a for a in achados if (a.cos or 0) >= piso_cos() or a.literal]
    elif tem_literal:
        r.veredito = "MENCAO_LITERAL"
        # NÃO se filtra para "só os literais" aqui, e a mudança tem motivo.
        #
        # A versão anterior escondia os candidatos por sentido que ficaram
        # abaixo do piso — e foi assim que este desfecho passou a AFIRMAR mais
        # do que sabia. Visto na 3ª execução da bateria: a consulta "portão
        # permanente, ordem física das colunas, carga posicional" saiu com o
        # carimbo antigo, cujo texto dizia "NADA no acervo responde", **enquanto
        # trazia na lista as três mensagens que respondiam**.
        #
        # O que esta função sabe é: *a palavra aparece, e o sentido não passou
        # do piso*. Ela **não** sabe que nada responde. Mostrar tudo, com a nota
        # de cada um, devolve o julgamento a quem lê — que é onde ele pertence.
        achados = [a for a in achados if a.literal or a.cos is not None]
    else:
        r.veredito = "SEM_REGISTRO"
        achados = []

    achados = achados[:limite]
    if achados:
        try:
            _corpo_de_exibicao(db, achados, pesos)
        except Exception:  # noqa: BLE001 — sem trecho, mostra-se o que houver
            pass
    r.achados = achados
    return r
