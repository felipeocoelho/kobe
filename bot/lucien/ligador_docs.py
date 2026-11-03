"""O documento ligado ao assunto (Highlander v3, F7.2) — o ligador.

POR QUE EXISTE
--------------
"Retoma o assunto X" tem que trazer os documentos de X — o plano da sala, a
análise, o material. Pela busca sozinha (a frase do operador contra o texto dos
documentos) isso falha justo quando a frase usa outras palavras: medido em 03/11,
recall@5 de 0,66–0,71 contra 0,89 da ligação por modelo. O operador aprovou a
ligação por modelo no mesmo dia (opção 1).

QUEM DECIDE O QUÊ
-----------------
1. o CÓDIGO escolhe os documentos (novos ou mudados desde a última leitura) e
   monta, para cada um, o mesmo resumo que foi medido: caminho, título, os
   títulos internos e o começo do texto;
2. o MODELO (o padrão do Kobe, pela assinatura — nunca um modelo fixo) diz a que
   assuntos da lista cada documento PERTENCE;
3. o CÓDIGO valida: documento e assunto têm que estar no lote mostrado, e cada
   documento leva no máximo `MAX_POR_DOC` assuntos. O resto é descartado e
   CONTADO (`lucien_runs.doc_links_rejected`).

O documento lido que não pertence a nada (a maioria) também fica marcado como
lido (`doc_sources.linked_sha256`): é o que impede de pagá-lo de novo.

O QUE O LIGADOR NUNCA FAZ
-------------------------
- não lê arquivo: lê `doc_sources.content`, que o indexador já guardou;
- não toca em ligação que não seja dele (`origin = 'ligador'`). A ligação por
  AUTORIA (o resumo por assunto que a F7.3 vai escrever) e a do operador ficam
  como estão — e documento com ligação por autoria nem entra na fila;
- não religa documentos antigos quando nasce um assunto novo. Esses continuam
  achados pela busca, que roda junto no dossiê.

A CHAVE
-------
`DOC_SUBJECTS_ENABLED` — desligada quando ausente (o `.env.example` traz ligada:
é requisito do produto, e o custo é cota da assinatura, não dinheiro).

NENHUMA FUNÇÃO DAQUI DÁ COMMIT, exceto `rodar` (que commita lote a lote).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("kobe.lucien.ligador_docs")

LOTE = 70                 # documentos por chamada (o tamanho medido)
MAX_POR_DOC = 4           # acima disso é espalhamento, não pertença
CABS_CHARS = 400          # títulos internos mostrados
INICIO_CHARS = 900        # começo do texto mostrado
DESC_CHARS = 90           # descrição do assunto na lista
ASSENTADO_S = 600         # documento mexido há menos que isto espera (edição em curso)


def _env(nome: str) -> str:
    return (os.getenv(nome) or "").strip()


def habilitado() -> bool:
    """`DOC_SUBJECTS_ENABLED` — desligada quando ausente."""
    return _env("DOC_SUBJECTS_ENABLED").lower() in ("1", "true", "yes", "on", "sim")


def maximo_rodadas_hora() -> int:
    try:
        return max(1, int(_env("DOC_SUBJECTS_MAX_RUNS_HORA") or 4))
    except ValueError:
        return 4


_DISPONIVEL: dict[str, bool] = {}


def tem_tabela(cx) -> bool:
    """A 016 está aplicada neste banco? Código novo sobre banco velho (o `git pull`
    sem `migrate.py up`) degrada para o dossiê só com a busca."""
    chave = str(getattr(cx, "info", None) and cx.info.dsn)
    if chave not in _DISPONIVEL:
        cur = cx.cursor()
        cur.execute("SELECT to_regclass('doc_subjects') IS NOT NULL AS ok")
        _DISPONIVEL[chave] = bool(cur.fetchone()["ok"])
    return _DISPONIVEL[chave]


def ativo(cx) -> bool:
    if not habilitado():
        return False
    try:
        return tem_tabela(cx)
    except Exception:  # noqa: BLE001
        return False


# ── A fila ────────────────────────────────────────────────────────────────


def pendentes(cx, *, assentado_s: int = ASSENTADO_S, limite: Optional[int] = None) -> list[dict]:
    """Documentos com texto, presentes no disco, novos ou mudados desde a última
    leitura do ligador — e parados há `assentado_s` (um plano sendo escrito não
    é relido a cada salvamento). Documento com ligação por AUTORIA fica fora."""
    cur = cx.cursor()
    cur.execute(
        "SELECT s.id, s.rel_path, s.title, s.nature, s.content, s.sha256, s.file_mtime"
        "  FROM doc_sources s"
        " WHERE s.indexable AND s.status = 'presente' AND s.content IS NOT NULL"
        "   AND s.linked_sha256 IS DISTINCT FROM s.sha256"
        "   AND s.updated_at <= NOW() - make_interval(secs => %s)"
        "   AND NOT EXISTS (SELECT 1 FROM doc_subjects d WHERE d.doc_id = s.id"
        "                    AND d.origin = 'autoria' AND d.retired_at IS NULL)"
        " ORDER BY s.updated_at, s.id" + (" LIMIT %s" if limite else ""),
        (assentado_s, limite) if limite else (assentado_s,))
    return cur.fetchall()


def quantos_pendentes(cx, *, assentado_s: int = ASSENTADO_S) -> int:
    cur = cx.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM doc_sources s"
        " WHERE s.indexable AND s.status = 'presente' AND s.content IS NOT NULL"
        "   AND s.linked_sha256 IS DISTINCT FROM s.sha256"
        "   AND s.updated_at <= NOW() - make_interval(secs => %s)", (assentado_s,))
    return int(cur.fetchone()["n"])


def cota_disponivel(cx) -> bool:
    cur = cx.cursor()
    cur.execute("SELECT COUNT(*) AS n FROM lucien_runs WHERE mode = 'documentos'"
                "   AND started_at > NOW() - INTERVAL '1 hour'")
    return int(cur.fetchone()["n"]) < maximo_rodadas_hora()


def devido(cx) -> bool:
    """A pergunta barata do tick: há documento para ler, e cabe na cota?"""
    return ativo(cx) and cota_disponivel(cx) and quantos_pendentes(cx) > 0


# ── O que o modelo vê ─────────────────────────────────────────────────────


def vocabulario(cx) -> dict[str, dict]:
    """`{apelido: assunto}` — todos os assuntos ATIVOS, temas e conversas. O
    apelido é curto (`S12`) porque o modelo copia apelido melhor que UUID."""
    cur = cx.cursor()
    cur.execute(
        "SELECT s.id, s.title, s.description, s.kind, p.title AS pai"
        "  FROM lucien_subjects s LEFT JOIN lucien_subjects p ON p.id = s.parent_id"
        " WHERE s.status = 'ativo'"
        " ORDER BY COALESCE(p.title, s.title), s.parent_id NULLS FIRST, s.title")
    return {f"S{i}": dict(r) for i, r in enumerate(cur.fetchall(), start=1)}


def linhas_vocabulario(voc: dict[str, dict]) -> list[str]:
    out = []
    for ap, s in voc.items():
        desc = " ".join((s.get("description") or "").split())
        if len(desc) > DESC_CHARS:
            desc = desc[:DESC_CHARS].rstrip() + "…"
        onde = f" (conversa em \"{s['pai']}\")" if s.get("pai") else ""
        out.append(f"{ap}  {s['title']}{onde}" + (f" — {desc}" if desc else ""))
    return out


_RE_CAB = re.compile(r"^#{1,3}\s+(.+)$", re.M)


def resumo_doc(d: dict) -> str:
    """O que o modelo vê de um documento — o MESMO formato medido em 03/11."""
    t = d.get("content") or ""
    cabs = " | ".join(m.group(1).strip() for m in _RE_CAB.finditer(t))[:CABS_CHARS]
    corpo = " ".join(t.split())[:INICIO_CHARS]
    return f"[D{d['id']}] {d['rel_path']}\n  títulos: {cabs}\n  início: {corpo}"


SYSTEM = (
    "Você é LUCIEN, o arquivista da memória do Kobe. Você liga documentos aos "
    "assuntos de conversa de um operador com o agente dele. Você NÃO conversa, "
    "NÃO usa ferramentas e NÃO escreve nada além do JSON pedido. Quando um "
    "documento não pertence a assunto nenhum, dizer isso é a resposta certa — e "
    "é a mais comum."
)

INSTRUCAO = """\
Você liga documentos a assuntos de conversa de um operador com seu agente.

