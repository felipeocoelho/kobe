"""Classificação retroativa das sessions existentes em conversations.

Fase 5 do Chat Manager (~/.claude/plans/claude-sobre-o-chat-noble-dawn.md).

Para cada session arquivada/compacted com summary:
1. Calcula embedding do summary via bot.embedding.
2. Greedy clustering por topic: agrupa sessions com similaridade
   >= CLUSTER_THRESHOLD (0.55) e mesmo topic_id.
3. Pra cada cluster, gera title via GPT-4o-mini (1 chamada por cluster,
   ~$0.001 cada).
4. Insere conversation com centroid = média dos embeddings das sessions
   do cluster, vincula sessions com conversation_id.

Uso:
    .venv/bin/python infra/migrate_sessions_to_conversations.py
      → dry-run, escreve proposta em
        backups/migrate-sessions-proposal-YYYY-MM-DD.md

    .venv/bin/python infra/migrate_sessions_to_conversations.py --apply
      → aplica de fato (precisa rodar dry-run antes, espera confirmação
        explícita do operador).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

# Carrega .env antes de qualquer import que use env vars
load_dotenv(Path(__file__).parent.parent / ".env")

from supabase import create_client  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent))
from bot.embedding import embed, cosine_similarity  # noqa: E402


CLUSTER_THRESHOLD = 0.55  # similaridade mínima pra juntar 2 sessions no mesmo cluster
MIN_SUMMARY_CHARS = 50    # filtra summaries muito curtos (provavelmente vazios/lixo)


@dataclass
class SessionRow:
    id: str
    topic_id: str
    topic_name: str
    started_at: str
    ended_at: Optional[str]
    summary: str
    status: str
    embedding: Optional[list[float]] = None


@dataclass
class Cluster:
    topic_id: str
    topic_name: str
    sessions: list[SessionRow] = field(default_factory=list)
    title: Optional[str] = None
    slug: Optional[str] = None
    centroid: Optional[list[float]] = None


def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "conversa"


async def load_sessions(sb) -> list[SessionRow]:
    """Carrega sessions arquivadas/compacted com summary não-trivial."""
    topics_map = {
        t["id"]: t.get("current_name") or "(sem nome)"
        for t in sb.table("topics").select("id, current_name").execute().data
    }
    res = (
        sb.table("sessions")
        .select("id, topic_id, started_at, ended_at, summary, status")
        .in_("status", ["archived", "compacted"])
        .not_.is_("summary", "null")
        .order("started_at", desc=False)
        .execute()
    )
    out: list[SessionRow] = []
    for r in res.data or []:
        summary = (r.get("summary") or "").strip()
        if len(summary) < MIN_SUMMARY_CHARS:
            continue
        out.append(SessionRow(
            id=r["id"],
            topic_id=r["topic_id"],
            topic_name=topics_map.get(r["topic_id"], "?"),
            started_at=r["started_at"],
            ended_at=r.get("ended_at"),
            summary=summary,
            status=r["status"],
        ))
    return out


async def embed_summaries(sessions: list[SessionRow]) -> None:
    """Popula `session.embedding` para todas. Concorrência simples (gather).

    OpenAI API aceita rate alto pra embeddings; sem worry sobre throttle no
    volume da migração (~14 sessions = 14 calls).
    """
    print(f"[embed] gerando embeddings de {len(sessions)} sessions...", flush=True)
    embeddings = await asyncio.gather(
        *[embed(s.summary[:8000]) for s in sessions]
    )
    for s, e in zip(sessions, embeddings):
        s.embedding = e


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("vectors vazio")
    dim = len(vectors[0])
    return [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]


def cluster_sessions(sessions: list[SessionRow]) -> list[Cluster]:
    """Greedy clustering por topic. Ordem cronológica (started_at).

    Para cada session, calcula sim com o centroide atual de cada cluster
    do MESMO topic. Junta no cluster de maior sim se for >= threshold;
    senão cria cluster novo.
    """
    by_topic: dict[str, list[Cluster]] = defaultdict(list)

    for s in sessions:
        if s.embedding is None:
            continue
        topic_clusters = by_topic[s.topic_id]
        best_cluster: Optional[Cluster] = None
        best_sim = -2.0
        for c in topic_clusters:
            if c.centroid is None:
                continue
            sim = cosine_similarity(c.centroid, s.embedding)
            if sim > best_sim:
                best_sim = sim
                best_cluster = c
        if best_cluster is not None and best_sim >= CLUSTER_THRESHOLD:
            best_cluster.sessions.append(s)
            # Atualiza centroid como média acumulada
            best_cluster.centroid = _mean_vector(
                [ss.embedding for ss in best_cluster.sessions if ss.embedding]
            )
        else:
            new_cluster = Cluster(
                topic_id=s.topic_id,
                topic_name=s.topic_name,
                sessions=[s],
                centroid=s.embedding,
            )
            topic_clusters.append(new_cluster)

    return [c for cs in by_topic.values() for c in cs]


async def generate_titles(clusters: list[Cluster]) -> None:
    """Gera title pra cada cluster via GPT-4o-mini, baseado nos summaries.

    Prompt curto: junta primeiros chars dos summaries e pede título de
    até 6 palavras. Slug é derivado depois.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY não setada")
    client = AsyncOpenAI(api_key=api_key)
    print(f"[titles] gerando títulos pra {len(clusters)} clusters via GPT-4o-mini...", flush=True)

    async def one(c: Cluster) -> None:
        sample = "\n---\n".join(s.summary[:400] for s in c.sessions[:5])
        prompt = (
            f"Você tem {len(c.sessions)} resumos de sessions do tópico "
            f"'{c.topic_name}' que tratam do mesmo tema. Resumos:\n\n"
            f"{sample}\n\n"
            "Sugira um título curto (até 7 palavras, em português, "
            "sem aspas, sem ponto final) que capture o tema comum dos "
            "resumos. Responda APENAS com o título."
        )
        try:
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Você gera títulos curtos e descritivos para conversas em português."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=20,
            )
            title = (resp.choices[0].message.content or "").strip()
            title = title.strip("\"'.").strip()
            if not title:
                title = f"Tema sem título"
        except Exception as exc:
            print(f"  ! title gen falhou: {exc}", flush=True)
            title = f"Tema {c.topic_name[:20]}"
        c.title = title
        c.slug = _slugify(title)[:60] or "conversa"

    await asyncio.gather(*[one(c) for c in clusters])


