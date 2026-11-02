"""O agrupamento retroativo — dar assunto ao que já está registrado (F7.1).

POR QUE EXISTE
--------------
A T11 (`assuntos.py`) cuida do que o curador grava daqui para a frente. Mas a
dor do operador é **retomar o passado** — e em 13/10/2026 havia 1.166
afirmações, cada uma com um assunto próprio. Sem este comando, "retoma o assunto
X" só funcionaria para o que fosse dito depois da F7. O operador aprovou o
agrupamento retroativo em 13/10/2026, com a ressalva: *"seja criterioso com o que
vai no campo assunto: tamanho de documento, não tamanho de tópico"*.

COMO, EM TRÊS PASSOS — SÓ O SEGUNDO USA MODELO
-----------------------------------------------
1. **Ordenar sem modelo.** As afirmações já têm vetor. Dentro de cada tópico,
   uma cadeia de vizinhos (a próxima é a mais próxima da anterior, busca exata no
   Postgres) põe as parecidas lado a lado — assim cada lote mostrado ao modelo
   tem afirmações da mesma conversa juntas, e o nome que ele dá sai melhor.
2. **O modelo atribui, em lotes, com o vocabulário acumulado.** Cada lote vê a
   lista de assuntos que já existem (os criados pelos lotes anteriores
   inclusive) e devolve, para cada afirmação, um apelido existente, um assunto
   novo, ou nada (avulsa). Os lotes são **sequenciais de propósito**: é o
   vocabulário acumulado que impede o lote 7 de recriar o que o lote 2 criou.
3. **O modelo revisa o vocabulário inteiro uma vez** — junta o que ficou
   duplicado e renomeia o que ficou largo demais. O código aplica, por fusão
   com histórico.

O que o modelo devolve passa pela mesma T11 do curador: título que duplicaria
um assunto existente vira reuso, e conta.

O CAMINHO DE VOLTA
------------------
Tudo é gravado com o `run_id` da rodada (`lucien_runs.mode = 'assuntos'`), e
cada atribuição vira evento com a lista das afirmações. `assuntos.desfazer_rodada`
desfaz a rodada inteira; `UPDATE lucien_claims SET subject_id = NULL` desfaz
tudo. Os rótulos antigos (`subject`) nunca são tocados.

RETOMÁVEL
---------
Cada lote é uma transação. Se a cota acabar no meio (o crivo da faxina bateu no
teto de gasto mensal), o comando para na primeira falha, diz onde parou, e
`--retomar <run_id>` continua de lá. O que já foi decidido não é pago de novo:
as afirmações deixadas avulsas são lembradas num arquivo de estado da rodada
(perdê-lo só faz o modelo revê-las).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from bot.lucien import assuntos, brain, store

logger = logging.getLogger("kobe.lucien.agrupamento")

POR_LOTE = 110          # ~33 mil caracteres de afirmações por chamada
TETO_CHARS_LOTE = 40000

SYSTEM = (
    "Você é LUCIEN, o arquivista da memória do Kobe. Agora você ORGANIZA: dá a "
    "cada afirmação já registrada o assunto (a 'thread') a que ela pertence, "
    "reusando os assuntos que existem. Você NÃO conversa, NÃO usa ferramentas e "
    "NÃO escreve nada além do JSON pedido."
)

REGUA = """\
O QUE É UM ASSUNTO — a régua é do operador, e ela é curta:

