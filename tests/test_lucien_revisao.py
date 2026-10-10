#!/usr/bin/env python3
"""A revisão da memória (F5) — `bot/lucien/revisao.py`, a fonte, e a porta.

O que estes testes guardam:

1. **A seleção** dos dois blocos: só o deduzido pelo LUCIEN no bloco A (a palavra
   do operador vira contagem), confiança mais baixa primeiro, teto global; no
   bloco B, só pendência velha, mais antiga primeiro, sem repetir a perguntada.
2. **A mensagem cabe** numa mensagem de Telegram, com numeração global e a
   etiqueta do tópico.
3. **A correção a partir da fala**: a origem é a fala do operador no PRIVADO, de
   outro tópico que o fato — e continua real, com `seq`, gravada no histórico.
4. **Desfazer** devolve exatamente o estado anterior; desfazer de novo é recusado.
5. **Silêncio**: nada muda, e a pendência perguntada não volta na semana seguinte.
6. **Falha de envio** não corrompe estado nem troca a revisão aberta.
7. **A fonte**: ligar não dispara na hora; dispara uma vez por horário agendado;
   reposição única se a máquina ficou fora; desligada não registra.

COMO RODAR
----------
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_dev .venv/bin/python -m pytest -q \\
        tests/test_lucien_revisao.py

Tudo que grava roda dentro de transação **revertida** no teardown. As datas
semeadas ficam em 2001, longe de qualquer dado real do banco de apoio.
"""

from __future__ import annotations

import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.lucien import porta, revisao, revisao_source, store  # noqa: E402

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")
UTC = timezone.utc
T0 = datetime(2001, 2, 5, 12, 0, tzinfo=UTC)  # "agora" dos testes
CHAT_PRIVADO = 900000000 + random.randint(0, 99999999)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LUCIEN_REVIEW_ENABLED", "true")
    monkeypatch.setenv("LUCIEN_REVIEW_CHAT_ID", str(CHAT_PRIVADO))
    for k in ("LUCIEN_REVIEW_MAX_RECORDED", "LUCIEN_REVIEW_MAX_OPEN",
              "LUCIEN_REVIEW_OPEN_AGE_DAYS", "LUCIEN_REVIEW_REASK_WEEKS",
              "LUCIEN_REVIEW_WHEN"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def cx():
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")
    conn = store.conectar(_URL)
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'lucien_claims_status_check'"
                    " AND pg_get_constraintdef(oid) LIKE '%contestada%'")
        if cur.fetchone() is None:
            pytest.skip("banco sem a migration 011")
        yield conn
    finally:
        conn.rollback()
        conn.close()


# ── semeadura ────────────────────────────────────────────────────────────


def _topico(cx, nome: str, chat: int, thread: int = 0) -> str:
    cur = cx.cursor()
    cur.execute(
        "INSERT INTO topics (telegram_chat_id, telegram_thread_id, current_name)"
        " VALUES (%s,%s,%s) RETURNING id", (chat, thread, nome))
    return str(cur.fetchone()["id"])


def _mensagem(cx, topic_id: str, texto: str, *, role="user", quando=T0,
              audio=False) -> dict:
    cur = cx.cursor()
    cur.execute("SELECT id FROM sessions WHERE topic_id = %s LIMIT 1", (topic_id,))
    s = cur.fetchone()
    if s is None:
        cur.execute("INSERT INTO sessions (topic_id) VALUES (%s) RETURNING id", (topic_id,))
        s = cur.fetchone()
    cur.execute(
        "INSERT INTO messages (session_id, topic_id, role, content, created_at,"
        " audio_transcribed) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id, seq",
        (s["id"], topic_id, role, texto, quando, audio))
    return cur.fetchone()


