"""A busca nos documentos — as mesmas três pernas da conversa, e o rótulo.

POR QUE AS MESMAS PERNAS
------------------------
O que foi medido na conversa vale aqui pelo mesmo motivo: identificador exato
(`compat_gate`, `plano-faxina-r15.md`) só a perna literal acha; flexão, a de
palavra; paráfrase, só o vetor. E o voto sobre "existe" é o mesmo: sentido acima
do piso, OU todos os identificadores nomeados achados literalmente. A perna de
palavra ordena e não vota.

O PISO É PRÓPRIO
----------------
O piso da conversa (`SEARCH_PISO_COS`) foi calibrado com mensagens; documento é
texto mais longo e estruturado, com título e âncora no vetor. O piso daqui foi
medido com perguntas dos dois lados (ver `PISO_COS_PADRAO`).

O RÓTULO É DADO, NÃO REDAÇÃO
----------------------------
Cada resultado sai com a natureza e — para artefato de sala — o desfecho, lidos do
catálogo. Desfecho desconhecido sai DESCONHECIDO; nunca "aprovado" por omissão.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.search import embedder
from bot.search import query as q

# Calibrado em 03/11/2026 no laboratório (carga real, bateria de perguntas com
# e sem resposta) — ver CHANGELOG da F7.2 e `.local/` da sala para os números.
PISO_COS_PADRAO = 0.50

K_PERNA = 30


def piso_cos() -> float:
    try:
        return float(os.environ.get("DOC_SEARCH_PISO_COS") or PISO_COS_PADRAO)
    except ValueError:
        return PISO_COS_PADRAO


@dataclass
class DocAchado:
    doc_id: int
    root: str
    rel_path: str
    nature: str
    topic_slug: Optional[str]
    title: str
    ext: str
    indexable: bool
    status: str
    file_mtime: Optional[str]
    anchor: Optional[str] = None
    corpo: str = ""
    cos: Optional[float] = None
    literal: bool = False
    rrf: float = 0.0
    pernas: list[str] = field(default_factory=list)
    sala_id: Optional[str] = None
    sala_kind: Optional[str] = None
    outcome: Optional[str] = None
    outcome_source: Optional[str] = None
    origin_seq: Optional[int] = None
    caminho: Optional[str] = None


@dataclass
class ResultadoDocs:
    veredito: str                 # ACHOU | MENCAO_LITERAL | SEM_REGISTRO | FALHA | FORA
    achados: list[DocAchado] = field(default_factory=list)
    cos_topo: Optional[float] = None
    sentido_ativo: bool = True
    motivo_sentido_fora: Optional[str] = None
    literais: list[str] = field(default_factory=list)
    pendencia: dict = field(default_factory=dict)
    erro: Optional[str] = None


_SEL = (
    "SELECT s.id AS doc_id, s.root, s.rel_path, s.nature, s.topic_slug, s.title, s.ext,"
    "       s.indexable, s.status, s.file_mtime"
)


def _filtro(topic_slug: Optional[str]) -> tuple[str, list]:
    if topic_slug:
        return " AND s.topic_slug = %s", [topic_slug]
    return "", []


def tabelas_existem(db) -> bool:
    r = db.query("SELECT to_regclass('doc_chunks') IS NOT NULL AS ok")
    return bool(r and r[0]["ok"])


def _literal(db, tokens, topic_slug, k=K_PERNA):
    onde, params = _filtro(topic_slug)
    saida, sem = [], []
    for t in tokens:
        pat = "%" + t + "%"
        linhas = db.query(
            f"{_SEL}, c.anchor, c.body FROM doc_chunks c JOIN doc_sources s ON s.id = c.doc_id"
            f" WHERE c.body ILIKE %s{onde} ORDER BY s.id DESC LIMIT %s",
            [pat, *params, k],
        )
        linhas += db.query(
            f"{_SEL}, NULL AS anchor, NULL AS body FROM doc_sources s"
            f" WHERE (s.rel_path ILIKE %s OR s.title ILIKE %s){onde} ORDER BY s.id DESC LIMIT %s",
            [pat, pat, *params, k],
        )
        if not linhas:
            sem.append(t)
        saida += linhas
    return saida, sem


def _palavra(db, pesos, topic_slug, k=K_PERNA):
    if not pesos:
        return []
    ap = chr(39)
    tq = " | ".join(f"{ap}{w}{ap}" for w in pesos)
    soma = " + ".join(
        f"(CASE WHEN c.search_tsv @@ {ap}{w}{ap}::tsquery THEN {idf:.4f} ELSE 0 END)"
        for w, idf in pesos.items())
    onde, params = _filtro(topic_slug)
    return db.query(
        f"{_SEL}, c.anchor, c.body, ({soma}) AS idf"
        f"  FROM doc_chunks c JOIN doc_sources s ON s.id = c.doc_id"
        f" WHERE c.search_tsv @@ %s::tsquery{onde} ORDER BY idf DESC LIMIT %s",
        [tq, *params, k],
    )


def _sentido(db, vetor, topic_slug, k=K_PERNA):
    v = embedder.para_sql(vetor)
    onde, params = _filtro(topic_slug)
    return db.query(
        f"{_SEL}, c.anchor, c.body, 1 - (c.embedding <=> %s::vector) AS cos"
        f"  FROM doc_chunks c JOIN doc_sources s ON s.id = c.doc_id"
        f" WHERE c.embedding IS NOT NULL{onde}"
        f" ORDER BY c.embedding <=> %s::vector LIMIT %s",
        [v, *params, v, k],
    )


# Peso de cada perna na fusão. A de palavra conta MENOS: um documento longo casa
# com quase todos os radicais de qualquer pergunta e, com peso igual, ganharia
# por tamanho, não por assunto.
PESO_PERNA = {"sentido": 1.0, "literal": 1.0, "palavra": float(os.environ.get("DOC_PESO_PALAVRA") or 0.25)}


def _fundir(listas) -> dict[int, DocAchado]:
    por: dict[int, DocAchado] = {}
    for nome, linhas in listas:
        vistos_aqui: set[int] = set()
        posicao = 0
        for l in linhas:
            did = int(l["doc_id"])
            if did in vistos_aqui and nome != "sentido":
                continue
            posicao += 1
            a = por.get(did)
            if a is None:
                a = DocAchado(
                    doc_id=did, root=l["root"], rel_path=l["rel_path"], nature=l["nature"],
                    topic_slug=l["topic_slug"], title=l["title"], ext=l["ext"],
                    indexable=bool(l["indexable"]), status=l["status"],
                    file_mtime=str(l["file_mtime"]) if l["file_mtime"] else None,
                    anchor=l.get("anchor"), corpo=(l.get("body") or "").strip())
                por[did] = a
            if did not in vistos_aqui:
                a.rrf += PESO_PERNA.get(nome, 1.0) / (q.RRF_K + posicao)
                vistos_aqui.add(did)
            if nome not in a.pernas:
                a.pernas.append(nome)
            if nome == "sentido":
                cos = float(l["cos"])
                if a.cos is None or cos > a.cos:
                    a.cos = cos
                    a.corpo = (l.get("body") or a.corpo).strip()
                    a.anchor = l.get("anchor") or a.anchor
            elif nome == "literal":
                a.literal = True
                if not a.corpo and l.get("body"):
                    a.corpo = l["body"].strip()
                    a.anchor = l.get("anchor")
            elif not a.corpo and l.get("body"):
                a.corpo = l["body"].strip()
                a.anchor = l.get("anchor")
    return por


def _anotar(db, achados: list[DocAchado], kobe_home: Optional[Path]) -> None:
    ids = [a.doc_id for a in achados]
    if not ids:
        return
    prods = {int(l["doc_id"]): l for l in db.query(
        "SELECT doc_id, sala_id, sala_kind, outcome, outcome_source, origin_seq"
        "  FROM doc_producoes WHERE doc_id = ANY(%s)", (ids,))}
    from bot.docs import raizes

    for a in achados:
        p = prods.get(a.doc_id)
        if p:
            a.sala_id, a.sala_kind = p["sala_id"], p["sala_kind"]
            a.outcome, a.outcome_source = p["outcome"], p["outcome_source"]
            a.origin_seq = p["origin_seq"]
        if kobe_home is not None:
            c = raizes.caminho_absoluto(a.root, a.rel_path, kobe_home)
            a.caminho = str(c) if c else None


def buscar(db, pergunta: str, *, vetor: Optional[list] = None, topic_slug: Optional[str] = None,
           limite: int = 5, kobe_home: Optional[Path] = None) -> ResultadoDocs:
    """A busca nos documentos, com veredito. Nunca levanta."""
    from bot.docs import indexer

    if not indexer.indexer_enabled():
        return ResultadoDocs(veredito="FORA", erro="DOC_INDEX_ENABLED desligada")
    r = ResultadoDocs(veredito="SEM_REGISTRO")
    try:
        if not tabelas_existem(db):
            return ResultadoDocs(veredito="FORA", erro="migration 015 não aplicada")
        # Só IDENTIFICADOR e nome próprio vão para a perna literal. A repescagem
        # de "palavra crua rara" da conversa (`literais_raros`) NÃO serve aqui:
        # a raridade dela é medida nas MENSAGENS, e num plano de 60 mil
        # caracteres quase toda palavra aparece. Medido na 1ª rodada da bateria
        # (03/11): com ela, os planos longos tomavam o topo de tudo (12/20 no 1º
        # lugar). Termo cru isolado continua achável pelas pernas de palavra e
        # de sentido.
        toks = q.literais(pergunta)
        pesos, _, _ = q.radicais(db, pergunta)
        r.literais = toks
        lit, sem_acerto = _literal(db, toks, topic_slug)
        pal = _palavra(db, pesos, topic_slug)
    except Exception as exc:  # noqa: BLE001
        r.veredito, r.erro = "FALHA", f"o banco não respondeu: {type(exc).__name__}: {exc}"
        return r
    sen: list[dict] = []
    try:
        if vetor is None:
            vetor = embedder.embed_um(pergunta)
        sen = _sentido(db, vetor, topic_slug)
    except embedder.EmbeddingIndisponivel as exc:
        r.sentido_ativo, r.motivo_sentido_fora = False, str(exc)
    except Exception as exc:  # noqa: BLE001
        r.veredito, r.erro = "FALHA", f"a busca por sentido falhou: {type(exc).__name__}: {exc}"
        return r
    try:
        r.pendencia = indexer.pendencia(db)
    except Exception:  # noqa: BLE001
        r.pendencia = {}
    if sen:
        r.cos_topo = max(float(l["cos"]) for l in sen)
    achados = sorted(_fundir([("literal", lit), ("palavra", pal), ("sentido", sen)]).values(),
                     key=lambda a: -a.rrf)
    piso = piso_cos()
    passou = r.cos_topo is not None and r.cos_topo >= piso
    tem_literal = bool(lit) and not sem_acerto
    if passou:
        r.veredito = "ACHOU"
        achados = [a for a in achados if (a.cos or 0) >= piso or a.literal]
    elif tem_literal:
        r.veredito = "MENCAO_LITERAL"
        achados = [a for a in achados if a.literal or a.cos is not None]
    else:
        r.veredito = "SEM_REGISTRO"
        achados = []
    r.achados = achados[:limite]
    try:
        _anotar(db, r.achados, kobe_home)
    except Exception:  # noqa: BLE001 — sem anotação o resultado sai sem desfecho,
        pass           # e o rótulo diz DESCONHECIDO (nunca aprovado)
    return r


# ── O rótulo ──────────────────────────────────────────────────────────────

_DESFECHO = {
    "aprovado": "APROVADO",
    "recusado": "RECUSADO",
    "sem_resposta": "SEM RESPOSTA",
    "superado": "SUPERADO",
    "desconhecido": "DESCONHECIDO",
}
_FONTE = {"estado_coder": "registro do Coder", "operador": "fala do operador",
          "fim_da_sala": "fim da sala"}


def _dia(iso: Optional[str]) -> str:
    if not iso:
        return "?"
    return q_data(iso).split(" ")[0]


def q_data(iso: str) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    try:
        dt = datetime.fromisoformat(str(iso).replace(" ", "T"))
    except ValueError:
        return str(iso)[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y %H:%M")


def rotulo(a: DocAchado) -> str:
    """A linha que vai na frente do trecho. Testada: nenhuma natureza sai sem
    rótulo, e artefato de sala sem desfecho registrado sai DESCONHECIDO."""
    nome = a.rel_path.rsplit("/", 1)[-1]
    if a.nature == "artefato_sala":
        d = _DESFECHO.get(a.outcome or "desconhecido", "DESCONHECIDO")
        fonte = f" ({_FONTE[a.outcome_source]}" + (
            f" #{a.origin_seq})" if a.origin_seq else ")") if a.outcome_source in _FONTE else ""
        quem = f"missão {a.sala_id}" if a.sala_kind == "missao" else f"sala {a.sala_id or '?'}"
        cab = f"📄 artefato de sala · desfecho: {d}{fonte} · {quem}"
    elif a.nature == "anotacao_agente":
        cab = "📝 anotação do agente (registro de segunda mão)"
    elif a.nature == "base_topico":
        cab = f"📚 base do tópico {a.topic_slug} — material seu"
    else:
        cab = f"📎 documento seu · tópico {a.topic_slug}"
    extra = ""
    if not a.indexable:
        extra = " · só listado — conteúdo NÃO lido"
    if a.status == "ausente_do_disco":
        extra += " · o arquivo saiu do disco (texto preservado no índice)"
    return f"{cab} · {nome}" + (f" › {a.anchor}" if a.anchor else "") + \
        f" · arquivo de {_dia(a.file_mtime)}{extra}"
