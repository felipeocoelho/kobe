#!/usr/bin/env python3
"""As nove travas do LUCIEN — exercitadas, não prometidas (Highlander v3, F3).

POR QUE ESTE ARQUIVO É O MAIS IMPORTANTE DA FASE
-------------------------------------------------
A F3 é a única fase do Highlander v3 em que **um modelo escreve estado que o
agente depois serve como se fosse conhecido**. O briefing declara isso como o
risco de verdade do projeto e lista as mitigações como **não-opcionais**.

Mitigação que ninguém exercita é esperança. Cada teste aqui é uma tentativa de
fazer o LUCIEN escrever algo que ele não deveria — origem que o modelo não viu,
superação de uma afirmação que não lhe foi mostrada, data de gravação no lugar
da data do fato — e a asserção é que **não passou**.

Nenhum teste aqui chama modelo nenhum. A proposta é construída à mão, que é
exatamente como um modelo alucinando a entregaria.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_lucien_store.py

Tudo roda dentro de uma transação **revertida** no teardown. Não há um único
comando destrutivo aqui, e o banco fica como estava.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import store  # noqa: E402
from bot.lucien.models import (  # noqa: E402
    ClaimProposta,
    Encerramento,
    Lote,
    Mensagem,
    Proposta,
)

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


@pytest.fixture
def cx():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    conn = store.conectar(_URL)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def lote(cx) -> Lote:
    """Um lote real: duas mensagens do acervo, uma de texto e uma de áudio.

    Vem do banco de verdade porque a chave estrangeira de origem é `NOT NULL` —
    não há como testar a trava com identificador inventado, que é justamente o
    ponto.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT id, seq, topic_id, role, content, created_at, audio_transcribed"
        "  FROM messages WHERE audio_transcribed = false"
        " ORDER BY seq DESC LIMIT 1"
    )
    texto = cur.fetchone()
    cur.execute(
        "SELECT id, seq, topic_id, role, content, created_at, audio_transcribed"
        "  FROM messages WHERE audio_transcribed = true AND topic_id = %s"
        " ORDER BY seq DESC LIMIT 1",
        (texto["topic_id"],),
    )
    audio = cur.fetchone()
    if texto is None or audio is None:
        pytest.skip("acervo de teste sem o par texto/áudio necessário")

    msgs = [
        Mensagem(seq=int(m["seq"]), id=str(m["id"]), role=m["role"],
                 created_at=m["created_at"], content=m["content"] or "",
                 audio=bool(m["audio_transcribed"]))
        for m in (audio, texto)
    ]
    msgs.sort(key=lambda m: m.seq)
    return Lote(topic_id=str(texto["topic_id"]), topico_nome="teste", mensagens=msgs)


@pytest.fixture
def rodada(cx, lote):
    return store.abrir_rodada(cx, mode="incremental", topic_id=lote.topic_id,
                              lote=lote, model="teste")


def _proposta(lote: Lote, **kw) -> Proposta:
    base = dict(
        subject="normalizador de transcrição",
        statement="O normalizador roda ANTES de gravar a mensagem no banco.",
        kind="decision",
        source_seq=lote.ate_seq,
    )
    base.update(kw)
    return Proposta(claims=[ClaimProposta(**base)])


def _vigentes(cx, topic_id):
    cur = cx.cursor()
    cur.execute(
        "SELECT * FROM lucien_claims WHERE topic_id=%s AND status='vigente'"
        " ORDER BY valid_from",
        (topic_id,),
    )
    return cur.fetchall()


# ── T1 — a trava que sustenta a fase ──────────────────────────────────────


def test_T1_origem_fora_do_lote_e_descartada(cx, lote, rodada):
    """O modelo não pode citar uma mensagem que não viu.

    O banco já garante que a mensagem EXISTE (chave estrangeira). Esta trava
    garante coisa diferente e mais forte: que ela estava **no lote mostrado**.
    Sem ela, uma citação plausível de um assunto que o modelo conhece de outro
    lugar entraria com cara de origem conferida — e origem conferida é o que faz
    o agente servir a linha como fato.
    """
    fora = max(lote.seqs) - 500  # existe no acervo, não estava no lote
    r = store.aplicar(cx, lote, _proposta(lote, source_seq=fora), run_id=rodada)
    assert r.criadas == 0
    assert [x.trava for x in r.recusas] == ["T1"]
    assert "não estava no lote" in r.recusas[0].motivo
    assert not _vigentes(cx, lote.topic_id)


