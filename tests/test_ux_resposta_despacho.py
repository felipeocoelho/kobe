"""Testes do pacote UX de resposta + despacho (Fase B/C, 2026-06-05).

Cobre os pedaços PUROS (sem rede/DB):
- `build_prompt(background_handoff=...)` injeta a nota de handoff PRIMEIRO.
- `ProgressReporter.acked` liga quando o agente chama `kobe-notify` no stream.
"""

from __future__ import annotations

import asyncio

from bot.claude_runner import build_prompt
from bot.progress import ProgressReporter
from bot.telegram_handler import _background_handoff_note, _now_utc_iso


# ── nota de handoff de background: helpers (regressão do NameError) ────────


def test_now_utc_iso_tem_offset():
    iso = _now_utc_iso()
    assert iso.endswith("+00:00")


def test_background_handoff_note_injeta_boundary_e_instrui_ack():
    boundary = "2026-06-08T12:00:00+00:00"
    nota = _background_handoff_note(boundary)
    # Abre dizendo que está em background; manda ackar na própria voz e reler
    # a janela de frescor com o boundary copiado literalmente.
    assert nota.startswith("[VOCÊ ESTÁ RODANDO EM BACKGROUND")
    assert "kobe-notify" in nota
    assert f"kobe-recall-since '{boundary}'" in nota


# ── build_prompt: nota de handoff de background ───────────────────────────


def test_build_prompt_sem_handoff_comeca_no_header():
    out = build_prompt(thread_id=None, history=[], new_message="oi")
    assert out.lstrip().startswith("[Telegram] tópico:")
    assert "BACKGROUND" not in out


def test_build_prompt_handoff_vem_primeiro():
    nota = "[VOCÊ ESTÁ EM BACKGROUND — leia isto primeiro]\nblá blá"
    out = build_prompt(
        thread_id=42,
        history=[{"role": "user", "content": "antes"}],
        new_message="faz a varredura",
        background_handoff=nota,
    )
    # A nota é a primeira coisa do prompt, antes do header do Telegram.
    assert out.startswith(nota)
    assert out.index(nota) < out.index("[Telegram] tópico:")
    # O resto do contexto continua presente.
    assert "[Mensagem nova do operador]" in out
    assert "faz a varredura" in out


# ── ProgressReporter.acked: detecção do ack via stream ────────────────────


class _FakeBot:
    """Bot mínimo: send/edit/delete viram no-op assíncrono."""

    class _Sent:
        message_id = 1

    async def send_message(self, *a, **k):
        return self._Sent()

    async def edit_message_text(self, *a, **k):
        return None

    async def delete_message(self, *a, **k):
        return None


def _assistant_event(tool_name: str, tool_input: dict) -> dict:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": tool_name, "input": tool_input}
            ]
        },
    }


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_acked_false_sem_notify():
    rep = ProgressReporter(chat_id=1, thread_id=None, bot=_FakeBot())
    _run(rep.on_event(_assistant_event("Read", {"file_path": "/x.py"})))
    assert rep.acked is False
    assert rep.tool_call_count == 1


def test_acked_true_com_kobe_notify():
    rep = ProgressReporter(chat_id=1, thread_id=None, bot=_FakeBot())
    _run(
        rep.on_event(
            _assistant_event(
                "Bash",
                {
                    "command": 'bot/bin/kobe-notify "Deixa eu olhar o Drive"',
                    "description": "avisa o operador",
                },
            )
        )
    )
    assert rep.acked is True


def test_acked_ignora_bash_comum():
    rep = ProgressReporter(chat_id=1, thread_id=None, bot=_FakeBot())
    _run(rep.on_event(_assistant_event("Bash", {"command": "ls -la"})))
    assert rep.acked is False