Assunto é o que, num aplicativo de chat, seria uma THREAD: uma conversa sobre
UMA coisa, que ele vai querer retomar depois dizendo "retoma o assunto X".
**Tamanho de DOCUMENTO, não tamanho de TÓPICO.** A referência de tamanho são
documentos como "decisão de aposentar o Chat Manager", "plano da faxina dos
depósitos", "brainstorm do onboarding conversacional".

  bom:  "Highlander v3 — F5, revisão semanal da memória"
        "Faxina dos depósitos de conhecimento do Kobe"
        "Migração do banco do Kobe para Postgres direto"
        "Coder — isolamento de sessões por worktree"
  ruim, GRANDE demais — cabe em "tudo que a gente faz aqui":
        "Manutenção do Kobe" · "Melhorias do Coder" · "Memória" · "Olimpo" ·
        "Infraestrutura" · "Decisões do operador"
  ruim, PEQUENO demais — é o rótulo de uma afirmação só:
        "Timeout do kobe-reflect subido para 90s"

REGRAS:
  1. REUSE. Se a afirmação pertence a um assunto da lista, use o apelido dele.
  2. Crie assunto novo só quando nenhum serve. Afirmações deste lote sobre a
     mesma conversa vão para o MESMO assunto novo.
  3. Quando o assunto é parte de um projeto com nome, ponha o nome no começo do
     título ("Highlander v3 — …", "Coder — …", "Flow — …"). É assim que uma
     pergunta larga ("retoma a V3 do Highlander") acha todos os assuntos dele.
  4. Afirmação pontual, que não pertence a conversa nenhuma (horário de um
     jogo, um pedido avulso já resolvido), fica SEM assunto (null). É permitido
     e é o certo nesses casos — mas é a exceção: a maioria do que está
     registrado pertence a alguma conversa.
  5. Não junte coisas só porque são do mesmo tópico ou do mesmo sistema. Duas
     frentes diferentes do mesmo projeto são dois assuntos.
"""

CONTRATO = """\
Responda SOMENTE com um objeto JSON, sem texto antes nem depois:

{
  "novos": [
    {"id": "N1", "titulo": "3 a 120 caracteres", "descricao": "uma frase: do que trata a conversa"}
  ],
  "atribuicoes": {"C1": "A3", "C2": "N1", "C3": null}
}

- TODA afirmação C… do lote tem que aparecer em "atribuicoes".
- O valor é um apelido da lista (A…), um id de "novos" (N…), ou null.
- Não invente apelido: A… que não está na lista é descartado.
"""

REPESCAGEM = """\
SEGUNDA PASSADA. As afirmações abaixo ficaram SEM assunto na primeira passada.
Uma auditoria independente achou que METADE delas pertence, sim, a uma conversa
retomável. Releia cada uma contra a lista inteira de assuntos.

Só deixe sem assunto (null) o que é de fato PONTUAL — um dado avulso que ninguém
retomaria como conversa (o horário de um jogo, um pedido isolado já resolvido,
uma preferência solta de formatação). Se a afirmação é parte de uma frente de
trabalho, de um projeto, de um incidente ou de uma decisão que tem história,
ela tem assunto: reuse um da lista ou crie um novo.
"""

REVISAO = """\
Abaixo está o VOCABULÁRIO INTEIRO de assuntos, com quantas afirmações cada um
tem e duas delas de amostra. Revise-o com a régua acima e responda:

{
  "fundir": [{"de": "A7", "para": "A2", "motivo": "uma frase"}],
  "renomear": [{"assunto": "A4", "titulo": "novo título", "motivo": "uma frase"}]
}

- "fundir" quando dois assuntos são a MESMA conversa com nomes diferentes.
  NÃO funda duas frentes diferentes do mesmo projeto.
- "renomear" quando o título está largo demais ("Manutenção do Kobe") ou não
  diz do que se trata. Só renomeie se o novo título for claramente melhor.
