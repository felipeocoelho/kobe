"""A mensagem inteira em cada assunto (Highlander v3, F7) — a marcação.

O QUE MUDOU EM RELAÇÃO À F7.1
------------------------------
Na F7.1 só a AFIRMAÇÃO recebia assunto: a mensagem que não gerou afirmação
ficava fora de qualquer conversa, e "retoma o assunto X" só tinha as frases
destiladas para mostrar. Agora o curador marca a MENSAGEM: qual é o assunto
principal dela, e que partes dela são de que assunto.

A REGRA DO OPERADOR (23/10/2026)
--------------------------------
*"Pegar assim um trecho eu acho que é meio Frankenstein, é meio gambiarra… pense
numa forma de levar a mensagem inteira… sinalize que tal parte da mensagem é uma
referência a outro assunto."* Por isso NADA aqui recorta, copia ou reescreve a
fala. A mensagem é sempre devolvida inteira; o que este módulo guarda são
POSIÇÕES (início e fim em caracteres) sobre o texto de `messages`, que não muda.

QUEM DECIDE O QUÊ — E POR QUE ISSO MANTÉM OS NÚMEROS MEDIDOS VÁLIDOS
--------------------------------------------------------------------
1. o CÓDIGO corta a mensagem em frases e agrupa em unidades de ~350 caracteres
   (`unidades`) — mecânico, sempre em fim de frase;
2. o MODELO diz o assunto de cada unidade e DECLARA o principal da mensagem;
3. o CÓDIGO junta unidades vizinhas de mesmo assunto em partes e grava as
   posições (`aplicar`).

O passo 2 é exatamente a tarefa medida na rodada 2 da F7 ("gabarito de
assunto"): com a lista de assuntos fixa, dois leitores independentes concordam
sobre o assunto de cada parte em 98–99% dos casos. O principal é DECLARADO, e
não deduzido, porque deduzi-lo ("o assunto com mais texto") caiu para ~97% por
empate.

A HERANÇA DA AFIRMAÇÃO, POR POSIÇÃO
-----------------------------------
O curador devolve, com cada afirmação, as palavras literais da mensagem que a
sustentam (aprovado em 31/10/2026). `localizar` acha essas palavras no texto, e
a afirmação herda o assunto da PARTE onde elas caem — mecânico. Palavra não
achada = herança pelo principal da mensagem, e a ocorrência é CONTADA
(`subject_fallbacks`, `quotes_missing` em `lucien_runs`).

A CHAVE
-------
`LUCIEN_MARCACAO_ENABLED` — **desligada quando ausente** (comportamento novo
nasce desligado; ligar é ato do operador). Desligada, o prompt do curador é,
byte a byte, o da F7.1.

NENHUMA FUNÇÃO DAQUI DÁ COMMIT — quem chama é dono da transação.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Optional

logger = logging.getLogger("kobe.lucien.marcacao")

ALVO_UNIDADE = 350          # caracteres por unidade (o tamanho medido na rodada 2)
MAX_ABERTAS = 6             # conversas abertas mostradas para dar continuidade
TRECHO_ABERTA = 240         # quanto do último trecho de cada conversa aberta
PALAVRAS_MIN_CHARS = 8      # abaixo disso a "citação" não identifica lugar nenhum


def _env(nome: str) -> str:
    return (os.getenv(nome) or "").strip()


def habilitado() -> bool:
    """`LUCIEN_MARCACAO_ENABLED` — desligada quando ausente."""
    return _env("LUCIEN_MARCACAO_ENABLED").lower() in ("1", "true", "yes", "on", "sim")


_DISPONIVEL: dict[str, bool] = {}


def ativo(cx) -> bool:
    """Ligada, com assunto ligado, e com a 013 aplicada neste banco.

    Código novo sobre banco velho (o `git pull` sem `migrate.py up`) degrada para
    o comportamento da F7.1, em vez de derrubar a rodada por uma tabela que não
    existe."""
    if not habilitado():
        return False
    from bot.lucien import assuntos

    if not assuntos.ativo(cx):
        return False
    chave = str(getattr(cx, "info", None) and cx.info.dsn)
    if chave not in _DISPONIVEL:
        try:
            cur = cx.cursor()
            cur.execute("SELECT to_regclass('message_subjects') IS NOT NULL AS ok")
            _DISPONIVEL[chave] = bool(cur.fetchone()["ok"])
        except Exception:  # noqa: BLE001
            return False
        if not _DISPONIVEL[chave]:
            logger.warning("lucien: marcação ligada, mas a migration 013 não está "
                           "aplicada neste banco — rodando sem marcação")
    return _DISPONIVEL[chave]


def _tem_tabela(cx) -> bool:
    """A 013 está aplicada neste banco? (independe da chave: LER a marcação que
    já existe não depende de o curador estar marcando agora.)"""
    chave = "t:" + str(getattr(cx, "info", None) and cx.info.dsn)
    if chave not in _DISPONIVEL:
        cur = cx.cursor()
        cur.execute("SELECT to_regclass('message_subjects') IS NOT NULL AS ok")
        _DISPONIVEL[chave] = bool(cur.fetchone()["ok"])
    return _DISPONIVEL[chave]


def md5(texto: str) -> str:
    return hashlib.md5((texto or "").encode("utf-8")).hexdigest()


# ── As unidades: o corte mecânico ─────────────────────────────────────────

_FIM_DE_FRASE = re.compile(r"(?<=[.!?…])\s+|\n\s*\n")


def frases(texto: str) -> list[tuple[int, int]]:
    """As frases do texto como posições `[início, fim)`, sem os espaços entre elas."""
    texto = texto or ""
    out, ini = [], 0
    for m in _FIM_DE_FRASE.finditer(texto):
        if m.start() > ini:
            out.append((ini, m.start()))
        ini = m.end()
    if ini < len(texto):
        out.append((ini, len(texto)))
    # tira espaço nas pontas sem perder a posição
    limpas = []
    for a, b in out:
        while a < b and texto[a].isspace():
            a += 1
        while b > a and texto[b - 1].isspace():
            b -= 1
        if b > a:
            limpas.append((a, b))
    return limpas


def unidades(texto: str, alvo: int = ALVO_UNIDADE) -> list[tuple[int, int]]:
    """Frases vizinhas agrupadas até ~`alvo` caracteres. Sempre ao menos uma
    unidade para texto não vazio; uma frase maior que o alvo vira uma unidade só
    (nunca se corta no meio de frase)."""
    fs = frases(texto)
    us: list[tuple[int, int]] = []
    for a, b in fs:
        if us and (b - us[-1][0]) <= alvo:
            us[-1] = (us[-1][0], b)
        else:
            us.append((a, b))
    return us


# ── Localizar as palavras literais ────────────────────────────────────────


def _normalizado(texto: str) -> tuple[str, list[int]]:
    """Texto sem acento, minúsculo, só letra/número e um espaço entre palavras —
    e, para cada caractere dele, a posição de origem no texto original."""
    out, idx = [], []
    espaco = True
    for i, ch in enumerate(texto or ""):
        base = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in base if not unicodedata.combining(c)).lower()
        alnum = [c for c in base if c.isalnum()]
        if alnum:
            for c in alnum:
                out.append(c)
                idx.append(i)
            espaco = False
        elif not espaco:
            out.append(" ")
            idx.append(i)
            espaco = True
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def localizar(texto: str, palavras: str) -> Optional[tuple[int, int]]:
    """Onde, no texto original, estão as palavras literais — ou `None`.

    A comparação ignora acento, caixa e pontuação (o modelo copia, não
    datilografa), mas a posição devolvida é sobre o texto ORIGINAL."""
    alvo, _ = _normalizado(palavras or "")
    alvo = alvo.strip()
    if len(alvo) < PALAVRAS_MIN_CHARS:
        return None
    norm, idx = _normalizado(texto or "")
    k = norm.find(alvo)
    if k < 0:
        return None
    return idx[k], idx[k + len(alvo) - 1] + 1


# ── O que o modelo vê ─────────────────────────────────────────────────────


def bloco_da_mensagem(seq: int, texto: str) -> list[str]:
    """As linhas `[seq.n] …` de uma mensagem. O texto de cada unidade é a fatia
    LITERAL do original; só os espaços de dentro são comprimidos para leitura."""
    return [f"[{seq}.{n}] {' '.join(texto[a:b].split())}"
            for n, (a, b) in enumerate(unidades(texto), start=1)]


INSTRUCAO = """\
MARCAÇÃO DAS MENSAGENS — A QUE CONVERSA CADA MENSAGEM PERTENCE

