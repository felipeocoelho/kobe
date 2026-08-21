"""Gate de referência temporal na saída — MODO OBSERVAÇÃO.

O problema
──────────
O contrato (`CLAUDE.md`) já proíbe afirmar *quando* algo aconteceu sem ter
conferido: *"Nada relativo ao TEMPO sem conferir o tempo."* A regra vazou mesmo
assim, e o motivo é estrutural: **ela não tem gatilho**. Escrever um advérbio não
parece uma ação, então não dispara verificação nenhuma. E até aqui não existia
NENHUM passo entre o agente terminar de escrever e o operador receber — o texto
saía do `claude -p`, passava por `_resolve_claude` (que só trata erro/timeout),
levava formatação de markdown e ia pro Telegram. Linha direta. Todo o grounding
do Kobe vivia do lado da ENTRADA (`bot/memory/grounding.py`); a saída não tinha
nenhum.

Este módulo é o primeiro passo desse lado. Ele é **código, não instrução de
prompt** — instrução já existia e vazou.

O desenho: dois níveis, custo concentrado no que quase nunca acende
──────────────────────────────────────────────────────────────────
**Nível 1 — sempre ligado, microssegundos.** Varredura determinística por
marcadores retrospectivos (regex compilada uma vez no import). Se nada casa, o
gate devolve `None` e o turno segue: zero latência adicional. Essa é a maioria
esmagadora dos turnos, e é onde mora a garantia de que o gate não custa nada.

**Nível 1.5 — o filtro de âncora, também determinístico.** A frase já traz o dado
absoluto ao lado da referência relativa ("no ar desde 14/07 **às 23:03**", "última
atividade **às 14:09 UTC** — uns 5 min atrás")? Então a relativa é uma glosa de
algo verificável que está ali, não o "desde ontem" seco que parece preciso e não
é. Medido em 50 trechos classificados à mão: **com âncora, "não mexer" é a decisão
certa em 89% dos casos; sem âncora, em 17%.** Esse filtro sozinho derruba 40% dos
acendimentos, e é o melhor sinal que apareceu em toda a investigação — melhor que
qualquer modelo testado (ver abaixo).

**Nível 2 — só quando o nível 1 acende: (a) determinístico, custo ~zero.** A
pergunta que importa não é *"isto é uma afirmação temporal?"* (o nível 1 já
responde) e sim *"isto tem LASTRO?"* — o turno tocou alguma fonte que verifica
tempo/estado (`git log`, `stat`, `systemctl`, leitura do arquivo citado, agenda)?
Se tocou, a afirmação tem base plausível e passa. Se o turno inteiro não tocou
fonte nenhuma e mesmo assim diz "desde ontem", é confabulação quase certa.

Por que NÃO existe corretor aqui — e por que não vai existir
────────────────────────────────────────────────────────────
O gate NUNCA altera a resposta. Isso não é uma fase preguiçosa: um corretor
automático foi projetado, medido em duas formas independentes, e **reprovado nas
duas**. O operador vetou o desenho depois de ver os números.

**(1) Corretor por modelo.** 50 referências reais classificadas à mão em
manter/remover/trocar, e a mesma decisão pedida a um modelo, recebendo só a
frase (payload mínimo, três saídas):

    gpt-4o-mini : 38% de concordância · p50 600 ms · p95 938 ms
    gpt-4o      : 36% de concordância · p50 696 ms · p95 947 ms
    "nunca mexer" (não fazer nada): 56%

Os dois modelos perdem para não fazer nada, e o modelo caro não é melhor que o
barato — sinal de que a tarefa é **subdeterminada**, não de que o modelo é fraco.
Pior: numa primeira versão o modelo INVENTOU datas ("do dia 25 de outubro de
2023") para ancorar frases que não tinha como conhecer — o remédio virando a
doença. O motivo é estrutural: para decidir é preciso saber se o turno conferiu
alguma fonte e qual é o fato verdadeiro, e **nada disso está na frase**. Qualquer
corretor fora do turno está cego por construção, a qualquer preço.

**(2) Corretor determinístico (apagar o trecho).** Cobre 30% dos casos e o texto
sai fluente, mas erra calado: *"rodando desde 23:03 de ontem"* vira *"rodando
desde 23:03"* — perdeu o dia, destruiu o lastro que existia. Erro fluente é o
pior tipo, porque chega ao operador sem sinal de que algo se perdeu.

**A prevenção é que faz o trabalho.** A regra "Referência temporal só sai com
âncora — ou não sai" no `CLAUDE.md` age no único ponto do sistema que TEM a
informação: quem está escrevendo a frase. Este módulo é o **instrumento que mede
se a regra funcionou** (taxa de `grounded=false` antes e depois), não o corretor.

Se alguém for reabrir a porta do corretor: **refaça a medição antes.** Os números
acima são de 2026-08.

Números medidos (corpus: 1.644 respostas reais, mai→ago/2026)
─────────────────────────────────────────────────────────────
    acendimento do nível 1 .................. 9,9% dos turnos
    custo do nível 1 (caminho comum) ........ 140 µs por resposta
    custo do nível 2 (a) .................... leitura de um bool
    latência esperada por turno ............. ~0,15 ms

Fase atual: OBSERVAÇÃO
──────────────────────
O gate **só loga** o que teria pego. Não altera a resposta, não anexa ressalva,
não devolve nada ao agente. É o que permite calibrar em produção real com risco
zero antes de deixá-lo agir — e transformar a estimativa ("~3% dos turnos
afirmariam tempo sem fonte") em número medido. Deixar o gate AGIR é uma decisão
separada do operador, depois de ver esses números.

Cobertura: isto é uma REDE, não um muro
───────────────────────────────────────
Regex sobre linguagem natural aberta tem recall finito. Perífrase, construção
nova e afirmação temporal implícita sem marcador passam batido. A lista mora em
`bot/temporal_markers.toml`, legível e editável, justamente pra apertar a malha
quando um vazamento concreto aparecer.

Flag: `TEMPORAL_GATE_ENABLED` (default **false**). Rollback = flag off +
restart, sem tocar em código.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


logger = logging.getLogger("kobe.temporal_gate")

_MARKERS_PATH = Path(__file__).with_name("temporal_markers.toml")

# Contexto (em caracteres) de cada lado do marcador no trecho que vai pro log.
_SNIPPET_PAD = 70
_SNIPPET_MAX = 200


@dataclass(frozen=True)
class Report:
    """O que o gate viu num turno aceso. NUNCA carrega texto alterado — nesta
    fase o gate é read-only sobre a resposta, por construção."""

    markers: tuple[str, ...]
    """Trechos que casaram, já descontadas as máscaras (código/citação/ack)."""

    grounded: bool
    """O turno tocou alguma fonte temporal (nível 2a)."""

    snippet: str
    """Um trecho com contexto ao redor do primeiro marcador, pro log."""

    action: str = "observe"
    """Sempre "observe" nesta fase — o gate não age na resposta."""

    anchored_dropped: int = 0
    """Quantas referências o filtro de âncora descartou nesta resposta (a frase
    já trazia o dado absoluto ao lado). Vai pro log: é a medida de quanto ruído
    o filtro está tirando do que você conta."""


# ── Carga da configuração (uma vez, no import) ─────────────────────────────
# Compilar aqui e não por chamada é o que garante o custo de microssegundos do
# nível 1. Um TOML quebrado NÃO derruba o bot: loga ERROR alto e desliga o gate
# (o pior caso vira o comportamento de hoje, não uma indisponibilidade).


def _compile(patterns: list) -> Optional[re.Pattern]:
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def _load() -> dict:
    with _MARKERS_PATH.open("rb") as fh:
        raw = tomllib.load(fh)
    gate = raw.get("gate") or {}
    fontes = raw.get("fontes") or {}
    ancoras = raw.get("ancoras") or {}
    return {
        "markers": _compile(gate.get("marcadores") or []),
        "acks": _compile(gate.get("acks") or []),
        "ancoras": _compile(ancoras.get("padroes") or []),
        "tools": {t for t in (fontes.get("tools") or [])},
        "tools_contendo": tuple(
            t.lower() for t in (fontes.get("tools_contendo") or [])
        ),
        "bash_contendo": tuple(
            c.lower() for c in (fontes.get("bash_contendo") or [])
        ),
    }


try:
    _CFG = _load()
    _LOAD_ERROR: Optional[str] = None
except Exception as exc:  # noqa: BLE001 — config quebrada nunca derruba o bot
    _CFG = {"markers": None, "acks": None, "ancoras": None, "tools": set(),
            "tools_contendo": (), "bash_contendo": ()}
    _LOAD_ERROR = repr(exc)
    logger.error(
        "temporal_gate: %s ilegível (%s) — gate DESLIGADO nesta execução",
        _MARKERS_PATH, _LOAD_ERROR,
    )

# Regiões que o gate nunca enxerga. Compiladas aqui pelo mesmo motivo.
_FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_QUOTE = re.compile(r"^\s*>.*$", re.MULTILINE)
# Trecho curto entre aspas: cobre dois casos de uma vez, os dois legítimos.
# (1) MENÇÃO da palavra em vez de uso — o falso positivo real do corpus:
#     «quando você falar referência temporal ("essa semana", "amanhã", "ontem")».
# (2) CITAÇÃO curta de fala do operador ou de terceiro, que o gate não deve
#     auditar porque não é afirmação do agente.
# Teto de 40 caracteres de propósito: aspas longas normalmente são prosa do
# próprio agente e continuam sob o gate.
# Dano colateral medido no corpus (1.644 respostas): a máscara descarta 8 de 170
# candidatos, todos por aspas duplas/curvas — o ramo do apóstrofo, que era o
# suspeito de engolir prosa legítima ("d'água"), descarta ZERO.
_SHORT_QUOTE = re.compile(
    r'"[^"\n]{1,40}"' r"|'[^'\n]{1,40}'" r"|[“][^”\n]{1,40}[”]"
)


def enabled() -> bool:
    """Flag mestra. Default **false** — com ela off, o caminho de entrega da
    resposta é bit-a-bit o de antes deste módulo existir."""
    if _CFG["markers"] is None:  # config quebrada → nunca liga
        return False
    return (os.getenv("TEMPORAL_GATE_ENABLED") or "").strip().lower() in (
        "1", "true", "on", "yes", "sim",
    )


# ── Nível 2 (a): o turno tocou alguma fonte temporal? ──────────────────────


def is_temporal_source(tool_name: str, bash_command: str = "") -> bool:
    """True se esta tool_use consultou uma fonte de tempo/estado datado.

    Chamada de dentro do laço de `tool_use` que os contadores do turno já
    percorrem (`ProgressReporter.on_event`, `_ToolCounter.on_event`) — é o mesmo
    lugar onde a detecção de ack já lê o comando do Bash hoje. Custo: um lookup
    em set + algumas comparações de substring.
    """
    if not tool_name:
        return False
    if tool_name in _CFG["tools"]:
        return True
    low = tool_name.lower()
    if any(frag in low for frag in _CFG["tools_contendo"]):
        return True
    if tool_name == "Bash" and bash_command:
        cmd = bash_command.lower()
        return any(frag in cmd for frag in _CFG["bash_contendo"])
    return False


# ── Nível 1 ────────────────────────────────────────────────────────────────


def _masked_spans(text: str) -> list[tuple[int, int]]:
    """Intervalos que o gate ignora: bloco de código, código inline, linha de
    citação (é assim que fala do operador e histórico aparecem na resposta),
    trecho curto entre aspas (menção/citação) e frase de ack. Só é calculado
    quando JÁ houve candidato — nos ~90% de turnos em que nada casa, não se
    paga por isto."""
    spans: list[tuple[int, int]] = []
    for rx in (_FENCE, _INLINE_CODE, _QUOTE, _SHORT_QUOTE):
        spans += [m.span() for m in rx.finditer(text)]
    acks = _CFG["acks"]
    if acks is not None:
        spans += [m.span() for m in acks.finditer(text)]
    return spans


# Fronteira de frase. Quebra de linha e marcador de lista contam como fronteira:
# num texto de chat, "- item" é uma unidade tanto quanto uma oração.
_SENT_SPLIT = re.compile(r"(?<=[.!?:;])\s+|\n+")


def _sentence_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Limites da frase que contém [start, end). É a JANELA do filtro de âncora:
    o escopo tem que ser a frase, não a resposta — uma resposta longa pode ter
    uma frase ancorada ("no ar desde 14/07 às 23:03") e outra solta ("desde
    ontem"), e só a solta deve acender."""
    ini = 0
    for m in _SENT_SPLIT.finditer(text, 0, start):
        ini = m.end()
    m = _SENT_SPLIT.search(text, end)
    return ini, (m.start() if m else len(text))


