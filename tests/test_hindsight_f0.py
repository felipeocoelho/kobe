"""Bloco E do plano de testes da F0 — as duas linhas do cliente do Hindsight.

Os dois defeitos são anti-padrões que a **documentação oficial do Hindsight**
nomeia, e que o Kobe cometia:

1. **Retain sem carimbo de tempo.** "Missing `timestamp` on retain → disables
   temporal retrieval strategies". Sem eixo de tempo, "o que a gente decidiu em
   julho?" não tem como ordenar nada.
2. **Leitura sem `observation`.** O default do Hindsight é `world`+`experience`,
   e o Kobe replicava. `observation` é a camada consolidada — **507 dos 934
   fatos** do bank do operador estavam invisíveis.

E1–E3 são unitários (payload). E4–E5 batem no Hindsight de **dev** (`:8890`) num
bank descartável — são o que prova que o campo é ACEITO, e não só que a gente o
manda: um `422` silencioso viraria memória não gravada, que é o pior dos mundos.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from bot import hindsight_client

DEV_BASE_URL = os.getenv("HINDSIGHT_DEV_URL", "http://127.0.0.1:8890")


class _ClientEspiao:
    """Captura o corpo do POST sem rede — o teste é sobre o payload."""

    def __init__(self, capturado: dict):
        self.capturado = capturado

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def put(self, url, **kw):
        return httpx.Response(200, request=httpx.Request("PUT", url))

    async def patch(self, url, **kw):
        return httpx.Response(200, request=httpx.Request("PATCH", url))

    async def get(self, url, **kw):
        return httpx.Response(
            200, json={"directives": []}, request=httpx.Request("GET", url)
        )

    async def post(self, url, *, json=None, **kw):
        self.capturado["url"] = url
        self.capturado["body"] = json
        return httpx.Response(200, json={}, request=httpx.Request("POST", url))


@pytest.fixture
def espiao(monkeypatch):
    capturado: dict = {}
    monkeypatch.setattr(
        hindsight_client.httpx,
        "AsyncClient",
        lambda **kw: _ClientEspiao(capturado),
    )
    # O bank já "configurado" evita a fiação repetida no meio da captura.
    hindsight_client._configured_banks.add("bank-teste")
    return capturado


@pytest.mark.asyncio
async def test_e1_retain_manda_o_carimbo_de_tempo(espiao):
    """E1 — com `timestamp`, ele vai no item."""
    ok = await hindsight_client.retain(
        "http://x", "bank-teste", "o operador disse algo",
        timestamp="2026-07-14T10:30:00+00:00",
    )
    assert ok
    item = espiao["body"]["items"][0]
    assert item["timestamp"] == "2026-07-14T10:30:00+00:00"


@pytest.mark.asyncio
async def test_e2_retain_sem_timestamp_e_retrocompativel(espiao):
    """E2 — sem `timestamp`, a chave nem aparece (o servidor assume "agora")."""
    await hindsight_client.retain("http://x", "bank-teste", "conteudo")
    assert "timestamp" not in espiao["body"]["items"][0]


@pytest.mark.asyncio
async def test_e3_recall_inclui_observation_por_default(espiao):
    """E3 — os três tipos; era esta linha que escondia 507 dos 934 fatos."""
    await hindsight_client.recall("http://x", "bank-teste", "pergunta")
    assert espiao["body"]["types"] == ["world", "experience", "observation"]


@pytest.mark.asyncio
async def test_e3b_types_explicito_continua_mandando(espiao):
    """Quem pedir só um tipo continua tendo só aquele."""
    await hindsight_client.recall(
        "http://x", "bank-teste", "pergunta", types=["observation"]
    )
    assert espiao["body"]["types"] == ["observation"]


# ── E4/E5: contra o Hindsight de DEV, de verdade ──────────────────────────


def _dev_no_ar() -> bool:
    try:
        return httpx.get(f"{DEV_BASE_URL}/openapi.json", timeout=3).status_code == 200
    except Exception:  # noqa: BLE001
        return False


requer_dev = pytest.mark.skipif(
    not _dev_no_ar(),
    reason="Hindsight de dev fora do ar — cenários E4/E5 exigem o serviço",
)


@requer_dev
@pytest.mark.asyncio
async def test_e4_e5_a_api_de_dev_aceita_timestamp_e_observation():
    """E4/E5 — o servidor real aceita as duas coisas (nada de 422 calado).

    Bank descartável por execução: nenhuma memória de verdade é tocada.
    """
    bank = f"kobe-dev-f0-smoke-{uuid.uuid4().hex[:8]}"

    aceito = await hindsight_client.retain(
        DEV_BASE_URL,
        bank,
        "Bateria F0 do Highlander v3: este item existe só pra provar o carimbo.",
        document_id="f0-smoke",
        timestamp="2026-07-14T10:30:00+00:00",
        context="Bateria automatizada da sessão Coder da F0",
        tags=["topic:f0-smoke"],
    )
    assert aceito, "o retain com timestamp foi recusado pelo Hindsight de dev"

    # Não asseguramos QUE veio (a extração é assíncrona e usa LLM) — asseguramos
    # que a leitura com os três tipos é aceita e responde. Afirmar conteúdo aqui
    # seria testar o modelo do fornecedor, não o nosso código.
    resultados = await hindsight_client.recall(DEV_BASE_URL, bank, "bateria F0")
    assert isinstance(resultados, list)

    async with httpx.AsyncClient(timeout=10) as c:
        await c.delete(f"{DEV_BASE_URL}/v1/default/banks/{bank}")
