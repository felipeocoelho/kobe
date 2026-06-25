"""Cliente do Hindsight — memória durável do Kobe (Highlander Frente 2.3 / v2).

Fala com o serviço Hindsight (modo serviço, REST) que roda no host
(`infra/hindsight/`). Três operações:

- **retain** (escrita, no turno): destila fato durável da mensagem DO OPERADOR
  (ground truth — ele disse), não da resposta gerada (que pode alucinar). Agrupada
  por `document_id` ESTÁVEL (= sessão) com `update_mode="append"` — a conversa vira
  UM documento que cresce, não N memórias soltas com UUID aleatório (o anti-padrão
  do plano v2 §6: "mensagem solta duplica documento; usar id estável"). Roda `async`
  no servidor → não bloqueia o turno. Atrás de `HINDSIGHT_RETAIN`.
- **recall** (leitura crua, na entrada): traz fatos duráveis (`world`/`experience`)
  relevantes — caminho barato (50–500ms), o agente raciocina. Cada resultado vem com
  rastreabilidade (`document_id`, `chunk_id`). Atrás de `HINDSIGHT_RECALL`.
- **reflect** (leitura sintetizada+citada): resposta markdown com `based_on.memories`
  (citações) — caminho confiável. O bank é configurado cético por construção
  (skepticism=5, literalism=5) + uma **directive** com a regra de Fundamentação, então
  o reflect "só responde do que está citado". Atrás de `HINDSIGHT_RECALL` (mesma flag
  de leitura; a F3 decide quando usar recall cru vs reflect).

**Por que retain só da msg do operador (e não a conversa de ambos os lados):** o plano
v2 quer "conversa inteira, não solta" — resolvido aqui agrupando por `document_id`
estável (conserta a duplicação, que era o defeito real). Mantém-se a escrita
conservadora (só o que o operador DISSE, ground truth) pra NÃO gravar resposta gerada
como fato — a dor nº1. As respostas do agente, sendo raw, não viram "fato do mundo";
e a leitura (recall/reflect) é cética por config. Trocar pra incluir os dois lados é
uma linha (`_conversation_document`), reversível.

**Trava anti-alucinação (inegociável):** o fato que a leitura devolve é PISTA, não
verdade — o agente confere contra a fonte viva antes de afirmar.

**Best-effort sempre:** qualquer falha (serviço fora, timeout, flag off) retorna
vazio/False e **nunca** levanta — o Hindsight jamais derruba um turno do Kobe.

Contrato REST verificado ao vivo (imagem 0.8.3, openapi):
- retain:  `POST /v1/default/banks/{bank}/memories` → `{"items":[MemoryItem], "async":bool}`
  MemoryItem: {content, context?, metadata?, document_id?, tags?, update_mode?}
- recall:  `POST /v1/default/banks/{bank}/memories/recall`
  `{"query", "types":[...], "budget":"low|mid|high", "tags":[...], "include":{...}}`
  → `{"results":[RecallResult{text,type,document_id,chunk_id,tags,mentioned_at,...}]}`
- reflect: `POST /v1/default/banks/{bank}/reflect`
  `{"query", "include":{"facts":bool}, "tags":[...]}` → `{"text", "based_on":{memories:[...]}}`
- config:  `PATCH /v1/default/banks/{bank}/config` → `{"updates":{...}}` (disposition_*, *_mission)
- directives: `GET|POST /v1/default/banks/{bank}/directives` → CreateDirectiveRequest{name,content}
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("kobe.hindsight")

DEFAULT_BASE_URL = "http://127.0.0.1:8888"
DEFAULT_TIMEOUT = 10.0
RECALL_LIMIT_DEFAULT = 5

# Disposições do bank (v2 F2) — cético + literal por CONSTRUÇÃO. É o "HAL, diga a
# verdade sempre" codificado na ferramenta, não na boa-vontade do modelo.
DISPOSITION_SKEPTICISM = 5  # 1=confiante … 5=cético
DISPOSITION_LITERALISM = 5  # 1=flexível … 5=literal
DISPOSITION_EMPATHY = 3     # neutro — não é um eixo relevante pra fato durável

# Missões do bank (steers a extração / a resposta do reflect).
RETAIN_MISSION = (
    "Extraia fatos duráveis e estáveis que o OPERADOR declarou sobre si, suas "
    "preferências, decisões, projetos e o mundo dele. Ignore conteúdo efêmero, "
    "saudações e ruído de transcrição. Nunca invente: registre só o que foi dito."
)
REFLECT_MISSION = (
    "Responda em português brasileiro. Responda SOMENTE a partir das memórias "
    "citadas. Se a evidência não cobre a pergunta, diga que não há registro — não "
    "preencha lacuna com suposição."
)

# A regra de Fundamentação, codificada como DIRECTIVE dura (injetada e obrigatória
# em todo reflect). Espelha o contrato anti-mentira do CLAUDE.md.
FUNDAMENTACAO_DIRECTIVE_NAME = "kobe-fundamentacao"
FUNDAMENTACAO_DIRECTIVE = (
    "Você só afirma como FATO o que está nas memórias citadas (com document_id/"
    "chunk_id). Todo o resto é hipótese — e hipótese se marca como hipótese. Se a "
    "evidência for parcial ou ausente, diga 'não há registro disso' em vez de "
    "inventar. Não crave causa, número ou estado a partir de evidência fraca."
)

_SLUG_SANITIZE = re.compile(r"[^a-z0-9_-]+")

# Banks já configurados NESTE processo (mission/dispositions/directive) — evita
# repetir a fiação a cada turno. Reset no restart (e a config é idempotente).
_configured_banks: set[str] = set()


def bank_id_for_topic(slug: Optional[str]) -> str:
    """Bank por tópico — isolamento, igual ao resto da memória do Kobe (Dev Kobe
    não puxa Olimpo). Slug saneado pra caber no path da URL."""
    s = _SLUG_SANITIZE.sub("-", (slug or "general").strip().lower()).strip("-")
    return f"kobe-{s or 'general'}"


def document_id_for_session(session_id) -> str:
    """ID de documento ESTÁVEL por sessão — a conversa vira um documento que cresce
    (append), não N memórias soltas. Conserta o anti-padrão 'UUID aleatório duplica'."""
    return f"session-{session_id}"


async def _ensure_bank(client: httpx.AsyncClient, base_url: str, bank_id: str) -> None:
    """Cria o bank se não existir (idempotente) e o configura uma vez por processo
    (mission + dispositions cético/literal + directive de Fundamentação). Best-effort."""
    try:
        await client.put(f"{base_url}/v1/default/banks/{bank_id}", json={})
    except Exception:  # noqa: BLE001 — best-effort
        logger.debug("hindsight: _ensure_bank falhou (segue mesmo assim)", exc_info=True)
        return
    if bank_id in _configured_banks:
        return
    await _configure_bank(client, base_url, bank_id)
    _configured_banks.add(bank_id)


async def _configure_bank(client: httpx.AsyncClient, base_url: str, bank_id: str) -> None:
    """Fiação best-practice do bank (idempotente): missões, disposições céticas e a
    directive de Fundamentação. Cada passo é best-effort — nenhum derruba o turno."""
    # 1) config: missões + disposições (PATCH /config, formato de campo Python).
    try:
        await client.patch(
            f"{base_url}/v1/default/banks/{bank_id}/config",
            json={"updates": {
                "retain_mission": RETAIN_MISSION,
                "reflect_mission": REFLECT_MISSION,
                "disposition_skepticism": DISPOSITION_SKEPTICISM,
                "disposition_literalism": DISPOSITION_LITERALISM,
                "disposition_empathy": DISPOSITION_EMPATHY,
            }},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hindsight: config do bank %s falhou: %s", bank_id, exc)
    # 2) directive de Fundamentação (idempotente: cria só se ainda não existe pelo nome).
    try:
        existing = await client.get(f"{base_url}/v1/default/banks/{bank_id}/directives")
        names = set()
        if existing.status_code == 200:
            data = existing.json() or {}
            for d in (data.get("directives") or data.get("items") or []):
                if isinstance(d, dict) and d.get("name"):
                    names.add(d["name"])
        if FUNDAMENTACAO_DIRECTIVE_NAME not in names:
            await client.post(
                f"{base_url}/v1/default/banks/{bank_id}/directives",
                json={
                    "name": FUNDAMENTACAO_DIRECTIVE_NAME,
                    "content": FUNDAMENTACAO_DIRECTIVE,
                    "priority": 100,
                    "is_active": True,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hindsight: directive do bank %s falhou: %s", bank_id, exc)


async def retain(
    base_url: str,
    bank_id: str,
    content: str,
    *,
    document_id: Optional[str] = None,
    context: Optional[str] = None,
    tags: Optional[list[str]] = None,
    update_mode: str = "append",
    metadata: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Destila um fato durável (async no servidor). True se aceito, False em
    qualquer falha (best-effort — nunca levanta).

    `document_id` estável + `update_mode="append"` agrupam a conversa num documento
    que cresce (vs N memórias soltas). `context`/`tags` melhoram extração e isolamento.
    `metadata` carrega a fonte (rastreabilidade)."""
    content = (content or "").strip()
    if not content:
        return False
    item: dict = {"content": content}
    if document_id:
        item["document_id"] = document_id
        item["update_mode"] = update_mode
    if context:
        item["context"] = context
    if tags:
        item["tags"] = list(tags)
    if metadata:
        # O Hindsight exige metadata como dict[str, str] (valor int/bool dá 422).
        item["metadata"] = {str(k): str(v) for k, v in metadata.items()}
    payload = {"items": [item], "async": True}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            await _ensure_bank(client, base_url, bank_id)
            resp = await client.post(
                f"{base_url}/v1/default/banks/{bank_id}/memories", json=payload
            )
            resp.raise_for_status()
            return True
    except Exception as exc:  # noqa: BLE001 — retain nunca derruba o turno
        logger.warning("hindsight retain falhou (best-effort): %s", exc)
        return False


