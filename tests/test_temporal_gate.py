"""Testes do gate de referência temporal (modo observação).

O gate existe porque a regra "nada relativo ao TEMPO sem conferir o tempo" não
tinha gatilho: escrever um advérbio não parece uma ação. Estes testes travam as
três coisas que decidem se ele presta:

1. **Acende no que importa** — afirmação retrospectiva de verdade.
2. **NÃO acende no que é mobília** — e os casos aqui não são inventados: são os
   falso-positivos REAIS colhidos no corpus de 1.644 respostas do assistant
   (menção meta à própria palavra, hipótese, conhecimento geral do mundo), mais
   as construções que a sondagem mostrou serem linguagem corrente do agente
   ("agora" 57% das respostas, "hoje" 32%, "antes de" 39%, "quando você" 18%).
   Um gate que acende nessas acende em 36% dos turnos — que é o desenho errado.
3. **Flag off = caminho de hoje, bit a bit** — a resposta sai idêntica e o gate
   nem é consultado.

Rodar: .venv/bin/python -m pytest tests/test_temporal_gate.py -q
"""
from __future__ import annotations

import asyncio
import time

import pytest

from bot import temporal_gate
from bot.telegram_handler import _resolve_claude, _ToolCounter
from bot.progress import ProgressReporter


# ── Nível 1: o que DEVE acender ────────────────────────────────────────────

ACENDE = [
    # O caso que o operador nomeou: afirmação sobre quando algo aconteceu.
    "Isso subiu ontem às 21h, junto com o resto.",
    "O fix quebrou anteontem, no meio do deploy.",
    "A gente fechou esse desenho semana passada.",
    "O bot está reiniciado desde ontem.",
    "Rodou pela última vez há 3 dias.",
    "Faz umas duas semanas que não mexo nisso.",
    "O worker morreu 4 horas atrás.",
    "Na última vez que falamos disso, tinha ficado pendente.",
    "Recentemente esse caminho mudou.",
    "Na época a gente ainda usava o modelo antigo.",
    "O container está de pé há 11 dias.",
]


@pytest.mark.parametrize("texto", ACENDE)
def test_acende_em_afirmacao_retrospectiva(texto):
    r = temporal_gate.scan(texto, touched_temporal_source=False)
    assert r is not None, f"deveria acender: {texto!r}"
    assert r.markers
    assert r.action == "observe"


# ── Nível 1: o que NÃO pode acender ────────────────────────────────────────

NAO_ACENDE = [
    # ---- mobília da linguagem do agente (medida no corpus) ----
    "O fluxo agora é: você fala, o bot recebe, eu respondo.",          # agora 57%
    "Hoje cada conversa é meio amnésica entre invocações.",            # hoje 32%
    "Preciso conferir a data antes de sair buscando.",                 # antes de 39%
    "Quando você rodar, a sessão atual fica arquivada.",               # quando você 18%
    "Quando a gente sincronizar, ele espelha pro dev.",                # futuro
    "Você acabou de fundir dois itens pendentes num mecanismo só.",    # o próprio turno
    "Vamos deixar isso pra outro dia.",                                # futuro
    "O Messi joga uma fase mais cedo do que eu te disse.",             # comparativo
    "Tem que mexer sem parar desde o começo, senão empelota.",         # desde solto
    "Catálogo limpo desde o dia um.",                                  # idiom
    # ---- falso-positivos REAIS do corpus ----
    'Quando você falar referência temporal relativa ("essa semana", '
    '"amanhã", "ontem"), eu ancoro na data atual.',                    # menção meta
    # ---- ack / gentileza: promessa de voltar, não afirmação ----
    "Deixa eu olhar isso e já te volto.",
    "Vou conferir as duas fontes, volto em seguida.",
    "Depois eu vejo esse detalhe com calma.",
    # ---- vazio / trivial ----
    "",
    "Beleza.",
]


@pytest.mark.parametrize("texto", NAO_ACENDE)
def test_nao_acende_em_mobilia_hipotese_e_ack(texto):
    r = temporal_gate.scan(texto, touched_temporal_source=False)
    assert r is None, f"NÃO deveria acender: {texto!r}"


def test_nao_acende_dentro_de_bloco_de_codigo():
    texto = (
        "Segue o comando:\n\n"
        "```bash\n"
        "git log --since=ontem --until='semana passada'\n"
        "```\n\n"
        "É isso."
    )
    assert temporal_gate.scan(texto, touched_temporal_source=False) is None


