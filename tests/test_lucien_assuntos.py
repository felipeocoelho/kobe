#!/usr/bin/env python3
"""O assunto como unidade — a T11 e o caminho de volta (Highlander v3, F7.1).

O QUE SE TENTA AQUI
-------------------
Cada teste de banco é uma tentativa de fazer o curador **fragmentar o
vocabulário de novo** (criar um assunto que já existe com outra grafia), ou de
**perder uma afirmação por causa de assunto** (apelido inválido, título
torto) — e a asserção é que não aconteceu. Os testes sem banco guardam a
promessa da chave: desligada, o prompt é o de antes.

Nenhum teste chama modelo nem embedder: a proposta é construída à mão, e o
vetor do título é desligado por `monkeypatch` — a T11 roda nas pernas de slug e
trigrama, que são as que não dependem de rede.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_lucien_assuntos.py

Banco: tudo dentro de transação revertida no teardown.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import assuntos, brain, prompts, store  # noqa: E402
from bot.lucien.models import ClaimProposta, Lote, Mensagem, Proposta  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")
_AGORA = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _lote_sem_banco(**kw) -> Lote:
    m = Mensagem(seq=10, id="00000000-0000-0000-0000-000000000010", role="user",
                 created_at=_AGORA, content="vamos fazer a F7 assim", audio=False)
    lote = Lote(topic_id="t", topico_nome="Dev Kobe", mensagens=[m])
    lote.estado = {"E1": {"id": "c1", "kind": "decision", "valid_from": _AGORA,
                          "subject": "rótulo", "statement": "uma decisão que já vale",
                          "subject_id": "s1"}}
    for k, v in kw.items():
        setattr(lote, k, v)
    return lote


# ── A chave ──────────────────────────────────────────────────────────────


def test_chave_liga_por_omissao(monkeypatch):
    monkeypatch.delenv("LUCIEN_ASSUNTOS_ENABLED", raising=False)
    assert assuntos.habilitado() is True


@pytest.mark.parametrize("valor", ["false", "0", "off", "nao"])
def test_chave_desliga_explicitamente(monkeypatch, valor):
    monkeypatch.setenv("LUCIEN_ASSUNTOS_ENABLED", valor)
    assert assuntos.habilitado() is False


def test_chave_desligada_prompt_sem_nada_de_assunto():
    """Desligada (`assuntos=None`), o prompt não ganha uma letra de assunto — nem
    a lista, nem a régua, nem o contrato, nem a marca na linha do estado."""
    texto = prompts.montar(_lote_sem_banco())
    for marca in ("ASSUNTOS VIVOS", "ASSUNTO —", "CAMPOS DE ASSUNTO", "(A1)"):
        assert marca not in texto
    assert texto.endswith(prompts.CONTRATO.rstrip("\n")) or texto.endswith(prompts.CONTRATO)


def test_chave_ligada_prompt_traz_lista_regua_e_contrato():
    lote = _lote_sem_banco(assuntos={
        "A1": {"id": "s1", "title": "Highlander v3 — F7, o assunto como unidade",
               "description": None, "n": 4},
    })
    texto = prompts.montar(lote)
    assert "ASSUNTOS VIVOS" in texto
    assert "A1  Highlander v3 — F7, o assunto como unidade · 4 afirmação(ões)" in texto
    assert "tamanho de DOCUMENTO, não tamanho de" in texto
    assert "CAMPOS DE ASSUNTO" in texto
    # a afirmação que já vale aparece marcada com o assunto dela
    assert "E1  [decision] 13/10/2026 — (A1) rótulo" in texto


def test_chave_ligada_sem_assunto_nenhum_ainda():
    texto = prompts.montar(_lote_sem_banco(assuntos={}))
    assert "nenhum ainda" in texto


def test_parser_le_assunto_e_assunto_novo():
    bruto = ('{"claims": [{"subject": "x", "statement": "uma afirmação longa o bastante",'
             ' "kind": "fact", "source_seq": 1, "assunto": "a3"},'
             ' {"subject": "y", "statement": "outra afirmação longa o bastante",'
             ' "kind": "fact", "source_seq": 1,'
             ' "assunto_novo": {"titulo": "Coder — X", "por_que": "nada servia"}},'
             ' {"subject": "z", "statement": "mais uma afirmação longa o bastante",'
             ' "kind": "fact", "source_seq": 1, "assunto_novo": "texto solto"}],'
             ' "closures": [], "nothing_durable": false}')
    p = brain.parsear(bruto)
    assert p.claims[0].assunto == "a3"
    assert p.claims[1].assunto_novo == {"titulo": "Coder — X", "por_que": "nada servia"}
    assert p.claims[2].assunto_novo is None  # forma torta é ignorada, não quebra


# ── Banco ────────────────────────────────────────────────────────────────


@pytest.fixture
def cx(monkeypatch):
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    monkeypatch.setattr(assuntos, "_vetor_do_titulo", lambda *a, **k: None)
    conn = store.conectar(_URL)
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('lucien_subjects') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            pytest.skip("banco sem a migration 012")
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def lote(cx) -> Lote:
    cur = cx.cursor()
    cur.execute(
        "SELECT id, seq, topic_id, role, content, created_at, audio_transcribed"
        "  FROM messages WHERE role = 'user' AND seq IS NOT NULL"
        " ORDER BY seq DESC LIMIT 2")
    linhas = cur.fetchall()
    if len(linhas) < 2 or linhas[0]["topic_id"] != linhas[1]["topic_id"]:
        pytest.skip("acervo sem duas mensagens do operador no mesmo tópico")
    msgs = sorted(
        (Mensagem(seq=int(m["seq"]), id=str(m["id"]), role=m["role"],
                  created_at=m["created_at"], content=m["content"] or "",
                  audio=bool(m["audio_transcribed"])) for m in linhas),
        key=lambda m: m.seq)
    return Lote(topic_id=str(linhas[0]["topic_id"]), topico_nome="teste",
                mensagens=msgs, assuntos={})


@pytest.fixture
def rodada(cx, lote):
    return store.abrir_rodada(cx, mode="incremental", topic_id=lote.topic_id,
                              lote=lote, model="teste")


def _claim(lote, n=1, **kw) -> ClaimProposta:
    base = dict(subject=f"rótulo de teste f7 {n}",
                statement=f"Afirmação de teste da F7 número {n}, longa o bastante.",
                kind="fact", source_seq=lote.ate_seq)
    base.update(kw)
    return ClaimProposta(**base)


def _assunto_de(cx, claim_statement):
    cur = cx.cursor()
    cur.execute("SELECT c.subject_id, s.title FROM lucien_claims c"
                " LEFT JOIN lucien_subjects s ON s.id = c.subject_id"
                " WHERE c.statement = %s", (claim_statement,))
    return cur.fetchone()


def test_T11_reusa_pelo_apelido(cx, lote, rodada):
    sid = assuntos.criar(cx, "Teste F7 — assunto existente", quando=_AGORA)
    lote.assuntos = {"A1": {"id": sid, "title": "Teste F7 — assunto existente"}}
    p = _claim(lote, assunto="a1")
    r = store.aplicar(cx, lote, Proposta(claims=[p]), run_id=rodada, avancar_cursor=False)
    assert r.criadas == 1 and r.assuntos_reusados == 1 and r.assuntos_criados == 0
    assert str(_assunto_de(cx, p.statement)["subject_id"]) == sid


def test_T11_apelido_invalido_nao_custa_a_afirmacao(cx, lote, rodada):
    p = _claim(lote, assunto="A99")
    r = store.aplicar(cx, lote, Proposta(claims=[p]), run_id=rodada, avancar_cursor=False)
    assert r.criadas == 1, "assunto errado não pode derrubar a afirmação"
    assert r.assuntos_invalidos == 1 and not r.recusas
    assert _assunto_de(cx, p.statement)["subject_id"] is None


def test_T11_assunto_novo_e_criado_uma_vez_so_no_mesmo_lote(cx, lote, rodada):
    novo = {"titulo": "Teste F7 — assunto nascido agora", "por_que": "nada servia"}
    p1 = _claim(lote, 1, assunto_novo=novo)
    p2 = _claim(lote, 2, assunto_novo=dict(novo, titulo="teste f7: assunto nascido agora"))
    r = store.aplicar(cx, lote, Proposta(claims=[p1, p2]), run_id=rodada,
                      avancar_cursor=False)
    assert r.criadas == 2
    assert r.assuntos_criados == 1 and r.assuntos_evitados == 1
    a, b = _assunto_de(cx, p1.statement), _assunto_de(cx, p2.statement)
    assert a["subject_id"] == b["subject_id"]


def test_T11_titulo_parecido_reusa_por_trigrama(cx):
    sid = assuntos.criar(cx, "Teste F7 — migração do banco para Postgres direto", quando=_AGORA)
    dup = assuntos.ja_existe(cx, "Teste F7 — migracao do banco pro Postgres direto")
    assert dup == (sid, "trigrama")


def test_T11_titulos_irmaos_de_um_projeto_nao_se_fundem(cx):
    """O risco do prefixo comum: "Highlander v3 — F5 …" e "Highlander v3 — F7 …"
    são assuntos diferentes, e a trava não pode juntá-los por trigrama."""
    assuntos.criar(cx, "Projeto Zeta (teste F7) — fase um, cadastro de clientes", quando=_AGORA)
    assert assuntos.ja_existe(cx, "Projeto Zeta (teste F7) — fase dois, emissão de notas") is None


def test_T11_slug_de_assunto_fundido_vai_para_o_destino(cx):
    a = assuntos.criar(cx, "Teste F7 — origem da fusão", quando=_AGORA)
    b = assuntos.criar(cx, "Teste F7 — destino da fusão", quando=_AGORA)
    assuntos.fundir(cx, a, b, motivo="teste")
    assert assuntos.ja_existe(cx, "teste f7 origem da fusão") == (b, "slug")


def test_superacao_herda_o_assunto_da_substituida(cx, lote, rodada):
    sid = assuntos.criar(cx, "Teste F7 — assunto herdado", quando=_AGORA)
    p0 = _claim(lote, 1, assunto="A1", kind="decision")
    lote.assuntos = {"A1": {"id": sid, "title": "x"}}
    store.aplicar(cx, lote, Proposta(claims=[p0]), run_id=rodada, avancar_cursor=False)
    cur = cx.cursor()
    cur.execute("SELECT id, subject, statement, kind, valid_from, confidence,"
                " source_seq, subject_id FROM lucien_claims WHERE statement = %s",
                (p0.statement,))
    lote.estado = {"E1": cur.fetchone()}
    p1 = _claim(lote, 2, kind="decision", supersedes=["E1"],
                supersede_reason="o operador mudou de ideia")
    r = store.aplicar(cx, lote, Proposta(claims=[p1]), run_id=rodada, avancar_cursor=False)
    assert r.superadas == 1
    assert str(_assunto_de(cx, p1.statement)["subject_id"]) == sid


def test_assunto_desligado_grava_como_antes(cx, lote, rodada):
    lote.assuntos = None
    p = _claim(lote, assunto="A1", assunto_novo={"titulo": "Teste F7 — não pode nascer"})
    r = store.aplicar(cx, lote, Proposta(claims=[p]), run_id=rodada, avancar_cursor=False)
    assert r.criadas == 1 and r.assuntos_criados == 0
    assert _assunto_de(cx, p.statement)["subject_id"] is None
    cur = cx.cursor()
    cur.execute("SELECT 1 FROM lucien_subjects WHERE title = 'Teste F7 — não pode nascer'")
    assert cur.fetchone() is None


def test_toque_usa_a_data_do_fato(cx):
    velho = datetime(2026, 5, 1, tzinfo=timezone.utc)
    sid = assuntos.criar(cx, "Teste F7 — datas", quando=_AGORA)
    assuntos.tocar(cx, sid, velho)
    cur = cx.cursor()
    cur.execute("SELECT opened_at, last_touched_at FROM lucien_subjects WHERE id=%s", (sid,))
    r = cur.fetchone()
    assert r["opened_at"] == velho and r["last_touched_at"] == _AGORA


def test_fusao_e_desfazer_devolvem_exatamente(cx, lote, rodada):
    a = assuntos.criar(cx, "Teste F7 — fusão A", quando=_AGORA)
    b = assuntos.criar(cx, "Teste F7 — fusão B", quando=_AGORA)
    lote.assuntos = {"A1": {"id": a, "title": "x"}}
    p = _claim(lote, 1, assunto="A1")
    store.aplicar(cx, lote, Proposta(claims=[p]), run_id=rodada, avancar_cursor=False)
    assert assuntos.fundir(cx, a, b, motivo="teste") == 1
    assert str(_assunto_de(cx, p.statement)["subject_id"]) == b
    cur = cx.cursor()
    cur.execute("SELECT id FROM lucien_subject_events WHERE subject_id=%s AND action='fundido'", (a,))
    ev = cur.fetchone()["id"]
    assert assuntos.desfazer_fusao(cx, ev) == 1
    assert str(_assunto_de(cx, p.statement)["subject_id"]) == a
    assert assuntos.imagem(cx, a)["status"] == "ativo"


def test_desfazer_rodada_solta_e_arquiva(cx, lote, rodada):
    sid = assuntos.criar(cx, "Teste F7 — da rodada", quando=_AGORA, run_id=rodada,
                         criado_por="agrupamento")
    p = _claim(lote, 1)
    store.aplicar(cx, lote, Proposta(claims=[p]), run_id=rodada, avancar_cursor=False)
    cur = cx.cursor()
    cur.execute("SELECT id FROM lucien_claims WHERE statement=%s", (p.statement,))
    cid = str(cur.fetchone()["id"])
    cur.execute("UPDATE lucien_claims SET subject_id=%s WHERE id=%s", (sid, cid))
    assuntos.evento(cx, sid, "atribuido", None, {"afirmacoes": [cid]},
                    run_id=rodada, actor="agrupamento")
    res = assuntos.desfazer_rodada(cx, rodada)
    assert res == {"afirmacoes_soltas": 1, "assuntos_arquivados": 1}
    assert _assunto_de(cx, p.statement)["subject_id"] is None


def test_contadores_da_rodada_sao_gravados(cx, lote, rodada):
    p = _claim(lote, assunto_novo={"titulo": "Teste F7 — contadores"})
    r = store.aplicar(cx, lote, Proposta(claims=[p]), run_id=rodada, avancar_cursor=False)
    store.fechar_rodada(cx, rodada, r)
    cur = cx.cursor()
    cur.execute("SELECT subjects_created FROM lucien_runs WHERE id=%s", (rodada,))
    assert cur.fetchone()["subjects_created"] == 1


# ── Agrupamento retroativo e divisão (o modelo é uma resposta montada à mão) ──

from bot.lucien import agrupamento, dossie_assunto, revisao  # noqa: E402


def _tres_afirmacoes(cx, lote, rodada):
    ps = [_claim(lote, k) for k in (1, 2, 3)]
    store.aplicar(cx, lote, Proposta(claims=ps), run_id=rodada, avancar_cursor=False)
    cur = cx.cursor()
    cur.execute(
        "SELECT c.id, c.topic_id, 'teste' AS topico, c.kind, c.status, c.valid_from,"
        "       c.subject, c.statement FROM lucien_claims c"
        " WHERE c.statement = ANY(%s) ORDER BY c.statement", ([p.statement for p in ps],))
    return cur.fetchall()


def test_agrupamento_aplica_novo_existente_avulsa_e_conta_o_torto(cx, lote, rodada):
    linhas = _tres_afirmacoes(cx, lote, rodada)
    existente = assuntos.criar(cx, "Teste F7 — já existia", quando=_AGORA)
    voc = {"A1": {"id": existente, "title": "Teste F7 — já existia", "n": 0}}
    apelidos = {"C1": linhas[0], "C2": linhas[1], "C3": linhas[2]}
    rel = agrupamento.Relatorio(run_id=rodada)
    resposta = {"novos": [{"id": "N1", "titulo": "Teste F7 — nascido no agrupamento",
                           "descricao": "teste"}],
                "atribuicoes": {"C1": "N1", "C2": "A1", "C3": None}}
    avulsas = agrupamento.aplicar_lote(cx, resposta, voc, apelidos, run_id=rodada, rel=rel)
    assert rel.criados == 1 and rel.atribuidas == 2 and rel.avulsas == 1
    assert avulsas == [str(linhas[2]["id"])]
    assert str(_assunto_de(cx, linhas[1]["statement"])["subject_id"]) == existente
    assert _assunto_de(cx, linhas[0]["statement"])["title"] == "Teste F7 — nascido no agrupamento"


def test_agrupamento_nao_inventa(cx, lote, rodada):
    """Apelido inexistente e afirmação esquecida pelo modelo ficam SEM assunto,
    e as duas coisas são contadas — nunca atribuídas por palpite."""
    linhas = _tres_afirmacoes(cx, lote, rodada)
    rel = agrupamento.Relatorio(run_id=rodada)
    agrupamento.aplicar_lote(cx, {"atribuicoes": {"C1": "A9"}}, {},
                             {"C1": linhas[0], "C2": linhas[1]}, run_id=rodada, rel=rel)
    assert rel.apelidos_invalidos == 1 and rel.nao_respondidas == 1 and rel.atribuidas == 0
    assert _assunto_de(cx, linhas[0]["statement"])["subject_id"] is None


def test_agrupamento_prompt_tem_a_regua_e_todas_as_afirmacoes(cx, lote, rodada):
    linhas = _tres_afirmacoes(cx, lote, rodada)
    texto, apelidos = agrupamento.montar_prompt({}, linhas)
    assert "Tamanho de DOCUMENTO, não tamanho de TÓPICO" in texto
    assert "Manutenção do Kobe" in texto           # o exemplo de lixo do operador
    assert all(f"{ap} ·" in texto for ap in apelidos) and len(apelidos) == 3


def test_divisao_e_desfazer_devolvem_ao_assunto_de_origem(cx, lote, rodada):
    linhas = _tres_afirmacoes(cx, lote, rodada)
    grande = assuntos.criar(cx, "Teste F7 — assunto que virou tópico", quando=_AGORA)
    cur = cx.cursor()
    cur.execute("UPDATE lucien_claims SET subject_id = %s WHERE id = ANY(%s::uuid[])",
                (grande, [str(r["id"]) for r in linhas]))

    def pensador(prompt, system):
        assert "GRANDE DEMAIS" in prompt
        return ('{"novos": [{"id": "N1", "titulo": "Teste F7 — fatia um"},'
                ' {"id": "N2", "titulo": "Teste F7 — fatia dois, outra conversa"}],'
                ' "atribuicoes": {"C1": "N1", "C2": "N2", "C3": "N2"}}')

    rel = agrupamento.dividir(cx, grande, kobe_home="/tmp", pensador=pensador, commit=False)
    assert rel.erro is None and rel.criados == 2
    assert assuntos.imagem(cx, grande)["status"] == "arquivado"
    r = assuntos.desfazer_rodada(cx, rel.run_id)
    assert r["afirmacoes_soltas"] == 3
    assert assuntos.imagem(cx, grande)["status"] == "ativo"
    for linha in linhas:
        assert str(_assunto_de(cx, linha["statement"])["subject_id"]) == grande


# ── O dossiê ──────────────────────────────────────────────────────────────


def test_dossie_separa_o_que_vale_do_que_mudou(cx, lote, rodada):
    sid = assuntos.criar(cx, "Teste F7 — dossiê", quando=_AGORA)
    lote.assuntos = {"A1": {"id": sid, "title": "x"}}
    p0 = _claim(lote, 1, assunto="A1", kind="decision",
                statement="Decisão de teste F7 que depois foi revertida pelo operador.")
    store.aplicar(cx, lote, Proposta(claims=[p0]), run_id=rodada, avancar_cursor=False)
    cur = cx.cursor()
    cur.execute("SELECT id, subject, statement, kind, valid_from, confidence,"
                " source_seq, subject_id FROM lucien_claims WHERE statement=%s", (p0.statement,))
    lote.estado = {"E1": cur.fetchone()}
    p1 = _claim(lote, 2, kind="decision", supersedes=["E1"], supersede_reason="mudou",
                statement="Decisão de teste F7 que vale hoje, no lugar da antiga.")
    store.aplicar(cx, lote, Proposta(claims=[p1]), run_id=rodada, avancar_cursor=False)

    texto = dossie_assunto.montar(cx, [sid])
    vale, mudou = texto.split("### O que mudou")
    assert p1.statement in vale and p0.statement not in vale
    assert p0.statement in mudou and "substituída por" in mudou
    assert f"#{lote.ate_seq}" in texto and "kobe-remember --ver" in texto


def test_dossie_corta_e_diz_quanto(cx, lote, rodada):
    sid = assuntos.criar(cx, "Teste F7 — dossiê grande", quando=_AGORA)
    lote.assuntos = {"A1": {"id": sid, "title": "x"}}
    ps = [_claim(lote, k, assunto="A1") for k in range(1, 30)]
    store.aplicar(cx, lote, Proposta(claims=ps[:15]), run_id=rodada, avancar_cursor=False)
    texto = dossie_assunto.montar(cx, [sid], teto=1200)
    assert "cortada(s) para caber no turno" in texto


def test_resolver_slug_literal_e_ambiguo(cx):
    a = assuntos.criar(cx, "Projeto Ômega (teste F7) — fase um", quando=_AGORA)
    b = assuntos.criar(cx, "Projeto Ômega (teste F7) — fase dois", quando=_AGORA)
    cands, seguro = dossie_assunto.resolver(cx, "projeto omega (teste f7) — fase um")
    assert seguro and [c.id for c in cands] == [a]
    cands, seguro = dossie_assunto.resolver(cx, "Projeto Ômega (teste F7)", vetor_sql=None)
    assert seguro and {c.id for c in cands} == {a, b}      # a pergunta larga junta


# ── O bloco C da revisão ──────────────────────────────────────────────────


def test_revisao_bloco_c_numera_e_fala_humano():
    sel = revisao.Selecao(gravadas=[], gravadas_fora=0, do_operador=0, pendencias=[],
                          desde=_AGORA, ate=_AGORA, assuntos=[
        {"tipo": "divisao", "subject_id": "s1", "titulo": "Coder — tudo", "n_afirmacoes": 57},
        {"tipo": "fusao", "subject_id": "s2", "titulo": "Bug do digitando",
         "n_afirmacoes": 1, "sugestao_id": "s3", "sugestao": "Liveness — ack da borda"},
    ])
    assert not sel.vazia
    itens, texto = revisao.preparar(sel)
    assert [i["n"] for i in itens] == [1, 2]
    assert "1. “Coder — tudo” tem 57 afirmações — virou tópico. Divido?" in texto
    assert "junto em “Liveness — ack da borda”?" in texto


def test_revisao_fundir_pela_resposta_do_operador(cx):
    a = assuntos.criar(cx, "Teste F7 — sozinho", quando=_AGORA)
    b = assuntos.criar(cx, "Teste F7 — destino sugerido", quando=_AGORA)
    rev = {"id": "rev-teste-f7", "itens": [
        {"n": 1, "bloco": "assunto", "claim_id": None, "topico": "assunto",
         "titulo": "Teste F7 — sozinho", "tipo": "fusao", "subject_id": a,
         "n_afirmacoes": 1, "sugestao_id": b, "sugestao": "Teste F7 — destino sugerido"}]}
    origem = {"seq": 1, "topico": "privado"}
    feito = revisao.aplicar(cx, rev, [{"item": 1, "acao": "fundir"}],
                            frase="pode juntar o 1", origem=origem)
    assert feito[0]["acao"] == "fundir"
    assert assuntos.imagem(cx, a)["status"] == "fundido"
    assert "juntado em" in revisao.foto(cx, rev)
    revisao.aplicar(cx, rev, [{"item": 1, "acao": "desfazer"}], frase="desfaz", origem=origem)
    assert assuntos.imagem(cx, a)["status"] == "ativo"


def test_revisao_recusa_acao_de_assunto_em_afirmacao(cx):
    rev = {"id": "rev-teste-f7b", "itens": [
        {"n": 1, "bloco": "gravada", "claim_id": "00000000-0000-0000-0000-000000000000",
         "topico": "x", "titulo": "x", "kind": "fact"}]}
    with pytest.raises(revisao.RevisaoRecusou, match="não um assunto"):
        revisao.aplicar(cx, rev, [{"item": 1, "acao": "fundir"}], frase="x",
                        origem={"seq": 1, "topico": "x"})
