#!/usr/bin/env python3
"""Testes da garantia de turno — bot/turn_guarantee.py (camada 2 do bug 2).

A camada 2 é a que IMPORTA: ela não sabe qual é o banco. Mesmo que a camada de
conexão falhe (ou que um dia o banco seja outro), o contrato aqui é único:

    o turno NUNCA morre calado, e a mensagem NÃO se perde.

O que estas travas protegem:
- turno morre → a mensagem vai pro disco E o operador é avisado;
- retentativa automática SÓ quando é seguro (erro de transporte + antes do
  ponto de não-retorno) — nunca quando o turno já pode ter gravado/respondido;
- retentativa que dá certo → operador nem percebe, e a pendência some;
- cancelamento (shutdown) NÃO é tratado como falha de turno (lição do bug do
  auto-cancelamento do assembler: engolir CancelledError trava o encerramento);
- aviso é à prova de HTML na mensagem do operador (senão a garantia falharia
  justamente no ato de garantir);
- flag off = comportamento legado (a exceção sobe como antes).

Rodar: .venv/bin/python -m pytest tests/test_turn_guarantee.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import turn_guarantee as tg


def _home() -> Path:
    return Path(tempfile.mkdtemp(prefix="kobe-tg-"))


def _run_guarded(home, run, *, text="preciso disso aqui", avisos=None):
    avisos = avisos if avisos is not None else []

    async def _notify(t):
        avisos.append(t)

    asyncio.run(
        tg.run_guarded(
            run=run,
            kobe_home=home,
            chat_id=-100,
            thread_id=475,
            message_id=7,
            text=text,
            audio=False,
            notify=_notify,
        )
    )
    return avisos


def _env(**over):
    base = {"TURN_GUARANTEE_ENABLED": "true"}
    base.update(over)
    return mock.patch.dict(os.environ, base, clear=False)


# ── Caminho feliz ─────────────────────────────────────────────────────────


def test_turno_ok_nao_deixa_rastro() -> None:
    home = _home()
    chamadas = []

    async def _run(progress):
        chamadas.append(progress)

    with _env():
        avisos = _run_guarded(home, _run)
    assert len(chamadas) == 1
    assert avisos == [], "turno normal não avisa nada"
    assert tg.list_pending(home) == [], "nem deixa pendência"


# ── O incidente real: morre ANTES do ponto seguro, retenta, dá certo ──────


def test_repro_morre_antes_do_ponto_seguro_e_retentativa_salva() -> None:
    """O caso das 3 mortes de produção: falha na leitura do histórico."""
    home = _home()
    tentativas = []

    async def _run(progress):
        tentativas.append(1)
        if len(tentativas) == 1:
            # Morre ANTES de marcar committed — igual ao incidente real.
            raise httpx.RemoteProtocolError("Server disconnected")
        progress["committed"] = True

    with _env(), mock.patch.object(tg, "RETRY_DELAY_SECONDS", 0):
        avisos = _run_guarded(home, _run)

    assert len(tentativas) == 2, "tentou de novo"
    assert avisos == [], "deu certo na 2ª — o operador nem precisa saber"
    assert tg.list_pending(home) == [], "pendência resolvida some do disco"


def test_retentativa_tambem_falha_avisa_e_guarda() -> None:
    home = _home()

    async def _run(progress):
        raise httpx.ReadError("banco fora de verdade")

    with _env(), mock.patch.object(tg, "RETRY_DELAY_SECONDS", 0):
        avisos = _run_guarded(home, _run, text="manda o relatório de ontem")

    assert len(avisos) == 1, "o turno NÃO morre calado"
    assert "não se perdeu" in avisos[0]
    assert "Tentei de novo" in avisos[0]
    assert "manda o relatório de ontem" in avisos[0], "o operador reconhece o quê"

    pend = tg.list_pending(home)
    assert len(pend) == 1
    assert pend[0]["text"] == "manda o relatório de ontem"
    assert pend[0]["chat_id"] == -100 and pend[0]["thread_id"] == 475


# ── Precisão da retentativa (o que protege contra resposta dupla) ─────────


def test_nao_retenta_depois_do_ponto_seguro() -> None:
    """Turno já gravou a mensagem: re-executar duplicaria e poderia responder 2×."""
    home = _home()
    tentativas = []

    async def _run(progress):
        tentativas.append(1)
        progress["committed"] = True  # cruzou o ponto de não-retorno
        raise httpx.ReadError("morreu depois de gravar")

    with _env(), mock.patch.object(tg, "RETRY_DELAY_SECONDS", 0):
        avisos = _run_guarded(home, _run)

    assert len(tentativas) == 1, "NÃO pode ter re-executado"
    assert len(avisos) == 1 and "Tentei de novo" not in avisos[0]
    assert len(tg.list_pending(home)) == 1, "mas a mensagem continua guardada"


def test_nao_retenta_erro_que_nao_e_de_transporte() -> None:
    """Bug de verdade (KeyError, ValueError): repetir só mascararia."""
    home = _home()
    tentativas = []

    async def _run(progress):
        tentativas.append(1)
        raise KeyError("curated_core")

    with _env(), mock.patch.object(tg, "RETRY_DELAY_SECONDS", 0):
        avisos = _run_guarded(home, _run)

    assert len(tentativas) == 1
    assert len(avisos) == 1, "mas o operador é avisado do mesmo jeito"
    assert len(tg.list_pending(home)) == 1


def test_cancelamento_propaga_e_nao_vira_pendencia() -> None:
    """Shutdown não é falha de turno. Engolir CancelledError travaria o
    encerramento do bot — foi assim que o auto-cancelamento do assembler
    trocou um bug por outro."""
    home = _home()

    async def _run(progress):
        raise asyncio.CancelledError()

    async def _notify(t):  # pragma: no cover — não deve ser chamado
        raise AssertionError("cancelamento não avisa")

    async def _cenario():
        await tg.run_guarded(
            run=_run, kobe_home=home, chat_id=-100, thread_id=None,
            message_id=1, text="x", audio=False, notify=_notify,
        )

    with _env():
        try:
            asyncio.run(_cenario())
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("CancelledError deve PROPAGAR")
    assert tg.list_pending(home) == []


# ── Robustez do aviso e da fila ───────────────────────────────────────────


def test_aviso_escapa_html_da_mensagem_do_operador() -> None:
    """Um `<` na mensagem não pode derrubar o próprio aviso."""
    aviso = tg.failure_notice("rode <script>alert(1)</script> & veja", tentou_de_novo=False)
    assert "&lt;script&gt;" in aviso and "&amp;" in aviso
    assert "<script>" not in aviso


def test_falha_de_disco_ainda_avisa() -> None:
    """Se nem gravar dá, o aviso continua saindo — a última linha de defesa."""
    home = _home()

    async def _run(progress):
        raise KeyError("boom")

    with _env(), mock.patch.object(tg, "queue_pending", return_value=None):
        avisos = _run_guarded(home, _run)
    assert len(avisos) == 1


def test_falha_do_proprio_aviso_nao_propaga() -> None:
    """Telegram fora no pior momento: loga e segue, não explode a task."""
    home = _home()

    async def _run(progress):
        raise KeyError("boom")

    async def _notify(t):
        raise RuntimeError("Telegram fora")

    async def _cenario():
        await tg.run_guarded(
            run=_run, kobe_home=home, chat_id=-100, thread_id=None,
            message_id=1, text="x", audio=False, notify=_notify,
        )

    with _env():
        asyncio.run(_cenario())  # não levanta
    assert len(tg.list_pending(home)) == 1, "e a mensagem continua guardada"


def test_flag_off_e_comportamento_legado() -> None:
    """Rollback trivial: a exceção volta a subir, como antes."""
    home = _home()

    async def _run(progress):
        raise httpx.ReadError("x")

    with _env(TURN_GUARANTEE_ENABLED="false"):
        try:
            _run_guarded(home, _run)
        except httpx.ReadError:
            pass
        else:
            raise AssertionError("com a flag off a exceção deve subir")
    assert tg.list_pending(home) == [], "nem grava pendência"


def test_poda_respeita_o_teto() -> None:
    home = _home()
    for i in range(12):
        tg.queue_pending(
            home, chat_id=-100, thread_id=1, message_id=i,
            text=f"msg {i}", audio=False, erro="x",
        )
    assert len(tg.list_pending(home)) == 12
    removidos = tg.prune_pending(home, keep=5)
    assert removidos == 7
    restantes = tg.list_pending(home)
    assert len(restantes) == 5
    assert [r["text"] for r in restantes] == [f"msg {i}" for i in range(7, 12)], \
        "a poda tira os MAIS VELHOS"


# ── Relatório de arranque ─────────────────────────────────────────────────


def test_relatorio_de_arranque_agrupa_por_topico_e_nao_reprocessa() -> None:
    home = _home()
    tg.queue_pending(home, chat_id=-100, thread_id=475, message_id=1,
                     text="primeira", audio=False, erro="x")
    tg.queue_pending(home, chat_id=-100, thread_id=475, message_id=2,
                     text="segunda", audio=False, erro="x")
    tg.queue_pending(home, chat_id=-100, thread_id=999, message_id=3,
                     text="outro tópico", audio=False, erro="x")

    rel = tg.render_startup_report(home)
    assert len(rel) == 2, "um aviso por tópico"
    por_thread = {t: txt for _, t, txt in rel}
    assert "2 mensagem(ns)" in por_thread[475]
    assert "primeira" in por_thread[475] and "segunda" in por_thread[475]
    assert "outro tópico" in por_thread[999]
    # Reportar NÃO reprocessa — só mostra. E não apaga sozinho (quem apaga é o
    # main.py, depois de conseguir enviar).
    assert len(tg.list_pending(home)) == 3
    assert tg.clear_pending(home) == 3
    assert tg.list_pending(home) == []


def test_pendencia_e_json_legivel_e_atomico() -> None:
    """Outro processo (ou eu, depurando) tem que conseguir ler isso."""
    home = _home()
    p = tg.queue_pending(home, chat_id=-100, thread_id=475, message_id=9,
                         text="olha isso", audio=True, erro="httpx.ReadError: x")
    data = json.loads(Path(p).read_text(encoding="utf-8"))
    assert data["text"] == "olha isso" and data["audio_transcribed"] is True
    assert data["erro"].startswith("httpx.ReadError")
    assert list(tg.pending_dir(home).glob("*.tmp")) == [], "sem .tmp órfão"


# ── Fiação: o caminho do assembler, onde as 3 mortes aconteceram ─────────


def _fake_message(chat_id=-100, thread_id=475, message_id=7):
    msg = mock.MagicMock()
    msg.chat_id = chat_id
    msg.message_thread_id = thread_id
    msg.message_id = message_id
    msg.reply_text = mock.AsyncMock()
    return msg


def _handler_config(home):
    cfg = mock.MagicMock()
    cfg.kobe_home = home
    return cfg


def test_flush_do_assembler_nao_engole_mais() -> None:
    """A trava-mãe desta correção.

    Antes: `assembler.py` capturava a exceção do flush e só logava — a rede
    global (`on_error`) nem via, porque o turno roda numa task do montador. As 3
    mortes silenciosas de produção passaram exatamente por aqui.
    Agora: o callback é embrulhado pela garantia, então o operador é avisado e a
    mensagem vai pro disco.
    """
    from bot import telegram_handler as th

    home = _home()

    async def _cenario():
        cb = th._make_flush_cb(_handler_config(home), mock.MagicMock(),
                               mock.MagicMock(), [])
        msg = _fake_message()
        with mock.patch.object(
            th, "_handle_user_text",
            new=mock.AsyncMock(side_effect=httpx.RemoteProtocolError("Server disconnected")),
        ):
            # Não levanta: a garantia trata. Antes, isto sumia no log.
            await cb("me manda o resumo", False, msg)
        return msg

    with _env(), mock.patch.object(tg, "RETRY_DELAY_SECONDS", 0):
        msg = asyncio.run(_cenario())

    assert msg.reply_text.await_count == 1, "o operador FOI avisado"
    assert "não se perdeu" in msg.reply_text.await_args.args[0]
    pend = tg.list_pending(home)
    assert len(pend) == 1 and pend[0]["text"] == "me manda o resumo"


def test_flush_do_assembler_retentativa_transparente() -> None:
    """Falha de transporte que passa na 2ª: o operador não vê nada."""
    from bot import telegram_handler as th

    home = _home()
    chamadas = []

    async def _flaky(**kwargs):
        chamadas.append(kwargs)
        if len(chamadas) == 1:
            raise httpx.RemoteProtocolError("Server disconnected")

    async def _cenario():
        cb = th._make_flush_cb(_handler_config(home), mock.MagicMock(),
                               mock.MagicMock(), [])
        msg = _fake_message()
        with mock.patch.object(th, "_handle_user_text", new=_flaky):
            await cb("oi", False, msg)
        return msg

    with _env(), mock.patch.object(tg, "RETRY_DELAY_SECONDS", 0):
        msg = asyncio.run(_cenario())

    assert len(chamadas) == 2, "tentou de novo"
    assert msg.reply_text.await_count == 0, "silêncio: deu certo, nada a avisar"
    assert tg.list_pending(home) == []
    # E o turno recebeu o dict de progresso (o que torna a retentativa precisa).
    assert "turn_progress" in chamadas[0]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
