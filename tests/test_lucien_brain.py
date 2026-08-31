#!/usr/bin/env python3
"""O parser do LUCIEN — o que ele aceita e, sobretudo, o que ele recusa.

POR QUE O PARSER É ESTRITO E O ERRO É UMA EXCEÇÃO
--------------------------------------------------
Um parser tolerante devolveria "nenhuma afirmação" para uma resposta truncada —
e "nenhuma afirmação" é **indistinguível** de "o modelo leu e não achou nada
durável", que é a resposta certa na maioria das rodadas. As duas levariam o
cursor a avançar; só que uma delas teria pulado um pedaço da conversa **para
sempre**, sem deixar rastro.

Este sistema já transformou falha de instrumento em "não há registro" duas vezes
(a F0.5-B, com os embeddings tomando 401, e o `kobe-reflect` desistindo aos 20 s
de um servidor que respondia aos 28 s). A classe do erro é a mesma, e por isso
`CerebroIndisponivel` existe como exceção separada em vez de um retorno vazio.

Nenhum teste aqui chama modelo nenhum — as respostas são gravadas.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_lucien_brain.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import brain  # noqa: E402
from bot.lucien.brain import CerebroIndisponivel  # noqa: E402

PROPOSTA = {
    "claims": [{
        "subject": "normalizador de transcrição",
        "statement": "O normalizador roda ANTES de gravar a mensagem.",
        "kind": "decision",
        "source_seq": 3712,
        "evidence_seqs": [3712],
        "supersedes": ["E1"],
        "supersede_reason": "o operador disse que mudou de ideia",
    }],
    "closures": [{"claim_id": "E2", "action": "closed", "source_seq": 3713,
                  "reason": "foi implementado"}],
    "nothing_durable": False,
}


# ── As três formas que o parser aceita ────────────────────────────────────


def test_o_envelope_da_CLI_com_a_resposta_em_texto():
    """A forma REAL, conferida contra a CLI em 30/08/2026: o `claude -p
    --output-format json` devolve um envelope com metadados e o texto da
    resposta em `result`."""
    envelope = {
        "type": "result", "duration_api_ms": 3450, "stop_reason": "end_turn",
        "session_id": "6a3a172c", "total_cost_usd": 0.437,
        "usage": {"input_tokens": 2, "output_tokens": 25},
        "result": json.dumps(PROPOSTA, ensure_ascii=False),
    }
    p = brain.parsear(json.dumps(envelope))
    assert len(p.claims) == 1 and p.claims[0].source_seq == 3712
    assert p.claims[0].supersedes == ["E1"]
    assert len(p.closures) == 1 and p.closures[0].apelido == "E2"


def test_o_bloco_cercado_dentro_de_prosa():
    """A forma que aparece quando o modelo resolve explicar antes de responder.
    Recusar aqui seria perder uma resposta boa por causa da embalagem."""
    texto = (
        "Analisei o trecho e encontrei uma decisão:\n\n"
        "```json\n" + json.dumps(PROPOSTA, ensure_ascii=False) + "\n```\n"
        "Espero ter ajudado."
    )
    p = brain.parsear(json.dumps({"result": texto}))
    assert len(p.claims) == 1


def test_o_json_cru():
    p = brain.parsear(json.dumps(PROPOSTA, ensure_ascii=False))
    assert len(p.claims) == 1


def test_o_json_cru_no_meio_de_prosa_sem_cerca():
    texto = "Aqui está: " + json.dumps(PROPOSTA, ensure_ascii=False) + " — pronto."
    p = brain.parsear(json.dumps({"result": texto}))
    assert len(p.claims) == 1


def test_nada_duravel_e_uma_resposta_legitima():
    """E é a mais comum. A maior parte de uma conversa não estabelece nada."""
    p = brain.parsear(json.dumps({"result": json.dumps(
        {"claims": [], "closures": [], "nothing_durable": True})}))
    assert p.nothing_durable and not p.claims and not p.closures


# ── As respostas tortas: todas têm que LEVANTAR ───────────────────────────


def test_resposta_truncada_levanta_em_vez_de_virar_vazio():
    """O caso perigoso. Um JSON cortado no meio parseado com tolerância viraria
    "nada durável", o cursor avançaria, e o pedaço da conversa se perderia."""
    truncado = json.dumps(PROPOSTA, ensure_ascii=False)[: 120]
    with pytest.raises(CerebroIndisponivel, match="JSON inválido"):
        brain.parsear(json.dumps({"result": truncado}))


def test_resposta_sem_json_nenhum_levanta():
    with pytest.raises(CerebroIndisponivel, match="não achei JSON"):
        brain.parsear(json.dumps({"result": "Desculpe, não consegui analisar."}))


def test_resposta_vazia_levanta():
    with pytest.raises(CerebroIndisponivel):
        brain.parsear("")


def test_envelope_de_erro_da_CLI_levanta_com_as_chaves_na_mensagem():
    """A CLI pode devolver um envelope sem `result` (erro de sessão, recusa).
    A mensagem tem que dizer o que veio, senão o diagnóstico vira adivinhação."""
    with pytest.raises(CerebroIndisponivel, match="sem proposta reconhecível"):
        brain.parsear(json.dumps({"type": "error", "subtype": "auth", "is_error": True}))


def test_bloco_cercado_com_json_quebrado_levanta():
    with pytest.raises(CerebroIndisponivel, match="bloco cercado"):
        brain.parsear(json.dumps({"result": "```json\n{\"claims\": [ }\n```"}))


def test_resposta_que_e_lista_e_nao_objeto_levanta():
    with pytest.raises(CerebroIndisponivel):
        brain.parsear(json.dumps({"result": "[1, 2, 3]"}))


# ── O parser NÃO valida conteúdo — quem valida é o store ──────────────────


def test_o_parser_deixa_passar_lixo_de_conteudo_de_proposito():
    """A divisão de trabalho importa: o parser cuida da FORMA, `store.aplicar`
    cuida do CONTEÚDO. Juntar os dois faria a trava viver ao lado de quem ela
    desconfia, e uma mudança no formato de saída poderia afrouxar a validação
    sem ninguém perceber."""
    torto = {"claims": [{"subject": "x", "statement": "curta", "kind": "chute",
                         "source_seq": "não é número"}]}
    p = brain.parsear(json.dumps({"result": json.dumps(torto)}))
    assert len(p.claims) == 1, "o parser aceita; o store é quem recusa"
    assert p.claims[0].kind == "chute"


def test_o_juizo_de_legibilidade_e_parseado():
    """O único julgamento de confiança que se pede ao modelo. Ele só rebaixa —
    quem aplica é `store._criar`."""
    d = {"claims": [{"subject": "a", "statement": "b", "kind": "fact",
                     "source_seq": 1, "legibility_doubt": True,
                     "legibility_reason": "o nome do arquivo veio deturpado"}]}
    p = brain.parsear(json.dumps({"result": json.dumps(d)}))
    assert p.claims[0].legibility_doubt is True
    assert "deturpado" in p.claims[0].legibility_reason


def test_sem_o_campo_de_legibilidade_a_afirmacao_NAO_e_rebaixada():
    """Omitir é o caso comum — a maioria das afirmações não tem dúvida de
    legibilidade, e a ausência do campo não pode virar suspeita."""
    d = {"claims": [{"subject": "a", "statement": "b", "kind": "fact",
                     "source_seq": 1}]}
    p = brain.parsear(json.dumps({"result": json.dumps(d)}))
    assert p.claims[0].legibility_doubt is False


def test_o_prompt_diz_o_que_NAO_e_motivo_de_rebaixamento():
    """O lado negativo tem que estar escrito. Sem ele o modelo cai no reflexo de
    carimbar tudo que vem de áudio — e o operador usa voz como canal principal,
    por escolha de produto."""
    from bot.lucien import prompts

    # Normalizado: o prompt é quebrado em 79 colunas, e um teste que depende de
    # onde a linha quebra reprova por reformatação em vez de por conteúdo.
    texto = " ".join(prompts.LEGIBILIDADE.split())
    assert "NÃO marque quando" in texto
    assert "NUNCA é motivo" in texto
    assert "sim, plano aprovado" in texto, "falta o exemplo do caso inequívoco"
    assert "o trecho corrompido é justamente o que SUSTENTA" in texto, (
        "a pergunta central sumiu do prompt: o rebaixamento é POR AFIRMAÇÃO, "
        "não por mensagem"
    )


def test_campos_ausentes_viram_vazio_e_nao_estouram():
    """Campo faltando é caso normal (o modelo omite `evidence_seqs` quando não
    há). Estourar aqui transformaria uma resposta boa em rodada perdida."""
    magro = {"claims": [{"subject": "a", "statement": "b", "kind": "fact",
                         "source_seq": 1}]}
    p = brain.parsear(json.dumps({"result": json.dumps(magro)}))
    assert p.claims[0].evidence_seqs == [] and p.claims[0].supersedes == []


def test_supersedes_como_string_solta_vira_lista():
    """Modelo devolvendo `"supersedes": "E1"` em vez de `["E1"]` é erro de
    forma corriqueiro, e a informação está lá inteira."""
    d = {"claims": [{"subject": "a", "statement": "b", "kind": "fact",
                     "source_seq": 1, "supersedes": "E1"}]}
    p = brain.parsear(json.dumps({"result": json.dumps(d)}))
    assert p.claims[0].supersedes == ["E1"]


# ── O isolamento da chamada ───────────────────────────────────────────────


def test_a_chamada_nao_leva_o_canal_do_operador(monkeypatch):
    """LUCIEN não fala no chat. Sem `KOBE_CHAT_ID` no ambiente, um `kobe-notify`
    disparado lá dentro não teria para onde ir — o relatório é F5, e é do
    operador."""
    monkeypatch.setenv("KOBE_CHAT_ID", "-100123")
    monkeypatch.setenv("KOBE_THREAD_ID", "7")
    env = brain._ambiente("/tmp", "/tmp/cfg")
    assert "KOBE_CHAT_ID" not in env and "KOBE_THREAD_ID" not in env


def test_a_chamada_isola_a_configuracao_do_claude_code(monkeypatch):
    """A lição medida da F0.5: sem isolar, a chamada dispara os hooks e plugins
    do operador — laço recursivo de gravação de memória. O par exato
    (`CLAUDE_CONFIG_DIR` + `CLAUDE_SECURESTORAGE_CONFIG_DIR` vazio) é o que
    isola SEM derrubar a autenticação."""
    env = brain._ambiente("/tmp", "/tmp/cfg-isolado")
    assert env["CLAUDE_CONFIG_DIR"] == "/tmp/cfg-isolado"
    assert env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] == ""


def test_o_cerebro_roda_sem_ferramenta_e_sem_mcp():
    """Medido em 30/08/2026: com as flags, 13.154 tokens de contexto por
    chamada; sem elas, 43.039. E o cérebro não precisa de ferramenta nenhuma —
    ele recebe texto e devolve JSON."""
    fonte = (RAIZ / "bot" / "lucien" / "brain.py").read_text(encoding="utf-8")
    assert '"--tools", ""' in fonte
    assert '"--strict-mcp-config"' in fonte
    assert '"--system-prompt", SYSTEM' in fonte


def test_a_CLI_ausente_vira_CerebroIndisponivel_e_nao_FileNotFound(monkeypatch):
    """Quem chama trata `CerebroIndisponivel`; um `FileNotFoundError` cru
    subiria pelo worker e derrubaria a rodada com um erro que não diz nada
    sobre memória."""
    def _explodir(*a, **k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(brain.subprocess, "run", _explodir)
    with pytest.raises(CerebroIndisponivel, match="não foi encontrada"):
        brain.chamar("oi", kobe_home="/tmp")


def test_timeout_vira_CerebroIndisponivel_dizendo_quantos_segundos(monkeypatch):
    import subprocess as sp

    def _estourar(*a, **k):
        raise sp.TimeoutExpired(cmd="claude", timeout=180)

    monkeypatch.setattr(brain.subprocess, "run", _estourar)
    with pytest.raises(CerebroIndisponivel, match="180s"):
        brain.chamar("oi", kobe_home="/tmp", timeout=180)