def test_nao_acende_em_codigo_inline():
    texto = "Usa o `--since=ontem` que resolve."
    assert temporal_gate.scan(texto, touched_temporal_source=False) is None


def test_nao_acende_em_citacao_do_operador():
    texto = "> você disse: isso subiu ontem\n\nConfere o que temos hoje."
    assert temporal_gate.scan(texto, touched_temporal_source=False) is None


def test_limite_conhecido_hipotese_condicional_ainda_acende():
    """LIMITE CONHECIDO, fixado de propósito — não é um teste "passando".

    Marcador dentro de oração condicional ("se foi… da última vez") é hipótese,
    não afirmação: falso positivo real do corpus. Distinguir exige análise
    sintática, não regex — um padrão pra "se" abriria um buraco largo demais no
    recall pra pagar por este caso. Fica registrado aqui em vez de escondido:
    se alguém apertar isso um dia, este teste é que muda.

    Custo real hoje: uma linha de log a mais em modo observação. Nada além.
    """
    r = temporal_gate.scan(
        "Se foi só algum erro de entrega da última vez, era isso mesmo.",
        touched_temporal_source=False,
    )
    assert r is not None and r.markers == ("da última vez",)


def test_acende_fora_do_bloco_mesmo_com_bloco_presente():
    """A máscara descarta o marcador DENTRO do código — não a resposta inteira."""
    texto = (
        "```bash\ngit log --since=ontem\n```\n\n"
        "O deploy que quebrou isso foi ontem de manhã."
    )
    r = temporal_gate.scan(texto, touched_temporal_source=False)
    assert r is not None
    assert r.markers == ("ontem",)  # só a de fora


# ── Nível 1.5: o filtro de âncora ──────────────────────────────────────────
#
# O melhor sinal determinístico da investigação: a frase que já traz o dado
# absoluto ao lado da referência relativa não é o caso que o gate procura. Os
# exemplos abaixo são frases REAIS do corpus. Medido: com âncora, "não mexer" é
# a decisão certa em 89% dos casos; sem âncora, em 17%.

# Cada caso é um PAR: a frase ancorada (silencia) e a gêmea sem âncora (acende).
# O par é o que prova que o silêncio vem da ÂNCORA e não de a frase ser
# inofensiva — sem ele, um bug que silenciasse tudo passaria batido.
PARES_ANCORA = [
    ("hora",
     "O `kobe.service` está no ar desde 14/07 às 23:03, com `NRestarts=0`.",
     "O `kobe.service` está no ar desde ontem, sem reiniciar."),
    ("hora",
     "Worker subiu às 14:04 UTC, última atividade às 14:09 UTC (uns 5 min atrás).",
     "O worker subiu cedo e teve atividade há 5 minutos."),
    ("data com barra",
     "Os comandos estavam fora do menu desde 30/mai por causa do manifest.",
     "Os comandos estavam fora do menu desde ontem por causa do manifest."),
    ("data por extenso",
     "O último movimento foi em 7 de julho (15 dias atrás).",
     "O último movimento foi há 15 dias."),
    ("data ISO",
     "O brainstorm está completo e datado de ontem (2026-06-25).",
     "O brainstorm está completo e datado de ontem."),
    ("hash",
     "O artefato de ontem (`44233c4e`) tá ali registrando o estado.",
     "O artefato de ontem tá ali registrando o estado."),
    ("PID",
     "O processo (PID 4012126) está rodando desde ontem.",
     "O processo do bot está rodando desde ontem."),
    ("versão",
     "`v0.16.1` é release de ontem, já no público.",
     "Essa é a release de ontem, já no público."),
]


@pytest.mark.parametrize("tipo,ancorada,solta", PARES_ANCORA)
def test_filtro_de_ancora_silencia_frase_ja_ancorada(tipo, ancorada, solta):
    assert temporal_gate.scan(ancorada, touched_temporal_source=False) is None, (
        f"âncora de {tipo} deveria silenciar: {ancorada!r}"
    )


@pytest.mark.parametrize("tipo,ancorada,solta", PARES_ANCORA)
def test_a_gemea_sem_ancora_acende(tipo, ancorada, solta):
    assert temporal_gate.scan(solta, touched_temporal_source=False) is not None, (
        f"sem âncora de {tipo}, deveria acender: {solta!r}"
    )


