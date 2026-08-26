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

AS DUAS PERNAS DO CONSERTO DE 2026-08-26 (a ferramenta não funcionava)
---------------------------------------------------------------------
A primeira versão montava o `Update` com os construtores da python-telegram-bot,
e isso derrubava a bateria inteira por dois motivos independentes — o segundo
escondido atrás do primeiro:

1. **O objeto não tinha bot associado.** O handler chama `message.get_bot()` na
   PRIMEIRA linha (a reação 👀 de `_react_received`), o que levanta
   `RuntimeError: This object has no bot associated with it`. O turno morria
   antes de começar. Conserto: `Update.de_json(bruto, bot)`, o mesmo caminho que
   o PTB usa ao receber do Telegram — ver `montar_update`.
2. **A mensagem não existia do lado do Telegram.** O bot responde CITANDO a
   mensagem de entrada e reage a ela; com um `message_id` inventado, o Telegram
   recusa ("Message to be replied not found") e o turno morre ao responder.
   Conserto: publicar o texto no tópico antes de injetar e usar o `message_id`
   real — ver o parâmetro `eco` em `injetar`.

Um bônus do segundo conserto: como o eco sai marcado com 🧪 no grupo de dev, o
operador **assiste** a bateria acontecer, que é o que este arquivo diz querer lá
em cima.

EXIGÊNCIA DE AMBIENTE
---------------------
O turno chama o CLI do `claude`, que mora em `~/.local/bin`. O serviço tem esse
caminho no `PATH` (está no unit); um shell de sessão automatizada pode não ter, e
aí a bateria responde "o CLI do Claude não está disponível" — falha do arnês, não
do bot. Custou a primeira tentativa de 26/08.

USO
---
    KOBE_ENV=dev python infra/dev_inject.py --texto "oi, tudo bem?"
    KOBE_ENV=dev python infra/dev_inject.py --roteiro bateria.txt
    KOBE_ENV=dev python infra/dev_inject.py --roteiro rajada.txt --rajada

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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

logger = logging.getLogger("kobe.dev_inject")

# Marca do eco no grupo de dev. Existe pra que ninguém confunda mensagem de
# bateria com mensagem de gente — inclusive daqui a seis meses, lendo o
# histórico do tópico sem lembrar que houve bateria nenhuma.
ECO_PREFIXO = "🧪"


class RecusaDeSeguranca(RuntimeError):
    """A ferramenta se recusou a rodar. Sempre por uma razão nomeada."""


