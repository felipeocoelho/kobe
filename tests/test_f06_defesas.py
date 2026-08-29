"""Highlander v3, F0.6 — as duas defesas contra o envenenamento da memória na ENTRADA.

O defeito: em 3,6% dos áudios o Whisper **insere uma frase que o operador não disse**,
com forma de definição ("O que é o Cade? O Cade é…"). Dano gravado até hoje: zero — mas
o mecanismo é reprodutível (2/2), e um extrator melhor só grava o lixo com mais elegância.

Duas defesas, e o que cada bloco aqui prova:

- **T4** — a regra anti-ruído no `RETAIN_MISSION` (`bot/hindsight_client.py`) chega ao
  bank **que já existe** depois de um restart. É a pergunta que o brief manda conferir:
  `_configured_banks` reseta por processo e o `PATCH` é idempotente, mas isso é teoria
  até alguém bater no servidor. Roda contra o Hindsight de **DEV** (`:8890`); produção
  (`:8888`) não é tocada, nem por leitura.
- **T1–T3** — a instrumentação de `bot/transcribe.py`: pedir `verbose_json` e
  `temperature=0`, colher os três sinais nativos do Whisper sem decidir nada com eles,
  degradar limpo quando eles não vierem, não vazar conteúdo pro log, e respeitar os
  DOIS tetos do prompt de hints (bytes e tokens).

**O que estes testes NÃO provam** (e é honesto dizer, porque o ambiente não permite):
`dev_inject` não injeta áudio, então a perna áudio→texto é provada só por contrato,
contra uma resposta `verbose_json` de mentira. Fica dependendo de áudio real em
produção: (1) que a Groq devolve `segments` populados neste plano de conta; (2) que
`temperature=0` não muda o texto que sai; (3) qualquer julgamento sobre os VALORES dos
sinais — a faixa normal deles nos áudios do operador só existe depois de coleta.

Rodar:

    .venv/bin/python -m pytest tests/test_f06_defesas.py -q
"""

from __future__ import annotations

import logging
import os
import uuid

import httpx
import pytest

from bot import hindsight_client, transcribe as transcribe_mod
from bot.transcribe import MAX_HINTS_BYTES, MAX_HINTS_TOKENS, Transcriber

DEV_BASE_URL = os.getenv("HINDSIGHT_DEV_URL", "http://127.0.0.1:8890")


# --------------------------------------------------------------------------- #
# T4 — a regra anti-ruído chega ao bank EXISTENTE no restart
# --------------------------------------------------------------------------- #


def test_t4a_a_missao_carrega_a_regra_anti_ruido():
    """Unitário: a constante tem as duas partes — a original e a regra nova.

    Barato e sem rede, mas não é decorativo: se alguém reescrever `RETAIN_MISSION`
    e derrubar a regra sem querer, isto falha antes de chegar no servidor.
    """
    m = hindsight_client.RETAIN_MISSION
    assert "Extraia fatos duráveis" in m, "a missão original sumiu"
    assert "FORMA DE DEFINIÇÃO" in m, "a regra anti-ruído da F0.6 não está na missão"
    assert "na dúvida entre gravar e descartar" in m.lower(), "falta a regra de desempate"


