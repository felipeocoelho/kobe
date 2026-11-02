"""O dossiê do assunto — "retoma o assunto X" (Highlander v3, F7.1).

O QUE ELE É
-----------
A resposta mecânica ao pedido que originou o Highlander: *"quando a gente
RETOME ALGUM ASSUNTO, você retome aquele assunto PLENAMENTE CIENTE DE TUDO O QUE
FOI CONVERSADO"*. Dado um assunto, ele mostra, nesta ordem:

    1. o cabeçalho — aberto em, último toque, tópicos, status;
    2. o que vale hoje — decisões, preferências, pendências e fatos vigentes;
    3. o que mudou — o que foi superado, fechado, abandonado ou contestado, com
       a data e o que entrou no lugar. É a seção que impede o defeito original:
       retomar como "em aberto" o que já tinha fechado;
    4. onde se conversou — os `#número` de origem e de evidência, com data, para
       abrir com `kobe-remember --ver`.

Documentos e salas (o que as salas produziram, o material do operador) entram
quando a F7.2 for aprovada — o redesenho das quatro naturezas de documento
ainda está com o operador. O dossiê diz isso em uma linha, em vez de calar.

POR QUE É DETERMINÍSTICO
------------------------
Mesmo motivo do dossiê de sala (`bot/transcripts/dossier.py`): o que um modelo
escreveria sobre o assunto é texto gerado; o que está aqui é o registro, linha a
linha, com a origem ao lado. Não tem como inventar uma decisão que não houve. E
custa zero: é SQL e formatação de texto.

UMA PERGUNTA LARGA JUNTA VÁRIOS ASSUNTOS
-----------------------------------------
A lista é plana (decisão do operador), então "retoma a V3 do Highlander" não
tem um nó-pai para abrir. Quem junta é a resolução: se a frase aparece
literalmente no título de vários assuntos, todos entram, cada um com a sua
seção. Se a frase é ambígua entre dois (sentido parecido, nenhum literal), o
dossiê LISTA os candidatos em vez de escolher — escolher errado e mostrar com
cara de certo é o pior desfecho possível aqui.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from bot.lucien import assuntos

logger = logging.getLogger("kobe.lucien.dossie_assunto")

TETO_CHARS = 12000
MAX_LARGO = 8          # quantos assuntos uma pergunta larga junta no máximo

_TIPOS = (("decision", "DECIDIDO"), ("preference", "PREFERÊNCIA"),
          ("open", "EM ABERTO"), ("fact", "FATO"))
_ESTADOS = {"superada": "SUPERADA", "fechada": "FECHADA",
            "abandonada": "ABANDONADA", "contestada": "CONTESTADA"}


@dataclass
class Candidato:
    id: str
    title: str
    como: str        # slug | literal | trigrama | sentido
    nota: float


def resolver(cx, frase: str, *, vetor_sql: Optional[str] = None) -> tuple[list[Candidato], bool]:
    """Os assuntos que a frase nomeia, e se a resposta é SEGURA.

    Devolve `(candidatos, seguro)`. `seguro=False` quer dizer "não sei qual dos
    dois" — quem chama lista, não escolhe.
    """
    frase = " ".join((frase or "").split())
    if not frase:
        return [], False
    cur = cx.cursor()

    cur.execute("SELECT id, title FROM lucien_subjects WHERE slug = %s",
                (assuntos.slug(frase),))
    r = cur.fetchone()
    if r is not None:
        sid = assuntos._seguir_fusao(cur, str(r["id"]))
        cur.execute("SELECT id, title FROM lucien_subjects WHERE id = %s", (sid,))
        r = cur.fetchone()
        return [Candidato(str(r["id"]), r["title"], "slug", 1.0)], True

    # Literal: a frase dentro do título. É a perna da pergunta LARGA.
    cur.execute("SELECT id, title FROM lucien_subjects WHERE status = 'ativo'"
                "   AND title ILIKE %s ORDER BY last_touched_at DESC LIMIT %s",
                (f"%{frase}%", 30))
    literais = cur.fetchall()
    if literais:
        return [Candidato(str(x["id"]), x["title"], "literal", 1.0)
                for x in literais], True

    achados: dict[str, Candidato] = {}
    cur.execute("SELECT id, title, similarity(title, %s) AS sim FROM lucien_subjects"
                " WHERE status = 'ativo' AND similarity(title, %s) >= 0.3"
                " ORDER BY sim DESC LIMIT 5", (frase, frase))
    for x in cur.fetchall():
        achados[str(x["id"])] = Candidato(str(x["id"]), x["title"], "trigrama", float(x["sim"]))

    if vetor_sql is None:
        try:
            from bot.search import embedder

            vetor_sql = embedder.para_sql(embedder.embed_um(frase))
        except Exception:  # noqa: BLE001 — sem sentido, resolve só pelo texto
            logger.info("dossiê: sem a perna de sentido na resolução")
    if vetor_sql:
        cur.execute("SELECT id, title, 1 - (embedding <=> %s::vector) AS cos"
                    "  FROM lucien_subjects WHERE status = 'ativo' AND embedding IS NOT NULL"
                    " ORDER BY embedding <=> %s::vector LIMIT 5", (vetor_sql, vetor_sql))
        for x in cur.fetchall():
            if float(x["cos"]) < piso_cos():
                continue
            c = achados.get(str(x["id"]))
            if c is None or float(x["cos"]) > c.nota:
                achados[str(x["id"])] = Candidato(str(x["id"]), x["title"], "sentido",
                                                  float(x["cos"]))

    ordem = sorted(achados.values(), key=lambda c: c.nota, reverse=True)
    if not ordem:
        return [], False
    if len(ordem) == 1 or ordem[0].nota - ordem[1].nota >= 0.08:
        return [ordem[0]], True
    return ordem[:5], False


def piso_cos() -> float:
    """Abaixo disto, "sentido parecido" é ruído. Calibrado no laboratório sobre
    os títulos reais (ver o relatório da F7.1); ajustável por ambiente."""
    import os

    try:
        return float(os.getenv("LUCIEN_ASSUNTOS_PISO_COS") or 0.45)
    except ValueError:
        return 0.45


# ── A montagem ────────────────────────────────────────────────────────────


_TZ = None


def _fuso():
    """As datas saem no fuso do operador, não no do servidor: uma fala das 22h
    de 13/10 em Brasília é 14/10 em UTC, e "último toque 14/10" seria mentira."""
    global _TZ
    if _TZ is None:
        import os
        from zoneinfo import ZoneInfo

        _TZ = ZoneInfo(os.getenv("KOBE_TZ") or "America/Sao_Paulo")
    return _TZ


def _data(d, fmt: str = "%d/%m/%Y") -> str:
    if not d:
        return "?"
    if getattr(d, "tzinfo", None) is not None:
        d = d.astimezone(_fuso())
    return d.strftime(fmt)


def _fam(cx, sid: str) -> list[str]:
    """O assunto e a família dele (fundidos nele; com a 014, as conversas dentro
    do tema). Sem a 013 no banco (sem `parent_id`), só ele — o da F7.1."""
    from bot.lucien import conversa_assunto, marcacao

    if not marcacao._tem_tabela(cx):
        return [sid]
    return conversa_assunto.familia(cx, sid)


def _subassuntos(cx, sid: str) -> list[str]:
    """F7 (014): no tema, a lista das conversas que moram nele — é por ela que
    o operador escolhe qual retomar."""
    try:
        from bot.lucien import assuntos, conversa_assunto

        if not assuntos.tem_natureza(cx):
            return []
        filhos = conversa_assunto.subassuntos(cx, sid)
    except Exception:  # noqa: BLE001
        return []
    if not filhos:
        return []
    linhas = ["", f"### Conversas dentro deste assunto ({len(filhos)})", ""]
    for f in filhos[:15]:
        linhas.append(f"- {f['title']} — {f['n']} mensagem(ns), último toque "
                      f"{_data(f['last_touched_at'])}")
    if len(filhos) > 15:
        linhas.append(f"- _e mais {len(filhos) - 15}; `kobe-assunto ver \"<título>\"` abre uma._")
    return linhas


def _cabecalho(cx, sid: str) -> list[str]:
    cur = cx.cursor()
    cur.execute(
        "SELECT s.title, s.description, s.status, s.opened_at, s.last_touched_at,"
        "       ws.name AS sistema, ss.name AS subsistema"
        "  FROM lucien_subjects s"
        "  LEFT JOIN work_systems ws ON ws.id = s.system_id"
        "  LEFT JOIN work_subsystems ss ON ss.id = s.subsystem_id"
        " WHERE s.id = %s", (sid,))
    s = cur.fetchone()
    cur.execute(
        "SELECT COALESCE(t.current_name, '?') AS topico, COUNT(*) AS n"
        "  FROM lucien_claims c JOIN topics t ON t.id = c.topic_id"
        " WHERE c.subject_id = ANY(%s::uuid[]) GROUP BY 1 ORDER BY 2 DESC", (_fam(cx, sid),))
    topicos = " · ".join(f"{r['topico']} ({r['n']})" for r in cur.fetchall()) or "—"
    linhas = [f"## {s['title']}", ""]
    if s["description"]:
        linhas += [f"_{s['description']}_", ""]
    linhas.append(f"aberto em {_data(s['opened_at'])} · último toque "
                  f"{_data(s['last_touched_at'])} · tópicos: {topicos}"
                  + (f" · sistema: {s['sistema']}" + (f" / {s['subsistema']}"
                                                      if s["subsistema"] else "")
                     if s["sistema"] else "")
                  + ("" if s["status"] == "ativo" else f" · status: {s['status']}"))
    return linhas


def _vigentes(cx, sid: str) -> list[str]:
    cur = cx.cursor()
    cur.execute(
        "SELECT c.kind, c.statement, c.valid_from, c.source_seq, c.confidence,"
        "       c.created_by, COALESCE(t.current_name, '?') AS topico,"
        "       m.audio_transcribed AS audio"
        "  FROM lucien_claims c JOIN topics t ON t.id = c.topic_id"
        "  LEFT JOIN messages m ON m.id = c.source_message_id"
        " WHERE c.subject_id = ANY(%s::uuid[]) AND c.status = 'vigente'"
        " ORDER BY c.valid_from DESC", (_fam(cx, sid),))
    por_tipo: dict[str, list[dict]] = {}
    for r in cur.fetchall():
        por_tipo.setdefault(r["kind"], []).append(r)
    linhas = ["", "### O que vale hoje", ""]
    if not por_tipo:
        return linhas + ["_nada vigente neste assunto._"]
    for kind, rotulo in _TIPOS:
        for r in por_tipo.get(kind, []):
            extras = [f"#{r['source_seq']}", r["topico"]]
            if r["created_by"] == "operador":
                extras.append("registrado pelo operador")
            if r["confidence"] == "baixa":
                extras.append("confiança baixa")
            if r["audio"]:
                extras.append("origem em áudio")
            linhas.append(f"- **{rotulo}** {_data(r['valid_from'])} — {r['statement']}"
                          f"  ← {' · '.join(extras)}")
    return linhas


def _mudancas(cx, sid: str) -> list[str]:
    cur = cx.cursor()
    cur.execute(
        "SELECT c.status, c.kind, c.statement, c.valid_from, c.valid_to, c.source_seq,"
        "       n.statement AS substituta"
        "  FROM lucien_claims c LEFT JOIN lucien_claims n ON n.id = c.superseded_by"
        " WHERE c.subject_id = ANY(%s::uuid[]) AND c.status <> 'vigente'"
        " ORDER BY c.valid_to DESC NULLS LAST", (_fam(cx, sid),))
    linhas = ["", "### O que mudou (não vale mais)", ""]
    rs = cur.fetchall()
    if not rs:
        return linhas + ["_nada foi superado ou encerrado neste assunto._"]
    for r in rs:
        rotulo = _ESTADOS.get(r["status"], r["status"].upper())
        linha = (f"- **{rotulo}** em {_data(r['valid_to'])} (valia desde "
                 f"{_data(r['valid_from'])}) — {r['statement']}  ← #{r['source_seq']}")
        if r["substituta"]:
            linha += f"\n  → substituída por: {r['substituta']}"
        linhas.append(linha)
    return linhas


def _conversa(cx, sid: str) -> list[str]:
    cur = cx.cursor()
    cur.execute(
        "SELECT DISTINCT seq, d FROM ("
        "  SELECT c.source_seq AS seq, c.valid_from AS d FROM lucien_claims c"
        "   WHERE c.subject_id = ANY(%s::uuid[])"
        "  UNION"
        "  SELECT e.seq, m.created_at FROM lucien_claim_evidence e"
        "    JOIN lucien_claims c ON c.id = e.claim_id"
        "    JOIN messages m ON m.id = e.message_id"
        "   WHERE c.subject_id = ANY(%s::uuid[])) x ORDER BY seq", (_fam(cx, sid), _fam(cx, sid)))
    rs = cur.fetchall()
    linhas = ["", "### Onde se conversou", ""]
    if not rs:
        return linhas + ["_sem mensagens de origem._"]
    linhas.insert(3, "_abra qualquer uma com `kobe-remember --ver <número>` — é a fala literal._")
    itens = [f"#{r['seq']} ({_data(r['d'], '%d/%m/%y')})" for r in rs]
    for k in range(0, len(itens), 10):
        linhas.append(", ".join(itens[k:k + 10]))
    return linhas


def _conversa_ou_lista(cx, sid: str, *, secoes_antes, teto: int) -> list[str]:
    """F7 — com a mensagem marcada, a seção final é A CONVERSA: as mensagens
    inteiras, com as partes de outro assunto sinalizadas, no espaço que sobrou do
    teto (`conversa_assunto.montar` recolhe e pagina sem cortar em silêncio).
    Sem marcação (a 013 ausente, ou o assunto ainda não marcado), continua a
    lista de `#número` da F7.1."""
    try:
        from bot.lucien import conversa_assunto, marcacao

        tem = marcacao._tem_tabela(cx)
    except Exception:  # noqa: BLE001
        tem = False
    if not tem:
        return _conversa(cx, sid)
    usado = sum(len(x) + 1 for s in secoes_antes for x in s)
    sobra = teto - usado - 300
    cur = cx.cursor()
    cur.execute("SELECT title FROM lucien_subjects WHERE id = %s", (sid,))
    titulo = cur.fetchone()["title"]
    if sobra < 1500:
        n = len(conversa_assunto.mensagens(cx, sid))
        if not n:
            return _conversa(cx, sid)
        return ["", "### A conversa", "",
                f"_{n} mensagem(ns) marcadas com este assunto — não cabem neste turno; "
                f"abra com `kobe-assunto conversa \"{titulo}\"`._"]
    texto = conversa_assunto.montar(cx, sid, teto=sobra, titulo_para_comando=titulo)
    if not texto:
        return _conversa(cx, sid)
    return ["", "### A conversa", ""] + texto.splitlines() + _origens_fora_da_conversa(cx, sid)


def _origens_fora_da_conversa(cx, sid: str) -> list[str]:
    """As mensagens de origem das afirmações do assunto que NÃO estão na conversa
    marcada. A régua das 30 perguntas mediu (02/11/2026): o caminho pelas
    afirmações e o caminho pela marcação erram em perguntas diferentes, e só os
    dois juntos nunca perdem para nenhum dos dois sozinho — então a conversa
    marcada não substitui a lista da F7.1, soma a ela."""
    from bot.lucien import conversa_assunto

    marcadas = {m.seq for m in conversa_assunto.mensagens(cx, sid)}
    cur = cx.cursor()
    fam = _fam(cx, sid)
    cur.execute(
        "SELECT DISTINCT seq, d FROM ("
        "  SELECT c.source_seq AS seq, c.valid_from AS d FROM lucien_claims c"
        "   WHERE c.subject_id = ANY(%s::uuid[])"
        "  UNION"
        "  SELECT e.seq, m.created_at FROM lucien_claim_evidence e"
        "    JOIN lucien_claims c ON c.id = e.claim_id"
        "    JOIN messages m ON m.id = e.message_id"
        "   WHERE c.subject_id = ANY(%s::uuid[])) x ORDER BY seq", (fam, fam))
    fora = [r for r in cur.fetchall() if r["seq"] is not None and int(r["seq"]) not in marcadas]
    if not fora:
        return []
    itens = [f"#{r['seq']} ({_data(r['d'], '%d/%m/%y')})" for r in fora]
    linhas = ["", "### Também de onde vieram as afirmações deste assunto", "",
              "_mensagens que não estão marcadas com o assunto, mas deram origem a afirmações "
              "dele — abra com `kobe-remember --ver <número>`._"]
    for k in range(0, len(itens), 10):
        linhas.append(", ".join(itens[k:k + 10]))
    return linhas


def _cortar(secoes: list[list[str]], teto: int) -> list[str]:
    """Corta de trás para a frente — a conversa primeiro, depois o que mudou,
    por último o que vale hoje — e diz quanto ficou de fora. Cada seção guarda
    o título e as primeiras linhas; o que sai é contado, nunca some calado."""
    def tam(xs):
        return sum(len(x) + 1 for s in xs for x in s)

    secoes = [list(s) for s in secoes]
    for i in (3, 2, 1):
        if tam(secoes) <= teto:
            break
        cab, corpo = secoes[i][:3], secoes[i][3:]
        cortadas = 0
        while corpo and tam(secoes[:i] + [cab + corpo] + secoes[i + 1:]) > teto:
            corpo.pop()
            cortadas += 1
        if cortadas:
            corpo.append(f"_(+{cortadas} linha(s) cortada(s) para caber no turno — "
                         "peça este assunto sozinho para ver tudo)_")
        secoes[i] = cab + corpo
    return [x for s in secoes for x in s]


def montar(cx, subject_ids: list[str], *, teto: int = TETO_CHARS) -> str:
    """O dossiê de um ou mais assuntos, em markdown, dentro do teto."""
    if not subject_ids:
        return ""
    subject_ids = _sem_filho_do_pai(cx, subject_ids)
    fora = subject_ids[MAX_LARGO:]
    subject_ids = subject_ids[:MAX_LARGO]
    # O teto é do TURNO, não de cada assunto: numa pergunta larga ele é
    # repartido, e cada assunto vira um resumo — o que vale hoje primeiro.
    por_um = max(600, (teto - 900) // len(subject_ids))   # 900: abertura, "e mais", rodapé
    partes = []
    if len(subject_ids) > 1:
        partes.append(f"_{len(subject_ids) + len(fora)} assuntos respondem a esta frase — "
                      "cada um aparece resumido; peça um deles pelo título para vê-lo inteiro._")
    resumido = len(subject_ids) > 1
    for sid in subject_ids:
        if resumido:
            # No resumo, o que vale hoje é o conteúdo; o resto vira contagem —
            # seção vazia com "cortado" é ruído, não informação.
            mud, conv = _mudancas(cx, sid), _conversa(cx, sid)
            n_mud = sum(1 for x in mud if x.startswith("- "))
            n_conv = sum(x.count("#") for x in conv[4:])
            rodape_um = ["", f"_mais: {n_mud} item(ns) que não valem mais · {n_conv} "
                         "mensagem(ns) de origem — peça este assunto sozinho para ver._"]
            secoes = [_cabecalho(cx, sid), _vigentes(cx, sid), rodape_um, []]
        else:
            secoes = [_cabecalho(cx, sid) + _subassuntos(cx, sid), _vigentes(cx, sid),
                      _mudancas(cx, sid)]
            secoes.append(_conversa_ou_lista(cx, sid, secoes_antes=secoes, teto=por_um))
        partes.append("\n".join(_cortar(secoes, por_um)))
    if fora:
        cur = cx.cursor()
        cur.execute("SELECT title FROM lucien_subjects WHERE id = ANY(%s::uuid[])"
                    " ORDER BY last_touched_at DESC", (fora,))
        partes.append("### E mais " + str(len(fora)) + " assunto(s), não mostrados:\n"
                      + "\n".join(f"- {r['title']}" for r in cur.fetchall()))
    rodape = ("\n\n---\n_Documentos e salas ligados ao assunto ainda não entram aqui: "
              "a indexação de documentos (F7.2) está em redesenho com o operador._\n"
              "_ESTADO é curado por um modelo: cada linha traz a origem — confira com "
              "`kobe-remember --ver <número>` antes de afirmar que algo foi decidido._")
    return "\n\n".join(partes) + rodape


def _sem_filho_do_pai(cx, subject_ids: list[str]) -> list[str]:
    """F7 (014): se a frase acertou um tema E uma conversa de dentro dele, fica o
    tema — ele já traz a conversa (e a lista das conversas)."""
    from bot.lucien import marcacao

    if len(subject_ids) < 2 or not marcacao._tem_tabela(cx):
        return subject_ids
    cur = cx.cursor()
    cur.execute("SELECT id, parent_id FROM lucien_subjects WHERE id = ANY(%s::uuid[])",
                (subject_ids,))
    pai = {str(r["id"]): (str(r["parent_id"]) if r["parent_id"] else None) for r in cur.fetchall()}
    todos = set(subject_ids)
    return [s for s in subject_ids if pai.get(s) not in todos]


def listar(cx, *, busca: str = "", limite: int = 40) -> list[dict]:
    cur = cx.cursor()
    filtro, params = "", []
    if busca:
        filtro = " AND (s.title ILIKE %s OR s.description ILIKE %s)"
        params = [f"%{busca}%", f"%{busca}%"]
    cur.execute(
        "SELECT s.id, s.title, s.last_touched_at, s.opened_at,"
        "       COUNT(c.id) AS total,"
        "       COUNT(c.id) FILTER (WHERE c.status = 'vigente') AS vigentes"
        "  FROM lucien_subjects s LEFT JOIN lucien_claims c ON c.subject_id = s.id"
        f" WHERE s.status = 'ativo'{filtro}"
        " GROUP BY s.id ORDER BY s.last_touched_at DESC LIMIT %s",
        (*params, limite))
    return cur.fetchall()


def fila_de_arrumacao(cx, *, dias_sozinho: int = 30, teto: int = 40) -> dict[str, list[dict]]:
    """Os assuntos que pedem a mão do operador — nada aqui se mexe sozinho.

    - **fusão**: assunto com UMA afirmação só há mais de `dias_sozinho` dias —
      provavelmente devia estar dentro de outro;
    - **divisão**: assunto com mais de `teto` afirmações — provavelmente virou
      tópico, e a régua dele é tamanho de documento.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT s.id, s.title, s.last_touched_at, COUNT(c.id) AS n"
        "  FROM lucien_subjects s JOIN lucien_claims c ON c.subject_id = s.id"
        " WHERE s.status = 'ativo'"
        " GROUP BY s.id"
        " HAVING COUNT(c.id) = 1 AND MAX(c.created_at) < NOW() - make_interval(days => %s)"
        " ORDER BY s.last_touched_at", (dias_sozinho,))
    fusao = cur.fetchall()
    cur.execute(
        "SELECT s.id, s.title, s.last_touched_at, COUNT(c.id) AS n"
        "  FROM lucien_subjects s JOIN lucien_claims c ON c.subject_id = s.id"
        " WHERE s.status = 'ativo'"
        " GROUP BY s.id HAVING COUNT(c.id) > %s ORDER BY COUNT(c.id) DESC", (teto,))
    return {"fusao": fusao, "divisao": cur.fetchall()}