- Listas vazias são a resposta certa quando o vocabulário está bom.
"""


def _e_resposta(d: dict) -> bool:
    return any(c in d for c in ("atribuicoes", "novos", "fundir", "renomear"))


def _json(bruto: str) -> dict:
    return brain.extrair_json(bruto, reconhece=_e_resposta)


@dataclass
class Relatorio:
    run_id: str = ""
    lotes: int = 0
    chamadas: int = 0
    atribuidas: int = 0
    avulsas: int = 0
    nao_respondidas: int = 0
    apelidos_invalidos: int = 0
    criados: int = 0
    evitados: int = 0
    fundidos: int = 0
    renomeados: int = 0
    repescadas: int = 0
    erro: Optional[str] = None
    falhas: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        return (f"rodada {self.run_id} · {self.lotes} lote(s), {self.chamadas} chamada(s) · "
                f"{self.atribuidas} afirmação(ões) com assunto, {self.avulsas} avulsa(s), "
                f"{self.nao_respondidas} sem resposta · {self.criados} assunto(s) criado(s), "
                f"{self.evitados} duplicata(s) evitada(s), {self.fundidos} fundido(s), "
                f"{self.renomeados} renomeado(s), {self.repescadas} avulsa(s) repescada(s)"
                + (f" · PAROU: {self.erro}" if self.erro else ""))


# ── Estado da rodada (só o que não está no banco: as avulsas) ──────────────


def _estado_path(kobe_home: str, run_id: str) -> Path:
    d = Path(kobe_home) / "user-data" / "lucien"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"agrupamento-{run_id}.json"


def _ler_estado(kobe_home: str, run_id: str) -> dict:
    p = _estado_path(kobe_home, run_id)
    if p.exists():
        return json.loads(p.read_text())
    return {"avulsas": [], "revisado": False}


def _gravar_estado(kobe_home: str, run_id: str, estado: dict) -> None:
    p = _estado_path(kobe_home, run_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado))
    os.replace(tmp, p)


# ── Passo 1: ordenar ──────────────────────────────────────────────────────


def pendentes_ordenadas(cx, *, ignorar: set[str]) -> list[dict]:
    """As afirmações sem assunto, em cadeia de vizinhos dentro de cada tópico.

    Busca exata no Postgres: a próxima é a mais próxima da anterior entre as
    que faltam. Sem vetor (raro), entram no fim do tópico, em ordem de data.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT c.id, c.topic_id, COALESCE(t.current_name, '?') AS topico,"
        "       c.kind, c.status, c.valid_from, c.subject, c.statement,"
        "       c.embedding IS NOT NULL AS tem_vetor"
        "  FROM lucien_claims c JOIN topics t ON t.id = c.topic_id"
        " WHERE c.subject_id IS NULL"
        " ORDER BY t.current_name, c.valid_from")
    todas = [r for r in cur.fetchall() if str(r["id"]) not in ignorar]
    por_topico: dict[str, list[dict]] = {}
    for r in todas:
        por_topico.setdefault(str(r["topic_id"]), []).append(r)

    saida: list[dict] = []
    for linhas in por_topico.values():
        com = {str(r["id"]): r for r in linhas if r["tem_vetor"]}
        sem = [r for r in linhas if not r["tem_vetor"]]
        if com:
            atual = next(iter(com))
            ordem = [atual]
            restantes = set(com) - {atual}
            while restantes:
                cur.execute(
                    "SELECT id FROM lucien_claims WHERE id = ANY(%s::uuid[])"
                    " ORDER BY embedding <=> (SELECT embedding FROM lucien_claims"
                    "                          WHERE id = %s) LIMIT 1",
                    (list(restantes), atual))
                atual = str(cur.fetchone()["id"])
                ordem.append(atual)
                restantes.discard(atual)
            saida += [com[i] for i in ordem]
        saida += sem
    return saida


def _lotes(linhas: list[dict]) -> list[list[dict]]:
    lotes, atual, chars = [], [], 0
    for r in linhas:
        tam = len(r["statement"]) + len(r["subject"]) + 60
        if atual and (len(atual) >= POR_LOTE or chars + tam > TETO_CHARS_LOTE):
            lotes.append(atual)
            atual, chars = [], 0
        atual.append(r)
        chars += tam
    if atual:
        lotes.append(atual)
    return lotes


