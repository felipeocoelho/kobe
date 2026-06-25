"""Cliente do Hindsight — memória durável do Kobe (Highlander Frente 2.3).

Fala com o serviço Hindsight (modo serviço, REST) que roda no host
(`infra/hindsight/`), atrás da flag `HINDSIGHT_ENABLED`. Duas operações no turno:

- **recall** (na entrada): traz fatos duráveis relevantes pra mensagem atual e
  injeta no prompt — é o "trazer um assunto velho de volta" sem a maquinaria do
  Chat Manager.
- **retain** (no fim do turno): destila fato durável da mensagem DO OPERADOR
  (ground truth — ele disse), não da resposta gerada (que pode alucinar). Roda
  `async` no servidor, então não bloqueia o turno.

**Trava anti-alucinação (v4 §6, inegociável):** retain conservador e rastreável à
fonte (metadata com tópico + message_id + timestamp); o fato que o recall devolve
**obedece o contrato** — é PISTA, não verdade absoluta; o agente ainda confere
contra a fonte viva antes de afirmar (Memory OS proibida).

**Best-effort sempre:** qualquer falha (serviço fora, timeout, flag off) retorna
vazio/False e **nunca** levanta — o Hindsight jamais derruba um turno do Kobe.

Contrato REST verificado ao vivo (imagem 0.8.3):
- retain: `POST /v1/default/banks/{bank}/memories`  → `{"items":[{content,...}], "async":bool}`
- recall: `POST /v1/default/banks/{bank}/memories/recall`  → `{"query": "..."}`
  resposta: `{"results": [{"text", "type", "mentioned_at", "tags", ...}]}`
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

_SLUG_SANITIZE = re.compile(r"[^a-z0-9_-]+")


def bank_id_for_topic(slug: Optional[str]) -> str:
    """Bank por tópico — isolamento, igual ao resto da memória do Kobe (Dev Kobe
    não puxa Olimpo). Slug saneado pra caber no path da URL."""
    s = _SLUG_SANITIZE.sub("-", (slug or "general").strip().lower()).strip("-")
    return f"kobe-{s or 'general'}"


async def _ensure_bank(client: httpx.AsyncClient, base_url: str, bank_id: str) -> None:
    """Cria o bank se não existir (idempotente). Best-effort — se falhar, o
    retain/recall seguinte reporta o erro real."""
    try:
        await client.put(f"{base_url}/v1/default/banks/{bank_id}", json={})
    except Exception:  # noqa: BLE001 — best-effort
        logger.debug("hindsight: _ensure_bank falhou (segue mesmo assim)", exc_info=True)


async def retain(
    base_url: str,
    bank_id: str,
    content: str,
    *,
    context: Optional[str] = None,
    metadata: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Destila um fato durável (async no servidor). True se aceito, False em
    qualquer falha (best-effort — nunca levanta). `metadata` carrega a fonte."""
    content = (content or "").strip()
    if not content:
        return False
    item: dict = {"content": content}
    if context:
        item["context"] = context
    if metadata:
        # O Hindsight exige metadata como dict[str, str] (valor int/bool dá 422).
        # Coage tudo a string aqui pra robustez independente do chamador.
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
        # Sem exc_info: componente opcional; se o serviço cai, não enche o log
        # com traceback todo turno — uma linha basta pra diagnosticar.
        logger.warning("hindsight retain falhou (best-effort): %s", exc)
        return False


async def recall(
    base_url: str,
    bank_id: str,
    query: str,
    *,
    limit: int = RECALL_LIMIT_DEFAULT,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Traz até `limit` fatos duráveis relevantes. [] em qualquer falha."""
    query = (query or "").strip()
    if not query:
        return []
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/v1/default/banks/{bank_id}/memories/recall",
                json={"query": query},
            )
            resp.raise_for_status()
            results = (resp.json() or {}).get("results") or []
            return results[:limit]
    except Exception as exc:  # noqa: BLE001 — recall nunca derruba o turno
        logger.warning("hindsight recall falhou (best-effort): %s", exc)
        return []


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
