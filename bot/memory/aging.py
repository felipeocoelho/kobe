"""Datar o que envelhece — carimbo de idade no que o prompt apresenta como atual.

A dor (caso real, 19/08): o agente afirmou que uma sala de missão "segue
rodando, te reporto quando entregar". A sala estava `idle` havia 12 dias e já
tinha entregado os documentos. Não foi só indisciplina do modelo — tem causa
material, confirmada no código:

1. **O histórico injetado no prompt não tinha timestamp.** O código buscava
   `created_at` do banco e DESCARTAVA ao montar o texto: cada linha virava
   `papel: conteúdo`. Uma mensagem de 12 dias ficava visualmente IDÊNTICA à de
   agora — o modelo não tinha como saber que estava lendo passado.
2. **A linha da sala entrava sem estado e sem data**, com a palavra "ativa" no
   presente, porque `idle` conta como status ativo na busca. O agente leu o que
   estava escrito.
3. O bloco `[Estado de background vivo]`, que deveria ser o antídoto, **só
   olhava sessões do Coder** e escondia o que tinha mais de 6h — sala de missão
   nunca entrava.

Este módulo concentra o formato de idade usado nas três correções, pra a
linguagem ser a mesma em todo lugar do prompt (o agente aprende um padrão só).

Formato escolhido pelo operador: `[dd/mm HH:MM]` em TODA linha (verificável,
sem ambiguidade), mais a idade relativa (`— há 12 dias`) na primeira linha e
sempre que houver um salto grande de tempo. Custa ~4% da janela e não deixa
brecha; a alternativa mais barata (carimbar só nos saltos) foi descartada.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")

# Salto a partir do qual a idade relativa é repetida numa linha do histórico.
# Abaixo disso, a conversa é contínua e a data absoluta já basta — repetir "há
# X" em toda linha seria ruído e token gasto à toa.
SALTO_RELEVANTE_SEGUNDOS = 6 * 3600


def parse_ts(value: str) -> Optional[datetime]:
    """Parseia timestamp ISO 8601 com tolerância a sufixo 'Z'. None se ilegível
    — o chamador degrada pra linha sem carimbo, nunca pra erro."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    # Timestamp sem fuso: assume UTC (é o que o banco grava).
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def humanizar_idade(segundos: float) -> str:
    """Idade em linguagem humana grosseira. Aproximado de propósito — é sinal de
    ordem de grandeza ("isso é velho"), não cronômetro."""
    if segundos < 60:
        return "agora há pouco"
    minutos = int(segundos // 60)
    if minutos < 90:
        return f"há ~{minutos} min"
    horas = segundos / 3600
    if horas < 36:
        return f"há ~{round(horas)} h"
    dias = round(horas / 24)
    return f"há ~{dias} dia" if dias == 1 else f"há ~{dias} dias"


def carimbo(
    dt: Optional[datetime],
    *,
    agora: Optional[datetime] = None,
    com_idade: bool = False,
) -> str:
    """Carimbo de uma linha: `[20/08 09:14]` ou `[08/08 14:02 — há ~12 dias]`.

    String vazia quando não há timestamp legível (a linha sai sem carimbo, em
    vez de sair com uma data inventada — na dúvida, nada).
    """
    if dt is None:
        return ""
    local = dt.astimezone(OPERATOR_TZ)
    base = local.strftime("%d/%m %H:%M")
    if not com_idade:
        return f"[{base}]"
    agora = agora or datetime.now(timezone.utc)
    return f"[{base} — {humanizar_idade((agora - dt).total_seconds())}]"


def estado_com_idade(
    status: str, last_activity: Optional[str], *, agora: Optional[datetime] = None
) -> str:
    """Trecho `status=idle, última atividade há ~12 dias` pra qualquer coisa que
    o prompt apresente como viva (sala de missão, sessão do Coder, job)."""
    dt = parse_ts(last_activity or "")
    if dt is None:
        return f"status={status}, última atividade desconhecida"
    agora = agora or datetime.now(timezone.utc)
    idade = humanizar_idade((agora - dt).total_seconds())
    quando = dt.astimezone(OPERATOR_TZ).strftime("%d/%m %H:%M")
    return f"status={status}, última atividade {idade} ({quando})"
