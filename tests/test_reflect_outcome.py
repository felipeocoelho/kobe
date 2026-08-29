"""O falso negativo silencioso do `reflect` — cobertura do conserto de 29/08/2026.

O DEFEITO: `reflect()` devolvia `Optional[dict]`, e `None` significava cinco coisas
diferentes ao mesmo tempo — acervo vazio, timeout, serviço fora, HTTP 500, JSON
quebrado. O `bot/bin/kobe-reflect` imprimia a MESMA frase ("sem registro durável")
para todas, e o agente que lê essa saída é instruído pelo CLAUDE.md a tratar vazio
como ausência de registro. Resultado medido em 29/08/2026: o servidor concluiu em
28,1 s com resposta boa e fontes citadas, o cliente cortou aos 20 s, e a memória
durável "disse" que não havia registro. Instrumento que mente.

Agravante que estes testes fixam: `str(httpx.ReadTimeout)` é **string vazia**. O log
do incidente saiu literalmente como `reflect falhou (best-effort): ` e nada depois —
não dava nem pra saber que tinha sido tempo. Por isso T1 assere o CONTEÚDO do
`detail`, não só o status: um detail vazio é a regressão exata do bug.

T7 é o teste da garantia inegociável: `reflect` é best-effort e **não pode levantar**.
O conserto trocou o tipo de retorno; não pode ter trocado essa propriedade junto.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import httpx
import pytest

from bot import hindsight_client

_REPO_ROOT = Path(__file__).resolve().parent.parent
_HELPER = _REPO_ROOT / "bot" / "bin" / "kobe-reflect"


class _ClienteFalso:
    """Cliente httpx de mentira: ou levanta o que mandarem, ou devolve a resposta."""

    def __init__(self, *, exc=None, response=None):
        self._exc = exc
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        if self._exc is not None:
            raise self._exc
        return self._response


def _finge(monkeypatch, *, exc=None, response=None):
    monkeypatch.setattr(
        hindsight_client.httpx,
        "AsyncClient",
        lambda **kw: _ClienteFalso(exc=exc, response=response),
    )


def _resposta(status: int, corpo: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status, json=corpo if corpo is not None else {},
        request=httpx.Request("POST", "http://x/reflect"),
    )


# ── T1–T4, T7: cada falha tem NOME ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_t1_timeout_tem_status_e_detail_falante(monkeypatch):
    """T1 — o caso do incidente. `ReadTimeout` vinha com `str()` VAZIO; o detail
    tem que ser construído no código, com os segundos, ou o bug volta mudo."""
    _finge(monkeypatch, exc=httpx.ReadTimeout(""))
    out = await hindsight_client.reflect("http://x", "bank", "pergunta", timeout=90.0)
    assert out.status == "timeout"
    assert not out.ok
    assert out.data is None
    assert out.detail.strip(), "detail vazio é exatamente a regressão do bug"
    assert "90" in out.detail


@pytest.mark.asyncio
async def test_t2_conexao_recusada_e_servico_fora(monkeypatch):
    """T2 — serviço fora não é lentidão; quem lê tem que ir olhar o container."""
    _finge(monkeypatch, exc=httpx.ConnectError("recusada"))
    out = await hindsight_client.reflect("http://x:9", "bank", "pergunta")
    assert out.status == "servico_fora"
    assert "http://x:9" in out.detail


@pytest.mark.asyncio
async def test_t3_connect_timeout_cai_em_servico_fora(monkeypatch):
    """T3 — prova a ORDEM dos `except`. `ConnectTimeout` É subclasse de
    `TimeoutException`: se a ordem inverter, um Hindsight fora do ar passa a ser
    reportado como 'demorou', e se procura performance onde o problema é o serviço."""
    _finge(monkeypatch, exc=httpx.ConnectTimeout(""))
    out = await hindsight_client.reflect("http://x", "bank", "pergunta")
    assert out.status == "servico_fora"


@pytest.mark.asyncio
async def test_t4_http_nao_2xx_carrega_o_codigo(monkeypatch):
    """T4 — HTTP 503 é falha de serviço, não acervo vazio."""
    _finge(monkeypatch, response=_resposta(503))
    out = await hindsight_client.reflect("http://x", "bank", "pergunta")
    assert out.status == "http_error"
    assert "503" in out.detail


@pytest.mark.asyncio
async def test_t7_excecao_arbitraria_nao_escapa(monkeypatch):
    """T7 — a garantia inegociável: best-effort, `reflect` NUNCA levanta."""
    _finge(monkeypatch, exc=RuntimeError("qualquer coisa inesperada"))
    out = await hindsight_client.reflect("http://x", "bank", "pergunta")
    assert out.status == "erro"
    assert "RuntimeError" in out.detail


@pytest.mark.asyncio
async def test_t7b_json_quebrado_tambem_nao_escapa(monkeypatch):
    """T7b — 2xx com corpo que não é JSON: erro, e não 'sem registro'."""
    resp = httpx.Response(
        200, content=b"<html>nada disso</html>",
        request=httpx.Request("POST", "http://x/reflect"),
    )
    _finge(monkeypatch, response=resp)
    out = await hindsight_client.reflect("http://x", "bank", "pergunta")
    assert out.status == "erro"


# ── T5–T6: o sucesso, e o "vazio LEGÍTIMO" que não pode ser confundido ────────


@pytest.mark.asyncio
async def test_t5_resposta_boa_vira_secao_citada(monkeypatch):
    """T5 — caminho feliz ponta a ponta: outcome ok → seção com texto e fontes."""
    corpo = {
        "text": "O operador decidiu usar git pra deploy.",
        "based_on": {"memories": [{"document_id": "doc-1", "occurred_start": "2026-06-13"}]},
    }
    _finge(monkeypatch, response=_resposta(200, corpo))
    out = await hindsight_client.reflect("http://x", "bank", "pergunta")
    assert out.ok and out.status == "ok"
    secao = hindsight_client.render_reflect_section(out)
    assert secao is not None
    assert "git pra deploy" in secao
    assert "doc-1" in secao


@pytest.mark.asyncio
async def test_t6_2xx_sem_texto_e_ok_mas_sem_secao(monkeypatch):
    """T6 — a distinção que é o ponto de tudo: o servidor RESPONDEU (`ok`) e o
    acervo não cobre (`render` → None). Este é o único 'não há registro' que se
    pode afirmar. Antes do conserto ele era indistinguível de um 500."""
    _finge(monkeypatch, response=_resposta(200, {"text": ""}))
    out = await hindsight_client.reflect("http://x", "bank", "pergunta")
    assert out.ok, "2xx é 'o servidor respondeu', mesmo sem texto"
    assert hindsight_client.render_reflect_section(out) is None


@pytest.mark.asyncio
async def test_pergunta_vazia_nao_bate_na_rede(monkeypatch):
    _finge(monkeypatch, exc=AssertionError("não devia ter chamado a rede"))
    out = await hindsight_client.reflect("http://x", "bank", "   ")
    assert out.status == "sem_pergunta"


# ── T8: retrocompat do render ────────────────────────────────────────────────


def test_t8_render_ainda_aceita_dict_cru():
    """T8 — quem passava o corpo direto (assinatura antiga) não regride."""
    secao = hindsight_client.render_reflect_section({"text": "fato antigo"})
    assert secao is not None and "fato antigo" in secao
    assert hindsight_client.render_reflect_section(None) is None
    assert hindsight_client.render_reflect_section({}) is None


# ── T9: o teto e a env var, no helper ────────────────────────────────────────


def _helper():
    """Carrega `bot/bin/kobe-reflect` como módulo (o arquivo não tem extensão)."""
    loader = importlib.machinery.SourceFileLoader("kobe_reflect_helper", str(_HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_t9_teto_default_e_90(monkeypatch):
    """T9 — sem env, 90 s. O valor que era 20 e cortava a chamada fria de 28,1 s."""
    assert hindsight_client.REFLECT_TIMEOUT_DEFAULT == 90.0
    mod = _helper()
    monkeypatch.setattr(mod, "read_dotenv", lambda keys: {})
    assert mod._timeout() == 90.0


def test_t9b_env_sobrescreve(monkeypatch):
    mod = _helper()
    monkeypatch.setattr(
        mod, "read_dotenv", lambda keys: {"HINDSIGHT_REFLECT_TIMEOUT": "150"}
    )
    assert mod._timeout() == 150.0


@pytest.mark.parametrize("lixo", ["noventa", "", "  ", "-5", "0"])
def test_t9c_valor_torto_cai_no_default_sem_morrer(monkeypatch, lixo):
    """Uma env malformada não pode ser o motivo de a memória durável ficar
    inacessível — degrada pro default e avisa no stderr."""
    mod = _helper()
    monkeypatch.setattr(
        mod, "read_dotenv", lambda keys: {"HINDSIGHT_REFLECT_TIMEOUT": lixo}
    )
    assert mod._timeout() == 90.0


def test_texto_de_falha_grita_que_nao_e_sem_registro():
    """A frase é a interface com o agente: se ela for branda, o falso negativo
    volta pela porta do comportamento, com o código certo."""
    mod = _helper()
    for status in ("timeout", "servico_fora", "http_error", "erro"):
        txt = mod._texto_de_falha(status, "detalhe qualquer", "http://x")
        assert "FALHA DO INSTRUMENTO" in txt
        assert 'NÃO é "sem registro"' in txt
        assert "detalhe qualquer" in txt