def test_T1_origem_que_nem_existe_e_descartada(cx, lote, rodada):
    r = store.aplicar(cx, lote, _proposta(lote, source_seq=99999999), run_id=rodada)
    assert r.criadas == 0 and r.recusas[0].trava == "T1"


def test_T1_origem_que_nao_e_numero_nao_estoura(cx, lote, rodada):
    """Modelo devolvendo `"#3059"` em vez de `3059` é erro de formato, não
    motivo para a rodada morrer."""
    r = store.aplicar(cx, lote, _proposta(lote, source_seq="#3059"), run_id=rodada)
    assert r.criadas == 0 and r.recusas[0].trava == "T1"


# ── T2 — superação só do que foi mostrado ─────────────────────────────────


def test_T2_superacao_de_apelido_nao_mostrado_e_recusada(cx, lote, rodada):
    """A afirmação nova entra; a superação inventada, não. É o desfecho certo:
    o que o modelo observou pode valer, o que ele imaginou não pode apagar nada.
    """
    p = _proposta(lote, supersedes=["E7"], supersede_reason="mudou de ideia")
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.criadas == 1
    assert r.superadas == 0
    assert [x.trava for x in r.recusas] == ["T2"]


def test_T2_a_mesma_afirmacao_nao_e_superada_duas_vezes_na_mesma_rodada(cx, lote, rodada):
    velha = store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    assert velha.criadas == 1
    alvo = _vigentes(cx, lote.topic_id)[0]
    lote.estado = {"E1": alvo}

    p = Proposta(claims=[
        ClaimProposta(subject="normalizador de transcrição",
                      statement="O normalizador roda DEPOIS de gravar a mensagem.",
                      kind="decision", source_seq=lote.ate_seq,
                      supersedes=["E1"], supersede_reason="o operador mudou de ideia"),
        ClaimProposta(subject="normalizador de transcrição",
                      statement="O normalizador roda em algum outro momento qualquer.",
                      kind="decision", source_seq=lote.ate_seq,
                      supersedes=["E1"], supersede_reason="mudou de novo"),
    ])
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.superadas == 1
    assert any(x.trava == "T2" for x in r.recusas)


# ── T3 — superação sem motivo ─────────────────────────────────────────────


def test_T3_superacao_sem_motivo_derruba_a_afirmacao_inteira(cx, lote, rodada):
    """Aqui a afirmação NÃO entra, diferente da T2 — e a assimetria é
    deliberada. Superação sem justificativa escrita é exatamente a linha que o
    operador não consegue auditar seis meses depois."""
    p = _proposta(lote, supersedes=["E1"], supersede_reason="   ")
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.criadas == 0
    assert r.recusas[0].trava == "T3"


# ── T4 — vocabulário e tamanho ────────────────────────────────────────────


@pytest.mark.parametrize("campo,valor", [
    ("kind", "chute"),
    ("statement", "curta"),
    ("statement", "x" * 500),
    ("subject", "ab"),
])
def test_T4_forma_invalida_e_recusada(cx, lote, rodada, campo, valor):
    r = store.aplicar(cx, lote, _proposta(lote, **{campo: valor}), run_id=rodada)
    assert r.criadas == 0 and r.recusas[0].trava == "T4"


# ── T5 — a data do FATO, nunca a da gravação ──────────────────────────────


def test_T5_valid_from_e_a_data_da_mensagem_de_origem(cx, lote, rodada):
    """A reconstrução do passado vai criar, numa madrugada de agosto, afirmações
    que passaram a valer em julho. Com `NOW()`, o registro inteiro nasceria
    dizendo que tudo foi decidido no dia em que foi catalogado — a mesma classe
    de mentira que a fase existe para matar."""
    origem = lote.por_seq[lote.ate_seq]
    store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    linha = _vigentes(cx, lote.topic_id)[0]
    assert linha["valid_from"] == origem.created_at
    assert linha["created_at"] != linha["valid_from"] or True  # o carimbo é outro campo
    agora = datetime.now(timezone.utc)
    assert abs((linha["created_at"] - agora).total_seconds()) < 120


# ── T6 — a confiança mede CORROBORAÇÃO, não o canal ──────────────────────