# ── O vocabulário mostrado ────────────────────────────────────────────────


def vocabulario(cx, *, amostras: int = 0) -> dict[str, dict]:
    """Todos os assuntos ativos, por apelido. Global e plano — é a lista inteira."""
    cur = cx.cursor()
    cur.execute(
        "SELECT s.id, s.title, s.description,"
        "       (SELECT COUNT(*) FROM lucien_claims c WHERE c.subject_id = s.id) AS n"
        "  FROM lucien_subjects s WHERE s.status = 'ativo'"
        " ORDER BY s.title")
    linhas = cur.fetchall()
    if amostras:
        for r in linhas:
            cur.execute("SELECT statement FROM lucien_claims WHERE subject_id = %s"
                        " ORDER BY valid_from DESC LIMIT %s", (r["id"], amostras))
            r["amostras"] = [x["statement"] for x in cur.fetchall()]
    return {f"A{i}": r for i, r in enumerate(linhas, start=1)}


def _linhas_vocabulario(voc: dict[str, dict], *, com_amostras: bool = False) -> list[str]:
    if not voc:
        return ["(nenhum ainda)"]
    out = []
    for ap, a in voc.items():
        desc = f" — {a['description']}" if a.get("description") else ""
        out.append(f"{ap}  {a['title']}{desc} · {a['n']} afirmação(ões)")
        if com_amostras:
            for x in a.get("amostras") or []:
                out.append(f"      · {x[:220]}")
    return out


def montar_prompt(voc: dict[str, dict], lote: list[dict]) -> tuple[str, dict[str, dict]]:
    apelidos = {f"C{i}": r for i, r in enumerate(lote, start=1)}
    linhas = ["Organize as afirmações abaixo por ASSUNTO.", "", REGUA, "",
              "─" * 60, "ASSUNTOS QUE JÁ EXISTEM (reuse)", "─" * 60]
    linhas += _linhas_vocabulario(voc)
    linhas += ["", "─" * 60, "AS AFIRMAÇÕES DESTE LOTE", "─" * 60]
    tipos = {"decision": "decisão", "open": "pendência", "preference": "preferência",
             "fact": "fato"}
    for ap, r in apelidos.items():
        data = r["valid_from"].strftime("%d/%m/%Y")
        estado = "" if r["status"] == "vigente" else f" ({r['status']})"
        linhas.append(f"{ap} · {tipos.get(r['kind'], r['kind'])}{estado} · {data} · "
                      f"tópico {r['topico']} — {r['subject']}: {r['statement']}")
    linhas += ["", CONTRATO]
    return "\n".join(linhas), apelidos


# ── Passo 2: aplicar um lote ──────────────────────────────────────────────


_RE_ID = re.compile(r"^[ACN]\d+$")