@pytest.mark.asyncio
async def test_t4_bank_existente_recebe_a_missao_nova_no_restart():
    """O bank JÁ EXISTE com uma missão velha. Depois do restart, recebe a nova?

    Encenação, na ordem:
      1. cria o bank e o configura com uma missão VELHA (é o bank de produção de
         ontem, do ponto de vista do código de hoje);
      2. limpa `_configured_banks` — é exatamente o que um restart do processo faz;
      3. chama `_ensure_bank`, que é o caminho real (o `retain` passa por ele);
      4. lê o config de volta e exige a missão NOVA, com a regra anti-ruído dentro.

    Se isto falhar, o achado é maior que a correção: significa que ligar a defesa
    não basta — os banks vivos ficariam com a missão velha para sempre.
    """
    bank = f"kobe-dev-f06-{uuid.uuid4().hex[:8]}"
    missao_velha = "MISSÃO ANTIGA — sem a regra anti-ruído."
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.put(f"{DEV_BASE_URL}/v1/default/banks/{bank}", json={})
            assert r.status_code < 400, f"não criou o bank de teste: {r.status_code}"
            r = await client.patch(
                f"{DEV_BASE_URL}/v1/default/banks/{bank}/config",
                json={"updates": {"retain_mission": missao_velha}},
            )
            assert r.status_code < 400, f"não configurou a missão velha: {r.status_code}"

            # O bank já está configurado no servidor — mas o processo "reinicia".
            hindsight_client._configured_banks.discard(bank)
            await hindsight_client._ensure_bank(client, DEV_BASE_URL, bank)

            r = await client.get(f"{DEV_BASE_URL}/v1/default/banks/{bank}/config")
            assert r.status_code == 200, f"GET config falhou: {r.status_code}"
            config = (r.json() or {}).get("config") or {}
            atual = config.get("retain_mission") or ""

            assert atual != missao_velha, (
                "ACHADO: o bank existente FICOU com a missão velha depois do restart — "
                "ligar a defesa no código não a instalaria nos banks vivos"
            )
            assert atual == hindsight_client.RETAIN_MISSION, (
                "a missão gravada não é a do código:\n"
                f"  gravada: {atual[:120]!r}\n"
                f"  esperada: {hindsight_client.RETAIN_MISSION[:120]!r}"
            )
            assert "FORMA DE DEFINIÇÃO" in atual, "a regra anti-ruído não chegou ao bank"

            # E as disposições céticas continuam de pé (o PATCH manda tudo junto).
            assert config.get("disposition_skepticism") == hindsight_client.DISPOSITION_SKEPTICISM
            assert config.get("disposition_literalism") == hindsight_client.DISPOSITION_LITERALISM
        finally:
            hindsight_client._configured_banks.discard(bank)
            try:
                await client.delete(f"{DEV_BASE_URL}/v1/default/banks/{bank}")
            except Exception:  # noqa: BLE001 — limpeza best-effort
                pass


@pytest.mark.asyncio
async def test_t4c_producao_nunca_e_tocada_por_este_teste():
    """Trava explícita: a URL usada aqui é a de DEV, não a de produção.

    Parece bobo até o dia em que alguém exporta `HINDSIGHT_DEV_URL=…:8888` e a
    suíte reconfigura os banks vivos do operador.
    """
    assert ":8890" in DEV_BASE_URL or "8888" not in DEV_BASE_URL, (
        f"teste apontado para produção do Hindsight: {DEV_BASE_URL}"
    )


# --------------------------------------------------------------------------- #
# T1–T3 — a instrumentação de bot/transcribe.py
#
# Sem rede e sem centavo de API: o cliente da Groq é um fake que devolve uma
# resposta `verbose_json` de mentira. O que se prova aqui é CONTRATO — pedimos o
# formato certo, extraímos o texto igual, colhemos os três sinais, e nada quebra
# quando eles não vêm. O que só áudio real prova está declarado no topo do arquivo.
# --------------------------------------------------------------------------- #


class _RespostaVerboseJson:
    """Imita o `Transcription` do SDK: modelo com `text` + extras (`segments`)."""

    def __init__(self, text: str, segments):
        self.text = text
        self.segments = segments

    def model_dump(self):
        return {"text": self.text, "segments": self.segments}


class _RespostaSoModelDump:
    """Variante onde `segments` só aparece no `model_dump()` (extras do pydantic)."""

    def __init__(self, text: str, segments):
        self.text = text
        self._segments = segments

    def model_dump(self):
        return {"text": self.text, "segments": self._segments}


SEGMENTOS_OK = [
    {"text": " Bom dia, tudo certo.", "avg_logprob": -0.21, "compression_ratio": 1.30, "no_speech_prob": 0.01},
    {"text": " Vamos falar do Kobe.", "avg_logprob": -0.35, "compression_ratio": 1.55, "no_speech_prob": 0.04},
    # o segmento degenerado: logprob no chão, compressão nas alturas
    {"text": " O que é o Cade? O Cade é um código.", "avg_logprob": -1.42, "compression_ratio": 3.10, "no_speech_prob": 0.62},
]


def _transcriber_fake(resposta, captura: dict | None = None, hints_path=None):
    """Transcriber com cliente Groq fake — devolve `resposta` e captura os kwargs."""
    t = Transcriber(api_key="fake", hints_path=hints_path)

    class _FakeCreate:
        def create(self, **kwargs):
            if captura is not None:
                captura.update(kwargs)
            return resposta

    class _FakeAudio:
        transcriptions = _FakeCreate()

    class _FakeClient:
        audio = _FakeAudio()

    t._client = _FakeClient()
    return t