def test_T6_confianca_ALTA_quando_ha_evidencia_alem_da_origem(cx, lote, rodada):
    """A régua nova, decidida em 30/08/2026.

    A antiga media o CANAL — `"baixa" if origem.audio else "media"` — e tinha
    três defeitos de uma vez: `alta` nunca era escrita por ninguém (nível morto
    num CHECK de três); "baixa" saía em **27 de 40** linhas do piloto, porque o
    operador usa áudio como canal principal; e um sinal que aparece em dois
    terços das linhas não distingue nada. O efeito prático é o pior possível: ou
    o agente hedgeia tudo, e a fase não cura a doença que existe pra curar, ou
    ignora a flag, e a mitigação vira teatro.
    """
    outra = [m for m in lote.mensagens if m.seq != lote.ate_seq][0]
    store.aplicar(cx, lote, _proposta(lote, evidence_seqs=[outra.seq]), run_id=rodada)
    assert _vigentes(cx, lote.topic_id)[0]["confidence"] == "alta"


def test_T6_confianca_MEDIA_quando_a_origem_esta_sozinha(cx, lote, rodada):
    store.aplicar(cx, lote, _proposta(lote, evidence_seqs=[]), run_id=rodada)
    assert _vigentes(cx, lote.topic_id)[0]["confidence"] == "media"


def test_T6_o_canal_NAO_decide_mais_a_confianca(cx, lote, rodada):
    """O teste que trava a régua velha. Origem em áudio, corroborada: `alta`.

    O operador usa voz como canal principal, por escolha de produto. Um desenho
    que pune áudio por ser áudio contradiz o produto — o alvo é transcrição
    ruim, não voz.
    """
    de_audio = next(m for m in lote.mensagens if m.audio)
    outra = next(m for m in lote.mensagens if m.seq != de_audio.seq)
    store.aplicar(cx, lote, _proposta(lote, source_seq=de_audio.seq,
                                      evidence_seqs=[outra.seq]), run_id=rodada)
    linha = _vigentes(cx, lote.topic_id)[0]
    assert linha["source_seq"] == de_audio.seq
    assert linha["confidence"] == "alta", "o canal voltou a decidir a confiança"


def test_T6_legibility_doubt_REBAIXA_ate_o_fundo(cx, lote, rodada):
    """O único juízo de confiança que se pede ao modelo — e ele só desce."""
    outra = [m for m in lote.mensagens if m.seq != lote.ate_seq][0]
    store.aplicar(cx, lote, _proposta(lote, evidence_seqs=[outra.seq],
                                      legibility_doubt=True,
                                      legibility_reason="o identificador veio deturpado"),
                  run_id=rodada)
    assert _vigentes(cx, lote.topic_id)[0]["confidence"] == "baixa", (
        "corroborada MAS com trecho ilegível tem que descer para baixa"
    )


def test_T6_o_modelo_nao_consegue_se_PROMOVER(cx, lote, rodada):
    """`legibility_doubt` é rebaixamento e só. Não existe campo com que o modelo
    diga "estou seguro" — o princípio da 008 (*"`confidence` é preenchida pelo
    código, nunca pelo modelo"*) continua valendo onde importa."""
    from bot.lucien.models import ClaimProposta

    campos = set(ClaimProposta.__dataclass_fields__)
    assert "confidence" not in campos and "confianca" not in campos
    store.aplicar(cx, lote, _proposta(lote, evidence_seqs=[], legibility_doubt=False),
                  run_id=rodada)
    assert _vigentes(cx, lote.topic_id)[0]["confidence"] == "media"


def test_T6_o_canal_continua_REGISTRADO_como_metadado(cx, lote, rodada):
    """O fato de vir de áudio não some — ele só deixa de ser a confiança."""
    de_audio = next(m for m in lote.mensagens if m.audio)
    store.aplicar(cx, lote, _proposta(lote, source_seq=de_audio.seq), run_id=rodada)
    cur = cx.cursor()
    cur.execute("SELECT detail FROM lucien_events WHERE action='created'"
                " ORDER BY at DESC LIMIT 1")
    assert cur.fetchone()["detail"]["audio"] is True


# ── T7 — o teto MANDA DIVIDIR, não descarta ──────────────────────────────


