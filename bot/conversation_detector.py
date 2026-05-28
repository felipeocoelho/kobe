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
#
# Recalibração 2026-05-27 (junto com fix do detector cego a réplica
# direta): LOW baixado de 0.35 → 0.20. Msgs curtas/vagas do mesmo
# tema têm embedding fraco (sim ~0.25-0.35 contra o centroide), e
# antes caíam direto em open_new sem judge. Com LOW=0.20, expandem
# pra zona cinza, judge decide com base nos turnos da conv. Trocas
# reais de tema (sim ~0.10-0.25) ainda podem cair na zona cinza,
# mas o judge as identifica corretamente como tema novo. Custo
# extra estimado: +~$0.10/mês em chamadas adicionais ao judge.
THRESHOLD_HIGH = 0.55
THRESHOLD_LOW = 0.20

# Peso da mensagem nova ao atualizar centroide (EMA).
# 0.1 = conversation "esquece" devagar; vetor preserva tema dominante.
CENTROID_WEIGHT_NEW = 0.10

# Modelo do LLM judge. GPT-4o-mini é barato (~$0.15/1M tokens input)
# e rápido (~500ms-1s por call). Suficiente pra binary classification.
JUDGE_MODEL = "gpt-4o-mini"

# Bypass "resposta curta a pergunta direta" (2026-05-28). Quando o
# operador responde de forma muito curta a uma pergunta direta do
# agente (msg termina em '?'), o detector pula a análise de
# embedding/judge e força 'continue' na conversation ativa. Cobre
# caso real: agente "Flow ou Kobe?" → operador "Kobe". Sem o bypass,
# msg curta tem embedding genérico, judge decide cego e errava (visto
# em 2026-05-28).
SHORT_REPLY_MAX_CHARS = 60
SHORT_REPLY_MAX_WORDS = 6
SHORT_REPLY_MAX_AGE_SECONDS = 900  # 15 min


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
    last_agent_message: Optional[str] = None,
    last_agent_at: Optional[datetime] = None,
) -> DetectorResult:
    """Decide a qual conversation a mensagem nova pertence.

    Calcula DOIS embeddings (separação deliberada — ver design):

    - `decision_vec`: contextual (turno anterior do agente + msg nova
      do operador, se houver). Usado SÓ pra comparar contra centroides
      e decidir continue/reopen/open_new. Sem ele, msg do tipo "ué,
      como assim?" vira vetor genérico e o detector erra (não enxerga
      que é réplica direta à resposta anterior do agente).

    - `msg_vec`: limpo (só msg do operador). Usado pra atualizar
      centroide via EMA e como vetor inicial de conversation nova.
      Centroide representa "tema da conversation"; misturar resposta
      do agente nela contaminaria o tema com padrão de diálogo (ex:
      conversation de futebol cuja resposta foi "não tenho acesso"
      passaria a representar "limitação técnica" em vez de futebol).

    `last_agent_at` (opcional): timestamp da última msg do agente.
    Quando presente, habilita o bypass de "resposta curta a pergunta
    direta" — se a msg do operador é curta e a última fala do agente
    terminou em '?', força continue na ativa sem chamar embedding/
    judge. Cobre o caso real `/flow_lista` → 'Kobe' onde o judge
    decidia cego e errava.

    Idempotente — chamadas duplicadas com mesma msg+contexto dão
    mesmo resultado (cache de embedding + estado no banco).
    """
    if not message_text.strip():
        raise ValueError("message_text vazia")

    msg_vec = await embed(message_text)

    # Contextual só se houver turno anterior. Trunca a resposta do
    # agente em 2000 chars pra não estourar o limite de tokens do
    # embedding (8k tokens) com respostas longas — 2000 chars cobre
    # ~500 tokens de cauda, suficiente pra capturar o "fim" da
    # resposta que é o que o operador comenta normalmente.
    if last_agent_message and last_agent_message.strip():
        agent_tail = last_agent_message.strip()[-2000:]
        context_text = f"Agente: {agent_tail}\nOperador: {message_text}"
        decision_vec = await embed(context_text)
    else:
        decision_vec = msg_vec

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

    # Caso 1.5: bypass "resposta curta a pergunta direta". Roda antes
    # da análise de similaridade — quando dispara, ignora embedding/
    # judge e força continue. Só faz sentido se há ativa pra continuar.
    if active is not None and _is_short_reply_to_question(
        message_text=message_text,
        last_agent_message=last_agent_message,
        last_agent_at=last_agent_at,
        now=datetime.now(timezone.utc),
    ):
        logger.info(
            "short_reply_bypass conv_id=%s msg_len=%d agent_tail=%r",
            active["id"][:8],
            len(message_text.strip()),
            (last_agent_message or "")[-80:],
        )
        await _do_continue(db, active, msg_vec, sim=1.0)
        return DetectorResult(
            action="continue",
            conversation_id=active["id"],
            confidence=1.0,
            notice_text=None,
        )

    # Similaridade: usa max(contextual, isolada). O contextual cobre o
    # caso "réplica direta" (msg que reage ao agente sem repetir o tema),
    # mas pode DILUIR o sinal quando a msg isolada já carrega o tema e
    # a resposta anterior do agente era genérica/negativa ("não sei",
    # "vou checar"). Tomar o max preserva o melhor dos dois sem regressão.
    sim_active = _best_sim(active, decision_vec, msg_vec) if active else None
    best_dormant, sim_dormant = _best_dormant_match(dormants, decision_vec, msg_vec)

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
                db=db,
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
        db=db,
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