# ── O complemento da busca ────────────────────────────────────────────────


class _Ponte:
    """A conexão do curador com a cara da ponte do bot (`query`/`scalar`) que a
    busca do `kobe-remember` espera."""

    def __init__(self, cx) -> None:
        self.cx = cx

    def query(self, sql, params=()):
        cur = self.cx.cursor()
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []

    def scalar(self, sql, params=()):
        r = self.query(sql, params)
        return next(iter(r[0].values())) if r else None


MAX_COMPLEMENTO = 6


def complemento_da_busca(cx, frase: str, subject_ids: list[str], *,
                         buscar=None, limite: int = MAX_COMPLEMENTO) -> list[str]:
    """F7 — "retoma o assunto X" não depende só da etiqueta: roda a mesma busca
    do `kobe-remember` (palavra, literal e sentido) com a frase do operador e
    acrescenta o que ela achou FORA das mensagens marcadas com o assunto (e a
    família dele). Assim, uma mensagem com etiqueta errada — ou ainda sem
    etiqueta — aparece do mesmo jeito.

    Falha da busca não derruba o dossiê: vira uma linha dizendo que a busca não
    rodou (que NÃO é "não há mais nada")."""
    from bot.search import query as q

    try:
        r = (buscar or q.buscar)(_Ponte(cx), frase, limite=12)
    except Exception as exc:  # noqa: BLE001
        cx.rollback()
        return ["", "### Também achado pela busca", "",
                f"_a busca não rodou ({type(exc).__name__}) — isto NÃO quer dizer que não há "
                "mais nada; rode `kobe-remember` com a mesma frase._"]
    if r.veredito == "FALHA":
        return ["", "### Também achado pela busca", "",
                f"_a busca falhou ({r.erro}) — rode `kobe-remember` com a mesma frase._"]
    ids = set()
    for sid in subject_ids:
        ids |= set(_fam(cx, sid))
    dentro: set[str] = set()
    from bot.lucien import marcacao

    if marcacao._tem_tabela(cx) and r.achados:
        cur = cx.cursor()
        cur.execute("SELECT DISTINCT message_id FROM message_subjects"
                    " WHERE subject_id = ANY(%s::uuid[]) AND message_id = ANY(%s::uuid[])",
                    (list(ids), [a.message_id for a in r.achados]))
        dentro = {str(x["message_id"]) for x in cur.fetchall()}
    fora = [a for a in r.achados if str(a.message_id) not in dentro][:limite]
    if not fora:
        return []
    linhas = ["", "### Também achado pela busca (fora da etiqueta deste assunto)", "",
              "_a etiqueta não é perfeita: estas mensagens respondem à frase pela busca, "
              "mas não estão marcadas com o assunto. Abra com `kobe-remember --ver <número>`._", ""]
    for a in fora:
        quem = "tu" if a.role == "user" else "agente"
        onde = f" · marcada em \"{a.assunto_do_trecho or a.assunto_principal}\"" \
            if (a.assunto_do_trecho or a.assunto_principal) else " · sem etiqueta"
        corpo = " ".join((a.corpo or "").split())
        corpo = corpo[:220] + ("…" if len(corpo) > 220 else "")
        linhas.append(f"- **#{a.seq}** · {str(a.created_at)[:10]} · {a.topico} · {quem}{onde}"
                      f" — {corpo}")
    return linhas
