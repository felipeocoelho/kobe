"""Bloco B do plano de testes da F0 — orçamento POR ARQUIVO na KB do tópico (E9).

O defeito que estes testes travam: a pasta `knowledge/` era tudo-ou-nada. Se a
SOMA passasse do teto, a pasta inteira virava lista de ponteiros e sumia do
prompt — com um `logger.info` que ninguém lê. Um arquivo grande derrubava todos
os pequenos junto, que é exatamente o caso do tópico `dev-kobe` (um índice de
86.213 chars sozinho): base curada aberta em 44 de 280 turnos.

B2 é o teste que prova o conserto; B6/B7 provam o gerador do índice curto contra
uma cópia do arquivo real de produção.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import subprocess
import sys
from pathlib import Path

import pytest

from bot import topic_manager

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def kb(tmp_path):
    """Escreve `user-data/topics/<slug>/knowledge/<nome>` e devolve o home."""
    slug = "topico-teste"
    d = tmp_path / "user-data" / "topics" / slug / "knowledge"
    d.mkdir(parents=True)

    def escrever(nome: str, conteudo: str) -> None:
        (d / nome).write_text(conteudo, encoding="utf-8")

    escrever.home = tmp_path  # type: ignore[attr-defined]
    escrever.slug = slug  # type: ignore[attr-defined]
    return escrever


def _carregar(kb, **kw):
    return topic_manager.load_topic_context(kb.home, kb.slug, **kw)


def test_b1_kb_pequena_vai_inteira_inline(kb):
    """B1 — abaixo do teto, tudo inline (sem regressão)."""
    kb("01-a.md", "conteudo A")
    kb("02-b.md", "conteudo B")

    bruto = _carregar(kb, knowledge_inline_limit=1000)
    texto, truncado, degradacao = topic_manager.consume_markers(bruto)

    assert "conteudo A" in texto and "conteudo B" in texto
    assert degradacao is None
    assert not truncado


def test_b2_arquivo_gigante_nao_derruba_os_pequenos(kb):
    """B2 — **o conserto**: o pequeno entra inline, só o gigante fica sob demanda.

    No comportamento antigo, os dois sumiam do prompt por causa da soma.
    """
    kb("00-curto.md", "MAPA CURTO DA BASE")
    kb("10-detalhado.md", "D" * 50_000)

    bruto = _carregar(kb, knowledge_inline_limit=12_000)
    texto, _, degradacao = topic_manager.consume_markers(bruto)

    assert "MAPA CURTO DA BASE" in texto           # entrou inline
    # 120 chars de previa do gigante entram no indice de proposito (e o que
    # deixa o agente saber SE vale abrir); o conteudo dele, nao.
    assert "D" * 200 not in texto
    assert len(texto) < 2000
    assert "10-detalhado.md" in texto               # mas está no índice sob demanda
    assert degradacao is not None
    assert degradacao.arquivos == ("10-detalhado.md",)
    assert degradacao.chars_fora == 50_000
    assert degradacao.teto == 12_000


def test_b2b_ordem_alfabetica_decide_quem_entra(kb):
    """A ordem é a convenção documentada (`00-`, `01-`, …), não o acaso."""
    kb("00-primeiro.md", "P" * 900)
    kb("01-segundo.md", "S" * 900)
    kb("02-terceiro.md", "T" * 900)

    bruto = _carregar(kb, knowledge_inline_limit=1000)
    texto, _, degradacao = topic_manager.consume_markers(bruto)

    assert "P" * 900 in texto
    assert "S" * 900 not in texto
    assert degradacao.arquivos == ("01-segundo.md", "02-terceiro.md")


def test_b3_b4_anti_ruido_do_aviso(kb):
    """B3/B4 — um aviso por tópico por processo; reemite se mudar de faixa."""
    from bot.telegram_handler import _DEGRADACAO_AVISADA, _deve_avisar_degradacao

    _DEGRADACAO_AVISADA.clear()
    d1 = topic_manager.KnowledgeDegraded(50_000, 12_000, ("x.md",))
    assert _deve_avisar_degradacao("t", d1) is True
    assert _deve_avisar_degradacao("t", d1) is False      # B3: não repete
    assert _deve_avisar_degradacao("outro", d1) is True   # tópico diferente avisa

    d2 = topic_manager.KnowledgeDegraded(60_000, 12_000, ("x.md",))
    assert _deve_avisar_degradacao("t", d2) is True       # B4: mudou de faixa
    _DEGRADACAO_AVISADA.clear()


def test_b5_teto_default_e_12000_e_a_env_manda(monkeypatch):
    """B5 — o default subiu pra 12.000 e continua sendo env."""
    assert topic_manager.TOPIC_KNOWLEDGE_INLINE_LIMIT == 12_000
    monkeypatch.setenv("TOPIC_KNOWLEDGE_INLINE_LIMIT", "777")
    recarregado = importlib.reload(topic_manager)
    try:
        assert recarregado.TOPIC_KNOWLEDGE_INLINE_LIMIT == 777
    finally:
        monkeypatch.undo()
        importlib.reload(recarregado)


def test_marcadores_nunca_vazam_pro_prompt(kb):
    """Regressão dura: NUL no prompt é lixo silencioso — e agora são dois marcadores."""
    kb("00-curto.md", "curto")
    kb("10-gigante.md", "G" * 40_000)

    bruto = _carregar(kb, knowledge_inline_limit=100)
    texto, _, degradacao = topic_manager.consume_markers(bruto)

    assert degradacao is not None
    assert "\x00" not in texto

    # E o consumidor antigo (usado por bot/resume.py) também limpa tudo.
    texto_legado, _ = topic_manager.consume_truncated_marker(bruto)
    assert "\x00" not in texto_legado


def test_consume_markers_tolera_none_e_texto_limpo():
    assert topic_manager.consume_markers(None) == (None, False, None)
    assert topic_manager.consume_markers("oi") == ("oi", False, None)


# ── B6/B7: o gerador do índice curto ──────────────────────────────────────

FIXTURE_DETALHADO = """# Índice — Base de conhecimento sobre o Kobe