Além das afirmações, diga para CADA mensagem do lote a que CONVERSA ela pertence.
A mensagem foi cortada em partes numeradas ([1234.1], [1234.2]…).

DUAS NATUREZAS, e a marcação usa só a segunda:
  - ASSUNTO (tema largo, T…): a pasta grande — "Coder — …", "Salas tmux — …".
    NÃO marque mensagem com tema. Ele só serve de "dentro_de" para conversa nova.
  - SUBASSUNTO (conversa, C…): uma conversa sobre UMA coisa, do tamanho de um
    documento, que o operador vai querer retomar ("retoma aquilo do bug do
    emoji"). É COM ELA que a mensagem é marcada.

  - Declare a conversa PRINCIPAL de cada mensagem: UMA só, a de que ela mais
    trata. Use uma conversa da lista (C…) ou uma nova declarada em
    "assuntos_novos" (N…).
  - Uma mensagem pode tratar de mais de uma conversa — o operador às vezes diz
    "até aqui era o A, agora o B". Nesse caso diga a conversa de cada parte em
    "partes". Uma parte que serve de verdade a duas leva as duas. Só marque uma
    segunda quando a parte diz algo NOVO sobre ela, não quando só a menciona.
  - Se todas as partes são da principal, omita "partes".
  - Aparte sem conteúdo ("ok", "obrigado", "tá aí?") fica sem conversa:
    "principal": null.
  - Conversa nova: REUSE antes de criar. Diga em "dentro_de" o tema (T…) a que
    ela pertence, se algum servir; senão null.
  - CONVERSAS ABERTAS mostram onde cada conversa recente do tópico parou: se o
    lote continua uma delas, use a mesma.

E, em cada afirmação, copie em "palavras" as palavras LITERAIS da mensagem de
origem que a sustentam — até 15 palavras seguidas, copiadas, não resumidas.
"""

CONTRATO = """\
CAMPOS DE MARCAÇÃO, no objeto de resposta (além dos de cima):
  "assuntos_novos": [{"id": "N1", "titulo": "…", "descricao": "uma frase",
                      "dentro_de": "T2 ou null", "por_que": "…"}],
  "mensagens": {
    "1234": {"principal": "C3"},
    "1235": {"principal": "C3", "partes": {"1": ["C3"], "2": ["N1"], "3": ["C3", "N1"]}},
    "1236": {"principal": null}
  }
E em cada item de "claims": "palavras": "até 15 palavras literais da mensagem de origem".
  - TODA mensagem do lote aparece em "mensagens".
  - Apelido que não está na lista nem em "assuntos_novos" é ignorado.
  - Em "claims", "assunto" pode ser também um N… de "assuntos_novos".
"""


def bloco_vocabulario(conversas: dict, temas: dict) -> list[str]:
    """As duas listas que a marcação vê: as conversas (para marcar) e os temas
    (só para `dentro_de` de conversa nova)."""
    linhas = ["", "─" * 60, "SUBASSUNTOS — CONVERSAS (marque com estas; reuse antes de criar)", "─" * 60]
    linhas += [f"{ap}  {c['title']}" + (f" — {c['description']}" if c.get("description") else "")
               for ap, c in conversas.items()] or ["(nenhuma ainda)"]
    linhas += ["", "─" * 60, "ASSUNTOS — TEMAS LARGOS (só para 'dentro_de')", "─" * 60]
    linhas += [f"{ap}  {t['title']}" for ap, t in temas.items()] or ["(nenhum)"]
    return linhas


def bloco_abertas(abertas: list[dict]) -> list[str]:
    if not abertas:
        return []
    linhas = ["", "─" * 60, "CONVERSAS ABERTAS DESTE TÓPICO (onde cada uma parou)", "─" * 60]
    for a in abertas:
        linhas.append(f"{a['apelido']}  {a['title']} — última mensagem #{a['seq']}"
                      f" ({a['quando']}): \"{a['trecho']}\"")
    return linhas


def abertas(cx, topic_id: str, *, antes_de_seq: Optional[int] = None,
            limite: int = MAX_ABERTAS) -> list[dict]:
    """As conversas marcadas mais recentes do tópico, com o último trecho de cada.

    O trecho é a ÚLTIMA parte do assunto na última mensagem dele — o lugar exato
    onde a conversa parou. Com a 014, só conversas (marcação antiga feita com
    tema não reabre o tema como rótulo)."""
    from bot.lucien import assuntos

    so_conversa = " AND s.kind = 'conversa'" if assuntos.tem_natureza(cx) else ""
    cur = cx.cursor()
    cur.execute(
        "SELECT DISTINCT ON (ms.subject_id) ms.subject_id, ms.parts, m.seq, m.content,"
        "       m.created_at, s.title, s.description, s.last_touched_at"
        "  FROM message_subjects ms JOIN messages m ON m.id = ms.message_id"
        "  JOIN lucien_subjects s ON s.id = ms.subject_id"
        " WHERE m.topic_id = %s AND s.status = 'ativo'" + so_conversa +
        "   AND (%s::bigint IS NULL OR m.seq < %s::bigint)"
        " ORDER BY ms.subject_id, m.seq DESC", (topic_id, antes_de_seq, antes_de_seq))
    linhas = sorted(cur.fetchall(), key=lambda r: r["seq"], reverse=True)[:limite]
    out = []
    for r in linhas:
        a, b = (r["parts"] or [[0, len(r["content"] or "")]])[-1]
        trecho = " ".join((r["content"] or "")[a:b].split())
        if len(trecho) > TRECHO_ABERTA:
            trecho = "…" + trecho[-TRECHO_ABERTA:]
        out.append({"id": str(r["subject_id"]), "title": r["title"],
                    "description": r["description"], "last_touched_at": r["last_touched_at"],
                    "seq": int(r["seq"]), "quando": r["created_at"].strftime("%d/%m %H:%M"),
                    "trecho": trecho})
    return out


# ── O que o modelo devolve, já validado ───────────────────────────────────


@dataclass
class MarcaDaMensagem:
    seq: int
    principal: Optional[str]                 # subject_id, ou None (aparte)
    # subject_id → lista de [início, fim) já juntadas
    partes: dict[str, list[list[int]]] = field(default_factory=dict)


@dataclass
class ResultadoMarcacao:
    marcas: dict[int, MarcaDaMensagem] = field(default_factory=dict)
    novos: dict[str, str] = field(default_factory=dict)     # N1 → subject_id
    marcadas: int = 0
    invalidos: int = 0          # apelido inexistente, parte inexistente
    principal_suprido: int = 0  # principal ausente/fora das partes, suprido pelo código
    criados: int = 0
    evitados: int = 0
    # F7 (014): as conversas (subassuntos) que valem como rótulo de marcação.
    conversas: set = field(default_factory=set)


def _juntar(intervalos: Iterable[tuple[int, int]], texto: Optional[str] = None) -> list[list[int]]:
    """Junta intervalos que se tocam ou que só têm ESPAÇO entre eles (o espaço
    entre duas frases do mesmo assunto não é outra parte)."""
    out: list[list[int]] = []
    for a, b in sorted(intervalos):
        if out and (a <= out[-1][1] or
                    (texto is not None and not texto[out[-1][1]:a].strip())):
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def resolver_apelidos(cx, lote, novos: list[dict], *, run_id: str, quando,
                      res: ResultadoMarcacao, criado_por: str = "lucien") -> dict[str, str]:
    """`{apelido: subject_id}` para os A… da lista e os N… declarados.

    Os N… passam pela mesma T11 do curador: título que duplicaria um assunto
    existente vira reuso, e conta. `dentro_de` só é aplicado quando cabe em um
    nível (a trava do banco recusaria; aqui se confere antes, para não abortar
    a transação inteira por causa de um pai)."""
    from bot.lucien import assuntos

    natureza = assuntos.tem_natureza(cx)
    mapa = {ap: str(a["id"]) for ap, a in (lote.assuntos or {}).items()}
    for lista in (getattr(lote, "conversas", None) or {}, getattr(lote, "temas", None) or {}):
        mapa.update({ap: str(a["id"]) for ap, a in lista.items()})
    if natureza and mapa:
        # A natureza vem do banco, não do dicionário do lote: só CONVERSA é rótulo.
        cur = cx.cursor()
        cur.execute("SELECT id FROM lucien_subjects WHERE id = ANY(%s::uuid[])"
                    " AND kind = 'conversa'", (list(set(mapa.values())),))
        res.conversas |= {str(r["id"]) for r in cur.fetchall()}
    for n in novos or []:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip().upper()
        titulo = " ".join(str(n.get("titulo") or "").split())
        if not nid or not (assuntos.TITULO_MIN <= len(titulo) <= assuntos.TITULO_MAX):
            res.invalidos += 1
            continue
        kind = "conversa" if natureza else "tema"
        kw = dict(quando=quando, descricao=str(n.get("descricao") or ""),
                  home_topic_id=lote.topic_id, run_id=run_id,
                  motivo=str(n.get("por_que") or ""), criado_por=criado_por, kind=kind)
        sid, desfecho = assuntos.resolver_novo(cx, titulo, **kw)
        if natureza and desfecho != "criado" and _natureza_de(cx, sid) != "conversa":
            # O título caiu num TEMA (mesmo slug): a conversa nasce com a data no
            # título, dentro desse tema — nunca se marca mensagem com tema.
            tema = sid
            titulo2 = f"{titulo} — {quando:%d/%m/%Y}" if quando else f"{titulo} (conversa)"
            sid, desfecho = assuntos.resolver_novo(cx, titulo2[:assuntos.TITULO_MAX], **kw)
            if desfecho == "criado" and _cabe_um_nivel(cx, sid, tema):
                cx.cursor().execute("UPDATE lucien_subjects SET parent_id = %s WHERE id = %s",
                                    (tema, sid))
        if desfecho == "criado":
            res.criados += 1
            pai = mapa.get(str(n.get("dentro_de") or "").strip().upper())
            if pai and pai != sid and _cabe_um_nivel(cx, sid, pai) and \
                    (not natureza or _natureza_de(cx, pai) == "tema"):
                cx.cursor().execute("UPDATE lucien_subjects SET parent_id = %s WHERE id = %s"
                                    " AND parent_id IS NULL", (pai, sid))
        else:
            res.evitados += 1
        mapa[nid] = sid
        if not natureza or _natureza_de(cx, sid) == "conversa":
            res.conversas.add(sid)
    return mapa


def _natureza_de(cx, sid: str) -> Optional[str]:
    cur = cx.cursor()
    cur.execute("SELECT kind FROM lucien_subjects WHERE id = %s", (sid,))
    r = cur.fetchone()
    return r["kind"] if r else None


def _cabe_um_nivel(cx, filho: str, pai: str) -> bool:
    cur = cx.cursor()
    cur.execute("SELECT (SELECT parent_id FROM lucien_subjects WHERE id = %s) IS NULL AS pai_livre,"
                "       NOT EXISTS (SELECT 1 FROM lucien_subjects WHERE parent_id = %s) AS filho_livre",
                (pai, filho))
    r = cur.fetchone()
    return bool(r and r["pai_livre"] and r["filho_livre"])


def interpretar(marcacao: dict, lote, mapa: dict[str, str],
                res: ResultadoMarcacao, *, so_conversas: bool = False) -> None:
    """De `{"1234": {"principal": "A3", "partes": {...}}}` a `MarcaDaMensagem`.

    Regras do código, não do modelo:
    - só mensagem do lote, e só mensagem marcável (a foto da revisão não é);
    - apelido desconhecido é ignorado e contado;
    - parte sem rótulo herda o principal;
    - principal ausente com partes rotuladas → o assunto com mais texto, contado;
    - principal que não aparece em parte nenhuma → entra nas partes sem rótulo.
    """
    por_seq = lote.por_seq
    if so_conversas:
        # F7 (014): só CONVERSA (subassunto) vale como rótulo. Tema na marcação é
        # apelido inválido — contado, nunca gravado.
        mapa = {ap: sid for ap, sid in mapa.items() if sid in res.conversas}
    for chave, item in (marcacao or {}).items():
        try:
            seq = int(str(chave).lstrip("#"))
        except ValueError:
            res.invalidos += 1
            continue
        m = por_seq.get(seq)
        if m is None or not getattr(m, "marcavel", True) or not isinstance(item, dict):
            res.invalidos += 1
            continue
        us = unidades(m.content)
        principal_ap = item.get("principal")
        principal = mapa.get(str(principal_ap).strip().upper()) if principal_ap else None
        if principal_ap and principal is None:
            res.invalidos += 1
        rotulos: dict[int, set[str]] = {}
        for n, aps in (item.get("partes") or {}).items():
            try:
                k = int(str(n).split(".")[-1])
            except ValueError:
                res.invalidos += 1
                continue
            if not (1 <= k <= len(us)):
                res.invalidos += 1
                continue
            sids = set()
            for ap in (aps if isinstance(aps, list) else [aps]):
                sid = mapa.get(str(ap or "").strip().upper())
                if sid:
                    sids.add(sid)
                elif ap:
                    res.invalidos += 1
            if sids:
                rotulos[k] = sids
        if principal is None and rotulos:
            tam: dict[str, int] = {}
            for k, sids in rotulos.items():
                for s in sids:
                    tam[s] = tam.get(s, 0) + (us[k - 1][1] - us[k - 1][0])
            principal = max(tam, key=tam.get)
            res.principal_suprido += 1
        if principal is None:
            continue                                 # aparte: sem assunto
        for k in range(1, len(us) + 1):
            rotulos.setdefault(k, {principal})
        partes: dict[str, list[tuple[int, int]]] = {}
        for k, sids in rotulos.items():
            for s in sids:
                partes.setdefault(s, []).append(us[k - 1])
        if principal not in partes:                  # declarado e sem parte nenhuma
            res.principal_suprido += 1
            principal = max(partes, key=lambda s: sum(b - a for a, b in partes[s]))
        res.marcas[seq] = MarcaDaMensagem(
            seq=seq, principal=principal,
            partes={s: _juntar(iv, m.content) for s, iv in partes.items()})


def gravar(cx, lote, res: ResultadoMarcacao, *, run_id: str,
           criado_por: str = "lucien") -> None:
    """Grava a marcação. Mensagem JÁ marcada não é remarcada (a releitura do
    mesmo lote, ou a retroativa passando por cima da corrente, não duplica nem
    sobrescreve — o caminho de volta fica por rodada)."""
    import json

    from bot.lucien import assuntos

    por_seq = lote.por_seq
    cur = cx.cursor()
    for seq, marca in sorted(res.marcas.items()):
        m = por_seq[seq]
        cur.execute("SELECT 1 FROM message_subjects WHERE message_id = %s LIMIT 1", (m.id,))
        if cur.fetchone():
            continue
        h = md5(m.content)
        for sid, partes in marca.partes.items():
            cur.execute(
                "INSERT INTO message_subjects (message_id, subject_id, role, parts,"
                " content_md5, created_by, run_id) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)",
                (m.id, sid, "principal" if sid == marca.principal else "secundario",
                 json.dumps(partes), h, criado_por, run_id))
            assuntos.tocar(cx, sid, m.created_at)
        res.marcadas += 1


def assuntos_da_mensagem(cx, message_id: str) -> tuple[Optional[str], dict[str, list[list[int]]]]:
    """`(principal, {subject_id: partes})` de uma mensagem já marcada.

    FUSÃO É APONTAR, NÃO MOVER: a marcação nunca é reescrita quando dois assuntos
    se fundem (`assuntos.fundir`). Ela continua apontando para o assunto
    fundido, e é AQUI, na leitura, que a fusão é seguida até o destino — assim
    desfazer a fusão não precisa restaurar nada. Se origem e destino marcavam a
    mesma mensagem, as partes se somam e o principal de qualquer um vale para o
    destino."""
    from bot.lucien import assuntos

    cur = cx.cursor()
    cur.execute("SELECT ms.subject_id, ms.role, ms.parts, m.content FROM message_subjects ms"
                "  JOIN messages m ON m.id = ms.message_id WHERE ms.message_id = %s",
                (message_id,))
    principal, partes = None, {}
    for r in cur.fetchall():
        sid = assuntos._seguir_fusao(cx.cursor(), str(r["subject_id"]))
        partes[sid] = _juntar([tuple(x) for x in partes.get(sid, [])] +
                              [tuple(x) for x in (r["parts"] or [])], r["content"])
        if r["role"] == "principal":
            principal = sid
    return principal, partes


def assunto_por_posicao(principal: Optional[str], partes: dict[str, list[list[int]]],
                        intervalo: tuple[int, int], *, preferido: Optional[str] = None
                        ) -> Optional[str]:
    """O assunto da parte onde o intervalo cai.

    Ordem: o preferido (a escolha do curador) se ele cobre o intervalo; senão o
    principal se ele cobre; senão o assunto com maior sobreposição."""
    a, b = intervalo
    cobre: dict[str, int] = {}
    for sid, ivs in partes.items():
        sob = sum(max(0, min(b, y) - max(a, x)) for x, y in ivs)
        if sob > 0:
            cobre[sid] = sob
    if not cobre:
        return None
    if preferido in cobre:
        return preferido
    if principal in cobre:
        return principal
    return max(cobre, key=cobre.get)