def _fato(cx, topic_id: str, assunto: str, *, kind="fact", by="lucien",
          conf="media", criado=T0 - timedelta(days=2), vale_desde=None) -> str:
    m = _mensagem(cx, topic_id, f"origem de {assunto}", quando=criado)
    cur = cx.cursor()
    cur.execute(
        "INSERT INTO lucien_claims (topic_id, subject, subject_slug, statement, kind,"
        " status, confidence, valid_from, source_message_id, source_seq, created_by,"
        " created_at) VALUES (%s,%s,%s,%s,%s,'vigente',%s,%s,%s,%s,%s,%s) RETURNING id",
        (topic_id, assunto, store.slug(assunto),
         f"Afirmação de teste sobre {assunto}, longa o bastante.", kind, conf,
         vale_desde or criado, m["id"], m["seq"], by, criado))
    return str(cur.fetchone()["id"])


@pytest.fixture
def mundo(cx):
    sfx = uuid.uuid4().hex[:6]
    dev = _topico(cx, f"Dev {sfx}", -CHAT_PRIVADO, random.randint(10**6, 10**7))
    olimpo = _topico(cx, f"Olimpo {sfx}", -CHAT_PRIVADO, random.randint(10**7, 10**8))
    privado = _topico(cx, f"Private {sfx}", CHAT_PRIVADO, 0)
    f = {
        "alta": _fato(cx, dev, "deduzido alta", conf="alta"),
        "media": _fato(cx, olimpo, "deduzido media", conf="media"),
        "baixa": _fato(cx, dev, "deduzido baixa", conf="baixa"),
        "operador": _fato(cx, dev, "registrado pelo operador", by="operador", conf="alta"),
        "velha1": _fato(cx, dev, "pendência de dezembro", kind="open",
                        criado=T0 - timedelta(days=60)),
        "velha2": _fato(cx, olimpo, "pendência de novembro", kind="open",
                        criado=T0 - timedelta(days=90)),
        "nova": _fato(cx, dev, "pendência recente", kind="open",
                      criado=T0 - timedelta(days=40), vale_desde=T0 - timedelta(days=5)),
        "antes": _fato(cx, dev, "deduzido antes da janela", criado=T0 - timedelta(days=30)),
    }
    return {"dev": dev, "olimpo": olimpo, "privado": privado, "fatos": f, "sfx": sfx}


def _sel(cx, **kw):
    return revisao.selecionar(cx, desde=T0 - timedelta(days=7), ate=T0, **kw)


def _so_do_mundo(itens, mundo):
    ids = set(mundo["fatos"].values())
    return [i for i in itens if i in ids]


# ── 1. seleção ───────────────────────────────────────────────────────────


def test_bloco_a_so_o_deduzido_pelo_lucien_confianca_baixa_primeiro(cx, mundo):
    s = _sel(cx)
    f = mundo["fatos"]
    ids = _so_do_mundo([str(r["id"]) for r in s.gravadas], mundo)
    assert ids == [f["baixa"], f["media"], f["alta"]]
    assert f["operador"] not in ids and f["antes"] not in ids
    assert s.do_operador >= 1


def test_bloco_a_teto_global_e_o_resto_vira_contagem(cx, mundo, monkeypatch):
    monkeypatch.setenv("LUCIEN_REVIEW_MAX_RECORDED", "2")
    s = _sel(cx)
    assert len(s.gravadas) == 2 and s.gravadas_fora >= 1


def test_bloco_b_so_pendencia_velha_mais_antiga_primeiro(cx, mundo):
    s = _sel(cx)
    f = mundo["fatos"]
    ids = _so_do_mundo([str(r["id"]) for r in s.pendencias], mundo)
    assert ids == [f["velha2"], f["velha1"]]  # a "recente" (5 dias) não entra


def test_bloco_b_nao_repergunta_a_perguntada_nem_a_reconfirmada(cx, mundo):
    f = mundo["fatos"]
    s = _sel(cx, perguntadas={f["velha2"]: revisao._iso(T0 - timedelta(days=7))})
    assert _so_do_mundo([str(r["id"]) for r in s.pendencias], mundo) == [f["velha1"]]
    # perguntada há mais de 8 semanas volta
    s = _sel(cx, perguntadas={f["velha2"]: revisao._iso(T0 - timedelta(weeks=9))})
    assert f["velha2"] in [str(r["id"]) for r in s.pendencias]