def aplicar_lote(cx, resposta: dict, voc: dict[str, dict], apelidos: dict[str, dict],
                 *, run_id: str, rel: Relatorio, de: Optional[str] = None,
                 extra: Optional[dict] = None) -> list[str]:
    """Grava as atribuições de um lote. Devolve os ids deixados avulsos.

    Chamador dá o commit. Tudo que vier torto (apelido inexistente, título fora
    do tamanho) deixa a afirmação SEM assunto e é contado — nunca inventa."""
    novos_ids: dict[str, str] = {}
    for n in resposta.get("novos") or []:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip().upper()
        titulo = " ".join(str(n.get("titulo") or "").split())
        if not _RE_ID.match(nid) or not (assuntos.TITULO_MIN <= len(titulo) <= assuntos.TITULO_MAX):
            continue
        # A data de abertura provisória é a de agora; `tocar` (abaixo) a corrige
        # para a data do fato mais antigo atribuído.
        primeiro = min((r["valid_from"] for k, r in apelidos.items()
                        if str((resposta.get("atribuicoes") or {}).get(k) or "").upper() == nid),
                       default=None)
        if primeiro is None:
            continue
        sid, desfecho = assuntos.resolver_novo(
            cx, titulo, quando=primeiro, descricao=str(n.get("descricao") or ""),
            run_id=run_id, criado_por="agrupamento", motivo="agrupamento retroativo")
        if desfecho == "criado":
            rel.criados += 1
        else:
            rel.evitados += 1
        novos_ids[nid] = sid

    atribuicoes = resposta.get("atribuicoes") or {}
    por_assunto: dict[str, list[dict]] = {}
    avulsas: list[str] = []
    for ap, r in apelidos.items():
        if ap not in atribuicoes:
            rel.nao_respondidas += 1
            continue
        alvo = atribuicoes.get(ap)
        if alvo is None or str(alvo).strip().lower() in ("", "null", "none"):
            rel.avulsas += 1
            avulsas.append(str(r["id"]))
            continue
        alvo = str(alvo).strip().upper()
        if alvo in novos_ids:
            sid = novos_ids[alvo]
        elif alvo in voc:
            sid = assuntos._seguir_fusao(cx.cursor(), str(voc[alvo]["id"]))
        else:
            rel.apelidos_invalidos += 1
            continue
        por_assunto.setdefault(sid, []).append(r)

    cur = cx.cursor()
    for sid, linhas in por_assunto.items():
        ids = [str(r["id"]) for r in linhas]
        if de is None:
            cur.execute("UPDATE lucien_claims SET subject_id = %s"
                        " WHERE id = ANY(%s::uuid[]) AND subject_id IS NULL RETURNING id",
                        (sid, ids))
        elif sid == de:
            continue   # ficou onde estava — nada a gravar
        else:
            cur.execute("UPDATE lucien_claims SET subject_id = %s"
                        " WHERE id = ANY(%s::uuid[]) AND subject_id = %s RETURNING id",
                        (sid, ids, de))
        feitos = [str(x["id"]) for x in cur.fetchall()]
        if not feitos:
            continue
        rel.atribuidas += len(feitos)
        datas = [r["valid_from"] for r in linhas]
        assuntos.tocar(cx, sid, min(datas))
        assuntos.tocar(cx, sid, max(datas))
        cur.execute("UPDATE lucien_subjects SET home_topic_id = COALESCE(home_topic_id, %s)"
                    " WHERE id = %s", (linhas[0]["topic_id"], sid))
        detalhe = {**(extra or {}), "afirmacoes": feitos}
        if de is not None:
            detalhe["dividido_de"] = str(de)
        assuntos.evento(cx, sid, "atribuido", None, detalhe,
                        run_id=run_id, actor="agrupamento")
    return avulsas


# ── Passo 3: revisar o vocabulário ────────────────────────────────────────


def aplicar_revisao(cx, resposta: dict, voc: dict[str, dict], *, run_id: str,
                    rel: Relatorio) -> None:
    fundidos: set[str] = set()
    for f in resposta.get("fundir") or []:
        if not isinstance(f, dict):
            continue
        de, para = str(f.get("de") or "").upper(), str(f.get("para") or "").upper()
        if de not in voc or para not in voc or de == para or de in fundidos:
            continue
        try:
            assuntos.fundir(cx, str(voc[de]["id"]), str(voc[para]["id"]),
                            actor="agrupamento", run_id=run_id,
                            motivo=str(f.get("motivo") or ""))
            fundidos.add(de)
            rel.fundidos += 1
        except (ValueError, LookupError) as exc:
            rel.falhas.append(f"fusão {de}→{para}: {exc}")
    for r in resposta.get("renomear") or []:
        if not isinstance(r, dict):
            continue
        ap = str(r.get("assunto") or "").upper()
        titulo = " ".join(str(r.get("titulo") or "").split())
        if ap not in voc or ap in fundidos or not (
                assuntos.TITULO_MIN <= len(titulo) <= assuntos.TITULO_MAX):
            continue
        try:
            renomear(cx, str(voc[ap]["id"]), titulo, actor="agrupamento",
                     run_id=run_id, motivo=str(r.get("motivo") or ""))
            rel.renomeados += 1
        except ValueError as exc:
            rel.falhas.append(f"renomear {ap}: {exc}")