def _is_anchored(text: str, start: int, end: int) -> bool:
    """A frase já traz o ancoradouro absoluto ao lado da referência relativa?

    Se traz, a referência é uma GLOSA de um dado verificável que está ali — não
    é o "desde ontem" seco que parece preciso e não é. Medido: com âncora, "não
    mexer" é a decisão certa em 89% dos casos; sem âncora, em 17%.
    """
    rx = _CFG["ancoras"]
    if rx is None:
        return False
    ini, fim = _sentence_span(text, start, end)
    frase = text[ini:fim]
    # A própria referência não conta como âncora de si mesma: "há 2 dias" casaria
    # o padrão de hora se não recortássemos o trecho fora da janela.
    frase = frase[:start - ini] + " " + frase[end - ini:]
    return rx.search(frase) is not None


def _snippet(text: str, start: int, end: int) -> str:
    a = max(0, start - _SNIPPET_PAD)
    frag = text[a:end + _SNIPPET_PAD]
    frag = " ".join(frag.split())  # uma linha só, pro log
    return frag[:_SNIPPET_MAX]


def scan(text: str, *, touched_temporal_source: bool) -> Optional[Report]:
    """Nível 1 + nível 2 (a). Devolve `None` quando nada acende — que é o
    caminho comum e o mais barato possível: uma varredura sem captura.

    NÃO altera `text` (nem poderia: recebe e devolve leitura). Nesta fase o gate
    é observador puro.
    """
    rx = _CFG["markers"]
    if rx is None or not text:
        return None

    # Casa PRIMEIRO no texto cru — uma passada. Só monta a máscara se houve
    # candidato (medido: 145 µs vs 217 µs por resposta no caminho comum).
    cands = list(rx.finditer(text))
    if not cands:
        return None

    spans = _masked_spans(text)
    visiveis = [m for m in cands
                if not any(a <= m.start() < b for a, b in spans)]
    if not visiveis:
        return None

    # Filtro de âncora: a frase que já carrega o dado absoluto não acende.
    hits, ancorados = [], 0
    for m in visiveis:
        if _is_anchored(text, m.start(), m.end()):
            ancorados += 1
        else:
            hits.append(m)
    if not hits:
        return None

    first = hits[0]
    return Report(
        markers=tuple(m.group(0) for m in hits),
        grounded=bool(touched_temporal_source),
        snippet=_snippet(text, first.start(), first.end()),
        anchored_dropped=ancorados,
    )


def observe(text: str, *, touched_temporal_source: bool) -> Optional[Report]:
    """`scan` + registro no log. **Não toca a resposta** — é a fase de observação.

    Duas severidades, de propósito: um turno que afirma tempo E consultou fonte
    é rotina (INFO); um que afirma tempo sem ter tocado fonte nenhuma é o caso
    que o gate existe pra pegar (WARNING) — e é ele que o operador vai contar no
    log pra decidir se o gate passa a agir.
    """
    report = scan(text, touched_temporal_source=touched_temporal_source)
    if report is None:
        return None
    log = logger.info if report.grounded else logger.warning
    log(
        'temporal_gate marked=%d anchored_dropped=%d grounded=%s action=%s '
        'markers=%s snippet="%s"',
        len(report.markers),
        report.anchored_dropped,
        report.grounded,
        report.action,
        list(report.markers[:6]),
        report.snippet,
    )
    return report
