#!/usr/bin/env python3
"""Entrega mensagens sintéticas ao bot de DESENVOLVIMENTO, sem Telegram no meio.

POR QUE ISTO EXISTE
-------------------
O operador declarou que não vai testar o ambiente novo digitando. Sem esta peça,
a bateria de aceite dependeria dele — e o requisito não fecharia. Aqui, um
roteiro em arquivo roda a bateria inteira sozinho, e ele apenas **assiste** o
grupo de dev no Telegram enquanto acontece.

O que a ferramenta cobre e o que não cobre, dito com precisão: ela monta um
`telegram.Update` como o Telegram o entregaria e o passa ao mesmo `Application`
que o bot usa em produção, via `process_update`. Portanto exercita **todo o
código do Kobe** — roteamento, autorização, FIFO por tópico, prompt, resposta.
O único trecho que fica de fora é a entrega Telegram→bot, que não é código nosso
e não muda nesta jornada.

AS DUAS TRAVAS, CONFERIDAS ANTES DE QUALQUER COISA ACONTECER
-----------------------------------------------------------
Isto é uma porta de entrada no bot: monta um update dizendo ser o operador e o
injeta direto no despachante, pulando o Telegram. Por isso recusa cedo e recusa
fechado.

1. **`KOBE_ENV` tem de ser `dev`.** Fora disso, nem carrega configuração.
2. **O chat alvo tem de estar em `TELEGRAM_ALLOWED_CHAT_IDS`** — e a whitelist
   **vazia também é recusa**, não liberação. Em todo o resto do sistema, lista
   vazia significa "não filtro canal"; aqui significa "não sei onde é seguro
   bater", e a resposta certa a isso é parar. A assimetria é deliberada.

USO
---
    KOBE_ENV=dev python infra/dev_inject.py --texto "oi, tudo bem?"
    KOBE_ENV=dev python infra/dev_inject.py --roteiro bateria.txt

Formato do roteiro: uma mensagem por linha; linha vazia e linha começando com
`#` são ignoradas; `@<segundos>` no começo da linha espera antes de enviar.

    # bateria de aceite do ambiente de dev
    oi, você está em dev?
    @20 lista os plugins instalados
    @30 /contexto
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

logger = logging.getLogger("kobe.dev_inject")


class RecusaDeSeguranca(RuntimeError):
    """A ferramenta se recusou a rodar. Sempre por uma razão nomeada."""


def conferir_ambiente(environment: str) -> None:
    """Trava 1: só existe em dev."""
    from bot import environment as env_layer

    if environment != env_layer.DEV:
        raise RecusaDeSeguranca(
            f"esta ferramenta só roda em desenvolvimento (KOBE_ENV={environment!r}). "
            "Ela injeta mensagens direto no despachante do bot, pulando o Telegram — "
            "no caminho de produção isso seria uma porta dos fundos."
        )


def conferir_alvo(chat_id: int, permitidos: frozenset[int]) -> None:
    """Trava 2: o chat alvo tem de estar na lista branca — e a lista não pode
    estar vazia.

    Em `bot/authz.py`, lista vazia libera tudo, porque lá ela significa "esta
    instância não filtra canal". Aqui significa outra coisa: "ninguém me disse
    onde é seguro bater". Liberar nesse caso deixaria a ferramenta apontar para
    qualquer chat, inclusive um de produção que por acaso estivesse configurado.
    """
    if not permitidos:
        raise RecusaDeSeguranca(
            "TELEGRAM_ALLOWED_CHAT_IDS está vazia. Sem lista branca não dá pra "
            "afirmar que este chat é do ambiente de dev, e injetar às cegas num "
            "chat desconhecido é o que esta trava existe pra impedir."
        )
    if chat_id not in permitidos:
        raise RecusaDeSeguranca(
            f"chat {chat_id} não está em TELEGRAM_ALLOWED_CHAT_IDS. "
            f"Chats permitidos nesta instância: {sorted(permitidos)}."
        )


def montar_update(
    *,
    update_id: int,
    chat_id: int,
    thread_id: int | None,
    user_id: int,
    texto: str,
):
    """Monta o `Update` como o Telegram o entregaria.

    `is_topic_message` importa: sem ele, o bot trata a mensagem como se fosse do
    "Geral" do fórum e a knowledge base do tópico não é carregada — o teste
    passaria exercitando um caminho diferente do real.
    """
    from telegram import Chat, Message, Update, User

    user = User(id=user_id, first_name="operador", is_bot=False)
    chat = Chat(id=chat_id, type="supergroup")
    mensagem = Message(
        message_id=update_id,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=texto,
        message_thread_id=thread_id,
        is_topic_message=thread_id is not None,
    )
    return Update(update_id=update_id, message=mensagem)


def ler_roteiro(caminho: Path) -> list[tuple[float, str]]:
    """Lê o roteiro em `(espera_em_segundos, texto)`."""
    passos: list[tuple[float, str]] = []
    for numero, linha in enumerate(
        caminho.read_text(encoding="utf-8").splitlines(), start=1
    ):
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        espera = 0.0
        if linha.startswith("@"):
            marca, _, resto = linha.partition(" ")
            try:
                espera = float(marca[1:])
            except ValueError as exc:
                raise ValueError(
                    f"{caminho}:{numero}: '{marca}' não é uma espera válida "
                    "(use '@30 texto da mensagem')"
                ) from exc
            linha = resto.strip()
            if not linha:
                raise ValueError(f"{caminho}:{numero}: espera sem mensagem depois")
        passos.append((espera, linha))
    if not passos:
        raise ValueError(f"{caminho}: roteiro sem nenhuma mensagem")
    return passos


async def injetar(config, passos: list[tuple[float, str]], *, chat_id, thread_id, user_id):
    """Sobe o `Application` de verdade e entrega os updates a ele."""
    from bot.main import build_application

    app = build_application(config)
    await app.initialize()
    try:
        for indice, (espera, texto) in enumerate(passos, start=1):
            if espera:
                logger.info("aguardando %.0fs antes da mensagem %d...", espera, indice)
                await asyncio.sleep(espera)
            logger.info("[%d/%d] injetando: %s", indice, len(passos), texto)
            await app.process_update(
                montar_update(
                    update_id=10_000 + indice,
                    chat_id=chat_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    texto=texto,
                )
            )
        # As tarefas do turno rodam fora do process_update (FIFO por tópico).
        # Sair aqui mataria o turno no meio; esperar é o que faz a bateria valer.
        pendentes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pendentes:
            logger.info("aguardando %d turno(s) em andamento...", len(pendentes))
            await asyncio.gather(*pendentes, return_exceptions=True)
    finally:
        await app.shutdown()


def main(argv: list[str] | None = None) -> int:
    from bot.config import ConfigError, load_config

    ap = argparse.ArgumentParser(
        description="Injeta mensagens sintéticas no bot de desenvolvimento."
    )
    grupo = ap.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--texto", help="uma mensagem só")
    grupo.add_argument("--roteiro", type=Path, help="arquivo com a bateria")
    ap.add_argument("--chat-id", type=int, help="default: o único da whitelist")
    ap.add_argument("--thread-id", type=int, default=None, help="tópico do fórum")
    ap.add_argument("--user-id", type=int, help="default: o único autorizado")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"configuração inválida: {exc}", file=sys.stderr)
        return 2

    # As travas primeiro, antes de montar Application, abrir conexão ou
    # qualquer outro efeito. Recusa cedo é o que separa uma trava de um aviso.
    try:
        conferir_ambiente(config.environment)
        chat_id = args.chat_id
        if chat_id is None:
            if len(config.telegram_allowed_chat_ids) != 1:
                raise RecusaDeSeguranca(
                    "sem --chat-id, a whitelist precisa ter exatamente um chat "
                    f"(tem {len(config.telegram_allowed_chat_ids)}). "
                    "Passe --chat-id explicitamente."
                )
            chat_id = next(iter(config.telegram_allowed_chat_ids))
        conferir_alvo(chat_id, config.telegram_allowed_chat_ids)
    except RecusaDeSeguranca as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 3

    user_id = args.user_id
    if user_id is None:
        if len(config.allowed_user_ids) != 1:
            print(
                "sem --user-id, TELEGRAM_ALLOWED_USER_IDS precisa ter exatamente "
                f"um usuário (tem {len(config.allowed_user_ids)}).",
                file=sys.stderr,
            )
            return 2
        user_id = next(iter(config.allowed_user_ids))

    try:
        passos = (
            ler_roteiro(args.roteiro) if args.roteiro else [(0.0, args.texto)]
        )
    except (OSError, ValueError) as exc:
        print(f"roteiro inválido: {exc}", file=sys.stderr)
        return 2

    logger.info(
        "injetando %d mensagem(ns) no chat %s (tópico %s) como usuário %s",
        len(passos), chat_id, args.thread_id, user_id,
    )
    asyncio.run(
        injetar(config, passos, chat_id=chat_id, thread_id=args.thread_id, user_id=user_id)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