def _best_sim(
    conv: dict, decision_vec: list[float], msg_vec: list[float]
) -> float:
    """max(sim contextual, sim isolada) contra o centroide da conv.

    Tomar o max é deliberado: cada vetor cobre um padrão de msg
    diferente — contextual ajuda quando msg isolada é genérica
    (réplica direta); isolada ajuda quando a resposta anterior do
    agente dilui o sinal. O vetor que melhor case vence; o outro
    fica como "second opinion" e não atrapalha.
    """
    centroid = conv["centroid_embedding"]
    sim_ctx = cosine_similarity(centroid, decision_vec)
    if decision_vec is msg_vec:
        return sim_ctx
    sim_iso = cosine_similarity(centroid, msg_vec)
    return max(sim_ctx, sim_iso)


def _best_dormant_match(
    dormants: list[dict], decision_vec: list[float], msg_vec: list[float]
) -> tuple[Optional[dict], Optional[float]]:
    """Conversation dormant com maior similarity, e o valor. (None, None) se vazio.

    Usa `_best_sim` por conv — cada candidata é avaliada com
    max(contextual, isolada) contra seu próprio centroide.
    """
    if not dormants:
        return None, None
    best: Optional[dict] = None
    best_sim: float = -2.0
    for c in dormants:
        sim = _best_sim(c, decision_vec, msg_vec)
        if sim > best_sim:
            best_sim = sim
            best = c
    return best, best_sim


# ---------------------------------------------------------------------------
# Bypass "resposta curta a pergunta direta"
# ---------------------------------------------------------------------------


_TRAILING_PUNCT = set('!.…)"\' \t\r\n')


def _is_short_reply_to_question(
    *,
    message_text: str,
    last_agent_message: Optional[str],
    last_agent_at: Optional[datetime],
    now: datetime,
) -> bool:
    """True se a msg parece resposta curta a uma pergunta recente do agente.

    Critérios (todos devem bater):

    1. msg do operador é "curta" — ≤60 chars OU ≤6 palavras (usa OR
       deliberado: 'pode mandar sim por favor' tem 5 palavras mas 25
       chars, ambas formas curtas devem disparar).
    2. última fala do agente, depois de stripar pontuação repetida
       final ("?!", "??", "?...") termina em '?'.
    3. fala do agente é recente — ≤15 min desde agora.

    Quando todas batem, o detector ignora embedding/judge e força
    continue na conversation ativa. Trade-off documentado:

    - Cobre: respostas legítimas curtas a perguntas (~95% dos casos).
    - Falsos positivos aceitos: msg curta que MUDA de tema logo após
      pergunta do agente (ex: 'Bom dia' depois de '...quer listar?').
      Frequência baixa em uso real. Operador pode `/nova` se quiser
      separar.
    """
    if not last_agent_message or last_agent_at is None:
        return False
    trimmed = message_text.strip()
    if not trimmed:
        return False
    n_chars = len(trimmed)
    n_words = len(trimmed.split())
    if n_chars > SHORT_REPLY_MAX_CHARS and n_words > SHORT_REPLY_MAX_WORDS:
        return False

    tail = last_agent_message.rstrip()
    while tail and tail[-1] in _TRAILING_PUNCT and tail[-1] != "?":
        tail = tail[:-1]
    if not tail.endswith("?"):
        return False

    age = (now - last_agent_at).total_seconds()
    if age < 0 or age > SHORT_REPLY_MAX_AGE_SECONDS:
        return False
    return True


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


