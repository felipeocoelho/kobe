"""Bloco C do plano de testes da F0 — a consulta cega de memória fica desligada.

O item 3 da F0 é o que devolve **4 a 7 segundos por turno**. Medido no briefing
contra o bank de produção do operador (934 fatos, 6.053 ligações): `budget=low`
3,8 s, `budget=mid` — o configurado — 5,1 a 7,2 s, pra entregar ~200 tokens, que
são 0,3% do prompt. E piora sozinho: a mesma consulta num bank de 10 fatos leva
0,5 s, e o bank cresce ~30 fatos por dia.

**O que estes testes travam:** que o default é desligado, e sobretudo que
desligar a LEITURA não desliga a GRAVAÇÃO — a memória continua sendo construída
em silêncio, que é a condição pra F3 e F4 existirem depois.

O que eles NÃO provam está declarado no plano como lacuna **L1**: a magnitude
dos 4–7 s não é reproduzível em dev, porque o bank de dev está praticamente
vazio. A confirmação da magnitude só acontece em produção, depois do passo P1.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from bot.config import load_config
from bot.telegram_handler import _recall_ativo, _retain_ativo


@pytest.fixture
def config_base(monkeypatch, tmp_path):
    """Config real, carregada de um `.env` MÍNIMO — sem as chaves do Hindsight.

    O `.env` é explícito de propósito: `load_config(None)` chama `load_dotenv()`
    sem caminho, que sobe procurando um `.env` na árvore e acabaria lendo o do
    ambiente de quem roda o teste. Aí o teste mediria a configuração da máquina,
    não o default do código — que é justamente o que ele existe pra travar.
    """
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_BOT_TOKEN=x\n"
        "TELEGRAM_ALLOWED_USER_IDS=1\n"
        "DATABASE_URL=postgresql://x/y\n"
        "GROQ_API_KEY=x\n"
        f"KOBE_HOME={tmp_path}\n",
        encoding="utf-8",
    )
    for chave in ("HINDSIGHT_ENABLED", "HINDSIGHT_RECALL", "HINDSIGHT_RETAIN"):
        monkeypatch.delenv(chave, raising=False)
    for chave in (
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS",
        "DATABASE_URL", "GROQ_API_KEY", "KOBE_HOME",
    ):
        monkeypatch.delenv(chave, raising=False)
    return load_config(env)


def test_c1_recall_nasce_desligado(config_base):
    """C1 — sem ninguém dizer nada, a consulta cega NÃO roda."""
    assert config_base.hindsight_recall_enabled is False
    assert _recall_ativo(config_base) is False


def test_c4_desligar_a_leitura_nao_desliga_a_gravacao(config_base):
    """C4 — o critério do briefing em uma linha: *a gravação continua*."""
    assert config_base.hindsight_retain_enabled is True
    assert _retain_ativo(config_base) is True
    assert _recall_ativo(config_base) is False


def test_c3_ligar_o_recall_e_uma_linha(config_base):
    """C3 — o gate é a flag, não código morto: religar volta o comportamento."""
    religado = replace(config_base, hindsight_recall_enabled=True)
    assert _recall_ativo(religado) is True


def test_master_desliga_os_dois(config_base):
    """O kill-switch continua sendo kill-switch."""
    desligado = replace(
        config_base, hindsight_enabled=False, hindsight_recall_enabled=True
    )
    assert _recall_ativo(desligado) is False
    assert _retain_ativo(desligado) is False


def test_c2_o_prompt_nao_ganha_secao_de_memoria_quando_o_recall_esta_off():
    """C2 — sem recall, o bloco `[Memória durável recuperada]` não existe.

    Provado no ponto onde o prompt é montado: `durable_memory=None` é o que o
    handler passa quando o gate está fechado.
    """
    from bot.claude_runner import build_prompt

    prompt = build_prompt(
        thread_id=1,
        history=[],
        new_message="oi",
        durable_memory=None,
    )
    assert "Memória durável" not in prompt

    # E com recall ligado a seção aparece — senão o teste acima passaria mesmo
    # se alguém removesse a funcionalidade inteira.
    from bot.hindsight_client import render_recall_section

    secao = render_recall_section([{"text": "um fato", "type": "world"}])
    prompt_com = build_prompt(
        thread_id=1, history=[], new_message="oi", durable_memory=secao
    )
    assert "Memória durável" in prompt_com