def renomear(cx, subject_id: str, titulo: str, *, actor: str = "operador",
             run_id: Optional[str] = None, motivo: str = "") -> None:
    """Troca título e slug, com a imagem de antes no histórico. Recusa título
    cujo slug já pertence a outro assunto — aí o certo é fundir, não renomear."""
    novo_slug = assuntos.slug(titulo)
    cur = cx.cursor()
    cur.execute("SELECT id FROM lucien_subjects WHERE slug = %s AND id <> %s",
                (novo_slug, subject_id))
    if cur.fetchone() is not None:
        raise ValueError("já existe assunto com esse título — funda em vez de renomear")
    antes = assuntos.imagem(cx, subject_id)
    cur.execute("UPDATE lucien_subjects SET title = %s, slug = %s, embedding = NULL,"
                " model = NULL, embedded_at = NULL, updated_at = NOW() WHERE id = %s",
                (titulo, novo_slug, subject_id))
    assuntos.evento(cx, subject_id, "renomeado", antes,
                    {"titulo": titulo, "motivo": (motivo or "").strip()[:400]},
                    run_id=run_id, actor=actor)


# ── O comando inteiro ─────────────────────────────────────────────────────


Pensador = Callable[[str, str], str]   # (prompt, system) -> saída crua


def _pensador_padrao(kobe_home: str) -> Pensador:
    def pensar(prompt: str, system: str) -> str:
        # O modelo PADRÃO (o do incremental), não o da reconstrução: o volume é
        # pequeno (~12 chamadas) e o vocabulário que sai daqui fica para sempre.
        # Qual modelo é esse sai do `.env` — o Kobe não fixa modelo.
        return brain.chamar(prompt, kobe_home=kobe_home, reconstrucao=False,
                            system=system, timeout=600)
    return pensar


