#!/usr/bin/env python3
"""Verificador de paridade de `.env` (Sessão #1, P6).

O teste que dá nome a este arquivo é o **anti-vazamento**: um valor reconhecível
é plantado nos dois lados e o teste assevera que ele não aparece em nenhuma
saída — nem no relatório, nem no log, nem na exceção. Um `.env` é o arquivo mais
sensível da instalação; uma ferramenta de diagnóstico que imprima valores vira o
caminho mais curto para um segredo acabar num journal.

Os fixtures são `.env` sintéticos criados em `tmp_path`. Teste não lê arquivo de
segredo de verdade — a conferência contra os `.env` reais foi feita à mão, uma
vez, e o resultado está no CHANGELOG.

Rodar: .venv/bin/python -m pytest tests/test_env_parity.py -q
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from infra.env_parity import avisar_no_start, comparar, main, nomes_de_chave

# O valor plantado. Se ele aparecer em qualquer saída, o teste falha — e falha
# nomeando o vazamento, que é o ponto.
SEGREDO = "valor-secreto-que-nunca-pode-vazar-xyz789"


def _env(tmp_path: Path, nome: str, linhas: list[str]) -> Path:
    caminho = tmp_path / nome
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


# ── A regra que governa o desenho: valor nenhum sai daqui ─────────────────


def test_nenhum_valor_aparece_no_relatorio(tmp_path: Path, capsys) -> None:
    ref = _env(tmp_path, "ref.env", [f"TOKEN={SEGREDO}", f"SO_NA_REF={SEGREDO}"])
    alvo = _env(tmp_path, "alvo.env", [f"TOKEN={SEGREDO}", f"SO_NO_ALVO={SEGREDO}"])

    codigo = main(["env_parity.py", str(ref), str(alvo)])
    saida = capsys.readouterr()

    assert codigo == 1
    assert SEGREDO not in saida.out
    assert SEGREDO not in saida.err
    # ...e o relatório continua útil: os NOMES saem.
    assert "SO_NA_REF" in saida.out
    assert "SO_NO_ALVO" in saida.out


def test_nenhum_valor_aparece_no_log_do_start(tmp_path: Path, caplog) -> None:
    ref = _env(tmp_path, "ref.env", [f"A={SEGREDO}", f"FALTANTE={SEGREDO}"])
    alvo = _env(tmp_path, "alvo.env", [f"A={SEGREDO}"])

    with caplog.at_level(logging.WARNING):
        avisar_no_start(ref, alvo, logging.getLogger("teste.paridade"))

    registrado = caplog.text
    assert SEGREDO not in registrado
    assert "FALTANTE" in registrado


def test_valor_com_igual_dentro_nao_confunde_o_parser(tmp_path: Path) -> None:
    """`SENHA=a=b=c` é um valor legítimo — o nome é só o que vem antes do 1º `=`."""
    arquivo = _env(tmp_path, "x.env", ["SENHA=a=b=c", "URL=postgres://u:p@h/db"])
    assert nomes_de_chave(arquivo) == {"SENHA", "URL"}


# ── Parsing ───────────────────────────────────────────────────────────────


def test_comentario_e_linha_vazia_sao_ignorados(tmp_path: Path) -> None:
    arquivo = _env(
        tmp_path,
        "x.env",
        ["# comentário", "", "   ", "REAL=1", "# OUTRA=2", "  ESPACADA = 3  "],
    )
    assert nomes_de_chave(arquivo) == {"REAL", "ESPACADA"}


def test_export_e_aceito(tmp_path: Path) -> None:
    """`.env` escrito à mão costuma ter `export`."""
    arquivo = _env(tmp_path, "x.env", ["export FOO=1", "BAR=2"])
    assert nomes_de_chave(arquivo) == {"FOO", "BAR"}


def test_linha_sem_igual_nao_vira_chave(tmp_path: Path) -> None:
    arquivo = _env(tmp_path, "x.env", ["isto não é uma atribuição", "OK=1"])
    assert nomes_de_chave(arquivo) == {"OK"}


# ── Comparação e código de saída ──────────────────────────────────────────


def test_paridade_perfeita_sai_zero(tmp_path: Path, capsys) -> None:
    ref = _env(tmp_path, "ref.env", ["A=1", "B=2"])
    alvo = _env(tmp_path, "alvo.env", ["B=outro", "A=outro"])  # ordem e valor diferem
    assert main(["env_parity.py", str(ref), str(alvo)]) == 0
    assert "paridade ok" in capsys.readouterr().out


def test_reporta_os_dois_sentidos(tmp_path: Path) -> None:
    ref = _env(tmp_path, "ref.env", ["COMUM=1", "SO_REF=1"])
    alvo = _env(tmp_path, "alvo.env", ["COMUM=1", "SO_ALVO=1"])
    r = comparar(ref, alvo)
    assert r.faltando_no_alvo == frozenset({"SO_REF"})
    assert r.sobrando_no_alvo == frozenset({"SO_ALVO"})
    assert r.em_paridade is False


def test_relatorio_distingue_os_dois_lados_pelo_caminho(tmp_path: Path) -> None:
    """Os dois arquivos quase sempre se chamam `.env` — só o nome não serve."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ref = _env(tmp_path / "a", ".env", ["SO_REF=1"])
    alvo = _env(tmp_path / "b", ".env", ["SO_ALVO=1"])
    texto = " ".join(comparar(ref, alvo).linhas())
    assert str(ref) in texto and str(alvo) in texto


def test_arquivo_inexistente_sai_dois(tmp_path: Path) -> None:
    """Código 2 = erro de uso, distinto de 1 = divergência encontrada."""
    existe = _env(tmp_path, "x.env", ["A=1"])
    assert main(["env_parity.py", str(existe), str(tmp_path / "nao-existe.env")]) == 2


def test_uso_errado_sai_dois(capsys) -> None:
    assert main(["env_parity.py"]) == 2


# ── O gancho do start nunca derruba o bot ─────────────────────────────────


def test_arquivo_ausente_no_start_apenas_avisa(tmp_path: Path, caplog) -> None:
    """Paridade é diagnóstico, não requisito. Falhar aqui deixaria o bot no chão."""
    alvo = _env(tmp_path, "alvo.env", ["A=1"])
    with caplog.at_level(logging.WARNING):
        avisar_no_start(tmp_path / "nao-existe.env", alvo, logging.getLogger("t"))
    assert "não deu pra comparar" in caplog.text


def test_paridade_ok_no_start_nao_gera_warning(tmp_path: Path, caplog) -> None:
    ref = _env(tmp_path, "ref.env", ["A=1"])
    alvo = _env(tmp_path, "alvo.env", ["A=2"])
    with caplog.at_level(logging.WARNING):
        avisar_no_start(ref, alvo, logging.getLogger("t"))
    assert caplog.text == ""


# ── Aditividade do gancho ─────────────────────────────────────────────────


def test_sem_a_variavel_de_referencia_o_start_nao_faz_nada(monkeypatch) -> None:
    """Sem `KOBE_ENV_PARITY_REFERENCE`, nem o verificador é importado."""
    from types import SimpleNamespace

    import bot.main as main_mod

    monkeypatch.delenv("KOBE_ENV_PARITY_REFERENCE", raising=False)
    chamou = []
    monkeypatch.setattr(
        main_mod.logger, "warning", lambda *a, **k: chamou.append(a)
    )
    main_mod._avisar_paridade_de_env(SimpleNamespace(kobe_home=Path("/nao/existe")))
    assert chamou == []
