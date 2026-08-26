#!/usr/bin/env python3
"""Trava dos quatro comandos de memória: /nova, /contexto, /salvar, /retomar.

**Por que este arquivo existe.** Os quatro sempre foram do Kobe, nunca do Chat
Manager — mas o Chat Manager tinha enxertado ramos condicionais em três deles
(`/nova` fechava a *conversation* junto, `/contexto` imprimia meta dela, e
`/retomar` sugeria `/conversa` quando não achava artefato). Na aposentadoria do
Chat Manager (2026-08-25) esses ramos saíram e os comandos voltaram ao
comportamento pré-Chat-Manager.

Eles não tinham teste nenhum — a remoção poderia ter mudado o texto, a ordem ou
o caminho de erro sem nada acusar. Estes testes travam o comportamento de volta:
os quatro **respondem**, e não sobra nenhuma menção a conversation/conversa nas
respostas.

Rodar: .venv/bin/python -m pytest tests/test_comandos_memoria.py -q
"""
from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import telegram_handler as th  # noqa: E402
from bot.config import Config  # noqa: E402


# ── Dublês ────────────────────────────────────────────────────────────────


class FakeMessage:
    """Mensagem do Telegram que só guarda o que foi respondido."""

    def __init__(self, chat_id: int = -100, thread_id: Optional[int] = None):
        self.chat_id = chat_id
        self.message_thread_id = thread_id
        self.respostas: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.respostas.append(text)


def _update(message: FakeMessage):
    return SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(id=message.chat_id),
    )


def _valor_default(f):
    """Default do tipo certo pra cada campo declarado em `Config`.

    Mesmo truque de `test_resume.py`: a Config falsa é DERIVADA da dataclass
    real, então campo novo (ou removido) na produção não passa despercebido.
    """
    t = str(f.type)
    if "bool" in t:
        return False
    if "int" in t:
        return 0
    if "Path" in t:
        return Path("/tmp/kobe-test")
    if "Optional" in t:
        return None
    return ""


def _config(**over):
    valores = {f.name: _valor_default(f) for f in dataclasses.fields(Config)}
    valores.update(kobe_home=Path("/tmp/kobe-test"), allowed_user_ids=[])
    valores.update(over)
    return SimpleNamespace(**valores)


def _context(*, args: Optional[list[str]] = None, db=None):
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"config": _config(), "db": db or object(), "claude": object()},
            bot=object(),
        ),
        args=args,
    )


def _rodar(handler, message, **ctx_kwargs) -> list[str]:
    asyncio.run(handler(_update(message), _context(**ctx_kwargs)))
    return message.respostas


def _patch(monkey: dict):
    originais = {n: getattr(th, n) for n in monkey}
    for n, fn in monkey.items():
        setattr(th, n, fn)
    return originais


def _restore(originais: dict):
    for n, fn in originais.items():
        setattr(th, n, fn)


BASE = {
    "ensure_topic": lambda db, tid, chat_id=None: "top1",
    "get_topic_slug": lambda db, c, t: "dev-kobe",
    "_update_authorized": lambda update, config: True,
}


# ── /nova ─────────────────────────────────────────────────────────────────


def test_nova_arquiva_a_sessao_e_responde() -> None:
    """Pré-Chat-Manager: arquiva a sessão e ponto. Sem fechar 'conversa'."""
    orig = _patch({
        **BASE,
        "get_active_session": lambda db, tid: None,
        "archive_active_session": lambda db, tid: "sess-antiga",
    })
    try:
        r = _rodar(th.on_command_nova, FakeMessage())
    finally:
        _restore(orig)
    assert len(r) == 1
    assert "arquivada" in r[0].lower()
    assert "conversa" not in r[0].lower(), "texto ainda fala de conversation"


def test_nova_sem_sessao_ativa_avisa_que_ja_esta_zerado() -> None:
    orig = _patch({
        **BASE,
        "get_active_session": lambda db, tid: None,
        "archive_active_session": lambda db, tid: None,
    })
    try:
        r = _rodar(th.on_command_nova, FakeMessage())
    finally:
        _restore(orig)
    assert "zerado" in r[0].lower()


# ── /contexto ─────────────────────────────────────────────────────────────