def _format_turns(turns: list[dict], *, max_chars_per_turn: int = 300) -> str:
    """Formata turnos pra exibição compacta no prompt do judge.

    Cada turno vira uma linha "- Operador: ..." ou "- Agente: ...",
    truncada a `max_chars_per_turn` chars pra não estourar o contexto
    do GPT-4o-mini com turnos longos. Roles desconhecidos viram a
    string crua do role (defensivo — não devemos ter outras roles
    além de 'user'/'assistant', mas system msgs de compactação podem
    aparecer e devem ser visíveis).
    """
    lines = []
    for t in turns:
        role = t.get("role", "?")
        label = {"user": "Operador", "assistant": "Agente"}.get(role, role)
        content = (t.get("content") or "").strip().replace("\n", " ")
        if len(content) > max_chars_per_turn:
            content = content[:max_chars_per_turn].rstrip() + "…"
        lines.append(f"   - {label}: {content}")
    return "\n".join(lines)


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
    db: Optional[Client] = None,
    active: Optional[dict],
    dormant: Optional[dict],
    message_text: str,
) -> Action:
    """Pergunta ao GPT-4o-mini: continue, reopen, ou open_new?

    Recebe título e ÚLTIMOS TURNOS da conversation ativa e do melhor
    dormant candidato (se houver), além da mensagem nova. Carregar
    turnos (não só título) é crítico: sem isso, o judge decide cego
    e tende a errar quando a msg nova é réplica direta à resposta
    anterior do agente (caso típico: "ué, como assim?" sem repetir
    o tema). Retorna ação como string. Em caso de erro/incerteza,
    retorna 'open_new' (default conservador).
    """
    if active is None and dormant is None:
        return "open_new"

    # Importação local pra evitar ciclo (topic_manager importa daqui
    # eventualmente em testes; manter import lazy é defensivo).
    from bot.topic_manager import get_last_messages_of_conversation

    active_turns = (
        get_last_messages_of_conversation(db, active["id"], limit=6)
        if (db is not None and active is not None) else []
    )
    dormant_turns = (
        get_last_messages_of_conversation(db, dormant["id"], limit=6)
        if (db is not None and dormant is not None) else []
    )

    options = []
    if active is not None:
        block = f"A) CONTINUAR a conversa atual ('{active['title']}'):"
        if active_turns:
            block += "\n" + _format_turns(active_turns)
        else:
            block += "\n   (sem turnos registrados)"
        options.append(block)
    if dormant is not None:
        block = (
            f"B) RETOMAR uma conversa dormente anterior "
            f"('{dormant['title']}'):"
        )
        if dormant_turns:
            block += "\n" + _format_turns(dormant_turns)
        else:
            block += "\n   (sem turnos registrados)"
        options.append(block)
    options.append("C) ABRIR conversa nova (tema totalmente diferente)")
    options_text = "\n\n".join(options)

    user_prompt = f"""Decida onde essa mensagem nova do usuário melhor encaixa.

Mensagem nova:
\"\"\"
{message_text[:1500]}
\"\"\"

Opções:

{options_text}

REGRAS:
- Se a mensagem é uma pergunta de follow-up, esclarecimento, reação
  ao agente, ou continuação natural de um dos diálogos acima →
  escolha A ou B, mesmo que ela use palavras diferentes do tema.
- Só escolha C se o assunto é GENUINAMENTE NOVO, sem ligação alguma
  com o que foi conversado.
- Em caso de dúvida, prefira continuar/retomar (A ou B) — é mais
  comum o usuário seguir o assunto do que mudar abruptamente.

Responda APENAS com a letra (A, B ou C). Nada mais."""

    try:
        resp = await _get_openai().chat.completions.create(
            model=JUDGE_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Você classifica mensagens em conversas existentes "
                        "ou novas. Prefira continuação (A/B) a abertura nova "
                        "(C) quando houver qualquer ligação temática ou de "
                        "diálogo. Responda apenas com A, B ou C."
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