def write_proposal(clusters: list[Cluster], path: Path) -> None:
    """Escreve proposta em markdown legível pro operador revisar."""
    by_topic: dict[str, list[Cluster]] = defaultdict(list)
    for c in clusters:
        by_topic[c.topic_name].append(c)

    lines: list[str] = []
    lines.append("# Proposta de classificação retroativa — Chat Manager Fase 5")
    lines.append("")
    lines.append(f"Gerada em {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")
    lines.append(f"**Total:** {sum(len(cs) for cs in by_topic.values())} conversations a partir de {sum(len(c.sessions) for c in clusters)} sessions.")
    lines.append("")
    lines.append(f"Threshold de clustering: {CLUSTER_THRESHOLD} (cosine similarity).")
    lines.append("Sessions com summary < 50 chars foram descartadas.")
    lines.append("")
    lines.append("---")
    lines.append("")
    for topic_name in sorted(by_topic):
        lines.append(f"## Topic: {topic_name}")
        lines.append("")
        for c in by_topic[topic_name]:
            lines.append(f"### Conversation proposta: \"{c.title}\"")
            lines.append(f"- **slug:** `{c.slug}`")
            lines.append(f"- **{len(c.sessions)} session(s):**")
            for s in c.sessions:
                started = s.started_at[:10]
                preview = s.summary.replace("\n", " ")[:120]
                lines.append(f"  - `{s.id[:8]}` ({started}, {s.status}): {preview}...")
            lines.append("")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("Pra aplicar: `.venv/bin/python infra/migrate_sessions_to_conversations.py --apply`")
    lines.append("")
    lines.append("Pra REVERTER após aplicar: ver Plano de Rollback (Fase 5) no doc do Chat Manager.")
    lines.append("```sql")
    lines.append("UPDATE sessions SET conversation_id=NULL;")
    lines.append("DELETE FROM conversations;")
    lines.append("```")

    path.write_text("\n".join(lines), encoding="utf-8")


def apply_clusters(sb, clusters: list[Cluster]) -> None:
    """Insere conversations no banco e vincula sessions."""
    for c in clusters:
        if not c.sessions:
            continue
        # Pega started_at = mais antiga; last_activity = mais recente.
        starts = sorted(s.started_at for s in c.sessions)
        latest = max(s.ended_at or s.started_at for s in c.sessions)
        # Status: dormant (todas as sessions são arquivadas/compacted, então
        # essa conversation não está "ativa agora")
        res = (
            sb.table("conversations")
            .insert(
                {
                    "topic_id": c.topic_id,
                    "title": c.title,
                    "slug": c.slug,
                    "status": "dormant",
                    "centroid_embedding": c.centroid,
                    "started_at": starts[0],
                    "last_activity_at": latest,
                }
            )
            .execute()
        )
        conv_id = res.data[0]["id"]
        ids = [s.id for s in c.sessions]
        sb.table("sessions").update({"conversation_id": conv_id}).in_("id", ids).execute()
        print(f"  ✓ conv {conv_id[:8]} '{c.title}' ({len(ids)} sessions)", flush=True)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Aplicar (sem isso, é dry-run)")
    args = parser.parse_args()

    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    print("=== Migração retroativa de sessions → conversations ===")
    sessions = await load_sessions(sb)
    print(f"sessions com summary >= {MIN_SUMMARY_CHARS} chars: {len(sessions)}")

    if not sessions:
        print("Nada a fazer.")
        return 0

    await embed_summaries(sessions)
    clusters = cluster_sessions(sessions)
    print(f"clusters formados: {len(clusters)}")

    await generate_titles(clusters)

    proposal_path = Path(__file__).parent.parent / "backups" / (
        f"migrate-sessions-proposal-{datetime.now().strftime('%Y-%m-%d')}.md"
    )
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    write_proposal(clusters, proposal_path)
    print(f"\n[proposta gravada] {proposal_path}")

    if not args.apply:
        print("\nDRY-RUN concluído. Revise a proposta e rode com --apply pra confirmar.")
        return 0

    # APPLY
    # Verifica se já há conversations (proteção contra duplo apply)
    existing = sb.table("conversations").select("id", count="exact").execute()
    if existing.count and existing.count > 0:
        print(f"\n[abort] já existem {existing.count} conversations no banco.")
        print("Se quer re-aplicar, primeiro:")
        print("  UPDATE sessions SET conversation_id=NULL;")
        print("  DELETE FROM conversations;")
        return 1

    print(f"\n=== APLICANDO {len(clusters)} conversations ===")
    apply_clusters(sb, clusters)
    print(f"\n✓ Aplicado.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
