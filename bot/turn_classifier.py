"""Classificador de turno — decide foreground vs background na ENTRADA.

Roda ANTES de segurar o lock do tópico pra rodar o Claude. Se prevê que o
pedido vai gerar um turno pesado (editar código, varrer repo, análise longa,
processar arquivos/URLs), devolve `background` — o handler despacha o
`claude -p` fora do lock e a linha fica livre pro próximo pedido.

Formação em cascata (do mais barato pro mais caro), fechada com o Felipe em
2026-06-04 (`user-data/knowledge/kobe/brainstorms/despacho-turno-pesado-background.md`):

1. **1ª fileira — roteamento por tipo (custo ~0):** slash de plugin pesado
   (`/transcrever*`, `/imagem*`…) → background na hora; slash de memória/gestão
   (`/contexto`, `/nova`, `/conversa*`, `/alerta_*`…) → foreground na hora.
2. **2ª fileira — sinais estruturais (custo ~0, somam no placar):** URLs,
   caminhos de arquivo/extensões, "o repo"/"todos os"/"cada", multi-etapa,
   tamanho do texto (proxy fraco).
3. **3ª fileira — catálogo léxico de intenção pesada (custo ~0, somam no
   placar):** *codifica, implementa, refatora, varre, audita, migra*…
4. **Rei — GPT-4o-mini (custo ~1s, SÓ na zona cinza):** placar entre os cortes
   LOW e HIGH acorda o mini pra desempatar. Fora da cota do plano Max.

A retaguarda (teto de tempo que promove turnos foreground que estouram X
segundos) NÃO mora aqui — é do handler, que é quem segura o lock. Aqui é só a
previsão de entrada. Mesmo errando, o erro de previsão é barato: pesado não
detectado → a retaguarda pega; leve marcado pesado → resposta vira assíncrona
sem precisar (custo de UX pequeno).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Optional


logger = logging.getLogger("kobe.classifier")

ROUTE_FOREGROUND = "foreground"
ROUTE_BACKGROUND = "background"

MINI_MODEL = "gpt-4o-mini"


@dataclass(frozen=True)
class TurnDecision:
    """Resultado da classificação. `route` é o que o handler consome.

    `score` e `reason` existem pra log/diagnóstico — calibrar os cortes no
    `.env` exige saber por que cada turno caiu onde caiu.
    """

    route: str
    reason: str
    score: int = 0
    used_mini: bool = False


# ── 1ª fileira — roteamento por tipo ──────────────────────────────────────

# Slashes de plugin que SEMPRE geram trabalho pesado. O turno inteiro (Hal
# lendo o pedido + delegando ao subagente) é longo, então despachamos já.
# Heurístico e extensível — a retaguarda cobre o que escapar daqui.
HEAVY_SLASH_PREFIXES: tuple[str, ...] = (
    "/transcrever",
    "/imagem",
    "/monet",
)

# Slashes de memória/gestão: resolvem rápido, sem trabalho pesado. Vão
# foreground na hora mesmo que o texto que os acompanha tenha sinais
# estruturais (ex: `/salvar Auditoria do repo todo`). Cobre também os
# CommandHandler do core que, por algum caminho, caiam aqui.
LIGHT_SLASH_PREFIXES: tuple[str, ...] = (
    "/contexto",
    "/nova",
    "/salvar",
    "/retomar",
    "/conversa",          # cobre /conversa, /conversas_topico, /conversas_global
    "/renomear",
    "/alerta",            # cobre /alerta_lista, /alerta_pausar, etc.
    "/missao_status",
    "/missao_lista",
    "/missao_abortar",
    "/contatos",
    "/whatsapp",
    "/handoff",
    "/start",
    "/help",
    "/ajuda",
)


# ── 2ª fileira — sinais estruturais ───────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
# Caminho de arquivo (tem barra com segmento não-URL) ou extensão de código/doc.
_PATH_RE = re.compile(r"\b[\w./-]+/[\w./-]+")
_EXT_RE = re.compile(
    r"\.(py|js|ts|tsx|jsx|go|rs|java|rb|sh|sql|md|json|ya?ml|toml|html|css|"
    r"c|cpp|h|hpp|txt|csv|pdf|docx?|xlsx?)\b",
    re.IGNORECASE,
)
# Sinais de varredura ampla — "o repo", "todos os arquivos", "cada", "tudo".
_SCAN_RE = re.compile(
    r"\b(o\s+repo(sitório)?|todos?\s+os|todas?\s+as|cada\s+\w+|"
    r"o\s+projeto\s+inteiro|a\s+base\s+de\s+código|codebase|"
    r"o\s+código\s+todo|arquivo\s+por\s+arquivo)\b",
    re.IGNORECASE,
)
# Multi-etapa: lista numerada (1. / 1) / passo) ou sequenciadores.
_NUMBERED_RE = re.compile(r"(^|\n)\s*\d+[\.\)]\s+\S", re.MULTILINE)
_SEQUENCER_RE = re.compile(
    r"\b(primeiro\b.*\bdepois\b|em\s+seguida|por\s+fim|por\s+último|"
    r"e\s+(também|aí)\s+\w+)\b",
    re.IGNORECASE | re.DOTALL,
)


# ── 3ª fileira — catálogo léxico de intenção pesada ───────────────────────

# Verbos/expressões que sinalizam trabalho pesado do próprio Hal. Pesos
# deliberadamente parecidos: o placar soma, os cortes no .env calibram.
_HEAVY_LEXICON: tuple[str, ...] = (
    "codifica", "codific", "implementa", "implement", "refatora", "refator",
    "reescreve", "reescrev", "varre", "varrer", "audita", "auditoria",
    "audit", "migra", "migrar", "migração", "investiga", "investig",
    "analisa o", "analise o", "análise do", "analisar o",
    "cria um projeto", "criar um projeto", "monta um projeto",
    "faz um script", "escreve um script", "cria um script",
    "builda", "build ", "deploy", "compila", "roda os testes",
    "corrige o bug", "conserta", "debugga", "depura",
    "revisa o código", "code review", "review do", "otimiza",
    "processa", "transcreve", "transcrev", "gera a imagem", "gera uma imagem",
)


def _structural_score(text: str) -> tuple[int, list[str]]:
    """Pontos da 2ª fileira. Retorna (score, lista de sinais p/ log)."""
    score = 0
    signals: list[str] = []

    urls = _URL_RE.findall(text)
    if urls:
        # 1 URL já é sinal médio (provável fetch/transcrição); cada extra soma.
        pts = 2 + (len(urls) - 1)
        score += pts
        signals.append(f"url x{len(urls)}(+{pts})")

    # Caminho de arquivo: ignora os que são pedaço de URL (já contados).
    text_wo_urls = _URL_RE.sub(" ", text)
    if _PATH_RE.search(text_wo_urls):
        score += 3
        signals.append("path(+3)")
    elif _EXT_RE.search(text_wo_urls):
        # Extensão solta sem caminho ("manda o x.py") ainda é sinal, menor.
        score += 2
        signals.append("ext(+2)")

    if _SCAN_RE.search(text):
        score += 3
        signals.append("scan(+3)")

    if _NUMBERED_RE.search(text) or _SEQUENCER_RE.search(text):
        score += 2
        signals.append("multi-etapa(+2)")

    # Tamanho do texto — proxy fraco, peso baixo.
    n = len(text)
    if n > 800:
        score += 2
        signals.append("len>800(+2)")
    elif n > 400:
        score += 1
        signals.append("len>400(+1)")

    return score, signals


def _lexical_score(text: str) -> tuple[int, list[str]]:
    """Pontos da 3ª fileira (catálogo léxico). +2 por termo casado, teto 6."""
    lowered = text.lower()
    hits = [term.strip() for term in _HEAVY_LEXICON if term in lowered]
    if not hits:
        return 0, []
    # Teto pra um texto não disparar background só por repetir sinônimos.
    score = min(len(hits) * 2, 6)
    return score, [f"lex:{hits[0]!r}+{len(hits)}x(+{score})"]


def score_turn(text: str) -> tuple[int, list[str]]:
    """Placar combinado das 2ª+3ª fileiras (puro, sem rede). Para testes."""
    s_struct, sig_struct = _structural_score(text)
    s_lex, sig_lex = _lexical_score(text)
    return s_struct + s_lex, sig_struct + sig_lex


def route_by_type(text: str, *, has_attachment: bool = False) -> Optional[str]:
    """1ª fileira: roteamento determinístico por tipo. None = inconclusivo.

    Anexo num turno de TEXTO é raro (anexos vão pro on_document), mas se vier
    junto de caption pesada, conta como sinal forte → background.
    """
    stripped = text.lstrip()
    if stripped.startswith("/"):
        # Primeiro token = o comando (sem args), case-insensitive.
        cmd = stripped.split(maxsplit=1)[0].lower()
        # Remove sufixo @botname que o Telegram anexa em grupos.
        cmd = cmd.split("@", 1)[0]
        for prefix in HEAVY_SLASH_PREFIXES:
            if cmd.startswith(prefix):
                return ROUTE_BACKGROUND
        for prefix in LIGHT_SLASH_PREFIXES:
            if cmd.startswith(prefix):
                return ROUTE_FOREGROUND
        # Slash desconhecido (ex: /algumacoisa): não decide aqui, cai no placar.
    return None


async def _ask_mini(text: str) -> Optional[str]:
    """Zona cinza: GPT-4o-mini decide PESADO vs LEVE. None se indisponível.

    Reusa o client OpenAI do detector de conversa (mesma OPENAI_API_KEY, fora
    da cota do plano Max). Em qualquer falha retorna None — o caller aplica o
    default conservador (foreground: nunca prende a linha à toa por erro do mini).
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        from bot.conversation_detector import _get_openai

        resp = await _get_openai().chat.completions.create(
            model=MINI_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você tria pedidos pra um assistente que atende por chat e "
                        "tem uma equipe pra trabalho pesado. Classifique o pedido do "
                        "operador em PESADO ou LEVE.\n"
                        "PESADO = vai exigir trabalho demorado: editar/escrever código, "
                        "varrer ou auditar um repositório, análise longa, processar "
                        "arquivos ou URLs, gerar/transcrever mídia, migração, build/deploy.\n"
                        "LEVE = pergunta, papo, confirmação, consulta rápida, ajuste "
                        "pequeno, comando de memória/gestão.\n"
                        "Na dúvida responda LEVE. Responda APENAS com PESADO ou LEVE."
                    ),
                },
                {"role": "user", "content": text[:1500]},
            ],
            temperature=0.0,
            max_tokens=3,
        )
        answer = (resp.choices[0].message.content or "").strip().upper()
    except Exception as exc:  # noqa: BLE001 — mini nunca derruba o turno
        logger.warning("classifier: mini falhou (%s); default foreground", exc)
        return None
    return answer