def test_t1_pede_verbose_json_e_temperature_zero():
    """A mudança de configuração chega mesmo na chamada — não ficou só na docstring."""
    captura: dict = {}
    t = _transcriber_fake(_RespostaVerboseJson("olá mundo", SEGMENTOS_OK), captura)
    t.transcribe(b"bytes", "voice.ogg")
    assert captura["response_format"] == "verbose_json", captura.get("response_format")
    assert captura["temperature"] == 0, captura.get("temperature")
    assert captura["model"] == t.model


def test_t2_texto_sai_igual_e_os_tres_sinais_sao_colhidos(caplog):
    """O texto não muda de forma; os três sinais aparecem no log, agregados."""
    t = _transcriber_fake(_RespostaVerboseJson("  olá mundo  ", SEGMENTOS_OK))
    with caplog.at_level(logging.INFO, logger="kobe.transcribe"):
        text, engine = t.transcribe(b"bytes", "voice.ogg")

    assert text == "olá mundo", repr(text)  # strip preservado, como antes
    assert engine == "groq-whisper"

    linha = next((r.getMessage() for r in caplog.records if "whisper_signals" in r.getMessage()), None)
    assert linha, "os sinais do Whisper não foram registrados"
    assert "segments=3" in linha, linha
    assert "avg_logprob_min=-1.420" in linha, linha   # o pior segmento
    assert "compression_ratio_max=3.100" in linha, linha
    assert "no_speech_max=0.620" in linha, linha


def test_t2_segmentos_so_no_model_dump_tambem_sao_lidos(caplog):
    """O SDK guarda campo extra fora do atributo — o fallback tem que pegar."""
    t = _transcriber_fake(_RespostaSoModelDump("olá", SEGMENTOS_OK))
    with caplog.at_level(logging.INFO, logger="kobe.transcribe"):
        text, _ = t.transcribe(b"bytes", "voice.ogg")
    assert text == "olá"
    assert any("whisper_signals" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "resposta,esperado",
    [
        (_RespostaVerboseJson("texto puro", None), "texto puro"),          # sem segments
        (_RespostaVerboseJson("texto puro", []), "texto puro"),            # segments vazio
        (_RespostaVerboseJson("texto puro", [{"text": " x "}]), "texto puro"),  # sem os campos
        ({"text": "texto puro", "segments": SEGMENTOS_OK}, "texto puro"),  # resposta dict
        ("texto puro", "texto puro"),                                      # formato antigo (str)
    ],
)
def test_t2b_degrada_limpo_quando_os_sinais_nao_vem(resposta, esperado, caplog):
    """Telemetria ausente não pode derrubar transcrição que já deu certo.

    É a propriedade que importa em produção: se a Groq mudar o payload, o operador
    perde o log dos sinais — não a transcrição do áudio dele.
    """
    t = _transcriber_fake(resposta)
    with caplog.at_level(logging.DEBUG, logger="kobe.transcribe"):
        text, engine = t.transcribe(b"bytes", "voice.ogg")
    assert text == esperado, repr(text)
    assert engine == "groq-whisper"


def test_t2c_o_log_nao_vaza_o_conteudo_do_audio(caplog):
    """Só números saem daqui. O que o operador FALA não entra em log.

    O log de um bot de mensagem vive em disco e em `journalctl`; conteúdo de áudio
    privado não tem por que estar lá pra render telemetria.
    """
    t = _transcriber_fake(_RespostaVerboseJson("olá mundo", SEGMENTOS_OK))
    with caplog.at_level(logging.DEBUG, logger="kobe.transcribe"):
        t.transcribe(b"bytes", "voice.ogg")
    tudo = "\n".join(r.getMessage() for r in caplog.records)
    for segmento in SEGMENTOS_OK:
        trecho = segmento["text"].strip()
        assert trecho not in tudo, f"o log vazou conteúdo do áudio: {trecho!r}"
    assert "Cade" not in tudo, "o log vazou conteúdo do áudio"


# --- T3: o guard de hints, agora nas DUAS unidades ------------------------- #