def test_contexto_mostra_a_sessao_e_ponto() -> None:
    orig = _patch({
        **BASE,
        "get_active_session": lambda db, tid: {
            "id": "sess1", "started_at": "2026-08-25T10:00:00+00:00",
        },
        "count_messages": lambda db, sid: 7,
        "get_recent_messages": lambda db, sid, limit=3: [
            {"role": "user", "content": "oi"},
        ],
    })
    try:
        r = _rodar(th.on_command_contexto, FakeMessage())
    finally:
        _restore(orig)
    texto = r[0]
    assert "Sessão ativa desde" in texto
    assert "7 mensagem(ns)" in texto
    assert "user: oi" in texto
    # O bloco de meta da conversation saiu — nem o ativo, nem o "sem conversa".
    assert "Conversa:" not in texto
    assert "Sem conversa ativa" not in texto


def test_contexto_sem_sessao_ativa_avisa() -> None:
    orig = _patch({**BASE, "get_active_session": lambda db, tid: None})
    try:
        r = _rodar(th.on_command_contexto, FakeMessage())
    finally:
        _restore(orig)
    assert "Nenhuma sessão ativa" in r[0]


# ── /salvar (não foi tocado — trava contra regressão colateral) ────────────


def test_salvar_consolida_a_sessao_em_artefato() -> None:
    orig = _patch({
        **BASE,
        "get_active_session": lambda db, tid: {"id": "sess1"},
        "get_recent_messages": lambda db, sid, limit=500: [
            {"role": "user", "content": "conteúdo"},
        ],
        "save_artifact_from_messages": lambda db, **kw: "art-1",
    })
    try:
        r = _rodar(th.on_command_salvar, FakeMessage(), args=["meu", "titulo"])
    finally:
        _restore(orig)
    assert "Salvo" in r[0] and "meu titulo" in r[0]


def test_salvar_sem_titulo_pede_o_titulo() -> None:
    orig = _patch(dict(BASE))
    try:
        r = _rodar(th.on_command_salvar, FakeMessage(), args=[])
    finally:
        _restore(orig)
    assert "/salvar <título do artefato>" in r[0]


# ── /retomar ──────────────────────────────────────────────────────────────


def test_retomar_sem_termo_pede_o_termo_e_nao_sugere_conversa() -> None:
    """A dica de `/conversa`/`/conversas` saiu junto com o Chat Manager."""
    orig = _patch(dict(BASE))
    try:
        r = _rodar(th.on_command_retomar, FakeMessage(), args=[])
    finally:
        _restore(orig)
    assert "/retomar <palavra-chave>" in r[0]
    assert "/conversa" not in r[0]


def test_retomar_sem_resultado_nao_sugere_conversa() -> None:
    orig = _patch({**BASE, "search_artifacts": lambda db, q: []})
    try:
        r = _rodar(th.on_command_retomar, FakeMessage(), args=["migração"])
    finally:
        _restore(orig)
    assert "Não achei nenhum" in r[0]
    assert "/conversa" not in r[0]
    assert "/conversas-global" not in r[0]


def test_retomar_acha_artefato_e_lista() -> None:
    orig = _patch({
        **BASE,
        "search_artifacts": lambda db, q: [
            {"title": "Plano X", "created_at": "2026-08-01", "content": "corpo"},
        ],
    })
    try:
        r = _rodar(th.on_command_retomar, FakeMessage(), args=["plano"])
    finally:
        _restore(orig)
    assert "Plano X" in r[0]


# ── Conformidade ──────────────────────────────────────────────────────────


def test_nenhum_dos_quatro_comandos_menciona_conversation() -> None:
    """Rede contra um ramo de conversation voltar por copy-paste."""
    fonte = Path(th.__file__).read_text(encoding="utf-8")
    for nome in (
        "on_command_nova",
        "on_command_contexto",
        "on_command_salvar",
        "on_command_retomar",
    ):
        i = fonte.index(f"async def {nome}(")
        j = fonte.index("\nasync def ", i + 1)
        corpo = fonte[i:j]
        assert "conversation" not in corpo.lower(), (
            f"{nome} voltou a mencionar conversation — o Chat Manager foi "
            "aposentado em 2026-08-25"
        )


if __name__ == "__main__":  # pragma: no cover
    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
