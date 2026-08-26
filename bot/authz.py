"""Quem pode falar com o bot: usuário autorizado **e** canal autorizado.

Até a camada de ambiente existir, a autorização do Kobe era uma dimensão só —
`user_id` na lista de `TELEGRAM_ALLOWED_USER_IDS`. Isso basta quando há um bot
só: o operador fala com ele de onde quiser. Com **dois** ambientes na mesma
máquina, deixa de bastar: o operador é a mesma pessoa nos dois, então o
`user_id` não distingue nada. O que distingue é o **chat**.

Daí a segunda dimensão, `TELEGRAM_ALLOWED_CHAT_IDS`:

- **vazia ou ausente → não filtra nada.** É o comportamento de hoje, e é o que
  mantém a produção intocada sem uma variável nova no `.env` dela.
- **preenchida → só os chats listados são atendidos.** Mensagem de qualquer
  outro chat é ignorada **em silêncio** (registro em DEBUG). Silêncio e não
  recusa educada porque um bot que responde "não te conheço" confirma que existe
  e que está ali; ignorar não confirma nada.

**Falha fechada é a regra.** Na dúvida, não responde. A assimetria (lista vazia
libera tudo) não contradiz isso: lista vazia não é dúvida, é a declaração de que
não há filtro de canal — o estado em que a produção vive hoje.

Este módulo existe porque a verificação estava **copiada em quatro lugares**
(`telegram_handler`, `alertas/handlers`, `chat_manager_commands` — este
aposentado junto com o Chat Manager — e `mission_control/handlers`), com 22
pontos de chamada. Quatro cópias de uma
regra de segurança é quatro chances de a whitelist falhar ABERTA — que é
exatamente o desfecho que ela existe para impedir. Agora é uma função só, e
`tests/test_chat_whitelist.py` assevera por grep que ninguém criou a quinta.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from telegram import Update

if TYPE_CHECKING:  # pragma: no cover — só para o type checker
    from bot.config import Config

logger = logging.getLogger("kobe.authz")


def user_authorized(update: Update, allowed_ids: frozenset[int]) -> bool:
    """O autor da mensagem está na lista de usuários autorizados."""
    user = update.effective_user
    return user is not None and user.id in allowed_ids


def chat_authorized(update: Update, allowed_chat_ids: frozenset[int]) -> bool:
    """O chat de origem está liberado.

    Lista vazia libera tudo — é o comportamento de hoje da produção. Com a lista
    preenchida, um update sem chat identificável é recusado: se não dá para
    saber de onde veio, não dá para dizer que veio de um lugar permitido.
    """
    if not allowed_chat_ids:
        return True
    chat = update.effective_chat
    if chat is None:
        logger.debug("authz: update sem chat identificável recusado pela whitelist")
        return False
    if chat.id in allowed_chat_ids:
        return True
    logger.debug("authz: chat %s fora da whitelist — ignorado", chat.id)
    return False


def update_authorized(update: Update, config: "Config") -> bool:
    """A verificação completa: usuário **e** canal.

    É esta que todo handler deve chamar. As duas condições são conjuntivas por
    desenho — liberar um chat não libera quem fala nele, e ser o operador não
    dá acesso a partir de um chat que o ambiente não conhece.
    """
    return user_authorized(update, config.allowed_user_ids) and chat_authorized(
        update, config.telegram_allowed_chat_ids
    )


def chat_allowed_for(update: Update, config: Optional["Config"]) -> bool:
    """Só a dimensão de canal, para handlers que hoje não verificam usuário.

    Os handlers de evento de fórum e os comandos do Apolo nunca tiveram
    verificação nenhuma. Acrescentar a de **usuário** neles mudaria o
    comportamento da produção (passaria a recusar quem hoje é atendido), o que a
    invariante de aditividade da Sessão #1 proíbe — então aqui vai só a de
    canal, que é inerte enquanto a whitelist estiver vazia.

    A ausência de verificação de usuário nesses pontos está registrada como
    achado de segurança no CHANGELOG desta entrega. Ela é dívida conhecida, não
    descuido — e o conserto é sessão própria, para não contaminar a prova de
    não-regressão desta.
    """
    if config is None:
        return True
    return chat_authorized(update, config.telegram_allowed_chat_ids)
