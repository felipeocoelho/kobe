#!/usr/bin/env python3
"""Prova de que a camada de ambiente é ADITIVA no prompt (Sessão #1, P2).

O QUE ESTE TESTE GARANTE
------------------------
A invariante nº 1 do projeto "Novo Ambiente Kobe" é aditividade total: com o
`.env` que a produção tem hoje (sem nenhuma variável nova), o comportamento tem
que ser **idêntico**. Para o prompt, "idêntico" não é opinião — é igualdade
byte-a-byte contra um arquivo dourado (`tests/fixtures/prompt_baseline_prod.txt`)
gerado a partir do código de ANTES da camada existir.

Por que arquivo dourado e não asserção pontual: uma asserção do tipo "o prompt
não começa com [Ambiente]" passaria mesmo se a mudança tivesse mexido no meio do
prompt. O dourado pega qualquer deslocamento, em qualquer seção.

COMO REGERAR O DOURADO
----------------------
Só se a mudança no prompt for **intencional e aprovada** — nunca "pra fazer o
teste passar":

    .venv/bin/python -m tests.test_prompt_environment_banner --regravar

DETERMINISMO
------------
`build_prompt` carimba o relógio (`[Agora]`) e calcula idade de cada linha do
histórico. Os dois são congelados aqui via mock de `bot.claude_runner.datetime`,
senão o dourado envelheceria sozinho e o teste viraria bomba-relógio (foi
exatamente esse erro que `tests/test_prompt_aging.py` documenta no cabeçalho).

Rodar: .venv/bin/python -m pytest tests/test_prompt_environment_banner.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import claude_runner
from bot.claude_runner import build_prompt

DOURADO = Path(__file__).resolve().parent / "fixtures" / "prompt_baseline_prod.txt"

# Relógio congelado do fixture. Qualquer valor serve — o que importa é ser fixo.
AGORA_UTC = datetime(2026, 8, 25, 23, 30, 0, tzinfo=timezone.utc)

# Argumentos do fixture: TODAS as seções opcionais preenchidas, para o dourado
# cobrir o prompt inteiro e não só o caminho magro. Textos estáticos de
# propósito — o que se está medindo é a MONTAGEM, não o conteúdo.
ARGS_FIXOS = dict(
    thread_id=42,
    history=[
        {"role": "user", "content": "primeira", "created_at": "2026-08-13T10:00:00+00:00"},
        {"role": "assistant", "content": "segunda", "created_at": "2026-08-13T10:01:00+00:00"},
        {"role": "user", "content": "terceira", "created_at": "2026-08-25T23:20:00+00:00"},
    ],
    new_message="mensagem nova do operador",
    plugins_section="[Plugins disponíveis]\n- exemplo: faz alguma coisa",
    topic_context="conteúdo curado do tópico",
    sala_ativa_info='[Sala de missão ativa neste tópico: xyz — "obj"]',
    alertas_abertos_info="[Alertas aguardando confirmação neste tópico]\n- id123",
    curated_core="[Núcleo curado]\nfato durável",
    grounding_signals="[Grounding] última troca há 10 min",
    background_state="[Estado de background vivo]\nnada rodando",
    durable_memory="[Memória durável]\npista cética",
    audio_transcribed=True,
    background_handoff="[Handoff de background] você está em bg",
    quoted_message="mensagem citada pelo operador",
    attachments_section="[Anexos deste turno]\n- foto.jpg",
)


class _RelogioCongelado:
    """Substituto de `datetime` que congela só o `now()`.

    O resto (`fromisoformat`, aritmética) tem que continuar funcionando, porque
    `build_prompt` e `bot.memory.aging` usam a classe real para outras coisas.
    """

    @staticmethod
    def now(tz=None):
        return AGORA_UTC.astimezone(tz) if tz is not None else AGORA_UTC

    def __getattr__(self, nome):  # pragma: no cover — delegação trivial
        return getattr(datetime, nome)


def prompt_congelado(**extra) -> str:
    """Monta o prompt do fixture com o relógio parado."""
    args = {**ARGS_FIXOS, **extra}
    with mock.patch.object(claude_runner, "datetime", _RelogioCongelado()):
        return build_prompt(**args)


# ── A prova de aditividade ────────────────────────────────────────────────


def test_sem_kobe_env_o_prompt_e_identico_ao_de_antes_da_camada(monkeypatch) -> None:
    """Invariante 1: `.env` de hoje (sem KOBE_ENV) → prompt byte-a-byte igual."""
    monkeypatch.delenv("KOBE_ENV", raising=False)
    assert DOURADO.exists(), (
        f"dourado ausente: {DOURADO}. Regere com "
        "`python -m tests.test_prompt_environment_banner --regravar` "
        "a partir do código SEM a camada de ambiente."
    )
    esperado = DOURADO.read_text(encoding="utf-8")
    obtido = prompt_congelado()
    assert obtido == esperado, (
        "o prompt de produção MUDOU. Isso viola a aditividade da Sessão #1. "
        "Se a mudança for intencional e aprovada, regere o dourado; senão, "
        "conserte o código."
    )


def test_kobe_env_prod_explicito_tambem_e_identico(monkeypatch) -> None:
    """`KOBE_ENV=prod` escrito à mão tem que dar no mesmo que ausente."""
    monkeypatch.setenv("KOBE_ENV", "prod")
    assert prompt_congelado() == DOURADO.read_text(encoding="utf-8")


# ── O banner de dev (P2, ponto 1) ─────────────────────────────────────────

BANNER_ESPERADO = (
    "[Ambiente] DESENVOLVIMENTO — esta instância é de DEV. Não faça deploy, "
    "não publique, não trate esta conversa como memória de produção."
)


@pytest.mark.parametrize("valor", ["dev", "DEV", " dev "])
def test_em_dev_o_banner_e_a_primeira_linha(monkeypatch, valor: str) -> None:
    monkeypatch.setenv("KOBE_ENV", valor)
    p = prompt_congelado()
    assert p.splitlines()[0] == BANNER_ESPERADO
    # Vem ANTES até da nota de handoff de background, que hoje é a primeira.
    assert p.index(BANNER_ESPERADO) < p.index("[Handoff de background]")


def test_em_dev_o_resto_do_prompt_nao_muda(monkeypatch) -> None:
    """O banner ACRESCENTA; não reordena nem reescreve nada."""
    monkeypatch.setenv("KOBE_ENV", "dev")
    com_banner = prompt_congelado()
    monkeypatch.delenv("KOBE_ENV")
    sem_banner = prompt_congelado()
    assert com_banner.endswith(sem_banner)
    assert com_banner[: -len(sem_banner)] == BANNER_ESPERADO + "\n\n"


def test_parametro_explicito_vence_o_ambiente(monkeypatch) -> None:
    """Injetável em teste sem depender de variável de ambiente global."""
    monkeypatch.delenv("KOBE_ENV", raising=False)
    assert prompt_congelado(environment="dev").splitlines()[0] == BANNER_ESPERADO
    monkeypatch.setenv("KOBE_ENV", "dev")
    assert prompt_congelado(environment="prod").splitlines()[0] != BANNER_ESPERADO


def _regravar() -> None:
    import os

    os.environ.pop("KOBE_ENV", None)
    DOURADO.parent.mkdir(parents=True, exist_ok=True)
    DOURADO.write_text(prompt_congelado(), encoding="utf-8")
    print(f"dourado regravado: {DOURADO}")


if __name__ == "__main__":
    if "--regravar" in sys.argv:
        _regravar()
    else:
        print(__doc__)
