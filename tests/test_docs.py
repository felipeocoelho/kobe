#!/usr/bin/env python3
"""F7.2 — documentos alcançáveis pela busca.

O QUE ESTES TESTES GUARDAM
--------------------------
1. **A lista do que entra** — com as armadilhas medidas no disco real (`.env`,
   `.git`, `.local`, código, estado JSON, link simbólico): nenhuma entra. E a regra é
   GERAL: tópico novo e pasta nova entram sem configuração (ajuste de 03/11).
2. **O arquivo nunca é tocado** — hash da árvore inteira antes e depois.
3. **Documento não vira verdade** — artefato de sala sem desfecho registrado sai
   DESCONHECIDO; `recusado`/`superado` só com a fala do operador.
4. **Falha não vira silêncio** — embedding fora é falha nomeada.

As partes de banco são puladas sem `KOBE_TEST_DATABASE_URL` e rodam em transação
revertida. O embedder é de mentira (bolsa de palavras com hash): o que se guarda é
o contrato, não o serviço da OpenAI.

    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test .venv/bin/python -m pytest -q tests/test_docs.py
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.docs import indexer, producoes, raizes  # noqa: E402
from bot.docs import query as dq  # noqa: E402
from bot.search import embedder  # noqa: E402

MIGRATION = RAIZ / "infra" / "migrations" / "015_documentos.sql"
_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


# ── A árvore de mentira, com as armadilhas reais ─────────────────────────


def _escrever(p: Path, texto: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")


@pytest.fixture
def casa(tmp_path):
    home = tmp_path / "kobe"
    u = home / "user-data"
    _escrever(u / "topics/olimpo/uploads/form-debriefing.md",
              "# Formulário de debriefing\n\nRespostas da mentorada sobre lançamento de perpétuo.")
    _escrever(u / "topics/olimpo/uploads/foto.jpg", "binário")
    _escrever(u / "topics/olimpo/uploads/contrato.pdf", "binário")
    _escrever(u / "topics/olimpo/uploads/pacote.zip", "zip")
    _escrever(u / "topics/olimpo/debriefing-loris/notas.md", "# Notas do Loris\n\nCPL dois fraco.")
    _escrever(u / "topics/olimpo/debriefing-loris/.env", "OPENAI_API_KEY=segredo")
    _escrever(u / "topics/olimpo/debriefing-loris/app/.env", "SEGREDO=1")
    _escrever(u / "topics/olimpo/debriefing-loris/app/.git/config", "[core]")
    _escrever(u / "topics/olimpo/debriefing-loris/app/main.py", "print('x')")
    _escrever(u / "topics/olimpo/debriefing-loris/pagina.html", "<html></html>")
    _escrever(u / "topics/olimpo/.local/venv/lib/site.md", "# não entra")
    _escrever(u / "topics/olimpo/prompt.md", "# Regras do Olimpo")
    _escrever(u / "topics/dev-kobe/knowledge/00-indice-curto.md", "# Índice curto")
    _escrever(u / "topics/nunca-visto/pasta-nova/sub/doc.txt", "documento de um tópico novo")
    _escrever(u / "knowledge/kobe/decisoes/2026-08-aposentar-chat-manager.md",
              "# Aposentar o Chat Manager\n\n## Decisão\n\nO Chat Manager foi aposentado em agosto.")
    _escrever(u / "missoes/2026-09-22-aula/sala-brief.md", "# Brief da aula")
    _escrever(u / "missoes/2026-09-22-aula/sala.json", "{}")
    _escrever(u / "missoes/2026-09-22-aula/sala.sysprompt.txt", "prompt de sistema")
    _escrever(u / "missoes/2026-09-22-aula/workspace/notas-da-missao.md", "# Notas")
    _escrever(u / "coder-worktrees/abcd1234/.local/plano-faxina-r15.md",
              "# Plano — faxina R15\n\n## 1. Visão\n\nLimpar o repositório e os rascunhos velhos.")
    _escrever(u / "coder-worktrees/abcd1234/.local/dump.md", "não é plano")
    _escrever(u / "coder-worktrees/abcd1234/README.md", "cópia do repo, não entra")
    fora = tmp_path / "fora.md"
    _escrever(fora, "segredo fora da casa")
    (u / "topics/olimpo/uploads/link.md").symlink_to(fora)
    return home


def _rel(arqs):
    return {f"{a.root}:{a.rel_path}": a for a in arqs}


def test_lista_do_que_entra_respeita_as_armadilhas(casa):
    a = _rel(raizes.descobrir(casa, extras={}))
    for proibido in (".env", ".git", ".local/venv", "main.py", "sala.json",
                     "sala.sysprompt.txt", "link.md", "pacote.zip", "README.md", "dump.md"):
        assert not any(proibido in k for k in a), proibido
    assert a["casa:topics/olimpo/uploads/form-debriefing.md"].nature == "material_operador"
    assert a["casa:topics/olimpo/uploads/form-debriefing.md"].topic_slug == "olimpo"
    assert a["casa:topics/olimpo/prompt.md"].nature == "material_operador"
    assert a["casa:topics/dev-kobe/knowledge/00-indice-curto.md"].nature == "base_topico"
    assert a["casa:knowledge/kobe/decisoes/2026-08-aposentar-chat-manager.md"].nature == \
        "anotacao_agente"
    plano = a["casa:coder-worktrees/abcd1234/.local/plano-faxina-r15.md"]
    assert (plano.nature, plano.sala_id, plano.sala_kind) == ("artefato_sala", "abcd1234", "coder")
    missao = a["casa:missoes/2026-09-22-aula/workspace/notas-da-missao.md"]
    assert (missao.sala_kind, missao.sala_id) == ("missao", "2026-09-22-aula")


def test_regra_geral_topico_e_pasta_novos_entram_sem_config(casa):
    a = _rel(raizes.descobrir(casa, extras={}))
    novo = a["casa:topics/nunca-visto/pasta-nova/sub/doc.txt"]
    assert novo.topic_slug == "nunca-visto" and novo.indexable


def test_binario_e_html_sao_so_listados(casa):
    a = _rel(raizes.descobrir(casa, extras={}))
    assert a["casa:topics/olimpo/uploads/contrato.pdf"].indexable is False
    assert a["casa:topics/olimpo/uploads/foto.jpg"].indexable is False
    assert a["casa:topics/olimpo/debriefing-loris/pagina.html"].indexable is False


def test_raizes_extras_da_instalacao(tmp_path):
    wt = tmp_path / "wt"
    _escrever(wt / "s1/.local/plano-x.md", "# X")
    br = tmp_path / "salas"
    _escrever(br / "brief.md", "# Brief")
    _escrever(br / "sub/nao.md", "# subpasta não entra")
    extras = raizes.raizes_extras(f"worktrees:{wt},briefs:{br},lixo:/x")
    assert set(extras) == {"worktrees", "briefs"}
    a = _rel(raizes.descobrir(tmp_path / "semcasa", extras=extras))
    assert a["worktrees:s1/.local/plano-x.md"].sala_id == "s1"
    assert a["briefs:brief.md"].nature == "anotacao_agente"
    assert not any("nao.md" in k for k in a)


def test_ancora_segue_os_titulos():
    texto = "# Plano\n\n## 1. Visão\n\n" + "a " * 600 + "\n\n## 2. Riscos\n\n" + "b " * 600
    t = indexer.trechos_com_ancora(texto)
    assert t[0][0].startswith("Plano")
    assert any("2. Riscos" in anc for anc, _ in t)


def test_rotulo_desconhecido_nunca_sai_como_aprovado():
    a = dq.DocAchado(doc_id=1, root="casa", rel_path="coder-worktrees/x/.local/plano-y.md",
                     nature="artefato_sala", topic_slug=None, title="Y", ext=".md",
                     indexable=True, status="presente", file_mtime=None, sala_id="x",
                     sala_kind="coder", outcome=None)
    assert "DESCONHECIDO" in dq.rotulo(a) and "APROVADO" not in dq.rotulo(a)
    for nat, marca in (("anotacao_agente", "segunda mão"), ("base_topico", "base do tópico"),
                       ("material_operador", "documento seu")):
        a.nature = nat
        assert marca in dq.rotulo(a)


def test_migration_e_aditiva():
    sql = "\n".join(l for l in MIGRATION.read_text().splitlines() if not l.lstrip().startswith("--"))
    assert not re.search(r"\b(DROP|TRUNCATE)\b|(?<!ON )\bDELETE\b", sql, re.I)
    assert not re.search(r"ALTER\s+TABLE", sql, re.I)
    assert "VECTOR(1536)" in sql and embedder.DIM == 1536


def test_chave_desligada_nao_faz_nada(monkeypatch, casa):
    monkeypatch.delenv("DOC_INDEX_ENABLED", raising=False)
    assert indexer.tick(object(), casa).fez_algo is False
    assert dq.buscar(object(), "x").veredito == "FORA"


# ── Banco ────────────────────────────────────────────────────────────────


class _Ponte:
    def __init__(self, cx):
        self.cx = cx

    def query(self, sql, params=()):
        with self.cx.cursor() as cur:
            cur.execute(sql, tuple(params) if params else None)
            return cur.fetchall() if cur.description else []

    execute = query

    def scalar(self, sql, params=()):
        r = self.query(sql, params)
        return next(iter(r[0].values())) if r else None

    def one(self, sql, params=()):
        r = self.query(sql, params)
        return r[0] if r else None


def _vetor(texto: str) -> list[float]:
    v = [0.0] * embedder.DIM
    for w in re.findall(r"\w{3,}", texto.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % embedder.DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture
def db(monkeypatch):
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    import psycopg
    from psycopg.rows import dict_row

    cx = psycopg.connect(_URL, row_factory=dict_row)
    monkeypatch.setenv("DOC_INDEX_ENABLED", "true")
    monkeypatch.setattr(embedder, "embed", lambda textos, **k: [_vetor(t) for t in textos])
    monkeypatch.setattr(embedder, "embed_um", lambda t, **k: _vetor(t))
    try:
        cur = cx.cursor()
        cur.execute("SELECT to_regclass('doc_sources') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            pytest.skip("migration 015 não aplicada no banco de teste")
        yield _Ponte(cx)
    finally:
        cx.rollback()
        cx.close()


def _hash_arvore(raiz: Path) -> dict[str, str]:
    saida = {}
    for p in sorted(raiz.rglob("*")):
        if p.is_file() and not p.is_symlink():
            saida[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest() + str(p.stat().st_mtime_ns)
    return saida


def test_carga_nao_toca_em_arquivo_nenhum(db, casa):
    antes = _hash_arvore(casa)
    r = indexer.tick(db, casa, extras={})
    assert r.erro is None and r.novos > 0 and r.trechos_embeddados > 0
    assert _hash_arvore(casa) == antes
    segredos = db.query(
        "SELECT COUNT(*) AS n FROM doc_chunks WHERE body LIKE '%OPENAI_API_KEY=segredo%'"
        " OR body LIKE '%SEGREDO=1%' OR body LIKE '%segredo fora da casa%'")
    assert segredos[0]["n"] == 0
    caminhos = db.query("SELECT COUNT(*) AS n FROM doc_sources WHERE rel_path LIKE '%.env%'"
                        " OR rel_path LIKE '%.git/%' OR rel_path LIKE '%link.md'")
    assert caminhos[0]["n"] == 0


def test_mudanca_refaz_trechos_e_sumico_preserva_texto(db, casa):
    indexer.tick(db, casa, extras={})
    alvo = casa / "user-data/knowledge/kobe/decisoes/2026-08-aposentar-chat-manager.md"
    alvo.write_text("# Aposentar o Chat Manager\n\nTexto novo sobre o detector.", encoding="utf-8")
    os.utime(alvo, (1, 1))
    r = indexer.tick(db, casa, extras={})
    assert r.mudados == 1
    corpo = db.query("SELECT c.body FROM doc_chunks c JOIN doc_sources s ON s.id=c.doc_id"
                     " WHERE s.rel_path LIKE %s", ("%aposentar%",))
    assert any("detector" in l["body"] for l in corpo)
    alvo.unlink()
    r = indexer.tick(db, casa, extras={})
    assert r.ausentes == 1
    linha = db.query("SELECT status, content FROM doc_sources WHERE rel_path LIKE %s",
                     ("%aposentar%",))[0]
    assert linha["status"] == "ausente_do_disco" and "detector" in linha["content"]


def test_raiz_nao_configurada_nao_faz_ninguem_sumir(db, casa, tmp_path):
    wt = tmp_path / "wt"
    _escrever(wt / "s9/.local/plano-z.md", "# Z")
    indexer.tick(db, casa, extras={"worktrees": wt})
    indexer.tick(db, casa, extras={})          # a raiz saiu da configuração
    st = db.query("SELECT status FROM doc_sources WHERE root='worktrees'")
    assert st and st[0]["status"] == "presente"


def test_desfecho_aprovado_pelo_estado_do_coder(db, casa):
    estado = casa / "user-data" / "coder-sessions" / "475" / "x.json"
    _escrever(estado, '{"short_id": "abcd1234", "plan_approved": true}')
    indexer.tick(db, casa, extras={})
    p = db.query("SELECT outcome, outcome_source FROM doc_producoes WHERE sala_id='abcd1234'")
    assert p[0]["outcome"] == "aprovado" and p[0]["outcome_source"] == "estado_coder"
    m = db.query("SELECT outcome FROM doc_producoes WHERE sala_kind='missao'")
    assert m and all(x["outcome"] == "desconhecido" for x in m)


def test_recusado_so_com_a_fala_do_operador(db, casa):
    import psycopg

    indexer.tick(db, casa, extras={})
    doc = db.query("SELECT doc_id FROM doc_producoes WHERE sala_id='abcd1234'")[0]["doc_id"]
    with pytest.raises(producoes.DesfechoRecusado):
        producoes.registrar_desfecho(db, doc, "recusado", 999999999)
    with pytest.raises(producoes.DesfechoRecusado):
        producoes.registrar_desfecho(db, doc, "talvez", 1)
    db.query("SAVEPOINT s")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.query("UPDATE doc_producoes SET outcome='recusado', outcome_source='estado_coder'"
                 " WHERE doc_id=%s", (doc,))
    db.query("ROLLBACK TO SAVEPOINT s")


def test_busca_acha_o_plano_com_rotulo_e_ignora_o_inexistente(db, casa, monkeypatch):
    monkeypatch.setenv("DOC_SEARCH_PISO_COS", "0.30")
    indexer.tick(db, casa, extras={})
    r = dq.buscar(db, "faxina do repositório e rascunhos velhos", kobe_home=casa)
    assert r.veredito == "ACHOU"
    topo = r.achados[0]
    assert "plano-faxina-r15.md" in topo.rel_path
    assert "DESCONHECIDO" in dq.rotulo(topo)
    assert topo.caminho and topo.caminho.endswith("plano-faxina-r15.md")
    nada = dq.buscar(db, "o campeonato de Wqrtyzzbol no clube", kobe_home=casa)
    assert nada.veredito == "SEM_REGISTRO"


def test_binario_e_achavel_pelo_nome(db, casa):
    indexer.tick(db, casa, extras={})
    r = dq.buscar(db, "o contrato.pdf da mentorada", kobe_home=casa)
    nomes = [a.rel_path for a in r.achados]
    assert any(n.endswith("contrato.pdf") for n in nomes)
    pdf = next(a for a in r.achados if a.rel_path.endswith("contrato.pdf"))
    assert "conteúdo NÃO lido" in dq.rotulo(pdf)


def test_filtro_por_topico(db, casa, monkeypatch):
    monkeypatch.setenv("DOC_SEARCH_PISO_COS", "0.30")
    indexer.tick(db, casa, extras={})
    r = dq.buscar(db, "respostas da mentorada sobre lançamento de perpétuo",
                  topic_slug="dev-kobe", kobe_home=casa)
    assert all(a.topic_slug == "dev-kobe" for a in r.achados)


def test_embedding_fora_e_falha_nomeada(db, casa, monkeypatch):
    def _fora(*a, **k):
        raise embedder.EmbeddingIndisponivel("sem chave")

    monkeypatch.setattr(embedder, "embed", _fora)
    monkeypatch.setattr(embedder, "embed_um", _fora)
    r = indexer.tick(db, casa, extras={})
    assert r.erro and "sem chave" in r.erro
    b = dq.buscar(db, "faxina", kobe_home=casa)
    assert b.sentido_ativo is False and b.motivo_sentido_fora == "sem chave"


def test_plano_anexado_entra_na_hora(db, casa):
    p = casa / "user-data/coder-worktrees/abcd1234/.local/plano-faxina-r15.md"
    assert producoes.registrar_plano_anexado(db, p, casa) is True
    linha = db.query("SELECT s.content, p.outcome FROM doc_sources s"
                     " JOIN doc_producoes p ON p.doc_id = s.id"
                     " WHERE p.sala_id = 'abcd1234' AND s.rel_path LIKE %s",
                     ("%plano-faxina%",))[0]
    assert "faxina R15" in linha["content"] and linha["outcome"] == "desconhecido"
    assert producoes.registrar_plano_anexado(db, casa / "user-data/knowledge/kobe/x.md", casa) \
        is False
