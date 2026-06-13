#!/usr/bin/env python3
"""Calibração dos 3 botões da detecção de borda do New Chat Manager (doc §7).

NÃO PULAR (ordem do doc). Roda o algoritmo PURO (`detect_segments`) contra
um corpus rotulado à mão, varre uma grade de knobs e escolhe o conjunto que
satisfaz todos os casos. Escreve relatório em docs/chat-manager/.

Casos rotulados (doc §7):
  A — este papo de arquitetura: assunto grosso único, vários beats finos →
      NÃO pode virar N conversas (esperado: 1 segmento, 0 cortes).
  B — /flow_lista → "Kobe" (resposta curta): NÃO pode cortar (1 segmento).
  C — Dev Kobe (técnico) → Olimpo (estratégia): tópicos/assuntos bem
      diferentes → DEVE cortar (2 segmentos, 1 corte).
  D — dentro de Dev Kobe, redesenho do chat manager → bug do atrus: assuntos
      grossos diferentes no mesmo tópico → DEVE cortar (2 segmentos).

Uso: .venv/bin/python infra/calibrate_chat_manager.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.embedding import embed  # noqa: E402
from bot.chat_manager.classifier import CMsg, Knobs, detect_segments  # noqa: E402


@dataclass
class Case:
    name: str
    desc: str
    messages: list[tuple[str, str]]  # (role, content)
    expected_segments: int


# ---------------------------------------------------------------------------
# Corpus rotulado
# ---------------------------------------------------------------------------

CASES: list[Case] = [
    Case(
        name="A_arquitetura_unica",
        desc="Papo de arquitetura: 1 assunto grosso, muitos beats finos → NÃO cortar",
        expected_segments=1,
        messages=[
            ("user", "vamos redesenhar o chat manager pra matar a latência e a granularidade macro"),
            ("assistant", "boa. o problema central é o detector rodar no caminho crítico do turno."),
            ("user", "isso. o detector síncrono é a fornalha de latência, tem que sair do turno"),
            ("user", "a ideia é o turno ser burro e rápido, e a inteligência cara rodar atrás, assíncrona"),
            ("assistant", "perfeito. async vira lei."),
            ("user", "o prompt passa a ser montado em quatro camadas: imediato, quente, frio e índice de relações"),
            ("user", "o imediato é os últimos dois minutos de conversa deste tópico, sempre, lido do disco"),
            ("user", "o quente é o assunto corrente inteiro, do marco de início até agora, puxado sob demanda"),
            ("user", "o frio é o catálogo dos assuntos passados, com busca vetorial quando precisar"),
            ("user", "e o classificador-bibliotecário mantém os ponteiros, não resumos — ponteiro endereça, não perde"),
            ("user", "a detecção de borda corta no assunto grosso, contra o acumulado, com histerese e voto ponderado"),
            ("user", "tudo atrás de feature flag, com migration aditiva e fase de calibração antes de ligar"),
        ],
    ),
    Case(
        name="B_resposta_curta",
        desc="Menu 'Flow ou Kobe?' → 'Kobe' (resposta curta) → NÃO cortar",
        expected_segments=1,
        messages=[
            ("user", "me lista os projetos que estão no flow agora"),
            ("assistant", "Você tem dois projetos ativos no Flow. Quer que eu liste as tarefas de qual deles — Flow ou Kobe?"),
            ("user", "Kobe"),
            ("assistant", "Beleza, aqui estão as tarefas do Kobe no Flow..."),
            ("user", "ok pode detalhar a primeira"),
        ],
    ),
    Case(
        name="C_devkobe_para_olimpo",
        desc="Dev Kobe (técnico) → Olimpo (estratégia de lançamento) → DEVE cortar",
        expected_segments=2,
        messages=[
            ("user", "preciso revisar o schema do banco do kobe, a tabela de mensagens"),
            ("assistant", "claro, o que você quer ajustar no schema?"),
            ("user", "falta um índice ivfflat na coluna de embedding pra busca vetorial escalar"),
            ("user", "e o detector de conversa está lento no caminho crítico, paga embedding antes de responder"),
            ("user", "agora muda completamente de assunto: vamos falar da estratégia do lançamento do Olimpo"),
            ("user", "preciso definir a oferta e o público do próximo lançamento do programa de mentoria"),
            ("user", "qual ângulo de captação converte melhor pra esse avatar de empreendedor"),
        ],
    ),
    Case(
        name="D_chatmanager_para_atrus",
        desc="Dentro de Dev Kobe: redesenho do chat manager → bug do atrus → DEVE cortar",
        expected_segments=2,
        messages=[
            ("user", "o redesenho do chat manager precisa tirar o detector do caminho crítico do turno"),
            ("assistant", "concordo, o detector síncrono é o gargalo."),
            ("user", "as quatro camadas são imediato, quente, frio e relações, com o classificador rodando atrás"),
            ("user", "o classificador vira um ofício novo dentro do keyko, disparado por debounce de silêncio"),
            ("user", "deixa o chat manager de lado agora, tem um bug sério no atrus que preciso resolver"),
            ("user", "o atrus está falhando ao despachar a missão, sai com exit code 1 e não notifica"),
            ("user", "o job fica preso no estado dispatched e o operador não recebe nenhum aviso de erro"),
        ],
    ),
]


# Grade de knobs a varrer.
BORDER_GRID = [0.30, 0.35, 0.38, 0.40, 0.42, 0.45, 0.48, 0.50]
SUSTAIN_GRID = [2, 3]
COHERENCE_GRID = [0.30, 0.35, 0.40]


async def _embed_cases() -> dict[str, list[CMsg]]:
    """Embeda as msgs de operador de cada caso (assistant não embeda — espelha
    produção, onde só msgs do operador entram no centroide/voto)."""
    out: dict[str, list[CMsg]] = {}
    for case in CASES:
        cmsgs: list[CMsg] = []
        for i, (role, content) in enumerate(case.messages):
            emb = await embed(content) if role == "user" else None
            cmsgs.append(CMsg(id=f"{case.name}-{i}", role=role, content=content, embedding=emb))
        out[case.name] = cmsgs
    return out


def _segments_for(cmsgs: list[CMsg], knobs: Knobs) -> int:
    plan = detect_segments(cmsgs, None, knobs)
    return len(plan.segments)


def _evaluate(embedded: dict[str, list[CMsg]], knobs: Knobs) -> tuple[int, dict]:
    """Retorna (acertos, detalhe-por-caso)."""
    hits = 0
    detail = {}
    for case in CASES:
        got = _segments_for(embedded[case.name], knobs)
        ok = got == case.expected_segments
        hits += int(ok)
        detail[case.name] = {"expected": case.expected_segments, "got": got, "ok": ok}
    return hits, detail


def main() -> int:
    embedded = asyncio.run(_embed_cases())

    # Varre a grade; coleta combos que acertam todos os 4 casos.
    winners: list[tuple[Knobs, dict]] = []
    all_results: list[tuple[Knobs, int, dict]] = []
    for b in BORDER_GRID:
        for s in SUSTAIN_GRID:
            for c in COHERENCE_GRID:
                k = Knobs(border_sim_threshold=b, sustain_length=s, cluster_coherence=c)
                hits, detail = _evaluate(embedded, k)
                all_results.append((k, hits, detail))
                if hits == len(CASES):
                    winners.append((k, detail))

    # Escolha: entre os vencedores, prefere sustain_length=2 (mais responsivo),
    # border_sim mais central (robustez), coherence mediana.
    chosen = None
    if winners:
        def score(item):
            k, _ = item
            # mais perto de border=0.42, sustain=2, coherence=0.35
            return (
                abs(k.border_sim_threshold - 0.42)
                + abs(k.sustain_length - 2) * 0.1
                + abs(k.cluster_coherence - 0.35)
            )
        winners.sort(key=score)
        chosen = winners[0]

    # ---- relatório ----
    lines: list[str] = []
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines.append("# Calibração do New Chat Manager — detecção de borda")
    lines.append("")
    lines.append(f"Gerado: {now}")
    lines.append("")
    lines.append("Método: algoritmo puro `detect_segments` contra corpus rotulado à")
    lines.append("mão (doc §7), varrendo grade de knobs. Embeddings reais via")
    lines.append("text-embedding-3-small. 1 segmento = 0 cortes; 2 = 1 corte.")
    lines.append("")
    lines.append("## Casos do corpus")
    for case in CASES:
        lines.append(f"- **{case.name}** — {case.desc} (esperado: {case.expected_segments} seg)")
    lines.append("")

    if chosen:
        k, detail = chosen
        lines.append("## Knobs escolhidos")
        lines.append("")
        lines.append(f"- `CM_BORDER_SIM` = **{k.border_sim_threshold}**")
        lines.append(f"- `CM_SUSTAIN` = **{k.sustain_length}**")
        lines.append(f"- `CM_CLUSTER_COHERENCE` = **{k.cluster_coherence}**")
        lines.append(f"- `CM_INFO_MIN_WORDS` = {k.info_min_words}, `CM_INFO_MIN_CHARS` = {k.info_min_chars} (default)")
        lines.append("")
        lines.append("Resultado por caso nos knobs escolhidos:")
        lines.append("")
        lines.append("| Caso | Esperado | Obtido | OK |")
        lines.append("|---|---|---|---|")
        for case in CASES:
            d = detail[case.name]
            lines.append(f"| {case.name} | {d['expected']} | {d['got']} | {'✅' if d['ok'] else '❌'} |")
        lines.append("")
        lines.append(f"Total de combinações que acertaram todos os {len(CASES)} casos: "
                     f"**{len(winners)}** (de {len(all_results)} testadas).")
    else:
        lines.append("## ⚠️ NENHUM conjunto de knobs acertou todos os casos")
        lines.append("")
        lines.append("Melhores combinações (por acertos):")
        all_results.sort(key=lambda x: x[1], reverse=True)
        lines.append("")
        lines.append("| border | sustain | coherence | acertos | detalhe |")
        lines.append("|---|---|---|---|---|")
        for k, hits, detail in all_results[:12]:
            dd = " ".join(f"{n.split('_')[0]}:{d['got']}/{d['expected']}"
                          for n, d in detail.items())
            lines.append(f"| {k.border_sim_threshold} | {k.sustain_length} | "
                         f"{k.cluster_coherence} | {hits}/{len(CASES)} | {dd} |")

    lines.append("")
    lines.append("## Nota sobre ruído de transcrição (áudio)")
    lines.append("")
    lines.append("Áudio transcrito mete ruído no vetor. O voto ponderado por")
    lines.append("informação (piso de palavras/chars) atenua: respostas curtas e")
    lines.append("vagas não votam abertura. Em produção, a flag `CM_*` permite")
    lines.append("recalibrar sem deploy de código — só editar .env e reiniciar o keyko.")

    report = "\n".join(lines)
    out_dir = Path(__file__).parent.parent / "docs" / "chat-manager"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "calibracao-2026-06-01.md"
    out_path.write_text(report + "\n", encoding="utf-8")

    # ---- stdout ----
    print(report)
    print(f"\n--- relatório salvo em {out_path} ---")
    if not chosen:
        print("FALHA: nenhum knob satisfez todos os casos — revisar corpus/algoritmo.")
        return 1
    k, _ = chosen
    print(f"\nKNOBS ESCOLHIDOS: CM_BORDER_SIM={k.border_sim_threshold} "
          f"CM_SUSTAIN={k.sustain_length} CM_CLUSTER_COHERENCE={k.cluster_coherence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