def forcar_env_do_arquivo(env_path: Path) -> list[str]:
    """Trava 0: o `.env` DESTA árvore vence o ambiente herdado.

    Descoberto em 26/08/2026, na primeira execução da bateria C3, e é o tipo de
    coisa que só aparece rodando: `load_dotenv()` **não sobrescreve** variável
    que já existe no ambiente. Uma sessão automatizada disparada pelo Kobe de
    PRODUÇÃO herda o ambiente dele — `TELEGRAM_BOT_TOKEN` inclusive. Resultado
    observado: o arnês subiu com o token do bot de **produção** apontando para o
    chat de **dev**.

    O estrago não aconteceu porque o bot de produção não é membro do grupo de
    dev (o Telegram respondeu "Chat not found") e porque a lista branca de chats
    é de dev. Mas depender disso é depender de sorte: com os dois bots no mesmo
    grupo, a ferramenta teria falado como produção sem avisar ninguém.

    A regra certa para uma ferramenta cujo propósito É rodar a configuração de
    dev: o arquivo manda, o ambiente não. Devolve os nomes sobrescritos para que
    a substituição apareça no log em vez de acontecer calada.
    """
    from dotenv import dotenv_values

    sobrescritos: list[str] = []
    for chave, valor in dotenv_values(env_path).items():
        if valor is None:
            continue
        if os.environ.get(chave) not in (None, valor):
            sobrescritos.append(chave)
        os.environ[chave] = valor
    return sobrescritos


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
    message_id: int | None = None,
    bot=None,
):
    """Monta o `Update` como o Telegram o entregaria — pelo mesmo caminho que ele.

    `is_topic_message` importa: sem ele, o bot trata a mensagem como se fosse do
    "Geral" do fórum e a knowledge base do tópico não é carregada — o teste
    passaria exercitando um caminho diferente do real.

    `bot` importa MAIS ainda, e é a primeira perna do conserto de 2026-08-26:
    montar com os construtores da python-telegram-bot devolve um objeto **sem
    bot associado**, e o handler chama `message.get_bot()` logo na entrada (a
    reação 👀 de `_react_received`). Sem bot amarrado aquilo levanta
    `RuntimeError: This object has no bot associated with it` e o turno morre
    ANTES de começar — a bateria inteira media o nada.

    `Update.de_json(bruto, bot)` é o caminho que o próprio PTB usa ao receber do
    Telegram, e ele desce o bot pela árvore inteira do objeto. `set_bot()` não
    serviria: marca só o objeto em que é chamado, deixando os filhos órfãos pra
    estourar mais adiante.

    `bot=None` continua montando, de propósito: é o que deixa o teste conferir a
    fidelidade do payload sem rede nenhuma.
    """
    from telegram import Update

    mensagem: dict = {
        "message_id": message_id if message_id is not None else update_id,
        "date": int(datetime.now(timezone.utc).timestamp()),
        "chat": {
            "id": chat_id,
            "type": "supergroup",
            "is_forum": thread_id is not None,
        },
        "from": {"id": user_id, "is_bot": False, "first_name": "operador"},
        "text": texto,
    }
    if thread_id is not None:
        mensagem["message_thread_id"] = thread_id
        mensagem["is_topic_message"] = True
    return Update.de_json({"update_id": update_id, "message": mensagem}, bot)


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


async def _aguardar_turnos() -> None:
    """Espera as tarefas do turno, que rodam FORA do `process_update`.

    O handler devolve o controle assim que enfileira o turno no FIFO por tópico
    (`_TopicGate`). Sair aqui mataria o turno no meio — esperar é o que faz a
    bateria valer alguma coisa.
    """
    pendentes = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pendentes:
        logger.info("aguardando %d turno(s) em andamento...", len(pendentes))
        await asyncio.gather(*pendentes, return_exceptions=True)


def percentil(valores: list[float], p: float) -> float:
    """Percentil pelo método do vizinho mais próximo. Amostra pequena não pede
    interpolação — pede honestidade sobre o tamanho da amostra."""
    if not valores:
        raise ValueError("percentil de amostra vazia")
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, max(0, round(p / 100 * len(ordenados) + 0.5) - 1))
    return ordenados[indice]