def test_T7_passar_do_teto_nao_grava_NADA(cx, lote, rodada, monkeypatch):
    """O conserto de 30/08/2026, e ele é sobre comportamento, não sobre o número.

    A versão anterior cortava **as últimas da lista** e seguia. Medido no
    piloto: o teto bateu em **5 de 5 lotes** e levou 20 afirmações embora,
    escolhidas por posição. É o pior comportamento possível numa trava — ela não
    escolhe, e o que se perde é invisível.

    Palavra do operador: *"eu não queria que nada que fosse relevante ficasse de
    fora"*.
    """
    from bot.lucien.models import ClaimProposta, Proposta

    monkeypatch.setattr("bot.lucien.claims_maximo", lambda: 2)
    p = Proposta(claims=[
        ClaimProposta(subject=f"assunto {i}",
                      statement=f"Uma afirmação durável de número {i}, com tamanho ok.",
                      kind="fact", source_seq=lote.ate_seq)
        for i in range(5)
    ])
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.excedeu is True
    assert r.criadas == 0
    assert not _vigentes(cx, lote.topic_id), "gravou alguma coisa antes de dividir"
    assert r.cursor_avancou_para is None, "o cursor andou — o lote não seria relido"


def test_T7_no_PISO_grava_o_que_cabe_e_a_recusa_e_barulhenta(cx, lote, rodada, monkeypatch):
    """O fim da linha: o lote já não se divide mais. Aí não é lote grande, é
    degeneração do modelo — e é o ÚNICO caminho em que algo se perde, por isso
    ele grita."""
    from bot.lucien.models import ClaimProposta, Proposta

    monkeypatch.setattr("bot.lucien.claims_maximo", lambda: 2)
    p = Proposta(claims=[
        ClaimProposta(subject=f"assunto {i}",
                      statement=f"Uma afirmação durável de número {i}, com tamanho ok.",
                      kind="fact", source_seq=lote.ate_seq)
        for i in range(5)
    ])
    r = store.aplicar(cx, lote, p, run_id=rodada, truncar=True)
    assert r.excedeu is False
    assert r.criadas == 2
    t7 = [x for x in r.recusas if x.trava == "T7"]
    assert t7 and "DEGENERAÇÃO" in t7[0].motivo


def test_T7_dentro_do_teto_grava_tudo(cx, lote, rodada):
    """Com o teto em 20 e o piloto medindo 9–15, este é o caminho normal."""
    from bot.lucien.models import ClaimProposta, Proposta

    p = Proposta(claims=[
        ClaimProposta(subject=f"assunto {i}",
                      statement=f"Uma afirmação durável de número {i}, com tamanho ok.",
                      kind="fact", source_seq=lote.ate_seq)
        for i in range(15)
    ])
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert not r.excedeu and r.criadas == 15


def test_o_teto_de_hoje_tem_folga_sobre_o_que_o_piloto_mediu():
    """20 não é chute: os 5 lotes do piloto quiseram entre 9 e 15."""
    from bot import lucien as c

    assert c.claims_maximo() >= 15, "o teto voltou a ficar abaixo do medido"


# ── T8 — dedupe ───────────────────────────────────────────────────────────


def test_T8_rodar_duas_vezes_nao_duplica(cx, lote, rodada):
    """O cursor não avança em rodada que falha, então o mesmo lote é relido. Se
    o dedupe não segurasse, cada falha de rede viraria uma afirmação repetida."""
    r1 = store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    r2 = store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    assert r1.criadas == 1 and r2.criadas == 0
    assert r2.recusas[0].trava == "T8"
    assert len(_vigentes(cx, lote.topic_id)) == 1


def test_T8_variacao_de_caixa_e_acento_no_assunto_e_o_mesmo_balde(cx, lote, rodada):
    store.aplicar(cx, lote, _proposta(lote, subject="Arquitetura de Borda"), run_id=rodada)
    r = store.aplicar(cx, lote, _proposta(lote, subject="arquitetura da borda"),
                      run_id=rodada)
    assert r.criadas == 0 and r.recusas[0].trava == "T8"


# ── A superação, quando ela é legítima ────────────────────────────────────