def test_mensagem_cabe_no_telegram_com_numeracao_global(cx, mundo):
    sfx = mundo["sfx"]
    for k in range(70):
        _fato(cx, mundo["dev"], f"assunto comprido número {k} {sfx} " + "x" * 60)
    sel = _sel(cx)
    itens, texto = revisao.preparar(sel)
    assert len(texto) <= revisao.TEXTO_MAXIMO
    assert [i["n"] for i in itens] == list(range(1, len(itens) + 1))
    assert texto.startswith(revisao.MARCA)
    assert "que não couberam aqui" in texto
    assert f"[Olimpo {sfx}]" in texto  # a etiqueta do tópico


# ── 2. o ciclo: envio, falha, silêncio ──────────────────────────────────


def _rodar(cx, tmp_path, *, ok=True, agora=T0):
    enviados = []

    def fake(home, chat, texto):
        enviados.append((chat, texto))
        return ok
    r = revisao.rodar_ciclo(cx, tmp_path, agora=agora, enviar_fn=fake)
    return r, enviados


def test_envio_abre_a_revisao_no_privado(cx, mundo, tmp_path):
    r, env = _rodar(cx, tmp_path)
    assert r["enviada"] and env[0][0] == CHAT_PRIVADO
    rev = revisao.aberta(tmp_path)
    assert rev and rev["id"] == r["id"] and len(rev["itens"]) == r["itens"]
    assert rev["texto"] == env[0][1]


def test_falha_de_envio_nao_troca_a_revisao_aberta(cx, mundo, tmp_path):
    r1, _ = _rodar(cx, tmp_path)
    antes = revisao.ler_estado(tmp_path)
    _fato(cx, mundo["dev"], "deduzido na semana seguinte", criado=T0 + timedelta(days=3))
    r2, _ = _rodar(cx, tmp_path, ok=False, agora=T0 + timedelta(days=7))
    depois = revisao.ler_estado(tmp_path)
    assert not r2["enviada"] and depois["falhas"] == 1
    assert depois["aberta"] == antes["aberta"]
    assert depois["janela_ate"] == antes["janela_ate"]


def test_silencio_nada_muda_e_nao_repergunta(cx, mundo, tmp_path):
    f = mundo["fatos"]
    cur = cx.cursor()
    cur.execute("SELECT id, status FROM lucien_claims WHERE id::text = ANY(%s)",
                (list(f.values()),))
    estado0 = {str(r["id"]): r["status"] for r in cur.fetchall()}
    _rodar(cx, tmp_path)
    ids1 = {i["claim_id"] for i in revisao.aberta(tmp_path)["itens"]}
    assert f["velha1"] in ids1
    r2, env2 = _rodar(cx, tmp_path, agora=T0 + timedelta(days=7))
    rev2 = revisao.aberta(tmp_path)
    ids2 = {i["claim_id"] for i in rev2["itens"]} if rev2 else set()
    assert f["velha1"] not in ids2 and f["velha2"] not in ids2
    cur.execute("SELECT id, status FROM lucien_claims WHERE id::text = ANY(%s)",
                (list(f.values()),))
    assert {str(r["id"]): r["status"] for r in cur.fetchall()} == estado0


# ── 3. correção a partir da fala ────────────────────────────────────────


def _abrir(cx, mundo, tmp_path):
    _rodar(cx, tmp_path)
    rev = revisao.aberta(tmp_path)
    num = {i["claim_id"]: i["n"] for i in rev["itens"]}
    fala = _mensagem(cx, mundo["privado"], "o 3 tá errado e a 12 eu já entreguei",
                     quando=T0 + timedelta(hours=1))
    origem = porta.origem_da_fala(cx, mundo["privado"])
    assert origem["seq"] == fala["seq"]
    return rev, num, origem


def _evs(cx, claim_id):
    cur = cx.cursor()
    cur.execute("SELECT action, detail, actor FROM lucien_events WHERE claim_id=%s"
                " ORDER BY at, id", (claim_id,))
    return cur.fetchall()


def _status(cx, claim_id):
    return store._imagem(cx, claim_id)["status"]


