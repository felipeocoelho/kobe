#!/usr/bin/env python3
"""A noção de ambiente do Kobe (Sessão #1, P1) — `bot/environment.py`.

O que estes testes seguram, em uma frase cada:

- **Default `prod`**: sem `KOBE_ENV`, tudo se comporta como hoje. É a invariante
  de aditividade do projeto inteiro, no ponto onde ela nasce.
- **Erro explícito**: valor desconhecido derruba o start com mensagem, em vez de
  cair calado em produção — uma instância que se comporta como prod enquanto a
  pessoa jura que está em dev é o pior desfecho possível.
- **Tradução do erro**: quem carrega a configuração vê `ConfigError`, não um
  `ValueError` cru vazando de outra camada.

Rodar: .venv/bin/python -m pytest tests/test_environment_layer.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import environment as env_layer
from bot.config import ConfigError, _environment_from_env


# ── Default: o mundo de hoje ──────────────────────────────────────────────


def test_sem_a_variavel_o_ambiente_e_prod(monkeypatch) -> None:
    monkeypatch.delenv("KOBE_ENV", raising=False)
    assert env_layer.current() == "prod"
    assert env_layer.is_dev() is False


@pytest.mark.parametrize("vazio", ["", "   ", "\t"])
def test_variavel_vazia_tambem_e_prod(monkeypatch, vazio: str) -> None:
    """`.env` com `KOBE_ENV=` (chave sem valor) não pode virar erro de start."""
    monkeypatch.setenv("KOBE_ENV", vazio)
    assert env_layer.current() == "prod"


# ── Normalização ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cru,esperado",
    [
        ("dev", "dev"),
        ("DEV", "dev"),
        (" dev ", "dev"),
        ("Dev", "dev"),
        ("prod", "prod"),
        ("PROD", "prod"),
        (" Prod\n", "prod"),
    ],
)
def test_caixa_e_espaco_sao_tolerados(cru: str, esperado: str) -> None:
    """`.env` escrito à mão erra caixa e espaço; recusar isso seria rigor vazio."""
    assert env_layer.normalize(cru) == esperado


@pytest.mark.parametrize("invalido", ["staging", "producao", "development", "test", "d"])
def test_valor_desconhecido_levanta_erro_nomeando_o_problema(invalido: str) -> None:
    with pytest.raises(env_layer.InvalidEnvironment) as exc:
        env_layer.normalize(invalido)
    mensagem = str(exc.value)
    assert "KOBE_ENV" in mensagem
    assert invalido in mensagem
    assert "prod" in mensagem and "dev" in mensagem


def test_nao_ha_fallback_silencioso(monkeypatch) -> None:
    """A regra que mais importa: desconhecido NUNCA vira prod por omissão."""
    monkeypatch.setenv("KOBE_ENV", "staging")
    with pytest.raises(env_layer.InvalidEnvironment):
        env_layer.current()


# ── Tradução do erro na fronteira da configuração ─────────────────────────


def test_config_reembrulha_o_erro_como_config_error(monkeypatch) -> None:
    """O start do bot só sabe tratar `ConfigError` — o erro tem que chegar assim."""
    monkeypatch.setenv("KOBE_ENV", "staging")
    with pytest.raises(ConfigError) as exc:
        _environment_from_env()
    assert "KOBE_ENV" in str(exc.value)


def test_config_devolve_prod_sem_a_variavel(monkeypatch) -> None:
    monkeypatch.delenv("KOBE_ENV", raising=False)
    assert _environment_from_env() == "prod"


# ── is_dev, incluindo a injeção explícita ─────────────────────────────────


def test_is_dev_com_parametro_explicito_ignora_o_ambiente(monkeypatch) -> None:
    monkeypatch.setenv("KOBE_ENV", "prod")
    assert env_layer.is_dev("dev") is True
    monkeypatch.setenv("KOBE_ENV", "dev")
    assert env_layer.is_dev("prod") is False
