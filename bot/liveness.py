"""Liveness Protocol — ACK semântico por duração (Peça B da borda nova).

A borda antiga conflava DOIS sinais num "ACK" só, e por isso ele saía
inconsistente (dependia do modelo lembrar de avisar). Este módulo separa:

- **LIV-ack (recepcionista semântico):** o "entendi — já te retorno". Dispara SÓ
  nas tarefas pesadas, no início. A **BORDA decide QUANDO** (via o
  `turn_classifier` — determinístico, consistente); um **modelo BARATO decide O
  QUÊ** (escreve o reconhecimento — semântico). Assim o ack é consistente E
  semântico. É a reconciliação com a regra "Avisa antes de agir" do CLAUDE.md.
- **LIV-progress:** o status mecânico ("lendo X / consultando a web") — é o
  `progress.py`, subsumido; não vive aqui.

O aviso enlatado de background ("passei pra rodar em background") é **aposentado**:
a tarefa pesada não fica muda porque o LIV-ack já disparou o "já te retorno" no
começo. A rede dos ~30s vira um LIV-ack TARDIO (tarefa classificada leve que
rendeu longa) — mesmo mecanismo, sem o texto burro.

Princípio (mesmo do sistema de Alertas do Kobe): o código decide o MECÂNICO
(quando), o modelo escreve o TEXTO (o quê).

──────────────────────────────────────────────────────────────────────────
CORREÇÃO 2026-08-20 — o ack estava ALUCINANDO
──────────────────────────────────────────────────────────────────────────

Sintoma relatado pelo operador: o ack inventava plano, inventava nome de
arquivo, inventava ferramenta que não seria usada.

Causa (confirmada no código, não era indisciplina do modelo — era o DESENHO):
o modelo barato recebe **apenas a mensagem do operador**. Sem repositório, sem
histórico, sem saber o que o agente principal vai fazer. E o prompt mandava, com
todas as letras, *"NOMEANDO a ação (o que você vai fazer)"* — com um exemplo que
era ele próprio uma invenção completa ("vou varrer a VPS atrás dos arquivos
elegíveis pra backup"). Pedíamos especificidade a quem não tem informação
nenhuma; ele obedecia, copiava o exemplo, e inventava.

A correção inverte a regra em três frentes:

1. **O prompt PROÍBE fato novo** e manda ancorar nas palavras do próprio
   operador. Variação de linguagem sim; invenção não.
2. **Guarda-costas programático** (`_tem_invencao`), atrás de flag própria:
   depois que o modelo escreve, o código confere e REJEITA silenciosamente o
   texto que traga sinal claro de invenção (número que não está na mensagem,
   caminho/extensão de arquivo, ou termo técnico de uma lista curta e fechada
   que não aparece na mensagem). Rejeitou → cai no fallback fixo, que não
   inventa nada. Conservador de propósito: pode derrubar um ack legítimo de vez
   em quando, e o preço disso é o operador ver o texto fixo — muito menor que o
   preço de uma invenção.
3. **Modelo configurável por `.env`** (`LIVENESS_ACK_PROVIDER` / `_MODEL`), com
   o modelo barato de hoje como DEFAULT. Trocar por Haiku depois é uma linha no
   `.env` + restart, sem obra — a decisão é do operador, com resultado na mão.

Atrás da flag `EDGE_LIVENESS_ENABLED` — ver bot/config.py e bot/telegram_handler.py.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata


logger = logging.getLogger("kobe.liveness")

# Modelo do ack — configurável por .env. Default = exatamente o de hoje, pra a
# correção do prompt ser avaliada isolada da troca de modelo.
_DEFAULT_PROVIDER = "openai"
_DEFAULT_MODEL = "gpt-4o-mini"

# Fallbacks consistentes (modelo barato indisponível/erro/rejeitado pelo
# guarda-costas): melhor um ack fixo garantido que silêncio — e, principalmente,
# que uma invenção. Genérico mas na intenção certa e sem nenhum fato novo.
_FALLBACK_START = "Entendi — deixa eu cuidar disso e já te retorno."
_FALLBACK_LATE = (
    "Isso rendeu mais que eu esperava — deixa eu terminar aqui que já te volto."
)

_SYS_START = (
    "Você é um assistente pessoal conversando com seu operador por chat, em "
    "português do Brasil, tom direto e caloroso (sem formalidade). O operador "
    "acabou de te passar uma tarefa que vai levar um tempo.\n\n"
    "Você NÃO SABE o que será feito, com quais ferramentas, em quais arquivos, "
    "nem quanto tempo leva — e NÃO PODE SUPOR. Você só tem a mensagem dele.\n\n"
    "Escreva UMA frase curta que faça apenas duas coisas: (a) mostre que você "
    "entendeu o pedido, REUSANDO as palavras do próprio operador; e (b) diga "
    "que está providenciando e que já volta com o resultado.\n\n"
    "É PROIBIDO citar nome de arquivo, pasta, sistema, ferramenta, comando, "
    "etapa, número, prazo, ou qualquer termo que não esteja na mensagem dele. "
    "Não descreva um plano. Não prometa etapas. Varie a forma de escrever; "
    "nunca invente conteúdo.\n\n"
    "Sem saudação, sem emoji, sem aspas. Exemplo de FORMA (não copie o "
    "conteúdo): se ele pedisse algo sobre 'o relatório de ontem', caberia "
    "'Beleza — vou providenciar isso do relatório de ontem e já te volto.'"
)
_SYS_LATE = (
    "Você é um assistente pessoal conversando com seu operador por chat, em "
    "português do Brasil, tom direto. Uma tarefa que parecia rápida rendeu mais "
    "que o esperado e ainda está rodando.\n\n"
    "Você NÃO SABE o que está sendo feito nem quanto falta — e NÃO PODE SUPOR. "
    "Escreva UMA frase curta dizendo que ainda está nisso e que já volta, "
    "reusando as palavras do próprio operador pra mostrar do que se trata.\n\n"
    "É PROIBIDO citar nome de arquivo, pasta, sistema, ferramenta, comando, "
    "etapa, número, prazo, ou qualquer termo que não esteja na mensagem dele. "
    "Não descreva o que está acontecendo por dentro.\n\n"
    "Sem saudação, sem emoji, sem aspas."
)


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def _guard_enabled() -> bool:
    raw = (os.getenv("LIVENESS_ACK_GUARD_ENABLED") or "").strip().lower()
    return raw in ("1", "true", "on", "yes")


def fallback_ack(*, late: bool = False) -> str:
    """Ack fixo (modelo barato fora do ar, ou texto rejeitado pelo guarda-costas).
    Consistente, na intenção certa, e sem nenhum fato novo."""
    return _FALLBACK_LATE if late else _FALLBACK_START


# ── Guarda-costas anti-invenção ───────────────────────────────────────────

# Lista CURTA e FECHADA de termos de invenção clássica. Não é filtro de
# conteúdo genérico: são as palavras que o modelo usa quando está fabricando um
# plano que não tem como conhecer. Se o operador usou a palavra, ela é legítima
# e passa — o teste é sempre "está na mensagem dele?".
_TERMOS_TECNICOS = frozenset(
    """
    arquivo arquivos pasta pastas diretorio diretorios repositorio repositorios
    repo script scripts comando comandos ferramenta ferramentas log logs
    tabela tabelas banco bancos api apis vps servidor servidores
    codigo commit commits branch deploy terminal shell
    minuto minutos hora horas segundo segundos dia dias semana semanas
    """.split()
)

# Caminho ou extensão de arquivo. Deliberadamente estreito pra não pegar o
# "e/ou" do português: exige caminho absoluto (`/algo`, `~/algo`), caminho com
# duas barras (`bot/memory/x`), ou extensão conhecida (`algo.py`).
_PADRAO_ARQUIVO = re.compile(
    r"(?:(?:^|\s)~?/[\w.-]+)"
    r"|(?:[\w.-]+/[\w.-]+/)"
    r"|(?:\b[\w.-]+\.(?:py|md|json|ya?ml|txt|sh|js|ts|csv|sql|log|env)\b)"
)
_PADRAO_NUMERO = re.compile(r"\d+")
_PADRAO_PALAVRA = re.compile(r"[a-z0-9]+")


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acento — pra 'diretório' casar com 'diretorio'."""
    sem_acento = unicodedata.normalize("NFKD", texto or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.lower()


def _tem_invencao(ack: str, pedido: str) -> tuple[bool, str]:
    """O ack introduziu fato que não está na mensagem do operador?

    Devolve `(rejeitar, motivo)`. Conservador de propósito: só pega sinal CLARO
    de fabricação. Um falso positivo custa o operador ver o texto fixo; um falso
    negativo custa uma invenção chegando como se fosse verdade.
    """
    ack_n, pedido_n = _normalizar(ack), _normalizar(pedido)

    if _PADRAO_ARQUIVO.search(ack_n) and not _PADRAO_ARQUIVO.search(pedido_n):
        return True, "caminho/extensão de arquivo inventado"

    numeros_pedido = set(_PADRAO_NUMERO.findall(pedido_n))
    for n in _PADRAO_NUMERO.findall(ack_n):
        if n not in numeros_pedido:
            return True, f"número inventado ({n})"

    palavras_pedido = set(_PADRAO_PALAVRA.findall(pedido_n))
    for termo in _PADRAO_PALAVRA.findall(ack_n):
        if termo in _TERMOS_TECNICOS and termo not in palavras_pedido:
            return True, f"termo técnico inventado ({termo})"

    return False, ""


# ── Escrita do ack ────────────────────────────────────────────────────────


async def _chamar_modelo(system: str, pedido: str) -> str:
    """Chama o modelo barato configurado. Levanta em qualquer problema — quem
    chama trata (o ack é GARANTIDO: erro vira fallback, nunca silêncio)."""
    provider = _env("LIVENESS_ACK_PROVIDER", _DEFAULT_PROVIDER).lower()
    model = _env("LIVENESS_ACK_MODEL", _DEFAULT_MODEL)

    if provider == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        resp = await client.messages.create(
            model=model,
            max_tokens=80,
            temperature=0.4,
            system=system,
            messages=[{"role": "user", "content": pedido}],
        )
        return "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )

    if provider != "openai":
        raise ValueError(f"LIVENESS_ACK_PROVIDER desconhecido: {provider!r}")

    from bot.conversation_detector import _get_openai

    resp = await _get_openai().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": pedido},
        ],
        temperature=0.4,
        max_tokens=80,
    )
    return resp.choices[0].message.content or ""