def test_contestar_com_origem_no_privado_e_frase_no_historico(cx, mundo, tmp_path):
    f = mundo["fatos"]
    rev, num, origem = _abrir(cx, mundo, tmp_path)
    revisao.aplicar(cx, rev, [{"item": num[f["media"]], "acao": "contestar"}],
                    frase="o item tá errado", origem=origem)
    assert _status(cx, f["media"]) == "contestada"
    ev = _evs(cx, f["media"])[-1]
    assert ev["action"] == "contested" and ev["actor"] == "operador"
    assert ev["detail"]["frase"] == "o item tá errado"
    assert ev["detail"]["origem_seq"] == origem["seq"]
    assert ev["detail"]["origem_topico"] == mundo["privado"]  # outro tópico, escrito
    assert ev["detail"]["revisao"] == rev["id"]


def test_corrigir_cria_versao_do_operador_amarrada_a_fala_real(cx, mundo, tmp_path):
    f = mundo["fatos"]
    rev, num, origem = _abrir(cx, mundo, tmp_path)
    out = revisao.aplicar(
        cx, rev, [{"item": num[f["alta"]], "acao": "corrigir",
                   "texto": "Isso vale só para o ambiente de desenvolvimento."}],
        frase="o item é só pra dev", origem=origem)
    novo = out[0]["novo"]
    antigo = store._imagem(cx, f["alta"])
    assert antigo["status"] == "superada" and antigo["superseded_by"] == novo
    cur = cx.cursor()
    cur.execute("SELECT topic_id, created_by, source_message_id, source_seq, status"
                " FROM lucien_claims WHERE id=%s", (novo,))
    n = cur.fetchone()
    assert str(n["topic_id"]) == mundo["dev"]           # vive no tópico do fato
    assert str(n["source_message_id"]) == str(origem["id"])  # origem real, no privado
    assert n["source_seq"] == origem["seq"] and n["created_by"] == "operador"
    assert n["status"] == "vigente"


def test_fechar_pendencia_e_recusar_fechar_o_que_nao_e_pendencia(cx, mundo, tmp_path):
    f = mundo["fatos"]
    rev, num, origem = _abrir(cx, mundo, tmp_path)
    revisao.aplicar(cx, rev, [{"item": num[f["velha1"]], "acao": "fechar"}],
                    frase="já entreguei", origem=origem)
    assert _status(cx, f["velha1"]) == "fechada"
    with pytest.raises(revisao.RevisaoRecusou, match="não é pendência"):
        revisao.aplicar(cx, rev, [{"item": num[f["alta"]], "acao": "fechar"}],
                        frase="fechou", origem=origem)


def test_manter_registra_reconfirmacao_sem_mudar_a_linha(cx, mundo, tmp_path):
    f = mundo["fatos"]
    rev, num, origem = _abrir(cx, mundo, tmp_path)
    revisao.aplicar(cx, rev, [{"item": num[f["velha2"]], "acao": "manter"}],
                    frase="essa ainda vale", origem=origem)
    assert _status(cx, f["velha2"]) == "vigente"
    assert _evs(cx, f["velha2"])[-1]["action"] == "reconfirmed"
    assert "reconfirmou" in revisao.foto(cx, rev)


def test_recusas_item_inexistente_acao_desconhecida_corrigir_sem_texto(cx, mundo, tmp_path):
    rev, num, origem = _abrir(cx, mundo, tmp_path)
    for ops, msg in [([{"item": 999, "acao": "contestar"}], "não existe item"),
                     ([{"item": 1, "acao": "apagar"}], "não existe"),
                     ([{"item": 1, "acao": "corrigir"}], "texto novo"),
                     ([], "nenhuma operação")]:
        with pytest.raises(revisao.RevisaoRecusou, match=msg):
            revisao.aplicar(cx, rev, ops, frase="x", origem=origem)
    with pytest.raises(revisao.RevisaoRecusou, match="frase"):
        revisao.aplicar(cx, rev, [{"item": 1, "acao": "contestar"}], frase=" ",
                        origem=origem)