Para cada documento abaixo, diga a quais assuntos da lista ele PERTENCE — isto é,
ele deveria aparecer quando o operador pedir "retoma o assunto X". Só ligue
quando o documento é claramente SOBRE aquele assunto; mencionar de passagem não
basta. A maioria dos documentos não pertence a nenhum. No máximo {max} assuntos
por documento. Um assunto marcado "(conversa em …)" é uma conversa dentro de um
tema: prefira a conversa quando o documento é sobre ela; o tema, quando é sobre
o tema inteiro.

Responda só JSON: {{"ligacoes": {{"D<id>": ["S<n>", ...], ...}}}} — só com os
documentos que têm ligação. Sem nenhuma: {{"ligacoes": {{}}}}.
"""


def montar_prompt(voc: dict[str, dict], docs: list[dict]) -> str:
    """Instrução e lista de assuntos PRIMEIRO, documentos depois: o começo do
    prompt é igual entre os lotes da mesma rodada, e o cache da CLI paga esse
    prefixo uma vez só."""
    return "\n".join([
        INSTRUCAO.format(max=MAX_POR_DOC), "", "─" * 60, "ASSUNTOS", "─" * 60,
        *(linhas_vocabulario(voc) or ["(nenhum)"]),
        "", "─" * 60, "DOCUMENTOS", "─" * 60, "",
        "\n\n".join(resumo_doc(d) for d in docs),
    ])


def _e_resposta(d: dict) -> bool:
    return "ligacoes" in d


# ── O que o modelo devolve, já validado ───────────────────────────────────


@dataclass
class Relatorio:
    run_id: str = ""
    lotes: int = 0
    chamadas: int = 0
    lidos: int = 0
    ligacoes: int = 0           # ligações vigentes criadas
    aposentadas: int = 0
    rejeitadas: int = 0         # apelido inexistente, documento fora do lote, excesso
    erro: Optional[str] = None
    por_doc: dict[int, list[str]] = field(default_factory=dict)

    def resumo(self) -> str:
        return (f"rodada {self.run_id} · {self.lotes} lote(s), {self.chamadas} chamada(s) · "
                f"{self.lidos} documento(s) lido(s) · {self.ligacoes} ligação(ões) nova(s), "
                f"{self.aposentadas} aposentada(s), {self.rejeitadas} descartada(s)"
                + (f" · ERRO: {self.erro}" if self.erro else ""))


def interpretar(resposta: dict, voc: dict[str, dict], docs: list[dict],
                rel: Relatorio) -> dict[int, list[str]]:
    """`{doc_id: [subject_id, ...]}` só com o que é válido. Todo documento do lote
    aparece (com lista vazia quando não pertence a nada) — ler é registrar."""
    ids = {int(d["id"]) for d in docs}
    out: dict[int, list[str]] = {i: [] for i in ids}
    lig = resposta.get("ligacoes") if isinstance(resposta, dict) else None
    if not isinstance(lig, dict):
        return out
    for chave, aps in lig.items():
        try:
            did = int(str(chave).strip().upper().lstrip("D"))
        except ValueError:
            rel.rejeitadas += 1
            continue
        if did not in ids:
            rel.rejeitadas += 1
            continue
        for ap in (aps if isinstance(aps, list) else [aps]):
            s = voc.get(str(ap or "").strip().upper())
            if s is None:
                rel.rejeitadas += 1
                continue
            sid = str(s["id"])
            if sid in out[did]:
                continue
            if len(out[did]) >= MAX_POR_DOC:
                rel.rejeitadas += 1
                continue
            out[did].append(sid)
    return out


def aplicar(cx, ligacoes: dict[int, list[str]], docs: list[dict], *, run_id: str,
            rel: Relatorio) -> None:
    """Grava. Por documento: aposenta as ligações DO LIGADOR que a leitura nova
    não repete, cria as que faltam, e marca a versão lida."""
    sha = {int(d["id"]): d["sha256"] for d in docs}
    cur = cx.cursor()
    for did, sids in ligacoes.items():
        cur.execute(
            "UPDATE doc_subjects SET retired_at = NOW(), retired_run_id = %s"
            " WHERE doc_id = %s AND origin = 'ligador' AND retired_at IS NULL"
            "   AND NOT (subject_id = ANY(%s::uuid[]))", (run_id, did, sids))
        rel.aposentadas += cur.rowcount or 0
        for sid in sids:
            cur.execute(
                "INSERT INTO doc_subjects (doc_id, subject_id, origin, doc_sha256, run_id)"
                " VALUES (%s, %s, 'ligador', %s, %s)"
                " ON CONFLICT (doc_id, subject_id) WHERE retired_at IS NULL DO NOTHING",
                (did, sid, sha[did], run_id))
            rel.ligacoes += cur.rowcount or 0
        cur.execute("UPDATE doc_sources SET linked_sha256 = %s, linked_at = NOW(),"
                    " link_run_id = %s WHERE id = %s", (sha[did], run_id, did))
        rel.lidos += 1
        rel.por_doc[did] = sids


# ── A rodada ──────────────────────────────────────────────────────────────

Pensador = Callable[[str, str], str]   # (prompt, system) -> saída crua


def _pensador_padrao(kobe_home: str) -> Pensador:
    from bot.lucien import brain

    def pensar(prompt: str, system: str) -> str:
        # O modelo PADRÃO do curador (o do incremental). Qual é sai do `.env` —
        # o Kobe não fixa modelo.
        return brain.chamar(prompt, kobe_home=kobe_home, reconstrucao=False,
                            system=system, timeout=600)
    return pensar


def rodar(cx, *, kobe_home: str, limite_lotes: Optional[int] = None,
          pensador: Optional[Pensador] = None, assentado_s: int = ASSENTADO_S,
          log: Callable[[str], None] = print) -> Relatorio:
    """Lê a fila em lotes de `LOTE`. Commita lote a lote; para na primeira falha
    (o lote que falhou continua na fila — nada dele foi gravado)."""
    from bot.lucien import brain, store

    pensar = pensador or _pensador_padrao(kobe_home)
    rel = Relatorio()
    rel.run_id = store.abrir_rodada(cx, mode="documentos", topic_id=None, lote=None,
                                    model="(padrão)")
    cx.commit()
    try:
        fila = pendentes(cx, assentado_s=assentado_s)
        lotes = [fila[i:i + LOTE] for i in range(0, len(fila), LOTE)]
        if limite_lotes:
            lotes = lotes[:limite_lotes]
        voc = vocabulario(cx)
        log(f"ligador {rel.run_id}: {len(fila)} documento(s) na fila, {len(lotes)} lote(s), "
            f"{len(voc)} assunto(s) na lista")
        if not voc:
            lotes = []
        for i, docs in enumerate(lotes, start=1):
            try:
                bruto = pensar(montar_prompt(voc, docs), SYSTEM)
                rel.chamadas += 1
                resposta = brain.extrair_json(bruto, reconhece=_e_resposta)
                if not isinstance(resposta, dict):
                    raise brain.CerebroIndisponivel("a resposta não é um objeto JSON")
                aplicar(cx, interpretar(resposta, voc, docs, rel), docs,
                        run_id=rel.run_id, rel=rel)
                cx.commit()
            except Exception as exc:  # noqa: BLE001 — para, diz onde; a fila continua
                cx.rollback()
                rel.erro = f"lote {i}/{len(lotes)}: {type(exc).__name__}: {exc}"
                break
            rel.lotes += 1
            log(f"  lote {i}/{len(lotes)}: {len(docs)} documento(s) · "
                f"{sum(1 for d in docs if rel.por_doc.get(int(d['id'])))} com ligação")
    except Exception as exc:  # noqa: BLE001 — a rodada fecha com o erro dito
        cx.rollback()
        rel.erro = rel.erro or f"{type(exc).__name__}: {exc}"
    finally:
        cx.cursor().execute(
            "UPDATE lucien_runs SET finished_at = NOW(), ok = %s, error = %s,"
            " docs_seen = %s, doc_links_created = %s, doc_links_rejected = %s WHERE id = %s",
            (rel.erro is None, rel.erro, rel.lidos, rel.ligacoes, rel.rejeitadas, rel.run_id))
        cx.commit()
    return rel


def desfazer(cx, run_id: str) -> dict:
    """Desfaz uma rodada inteira: tira as ligações que ela criou, desaposenta as
    que ela aposentou e devolve à fila os documentos que ela leu. Não commita."""
    cur = cx.cursor()
    cur.execute("SELECT mode FROM lucien_runs WHERE id = %s", (run_id,))
    r = cur.fetchone()
    if not r or r["mode"] != "documentos":
        raise ValueError(f"{run_id} não é uma rodada do ligador de documentos")
    cur.execute("DELETE FROM doc_subjects WHERE run_id = %s AND origin = 'ligador'", (run_id,))
    tiradas = cur.rowcount or 0
    cur.execute("UPDATE doc_subjects SET retired_at = NULL, retired_run_id = NULL"
                " WHERE retired_run_id = %s", (run_id,))
    devolvidas = cur.rowcount or 0
    cur.execute("UPDATE doc_sources SET linked_sha256 = NULL, linked_at = NULL,"
                " link_run_id = NULL WHERE link_run_id = %s", (run_id,))
    return {"tiradas": tiradas, "desaposentadas": devolvidas, "de_volta_na_fila": cur.rowcount or 0}


# ── A leitura (o dossiê) ──────────────────────────────────────────────────


def documentos_do_assunto(cx, subject_ids: list[str]) -> list[dict]:
    """As ligações vigentes da FAMÍLIA dos assuntos (fundidos nele e, num tema,
    as conversas de dentro), os documentos mais recentes primeiro. Um documento
    aparece uma vez, com a origem mais forte (autoria > operador > ligador)."""
    if not subject_ids or not tem_tabela(cx):
        return []
    from bot.lucien import conversa_assunto

    fam: set[str] = set()
    for sid in subject_ids:
        fam |= set(conversa_assunto.familia(cx, sid))
    cur = cx.cursor()
    cur.execute(
        "SELECT DISTINCT ON (s.id) s.id, s.root, s.rel_path, s.nature, s.topic_slug, s.title,"
        "       s.ext, s.indexable, s.status, s.file_mtime, d.origin, d.doc_sha256,"
        "       (d.doc_sha256 IS DISTINCT FROM s.sha256) AS versao_velha"
        "  FROM doc_subjects d JOIN doc_sources s ON s.id = d.doc_id"
        " WHERE d.subject_id = ANY(%s::uuid[]) AND d.retired_at IS NULL"
        " ORDER BY s.id, CASE d.origin WHEN 'autoria' THEN 0 WHEN 'operador' THEN 1 ELSE 2 END",
        (list(fam),))
    return sorted(cur.fetchall(), key=lambda r: (str(r["file_mtime"] or ""), r["id"]),
                  reverse=True)


def _main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover — CLI
    """O que a fonte do curador dispara, destacado: UM lote e sai."""
    import argparse
    import sys

    from bot.lucien import store, worker

    ap = argparse.ArgumentParser(prog="python -m bot.lucien.ligador_docs")
    ap.add_argument("--um-lote", action="store_true", help="lê um lote e sai (o default)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cx = store.conectar(worker._conninfo())
    try:
        if not devido(cx):
            cx.rollback()
            print("ligador: nada devido (chave, migration, cota ou fila)")
            return 0
        r = rodar(cx, kobe_home=worker._kobe_home(), limite_lotes=1,
                  log=lambda m: logger.info("%s", m))
        print(r.resumo())
        return 0 if r.erro is None else 1
    finally:
        cx.close()


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(_main())