def _tem_credencial() -> bool:
    provider = _env("LIVENESS_ACK_PROVIDER", _DEFAULT_PROVIDER).lower()
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    return bool(os.environ.get("OPENAI_API_KEY"))


async def write_ack(intent_text: str, *, late: bool = False) -> str:
    """Escreve o LIV-ack semântico via modelo barato. Nunca levanta — em
    qualquer falha (ou invenção detectada) devolve o fallback consistente."""
    if not _tem_credencial():
        return fallback_ack(late=late)
    pedido = (intent_text or "")[:1500]
    try:
        text = (await _chamar_modelo(_SYS_LATE if late else _SYS_START, pedido))
        text = text.strip().strip('"').strip()
    except Exception as exc:  # noqa: BLE001 — ack nunca derruba o turno
        logger.warning("liveness: modelo do ack falhou (%s); fallback", exc)
        return fallback_ack(late=late)

    if not text:
        return fallback_ack(late=late)

    if _guard_enabled():
        rejeitar, motivo = _tem_invencao(text, pedido)
        if rejeitar:
            # Log com o texto rejeitado: é a única forma de o operador calibrar
            # o guarda-costas depois (se derrubar ack legítimo demais, desliga).
            logger.warning(
                "liveness: ack REJEITADO (%s) — %r; fallback", motivo, text
            )
            return fallback_ack(late=late)

    return text