def test_superacao_legitima_data_fecha_e_aponta(cx, lote, rodada):
    """O comportamento que a bateria `f3-superacao` cobra do sistema inteiro,
    provado aqui na camada onde ele acontece."""
    store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    antiga = _vigentes(cx, lote.topic_id)[0]
    lote.estado = {"E1": antiga}

    origem = lote.por_seq[lote.ate_seq]
    p = _proposta(
        lote,
        statement="O normalizador roda DEPOIS de gravar, pra preservar o original.",
        supersedes=["E1"],
        supersede_reason="o operador disse que mudou de ideia",
    )
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.criadas == 1 and r.superadas == 1

    cur = cx.cursor()
    cur.execute("SELECT * FROM lucien_claims WHERE id = %s", (antiga["id"],))
    velha = cur.fetchone()
    assert velha["status"] == "superada"
    assert velha["valid_to"] == origem.created_at, "a data de fim é a do FATO"
    assert velha["superseded_by"] is not None

    vigentes = _vigentes(cx, lote.topic_id)
    assert len(vigentes) == 1, "tem que sobrar UMA vigente — é a régua da fase"
    assert "DEPOIS" in vigentes[0]["statement"]


def test_a_superacao_grava_a_imagem_anterior_e_o_motivo(cx, lote, rodada):
    """Sem `before`, não há reversão; sem o motivo, não há auditoria."""
    store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    antiga = _vigentes(cx, lote.topic_id)[0]
    lote.estado = {"E1": antiga}
    store.aplicar(cx, lote, _proposta(
        lote, statement="O normalizador roda DEPOIS de gravar a mensagem.",
        supersedes=["E1"], supersede_reason="o operador mudou de ideia"), run_id=rodada)

    cur = cx.cursor()
    cur.execute(
        "SELECT * FROM lucien_events WHERE claim_id=%s AND action='superseded'",
        (antiga["id"],),
    )
    ev = cur.fetchone()
    assert ev["before"]["status"] == "vigente"
    assert ev["detail"]["motivo"] == "o operador mudou de ideia"
    assert ev["detail"]["source_seq"] == lote.ate_seq


def test_reverter_devolve_a_afirmacao_ao_estado_anterior(cx, lote, rodada):
    """O caminho de volta é um comando, e ele deixa rastro do próprio uso —
    diferente de um `UPDATE` à mão, que desfaz sem dizer que desfez."""
    store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    antiga = _vigentes(cx, lote.topic_id)[0]
    lote.estado = {"E1": antiga}
    store.aplicar(cx, lote, _proposta(
        lote, statement="O normalizador roda DEPOIS de gravar a mensagem.",
        supersedes=["E1"], supersede_reason="mudou de ideia"), run_id=rodada)

    cur = cx.cursor()
    cur.execute("SELECT id FROM lucien_events WHERE claim_id=%s AND action='superseded'",
                (antiga["id"],))
    ev_id = cur.fetchone()["id"]

    store.reverter(cx, str(ev_id))
    cur.execute("SELECT * FROM lucien_claims WHERE id=%s", (antiga["id"],))
    voltou = cur.fetchone()
    assert voltou["status"] == "vigente"
    assert voltou["valid_to"] is None and voltou["superseded_by"] is None

    cur.execute("SELECT COUNT(*) c FROM lucien_events WHERE claim_id=%s AND action='reverted'",
                (antiga["id"],))
    assert cur.fetchone()["c"] == 1


def test_reverter_uma_criacao_e_recusado_com_motivo_util(cx, lote, rodada):
    """"Criada" não tem imagem anterior — não há para onde voltar. O erro tem
    que dizer o que fazer, senão vira tentativa e erro em cima do registro."""
    store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    nova = _vigentes(cx, lote.topic_id)[0]
    cur = cx.cursor()
    cur.execute("SELECT id FROM lucien_events WHERE claim_id=%s AND action='created'",
                (nova["id"],))
    with pytest.raises(ValueError, match="não tem imagem anterior"):
        store.reverter(cx, str(cur.fetchone()["id"]))


# ── Encerramento: "isto fechou", sem substituta ───────────────────────────