def _hints(tmp_path, conteudo: str):
    caminho = tmp_path / "hints.md"
    caminho.write_text(conteudo, encoding="utf-8")
    return _transcriber_fake(_RespostaVerboseJson("ok", []), hints_path=caminho)


def test_t3_hints_curto_passa_intacto(tmp_path):
    conteudo = "Kobe (não Kobi, não Colby)\nHAL (não Raul)\nHindsight (não HintSight)"
    t = _hints(tmp_path, conteudo)
    assert t._read_hints() == conteudo


def test_t3_hints_de_hoje_passa_sem_truncar(tmp_path):
    """O arquivo real do operador (209 bytes) tem que passar folgado.

    Um guard que trunca o arquivo legítimo seria uma regressão silenciosa: as dicas
    de vocabulário sumiriam do prompt sem ninguém notar.
    """
    conteudo = "x" * 209
    t = _hints(tmp_path, conteudo)
    assert t._read_hints() == conteudo


def test_t3_teto_de_tokens_morde_antes_do_de_bytes(tmp_path):
    """Texto sem acento: 800 bytes cabem no teto de bytes, mas não no de tokens.

    É exatamente o buraco que a F0.6 achou — o guard só olhava bytes, então este
    prompt seguia inteiro pra Groq, acima do limite documentado de 224 tokens.
    """
    conteudo = ", ".join(f"palavra{i:03d}" for i in range(60))  # ~718 bytes ASCII
    assert MAX_HINTS_BYTES > len(conteudo.encode("utf-8")) > MAX_HINTS_TOKENS * 2
    t = _hints(tmp_path, conteudo)
    saida = t._read_hints()
    assert len(saida) < len(conteudo), "não truncou pelo teto de tokens"
    assert transcribe_mod._estimate_tokens(saida) <= MAX_HINTS_TOKENS


def test_t3_teto_de_bytes_continua_valendo_com_acento(tmp_path):
    """pt-BR come 2 bytes por acento — o teto de bytes não pode ser esquecido."""
    conteudo = "coração, avião, então; " * 120  # muito acima dos dois tetos
    t = _hints(tmp_path, conteudo)
    saida = t._read_hints()
    assert len(saida.encode("utf-8")) <= MAX_HINTS_BYTES
    assert transcribe_mod._estimate_tokens(saida) <= MAX_HINTS_TOKENS


def test_t3_corte_cai_em_fronteira_nunca_no_meio_da_palavra(tmp_path):
    """Meia-palavra no prompt do Whisper é lixo que ele pode devolver transcrito.

    O arquivo de hints é lista de vocabulário; cortar "Hindsight" em "Hinds" injeta
    um termo que não existe justamente no campo que biasa o reconhecimento — o
    mesmo vetor de prompt-bleeding que a F0.6 está combatendo.
    """
    palavras = [f"vocabulario{i:03d}" for i in range(60)]
    t = _hints(tmp_path, ", ".join(palavras))
    saida = t._read_hints()
    for pedaco in saida.split(", "):
        assert pedaco.strip(" ,;\n") in palavras, f"corte no meio da palavra: {pedaco!r}"


def test_t3_truncamento_por_estimativa_avisa_em_warning(tmp_path, caplog):
    """Corte baseado em palpite avisa alto — quem lê o log merece saber."""
    t = _hints(tmp_path, ", ".join(f"palavra{i:03d}" for i in range(80)))
    with caplog.at_level(logging.DEBUG, logger="kobe.transcribe"):
        t._read_hints()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "truncamento por estimativa de token não avisou"
    assert "ESTIMADA" in warnings[0].getMessage()


def test_t3_hints_truncado_chega_truncado_na_chamada(tmp_path):
    """Fecha o circuito: não basta o guard cortar, o prompt enviado tem que ser o cortado."""
    captura: dict = {}
    conteudo = ", ".join(f"palavra{i:03d}" for i in range(80))
    caminho = tmp_path / "hints.md"
    caminho.write_text(conteudo, encoding="utf-8")
    t = _transcriber_fake(_RespostaVerboseJson("ok", []), captura, hints_path=caminho)
    t.transcribe(b"bytes", "voice.ogg")
    assert transcribe_mod._estimate_tokens(captura["prompt"]) <= MAX_HINTS_TOKENS
    assert len(captura["prompt"].encode("utf-8")) <= MAX_HINTS_BYTES