def test_data_explicita_e_ancora_de_si_mesma():
    """"desde 24 de junho" NÃO é o caso que o gate procura: a data está escrita,
    é verificável, e não envelhece. O caso é "desde ontem", que parece preciso e
    não é. (Este teste registra uma mudança de comportamento deliberada: antes
    do filtro de âncora, esta frase acendia.)"""
    assert temporal_gate.scan("Isso está parado desde 24 de junho.",
                              touched_temporal_source=False) is None
    assert temporal_gate.scan("Isso está parado desde ontem.",
                              touched_temporal_source=False) is not None


def test_ancora_tem_escopo_de_FRASE_nao_de_resposta():
    """Uma resposta pode ter uma frase ancorada e outra solta — e só a solta
    deve acender. Se o escopo fosse a resposta inteira, a âncora de uma frase
    abafaria o problema da outra."""
    texto = ("O serviço está no ar desde ontem às 23:03, sem reiniciar. "
             "O deploy que quebrou isso foi ontem.")
    r = temporal_gate.scan(texto, touched_temporal_source=False)
    assert r is not None
    assert r.markers == ("ontem",)          # só a da segunda frase
    assert r.anchored_dropped == 1          # a da primeira foi contada e descartada


def test_a_propria_referencia_nao_serve_de_ancora_de_si_mesma():
    """"há 2 dias" contém dígitos; sem recortar o próprio trecho da janela, ele
    casaria o padrão de âncora e o gate se auto-silenciaria."""
    r = temporal_gate.scan("A sala está parada há 2 dias.",
                           touched_temporal_source=False)
    assert r is not None


def test_idiom_de_urgencia_nao_e_referencia_temporal():
    """Falso positivo real do corpus: "prioridade pra ontem" é urgência."""
    assert temporal_gate.scan(
        'Se "aprovar na sala" é prioridade pra ontem, vale fazer C nesta leva.',
        touched_temporal_source=False,
    ) is None


# ── Nível 2 (a): lastro ────────────────────────────────────────────────────


def test_grounded_reflete_o_rastro_de_ferramentas():
    texto = "O bot está no ar desde ontem às 21h."
    assert temporal_gate.scan(texto, touched_temporal_source=True).grounded is True
    assert temporal_gate.scan(texto, touched_temporal_source=False).grounded is False


@pytest.mark.parametrize(
    "tool,cmd,esperado",
    [
        ("Bash", "git log --oneline -5", True),
        ("Bash", "systemctl --user status kobe", True),
        ("Bash", "stat /opt/kobe/.env", True),
        ("Bash", "journalctl --user -u kobe | tail", True),
        ("Bash", "docker ps", True),
        ("Bash", "echo oi", False),
        ("Bash", "python -c 'print(1)'", False),
        ("Read", "", True),
        ("Grep", "", True),
        ("Glob", "", True),
        ("mcp__claude_ai_Google_Calendar__list_events", "", True),
        ("mcp__claude_ai_Todoist__find-tasks", "", True),
        ("WebSearch", "", False),
        ("TodoWrite", "", False),
        ("", "", False),
    ],
)
def test_is_temporal_source(tool, cmd, esperado):
    assert temporal_gate.is_temporal_source(tool, cmd) is esperado


def test_contadores_marcam_fonte_temporal():
    """Os dois contadores do turno (inline e background) veem o mesmo sinal."""
    evento = {
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "git log --since=yesterday"}},
        ]},
    }

    counter = _ToolCounter()
    counter.on_event(evento)
    assert counter.touched_temporal_source is True
    assert counter.count == 1

    reporter = ProgressReporter(bot=None, chat_id=1, thread_id=None)
    asyncio.run(reporter.on_event(evento))
    assert reporter.touched_temporal_source is True


def test_contadores_nao_marcam_sem_fonte():
    evento = {
        "type": "assistant",
        "message": {"content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "bot/bin/kobe-notify 'oi'"}},
        ]},
    }
    counter = _ToolCounter()
    counter.on_event(evento)
    assert counter.touched_temporal_source is False
    assert counter.acked is True  # a detecção de ack continua funcionando


# ── Flag ───────────────────────────────────────────────────────────────────


def test_flag_off_por_default(monkeypatch):
    monkeypatch.delenv("TEMPORAL_GATE_ENABLED", raising=False)
    assert temporal_gate.enabled() is False


