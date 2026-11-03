#!/usr/bin/env python3
"""F7.2 — o documento ligado ao assunto (o ligador do curador, migration 016).

O QUE ESTES TESTES GUARDAM
--------------------------
1. **A saída do modelo é validada pelo código** — documento fora do lote, assunto
   que não está na lista e excesso por documento são descartados e contados.
2. **Ler é registrar** — o documento lido que não pertence a nada sai da fila; o
   que mudou volta para ela.
3. **Aposentar, não apagar; desfazer por rodada** — a releitura aposenta a ligação
   que não se repete, e desfazer a rodada devolve tudo como estava.
4. **O ligador não toca no que não é dele** — ligação por AUTORIA (o resumo por
   assunto da F7.3) fica, e o documento dela nem entra na fila.
5. **O dossiê** — primeiro os ligados (com a família do tema), depois a busca sem
   repetir; chave desligada = só a busca, como antes.

As partes de banco são puladas sem `KOBE_TEST_DATABASE_URL` e rodam em transação
revertida (o `commit` do ligador é neutralizado). O modelo é de mentira.

    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test .venv/bin/python -m pytest -q tests/test_ligador_docs.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import ligador_docs as lg  # noqa: E402

MIGRATION = RAIZ / "infra" / "migrations" / "016_documento_e_assunto.sql"
_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


# ── Sem banco ────────────────────────────────────────────────────────────


def test_migration_016_e_aditiva():
    sql = "\n".join(l for l in MIGRATION.read_text().splitlines() if not l.lstrip().startswith("--"))
    assert not re.search(r"\bTRUNCATE\b|(?<!ON )\bDELETE\b", sql, re.I)
    # O único DROP é o alargamento da restrição do modo (mesmo padrão da 012/013).
    drops = re.findall(r"DROP\s+\w+(?:\s+IF\s+EXISTS)?\s+\w+", sql, re.I)
    assert drops == ["DROP CONSTRAINT IF EXISTS lucien_runs_mode_check"]
    assert "'documentos'" in sql and "'marcacao'" in sql and "'incremental'" in sql
    assert re.search(r"origin IN \('ligador', 'operador', 'autoria'\)", sql)


def _voc():
    return {"S1": {"id": "11111111-0000-0000-0000-000000000001", "title": "Tema A",
                   "description": "d", "kind": "tema", "pai": None},
            "S2": {"id": "11111111-0000-0000-0000-000000000002", "title": "Conversa B",
                   "description": None, "kind": "conversa", "pai": "Tema A"}}


def test_interpretar_descarta_e_conta_o_invalido():
    docs = [{"id": 10, "sha256": "a"}, {"id": 11, "sha256": "b"}]
    rel = lg.Relatorio()
    resp = {"ligacoes": {"D10": ["S1", "S1", "S9"], "D99": ["S1"], "Dx": ["S1"],
                         "D11": ["S2"]}}
    out = lg.interpretar(resp, _voc(), docs, rel)
    assert out == {10: [_voc()["S1"]["id"]], 11: [_voc()["S2"]["id"]]}
    assert rel.rejeitadas == 3          # S9 inexistente, D99 fora do lote, Dx ilegível


def test_interpretar_tem_teto_por_documento():
    voc = {f"S{i}": {"id": str(uuid.UUID(int=i)), "title": f"t{i}"} for i in range(1, 8)}
    rel = lg.Relatorio()
    out = lg.interpretar({"ligacoes": {"D1": [f"S{i}" for i in range(1, 8)]}}, voc,
                         [{"id": 1}], rel)
    assert len(out[1]) == lg.MAX_POR_DOC and rel.rejeitadas == 7 - lg.MAX_POR_DOC


def test_todo_documento_do_lote_sai_lido_mesmo_sem_ligacao():
    rel = lg.Relatorio()
    out = lg.interpretar({"ligacoes": {}}, _voc(), [{"id": 1}, {"id": 2}], rel)
    assert out == {1: [], 2: []}
    assert lg.interpretar({"outra": 1}, _voc(), [{"id": 1}], rel) == {1: []}


def test_prompt_lista_primeiro_e_documento_no_formato_medido():
    doc = {"id": 7, "rel_path": "topics/x/plano.md",
           "content": "# Plano\n\n## Fase 1\n\ntexto " * 3}
    p = lg.montar_prompt(_voc(), [doc])
    assert p.index("ASSUNTOS") < p.index("DOCUMENTOS") < p.index("[D7]")
    assert "S2  Conversa B (conversa em \"Tema A\")" in p
    assert "títulos: Plano | Fase 1" in p and "início: # Plano" in p


def test_chave_desligada(monkeypatch):
    monkeypatch.delenv("DOC_SUBJECTS_ENABLED", raising=False)
    assert lg.habilitado() is False and lg.ativo(object()) is False
    from bot.lucien import dossie_assunto

    assert dossie_assunto.documentos_ligados(object(), ["x"]) == ([], set())


# ── Banco ────────────────────────────────────────────────────────────────


class _SemCommit:
    """A conexão do curador com o `commit` trocado por um ponto de salvamento:
    tudo some no fim do teste."""

    def __init__(self, cx):
        self.cx, self.info = cx, cx.info
        cx.execute("SAVEPOINT teste")

    def cursor(self):
        return self.cx.cursor()

    def commit(self):
        # O "commit" do teste só move o ponto de salvamento: o rollback do
        # ligador desfaz o lote, como no banco de verdade; o fim do teste
        # desfaz tudo.
        self.cx.execute("RELEASE SAVEPOINT teste")
        self.cx.execute("SAVEPOINT teste")

    def rollback(self):
        self.cx.execute("ROLLBACK TO SAVEPOINT teste")


@pytest.fixture
def cx(monkeypatch):
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    import psycopg
    from psycopg.rows import dict_row

    con = psycopg.connect(_URL, row_factory=dict_row)
    monkeypatch.setenv("DOC_SUBJECTS_ENABLED", "true")
    try:
        if not con.execute("SELECT to_regclass('doc_subjects') IS NOT NULL AS ok").fetchone()["ok"]:
            pytest.skip("migration 016 não aplicada no banco de teste")
        yield _SemCommit(con)
    finally:
        con.rollback()
        con.close()


def _assunto(cx, titulo, *, kind="tema", pai=None):
    cur = cx.cursor()
    cur.execute(
        "INSERT INTO lucien_subjects (title, slug, kind, parent_id, opened_at, last_touched_at)"
        " VALUES (%s, %s, %s, %s, NOW(), NOW()) RETURNING id",
        (titulo, "teste-ligador-" + uuid.uuid4().hex[:10], kind, pai))
    return str(cur.fetchone()["id"])


def _doc(cx, rel, texto):
    cur = cx.cursor()
    cur.execute(
        "INSERT INTO doc_sources (root, rel_path, nature, title, ext, size_bytes, sha256,"
        " indexable, content) VALUES ('casa', %s, 'anotacao_agente', %s, '.md', %s, %s, TRUE, %s)"
        " RETURNING id", (rel, rel, len(texto), hashlib.sha256(texto.encode()).hexdigest(), texto))
    return int(cur.fetchone()["id"])


def _mudar(cx, did, texto):
    cx.cursor().execute("UPDATE doc_sources SET content = %s, sha256 = %s, updated_at = NOW()"
                        " WHERE id = %s", (texto, hashlib.sha256(texto.encode()).hexdigest(), did))


def _pensador(regras):
    """O modelo de mentira: liga o documento cujo texto tem a MARCA ao assunto de
    título dado. Lê só o prompt — como o de verdade."""
    def pensar(prompt, system):
        ap = dict(re.findall(r"^(S\d+)  (.+?)(?: \(conversa em .*?\))?(?: — .*)?$", prompt, re.M))
        por_titulo = {v: k for k, v in ap.items()}
        lig = {}
        for bloco in prompt.split("\n\n"):
            m = re.match(r"\[D(\d+)\]", bloco.strip())
            if not m:
                continue
            for marca, titulo in regras.items():
                if marca in bloco and titulo in por_titulo:
                    lig.setdefault(f"D{m.group(1)}", []).append(por_titulo[titulo])
        return json.dumps({"type": "result", "result": json.dumps({"ligacoes": lig})})
    return pensar


def _ligacoes(cx, did):
    cur = cx.cursor()
    cur.execute("SELECT subject_id, origin, retired_at IS NULL AS vigente FROM doc_subjects"
                " WHERE doc_id = %s ORDER BY id", (did,))
    return [(str(r["subject_id"]), r["origin"], r["vigente"]) for r in cur.fetchall()]


def _na_fila(cx, did):
    return did in {int(r["id"]) for r in lg.pendentes(cx, assentado_s=0)}


def test_ligar_registrar_religar_e_desfazer(cx):
    a = _assunto(cx, "Tema do ligador alfa zq1")
    b = _assunto(cx, "Tema do ligador beta zq1")
    d1 = _doc(cx, "knowledge/zq1-um.md", "# Um\nMARCA-ALFA-ZQ1 texto")
    d2 = _doc(cx, "knowledge/zq1-dois.md", "# Dois\nnada a ver")
    r1 = lg.rodar(cx, kobe_home="/nao-usado", assentado_s=0, log=lambda m: None,
                  pensador=_pensador({"MARCA-ALFA-ZQ1": "Tema do ligador alfa zq1",
                                      "MARCA-BETA-ZQ1": "Tema do ligador beta zq1"}))
    assert r1.erro is None and r1.lidos >= 2
    assert _ligacoes(cx, d1) == [(a, "ligador", True)]
    assert _ligacoes(cx, d2) == []
    assert not _na_fila(cx, d1) and not _na_fila(cx, d2)      # lido sem ligação também sai

    _mudar(cx, d1, "# Um\nagora MARCA-BETA-ZQ1")
    assert _na_fila(cx, d1) and not _na_fila(cx, d2)
    r2 = lg.rodar(cx, kobe_home="/nao-usado", assentado_s=0, log=lambda m: None,
                  pensador=_pensador({"MARCA-BETA-ZQ1": "Tema do ligador beta zq1"}))
    assert r2.erro is None and r2.aposentadas == 1
    assert _ligacoes(cx, d1) == [(a, "ligador", False), (b, "ligador", True)]

    d = lg.desfazer(cx, r2.run_id)
    assert d["tiradas"] == 1 and d["desaposentadas"] == 1
    assert _ligacoes(cx, d1) == [(a, "ligador", True)]
    assert _na_fila(cx, d1)

    cur = cx.cursor()
    cur.execute("SELECT mode, ok, docs_seen, doc_links_created FROM lucien_runs WHERE id = %s",
                (r1.run_id,))
    run = cur.fetchone()
    assert run["mode"] == "documentos" and run["ok"] and run["doc_links_created"] >= 1


def test_falha_do_modelo_nao_grava_e_a_fila_fica(cx):
    _assunto(cx, "Tema do ligador gama zq2")
    d1 = _doc(cx, "knowledge/zq2.md", "# Z\ntexto")

    def quebra(prompt, system):
        raise RuntimeError("modelo fora")

    r = lg.rodar(cx, kobe_home="/nao-usado", assentado_s=0, pensador=quebra, log=lambda m: None)
    assert r.erro and "modelo fora" in r.erro and r.lidos == 0
    assert _na_fila(cx, d1) and _ligacoes(cx, d1) == []
    cur = cx.cursor()
    cur.execute("SELECT ok, error FROM lucien_runs WHERE id = %s", (r.run_id,))
    assert cur.fetchone()["ok"] is False


def test_autoria_e_intocavel_e_fora_da_fila(cx):
    a = _assunto(cx, "Tema do ligador delta zq3")
    b = _assunto(cx, "Tema do ligador epsilon zq3")
    resumo = _doc(cx, "lucien/resumos/zq3.md", "# Resumo\nMARCA-EPS-ZQ3")
    outro = _doc(cx, "knowledge/zq3-outro.md", "# Outro\nMARCA-EPS-ZQ3")
    cx.cursor().execute("INSERT INTO doc_subjects (doc_id, subject_id, origin) VALUES"
                        " (%s, %s, 'autoria'), (%s, %s, 'autoria')", (resumo, a, outro, a))
    assert not _na_fila(cx, resumo)
    # Um documento com autoria que ainda assim é lido (via `aplicar` direto) não
    # perde a ligação de autoria: o ligador só aposenta o que é dele.
    rel = lg.Relatorio()
    lg.aplicar(cx, {outro: [b]}, [{"id": outro, "sha256": "x"}],
               run_id=_run(cx), rel=rel)
    assert sorted(_ligacoes(cx, outro)) == sorted([(a, "autoria", True), (b, "ligador", True)])


def _run(cx):
    from bot.lucien import store

    return store.abrir_rodada(cx, mode="documentos", topic_id=None, lote=None, model="teste")


def test_dossie_traz_os_ligados_da_familia_e_a_busca_sem_repetir(cx, monkeypatch):
    from bot.docs import query as dq
    from bot.lucien import dossie_assunto

    tema = _assunto(cx, "Tema do ligador zeta zq4")
    conv = _assunto(cx, "Conversa do ligador zeta zq4", kind="conversa", pai=tema)
    d_tema = _doc(cx, "knowledge/zq4-tema.md", "# Do tema\nx")
    d_conv = _doc(cx, "knowledge/zq4-conversa.md", "# Da conversa\nx")
    d_busca = _doc(cx, "knowledge/zq4-busca.md", "# Só da busca\nx")
    rid = _run(cx)
    lg.aplicar(cx, {d_tema: [tema], d_conv: [conv]},
               [{"id": d_tema, "sha256": "s"}, {"id": d_conv, "sha256": "s"}],
               run_id=rid, rel=lg.Relatorio())

    linhas, ids = dossie_assunto.documentos_ligados(cx, [tema])
    texto = "\n".join(linhas)
    assert ids == {d_tema, d_conv}
    assert "### Documentos deste assunto (2)" in texto
    assert "📝 anotação do agente" in texto and "zq4-conversa.md" in texto
    assert "o arquivo mudou depois da ligação" in texto     # sha "s" ≠ o do documento

    # Só a conversa: não traz o documento do tema.
    _, ids_conv = dossie_assunto.documentos_ligados(cx, [conv])
    assert ids_conv == {d_conv}

    def falsa(db, frase, *, limite, kobe_home=None):
        achados = [dq.DocAchado(doc_id=i, root="casa", rel_path=f"knowledge/{i}.md",
                                nature="anotacao_agente", topic_slug=None, title=str(i),
                                ext=".md", indexable=True, status="presente", file_mtime=None)
                   for i in (d_conv, d_busca)]
        return dq.ResultadoDocs(veredito="ACHOU", achados=achados[:limite])

    busca = dossie_assunto.documentos_da_busca(cx, "zeta", buscar=falsa, excluir=ids, limite=3,
                                               titulo="Documentos também achados pela busca")
    texto = "\n".join(busca)
    assert "### Documentos também achados pela busca" in texto
    assert f" {d_busca}.md" in texto and f" {d_conv}.md" not in texto


def test_fusao_e_seguida_na_leitura(cx):
    velho = _assunto(cx, "Tema do ligador eta zq5 velho")
    novo = _assunto(cx, "Tema do ligador eta zq5 novo")
    d = _doc(cx, "knowledge/zq5.md", "# Eta\nx")
    lg.aplicar(cx, {d: [velho]}, [{"id": d, "sha256": "s"}], run_id=_run(cx), rel=lg.Relatorio())
    cx.cursor().execute("UPDATE lucien_subjects SET status = 'fundido', merged_into = %s"
                        " WHERE id = %s", (novo, velho))
    assert [int(r["id"]) for r in lg.documentos_do_assunto(cx, [novo])] == [d]
