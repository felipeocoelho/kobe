"""Liveness Protocol — ACK semântico por duração (Peça B da borda nova).

A borda antiga conflava DOIS sinais num "ACK" só, e por isso ele saía
inconsistente (dependia do modelo lembrar de avisar). Este módulo separa:

- **LIV-ack (recepcionista semântico):** o "entendi — vou [ação] e já te retorno".
  Dispara SÓ nas tarefas pesadas, no início. A **BORDA decide QUANDO** (via o
  `turn_classifier` — determinístico, consistente); um **modelo BARATO decide O
  QUÊ** (lê o pedido e NOMEIA a ação — semântico). Assim o ack é consistente E
  semântico. É a reconciliação com a regra "Avisa antes de agir" do CLAUDE.md:
  preserva a semântica (nomear a ação + "já volto"), remove a inconsistência (o
  disparo deixa de depender do modelo lembrar e vira garantia da borda).
- **LIV-progress:** o status mecânico ("lendo X / consultando a web") — é o
  `progress.py`, subsumido; não vive aqui.

O aviso enlatado de background ("passei pra rodar em background") é **aposentado**:
a tarefa pesada não fica muda porque o LIV-ack já disparou o "já te retorno" no
começo. A rede dos ~30s vira um LIV-ack TARDIO (tarefa classificada leve que
rendeu longa) — mesmo mecanismo, sem o texto burro.

Princípio (mesmo do sistema de Alertas do Kobe): o código decide o MECÂNICO
(quando), o modelo escreve o TEXTO (o quê). Modelo barato = gpt-4o-mini, fora da
cota do plano Max (reusa o client do detector de conversa).

Atrás da flag `EDGE_LIVENESS_ENABLED` — ver bot/config.py e bot/telegram_handler.py.
"""

from __future__ import annotations

import logging
import os


logger = logging.getLogger("kobe.liveness")

_MODEL = "gpt-4o-mini"

# Fallbacks consistentes (modelo barato indisponível/erro): melhor um ack fixo
# garantido que silêncio. Genérico mas na intenção certa ("já te retorno").
_FALLBACK_START = "Entendi — deixa eu cuidar disso e já te retorno."
_FALLBACK_LATE = (
    "Isso rendeu mais que eu esperava — deixa eu terminar aqui que já te volto."
)

_SYS_START = (
    "Você é um assistente pessoal conversando com seu operador por chat, em "
    "português do Brasil, tom direto e caloroso (sem formalidade). O operador "
    "acabou de te passar uma tarefa que vai levar um tempo. Escreva UMA frase "
    "curta confirmando que entendeu e que vai cuidar — NOMEANDO a ação (o que "
    "você vai fazer) e deixando claro que já volta com o resultado. Sem "
    "saudação, sem emoji, sem aspas. Exemplo de tom: 'Beleza, vou varrer a VPS "
    "atrás dos arquivos elegíveis pra backup e já te retorno.'"
)
_SYS_LATE = (
    "Você é um assistente pessoal conversando com seu operador por chat, em "
    "português do Brasil, tom direto. Uma tarefa que parecia rápida rendeu mais "
    "que o esperado e ainda está rodando. Escreva UMA frase curta dizendo que "
    "está terminando e que já volta — se der, nomeando o que está fazendo. Sem "
    "saudação, sem emoji, sem aspas."
)


def fallback_ack(*, late: bool = False) -> str:
    """Ack fixo (modelo barato fora do ar). Consistente, na intenção certa."""
    return _FALLBACK_LATE if late else _FALLBACK_START


async def write_ack(intent_text: str, *, late: bool = False) -> str:
    """Escreve o LIV-ack semântico via modelo barato. Nunca levanta — em
    qualquer falha devolve o fallback consistente (o ack é GARANTIDO)."""
    if not os.environ.get("OPENAI_API_KEY"):
        return fallback_ack(late=late)
    try:
        from bot.conversation_detector import _get_openai

        resp = await _get_openai().chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYS_LATE if late else _SYS_START},
                {"role": "user", "content": (intent_text or "")[:1500]},
            ],
            temperature=0.4,
            max_tokens=80,
        )
        text = (resp.choices[0].message.content or "").strip().strip('"').strip()
        return text or fallback_ack(late=late)
    except Exception as exc:  # noqa: BLE001 — ack nunca derruba o turno
        logger.warning("liveness: mini falhou (%s); fallback", exc)
        return fallback_ack(late=late)