def test_foto_mostra_o_estado_e_os_intocados_numa_linha(cx, mundo, tmp_path):
    f = mundo["fatos"]
    rev, num, origem = _abrir(cx, mundo, tmp_path)
    revisao.aplicar(cx, rev, [{"item": num[f["media"]], "acao": "contestar"}],
                    frase="errado", origem=origem)
    texto = revisao.foto(cx, rev)
    assert texto.startswith(revisao.MARCA)
    assert "contestada" in texto and "Os demais seguem como estavam" in texto


# ── 4. desfazer ─────────────────────────────────────────────────────────


def test_desfazer_contestacao_e_correcao(cx, mundo, tmp_path):
    f = mundo["fatos"]
    rev, num, origem = _abrir(cx, mundo, tmp_path)
    out = revisao.aplicar(cx, rev, [
        {"item": num[f["media"]], "acao": "contestar"},
        {"item": num[f["alta"]], "acao": "corrigir",
         "texto": "Texto corrigido pelo operador, longo o bastante."},
    ], frase="o 1 errado e o 2 é outra coisa", origem=origem)
    novo = out[1]["novo"]
    revisao.aplicar(cx, rev, [{"item": num[f["media"]], "acao": "desfazer"},
                              {"item": num[f["alta"]], "acao": "desfazer"}],
                    frase="desfaz, me enganei", origem=origem)
    assert _status(cx, f["media"]) == "vigente"
    antigo = store._imagem(cx, f["alta"])
    assert antigo["status"] == "vigente" and antigo["superseded_by"] is None
    assert _status(cx, novo) == "abandonada"
    with pytest.raises(revisao.RevisaoRecusou, match="nada aplicado"):
        revisao.aplicar(cx, rev, [{"item": num[f["media"]], "acao": "desfazer"}],
                        frase="desfaz de novo", origem=origem)
    # e dá para contestar de novo depois de desfazer
    revisao.aplicar(cx, rev, [{"item": num[f["media"]], "acao": "contestar"}],
                    frase="agora sim errado", origem=origem)
    assert _status(cx, f["media"]) == "contestada"


# ── 5. o bloco do prompt ────────────────────────────────────────────────


def test_bloco_so_no_privado_e_so_com_a_chave(cx, mundo, tmp_path, monkeypatch):
    _rodar(cx, tmp_path)
    b = revisao.render_bloco(tmp_path, CHAT_PRIVADO, None)
    assert b and b.startswith("[Revisão da memória aguardando tua resposta]")
    assert "kobe-revisao aplicar" in b
    assert revisao.render_bloco(tmp_path, CHAT_PRIVADO, 0) == b
    assert revisao.render_bloco(tmp_path, -CHAT_PRIVADO, 475) is None
    assert revisao.render_bloco(tmp_path, CHAT_PRIVADO + 1, None) is None
    monkeypatch.setenv("LUCIEN_REVIEW_ENABLED", "false")
    from bot.lucien.revisao_context import render_revisao_aberta
    assert render_revisao_aberta(tmp_path, CHAT_PRIVADO, None) is None


# ── 6. o curador não relê a foto como fato ──────────────────────────────


def test_lote_do_lucien_neutraliza_a_foto_da_revisao(cx, mundo):
    m = _mensagem(cx, mundo["dev"], revisao.MARCA + " — como ficou\n1. [x] fato — contestada",
                  role="assistant")
    lote = store.montar_lote(cx, mundo["dev"])
    corpo = {x.seq: x.content for x in lote.mensagens}
    assert m["seq"] in corpo and revisao.MARCA not in corpo[m["seq"]]


# ── 7. a fonte (sem banco) ──────────────────────────────────────────────


def test_ultimo_agendado_segunda_9h_brasilia():
    # 05/02/2001 é segunda. 12:00 UTC = 10:00 em Brasília (horário de verão: -02)
    m = revisao_source.ultimo_agendado("0 9 * * 1", T0)
    assert m.astimezone(revisao.TZ).strftime("%a %H:%M") in ("Mon 09:00",)
    assert m <= T0


