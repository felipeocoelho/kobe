#!/usr/bin/env python3
"""O porteiro da base de conhecimento — `bot/kb_catalog.py` + o carregador.

O QUE ESTE ARQUIVO GUARDA
--------------------------
O porteiro existe por um caso concreto: entre 17/06 e 04/09/2026, o formulário de
debriefing de uma mentorada — com nome, e-mail e telefone — entrou no prompt de
**todo turno** do tópico Olimpo. Ninguém decidiu isso. O arquivo caiu na pasta
pelo caminho de upload antigo, e a pasta não tinha porteiro.

Os testes de comportamento (sem banco) cobrem o carregador; os de integração
cobrem o catálogo contra o banco de desenvolvimento.

A distinção que mais importa aqui, e que é fácil de perder numa refatoração:
**"nunca decidi" e "decidi que não" são estados diferentes.** Arquivo removido
não pode voltar a bater na porta, senão uma decisão do operador vira pergunta
recorrente.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_kb_catalog.py -q
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_kb_catalog.py

Os testes de integração rodam em transação **revertida** no teardown.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.kb_catalog import Catalogo  # noqa: E402
from bot.topic_manager import load_topic_context  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


# ── o carregador, sem banco ───────────────────────────────────────────────

@pytest.fixture
def base(tmp_path: Path) -> Path:
    k = tmp_path / "user-data" / "topics" / "t" / "knowledge"
    k.mkdir(parents=True)
    (tmp_path / "user-data" / "topics" / "t" / "prompt.md").write_text(
        "instruções do tópico", encoding="utf-8")
    for nome, texto in [("a.md", "conteudo A"), ("b.md", "conteudo B"),
                        ("z.md", "conteudo Z")]:
        (k / nome).write_text(texto, encoding="utf-8")
    return tmp_path


def _ctx(raiz: Path, **kw) -> str:
    return (load_topic_context(raiz, "t", **kw) or "").split("\x00")[0]


def test_sem_catalogo_o_comportamento_e_o_de_antes(base):
    """Compatibilidade: quem chama fora do turno (CLI, medição, testes antigos)
    continua vendo a pasta inteira."""
    ctx = _ctx(base)
    for nome in ("a.md", "b.md", "z.md"):
        assert nome in ctx


def test_com_catalogo_so_entra_o_admitido(base):
    """O invariante central. Se isto quebrar, a pasta volta a ser um balaio."""
    cat = Catalogo(admitidos=["a.md"], conhecidos={"a.md", "b.md", "z.md"})
    ctx = _ctx(base, catalogo=cat)
    assert "conteudo A" in ctx
    assert "conteudo B" not in ctx
    assert "conteudo Z" not in ctx


def test_a_ordem_do_catalogo_substitui_a_alfabetica(base):
    """Hoje quem decide quem entra quando não cabe tudo é a ordem alfabética do
    NOME DO ARQUIVO — acidente de implementação. Com catálogo, quem decide é o
    operador."""
    cat = Catalogo(admitidos=["z.md", "a.md"], conhecidos={"a.md", "b.md", "z.md"})
    ctx = _ctx(base, catalogo=cat)
    assert ctx.index("conteudo Z") < ctx.index("conteudo A")


def test_arquivo_nao_decidido_nao_entra_MAS_aparece_batendo_na_porta(base):
    """As duas metades. Não entrar é o portão; aparecer é o que impede o portão
    de virar um sumiço silencioso — que é a mesma doença com outro nome."""
    cat = Catalogo(admitidos=["a.md"], conhecidos={"a.md"})
    ctx = _ctx(base, catalogo=cat)
    assert "conteudo B" not in ctx, "não decidido não pode entrar"
    assert "BATENDO NA PORTA" in ctx
    assert "b.md" in ctx and "z.md" in ctx


def test_arquivo_REMOVIDO_nao_bate_na_porta(base):
    """A distinção que faz o mecanismo não virar chateação: quem foi removido já
    foi decidido. Voltar a pedir transformaria a decisão do operador em pergunta
    recorrente — e pergunta recorrente é como aviso vira ruído."""
    cat = Catalogo(admitidos=["a.md"], conhecidos={"a.md", "b.md", "z.md"})
    ctx = _ctx(base, catalogo=cat)
    assert "BATENDO NA PORTA" not in ctx


def test_o_prompt_do_topico_nunca_passa_pelo_porteiro(base):
    """As instruções do tópico não são knowledge base e não podem depender do
    catálogo. Mesmo com catálogo vazio, o tópico continua sabendo o que é."""
    cat = Catalogo(admitidos=[], conhecidos={"a.md", "b.md", "z.md"})
    ctx = _ctx(base, catalogo=cat)
    assert "instruções do tópico" in ctx


def test_catalogo_vazio_e_pasta_vazia_nao_inventam_bloco(tmp_path):
    d = tmp_path / "user-data" / "topics" / "t"
    (d / "knowledge").mkdir(parents=True)
    (d / "prompt.md").write_text("só instruções", encoding="utf-8")
    ctx = _ctx(tmp_path, catalogo=Catalogo())
    assert "BATENDO NA PORTA" not in ctx


def test_admitido_que_sumiu_do_disco_nao_quebra(base):
    """Catálogo e disco podem divergir — alguém apaga o arquivo à mão. O
    carregador ignora a entrada morta em vez de estourar no meio do turno."""
    cat = Catalogo(admitidos=["a.md", "fantasma.md"], conhecidos={"a.md"})
    ctx = _ctx(base, catalogo=cat)
    assert "conteudo A" in ctx


# ── o catálogo, contra o banco ────────────────────────────────────────────

@pytest.fixture
def db():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    sys.path.insert(0, str(RAIZ))
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(_URL, row_factory=dict_row)

    class _DB:
        def query(self, sql, params=None):
            cur = conn.cursor(); cur.execute(sql, params); return list(cur.fetchall())

        def one(self, sql, params=None):
            cur = conn.cursor(); cur.execute(sql, params); return cur.fetchone()

        def execute(self, sql, params=None):
            conn.cursor().execute(sql, params)

    try:
        yield _DB()
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def topico(db) -> str:
    linha = db.one("SELECT id FROM topics LIMIT 1")
    if linha is None:
        pytest.skip("banco sem tópicos")
    return str(linha["id"])


def test_admitir_e_carregar(db, topico):
    from bot import kb_catalog
    nome = f"teste-{uuid.uuid4().hex[:8]}.md"
    assert kb_catalog.admitir(db, topico, nome, titulo="Teste") == "admitido"
    cat = kb_catalog.carregar(db, topico)
    assert nome in cat.admitidos and nome in cat.conhecidos


def test_remover_tira_do_admitido_mas_mantem_conhecido(db, topico):
    """É o que impede o arquivo removido de voltar a bater na porta."""
    from bot import kb_catalog
    nome = f"teste-{uuid.uuid4().hex[:8]}.md"
    kb_catalog.admitir(db, topico, nome, titulo="Teste")
    assert kb_catalog.remover(db, topico, nome) is True
    cat = kb_catalog.carregar(db, topico)
    assert nome not in cat.admitidos
    assert nome in cat.conhecidos, "removido continua CONHECIDO — decidido é decidido"
    assert cat.batendo_na_porta([nome]) == []


def test_readmitir_reusa_a_linha_e_se_declara(db, topico):
    from bot import kb_catalog
    nome = f"teste-{uuid.uuid4().hex[:8]}.md"
    kb_catalog.admitir(db, topico, nome, titulo="Teste")
    kb_catalog.remover(db, topico, nome)
    assert kb_catalog.admitir(db, topico, nome, titulo="Teste") == "readmitido"
    linhas = [l for l in kb_catalog.listar(db, topico) if l["caminho"] == nome]
    assert len(linhas) == 1, "um arquivo tem UMA linha por tópico"
    assert linhas[0]["removido_em"] is None


def test_remover_o_que_nao_esta_admitido_devolve_falso(db, topico):
    """Para o agente poder dizer 'não estava lá' em vez de relatar sucesso de
    uma operação que não aconteceu."""
    from bot import kb_catalog
    assert kb_catalog.remover(db, topico, f"inexistente-{uuid.uuid4().hex[:6]}.md") is False


def test_titulo_vazio_e_recusado(db, topico):
    from bot import kb_catalog
    with pytest.raises(ValueError):
        kb_catalog.admitir(db, topico, "x.md", titulo="   ")
