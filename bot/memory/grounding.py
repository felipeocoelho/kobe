"""Sinais de grounding baratos, resolvidos no código (Highlander Frente 1.1).

P2 do plano v4: o código resolve e injeta, já mastigado, o que o agente senão
narraria de memória — em especial o que MUDA com o tempo. Casa direto com duas
regras do contrato (Fundamentação):

- "Nada relativo ao TEMPO sem conferir o tempo." O cabeçalho já dá o `[Agora]`;
  aqui acrescentamos **há quanto tempo foi a última troca** neste tópico.
- "Retomada depois de um tempo: o contexto recente pode não ser sobre o que ele
  quer agora." Numa volta após horas/dias, o sinal lembra o agente de confirmar
  o antecedente em vez de colar a intenção no assunto mais saliente.

Barato e read-only: lê o `created_at` que JÁ veio no histórico imediato (sem
query nova, sem LLM). Só fala quando o gap é informativo (uma retomada) — num
papo contínuo fica calado pra não virar ruído.

A mensagem nova deste turno ainda NÃO está no histórico quando isto roda (o
handler persiste a msg depois de montar o contexto), então o último `created_at`
do histórico é o da mensagem ANTERIOR — exatamente o "tempo desde a última troca".
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from bot.memory.working_set import _parse_ts


# Abaixo deste gap, o turno é continuação — o `[Agora]` já basta e o sinal só
# poluiria. Acima, é retomada: vale lembrar o agente de não assumir o antecedente.
GAP_NOTICE_THRESHOLD_SECONDS = 1800  # 30 min


def _humanize_gap(seconds: float) -> str:
    """Gap em linguagem humana grosseira (min/horas/dias). Aproximado de
    propósito — é sinal de ordem de grandeza, não cronômetro de precisão."""
    minutes = int(seconds // 60)
    if minutes < 90:
        return f"~{minutes} min"
    hours = seconds / 3600
    if hours < 36:
        return f"~{round(hours)} h"
    days = hours / 24
    return f"~{round(days)} dia(s)"


def render_grounding_signals(
    history: Iterable[dict], *, now: Optional[datetime] = None
) -> Optional[str]:
    """Monta o bloco `[Grounding]` de sinais temporais baratos. None se não há
    histórico legível ou se o gap é curto demais pra ser informativo."""
    now = now or datetime.now(timezone.utc)

    # Último created_at parseável do histórico (de trás pra frente).
    last_dt: Optional[datetime] = None
    for row in reversed(list(history)):
        last_dt = _parse_ts(row.get("created_at") or "")
        if last_dt is not None:
            break
    if last_dt is None:
        return None

    gap = (now - last_dt).total_seconds()
    if gap < GAP_NOTICE_THRESHOLD_SECONDS:
        return None

    return (
        f"[Grounding] A última mensagem neste tópico foi há {_humanize_gap(gap)} "
        "(antes desta). Se for retomar um assunto antigo, confirme que a mensagem "
        "nova é de fato sobre ele — pode não ser; na dúvida do antecedente, pergunte."
    )
