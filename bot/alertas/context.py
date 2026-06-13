"""Injeção de contexto de alertas no prompt do Hal.

Quando um alerta com confirmação está ABERTO (cobrando), o Hal precisa
reconhecer, numa mensagem NORMAL do operador, que ele confirmou ("já
marquei", "agendei", "feito") — e fechar o ciclo chamando
`kobe-alerta confirmar <id>`. Como o Hal já vê toda mensagem no loop de
conversa, basta ele saber que há alerta aguardando confirmação. Esta
seção dá exatamente isso, em texto curto, sem inflar o prompt.

O fechamento NÃO edita estado direto: o helper emite um evento e a
AlertasSource aplica a transição. Código é dono do estado.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from bot.alertas import StatusAlerta, storage


logger = logging.getLogger("kobe.alertas.context")


def render_alertas_abertos(
    kobe_home: Path, chat_id: int, thread_id: Optional[int]
) -> Optional[str]:
    """Seção `[Alertas aguardando confirmação...]` ou None se não há nenhum.

    Lista só os alertas ABERTOS (cobrando) deste tópico. Best-effort: erro
    de leitura não derruba o fluxo de mensagem — devolve None e loga.
    """
    try:
        alertas = storage.listar_alertas(
            kobe_home, apenas_vivos=True, chat_id=chat_id, thread_id=thread_id,
        )
    except Exception:  # noqa: BLE001 — contexto é nice-to-have
        logger.warning("falha listando alertas pro contexto", exc_info=True)
        return None

    abertos = [a for a in alertas if a.estado.status == StatusAlerta.ABERTO.value]
    if not abertos:
        return None

    linhas = [
        "[Alertas aguardando confirmação neste tópico]",
        "Há lembrete(s) ABERTO(s) — você está cobrando o operador. Se a "
        "mensagem dele indicar que JÁ resolveu o que o alerta pedia, feche "
        "o ciclo rodando no Bash: `bot/bin/kobe-alerta confirmar <id> "
        "\"<o que ele disse>\"`. Não invente confirmação: só feche se ele "
        "realmente sinalizou que fez. Se ele disser pra deixar pra lá esta "
        "vez, use `dispensar` no lugar de `confirmar`.",
    ]
    for a in abertos:
        criterio = a.confirmacao.fecha_quando if a.confirmacao else "(sem critério)"
        linhas.append(f"  • id `{a.id}` — \"{a.titulo}\" · fecha quando: {criterio}")
    return "\n".join(linhas)
