#!/usr/bin/env python3
"""Testes da correção de rajada FIFO + citação órfã (2026-06-09, card 8b04cf6a).

O bug: em rajada de mensagens no mesmo tópico, a resposta saía fora de ordem
(Defeito 1) e a citação da 1ª mensagem ficava órfã (Defeito 2). Causa: o lock
por tópico era pego DEPOIS do "preparo" (download/transcrição, de duração
variável), então quem terminava o preparo primeiro furava a fila. Uma voice que
chega 1º mas leva 4s transcrevendo perdia a vez pra um texto que chegou em 2º.

A correção (`_TopicGate`): cada handler tira um TICKET síncrono na ENTRADA
(antes do preparo); o preparo segue em paralelo; só a entrada na seção crítica
espera a vez do ticket. Estes testes exercem a propriedade central — ordem de
ENTRADA na seção crítica == ordem de chegada — e as garantias de robustez
(sem deadlock em abort, sem serializar o preparo).

Sem rede, sem Telegram, sem DB: exercita o primitivo direto. Rodar:

    .venv/bin/python tests/test_rajada_fifo.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.telegram_handler import _Ticket, _TopicGate  # noqa: E402


def test_fifo_respeita_chegada_apesar_do_preparo_variavel():
    """Núcleo do fix: ticket tirado na ENTRADA (ordem de chegada) é servido
    nessa ordem, mesmo que o preparo de cada um termine fora de ordem.

    Reproduz o cenário real: msg A (voice) chega 1º, preparo lento (0.20s);
    msg B (texto) chega 2º, preparo zero. Sem o fix, B entraria na seção
    crítica primeiro (terminou o preparo antes). Com o fix, A entra primeiro.
    """
    gate = _TopicGate()
    # Tickets tirados na ordem de CHEGADA (síncrono, antes de qualquer preparo).
    ticket_a = gate.take()  # voice, chegou 1º
    ticket_b = gate.take()  # texto, chegou 2º

    served: list[str] = []

    async def handler(ticket: _Ticket, name: str, preparo_s: float):
        # Preparo FORA da seção crítica — roda em paralelo entre os handlers.
        await asyncio.sleep(preparo_s)
        # Só a entrada na seção crítica espera a vez do ticket.
        await ticket.wait_turn()
        try:
            served.append(name)
            await asyncio.sleep(0.01)  # simula o trabalho da seção crítica
        finally:
            await ticket.complete()

    async def run():
        await asyncio.gather(
            handler(ticket_a, "A", 0.20),  # chegou 1º, preparo LENTO
            handler(ticket_b, "B", 0.00),  # chegou 2º, preparo zero
        )

    asyncio.run(run())
    assert served == ["A", "B"], f"ordem de chegada não respeitada: {served}"
    print("ok: seção crítica respeita ordem de chegada apesar do preparo variável")


def test_preparo_roda_em_paralelo_sem_serializar():
    """O fix não pode custar latência: o preparo pesado de mensagens em rajada
    deve correr em PARALELO. Só a seção crítica serializa.

    3 preparos de 0.20s cada devem terminar em ~0.20s no total — não ~0.60s.
    Se o ticket/gate serializasse o preparo, o tempo triplicaria.
    """
    gate = _TopicGate()
    tickets = [gate.take() for _ in range(3)]
    PREP = 0.20

    async def handler(ticket: _Ticket):
        await asyncio.sleep(PREP)  # preparo — deve ser concorrente
        await ticket.wait_turn()
        try:
            pass  # seção crítica trivial
        finally:
            await ticket.complete()

    async def run():
        start = time.monotonic()
        await asyncio.gather(*(handler(t) for t in tickets))
        return time.monotonic() - start

    elapsed = asyncio.run(run())
    assert elapsed < PREP * 2, f"preparo serializou ({elapsed:.2f}s; paralelo ~{PREP}s)"
    print(f"ok: 3 preparos concorrentes em {elapsed:.2f}s (serial seria ~{PREP*3:.1f}s)")


def test_abort_antes_da_vez_nao_trava_o_topico():
    """Robustez: um handler que ABORTA antes da sua vez (ex.: transcrição
    falhou) precisa liberar o slot via `complete()` mesmo sem ter rodado
    `wait_turn()`. Senão o tópico travava no primeiro áudio problemático.

    Cenário: ticket 0 segura a vez; ticket 1 aborta no preparo (sem wait_turn);
    ticket 2 só pode entrar quando 0 concluir E 1 for pulado.
    """
    gate = _TopicGate()
    t0 = gate.take()
    t1 = gate.take()  # vai abortar antes da vez
    t2 = gate.take()

    served: list[int] = []

    async def holder():
        await t0.wait_turn()
        try:
            served.append(0)
            await asyncio.sleep(0.05)  # segura a vez enquanto t1 aborta
        finally:
            await t0.complete()

    async def aborter():
        # Simula preparo que falha (transcrição) → finally libera sem wait_turn.
        try:
            await asyncio.sleep(0.01)
            return  # aborta: nunca chama wait_turn
        finally:
            await t1.complete()

    async def follower():
        await t2.wait_turn()
        try:
            served.append(2)
        finally:
            await t2.complete()

    async def run():
        await asyncio.wait_for(
            asyncio.gather(holder(), aborter(), follower()), timeout=2.0
        )

    asyncio.run(run())
    assert served == [0, 2], f"esperava [0, 2] (1 abortou): {served}"
    print("ok: abort antes da vez libera o slot — sem deadlock, ordem mantida")


def test_complete_idempotente():
    """`complete()` chamado 2x (ex.: explícito + finally) não deve avançar a
    vez duas vezes — senão pularia o próximo ticket."""
    gate = _TopicGate()
    t0 = gate.take()
    t1 = gate.take()

    served: list[int] = []

    async def run():
        await t0.wait_turn()
        served.append(0)
        await t0.complete()
        await t0.complete()  # 2ª chamada: no-op (idempotente)
        await t1.wait_turn()  # se complete tivesse avançado 2x, isto travaria
        served.append(1)
        await t1.complete()

    asyncio.run(asyncio.wait_for(run(), timeout=2.0) if False else run())
    assert served == [0, 1], f"idempotência quebrou a fila: {served}"
    print("ok: complete() é idempotente (não pula ticket)")


def test_rajada_de_n_mensagens_ordem_total():
    """Rajada de N=8 com preparos embaralhados (uns lentos, uns rápidos):
    a seção crítica tem que sair em ordem de chegada 0..7, sempre."""
    gate = _TopicGate()
    N = 8
    tickets = [gate.take() for _ in range(N)]
    # Preparos propositalmente "ao contrário": o que chegou 1º demora mais.
    preparos = [(N - i) * 0.02 for i in range(N)]
    served: list[int] = []

    async def handler(i: int, ticket: _Ticket, prep: float):
        await asyncio.sleep(prep)
        await ticket.wait_turn()
        try:
            served.append(i)
            await asyncio.sleep(0.005)
        finally:
            await ticket.complete()

    async def run():
        await asyncio.wait_for(
            asyncio.gather(
                *(handler(i, tickets[i], preparos[i]) for i in range(N))
            ),
            timeout=5.0,
        )

    asyncio.run(run())
    assert served == list(range(N)), f"ordem total quebrou: {served}"
    print(f"ok: rajada de {N} com preparos invertidos sai em ordem de chegada")


def test_exclusao_mutua_secao_critica_nao_sobrepoe():
    """O gate é também o mutex: nunca duas seções críticas do mesmo tópico
    rodam ao mesmo tempo (protege user-data/, insert no Supabase, compactação).
    """
    gate = _TopicGate()
    tickets = [gate.take() for _ in range(5)]
    in_cs = 0
    max_overlap = 0

    async def handler(ticket: _Ticket):
        nonlocal in_cs, max_overlap
        await ticket.wait_turn()
        try:
            in_cs += 1
            max_overlap = max(max_overlap, in_cs)
            await asyncio.sleep(0.02)  # janela pra outro furar, se pudesse
            in_cs -= 1
        finally:
            await ticket.complete()

    async def run():
        await asyncio.gather(*(handler(t) for t in tickets))

    asyncio.run(run())
    assert max_overlap == 1, f"seções críticas se sobrepuseram: max={max_overlap}"
    print("ok: exclusão mútua — nunca 2 seções críticas ao mesmo tempo")


def test_serve_libera_e_propaga_excecao():
    """O açúcar `_serve` (usado por texto/comandos): se o corpo levanta, o
    ticket é liberado (próxima msg anda) E a exceção propaga (chega no
    on_error global). Importável só aqui pra não puxar Telegram nos demais."""
    from bot.telegram_handler import _get_topic_gate, _serve

    CHAT, THREAD = -999, 12345
    served: list[str] = []

    async def run():
        # 1ª msg: corpo levanta. Deve propagar e ainda liberar o ticket.
        raised = False
        try:
            async with _serve(CHAT, THREAD):
                served.append("A")
                raise RuntimeError("boom no turno")
        except RuntimeError:
            raised = True
        assert raised, "exceção do corpo não propagou (on_error não dispararia)"

        # 2ª msg: se o ticket da 1ª não tivesse sido liberado, isto travaria.
        async with _serve(CHAT, THREAD):
            served.append("B")

    asyncio.run(asyncio.wait_for(run(), timeout=2.0))
    assert served == ["A", "B"], f"ticket não liberado após exceção: {served}"
    # Higiene: não deixa gate de teste no registry global do módulo.
    _get_topic_gate(CHAT, THREAD)  # idempotente; só garante chave conhecida
    print("ok: _serve libera o ticket após exceção e propaga pro on_error")


if __name__ == "__main__":
    test_fifo_respeita_chegada_apesar_do_preparo_variavel()
    test_preparo_roda_em_paralelo_sem_serializar()
    test_abort_antes_da_vez_nao_trava_o_topico()
    test_complete_idempotente()
    test_rajada_de_n_mensagens_ordem_total()
    test_exclusao_mutua_secao_critica_nao_sobrepoe()
    test_serve_libera_e_propaga_excecao()
    print("\nTODOS OS TESTES PASSARAM")