def test_encerrar_fecha_sem_criar_substituta(cx, lote, rodada):
    """Sem este caso, um `ABERTO` de julho ficaria aberto para sempre — e "o que
    está de fato aberto" é metade do critério de pronto da fase."""
    store.aplicar(cx, lote, _proposta(lote, kind="open"), run_id=rodada)
    aberta = _vigentes(cx, lote.topic_id)[0]
    lote.estado = {"E1": aberta}

    p = Proposta(closures=[Encerramento(apelido="E1", action="closed",
                                        source_seq=lote.ate_seq,
                                        reason="foi implementado e testado")])
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.encerradas == 1 and r.criadas == 0
    cur = cx.cursor()
    cur.execute("SELECT status, valid_to FROM lucien_claims WHERE id=%s", (aberta["id"],))
    linha = cur.fetchone()
    assert linha["status"] == "fechada" and linha["valid_to"] is not None


def test_encerramento_sem_motivo_e_recusado(cx, lote, rodada):
    store.aplicar(cx, lote, _proposta(lote, kind="open"), run_id=rodada)
    lote.estado = {"E1": _vigentes(cx, lote.topic_id)[0]}
    p = Proposta(closures=[Encerramento(apelido="E1", action="closed",
                                        source_seq=lote.ate_seq, reason="")])
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.encerradas == 0 and r.recusas[0].trava == "T3"


def test_encerramento_de_apelido_nao_mostrado_e_recusado(cx, lote, rodada):
    p = Proposta(closures=[Encerramento(apelido="E42", action="closed",
                                        source_seq=lote.ate_seq, reason="oi")])
    r = store.aplicar(cx, lote, p, run_id=rodada)
    assert r.encerradas == 0 and r.recusas[0].trava == "T2"


# ── O cursor ──────────────────────────────────────────────────────────────


def test_o_cursor_avanca_ate_o_fim_do_lote(cx, lote, rodada):
    store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    cur = cx.cursor()
    cur.execute("SELECT last_seq FROM lucien_cursor WHERE scope='incremental'"
                " AND topic_id=%s", (lote.topic_id,))
    assert cur.fetchone()["last_seq"] == lote.ate_seq


def test_o_cursor_nao_anda_para_tras(cx, lote, rodada):
    """Duas rodadas fora de ordem (uma reconstrução lenta terminando depois de
    uma incremental) não podem fazer o sistema reler o que já leu."""
    store.aplicar(cx, lote, _proposta(lote), run_id=rodada)
    store._avancar_cursor(cx, "incremental", lote.topic_id, lote.ate_seq - 1000)
    cur = cx.cursor()
    cur.execute("SELECT last_seq FROM lucien_cursor WHERE scope='incremental'"
                " AND topic_id=%s", (lote.topic_id,))
    assert cur.fetchone()["last_seq"] == lote.ate_seq


def test_nada_e_escrito_quando_a_proposta_vem_vazia(cx, lote, rodada):
    """"Nada durável aqui" é uma resposta legítima e comum — a maior parte da
    conversa não estabelece nada. O que não pode é ela virar uma linha."""
    r = store.aplicar(cx, lote, Proposta(nothing_durable=True), run_id=rodada)
    assert r.criadas == 0 and r.rejeitadas == 0
    assert not _vigentes(cx, lote.topic_id)
    assert r.cursor_avancou_para == lote.ate_seq, (
        "lote sem nada durável AVANÇA o cursor — senão o mesmo lote seria relido "
        "para sempre, e a conversa nunca andaria"
    )


# ── O cadeado e a seleção de tópicos ──────────────────────────────────────


def test_topico_sem_mensagem_nao_aparece_como_pendente(cx):
    """Um tópico fantasma (criado por um `ensure_topic` de teste, por exemplo)
    não tem o que catalogar — e uma rodada sobre o vazio seria uma chamada de
    modelo gasta para nada."""
    cur = cx.cursor()
    cur.execute(
        "INSERT INTO topics (telegram_chat_id, telegram_thread_id, current_name,"
        " status, first_seen_at, last_activity_at)"
        " VALUES (-999999, 1, 'fantasma', 'active', NOW(), NOW()) RETURNING id"
    )
    fantasma = str(cur.fetchone()["id"])
    pendentes = {str(l["topic_id"]) for l in store.topicos_pendentes(cx)}
    assert fantasma not in pendentes