## Como usar

Texto de instrução, sem lista.

## Arquitetura

- `arquitetura/01.md` — visão geral
- `arquitetura/02.md` — fluxo de mensagem

## Decisões

- `decisoes/a.md` — uma decisão
"""


def test_b6_indice_curto_mapeia_secoes_e_aponta_o_detalhado(tmp_path):
    """B6 — o curto tem todas as seções, a contagem certa e o caminho absoluto."""
    import importlib.util

    caminho = REPO / "bot" / "bin" / "kobe-kb-shortindex"
    spec = importlib.util.spec_from_loader(
        "kobe_kb_shortindex",
        importlib.machinery.SourceFileLoader("kobe_kb_shortindex", str(caminho)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    detalhado = tmp_path / "10-indice-detalhado.md"
    detalhado.write_text(FIXTURE_DETALHADO, encoding="utf-8")

    curto = mod.montar(detalhado)

    assert "Como usar" in curto and "Arquitetura" in curto and "Decisões" in curto
    assert "2 entrada(s)" in curto           # Arquitetura
    assert "1 entrada(s)" in curto           # Decisões
    assert str(detalhado.resolve()) in curto  # o Read tem alvo
    assert len(curto) < 4000


def test_b7_gerador_e_deterministico(tmp_path):
    """B7 — rodar 2× dá byte a byte o mesmo (senão o curto apodrece sozinho)."""
    detalhado = tmp_path / "10-indice-detalhado.md"
    detalhado.write_text(FIXTURE_DETALHADO, encoding="utf-8")
    exe = [sys.executable, str(REPO / "bot" / "bin" / "kobe-kb-shortindex"), str(detalhado)]

    a = subprocess.run(exe, capture_output=True, text=True, check=True).stdout
    b = subprocess.run(exe, capture_output=True, text=True, check=True).stdout
    assert a == b
