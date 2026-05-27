"""Detector de mudança de tema/conversation no Chat Manager.

Para cada mensagem nova num topic:
1. Calcula embedding da msg via `bot.embedding`.
2. Compara com `centroid_embedding` das conversations do topic
   (status='active' + status='dormant').
3. Decide:
   - similarity com ativa > THRESHOLD_HIGH (0.65) → "continue" (mesma conv)
   - similarity max com dormant > THRESHOLD_HIGH e > sim com ativa → "reopen"
   - similarity com ativa < THRESHOLD_LOW (0.40) → "open_new" (tema novo)
   - Zona cinza (LOW–HIGH) → GPT-4o-mini judge decide
4. Atualiza `centroid_embedding` da conversation escolhida (EMA via
   `bot.embedding.update_centroid`).
5. Marca conversations não-escolhidas como `dormant` (se eram active).

Roda em background pós-resposta — resultado decide a qual conversation
a PRÓXIMA mensagem vai pertencer. Sistema atual (`sessions` ortogonais)
segue intacto até `CHAT_MANAGER_ENABLED=true` (gating em Fase 4).

LLM judge: GPT-4o-mini. Não consome cota do plano Max da Anthropic;
custo direto via OpenAI (~$0.72/mês worst case). Trocar pra Haiku via
Claude Code CLI é trivial (uma função local _judge_with_llm).
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from openai import AsyncOpenAI
from supabase import Client

from bot.embedding import (
    embed,
    cosine_similarity,
    update_centroid,
    EMBED_DIM,
)


logger = logging.getLogger("kobe.conversation_detector")

# Thresholds calibrados com base em smoke test do bot/embedding.py:
# - Textos do mesmo tema típico: ~0.65-0.85
# - Textos de temas diferentes: ~0.15-0.30
# Margem em torno de 0.50 fica como zona cinza onde LLM decide.
THRESHOLD_HIGH = 0.55
THRESHOLD_LOW = 0.35

# Peso da mensagem nova ao atualizar centroide (EMA).
# 0.1 = conversation "esquece" devagar; vetor preserva tema dominante.
CENTROID_WEIGHT_NEW = 0.10

# Modelo do LLM judge. GPT-4o-mini é barato (~$0.15/1M tokens input)
# e rápido (~500ms-1s por call). Suficiente pra binary classification.
JUDGE_MODEL = "gpt-4o-mini"


Action = Literal["continue", "reopen", "open_new"]


@dataclass(frozen=True)
class DetectorResult:
    """Resultado de uma rodada do detector.

    `action`:
    - "continue" → mesma conversation ativa, sem aviso
    - "reopen" → conversation dormente foi reativada (mostrar aviso)
    - "open_new" → conversation nova criada (mostrar aviso)

    `notice_text` é a linha curta a mandar pro operador no Telegram
    (None quando action="continue", pra não poluir o chat).
    """
    action: Action
    conversation_id: str
    confidence: float
    notice_text: Optional[str]
    judge_used: bool = False


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------


async def detect(
    db: Client,
    *,
    topic_id: str,
    message_text: str,
) -> DetectorResult:
    """Decide a qual conversation a mensagem nova pertence.

    Carrega conversations do topic, calcula similarity, decide ação,
    atualiza estado no banco. Idempotente — chamadas duplicadas com a
    mesma msg dão mesmo resultado (cache de embedding + estado no banco).
    """
    if not message_text.strip():
        raise ValueError("message_text vazia")

    msg_vec = await embed(message_text)

    active, dormants = _load_topic_conversations(db, topic_id)

    # Caso 1: topic novo, nenhuma conversation ainda
    if not active and not dormants:
        new_id = _create_new_conversation(db, topic_id, message_text, msg_vec)
        return DetectorResult(
            action="open_new",
            conversation_id=new_id,
            confidence=1.0,
            notice_text=None,  # 1ª conversation do topic — sem aviso, é o esperado
        )

    sim_active = (
        cosine_similarity(active["centroid_embedding"], msg_vec) if active else None
    )
    best_dormant, sim_dormant = _best_dormant_match(dormants, msg_vec)

    # Caso 2: continua na ativa (alta confiança)
    if sim_active is not None and sim_active >= THRESHOLD_HIGH:
        if sim_dormant is not None and sim_dormant > sim_active:
            # Dormant casa MELHOR que active → reopen vence
            return await _do_reopen(db, best_dormant, active, msg_vec, sim_dormant)
        await _do_continue(db, active, msg_vec, sim_active)
        return DetectorResult(
            action="continue",
            conversation_id=active["id"],
            confidence=sim_active,
            notice_text=None,
        )

    # Caso 3: dormant casa forte (e ativa não) → reopen
    if sim_dormant is not None and sim_dormant >= THRESHOLD_HIGH:
        return await _do_reopen(db, best_dormant, active, msg_vec, sim_dormant)

    # Caso 4: sim com ativa muito baixa → tema novo
    if sim_active is None or sim_active <= THRESHOLD_LOW:
        # Mas antes verifica se algum dormant caiu na zona cinza —
        # talvez o LLM judge decida que é retomada
        if sim_dormant is not None and sim_dormant > THRESHOLD_LOW:
            decision = await _judge_with_llm(
                active=active,
                dormant=best_dormant,
                message_text=message_text,
            )
            if decision == "reopen":
                return await _do_reopen(
                    db, best_dormant, active, msg_vec, sim_dormant, via_judge=True
                )
        return await _do_open_new(db, topic_id, active, message_text, msg_vec)

    # Caso 5: zona cinza com ativa → LLM judge decide
    decision = await _judge_with_llm(
        active=active,
        dormant=best_dormant,
        message_text=message_text,
    )
    if decision == "continue":
        await _do_continue(db, active, msg_vec, sim_active)
        return DetectorResult(
            action="continue",
            conversation_id=active["id"],
            confidence=sim_active,
            notice_text=None,
            judge_used=True,
        )
    if decision == "reopen" and best_dormant is not None:
        return await _do_reopen(
            db, best_dormant, active, msg_vec, sim_dormant or 0.0, via_judge=True
        )
    # decision == "open_new" (default do judge se incerto)
    result = await _do_open_new(db, topic_id, active, message_text, msg_vec)
    return DetectorResult(**{**result.__dict__, "judge_used": True})


# ---------------------------------------------------------------------------
# Carga de conversations + matching
# ---------------------------------------------------------------------------


def _load_topic_conversations(
    db: Client, topic_id: str
) -> tuple[Optional[dict], list[dict]]:
    """Carrega ativa + dormants do topic. Ignora 'archived' (fora do escopo).

    Filtra fora as que têm `centroid_embedding=None` (recém-criadas sem
    msgs ainda — caso raro mas defensivo).
    """
    res = (
        db.table("conversations")
        .select("id, title, slug, status, centroid_embedding")
        .eq("topic_id", topic_id)
        .in_("status", ["active", "dormant"])
        .execute()
    )
    active: Optional[dict] = None
    dormants: list[dict] = []
    for row in res.data or []:
        if row.get("centroid_embedding") is None:
            continue
        # Supabase retorna VECTOR como string "[0.1,0.2,...]" — parsear
        if isinstance(row["centroid_embedding"], str):
            row["centroid_embedding"] = _parse_vector(row["centroid_embedding"])
        if row["status"] == "active":
            active = row
        else:
            dormants.append(row)
    return active, dormants


def _parse_vector(s: str) -> list[float]:
    """Converte representação textual `[0.1,0.2,...]` em list[float]."""
    s = s.strip()
    if s.startswith("["):
        s = s[1:]
    if s.endswith("]"):
        s = s[:-1]
    return [float(x) for x in s.split(",") if x.strip()]


def _best_dormant_match(
    dormants: list[dict], msg_vec: list[float]
) -> tuple[Optional[dict], Optional[float]]:
    """Conversation dormant com maior similarity, e o valor. (None, None) se vazio."""
    if not dormants:
        return None, None
    best: Optional[dict] = None
    best_sim: float = -2.0
    for c in dormants:
        sim = cosine_similarity(c["centroid_embedding"], msg_vec)
        if sim > best_sim:
            best_sim = sim
            best = c
    return best, best_sim


# ---------------------------------------------------------------------------
# Mutações no banco
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_new_conversation(
    db: Client,
    topic_id: str,
    message_text: str,
    msg_vec: list[float],
) -> str:
    """Insere conversation nova com slug+title derivados da 1ª msg."""
    title, slug = _title_and_slug_from_message(message_text)
    res = (
        db.table("conversations")
        .insert(
            {
                "topic_id": topic_id,
                "title": title,
                "slug": slug,
                "status": "active",
                "centroid_embedding": msg_vec,
                "started_at": _now_iso(),
                "last_activity_at": _now_iso(),
            }
        )
        .execute()
    )
    if not res.data:
        raise RuntimeError("insert de conversation não retornou linha")
    return res.data[0]["id"]


async def _do_continue(
    db: Client, active: dict, msg_vec: list[float], sim: float
) -> None:
    """Atualiza centroide da ativa + last_activity_at."""
    new_centroid = update_centroid(
        active["centroid_embedding"], msg_vec, weight_new=CENTROID_WEIGHT_NEW
    )
    db.table("conversations").update(
        {
            "centroid_embedding": new_centroid,
            "last_activity_at": _now_iso(),
        }
    ).eq("id", active["id"]).execute()


async def _do_reopen(
    db: Client,
    target: dict,
    current_active: Optional[dict],
    msg_vec: list[float],
    sim: float,
    *,
    via_judge: bool = False,
) -> DetectorResult:
    """Move ativa atual pra dormant + reativa a target + atualiza centroide."""
    if current_active is not None and current_active["id"] != target["id"]:
        db.table("conversations").update({"status": "dormant"}).eq(
            "id", current_active["id"]
        ).execute()

    new_centroid = update_centroid(
        target["centroid_embedding"], msg_vec, weight_new=CENTROID_WEIGHT_NEW
    )
    db.table("conversations").update(
        {
            "status": "active",
            "centroid_embedding": new_centroid,
            "last_activity_at": _now_iso(),
        }
    ).eq("id", target["id"]).execute()

    return DetectorResult(
        action="reopen",
        conversation_id=target["id"],
        confidence=sim,
        notice_text=f"Reabri conversa '{target['title']}'.",
        judge_used=via_judge,
    )


async def _do_open_new(
    db: Client,
    topic_id: str,
    current_active: Optional[dict],
    message_text: str,
    msg_vec: list[float],
) -> DetectorResult:
    """Move ativa atual pra dormant + cria conversation nova."""
    if current_active is not None:
        db.table("conversations").update({"status": "dormant"}).eq(
            "id", current_active["id"]
        ).execute()

    new_id = _create_new_conversation(db, topic_id, message_text, msg_vec)
    title = _title_and_slug_from_message(message_text)[0]
    notice_parts = []
    if current_active is not None:
        notice_parts.append(f"Salvei conversa '{current_active['title']}'.")
    notice_parts.append(f"Abrindo nova: '{title}'.")
    return DetectorResult(
        action="open_new",
        conversation_id=new_id,
        confidence=1.0,
        notice_text=" ".join(notice_parts),
    )


# ---------------------------------------------------------------------------
# LLM judge (GPT-4o-mini)
# ---------------------------------------------------------------------------


_openai_client: Optional[AsyncOpenAI] = None


def _get_openai() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY não configurada — judge não pode rodar")
        _openai_client = AsyncOpenAI(api_key=key)
    return _openai_client


async def _judge_with_llm(
    *,
    active: Optional[dict],
    dormant: Optional[dict],
    message_text: str,
) -> Action:
    """Pergunta ao GPT-4o-mini: continue, reopen, ou open_new?

    Recebe o contexto curto da conversation ativa e do melhor dormant
    candidato (se houver), e a mensagem nova. Retorna ação como string.
    Em caso de erro/incerteza, retorna 'open_new' (default conservador).
    """
    if active is None and dormant is None:
        return "open_new"

    options = []
    if active is not None:
        options.append(f"A) Continuar conversa atual: '{active['title']}'")
    if dormant is not None:
        options.append(
            f"B) Retomar conversa anterior dormente: '{dormant['title']}'"
        )
    options.append("C) Abrir conversa nova com tema diferente")
    options_text = "\n".join(options)

    user_prompt = f"""Mensagem nova do usuário:
