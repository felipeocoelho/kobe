#!/usr/bin/env python3
"""Arnês de injeção de `Update` (Sessão #1, P9) — e, sobretudo, suas travas.

Esta ferramenta é uma **porta de entrada no bot**: monta um update que diz ser o
operador e o entrega direto ao despachante, pulando o Telegram. O valor dela é
fechar a bateria de aceite sem ninguém digitar; o risco é acabar no caminho de
produção. Por isso a maior parte dos testes aqui é sobre **recusar**.

A assimetria que vale explicar: em `bot/authz.py`, whitelist vazia LIBERA (a
instância não filtra canal). Aqui, whitelist vazia RECUSA — porque significa
"ninguém me disse onde é seguro bater", e injetar às cegas é justamente o que a
trava existe para impedir.

Rodar: .venv/bin/python -m pytest tests/test_dev_inject.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from infra.dev_inject import (
    ECO_PREFIXO,
    RecusaDeSeguranca,
    conferir_alvo,
    conferir_ambiente,
    forcar_env_do_arquivo,
    ler_roteiro,
    montar_update,
    percentil,
)

CHAT_DEV = -1004448020751
OPERADOR = 111222333


def _bot_falso():
    """Um `Bot` de verdade, com token sintético. Construir não faz rede nenhuma —
    é só o objeto que o `Update` precisa carregar."""
    from telegram import Bot

    return Bot("123456:AAHt-token-sintetico-de-teste")


# ── Trava 0: o `.env` da árvore vence o ambiente herdado ─────────────────


@pytest.fixture
def ambiente_restaurado():
    """Devolve `os.environ` ao que era, byte a byte, no fim do teste.

    `monkeypatch.setenv` não basta aqui: a função sob teste escreve DIRETO em
    `os.environ`, e o monkeypatch só desfaz o que ele mesmo fez. Sem esta
    fixture, um `KOBE_ENV=dev` escrito por este teste vaza para a suíte inteira
    — e foi o que aconteceu: dois testes de montagem de prompt, lá longe,
    passaram a receber o cabeçalho `[Ambiente] DESENVOLVIMENTO` e quebraram.
    Teste que suja o ambiente global falha um vizinho inocente, e o vizinho leva
    a culpa.
    """
    import os as _os

    antes = dict(_os.environ)
    try:
        yield
    finally:
        _os.environ.clear()
        _os.environ.update(antes)


def test_env_do_arquivo_vence_ambiente_herdado(tmp_path, monkeypatch, ambiente_restaurado) -> None:
    """O caso real de 26/08: a sessão automatizada herdou `TELEGRAM_BOT_TOKEN`
    do Kobe de PRODUÇÃO, `load_dotenv()` não sobrescreveu, e o arnês subiu
    falando como o bot de produção apontado pro chat de dev.

    Só não deu estrago porque o bot de produção não é membro do grupo de dev.
    Depender disso é depender de sorte.
    """
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=token-de-DEV\nKOBE_ENV=dev\n", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-de-PRODUCAO")

    sobrescritos = forcar_env_do_arquivo(env)

    import os as _os

    assert _os.environ["TELEGRAM_BOT_TOKEN"] == "token-de-DEV"
    assert "TELEGRAM_BOT_TOKEN" in sobrescritos, "a substituição tem de ser reportada"


def test_variavel_so_do_arquivo_nao_conta_como_sobrescrita(
    tmp_path, monkeypatch, ambiente_restaurado
) -> None:
    """Ruído no aviso é aviso que ninguém lê. Só conta o que ATROPELOU algo."""
    env = tmp_path / ".env"
    env.write_text("SO_NO_ARQUIVO=1\nIGUAL_NOS_DOIS=mesmo\n", encoding="utf-8")
    monkeypatch.delenv("SO_NO_ARQUIVO", raising=False)
    monkeypatch.setenv("IGUAL_NOS_DOIS", "mesmo")

    assert forcar_env_do_arquivo(env) == []


# ── Trava 1: só em dev ────────────────────────────────────────────────────


def test_recusa_em_producao() -> None:
    with pytest.raises(RecusaDeSeguranca) as exc:
        conferir_ambiente("prod")
    assert "produção" in str(exc.value)


def test_aceita_em_dev() -> None:
    conferir_ambiente("dev")  # não levanta


# ── Trava 2: o alvo tem de estar na lista, e a lista não pode estar vazia ─


def test_whitelist_vazia_recusa() -> None:
    """Aqui vazia NÃO é liberação — é ausência de informação."""
    with pytest.raises(RecusaDeSeguranca) as exc:
        conferir_alvo(CHAT_DEV, frozenset())
    assert "vazia" in str(exc.value)


def test_chat_fora_da_lista_recusa() -> None:
    with pytest.raises(RecusaDeSeguranca) as exc:
        conferir_alvo(-100999, frozenset([CHAT_DEV]))
    assert "-100999" in str(exc.value)


def test_chat_na_lista_passa() -> None:
    conferir_alvo(CHAT_DEV, frozenset([CHAT_DEV]))  # não levanta


def test_a_recusa_sempre_nomeia_a_razao() -> None:
    """Recusa muda é recusa que ninguém consegue destravar."""
    for chamada in (
        lambda: conferir_ambiente("prod"),
        lambda: conferir_alvo(CHAT_DEV, frozenset()),
        lambda: conferir_alvo(1, frozenset([CHAT_DEV])),
    ):
        with pytest.raises(RecusaDeSeguranca) as exc:
            chamada()
        assert len(str(exc.value)) > 40, "mensagem curta demais pra ser acionável"


# ── O `Update` sintético tem de ser fiel ao que o Telegram entrega ────────


def test_update_carrega_chat_topico_usuario_e_texto() -> None:
    u = montar_update(
        update_id=7, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="oi"
    )
    assert u.update_id == 7
    assert u.effective_chat.id == CHAT_DEV
    assert u.effective_user.id == OPERADOR
    assert u.effective_message.text == "oi"
    assert u.effective_message.message_thread_id == 2


def test_mensagem_de_topico_e_marcada_como_tal() -> None:
    """Sem `is_topic_message`, o bot trata como "Geral" e não carrega a KB do
    tópico — o teste passaria exercitando um caminho diferente do real."""
    com_topico = montar_update(
        update_id=1, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="x"
    )
    sem_topico = montar_update(
        update_id=2, chat_id=CHAT_DEV, thread_id=None, user_id=OPERADOR, texto="x"
    )
    assert com_topico.effective_message.is_topic_message is True
    # Fora de tópico o campo é AUSENTE, não `False` — é assim que o Telegram
    # entrega, e montar por `de_json` faz o arnês herdar essa fidelidade.
    assert not sem_topico.effective_message.is_topic_message


# ── A regressão que derrubava a bateria inteira (2026-08-26) ──────────────


def test_o_update_carrega_o_bot_perna_1_do_conserto() -> None:
    """O bug: `Message` montado à mão não tem bot, e `_react_received` chama
    `message.get_bot()` na PRIMEIRA linha do handler. Sem isto o turno morre
    com `RuntimeError: This object has no bot associated with it` antes de
    começar — e a bateria inteira media o nada.

    Este teste é o que acende vermelho se alguém desfizer o conserto.
    """
    bot = _bot_falso()
    u = montar_update(
        update_id=1, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="oi",
        bot=bot,
    )
    assert u.effective_message.get_bot() is bot
    # E não só a mensagem: `de_json` desce o bot pela árvore inteira. É por isso
    # que ele foi escolhido em vez de `set_bot()`, que marcaria só a raiz.
    assert u.effective_chat.get_bot() is bot
    assert u.effective_user.get_bot() is bot


def test_sem_bot_a_montagem_funciona_mas_get_bot_recusa() -> None:
    """`bot=None` existe pra o teste conferir payload sem rede. O que não pode é
    fingir que há bot: `get_bot()` tem de levantar, que é o sintoma original."""
    u = montar_update(
        update_id=1, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="oi"
    )
    assert u.effective_message.text == "oi"
    with pytest.raises(RuntimeError):
        u.effective_message.get_bot()


def test_message_id_real_entra_no_update_perna_2_do_conserto() -> None:
    """O bot responde CITANDO a mensagem de entrada. Com `message_id` inventado,
    o Telegram recusa com "Message to be replied not found" e o turno morre ao
    responder. O eco publica de verdade e passa o id real por aqui."""
    u = montar_update(
        update_id=42, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="oi",
        message_id=987_654,
    )
    assert u.update_id == 42
    assert u.effective_message.message_id == 987_654


def test_sem_message_id_cai_no_update_id() -> None:
    """Compatibilidade com quem chama sem eco: o id continua saindo do update."""
    u = montar_update(
        update_id=42, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="oi"
    )
    assert u.effective_message.message_id == 42


def test_o_eco_e_marcado_como_teste() -> None:
    """Mensagem de bateria não pode passar por mensagem de gente — nem hoje, nem
    pra quem ler o histórico do tópico daqui a seis meses."""
    assert ECO_PREFIXO and not ECO_PREFIXO.isascii()


def test_o_update_sintetico_passa_pela_autorizacao_real() -> None:
    """Prova de fidelidade: o que a ferramenta monta é aceito por `bot/authz.py`.

    Se não passasse, a bateria testaria a ferramenta em vez de testar o bot.
    """
    from types import SimpleNamespace

    from bot import authz

    cfg = SimpleNamespace(
        allowed_user_ids=frozenset([OPERADOR]),
        telegram_allowed_chat_ids=frozenset([CHAT_DEV]),
    )
    u = montar_update(
        update_id=1, chat_id=CHAT_DEV, thread_id=2, user_id=OPERADOR, texto="x"
    )
    assert authz.update_authorized(u, cfg) is True

    intruso = montar_update(
        update_id=2, chat_id=-100999, thread_id=None, user_id=OPERADOR, texto="x"
    )
    assert authz.update_authorized(intruso, cfg) is False


# ── Roteiro ───────────────────────────────────────────────────────────────


def test_roteiro_simples(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("primeira\nsegunda\n", encoding="utf-8")
    assert ler_roteiro(arq) == [(0.0, "primeira"), (0.0, "segunda")]


def test_roteiro_ignora_comentario_e_linha_vazia(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("# bateria\n\nprimeira\n   \n# fim\n", encoding="utf-8")
    assert ler_roteiro(arq) == [(0.0, "primeira")]


def test_roteiro_com_espera(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("primeira\n@30 segunda\n@1.5 terceira\n", encoding="utf-8")
    assert ler_roteiro(arq) == [
        (0.0, "primeira"),
        (30.0, "segunda"),
        (1.5, "terceira"),
    ]


def test_espera_invalida_aponta_a_linha(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("ok\n@depois mensagem\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ler_roteiro(arq)
    assert ":2:" in str(exc.value)


def test_espera_sem_mensagem_e_erro(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("@30\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        ler_roteiro(arq)
    assert "sem mensagem" in str(exc.value)


def test_roteiro_vazio_e_erro(tmp_path: Path) -> None:
    arq = tmp_path / "r.txt"
    arq.write_text("# só comentário\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ler_roteiro(arq)


# ── Latência: o instrumento tem de ser confiável antes do número valer ───


def test_percentil_de_amostra_de_um() -> None:
    assert percentil([3.0], 50) == 3.0
    assert percentil([3.0], 95) == 3.0


def test_percentil_ordena_antes_de_escolher() -> None:
    """A entrada chega na ordem dos turnos, não ordenada."""
    valores = [9.0, 1.0, 5.0, 3.0, 7.0]
    assert percentil(valores, 50) == 5.0
    assert percentil(valores, 100) == 9.0


def test_percentil_nunca_estoura_o_indice() -> None:
    """p95 de 3 amostras não pode virar IndexError — vira o maior que existe."""
    assert percentil([1.0, 2.0, 3.0], 95) == 3.0


def test_percentil_de_amostra_vazia_recusa() -> None:
    """Devolver 0.0 aqui seria inventar latência. Recusar é o certo."""
    with pytest.raises(ValueError):
        percentil([], 50)


# ── A ferramenta não pode encostar no caminho de produção ─────────────────


def test_nada_do_bot_importa_o_arnes() -> None:
    """`infra/dev_inject.py` é folha: o bot não sabe que ele existe."""
    culpados = [
        str(f.relative_to(RAIZ))
        for f in RAIZ.joinpath("bot").rglob("*.py")
        if "dev_inject" in f.read_text(encoding="utf-8")
    ]
    assert not culpados, f"o caminho de produção importa o arnês: {culpados}"