async def injetar(
    config,
    passos: list[tuple[float, str]],
    *,
    chat_id,
    thread_id,
    user_id,
    eco: bool = True,
    rajada: bool = False,
) -> list[tuple[str, float]]:
    """Sobe o `Application` de verdade e entrega os updates a ele.

    `eco` é a segunda perna do conserto de 2026-08-26. O bot responde CITANDO a
    mensagem de entrada (`ProgressReporter` leva `reply_to_message_id`, e
    `_send_long_text` usa `reply_text`), e reage a ela com 👀. Um `message_id`
    inventado não existe do lado do Telegram, então as duas coisas falham — a
    resposta com "Message to be replied not found", que derruba o turno.

    O eco publica o texto no tópico e usa o `message_id` REAL que o Telegram
    devolveu. O `from` do update injetado continua sendo o operador, então quem
    julga é o `bot/authz.py` de verdade — o eco só faz o objeto existir. Efeito
    colateral desejado, e declarado no topo deste arquivo: o operador **assiste**
    a bateria acontecer no grupo de dev, marcada com 🧪.

    `rajada` injeta o bloco inteiro sem esperar o turno anterior fechar. É o que
    exercita o FIFO por tópico: esperando cada turno, rajada não existe.

    Devolve `[(texto, segundos)]` — o instrumento de latência da bateria.
    """
    from bot.main import build_application

    app = build_application(config)
    await app.initialize()
    tempos: list[tuple[str, float]] = []
    try:
        # Dizer ALTO qual bot respondeu. Trocar de bot sem perceber é a falha
        # que a trava 0 existe pra impedir; imprimir o nome é o que a torna
        # impossível de passar batida mesmo se a trava um dia falhar.
        eu = await app.bot.get_me()
        logger.info("falando como @%s (id %s) no chat %s", eu.username, eu.id, chat_id)
        for indice, (espera, texto) in enumerate(passos, start=1):
            if espera:
                logger.info("aguardando %.0fs antes da mensagem %d...", espera, indice)
                await asyncio.sleep(espera)

            message_id = 10_000 + indice
            if eco:
                publicada = await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"{ECO_PREFIXO} {texto}",
                    **({"message_thread_id": thread_id} if thread_id is not None else {}),
                )
                message_id = publicada.message_id

            logger.info("[%d/%d] injetando: %s", indice, len(passos), texto)
            inicio = asyncio.get_running_loop().time()
            await app.process_update(
                montar_update(
                    update_id=10_000 + indice,
                    chat_id=chat_id,
                    thread_id=thread_id,
                    user_id=user_id,
                    texto=texto,
                    message_id=message_id,
                    bot=app.bot,
                )
            )
            if not rajada:
                await _aguardar_turnos()
                decorrido = asyncio.get_running_loop().time() - inicio
                tempos.append((texto, decorrido))
                logger.info("[%d/%d] pronto em %.1fs", indice, len(passos), decorrido)

        if rajada:
            # Em rajada o cronômetro por turno não faz sentido — os turnos se
            # sobrepõem. O que importa aqui é a ORDEM, e ela se lê no banco.
            await _aguardar_turnos()
    finally:
        await app.shutdown()
    return tempos


def relatar_tempos(tempos: list[tuple[str, float]]) -> None:
    """Imprime o resumo de latência. Amostra pequena é dita como tal."""
    if not tempos:
        return
    print("\n=== tempo por turno ===")
    for texto, segundos in tempos:
        print(f"  {segundos:6.1f}s  {texto[:60]}")
    valores = [s for _, s in tempos]
    print(
        f"\n  n={len(valores)}  "
        f"p50={percentil(valores, 50):.1f}s  "
        f"p95={percentil(valores, 95):.1f}s  "
        f"máximo={max(valores):.1f}s"
    )


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
    ap.add_argument(
        "--sem-eco",
        action="store_true",
        help=(
            "não publica a mensagem no tópico antes de injetar. O update passa a "
            "apontar pra um message_id que não existe, e o turno costuma morrer ao "
            "responder citando ('Message to be replied not found'). Só pra "
            "diagnóstico sem rede."
        ),
    )
    ap.add_argument(
        "--rajada",
        action="store_true",
        help=(
            "injeta tudo sem esperar o turno anterior fechar — é o que exercita o "
            "FIFO por tópico. Sem isto não existe rajada, existe fila."
        ),
    )
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    # Trava 0, antes de tudo: o `.env` desta árvore vence o ambiente herdado.
    # Ver `forcar_env_do_arquivo` — sem isto, uma sessão automatizada disparada
    # pela produção roda o arnês com o token DELA.
    env_path = RAIZ / ".env"
    if env_path.exists():
        sobrescritos = forcar_env_do_arquivo(env_path)
        if sobrescritos:
            logger.warning(
                "ambiente herdado sobrescrito pelo %s: %s "
                "(esta ferramenta roda a configuração desta árvore, não a de quem a chamou)",
                env_path,
                ", ".join(sorted(sobrescritos)),
            )

    try:
        config = load_config(env_path if env_path.exists() else None)
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
    tempos = asyncio.run(
        injetar(
            config,
            passos,
            chat_id=chat_id,
            thread_id=args.thread_id,
            user_id=user_id,
            eco=not args.sem_eco,
            rajada=args.rajada,
        )
    )
    relatar_tempos(tempos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
