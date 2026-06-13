"""Classificador-bibliotecário do New Chat Manager.

Roda ATRÁS, no daemon Keyko, pós-resposta. Nunca no caminho crítico do
turno, nunca Opus. Faz o ofício do bibliotecário (doc §4.3):

- calcula/armazena embedding das mensagens novas (text-embedding-3-small);
- detecta BORDAS de assunto grosso em RETROSPECTO e mantém a faixa de cada
  conversation (`messages.conversation_id` = a faixa);
- mantém o ponteiro do quente (a conversation status='active');
- atualiza a tag cloud (catálogo frio) — best-effort via modelo barato.

A detecção de borda implementa os 5 pilares (doc §4.2):
1. Contra o ACUMULADO (centroide do assunto grosso), não o vizinho.
2. Permanência/histerese — um lampejo não corta; precisa ASSENTAR.
3. Voto ponderado por informação — resposta curta não vota abertura.
4. Corte RETROSPECTIVO — vê o que veio depois; tail ambíguo fica pendente
   pra próxima passada (re-corte é de graça).
5. Hierarquia grosso/fino — a borda corta no assunto grosso; beats finos
   viram tags, não bordas.

O algoritmo de detecção (`detect_segments`) é PURO (sem DB) pra a fase de
calibração testá-lo contra corpus rotulado.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client

from bot.embedding import cosine_similarity, embed, update_centroid
from bot.conversation_detector import (
    _parse_vector,
    _title_and_slug_from_message,
)


logger = logging.getLogger("kobe.chat_manager.classifier")


# Quanto histórico olhar na PRIMEIRA classificação de um topic (sem
# watermark ainda) — evita reprocessar meses de backlog quando a flag
# liga. Mensagens mais antigas que isso ficam sem conversation_id (são
# pré-sistema; não poluem porque só comparamos contra o centroide ativo).
INITIAL_LOOKBACK_HOURS = 6

# Teto de mensagens por passada (proteção; o disjuntor de teto do source
# também limita). Em ordem cronológica crescente.
MAX_BATCH = 200


@dataclass(frozen=True)
class Knobs:
    """Os 3 botões de calibração (doc §7) + parâmetros de apoio.

    Calibrados em `infra/calibrate_chat_manager.py` contra corpus real.
    Defaults aqui são o ponto de partida; os valores finais saem da
    calibração e podem ser sobrescritos por env (ver `knobs_from_env`).
    """
    # 1. Distância de borda: sim ao centroide grosso ABAIXO da qual a msg
    #    é candidata a off-subject. (sim alta = mesmo assunto.)
    #    Calibrado 2026-06-01 (docs/chat-manager/calibracao-2026-06-01.md).
    border_sim_threshold: float = 0.40
    # 2. Permanência: nº de msgs informativas coerentes pra confirmar borda.
    #    3 = conservador (assunto precisa ASSENTAR; viés contra over-cut).
    sustain_length: int = 3
    # 3. Piso de informação: massa semântica mínima pra "votar" abertura.
    info_min_words: int = 4
    info_min_chars: int = 24
    # Coerência do cluster novo: sim mútua mínima entre as msgs off-subject
    # pra contarem como um assunto novo coerente (vs. digressões soltas).
    cluster_coherence: float = 0.35
    # EMA do centroide ao absorver msg on-subject.
    centroid_weight_new: float = 0.15


def knobs_from_env() -> Knobs:
    """Knobs com override por env (CM_BORDER_SIM, CM_SUSTAIN, ...).

    Permite recalibrar em produção sem deploy de código — só editar .env
    e reiniciar o keyko. Ausência → default do dataclass.
    """
    d = Knobs()

    def _f(name: str, default: float) -> float:
        raw = os.getenv(name)
        try:
            return float(raw) if raw else default
        except ValueError:
            return default

    def _i(name: str, default: int) -> int:
        raw = os.getenv(name)
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    return Knobs(
        border_sim_threshold=_f("CM_BORDER_SIM", d.border_sim_threshold),
        sustain_length=_i("CM_SUSTAIN", d.sustain_length),
        info_min_words=_i("CM_INFO_MIN_WORDS", d.info_min_words),
        info_min_chars=_i("CM_INFO_MIN_CHARS", d.info_min_chars),
        cluster_coherence=_f("CM_CLUSTER_COHERENCE", d.cluster_coherence),
        centroid_weight_new=_f("CM_CENTROID_WEIGHT", d.centroid_weight_new),
    )


def is_informative(text: str, knobs: Knobs) -> bool:
    """Massa semântica suficiente pra a msg "votar" abertura de assunto.

    OR deliberado entre chars e palavras: uma frase curta mas densa
    ('reescreve a home page toda') vota; um 'Kobe'/'isso'/'sim' não.
    """
    t = (text or "").strip()
    return len(t) >= knobs.info_min_chars or len(t.split()) >= knobs.info_min_words


# Pistas lexicais de TROCA EXPLÍCITA de assunto. Operador real sinaliza
# muito ("muda de assunto", "deixa isso de lado", "outra coisa"). É um
# sinal forte e barato que complementa o vetor — crítico porque embeddings
# de msgs curtas em PT têm cosseno comprimido (0.3-0.5), então a distância
# vetorial sozinha discrimina mal assuntos vizinhos (ex: redesenho do chat
# manager vs bug do atrus — ambos "interno do kobe"). A pista NUNCA corta
# sozinha: ainda exige permanência (cluster novo sustentado depois dela).
_SWITCH_CUES = (
    "muda de assunto",
    "mudando de assunto",
    "mudar de assunto",
    "trocando de assunto",
    "trocar de assunto",
    "mudando completamente de assunto",
    "muda completamente de assunto",
    "deixa isso de lado",
    "deixa isso pra la",
    "deixa pra la",
    "esquece isso",
    "outra coisa",
    "outro assunto",
    "outro tema",
    "mudando de tema",
    "vamos falar de outr",
    "agora vamos falar",
    "deixa o ",  # "deixa o chat manager de lado" / "deixa o X de lado"
    "deixa a ",  # "deixa a tarefa Y de lado"
)


def has_switch_cue(text: str) -> bool:
    """True se a msg traz pista lexical de troca explícita de assunto.

    Normaliza acento/caixa pra casar 'lá'/'la', 'Você'/'voce' etc.
    'deixa o/a ... de lado' exige o 'de lado' pra não casar 'deixa o time
    jogar' à toa.
    """
    import unicodedata

    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    for cue in _SWITCH_CUES:
        if cue in t:
            if cue in ("deixa o ", "deixa a "):
                # exige "de lado" na frase pra ser troca de assunto.
                if "de lado" in t:
                    return True
                continue
            return True
    return False


# ---------------------------------------------------------------------------
# Algoritmo PURO de detecção de borda (sem DB — testável na calibração)
# ---------------------------------------------------------------------------


@dataclass
class CMsg:
    """Mensagem mínima pro detector. `embedding` só é exigido em msgs de
    operador (role='user'); assistant/system entram só pra serem grudadas
    na faixa correta."""
    id: str
    role: str
    content: str
    embedding: Optional[list[float]] = None


@dataclass
class Segment:
    """Faixa resolvida de mensagens pertencendo a um assunto grosso."""
    message_ids: list[str] = field(default_factory=list)
    op_embeddings: list[list[float]] = field(default_factory=list)
    centroid: Optional[list[float]] = None
    is_new: bool = False  # True = abriu conversation nova (borda)
    seed_text: str = ""   # 1ª msg informativa do operador (título/slug)


@dataclass
class Plan:
    segments: list[Segment] = field(default_factory=list)
    pending_tail_ids: list[str] = field(default_factory=list)


def _mean_vector(vecs: list[list[float]]) -> list[float]:
    n = len(vecs)
    dim = len(vecs[0])
    acc = [0.0] * dim
    for v in vecs:
        for i in range(dim):
            acc[i] += v[i]
    return [x / n for x in acc]


def _coherent(op_embs: list[list[float]], knobs: Knobs) -> bool:
    """True se as msgs off-subject formam um cluster coerente (assunto novo
    sustentado) e não digressões soltas. Mede sim mútua média."""
    if len(op_embs) < 2:
        return True  # sustain_length=1 degenerate; um voto só já "coerente"
    sims = []
    for i in range(len(op_embs)):
        for j in range(i + 1, len(op_embs)):
            sims.append(cosine_similarity(op_embs[i], op_embs[j]))
    return (sum(sims) / len(sims)) >= knobs.cluster_coherence


def detect_segments(
    messages: list[CMsg],
    initial_centroid: Optional[list[float]],
    knobs: Knobs,
) -> Plan:
    """Detecção de borda retrospectiva. Devolve faixas + tail pendente.

    `initial_centroid`: centroide da conversation ativa (assunto grosso
    corrente) ou None se o topic ainda não tem conversation.

    O tail pendente (buffer off-subject que não atingiu permanência até o
    fim do lote) fica SEM classificar — a próxima passada, já com o que
    veio depois, decide (pilar 4: corte retrospectivo, re-corte de graça).
    """
    plan = Plan()
    centroid = list(initial_centroid) if initial_centroid is not None else None
    w = knobs.centroid_weight_new

    cur = Segment(is_new=(centroid is None))
    # Buffer off-subject (candidato a assunto novo). Guarda ids (todas as
    # msgs, inclusive low-info/assistant que vão junto), e SÓ os embeddings
    # informativos que contam como "voto"/coerência. `off_cue` = a pista
    # lexical de troca abriu/armou o buffer (então msgs seguintes entram
    # mesmo com vetor on-subject — o assunto novo pode compartilhar
    # vocabulário com o velho).
    off_ids: list[str] = []
    off_embs: list[list[float]] = []   # só informativas (voto)
    off_seed = ""
    off_cue = False

    def reset_off() -> None:
        nonlocal off_seed, off_cue
        off_ids.clear()
        off_embs.clear()
        off_seed = ""
        off_cue = False

    def flush_off_into_cur() -> None:
        """Digressão que voltou: absorve o buffer off de volta no assunto."""
        nonlocal centroid
        cur.message_ids.extend(off_ids)
        for e in off_embs:
            cur.op_embeddings.append(e)
            centroid = update_centroid(centroid, e, weight_new=w)
        reset_off()

    def commit_cur() -> None:
        if cur.message_ids:
            cur.centroid = centroid
            plan.segments.append(cur)

    def confirm_border() -> None:
        """Fecha cur e abre faixa nova a partir do buffer off."""
        nonlocal centroid, cur
        commit_cur()
        # Centroide do assunto novo: média das informativas. Se a pista de
        # troca abriu o buffer e há >=2 informativas, exclui a 1ª (a msg-pivô
        # costuma citar o assunto VELHO — "deixa o X de lado, agora Y") pra
        # não contaminar o centroide novo.
        seed_embs = off_embs[1:] if (off_cue and len(off_embs) >= 2) else off_embs
        new_centroid = _mean_vector(seed_embs or off_embs)
        cur = Segment(
            message_ids=list(off_ids),
            op_embeddings=list(off_embs),
            is_new=True,
            seed_text=off_seed,
        )
        centroid = new_centroid
        reset_off()

    def maybe_confirm() -> bool:
        n_info = len(off_embs)
        if n_info < knobs.sustain_length:
            return False
        # Pista lexical dispensa o teste de coerência (a troca é explícita);
        # buffer aberto só por vetor exige cluster coerente (vs digressões).
        if off_cue or _coherent(off_embs, knobs):
            confirm_border()
            return True
        return False

    for m in messages:
        if m.role != "user":
            (off_ids if off_ids else cur.message_ids).append(m.id)
            continue

        emb = m.embedding
        if emb is None:
            (off_ids if off_ids else cur.message_ids).append(m.id)
            continue

        informative = is_informative(m.content, knobs)

        # Bootstrap: primeiro assunto do topic.
        if centroid is None:
            centroid = list(emb)
            cur.message_ids.append(m.id)
            cur.op_embeddings.append(emb)
            if informative and not cur.seed_text:
                cur.seed_text = m.content
            continue

        sim = cosine_similarity(centroid, emb)
        cue = informative and has_switch_cue(m.content)
        off_subject = sim < knobs.border_sim_threshold

        if off_ids:
            # Buffer aberto. A msg entra no buffer se: é low-info (vai junto,
            # sem voto), OU é informativa e (off-subject por vetor, OU é/foi
            # pista de troca — buffer armado). Senão (informativa, on-subject,
            # sem pista) → era digressão: devolve o buffer e segue no assunto.
            if not informative:
                off_ids.append(m.id)
            elif off_subject or cue or off_cue:
                off_ids.append(m.id)
                off_embs.append(emb)
                if cue:
                    off_cue = True
                if not off_seed:
                    off_seed = m.content
            else:
                flush_off_into_cur()
                cur.message_ids.append(m.id)
                cur.op_embeddings.append(emb)
                centroid = update_centroid(centroid, emb, weight_new=w)
                if not cur.seed_text:
                    cur.seed_text = m.content
                continue
            maybe_confirm()
            continue

        # Sem buffer aberto.
        if informative and (off_subject or cue):
            # Abre buffer off (candidato a borda).
            off_ids.append(m.id)
            off_embs.append(emb)
            off_cue = cue
            off_seed = m.content
            maybe_confirm()
            continue

        # On-subject (ou low-info): absorve no assunto corrente.
        cur.message_ids.append(m.id)
        if informative:
            cur.op_embeddings.append(emb)
            centroid = update_centroid(centroid, emb, weight_new=w)
            if not cur.seed_text:
                cur.seed_text = m.content

    commit_cur()
    plan.pending_tail_ids = list(off_ids)  # ambíguo → próxima passada decide
    return plan


# ---------------------------------------------------------------------------
# Camada de DB — orquestra embedding, detecção, stamping, centroide, tags
# ---------------------------------------------------------------------------


@dataclass
class ClassifyResult:
    topic_id: str
    processed: int = 0
    borders: int = 0
    pending: int = 0
    active_conversation_id: Optional[str] = None
    # created_at da última msg COMMITADA — vira o novo watermark. None se
    # nada foi commitado nesta passada (watermark não avança).
    watermark: Optional[str] = None
    # Conversations abertas por TRANSIÇÃO de assunto nesta passada (borda que
    # fechou a anterior e abriu uma nova). Cada item {id, title}. NÃO inclui o
    # bootstrap (1ª conversation do topic, sem ativa anterior) — só transições
    # reais, pro source avisar o operador "novo assunto". Lista vazia = nada
    # a avisar.
    new_conversations: list[dict] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_pending_messages(
    db: Client, topic_id: str, watermark: Optional[str]
) -> list[dict]:
    q = (
        db.table("messages")
        .select("id, role, content, created_at, embedding")
        .eq("topic_id", topic_id)
        .is_("conversation_id", "null")
        .order("created_at", desc=False)
        .limit(MAX_BATCH)
    )
    if watermark:
        q = q.gt("created_at", watermark)
    else:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=INITIAL_LOOKBACK_HOURS)
        ).isoformat()
        q = q.gt("created_at", cutoff)
    return q.execute().data or []


def _load_active_conversation(db: Client, topic_id: str) -> Optional[dict]:
    res = (
        db.table("conversations")
        .select("id, title, centroid_embedding")
        .eq("topic_id", topic_id)
        .eq("status", "active")
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    cen = row.get("centroid_embedding")
    if isinstance(cen, str):
        row["centroid_embedding"] = _parse_vector(cen)
    return row


async def _ensure_embeddings(db: Client, rows: list[dict]) -> dict[str, list[float]]:
    """Garante embedding das msgs de operador. Persiste as recém-calculadas
    em messages.embedding (assim o re-processo do tail não re-embeda)."""
    out: dict[str, list[float]] = {}
    for r in rows:
        if r.get("role") != "user":
            continue
        emb = r.get("embedding")
        if isinstance(emb, str):
            emb = _parse_vector(emb)
        if not emb:
            text = (r.get("content") or "").strip()
            if not text:
                continue
            emb = await embed(text)
            try:
                db.table("messages").update({"embedding": emb}).eq(
                    "id", r["id"]
                ).execute()
            except Exception:  # noqa: BLE001 — persistência best-effort
                logger.warning("falha gravando embedding msg=%s", r["id"], exc_info=True)
        out[r["id"]] = emb
    return out


def _create_conversation(
    db: Client,
    topic_id: str,
    seed_text: str,
    centroid: list[float],
    *,
    title: Optional[str] = None,
) -> dict:
    """Cria a conversation. Se `title` (tema gerado por LLM) vier, usa ele e
    deriva o slug dele; senão cai no literal da 1ª frase do seed (fallback)."""
    if title and title.strip():
        title = title.strip()
        _, slug = _title_and_slug_from_message(title)
    else:
        title, slug = _title_and_slug_from_message(seed_text or "Conversa nova")
    res = (
        db.table("conversations")
        .insert(
            {
                "topic_id": topic_id,
                "title": title,
                "slug": slug,
                "status": "active",
                "centroid_embedding": centroid,
                "started_at": _now_iso(),
                "last_activity_at": _now_iso(),
            }
        )
        .execute()
    )
    row = res.data[0]
    row["title"] = title
    return row


def _stamp_messages(db: Client, message_ids: list[str], conversation_id: str) -> None:
    if not message_ids:
        return
    # Supabase .in_ aceita lista; faz em blocos pra não estourar URL.
    for i in range(0, len(message_ids), 100):
        chunk = message_ids[i : i + 100]
        db.table("messages").update({"conversation_id": conversation_id}).in_(
            "id", chunk
        ).execute()


def _set_dormant(db: Client, conversation_id: str) -> None:
    db.table("conversations").update({"status": "dormant"}).eq(
        "id", conversation_id
    ).execute()


def _update_conversation_centroid(
    db: Client, conversation_id: str, centroid: list[float]
) -> None:
    db.table("conversations").update(
        {"centroid_embedding": centroid, "last_activity_at": _now_iso()}
    ).eq("id", conversation_id).execute()


def _link_active_session(db: Client, topic_id: str, conversation_id: str) -> None:
    """Liga a session ativa do topic à conversation corrente — mantém a
    cronologia comprimida (summaries por session) funcionando."""
    try:
        db.table("sessions").update({"conversation_id": conversation_id}).eq(
            "topic_id", topic_id
        ).eq("status", "active").execute()
    except Exception:  # noqa: BLE001
        logger.warning("falha ligando session ativa topic=%s", topic_id, exc_info=True)


async def classify_topic(
    db: Client,
    topic_id: str,
    *,
    watermark: Optional[str],
    knobs: Knobs,
    force_resolve_tail: bool = False,
) -> ClassifyResult:
    """Classifica o lote pendente de um topic. Idempotente por watermark.

    Retorna `ClassifyResult` com o novo watermark embutido no caller (que
    persiste via activity.write_state). Não levanta — o source captura,
    mas defendemos aqui contra erro parcial.
    """
    rows = _fetch_pending_messages(db, topic_id, watermark)
    if not rows:
        return ClassifyResult(topic_id=topic_id)

    has_operator = any(r.get("role") == "user" for r in rows)
    if not has_operator:
        # Só respostas do agente sem msg nova do operador — nada a cortar.
        # Não avança watermark (a próxima msg do operador reprocessa junto).
        return ClassifyResult(topic_id=topic_id)

    emb_map = await _ensure_embeddings(db, rows)

    cmsgs = [
        CMsg(
            id=r["id"],
            role=r.get("role", "user"),
            content=r.get("content") or "",
            embedding=emb_map.get(r["id"]),
        )
        for r in rows
    ]

    active = _load_active_conversation(db, topic_id)
    initial_centroid = active["centroid_embedding"] if active else None

    plan = detect_segments(cmsgs, initial_centroid, knobs)

    current_active_id: Optional[str] = active["id"] if active else None
    current_active_title: Optional[str] = active.get("title") if active else None
    borders = 0
    new_conversations: list[dict] = []

    # id -> conteúdo das msgs do OPERADOR (assistant/system não contam), pra
    # colher as primeiras de cada segmento como contexto do título por tema.
    op_content_by_id = {m.id: m.content for m in cmsgs if m.role == "user"}

    def _first_op_texts(seg: Segment, limit: int = 5) -> list[str]:
        out: list[str] = []
        for mid in seg.message_ids:
            txt = (op_content_by_id.get(mid) or "").strip()
            if txt and is_informative(txt, knobs):
                out.append(txt)
            if len(out) >= limit:
                break
        return out

    for idx, seg in enumerate(plan.segments):
        if idx == 0 and not seg.is_new and current_active_id is not None:
            # Continua o assunto grosso corrente.
            _stamp_messages(db, seg.message_ids, current_active_id)
            if seg.centroid is not None:
                _update_conversation_centroid(db, current_active_id, seg.centroid)
        else:
            # Borda: fecha o ativo anterior, abre faixa nova.
            was_transition = current_active_id is not None
            if was_transition:
                _set_dormant(db, current_active_id)
                borders += 1
            seed = seg.seed_text or (cmsgs[0].content if cmsgs else "Conversa nova")
            centroid = seg.centroid or (seg.op_embeddings[0] if seg.op_embeddings else None)
            if centroid is None:
                continue
            # Título por TEMA + tags numa só chamada barata (item 2). Título None
            # → _create_conversation cai no literal da 1ª frase (fallback).
            llm_title, tags = await _make_title_and_tags(_first_op_texts(seg))
            conv = _create_conversation(
                db, topic_id, seed, centroid, title=llm_title
            )
            current_active_id = conv["id"]
            current_active_title = conv.get("title")
            _stamp_messages(db, seg.message_ids, current_active_id)
            _upsert_tags(db, current_active_id, tags)
            # Só transição real (havia ativa antes) vira aviso — bootstrap não.
            if was_transition:
                new_conversations.append({"id": conv["id"], "title": conv.get("title")})

    committed_ids = {mid for seg in plan.segments for mid in seg.message_ids}

    # --- Flush-por-silêncio: resolve o tail órfão (item 3 do bug) ----------
    # A histerese deixa um buffer off-subject pendente esperando "a próxima
    # msg". Quando o operador troca de assunto E silencia, não há próxima msg:
    # o tail vira órfão eterno (conversation_id NULL), o watermark congela e o
    # source re-roda em loop estéril. Sob silêncio prolongado
    # (force_resolve_tail), forçamos a decisão via juiz GPT-4o-mini: continua o
    # assunto ativo ou abre um novo.
    pending_ids = list(plan.pending_tail_ids)
    if force_resolve_tail and pending_ids and current_active_id is not None:
        resolved = await _resolve_pending_tail(
            db,
            topic_id,
            pending_ids,
            cmsgs,
            emb_map,
            active_id=current_active_id,
            active_title=current_active_title,
            knobs=knobs,
        )
        if resolved is not None:
            committed_ids.update(resolved["ids"])
            current_active_id = resolved["active_id"]
            pending_ids = []
            if resolved.get("new_conversation"):
                borders += 1
                new_conversations.append(resolved["new_conversation"])

    if not committed_ids:
        # Nada commitado nem resolvido: preserva o tail pendente pra próxima
        # passada (histerese normal — ainda pode chegar "a próxima msg"). O
        # active_conversation_id volta preenchido pro source não zerar o ponteiro.
        return ClassifyResult(
            topic_id=topic_id,
            pending=len(pending_ids),
            active_conversation_id=current_active_id,
        )

    if current_active_id is not None:
        _link_active_session(db, topic_id, current_active_id)

    # Watermark = created_at da última msg COMMITADA (inclui o tail resolvido,
    # que é cronologicamente o fim do lote → descongela a marca).
    last_committed_at = None
    for r in rows:
        if r["id"] in committed_ids:
            last_committed_at = r["created_at"]
    return ClassifyResult(
        topic_id=topic_id,
        processed=len(committed_ids),
        borders=borders,
        pending=len(pending_ids),
        active_conversation_id=current_active_id,
        watermark=last_committed_at,
        new_conversations=new_conversations,
    )


# ---------------------------------------------------------------------------
# Flush-por-silêncio do tail órfão — juiz GPT-4o-mini (continua vs assunto novo)
# ---------------------------------------------------------------------------


def _touch_conversation(db: Client, conversation_id: str) -> None:
    """Bump leve do last_activity_at (sem mexer no centroide). Best-effort —
    mantém o assunto ativo 'fresco' ao absorver o tail."""
    try:
        db.table("conversations").update(
            {"last_activity_at": _now_iso()}
        ).eq("id", conversation_id).execute()
    except Exception:  # noqa: BLE001
        logger.debug("touch_conversation falhou conv=%s", conversation_id)


async def _judge_tail(
    active_title: str, tail_texts: list[str]
) -> tuple[str, Optional[str]]:
    """Juiz do tail órfão (flush-por-silêncio): as últimas msgs do operador,
    paradas pela histerese, CONTINUAM o assunto ativo ou abrem um assunto NOVO?

    GPT-4o-mini (barato, fora do caminho do turno). Roda só quando há silêncio
    prolongado E tail pendente — caso raro. Best-effort: em qualquer falha
    devolve ('continue', None) — absorção conservadora, que descongela o
    watermark sem fragmentar. Retorna ('continue', None) ou ('new', titulo|None).
    """
    joined = "\n".join(f"- {t.strip()}" for t in tail_texts if (t or "").strip())[:1500]
    if len(joined) < 6:
        return ("continue", None)
    try:
        import json as _json

        from bot.conversation_detector import _get_openai, JUDGE_MODEL

        resp = await _get_openai().chat.completions.create(
            model=JUDGE_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você é um bibliotecário de conversas no Telegram. O assunto "
                        f'corrente da conversa é: "{active_title}". A seguir vêm as '
                        "ÚLTIMAS mensagens do operador que ficaram sem classificar. "
                        "Decida: elas CONTINUAM esse mesmo assunto, ou o operador MUDOU "
                        "para um assunto novo? Responda só um JSON: "
                        '{"decision": "continue" | "new", "title": "<se new, o tema '
                        "novo em 3-6 palavras, capitalização natural, sem aspas; se "
                        'continue, string vazia>"}. Na dúvida, prefira "continue" — não '
                        "fragmente um assunto por uma mensagem solta."
                    ),
                },
                {"role": "user", "content": joined},
            ],
            temperature=0.1,
            max_tokens=60,
        )
        data = _json.loads((resp.choices[0].message.content or "{}").strip())
    except Exception:  # noqa: BLE001 — rede/parse: absorção conservadora
        logger.debug("juiz do tail falhou", exc_info=True)
        return ("continue", None)

    decision = str(data.get("decision") or "continue").strip().lower()
    if decision == "new":
        title = str(data.get("title") or "").strip().strip('"') or None
        return ("new", title)
    return ("continue", None)


async def _resolve_pending_tail(
    db: Client,
    topic_id: str,
    pending_ids: list[str],
    cmsgs: list[CMsg],
    emb_map: dict[str, list[float]],
    *,
    active_id: str,
    active_title: Optional[str],
    knobs: Knobs,
) -> Optional[dict]:
    """Resolve o tail órfão sob silêncio prolongado. Devolve dict
    {ids, active_id, new_conversation?} com as msgs efetivamente stampadas, ou
    None se não havia nada a resolver.

    Tail sem voto informativo (só low-info/assistant) → absorve no ativo sem
    chamar o juiz (continuação trivial). Com voto → o juiz decide continue/new.
    'new' sem embedding pra formar centroide cai na absorção conservadora.
    """
    pend = set(pending_ids)
    tail = [m for m in cmsgs if m.id in pend]
    if not tail:
        return None
    all_ids = [m.id for m in tail]
    op_texts = [
        m.content
        for m in tail
        if m.role == "user" and is_informative(m.content, knobs)
    ]
    op_embs = [
        emb_map[m.id]
        for m in tail
        if m.role == "user" and emb_map.get(m.id)
    ]

    if not op_texts:
        # Continuação trivial: absorve no assunto ativo, sem gastar o juiz.
        _stamp_messages(db, all_ids, active_id)
        _touch_conversation(db, active_id)
        return {"ids": all_ids, "active_id": active_id}

    decision, new_title = await _judge_tail(active_title or "(sem título)", op_texts)

    if decision == "new" and op_embs:
        _set_dormant(db, active_id)
        centroid = _mean_vector(op_embs)
        conv = _create_conversation(
            db, topic_id, op_texts[0], centroid, title=new_title
        )
        _stamp_messages(db, all_ids, conv["id"])
        return {
            "ids": all_ids,
            "active_id": conv["id"],
            "new_conversation": {"id": conv["id"], "title": conv.get("title")},
        }

    # continue (ou 'new' sem embedding): absorção conservadora no assunto ativo.
    _stamp_messages(db, all_ids, active_id)
    _touch_conversation(db, active_id)
    return {"ids": all_ids, "active_id": active_id}


# ---------------------------------------------------------------------------
# Título (tema) + tags (catálogo frio) — UMA chamada GPT-4o-mini, modelo barato
# ---------------------------------------------------------------------------


async def _make_title_and_tags(texts: list[str]) -> tuple[Optional[str], list[str]]:
    """Nomeia o TEMA da conversation (3-6 palavras, legível) + 2-4 tags, numa
    única chamada GPT-4o-mini a partir das primeiras msgs do operador. LLM puro,
    sem DB. Best-effort: em falha, devolve (None, []) e o caller cai no título
    literal (1ª frase do seed). Roda no daemon, fora do caminho do turno.

    O título por tema substitui o antigo título-literal (1ª frase truncada),
    que era irreconhecível depois ('Eu tô vendo aí que pelas instruções…')."""
    joined = "\n".join(f"- {t.strip()}" for t in texts if (t or "").strip())[:1500]
    if len(joined) < 12:
        return None, []
    try:
        import json as _json

        from bot.conversation_detector import _get_openai, JUDGE_MODEL

        resp = await _get_openai().chat.completions.create(
            model=JUDGE_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você recebe as primeiras mensagens do operador numa "
                        "conversa e devolve um JSON com dois campos: "
                        '"title" = rótulo do TEMA em 3 a 6 palavras, ESPECÍFICO '
                        "(o que a conversa resolve, não categoria genérica tipo "
                        "'fluxo de trabalho'), capitalização natural em português, "
                        "sem aspas nem ponto final; "
                        '"tags" = lista de 2 a 4 tags curtas (1-2 palavras, '
                        "minúsculas, sem acento). Responda só o JSON."
                    ),
                },
                {"role": "user", "content": joined},
            ],
            temperature=0.2,
            max_tokens=80,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _json.loads(raw)
    except Exception:  # noqa: BLE001 — rede/parse: cai no fallback literal
        logger.debug("título/tags falhou", exc_info=True)
        return None, []

    title = (data.get("title") or "").strip().strip('"') or None
    if title and len(title) > 70:
        title = title[:70].rsplit(" ", 1)[0].strip()
    raw_tags = data.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.replace("\n", ",").split(",")
    tags = [str(t).strip().lower() for t in raw_tags]
    tags = [t for t in tags if 1 < len(t) <= 30][:4]
    return title, tags


def _upsert_tags(db: Client, conversation_id: str, tags: list[str]) -> None:
    """Persiste as tags da conversation (catálogo frio). Best-effort."""
    for tag in tags:
        try:
            db.table("conversation_tags").upsert(
                {"conversation_id": conversation_id, "tag": tag, "weight": 1.0},
                on_conflict="conversation_id,tag",
            ).execute()
        except Exception:  # noqa: BLE001
            logger.debug("upsert tag falhou conv=%s tag=%s", conversation_id, tag)
