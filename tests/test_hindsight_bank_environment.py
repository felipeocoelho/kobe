#!/usr/bin/env python3
"""Prefixo de ambiente no bank da memória durável (Sessão #1, P4).

Este é o **cinto de segurança**, não o mecanismo de isolamento — o isolamento de
verdade é de servidor (o dev tem instância própria do Hindsight, em porta e
volume próprios). O prefixo cobre um erro específico e plausível: o `.env` de
dev vir com `HINDSIGHT_BASE_URL` ainda apontando para o Hindsight de produção.
Sem o prefixo, esse erro de uma linha contamina a memória viva do operador em
silêncio, porque o Hindsight aceita a escrita sem reclamar. Com ele, o estrago
vira um bank órfão.

Rodar: .venv/bin/python -m pytest tests/test_hindsight_bank_environment.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.hindsight_client import bank_id_for_topic


# ── Produção: byte-a-byte o de hoje ───────────────────────────────────────


@pytest.mark.parametrize(
    "slug,esperado",
    [
        ("geral", "kobe-geral"),
        ("dev-kobe", "kobe-dev-kobe"),
        ("Olimpo", "kobe-olimpo"),
        ("Café & Livros", "kobe-caf-livros"),
        (None, "kobe-general"),
        ("", "kobe-general"),
        ("---", "kobe-general"),
    ],
)
def test_sem_kobe_env_o_bank_e_o_de_hoje(monkeypatch, slug, esperado: str) -> None:
    monkeypatch.delenv("KOBE_ENV", raising=False)
    assert bank_id_for_topic(slug) == esperado


def test_prod_explicito_da_no_mesmo(monkeypatch) -> None:
    monkeypatch.setenv("KOBE_ENV", "prod")
    assert bank_id_for_topic("geral") == "kobe-geral"


# ── Dev: prefixado ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "slug,esperado",
    [
        ("geral", "kobe-dev-geral"),
        ("ambiente-dev", "kobe-dev-ambiente-dev"),
        (None, "kobe-dev-general"),
        ("", "kobe-dev-general"),
    ],
)
def test_em_dev_o_bank_ganha_prefixo(monkeypatch, slug, esperado: str) -> None:
    monkeypatch.setenv("KOBE_ENV", "dev")
    assert bank_id_for_topic(slug) == esperado


def test_nenhum_bank_de_dev_colide_com_um_de_prod(monkeypatch) -> None:
    """A propriedade que o cinto de segurança precisa ter, dita como propriedade.

    O caso capcioso é o tópico chamado `dev-kobe`: em produção ele vira
    `kobe-dev-kobe`, que "parece" de dev. Tem que ser diferente do bank que o
    ambiente de dev usaria para o mesmo tópico, senão o prefixo não protege
    justamente o tópico onde o desenvolvimento acontece.
    """
    slugs = ["geral", "dev-kobe", "olimpo", "ambiente-dev", "dev", None, ""]

    monkeypatch.delenv("KOBE_ENV", raising=False)
    de_prod = {bank_id_for_topic(s) for s in slugs}
    monkeypatch.setenv("KOBE_ENV", "dev")
    de_dev = {bank_id_for_topic(s) for s in slugs}

    assert de_prod.isdisjoint(de_dev)


# ── Injeção explícita (os testes não dependem de env global) ──────────────


def test_parametro_explicito_vence_o_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("KOBE_ENV", "prod")
    assert bank_id_for_topic("geral", environment="dev") == "kobe-dev-geral"
    monkeypatch.setenv("KOBE_ENV", "dev")
    assert bank_id_for_topic("geral", environment="prod") == "kobe-geral"


def test_ambiente_invalido_no_parametro_levanta(monkeypatch) -> None:
    """Não há fallback silencioso nem por aqui."""
    from bot.environment import InvalidEnvironment

    with pytest.raises(InvalidEnvironment):
        bank_id_for_topic("geral", environment="staging")