async def classify_turn(
    text: str,
    *,
    score_high: int,
    score_low: int,
    has_attachment: bool = False,
) -> TurnDecision:
    """Roda a cascata inteira e devolve a decisão de roteamento.

    `score_high`/`score_low` vêm do `.env` (cortes calibráveis). A zona cinza
    (score_low < placar < score_high) é a única que paga o mini (~1s).
    """
    # 1ª fileira — roteamento por tipo.
    typed = route_by_type(text, has_attachment=has_attachment)
    if typed is not None:
        return TurnDecision(route=typed, reason="type-routing", score=0)

    # 2ª + 3ª fileiras — placar.
    score, signals = score_turn(text)
    if has_attachment:
        score += 3
        signals.append("attach(+3)")
    sig_str = ",".join(signals) or "nenhum"

    if score >= score_high:
        return TurnDecision(
            route=ROUTE_BACKGROUND,
            reason=f"score>={score_high} [{sig_str}]",
            score=score,
        )
    if score <= score_low:
        return TurnDecision(
            route=ROUTE_FOREGROUND,
            reason=f"score<={score_low} [{sig_str}]",
            score=score,
        )

    # Rei — zona cinza: acorda o mini.
    answer = await _ask_mini(text)
    if answer == "PESADO":
        route = ROUTE_BACKGROUND
    else:
        # LEVE, ou mini indisponível/erro → foreground (default conservador:
        # nunca empurra pro assíncrono à toa; a retaguarda cobre o erro).
        route = ROUTE_FOREGROUND
    return TurnDecision(
        route=route,
        reason=f"gray→mini={answer or 'indisponível'} [{sig_str}]",
        score=score,
        used_mini=True,
    )