async def recall(
    base_url: str,
    bank_id: str,
    query: str,
    *,
    limit: int = RECALL_LIMIT_DEFAULT,
    types: Optional[list[str]] = None,
    budget: str = "mid",
    tags: Optional[list[str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Traz até `limit` fatos duráveis crus relevantes (caminho barato). [] em falha.

    `types` default = ['world','experience'] (fato do mundo + experiência; observações
    de fora). `include.source_facts` liga a rastreabilidade (document_id/chunk_id)."""
    query = (query or "").strip()
    if not query:
        return []
    body: dict = {
        "query": query,
        "types": types or ["world", "experience"],
        "budget": budget,
        # Hindsight 0.8.3: liga a inclusão com {} (objeto vazio), não bool — a
        # rastreabilidade (document_id/chunk_id) já vem em cada RecallResult.
        "include": {"source_facts": {}},
    }
    if tags:
        body["tags"] = list(tags)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/v1/default/banks/{bank_id}/memories/recall", json=body
            )
            resp.raise_for_status()
            results = (resp.json() or {}).get("results") or []
            return results[:limit]
    except Exception as exc:  # noqa: BLE001 — recall nunca derruba o turno
        logger.warning("hindsight recall falhou (best-effort): %s", exc)
        return []


async def reflect(
    base_url: str,
    bank_id: str,
    query: str,
    *,
    tags: Optional[list[str]] = None,
    timeout: float = 20.0,
) -> Optional[dict]:
    """Resposta sintetizada + CITADA (caminho confiável, 1–10s). Devolve o dict
    `{text, based_on}` ou None em falha. `include.facts=True` traz as citações
    (`based_on.memories`). O bank é cético por config + directive de Fundamentação."""
    query = (query or "").strip()
    if not query:
        return None
    # Hindsight 0.8.3: liga as citações com {} (objeto vazio), não bool — vem em
    # based_on.memories.
    body: dict = {"query": query, "include": {"facts": {}}}
    if tags:
        body["tags"] = list(tags)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/v1/default/banks/{bank_id}/reflect", json=body
            )
            resp.raise_for_status()
            return resp.json() or None
    except Exception as exc:  # noqa: BLE001 — reflect nunca derruba o turno
        logger.warning("hindsight reflect falhou (best-effort): %s", exc)
        return None


def render_recall_section(results: list[dict]) -> Optional[str]:
    """Monta o bloco `[Memória durável recuperada]` pro prompt. None se vazio.

    A moldura é deliberadamente cética (contrato anti-mentira): é memória
    recuperada, PISTA, não verdade — o agente confirma contra a fonte viva."""
    if not results:
        return None
    parts = [
        "[Memória durável recuperada (Hindsight) — são PISTAS de turnos passados, "
        "podem estar desatualizadas. Trate como hipótese e confirme contra a fonte "
        "viva antes de afirmar como fato.]"
    ]
    for r in results:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        when = (r.get("mentioned_at") or "")[:10]
        typ = (r.get("type") or "fato").strip()
        suffix = f" — {when}" if when else ""
        parts.append(f"- ({typ}{suffix}) {text}")
    return "\n".join(parts) if len(parts) > 1 else None


def render_reflect_section(reflection: Optional[dict]) -> Optional[str]:
    """Monta o bloco `[Memória durável — resposta citada]` a partir do reflect.
    None se vazio. Inclui a resposta sintetizada + um rastro das citações."""
    if not reflection:
        return None
    text = (reflection.get("text") or "").strip()
    if not text:
        return None
    parts = [
        "[Memória durável — síntese citada do Hindsight (cético por construção). "
        "É PISTA de turnos passados; confirme contra a fonte viva antes de afirmar.]",
        text,
    ]
    based = (reflection.get("based_on") or {}).get("memories") or []
    cites = []
    for m in based[:5]:
        if not isinstance(m, dict):
            continue
        doc = m.get("document_id") or m.get("id") or ""
        when = (m.get("occurred_start") or "")[:10]
        if doc:
            cites.append(f"{doc}{f' ({when})' if when else ''}")
    if cites:
        parts.append("Fontes: " + "; ".join(cites))
    return "\n".join(parts)