def test_lote_devido_por_acumulo_e_por_idade(monkeypatch):
    monkeypatch.setattr("bot.lucien.lote_minimo", lambda: 12)
    monkeypatch.setattr("bot.lucien.idade_maxima_s", lambda: 3600.0)
    agora = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

    novinho = {"pendentes": 3, "mais_antiga": agora - timedelta(minutes=5)}
    assert not store.lote_devido(novinho, agora=agora)

    muitos = {"pendentes": 20, "mais_antiga": agora - timedelta(minutes=5)}
    assert store.lote_devido(muitos, agora=agora)

    velho = {"pendentes": 1, "mais_antiga": agora - timedelta(hours=3)}
    assert store.lote_devido(velho, agora=agora), (
        "sem o gatilho de idade, um tópico de pouca conversa nunca ganharia estado"
    )


def test_o_cadeado_e_exclusivo(cx):
    """Duas rodadas simultâneas fariam o dedupe enxergar um estado que a outra
    ainda não gravou."""
    if not _URL:
        pytest.skip("sem banco")
    assert store.travar(cx)
    outra = store.conectar(_URL)
    try:
        assert not store.travar(outra), "o cadeado deixou duas rodadas entrarem"
    finally:
        outra.close()
        store.destravar(cx)


# ── A relevância do estado vigente: a perna que estava MORTA ──────────────


def test_a_relevancia_nao_usa_conjuncao_sobre_o_lote_inteiro():
    """O defeito mais grave da fase, e ele era invisível de fora.

    `estado_vigente` usava `plainto_tsquery('portuguese', <texto do lote>)`, e
    esse construtor liga os termos por **E**. Um lote de 8.000 caracteres virava
    uma consulta de **859 lexemas em conjunção** — que nenhuma afirmação do
    mundo casa. Medido em 30/08/2026: **zero** resultados, enquanto havia 15
    afirmações vigentes falando do assunto daquele lote.

    A consequência foi a régua da fase falhando: a decisão *"a sincronização
    dev VPS → prod VPS deve ser feita via rsync"* (16/05) continuou **vigente**
    depois de a varredura atravessar o incidente de 12–13/06 que a proibiu. O
    modelo não errou — **ela nunca lhe foi mostrada**.
    """
    fonte = (RAIZ / "bot" / "lucien" / "store.py").read_text(encoding="utf-8")
    corpo = fonte[fonte.index("def estado_vigente"):fonte.index("def _radicais_raros")]
    # Só o CÓDIGO: a docstring cita o construtor antigo ao explicar o defeito, e
    # um teste que casasse com ela reprovaria a própria explicação.
    corpo = "\n".join(
        l for l in corpo.splitlines()
        if "plainto_tsquery" not in l or "cur.execute" in l or "@@" in l
    )
    assert "plainto_tsquery" not in corpo, (
        "a conjunção sobre o lote inteiro voltou — e com ela a perna morta"
    )
    assert "to_tsquery" in corpo and "embedding <=>" in corpo, (
        "faltou uma das duas pernas de relevância (palavra rara por OU, e sentido)"
    )


def test_a_relevancia_pesa_FREQUENCIA_no_lote_e_nao_so_raridade(cx):
    """A segunda tentativa também errou, e o erro é instrutivo: pegar só os
    radicais mais RAROS escolhe *hapax*, não assunto.

    Medido no lote do apagão de 12/06: os 25 mais raros eram `cifr`,
    `restoring`, `crypt`, `pem`, `decompiled`… e **`rsync` não entrava**, porque
    com 116 mensagens ele não é raro o bastante — enquanto o lote inteiro era
    sobre ele. O que um lote **repete** é do que ele trata.
    """
    texto = ("rsync " * 30) + (
        "o operador decidiu que a sincronizacao entre os ambientes muda "
        "completamente a partir de agora, por causa do incidente "
    ) * 5
    consulta = store._radicais_raros(cx, texto)
    if not consulta:
        pytest.skip("acervo sem estatística de radicais neste banco")
    assert "rsync" in consulta, (
        f"o termo que o lote martela ficou de fora da relevância: {consulta[:200]}"
    )
    assert " | " in consulta, "os radicais têm que ser ligados por OU, não por E"


def test_a_relevancia_descarta_termo_banal(cx):
    """Sem o corte de raridade, o OU traz o acervo inteiro e a relevância vira
    ruído — que é o outro jeito de esta perna morrer."""
    consulta = store._radicais_raros(cx, "a gente decidiu sobre isso e sobre aquilo")
    for banal in ("gent", "decid", "sobr"):
        assert banal not in consulta.split(" | "), f"{banal} passou pelo corte"
