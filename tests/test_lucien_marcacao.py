#!/usr/bin/env python3
"""A mensagem inteira em cada assunto — a marcação do curador (Highlander v3, F7).

O QUE SE TENTA AQUI
-------------------
Os testes sem banco guardam o corte mecânico (posições exatas sobre o texto
ORIGINAL, nunca no meio de frase), a localização das palavras literais, a chave
desligada (prompt idêntico ao da F7.1) e a interpretação do que o modelo
devolve — apelido inventado, principal ausente, parte inexistente.

Os de banco tentam fazer a marcação **mentir**: remarcar uma mensagem já
marcada, herdar assunto por um lugar que não existe, perder a afirmação por
causa da marcação — e a asserção é que não aconteceu. E o caso real da rodada 2:
um áudio com dois assuntos, ida e volta (o `#3325`), aparece INTEIRO nos dois.

Nenhum teste chama modelo nem embedder.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=<banco com a 013> .venv/bin/python -m pytest -q \\
        tests/test_lucien_marcacao.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import assuntos, brain, marcacao, prompts, store  # noqa: E402
from bot.lucien.models import ClaimProposta, Lote, Mensagem, Proposta  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")
_AGORA = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)

# O `#3325` real (20/08/2026), encurtado: começa e termina no bug do emoji, e no
# meio pergunta sobre a mensagem do ack.
AUDIO = ("HAL pode disparar a sessão Coder para corrigir esse bug do emoji. "
         "O emoji de transcrição pronta foi recusado pelo Telegram por causa de um "
         "caractere invisível, e isso precisa ser consertado no código da borda, com "
         "teste, porque eu fiquei sem saber se a mensagem tinha chegado. "
         "Quero o conserto ainda hoje, se der. "
         "\n\n"
         "Inclusive uma coisa que eu queria te perguntar. Na verdade, quando eu falei "
         "troca pro emoji, apareceu o próprio emoji, né? Eu só quero que você confirme "
         "pra mim que essa mensagem de fato veio do modelo mini, de fato é aquele nosso "
         "ACK. Só uma curiosidade é que ela não veio como uma resposta, veio solta. "
         "\n\n"
         "Nem sei se eu vou querer alterar isso. Mas enfim, pode disparar a sessão "
         "Coder pra corrigir esse bugzinho do emoji, e me avisa quando estiver no ar, "
         "com o teste rodado no ambiente de desenvolvimento antes, como sempre, tá? "
         "Obrigado.")


# ── O corte mecânico ─────────────────────────────────────────────────────


def test_unidades_sao_fatias_exatas_do_original_e_cobrem_o_texto():
    us = marcacao.unidades(AUDIO, alvo=120)
    assert len(us) >= 3
    # cada unidade começa e termina em fronteira de frase, e é fatia literal
    for a, b in us:
        trecho = AUDIO[a:b]
        assert trecho == trecho.strip()
        assert trecho[-1] in ".!?…" or b == len(AUDIO)
    # nada do texto (fora espaços entre unidades) fica de fora
    resto = AUDIO
    for a, b in reversed(us):
        resto = resto[:a] + resto[b:]
    assert resto.strip() == ""


def test_frase_maior_que_o_alvo_nao_e_cortada():
    longa = "palavra " * 100 + "fim."
    us = marcacao.unidades(longa, alvo=50)
    assert us == [(0, len(longa))]


def test_texto_vazio_nao_tem_unidade():
    assert marcacao.unidades("") == []


def test_bloco_da_mensagem_numera_e_preserva_o_texto():
    linhas = marcacao.bloco_da_mensagem(3325, AUDIO)
    assert linhas[0].startswith("[3325.1] HAL pode disparar")
    assert all(l.startswith(f"[3325.{n}] ") for n, l in enumerate(linhas, start=1))


# ── As palavras literais ─────────────────────────────────────────────────


def test_localizar_ignora_acento_caixa_e_pontuacao_mas_devolve_posicao_original():
    lugar = marcacao.localizar(AUDIO, "confirme pra mim que essa mensagem de fato veio do MODELO MINI")
    assert lugar is not None
    a, b = lugar
    assert AUDIO[a:b].lower().startswith("confirme pra mim")
    assert AUDIO[a:b].lower().endswith("modelo mini")
    assert marcacao.localizar("Já está decidido: não usar rsync.", "ja esta decidido nao usar rsync") \
        == (0, len("Já está decidido: não usar rsync"))


def test_localizar_recusa_citacao_curta_ou_ausente():
    assert marcacao.localizar(AUDIO, "o bug") is None           # curta demais para ser lugar
    assert marcacao.localizar(AUDIO, "isto não foi dito em lugar nenhum") is None
    assert marcacao.localizar(AUDIO, "") is None


# ── A chave ──────────────────────────────────────────────────────────────


def test_chave_desligada_por_omissao(monkeypatch):
    monkeypatch.delenv("LUCIEN_MARCACAO_ENABLED", raising=False)
    assert marcacao.habilitado() is False


def _lote(marcar: bool, conteudo: str = AUDIO, seq: int = 3325, **kw) -> Lote:
    m = Mensagem(seq=seq, id="00000000-0000-0000-0000-000000003325", role="user",
                 created_at=_AGORA, content=conteudo, audio=True)
    lote = Lote(topic_id="t", topico_nome="Dev Kobe", mensagens=[m],
                assuntos={"A1": {"id": "s-emoji", "title": "Bug do emoji ✍"},
                          "A2": {"id": "s-ack", "title": "Borda — ack do modelo mini"}})
    lote.marcar = marcar
    for k, v in kw.items():
        setattr(lote, k, v)
    return lote


def test_chave_desligada_prompt_identico_ao_da_f71():
    """Desligada, nem uma letra da marcação entra no prompt."""
    texto = prompts.montar(_lote(False))
    for marca in ("MARCAÇÃO DAS MENSAGENS", "[3325.1]", "CAMPOS DE MARCAÇÃO",
                  "CONVERSAS ABERTAS", '"palavras"'):
        assert marca not in texto
    assert AUDIO.strip() in texto


def test_chave_ligada_prompt_traz_partes_instrucao_e_contrato():
    lote = _lote(True, abertas=[{"apelido": "A1", "title": "Bug do emoji ✍", "seq": 3320,
                                 "quando": "20/08 14:50", "trecho": "o emoji não saiu"}])
    texto = prompts.montar(lote)
    assert "[3325.1] HAL pode disparar" in texto
    assert "MARCAÇÃO DAS MENSAGENS" in texto and "CAMPOS DE MARCAÇÃO" in texto
    assert "CONVERSAS ABERTAS" in texto and "última mensagem #3320" in texto


def test_foto_da_revisao_nao_e_cortada_em_partes():
    m = Mensagem(seq=7, id="x", role="assistant", created_at=_AGORA,
                 content="[resposta do agente à revisão…]", audio=False, marcavel=False)
    lote = _lote(True)
    lote.mensagens.append(m)
    assert "[7.1]" not in prompts.montar(lote)


def test_parser_le_palavras_mensagens_e_assuntos_novos():
    bruto = ('{"claims": [{"subject": "x", "statement": "uma afirmação longa o bastante",'
             ' "kind": "fact", "source_seq": 3325, "assunto": "N1", "palavras": "veio do modelo mini"}],'
             ' "closures": [], "nothing_durable": false,'
             ' "assuntos_novos": [{"id": "N1", "titulo": "Borda — ack", "dentro_de": null}],'
             ' "mensagens": {"3325": {"principal": "A1", "partes": {"2": ["A2"]}}}}')
    p = brain.parsear(bruto)
    assert p.claims[0].palavras == "veio do modelo mini"
    assert p.marcacao == {"3325": {"principal": "A1", "partes": {"2": ["A2"]}}}
    assert p.assuntos_novos[0]["id"] == "N1"


# ── A interpretação (sem banco) ──────────────────────────────────────────


MAPA = {"A1": "s-emoji", "A2": "s-ack"}


def _interpretar(item, conteudo=AUDIO):
    lote = _lote(True, conteudo)
    res = marcacao.ResultadoMarcacao()
    marcacao.interpretar({"3325": item}, lote, MAPA, res)
    return res, marcacao.unidades(conteudo)


def test_ida_e_volta_dois_assuntos_na_mesma_mensagem():
    us = marcacao.unidades(AUDIO)
    n = len(us)
    assert n >= 2
    partes = {str(k): ["A1"] for k in range(1, n + 1)}
    partes["2"] = ["A2"]
    res, us = _interpretar({"principal": "A1", "partes": partes})
    marca = res.marcas[3325]
    assert marca.principal == "s-emoji"
    assert set(marca.partes) == {"s-emoji", "s-ack"}
    assert marca.partes["s-ack"] == [list(us[1])]


def test_sem_partes_a_mensagem_inteira_e_do_principal():
    res, us = _interpretar({"principal": "A2"})
    assert res.marcas[3325].partes == {"s-ack": [[us[0][0], us[-1][1]]]}


def test_apelido_inventado_e_ignorado_e_contado():
    res, _ = _interpretar({"principal": "A9"})
    assert 3325 not in res.marcas and res.invalidos == 1


def test_principal_ausente_vira_o_de_mais_texto_e_conta():
    res, us = _interpretar({"partes": {"1": ["A2"]}})
    # só uma unidade rotulada: o principal suprido é ela, e o resto herda
    assert res.marcas[3325].principal == "s-ack"
    assert res.principal_suprido == 1


def test_parte_inexistente_e_contada_e_nao_derruba():
    res, _ = _interpretar({"principal": "A1", "partes": {"99": ["A2"]}})
    assert res.marcas[3325].principal == "s-emoji" and res.invalidos == 1


def test_aparte_fica_sem_assunto():
    res, _ = _interpretar({"principal": None}, conteudo="ok, obrigado")
    assert res.marcas == {}


def test_mensagem_de_fora_do_lote_e_ignorada():
    lote = _lote(True)
    res = marcacao.ResultadoMarcacao()
    marcacao.interpretar({"9999": {"principal": "A1"}}, lote, MAPA, res)
    assert res.marcas == {} and res.invalidos == 1


def test_assunto_por_posicao():
    partes = {"emoji": [[0, 50], [200, 260]], "ack": [[50, 200]]}
    assert marcacao.assunto_por_posicao("emoji", partes, (60, 90)) == "ack"
    assert marcacao.assunto_por_posicao("emoji", partes, (10, 20)) == "emoji"
    # a escolha do curador vale quando ela cobre o lugar
    partes["ack"].append([0, 50])
    assert marcacao.assunto_por_posicao("emoji", partes, (10, 20), preferido="ack") == "ack"
    assert marcacao.assunto_por_posicao("emoji", partes, (900, 950)) is None


# ── Banco ────────────────────────────────────────────────────────────────


@pytest.fixture
def cx(monkeypatch):
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    monkeypatch.setattr(assuntos, "_vetor_do_titulo", lambda *a, **k: None)
    monkeypatch.setenv("LUCIEN_MARCACAO_ENABLED", "true")
    conn = store.conectar(_URL)
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('message_subjects') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            pytest.skip("banco sem a migration 013")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _mensagens(cx, *textos) -> list[Mensagem]:
    cur = cx.cursor()
    # um tópico próprio do teste: funciona em banco vazio e não encosta nos reais
    cur.execute("INSERT INTO topics (current_name) VALUES ('teste f7 marcação') RETURNING id")
    tid = cur.fetchone()["id"]
    cur.execute("INSERT INTO sessions (topic_id, status) VALUES (%s, 'active') RETURNING id", (tid,))
    sid = cur.fetchone()["id"]
    out = []
    for t in textos:
        cur.execute("INSERT INTO messages (session_id, topic_id, role, content, created_at)"
                    " VALUES (%s,%s,'user',%s,%s) RETURNING id, seq, topic_id",
                    (sid, tid, t, _AGORA))
        r = cur.fetchone()
        out.append(Mensagem(seq=int(r["seq"]), id=str(r["id"]), role="user",
                            created_at=_AGORA, content=t, audio=True))
    return out


@pytest.fixture
def mundo(cx):
    msgs = _mensagens(cx, AUDIO, "ok, obrigado")
    k = {"kind": "conversa"} if assuntos.tem_natureza(cx) else {}
    emoji = assuntos.criar(cx, "Teste F7 — bug do emoji", quando=_AGORA, **k)
    ack = assuntos.criar(cx, "Teste F7 — ack do modelo mini", quando=_AGORA, **k)
    cur = cx.cursor()
    cur.execute("SELECT topic_id FROM messages WHERE id = %s", (msgs[0].id,))
    lote = Lote(topic_id=str(cur.fetchone()["topic_id"]), topico_nome="teste", mensagens=msgs,
                assuntos={"A1": {"id": emoji, "title": "bug do emoji"},
                          "A2": {"id": ack, "title": "ack do modelo mini"}})
    lote.marcar = True
    run = store.abrir_rodada(cx, mode="incremental", topic_id=lote.topic_id, lote=lote, model="teste")
    return {"lote": lote, "emoji": emoji, "ack": ack, "run": run, "msgs": msgs}


def _ida_e_volta(seq):
    """Começo e fim no emoji; a parte do meio (a pergunta sobre o ack) no outro."""
    us = marcacao.unidades(AUDIO)
    assert len(us) == 3, us
    return {str(seq): {"principal": "A1", "partes": {"1": ["A1"], "2": ["A2"], "3": ["A1"]}}}


def _claim(seq, **kw):
    base = dict(subject="rótulo f7 marcação", kind="fact", source_seq=seq,
                statement="O operador perguntou se a mensagem de aviso veio do modelo mini.")
    base.update(kw)
    return ClaimProposta(**base)


def _linhas(cx, mid):
    cur = cx.cursor()
    cur.execute("SELECT subject_id, role, parts FROM message_subjects WHERE message_id = %s"
                " ORDER BY role", (mid,))
    return cur.fetchall()


def test_a_mensagem_aparece_inteira_nos_dois_assuntos(cx, mundo):
    lote, m = mundo["lote"], mundo["msgs"][0]
    r = store.aplicar(cx, lote, Proposta(marcacao={**_ida_e_volta(m.seq),
                                                   str(mundo["msgs"][1].seq): {"principal": None}}),
                      run_id=mundo["run"], avancar_cursor=False)
    linhas = _linhas(cx, m.id)
    assert {(str(x["subject_id"]), x["role"]) for x in linhas} == {
        (mundo["emoji"], "principal"), (mundo["ack"], "secundario")}
    # o texto não foi tocado
    cur = cx.cursor()
    cur.execute("SELECT content FROM messages WHERE id = %s", (m.id,))
    assert cur.fetchone()["content"] == AUDIO
    # o aparte não ganhou assunto
    assert _linhas(cx, mundo["msgs"][1].id) == []
    assert r.mensagens_marcadas == 1


def test_releitura_nao_remarca(cx, mundo):
    lote, m = mundo["lote"], mundo["msgs"][0]
    store.aplicar(cx, lote, Proposta(marcacao=_ida_e_volta(m.seq)), run_id=mundo["run"],
                  avancar_cursor=False)
    r = store.aplicar(cx, lote, Proposta(marcacao={str(m.seq): {"principal": "A2"}}),
                      run_id=mundo["run"], avancar_cursor=False)
    assert r.mensagens_marcadas == 0
    principal = [x for x in _linhas(cx, m.id) if x["role"] == "principal"]
    assert str(principal[0]["subject_id"]) == mundo["emoji"]


def test_afirmacao_herda_o_assunto_da_parte_onde_as_palavras_caem(cx, mundo):
    lote, m = mundo["lote"], mundo["msgs"][0]
    claim = _claim(m.seq, assunto="A1",
                   palavras="confirme pra mim que essa mensagem de fato veio do modelo mini")
    r = store.aplicar(cx, lote, Proposta(claims=[claim], marcacao=_ida_e_volta(m.seq)),
                      run_id=mundo["run"], avancar_cursor=False)
    assert r.criadas == 1 and r.palavras_achadas == 1
    cur = cx.cursor()
    cur.execute("SELECT subject_id, support_from, support_to, support_quote FROM lucien_claims"
                " WHERE source_message_id = %s", (m.id,))
    c = cur.fetchone()
    # o curador escolheu A1 (emoji), mas as palavras estão na parte do ack: vale a posição
    assert str(c["subject_id"]) == mundo["ack"]
    assert AUDIO[c["support_from"]:c["support_to"]].lower().startswith("confirme pra mim")


def test_palavras_nao_achadas_herdam_o_principal_e_contam(cx, mundo):
    lote, m = mundo["lote"], mundo["msgs"][0]
    claim = _claim(m.seq, assunto="A9", palavras="frase que ninguém disse nesta mensagem")
    r = store.aplicar(cx, lote, Proposta(claims=[claim], marcacao=_ida_e_volta(m.seq)),
                      run_id=mundo["run"], avancar_cursor=False)
    assert r.criadas == 1, "a marcação nunca custa a afirmação"
    assert r.palavras_faltando == 1 and r.herancas_pelo_principal == 1
    cur = cx.cursor()
    cur.execute("SELECT subject_id, support_from FROM lucien_claims WHERE source_message_id = %s",
                (m.id,))
    c = cur.fetchone()
    assert str(c["subject_id"]) == mundo["emoji"] and c["support_from"] is None


def test_assunto_novo_da_marcacao_serve_a_afirmacao_e_vira_subassunto(cx, mundo):
    lote, m = mundo["lote"], mundo["msgs"][0]
    # Com a 014, a conversa nova fica dentro de um TEMA (a pasta); sem ela, de
    # qualquer assunto de topo.
    pai = mundo["ack"]
    if assuntos.tem_natureza(cx):
        pai = assuntos.criar(cx, "Teste F7 — tema dos modelos mini", quando=_AGORA)
        lote.temas = {"T1": {"id": pai, "title": "tema dos modelos mini"}}
    novos = [{"id": "N1", "titulo": "Teste F7 — aviso de mensagem solta",
              "descricao": "o ack que não veio como resposta",
              "dentro_de": "T1" if lote.temas else "A2"}]
    marcas = {str(m.seq): {"principal": "N1"}}
    claim = _claim(m.seq, assunto="N1", palavras="ela não veio como uma resposta")
    r = store.aplicar(cx, lote, Proposta(claims=[claim], marcacao=marcas, assuntos_novos=novos),
                      run_id=mundo["run"], avancar_cursor=False)
    assert r.assuntos_criados >= 1
    cur = cx.cursor()
    cur.execute("SELECT s.id, s.parent_id FROM lucien_subjects s WHERE s.title = %s",
                ("Teste F7 — aviso de mensagem solta",))
    s = cur.fetchone()
    assert str(s["parent_id"]) == str(pai)
    cur.execute("SELECT subject_id FROM lucien_claims WHERE source_message_id = %s", (m.id,))
    assert str(cur.fetchone()["subject_id"]) == str(s["id"])


def test_contadores_vao_para_a_rodada(cx, mundo):
    lote, m = mundo["lote"], mundo["msgs"][0]
    claim = _claim(m.seq, palavras="confirme pra mim que essa mensagem de fato veio do modelo mini")
    r = store.aplicar(cx, lote, Proposta(claims=[claim], marcacao=_ida_e_volta(m.seq)),
                      run_id=mundo["run"], avancar_cursor=False)
    store.fechar_rodada(cx, mundo["run"], r)
    cur = cx.cursor()
    cur.execute("SELECT messages_marked, quotes_found, quotes_missing FROM lucien_runs WHERE id = %s",
                (mundo["run"],))
    assert dict(cur.fetchone()) == {"messages_marked": 1, "quotes_found": 1, "quotes_missing": 0}


def test_fusao_e_seguida_na_leitura_sem_mexer_na_marcacao(cx, mundo):
    lote, m = mundo["lote"], mundo["msgs"][0]
    store.aplicar(cx, lote, Proposta(marcacao=_ida_e_volta(m.seq)), run_id=mundo["run"],
                  avancar_cursor=False)
    antes = _linhas(cx, m.id)
    assuntos.fundir(cx, mundo["ack"], mundo["emoji"], motivo="teste")
    assert _linhas(cx, m.id) == antes, "fundir é apontar: a marcação não é reescrita"
    principal, partes = marcacao.assuntos_da_mensagem(cx, m.id)
    assert principal == mundo["emoji"] and set(partes) == {mundo["emoji"]}
    a, b = partes[mundo["emoji"]][0]
    assert (a, b) == (0, len(AUDIO)), "as partes dos dois somam a mensagem inteira"


def test_chave_desligada_nao_marca(cx, mundo, monkeypatch):
    monkeypatch.setenv("LUCIEN_MARCACAO_ENABLED", "false")
    lote = store.montar_lote(cx, mundo["lote"].topic_id)
    assert lote.marcar is False


# ── A conversa do assunto (a apresentação) ───────────────────────────────

from bot.lucien import conversa_assunto, dossie_assunto  # noqa: E402


def _msg_ida_e_volta():
    us = marcacao.unidades(AUDIO)
    return conversa_assunto.MsgDoAssunto(
        seq=3325, role="user", audio=True, created_at=_AGORA, content=AUDIO,
        principal="emoji",
        partes={"emoji": [list(us[0]), list(us[2])], "ack": [list(us[1])]})


def test_renderizar_mostra_a_mensagem_inteira_e_sinaliza_o_outro_assunto():
    nomes = {"emoji": "Bug do emoji", "ack": "Ack do modelo mini"}
    linhas = conversa_assunto.renderizar(_msg_ida_e_volta(), "emoji", nomes, recolher=False)
    texto = "\n".join(linhas)
    assert linhas[0].startswith("**#3325") and 'também em: "Ack do modelo mini"' in linhas[0]
    assert "↪ _a parte abaixo é do assunto \"Ack do modelo mini\"" in texto
    assert "┆ Inclusive uma coisa que eu queria te perguntar" in texto
    # nenhuma palavra da fala se perdeu
    corpo = " ".join(l.lstrip("┆ ").strip() for l in linhas[1:] if not l.startswith("↪"))
    assert " ".join(AUDIO.split()) == " ".join(corpo.split())


def test_sob_o_outro_assunto_a_sinalizacao_se_inverte():
    nomes = {"emoji": "Bug do emoji", "ack": "Ack do modelo mini"}
    texto = "\n".join(conversa_assunto.renderizar(_msg_ida_e_volta(), "ack", nomes, recolher=False))
    assert "↪ _a parte abaixo é do assunto \"Bug do emoji\"" in texto
    assert "\nInclusive uma coisa que eu queria te perguntar" in texto   # a parte dele, sem marca


def test_recolher_tira_so_a_parte_do_outro_assunto():
    nomes = {"emoji": "Bug do emoji", "ack": "Ack do modelo mini"}
    texto = "\n".join(conversa_assunto.renderizar(_msg_ida_e_volta(), "emoji", nomes, recolher=True))
    assert "parte sobre \"Ack do modelo mini\" recolhida (" in texto
    assert "modelo mini, de fato é aquele nosso" not in texto
    assert texto.count("HAL pode disparar a sessão Coder") == 1   # a parte dele, inteira


def _marcar_ida_e_volta(cx, mundo):
    m = mundo["msgs"][0]
    store.aplicar(cx, mundo["lote"], Proposta(marcacao=_ida_e_volta(m.seq)),
                  run_id=mundo["run"], avancar_cursor=False)
    return m


def test_conversa_do_assunto_no_banco_e_inteira_nos_dois(cx, mundo):
    _marcar_ida_e_volta(cx, mundo)
    a = conversa_assunto.montar(cx, mundo["emoji"])
    b = conversa_assunto.montar(cx, mundo["ack"])
    for t in (a, b):
        assert "HAL pode disparar" in t and "modelo mini, de fato" in t and "Obrigado." in t
    assert "↪ _a parte abaixo é do assunto \"Teste F7 — ack do modelo mini\"" in a
    assert "↪ _a parte abaixo é do assunto \"Teste F7 — bug do emoji\"" in b


def test_conversa_acima_do_teto_recolhe_o_outro_assunto_primeiro(cx, mundo):
    _marcar_ida_e_volta(cx, mundo)
    t = conversa_assunto.montar(cx, mundo["emoji"], teto=700)
    assert "recolhida (" in t and "--inteira" in t
    assert "HAL pode disparar a sessão Coder" in t, "a parte do próprio assunto nunca sai"
    inteira = conversa_assunto.montar(cx, mundo["emoji"], teto=700, inteira=True)
    assert "modelo mini, de fato" in inteira


def test_dossie_traz_a_conversa(cx, mundo):
    _marcar_ida_e_volta(cx, mundo)
    t = dossie_assunto.montar(cx, [mundo["emoji"]])
    assert "### A conversa" in t and "HAL pode disparar" in t


# ── A marcação retroativa ────────────────────────────────────────────────

from datetime import timedelta  # noqa: E402

from bot.lucien import marcacao_retro  # noqa: E402


def _m(seq, t, conteudo="uma fala qualquer.", role="user"):
    return Mensagem(seq=seq, id=f"id{seq}", role=role, created_at=t, content=conteudo, audio=False)


def test_lotes_juntam_trechos_inteiros_e_partem_so_o_gigante(monkeypatch):
    monkeypatch.setattr(marcacao_retro, "TETO_CHARS_LOTE", 100)
    t = _AGORA
    trs = [("t", "x", [_m(1, t, "a" * 40)]), ("t", "x", [_m(2, t, "b" * 40)]),
           ("t", "x", [_m(3, t, "c" * 60), _m(4, t, "d" * 60), _m(5, t, "e" * 60)])]
    ls = marcacao_retro.lotes(trs)
    assert [[m.seq for _, _, ms in l for m in ms] for l in ls] == [[1, 2], [3], [4], [5]]


def test_prompt_retro_mostra_vocabulario_partes_e_contrato_sem_palavras():
    voc = {"A1": {"title": "Bug do emoji", "description": None}}
    p = marcacao_retro.montar_prompt(voc, [("t", "Dev Kobe", [_m(3325, _AGORA, AUDIO)])])
    assert "A1  Bug do emoji" in p and "[3325.1]" in p and '"mensagens"' in p
    assert '"palavras"' not in p


def test_resposta_do_agente_longa_mostra_so_o_comeco_e_avisa():
    longa = " ".join(f"Frase número {i} da resposta do agente." for i in range(400))
    p = marcacao_retro.montar_prompt({}, [("t", "x", [_m(9, _AGORA, longa, role="assistant")])])
    assert "resto da resposta não mostrado: herda o assunto principal" in p
    assert len(p) < len(longa)


class _SemCommit:
    """A conexão do teste, com `commit` desarmado: `marcacao_retro.rodar` commita
    lote a lote (é o que a torna retomável), e o teste NÃO pode gravar de verdade
    no banco de integração — tudo tem que sair no rollback do teardown."""

    def __init__(self, cx):
        self._cx = cx

    def commit(self):
        pass

    def __getattr__(self, nome):
        return getattr(self._cx, nome)


def test_retro_marca_cria_retoma_e_desfaz(cx, tmp_path):
    cx = _SemCommit(cx)
    import uuid

    sufixo = uuid.uuid4().hex[:6]     # títulos únicos: o teste nunca colide com o acervo
    msgs = _mensagens(cx, AUDIO, "ok", "Outra fala, já de outro dia, sobre o bug do emoji de novo.")
    cur = cx.cursor()
    cur.execute("UPDATE messages SET created_at = %s WHERE id = %s",
                (_AGORA + timedelta(days=2), msgs[2].id))
    k = {"kind": "conversa"} if assuntos.tem_natureza(cx) else {}
    existente = assuntos.criar(cx, f"Teste F7 retro {sufixo} — bug do emoji", quando=_AGORA, **k)
    cur.execute("SELECT topic_id FROM messages WHERE id = %s", (msgs[0].id,))
    tid = str(cur.fetchone()["topic_id"])
    chamadas = []

    def pensador(prompt, system):
        chamadas.append(prompt)
        voc = {l.split("  ", 1)[1]: l.split("  ", 1)[0] for l in prompt.splitlines()
               if l[:1] in "AC" and "  " in l}
        ap = voc[f"Teste F7 retro {sufixo} — bug do emoji"]
        resp = {"assuntos_novos": [{"id": "N1", "titulo": f"Teste F7 retro {sufixo} — ack do modelo mini"}],
                "mensagens": {}}
        for m in msgs:
            if f"#{m.seq} " not in prompt:
                continue
            if m.content == "ok":
                resp["mensagens"][str(m.seq)] = {"principal": None}
            elif m.content == AUDIO:
                resp["mensagens"][str(m.seq)] = {"principal": ap,
                                                 "partes": {"1": [ap], "2": ["N1"], "3": [ap]}}
            else:
                resp["mensagens"][str(m.seq)] = {"principal": ap}
        return json.dumps(resp)

    import json
    rel = marcacao_retro.rodar(cx, kobe_home=str(tmp_path), topic_id=tid, pensador=pensador,
                               log=lambda *_: None)
    assert rel.erro is None, rel.erro
    assert rel.marcadas == 2 and rel.apartes == 1 and rel.criados == 1
    assert len(chamadas) == 1, "os dois trechos (dias diferentes) cabem num lote só"
    principal, partes = marcacao.assuntos_da_mensagem(cx, msgs[0].id)
    assert principal == existente and len(partes) == 2

    # retomar: nada é pago de novo (marcadas são puladas, o aparte está no estado)
    rel2 = marcacao_retro.rodar(cx, kobe_home=str(tmp_path), retomar=rel.run_id, topic_id=tid,
                                pensador=pensador, log=lambda *_: None)
    assert rel2.chamadas == 0 and len(chamadas) == 1

    r = marcacao_retro.desfazer(cx, rel.run_id)
    assert r["marcacoes_desfeitas"] == 3 and r["assuntos_arquivados"] == 1
    assert marcacao.assuntos_da_mensagem(cx, msgs[0].id) == (None, {})


# ── A busca diz de que assunto é o trecho achado ─────────────────────────


class _DB:
    def __init__(self, cx):
        self.cx = cx

    def query(self, sql, params=()):
        cur = self.cx.cursor()
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def test_busca_aponta_quando_o_trecho_e_de_outro_assunto(cx, mundo):
    from bot.search import query

    m = _marcar_ida_e_volta(cx, mundo)
    meio = marcacao.unidades(AUDIO)[1]
    no_meio = query.Achado(seq=m.seq, message_id=m.id, role="user", topico="t",
                           created_at="", corpo=AUDIO[meio[0]:meio[1]])
    no_comeco = query.Achado(seq=m.seq, message_id=m.id, role="user", topico="t",
                             created_at="", corpo=AUDIO[:120])
    query._anotar_assuntos(_DB(cx), [no_meio, no_comeco])
    assert no_meio.assunto_principal == "Teste F7 — bug do emoji"
    assert no_meio.assunto_do_trecho == "Teste F7 — ack do modelo mini"
    assert no_comeco.assunto_do_trecho == "Teste F7 — bug do emoji"


def test_busca_sem_marcacao_nao_anota(cx, mundo):
    from bot.search import query

    m = mundo["msgs"][0]
    a = query.Achado(seq=m.seq, message_id=m.id, role="user", topico="t", created_at="",
                     corpo=AUDIO[:80])
    query._anotar_assuntos(_DB(cx), [a])
    assert a.assunto_principal is None and a.assunto_do_trecho is None


# ── 014: assunto (tema) × subassunto (conversa) ───────────────────────────


def test_tema_nao_vale_como_rotulo_de_mensagem(cx, mundo):
    if not assuntos.tem_natureza(cx):
        pytest.skip("banco sem a migration 014")
    lote, m = mundo["lote"], mundo["msgs"][0]
    tema = assuntos.criar(cx, "Teste F7 — tema largo do emoji", quando=_AGORA)
    lote.temas = {"T1": {"id": tema, "title": "tema largo do emoji"}}
    r = store.aplicar(cx, lote, Proposta(marcacao={str(m.seq): {"principal": "T1"}}),
                      run_id=mundo["run"], avancar_cursor=False)
    assert _linhas(cx, m.id) == [] and r.mensagens_marcadas == 0
    assert r.assuntos_invalidos >= 1


def test_conversa_nova_com_titulo_de_tema_nasce_dentro_dele(cx, mundo):
    if not assuntos.tem_natureza(cx):
        pytest.skip("banco sem a migration 014")
    lote, m = mundo["lote"], mundo["msgs"][0]
    tema = assuntos.criar(cx, "Teste F7 — título que já é tema", quando=_AGORA)
    novos = [{"id": "N1", "titulo": "Teste F7 — título que já é tema"}]
    store.aplicar(cx, lote, Proposta(marcacao={str(m.seq): {"principal": "N1"}},
                                     assuntos_novos=novos),
                  run_id=mundo["run"], avancar_cursor=False)
    principal, _ = marcacao.assuntos_da_mensagem(cx, m.id)
    assert principal and principal != str(tema)
    cur = cx.cursor()
    cur.execute("SELECT kind, parent_id FROM lucien_subjects WHERE id = %s", (principal,))
    s = cur.fetchone()
    assert s["kind"] == "conversa" and str(s["parent_id"]) == str(tema)


def test_banco_tema_dentro_de_tema_e_recusado(cx):
    import psycopg

    if not assuntos.tem_natureza(cx):
        pytest.skip("banco sem a migration 014")
    t1 = assuntos.criar(cx, "Teste F7 — tema A", quando=_AGORA)
    t2 = assuntos.criar(cx, "Teste F7 — tema B", quando=_AGORA)
    with pytest.raises(psycopg.errors.CheckViolation):
        cx.cursor().execute("UPDATE lucien_subjects SET parent_id = %s WHERE id = %s", (t1, t2))


def test_retoma_o_assunto_junta_o_que_a_busca_acha_fora_da_etiqueta(cx, mundo):
    """A etiqueta não precisa ser perfeita: o dossiê acrescenta o que a busca
    acha fora das mensagens marcadas com o assunto — e nada do que já está nele."""
    from bot.lucien import dossie_assunto
    from bot.search import query as q

    lote, m = mundo["lote"], mundo["msgs"]
    store.aplicar(cx, lote, Proposta(marcacao={str(m[0].seq): {"principal": "A1"}}),
                  run_id=mundo["run"], avancar_cursor=False)

    def buscar(db, frase, limite=12):
        assert db.scalar("SELECT 1 AS um") == 1          # a ponte serve à busca
        return q.Resultado(veredito="ACHOU", achados=[
            q.Achado(seq=m[0].seq, message_id=m[0].id, role="user", topico="t",
                     created_at="2026-10-31", corpo="já no assunto"),
            q.Achado(seq=m[1].seq, message_id=m[1].id, role="user", topico="t",
                     created_at="2026-10-31", corpo="fora da etiqueta")])

    linhas = dossie_assunto.complemento_da_busca(cx, "bug do emoji", [mundo["emoji"]],
                                                 buscar=buscar)
    texto = "\n".join(linhas)
    assert f"#{m[1].seq}**" in texto and "sem etiqueta" in texto
    assert f"#{m[0].seq}**" not in texto

    def quebrada(db, frase, limite=12):
        return q.Resultado(veredito="FALHA", erro="banco fora")

    falha = "\n".join(dossie_assunto.complemento_da_busca(cx, "x", [mundo["emoji"]], buscar=quebrada))
    assert "NÃO" in falha or "kobe-remember" in falha


def test_tema_traz_as_conversas_de_dentro(cx, mundo):
    if not assuntos.tem_natureza(cx):
        pytest.skip("banco sem a migration 014")
    from bot.lucien import conversa_assunto, dossie_assunto

    lote, m = mundo["lote"], mundo["msgs"][0]
    tema = assuntos.criar(cx, "Teste F7 — tema que contém o emoji", quando=_AGORA)
    cx.cursor().execute("UPDATE lucien_subjects SET parent_id = %s WHERE id = %s",
                        (tema, mundo["emoji"]))
    store.aplicar(cx, lote, Proposta(marcacao=_ida_e_volta(m.seq)), run_id=mundo["run"],
                  avancar_cursor=False)
    assert [x.seq for x in conversa_assunto.mensagens(cx, tema)] == [m.seq]
    texto = dossie_assunto.montar(cx, [tema])
    assert "Conversas dentro deste assunto (1)" in texto and "bug do emoji" in texto


def test_dossie_soma_a_origem_das_afirmacoes_fora_da_conversa(cx, mundo):
    """Régua das 30 (02/11): a conversa marcada soma à lista da F7.1, não a
    substitui — a afirmação nascida numa mensagem sem a etiqueta continua achável."""
    lote, m = mundo["lote"], mundo["msgs"]
    claim = _claim(m[1].seq, assunto="A1", statement="O operador confirmou o bug do emoji.")
    store.aplicar(cx, lote, Proposta(claims=[claim], marcacao={
        **_ida_e_volta(m[0].seq), str(m[1].seq): {"principal": None}}),
        run_id=mundo["run"], avancar_cursor=False)
    t = dossie_assunto.montar(cx, [mundo["emoji"]])
    assert "### A conversa" in t
    assert "Também de onde vieram as afirmações" in t and f"#{m[1].seq} (" in t


def test_frase_que_acerta_tema_e_conversa_dele_mostra_so_o_tema(cx, mundo):
    if not assuntos.tem_natureza(cx):
        pytest.skip("banco sem a migration 014")
    tema = assuntos.criar(cx, "Teste F7 — tema pai do emoji", quando=_AGORA)
    cx.cursor().execute("UPDATE lucien_subjects SET parent_id = %s WHERE id = %s",
                        (tema, mundo["emoji"]))
    assert dossie_assunto._sem_filho_do_pai(cx, [mundo["emoji"], tema, mundo["ack"]]) == \
        [tema, mundo["ack"]]