def rodar(cx, *, kobe_home: str, retomar: Optional[str] = None,
          limite_lotes: Optional[int] = None, revisar: bool = True,
          pensador: Optional[Pensador] = None,
          log: Callable[[str], None] = print) -> Relatorio:
    """O agrupamento retroativo. Commita lote a lote; para na primeira falha."""
    pensar = pensador or _pensador_padrao(kobe_home)
    rel = Relatorio()
    if retomar:
        rel.run_id = retomar
    else:
        rel.run_id = store.abrir_rodada(cx, mode="assuntos", topic_id=None, lote=None,
                                        model="(padrão)")
        cx.commit()
    estado = _ler_estado(kobe_home, rel.run_id)
    ignorar = set(estado["avulsas"])

    pend = pendentes_ordenadas(cx, ignorar=ignorar)
    lotes = _lotes(pend)
    log(f"agrupamento {rel.run_id}: {len(pend)} afirmação(ões) sem assunto, "
        f"{len(lotes)} lote(s)")
    for i, lote in enumerate(lotes[:limite_lotes] if limite_lotes else lotes, start=1):
        voc = vocabulario(cx)
        prompt, apelidos = montar_prompt(voc, lote)
        try:
            bruto = pensar(prompt, SYSTEM)
            rel.chamadas += 1
            resposta = _json(bruto)
            if not isinstance(resposta, dict):
                raise brain.CerebroIndisponivel("a resposta não é um objeto JSON")
        except Exception as exc:  # noqa: BLE001 — para, diz onde, e é retomável
            cx.rollback()
            rel.erro = f"lote {i}/{len(lotes)}: {exc}"
            break
        try:
            avulsas = aplicar_lote(cx, resposta, voc, apelidos, run_id=rel.run_id, rel=rel)
            cx.commit()
        except Exception as exc:  # noqa: BLE001
            cx.rollback()
            rel.erro = f"lote {i}/{len(lotes)} não gravou: {exc}"
            break
        rel.lotes += 1
        estado["avulsas"] = sorted(set(estado["avulsas"]) | set(avulsas))
        _gravar_estado(kobe_home, rel.run_id, estado)
        log(f"  lote {i}/{len(lotes)}: {len(lote)} afirmação(ões) · "
            f"vocabulário agora com {len(vocabulario(cx))} assunto(s)")

    # Repescagem: UMA passada sobre as avulsas, com o vocabulário inteiro. Medido
    # no laboratório (13/10/2026): na primeira passada o modelo deixou avulsas
    # 76 de 1.179, e um julgador independente achou que só metade era pontual.
    if rel.erro is None and not limite_lotes and estado["avulsas"] and not estado.get("repescado"):
        cur = cx.cursor()
        cur.execute(
            "SELECT c.id, c.topic_id, COALESCE(t.current_name, '?') AS topico, c.kind,"
            "       c.status, c.valid_from, c.subject, c.statement"
            "  FROM lucien_claims c JOIN topics t ON t.id = c.topic_id"
            " WHERE c.id = ANY(%s::uuid[]) AND c.subject_id IS NULL"
            " ORDER BY t.current_name, c.valid_from", (estado["avulsas"],))
        soltas = cur.fetchall()
        restam: list[str] = []
        for lote in _lotes(soltas):
            voc = vocabulario(cx)
            prompt, apelidos = montar_prompt(voc, lote)
            try:
                bruto = pensar(REPESCAGEM + "\n" + prompt, SYSTEM)
                rel.chamadas += 1
                resposta = _json(bruto)
                antes = rel.avulsas
                restam += aplicar_lote(cx, resposta, voc, apelidos, run_id=rel.run_id, rel=rel)
                rel.avulsas = antes   # a contagem final é a de `restam`, abaixo
                cx.commit()
            except Exception as exc:  # noqa: BLE001
                cx.rollback()
                rel.erro = f"repescagem: {exc}"
                break
        if rel.erro is None:
            rel.repescadas = len(soltas) - len(restam)
            estado["avulsas"] = sorted(set(restam))
            estado["repescado"] = True
            rel.avulsas = len(estado["avulsas"])
            _gravar_estado(kobe_home, rel.run_id, estado)
            log(f"  repescagem: {rel.repescadas} de {len(soltas)} avulsa(s) ganharam assunto")

    if revisar and rel.erro is None and not limite_lotes and not estado.get("revisado"):
        voc = vocabulario(cx, amostras=2)
        prompt = "\n".join(["Revise o vocabulário de assuntos.", "", REGUA, "",
                            "─" * 60, "O VOCABULÁRIO", "─" * 60,
                            *_linhas_vocabulario(voc, com_amostras=True), "", REVISAO])
        try:
            bruto = pensar(prompt, SYSTEM)
            rel.chamadas += 1
            resposta = _json(bruto)
            aplicar_revisao(cx, resposta if isinstance(resposta, dict) else {}, voc,
                            run_id=rel.run_id, rel=rel)
            cx.commit()
            estado["revisado"] = True
            _gravar_estado(kobe_home, rel.run_id, estado)
        except Exception as exc:  # noqa: BLE001
            cx.rollback()
            rel.erro = f"revisão do vocabulário: {exc}"

    cx.cursor().execute(
        "UPDATE lucien_runs SET finished_at = NOW(), ok = %s, error = %s,"
        " subjects_created = subjects_created + %s, subjects_avoided = subjects_avoided + %s"
        " WHERE id = %s",
        (rel.erro is None, rel.erro, rel.criados, rel.evitados, rel.run_id))
    cx.commit()
    return rel


# ── A divisão de um assunto que virou tópico ──────────────────────────────

