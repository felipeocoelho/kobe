#!/usr/bin/env python3
"""A trava de canal (Sessão #1, P3) — `TELEGRAM_ALLOWED_CHAT_IDS`.

Duas propriedades, e as duas são de segurança:

1. **Vazia = comportamento de hoje.** A produção não ganha variável nova e não
   muda um bit. Sem isto, a Sessão #1 deixaria de ser aditiva.
2. **Preenchida = falha fechada.** Só os chats listados são atendidos. Tudo o
   mais é ignorado em silêncio — não "recusado educadamente", porque responder
   confirma que o bot existe e está ali.

O teste que mais importa aqui é o de **conformidade**: ele varre `bot/` atrás de
uma quinta cópia da verificação de autorização. A regra estava copiada em quatro
módulos antes desta sessão, e quatro cópias de uma trava de segurança são quatro
chances de ela falhar ABERTA. Se alguém reintroduzir a cópia, o teste quebra —
e quebra por grep, que é a única forma de pegar isso sem exercitar cada handler.

Rodar: .venv/bin/python -m pytest tests/test_chat_whitelist.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import Chat, Message, Update, User

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import authz
from bot.config import ConfigError, _parse_chat_ids

RAIZ = Path(__file__).resolve().parent.parent

# O supergrupo de desenvolvimento. Vem dos dados verificados contra a API do
# Telegram em 25/08/2026; é o único chat que o ambiente de dev atende.
CHAT_DEV = -1004448020751
OPERADOR = 111222333


def _update(chat_id: int = CHAT_DEV, user_id: int = OPERADOR) -> Update:
    user = User(id=user_id, first_name="op", is_bot=False)
    chat = Chat(id=chat_id, type="supergroup")
    msg = Message(message_id=1, date=None, chat=chat, from_user=user, text="oi")
    return Update(update_id=1, message=msg)


def _config(chats=(), users=(OPERADOR,)) -> SimpleNamespace:
    return SimpleNamespace(
        allowed_user_ids=frozenset(users),
        telegram_allowed_chat_ids=frozenset(chats),
    )


# ── Propriedade 1: vazia = como hoje ──────────────────────────────────────


@pytest.mark.parametrize("chat_id", [CHAT_DEV, -100999, 42, -1])
def test_sem_whitelist_qualquer_chat_passa(chat_id: int) -> None:
    """Produção de hoje: a autorização é só por usuário, de qualquer chat."""
    assert authz.update_authorized(_update(chat_id=chat_id), _config()) is True


def test_sem_whitelist_usuario_nao_autorizado_continua_barrado() -> None:
    """A dimensão antiga não pode ter sido enfraquecida pela nova."""
    assert authz.update_authorized(_update(user_id=999), _config()) is False


@pytest.mark.parametrize("cru", [None, "", "   ", ","])
def test_variavel_ausente_ou_vazia_vira_conjunto_vazio(cru) -> None:
    assert _parse_chat_ids(cru) == frozenset()


# ── Propriedade 2: preenchida = falha fechada ─────────────────────────────


def test_com_whitelist_o_chat_certo_passa() -> None:
    cfg = _config(chats=[CHAT_DEV])
    assert authz.update_authorized(_update(chat_id=CHAT_DEV), cfg) is True


@pytest.mark.parametrize("intruso", [-100999, 42, -1004448020750, 1004448020751])
def test_com_whitelist_qualquer_outro_chat_e_barrado(intruso: int) -> None:
    """Inclui vizinhos de um dígito e o mesmo id sem o sinal — erro plausível."""
    cfg = _config(chats=[CHAT_DEV])
    assert authz.update_authorized(_update(chat_id=intruso), cfg) is False


def test_usuario_certo_no_chat_errado_nao_passa() -> None:
    """As duas condições são conjuntivas: ser o operador não basta."""
    cfg = _config(chats=[CHAT_DEV])
    assert authz.update_authorized(_update(chat_id=-100999), cfg) is False


def test_update_sem_chat_e_recusado_quando_ha_whitelist() -> None:
    """Se não dá pra saber de onde veio, não dá pra dizer que veio de lugar bom."""
    vazio = Update(update_id=1)
    assert authz.chat_authorized(vazio, frozenset([CHAT_DEV])) is False
    # ...mas sem whitelist, nada muda pra ele.
    assert authz.chat_authorized(vazio, frozenset()) is True


def test_id_nao_numerico_derruba_o_start() -> None:
    """`-100abc` é quase certamente um id digitado errado. Aceitar calado
    transformaria a trava de canal numa trava de nada."""
    with pytest.raises(ConfigError) as exc:
        _parse_chat_ids("-1004448020751,-100abc")
    assert "TELEGRAM_ALLOWED_CHAT_IDS" in str(exc.value)


def test_lista_com_espacos_e_multiplos_ids() -> None:
    assert _parse_chat_ids(" -1004448020751 , 42 ,") == frozenset([-1004448020751, 42])


# ── A verificação só-de-canal, pros handlers sem autorização ──────────────


def test_chat_allowed_for_e_inerte_sem_whitelist() -> None:
    assert authz.chat_allowed_for(_update(chat_id=-100999), _config()) is True


def test_chat_allowed_for_barra_com_whitelist() -> None:
    cfg = _config(chats=[CHAT_DEV])
    assert authz.chat_allowed_for(_update(chat_id=-100999), cfg) is False
    assert authz.chat_allowed_for(_update(chat_id=CHAT_DEV), cfg) is True


def test_chat_allowed_for_com_config_ausente_nao_quebra() -> None:
    """Handler que roda antes do bot_data estar montado não pode explodir."""
    assert authz.chat_allowed_for(_update(), None) is True


# ── Conformidade: ninguém pode criar a quinta cópia ───────────────────────

MODULOS_DE_HANDLER = [
    "bot/telegram_handler.py",
    "bot/alertas/handlers.py",
]


def test_nenhum_handler_verifica_autorizacao_por_conta_propria() -> None:
    """A regra mora em bot/authz.py. Cópia local volta a ser buraco de canal."""
    padrao = re.compile(r"user\.id\s+in\s+", re.MULTILINE)
    culpados = []
    for arquivo in RAIZ.joinpath("bot").rglob("*.py"):
        if arquivo.name == "authz.py":
            continue
        if padrao.search(arquivo.read_text(encoding="utf-8")):
            culpados.append(str(arquivo.relative_to(RAIZ)))
    assert not culpados, (
        "verificação de autorização copiada fora de bot/authz.py: "
        f"{culpados}. Use authz.update_authorized(update, config)."
    )


@pytest.mark.parametrize("modulo", MODULOS_DE_HANDLER)
def test_todo_handler_gateado_usa_a_verificacao_completa(modulo: str) -> None:
    """Nenhum ponto de chamada pode ter ficado pra trás na troca."""
    texto = RAIZ.joinpath(modulo).read_text(encoding="utf-8")
    assert "_user_authorized(update, config.allowed_user_ids)" not in texto
    assert "_update_authorized(update, config)" in texto
    assert "authz.update_authorized" in texto


def test_a_trava_de_canal_alcanca_os_handlers_sem_autorizacao() -> None:
    """Os 8 pontos que nunca tiveram verificação nenhuma: fórum + Apolo."""
    forum = RAIZ.joinpath("bot/telegram_handler.py").read_text(encoding="utf-8")
    apolo = RAIZ.joinpath("bot/apolo_handlers.py").read_text(encoding="utf-8")
    assert forum.count("authz.chat_allowed_for") == 4, "4 handlers de fórum"
    assert apolo.count("authz.chat_allowed_for") == 4, "4 comandos do Apolo"


def test_a_contagem_de_pontos_gateados_nao_encolheu() -> None:
    """Rede contra remoção silenciosa de trava numa refatoração futura.

    O número desce SÓ quando um handler inteiro é aposentado, e o motivo fica
    escrito aqui — encolher sem justificativa é exatamente o que este teste
    existe pra pegar.

    Histórico: **23** quando a trava de canal foi centralizada em `bot/authz.py`
    (2026-08-25) · **18** com a aposentadoria do Chat Manager (2026-08-25), que
    levou `bot/chat_manager_commands.py` e seus 5 comandos · **14** com a
    aposentadoria do Sistema de Missões v0.13 (2026-08-25), que levou
    `bot/mission_control/handlers.py` e seus 4 comandos. As SALAS de missão não
    têm handler de comando (são abertas por linguagem natural), então nunca
    apareceram nesta lista — e nada nelas foi tocado.
    """
    total = sum(
        RAIZ.joinpath(m).read_text(encoding="utf-8").count(
            "_update_authorized(update, config)"
        )
        for m in MODULOS_DE_HANDLER
    )
    assert total == 14, f"esperados 14 pontos gateados, achei {total}"