\"\"\"
{message_text[:1500]}
\"\"\"

Decida em qual conversa essa mensagem encaixa melhor. Opções:
{options_text}

Responda APENAS com a letra (A, B ou C). Nada mais."""

    try:
        resp = await _get_openai().chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você classifica mensagens em conversas existentes "
                        "ou novas. Responda apenas com A, B ou C."
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=5,
        )
        answer = (resp.choices[0].message.content or "").strip().upper()
    except Exception as exc:
        logger.warning("judge falhou (%s); defaulta pra open_new", exc)
        return "open_new"

    if answer.startswith("A"):
        return "continue"
    if answer.startswith("B"):
        return "reopen"
    return "open_new"


# ---------------------------------------------------------------------------
# Slug + title da 1ª mensagem
# ---------------------------------------------------------------------------


_STOP_WORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas",
    "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas",
    "e", "ou", "que", "para", "por", "com", "sem", "sobre",
    "eu", "voce", "voces", "nos", "ele", "ela", "eles", "elas",
    "isso", "isto", "aquilo", "esse", "essa", "este", "esta",
    "mas", "se", "ja", "ainda", "tambem", "muito", "pouco",
    "ser", "estar", "ter", "vai", "vou", "tem", "ha", "foi",
}


def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _title_and_slug_from_message(text: str, *, max_title_chars: int = 60) -> tuple[str, str]:
    """Deriva (title, slug) da 1ª mensagem do operador.

    Title: primeira frase truncada (até `max_title_chars`).
    Slug: kebab-case das palavras significativas do title.
    Fallback se vazio: 'Conversa nova' / 'conversa-nova'.
    """
    first = re.split(r"[.\n?!]", text, maxsplit=1)[0].strip()
    if not first:
        return "Conversa nova", "conversa-nova"
    if len(first) <= max_title_chars:
        title = first
    else:
        # Corta em word boundary pra não terminar com palavra truncada feia
        cut = first[:max_title_chars].rsplit(" ", 1)[0].strip()
        title = (cut or first[:max_title_chars]).strip()

    # Slug: pega palavras significativas do title.
    words = re.findall(r"\w+", _slugify(title).replace("-", " "))
    significant = [w for w in words if w not in _STOP_WORDS and len(w) > 2]
    # Se filtro removeu demais, relaxa o min length pra não perder
    # contexto (ex: "Bug 4 da v0.13" não pode virar só "bug").
    if len(significant) < 2:
        significant = [w for w in words if w not in _STOP_WORDS]
    if not significant:
        significant = words
    slug = "-".join(significant[:6]) or _slugify(title) or "conversa-nova"
    return title, slug