def test_fonte_ligar_nao_dispara_e_dispara_uma_vez_por_marco(tmp_path, monkeypatch):
    chamadas = []
    monkeypatch.setattr(revisao, "rodar_ciclo",
                        lambda cx, home, agora, marco, token: chamadas.append(marco) or
                        _marca(home, marco))

    class FakeCx:
        def rollback(self): pass
        def close(self): pass

    src = revisao_source.RevisaoSource(kobe_home=tmp_path, db_factory=FakeCx)
    quarta = datetime(2001, 2, 7, 15, 0, tzinfo=UTC)
    src.tick(agora=quarta)                      # liga numa quarta: não manda
    src.tick(agora=quarta + timedelta(hours=1))
    assert chamadas == []
    segunda = datetime(2001, 2, 12, 12, 30, tzinfo=UTC)  # 10h30 em Brasília
    src.tick(agora=segunda)
    src.tick(agora=segunda + timedelta(minutes=10))
    assert len(chamadas) == 1
    # máquina fora na segunda seguinte: volta na quarta e repõe UMA vez
    volta = datetime(2001, 2, 21, 12, 0, tzinfo=UTC)
    src.tick(agora=volta)
    src.tick(agora=volta + timedelta(minutes=10))
    assert len(chamadas) == 2


def _marca(home, marco):
    with revisao.travado(home):
        e = revisao.ler_estado(home)
        e["ultimo_marco"] = revisao._iso(marco)
        revisao.gravar_estado(home, e)
    return {"ok": True, "enviada": True, "id": "x", "itens": 1}


def test_desligada_nao_registra_e_sem_destino_tambem_nao(monkeypatch, tmp_path):
    monkeypatch.setenv("LUCIEN_REVIEW_ENABLED", "false")
    assert revisao_source.build(kobe_home=tmp_path) is None
    monkeypatch.setenv("LUCIEN_REVIEW_ENABLED", "true")
    monkeypatch.delenv("LUCIEN_REVIEW_CHAT_ID")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123,-100456")
    assert revisao_source.build(kobe_home=tmp_path) is None
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123,305")
    assert revisao_source.build(kobe_home=tmp_path) is not None


def test_env_example_nasce_ligada_e_diz_como_desligar():
    """Decisão do operador (10/10/2026): ligada por padrão para todo mundo — e
    quem instala tem que descobrir lendo, não pela mensagem na primeira segunda."""
    texto = (RAIZ / ".env.example").read_text(encoding="utf-8")
    assert "\nLUCIEN_REVIEW_ENABLED=true\n" in texto
    antes = texto.split("\nLUCIEN_REVIEW_ENABLED=true\n")[0][-500:]
    assert "segunda às 9h" in antes and "mude para false" in antes


def test_desligada_o_comando_recusa_e_nada_aparece(tmp_path):
    """O botão de desligar é de verdade: o comando do agente recusa."""
    import subprocess

    env = dict(os.environ, LUCIEN_REVIEW_ENABLED="false", KOBE_HOME=str(tmp_path))
    p = subprocess.run([sys.executable, str(RAIZ / "bot" / "bin" / "kobe-revisao"), "ver"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 1 and "desligada" in p.stderr


def test_envio_leva_o_token_ao_kobe_notify(tmp_path, monkeypatch):
    """Regressão do teste de ponta a ponta: no Keyko não há
    `KOBE_TELEGRAM_BOT_TOKEN` no ambiente, e o `kobe-notify` só lê dali."""
    import subprocess

    visto = {}

    class R:
        returncode = 0
        stderr = b""

    def fake_run(cmd, env, **kw):
        visto.update(env)
        return R()
    monkeypatch.delenv("KOBE_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert revisao.enviar(tmp_path, 123, "oi", token="tok-x")
    assert visto["KOBE_TELEGRAM_BOT_TOKEN"] == "tok-x"
    assert visto["KOBE_CHAT_ID"] == "123" and "KOBE_THREAD_ID" not in visto
    src = revisao_source.build(kobe_home=tmp_path, bot_token="tok-y")
    assert src is not None and src._bot_token == "tok-y"