@pytest.mark.parametrize("valor", ["true", "TRUE", "1", "on", "yes", "sim"])
def test_flag_liga(monkeypatch, valor):
    monkeypatch.setenv("TEMPORAL_GATE_ENABLED", valor)
    assert temporal_gate.enabled() is True


@pytest.mark.parametrize("valor", ["false", "0", "off", "", "  "])
def test_flag_desliga(monkeypatch, valor):
    monkeypatch.setenv("TEMPORAL_GATE_ENABLED", valor)
    assert temporal_gate.enabled() is False


# ── A trava principal: flag off = caminho de hoje ──────────────────────────


class _FakeResult:
    text = "Isso subiu ontem às 21h — bem no meio do deploy."
    input_tokens = 1
    output_tokens = 2
    cache_read_tokens = 0
    cache_creation_tokens = 0
    cost_usd = 0.0


async def _fake_claude_task():
    return _FakeResult()


def _resolve(**kwargs) -> str:
    async def run():
        task = asyncio.create_task(_fake_claude_task())
        return await _resolve_claude(
            task,
            started_at=time.monotonic(),
            prompt_len=10,
            history_len=1,
            tool_count_fn=lambda: 0,
            label="test",
            **kwargs,
        )

    return asyncio.run(run())


def test_flag_off_nao_consulta_o_gate_e_devolve_texto_identico(monkeypatch):
    """Com a flag off, o gate não é chamado NEM UMA VEZ e a resposta é a de hoje.

    A sabotagem é proposital: se o caminho tocasse o gate, o teste explodiria em
    vez de passar silenciosamente.
    """
    monkeypatch.setenv("TEMPORAL_GATE_ENABLED", "false")

    def explode(*a, **k):
        raise AssertionError("gate consultado com a flag OFF")

    monkeypatch.setattr(temporal_gate, "observe", explode)
    monkeypatch.setattr(temporal_gate, "scan", explode)

    assert _resolve() == _FakeResult.text


def test_flag_on_observa_mas_nao_altera_a_resposta(monkeypatch):
    """Modo observação: o gate roda, registra — e a resposta sai intacta."""
    monkeypatch.setenv("TEMPORAL_GATE_ENABLED", "true")
    vistos: list = []
    original = temporal_gate.observe

    def espiao(text, **kwargs):
        vistos.append((text, kwargs))
        return original(text, **kwargs)

    monkeypatch.setattr(temporal_gate, "observe", espiao)

    saida = _resolve(temporal_probe_fn=lambda: False)

    assert saida == _FakeResult.text          # <- a trava: resposta intocada
    assert len(vistos) == 1
    assert vistos[0][0] == _FakeResult.text
    assert vistos[0][1] == {"touched_temporal_source": False}


def test_gate_quebrado_nao_derruba_a_entrega(monkeypatch):
    """Um bug no gate NUNCA pode comer uma resposta do operador."""
    monkeypatch.setenv("TEMPORAL_GATE_ENABLED", "true")

    def explode(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(temporal_gate, "observe", explode)

    assert _resolve(temporal_probe_fn=lambda: False) == _FakeResult.text


def test_probe_ausente_nao_quebra(monkeypatch):
    """Chamada sem `temporal_probe_fn` (assinatura antiga) segue funcionando."""
    monkeypatch.setenv("TEMPORAL_GATE_ENABLED", "true")
    assert _resolve() == _FakeResult.text


# ── Custo ──────────────────────────────────────────────────────────────────


def test_nivel1_e_barato_no_caminho_comum():
    """Guarda-corpo contra regressão de desenho (ex.: alguém trocar o
    'casa-primeiro' por 'mascara-tudo-sempre', ou compilar regex por chamada).

    Medido no corpus real: ~145 µs por resposta de tamanho típico. O teto de
    5 ms aqui é folgado de propósito — não é benchmark, é alarme de incêndio;
    não falha por máquina lenta, só por desenho quebrado.
    """
    texto = ("Sobre o que você perguntou: o fluxo agora passa pelo assembler, "
             "e hoje isso já cobre o caso do upload. " * 20)  # ~2,4k chars
    assert temporal_gate.scan(texto, touched_temporal_source=False) is None

    inicio = time.perf_counter()
    for _ in range(200):
        temporal_gate.scan(texto, touched_temporal_source=False)
    por_chamada_ms = (time.perf_counter() - inicio) / 200 * 1000
    assert por_chamada_ms < 5.0, f"nível 1 custando {por_chamada_ms:.2f} ms/resposta"