DIVISAO = """\
TODAS as afirmações abaixo estão hoje num único assunto, "{titulo}", que ficou
GRANDE DEMAIS: {n} afirmações — virou tópico, e a régua é tamanho de documento.
Redistribua-as em assuntos do tamanho certo. Reuse assuntos da lista quando
couber; crie novos quando não. Uma afirmação pode continuar em "{apelido}" se
ela é de fato o núcleo dele (use o apelido {apelido}).
"""


def dividir(cx, subject_id: str, *, kobe_home: str, actor: str = "operador",
            pensador: Optional[Pensador] = None, commit: bool = True,
            extra: Optional[dict] = None) -> Relatorio:
    """Divide um assunto grande. Uma chamada de modelo; commit no fim (ou não,
    com `commit=False` — a revisão da memória é tudo-ou-nada e commita ela).

    Desfaz com `assuntos.desfazer_rodada(run_id)` — as afirmações voltam ao
    assunto de origem, que é reativado se tiver sido arquivado."""
    pensar = pensador or _pensador_padrao(kobe_home)
    rel = Relatorio()
    rel.run_id = store.abrir_rodada(cx, mode="assuntos", topic_id=None, lote=None,
                                    model="(padrão)")
    cur = cx.cursor()
    cur.execute("SELECT title FROM lucien_subjects WHERE id = %s AND status = 'ativo'",
                (subject_id,))
    alvo = cur.fetchone()
    if alvo is None:
        raise LookupError("assunto inexistente ou não ativo")
    cur.execute(
        "SELECT c.id, c.topic_id, COALESCE(t.current_name, '?') AS topico, c.kind,"
        "       c.status, c.valid_from, c.subject, c.statement"
        "  FROM lucien_claims c JOIN topics t ON t.id = c.topic_id"
        " WHERE c.subject_id = %s ORDER BY c.valid_from", (subject_id,))
    linhas = cur.fetchall()
    voc = vocabulario(cx)
    apelido_alvo = next((ap for ap, a in voc.items() if str(a["id"]) == str(subject_id)), "")
    prompt, apelidos = montar_prompt(voc, linhas)
    prompt = DIVISAO.format(titulo=alvo["title"], n=len(linhas), apelido=apelido_alvo) \
        + "\n" + prompt
    try:
        bruto = pensar(prompt, SYSTEM)
        rel.chamadas += 1
        resposta = _json(bruto)
        if not isinstance(resposta, dict):
            raise brain.CerebroIndisponivel("a resposta não é um objeto JSON")
        aplicar_lote(cx, resposta, voc, apelidos, run_id=rel.run_id, rel=rel,
                     de=str(subject_id), extra=extra)
        cur.execute("SELECT COUNT(*) AS n FROM lucien_claims WHERE subject_id = %s",
                    (subject_id,))
        if int(cur.fetchone()["n"]) == 0:
            cur.execute("UPDATE lucien_subjects SET status = 'arquivado', updated_at = NOW()"
                        " WHERE id = %s", (subject_id,))
            assuntos.evento(cx, str(subject_id), "arquivado", None,
                            {"dividido_na_rodada": rel.run_id}, run_id=rel.run_id,
                            actor=actor)
    except Exception as exc:  # noqa: BLE001
        rel.erro = str(exc)
        if not commit:
            return rel   # quem chama desfaz a transação inteira
        cx.rollback()
        rel.run_id = store.abrir_rodada(cx, mode="assuntos", topic_id=None, lote=None,
                                        model="(padrão)")
    cx.cursor().execute(
        "UPDATE lucien_runs SET finished_at = NOW(), ok = %s, error = %s,"
        " subjects_created = %s, subjects_avoided = %s WHERE id = %s",
        (rel.erro is None, rel.erro, rel.criados, rel.evitados, rel.run_id))
    if commit:
        cx.commit()
    return rel
