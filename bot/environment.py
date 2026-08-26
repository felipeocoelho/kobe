"""Em que ambiente esta instância do Kobe está rodando.

O Kobe passou a existir em **dois ambientes independentes na mesma máquina**
(Projeto Novo Ambiente Kobe, Sessão #1): produção — o que o operador usa — e
desenvolvimento — onde se testa antes. Bot, canal do Telegram, memória durável e
alcance de WhatsApp são separados; o que amarra tudo é uma variável só,
`KOBE_ENV`.

**Default `prod`, e isso é regra, não conveniência.** A camada inteira é aditiva:
com o `.env` que a produção tem hoje — onde `KOBE_ENV` não existe — o
comportamento tem que ser idêntico ao de antes de este módulo existir. Quem
esquecer a variável cai em produção, que é o lado seguro do erro: um dev que se
comporta como prod escreve no lugar errado; um prod que se comporta como dev
para de fazer o que o operador espera.

**Valor desconhecido é ERRO, nunca fallback silencioso.** `KOBE_ENV=staging` num
mundo que só conhece `prod` e `dev` significa que alguém acha que está num
terceiro ambiente que não existe. Cair calado em `prod` aí seria o pior dos dois
mundos — a instância se comportaria como produção enquanto a pessoa jura que
não. Melhor não subir e dizer por quê.

Este módulo existe separado de `bot/config.py` por um motivo prático: nem todo
consumidor tem um `Config` na mão. O helper `bot/bin/kobe-reflect` e o
`bot/hindsight_client.py` precisam saber o ambiente e são chamados fora do
processo do bot — eles leem daqui, do ambiente que o `ClaudeRunner` exporta pro
subprocesso.
"""

from __future__ import annotations

import os
from typing import Optional

#: Nome da variável de ambiente que carrega o ambiente atual.
ENV_VAR = "KOBE_ENV"

PROD = "prod"
DEV = "dev"

#: Os únicos valores aceitos. Ampliar isto é decisão de projeto, não detalhe.
VALORES_ACEITOS = (PROD, DEV)


class InvalidEnvironment(ValueError):
    """`KOBE_ENV` veio com um valor que o Kobe não conhece.

    É `ValueError` e não `ConfigError` de propósito: `bot/config.py` importa
    este módulo, então a dependência só pode andar num sentido. Quem carrega a
    configuração captura isto e reembrulha em `ConfigError`, para o start do bot
    falhar com a mensagem que o resto do sistema já sabe tratar.
    """


def normalize(raw: Optional[str]) -> str:
    """Converte o valor cru de `KOBE_ENV` no ambiente canônico.

    Ausente, vazio ou só espaço → `prod` (o mundo de hoje). Aceita variação de
    caixa e espaço em volta, porque `.env` escrito à mão erra nisso e recusar
    `KOBE_ENV=Dev ` seria rigor sem ganho. Qualquer outro valor levanta
    `InvalidEnvironment`.
    """
    valor = (raw or "").strip().lower()
    if not valor:
        return PROD
    if valor in VALORES_ACEITOS:
        return valor
    aceitos = " | ".join(VALORES_ACEITOS)
    raise InvalidEnvironment(
        f"{ENV_VAR}={raw!r} não é um ambiente conhecido. Valores aceitos: {aceitos}. "
        f"Deixe a variável ausente para rodar como {PROD}."
    )


def current() -> str:
    """O ambiente desta execução, lido do ambiente do processo.

    Para quem tem um `Config` na mão, prefira `config.environment` — é o mesmo
    valor, já validado uma vez no start. Isto aqui é para os consumidores que
    rodam fora do processo do bot (helpers em `bot/bin/`, scripts de plugin).
    """
    return normalize(os.getenv(ENV_VAR))


def is_dev(environment: Optional[str] = None) -> bool:
    """Atalho legível para o ramo de desenvolvimento.

    `environment=None` significa "descobre sozinho" — é o default de todo
    consumidor que aceita o parâmetro, para que injetar o ambiente em teste
    continue possível sem obrigar cada chamador a passá-lo.
    """
    resolvido = current() if environment is None else normalize(environment)
    return resolvido == DEV
