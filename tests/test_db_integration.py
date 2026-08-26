#!/usr/bin/env python3
"""Integração real: cada função convertida, exercitada contra Postgres de verdade.

POR QUE ESTE ARQUIVO É A PROVA DA MIGRAÇÃO, E NÃO UM EXTRA
-----------------------------------------------------------
O resto da suíte finge no nível de FUNÇÃO, não no nível de banco: os testes
trocam `get_active_session`, `count_messages`, `get_recent_messages` por
lambdas. Isso é ótimo para velocidade e para blast radius — e é exatamente o
buraco desta migração. **Reescrever o corpo de `get_recent_messages` em SQL não
é coberto por nada lá.** Um `ORDER BY` invertido, um `eq` que virou `gte`, um
`LIMIT` esquecido, um `RETURNING` faltando: tudo isso passa verde numa suíte que
substitui a função inteira por uma lambda.

Então aqui não se testa "não levantou exceção". Testa-se **semântica**: a ordem
sai crescente? o `RETURNING` devolve a linha? a segunda chamada é idempotente? o
filtro por tópico exclui o tópico errado? o estado one-shot some depois de lido?

ISOLAMENTO: UMA TRANSAÇÃO POR TESTE, SEMPRE REVERTIDA
------------------------------------------------------
Cada teste roda dentro de uma transação própria que é **desfeita no teardown**.
Isso dá três coisas de uma vez: os testes não se enxergam, a suíte é repetível
(rodar duas vezes seguidas dá o mesmo resultado), e nada precisa ser apagado —
não há comando destrutivo em lugar nenhum deste arquivo.

A primeira versão destes testes usava um `(chat_id, thread_id)` derivado do nome
do teste e não limpava nada. Passou na primeira execução e **falhou na segunda**,
com dez testes quebrando por dado acumulado da rodada anterior. Suíte que só
passa uma vez não é suíte; a transação revertida é o conserto.

O que esse desenho NÃO exercita, dito às claras: como tudo roda numa transação
só, não se testa visibilidade entre transações nem o comportamento do pool sob
concorrência. Isso é coberto em `tests/test_db_resilience.py` e pela fumaça
manual registrada no CHANGELOG. Aqui o alvo é o SQL.

COMO RODAR
----------
    createdb kobe_test   # uma vez
    .venv/bin/python infra/migrate.py up --database-url postgresql:///kobe_test
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test .venv/bin/python -m pytest -q \\
        tests/test_db_integration.py

Sem `KOBE_TEST_DATABASE_URL` tudo aqui é pulado, para que um clone limpo siga
verde. O teste de conformidade no fim é a exceção: ele lê o repositório, não o
banco, e por isso roda sempre.
"""
from __future__ import annotations

import os
import re
import sys
import zlib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")

# O "pular sem banco" mora na fixture `db`, e NÃO num `pytestmark` de módulo,
# de propósito: com a marca no módulo o teste de conformidade do fim — que lê o
# repositório e não encosta em banco nenhum — seria pulado junto, e a rede que
# pega um ponto do driver antigo esquecido só existiria na máquina de quem tem
# o banco de integração montado. Quem não usa a fixture, roda sempre.


@pytest.fixture
def db():
    """A ponte real, amarrada a UMA conexão em transação que sempre volta atrás.

    Herda `KobeDB` e troca só o `_attempt` — ou seja, os quatro verbos, a
    política de repetição e **a normalização de tipos** são exatamente os de
    produção. O que muda é onde a consulta roda: numa conexão só, sem
    `autocommit`, revertida no teardown.
    """
    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida — sem banco de integração")

    import psycopg
    from psycopg.rows import dict_row

    from bot.db import KobeDB, _normalize_row

    class _PonteEmTransacao(KobeDB):
        def __init__(self, conn):
            self._conn = conn  # sem super().__init__: nada de pool aqui

        def _attempt(self, sql, params):
            with self._conn.cursor() as cur:
                cur.execute(sql, tuple(params) if params else None)
                if cur.description is None:
                    return []
                return [_normalize_row(linha) for linha in cur.fetchall()]

    conn = psycopg.connect(
        _URL, row_factory=dict_row, autocommit=False, options="-c TimeZone=UTC"
    )
    try:
        yield _PonteEmTransacao(conn)
    finally:
        conn.rollback()  # nada do que o teste fez sobrevive
        conn.close()


@pytest.fixture
def canal(request):
    """`(chat_id, thread_id)` sintético e distinto por teste.

    Derivado do nome do teste com `crc32`. Com a transação revertida o
    isolamento já estaria garantido; isto é cinto e suspensório, e mantém a
    linha de um teste que falhou identificável enquanto ele roda.
    """
    marca = zlib.crc32(request.node.name.encode()) % 900_000
    return -100_000_000 - marca, 900_000 + marca


@pytest.fixture
def topico(db, canal):
    from bot import topic_manager as tm

    chat_id, thread_id = canal
    return tm.ensure_topic(db, thread_id, chat_id=chat_id)


@pytest.fixture
def sessao(db, topico):
    from bot import topic_manager as tm

    return tm.ensure_active_session(db, topico)


def _msg(db, sessao, topico, conteudo, minuto, role="user"):
    """Insere uma mensagem e FIXA o `created_at` num instante conhecido.

    Isto não é conveniência — é necessidade, e ela ensina uma coisa sobre o
    schema: `messages.created_at` tem `DEFAULT now()`, e `now()` no Postgres é
    o carimbo de INÍCIO DA TRANSAÇÃO, não o relógio de parede. Como cada teste
    aqui roda numa transação só, três inserts seguidos receberiam o **mesmo**
    `created_at`, os `ORDER BY created_at` empatariam, e um teste de ordem
    passaria ou falharia pela ordem física das linhas — quer dizer, por acaso.

    Em produção isso não acontece: lá cada insert é sua própria transação
    (a ponte usa `autocommit`), então os carimbos saem distintos. O problema é
    do arranjo do teste, e a saída é o teste dizer QUAL é o instante de cada
    mensagem em vez de torcer pela resolução do relógio.
    """
    from bot import topic_manager as tm

    novo = tm.insert_message(
        db, session_id=sessao, topic_id=topico, role=role, content=conteudo
    )
    db.execute(
        "UPDATE messages SET created_at = %s WHERE id = %s",
        (f"2026-08-26T10:{minuto:02d}:00+00:00", novo),
    )
    return novo


# ══ topics ════════════════════════════════════════════════════════════════


def test_ensure_topic_cria_e_e_idempotente(db, canal):
    from bot import topic_manager as tm

    chat_id, thread_id = canal
    primeiro = tm.ensure_topic(db, thread_id, chat_id=chat_id)
    segundo = tm.ensure_topic(db, thread_id, chat_id=chat_id)

    assert primeiro == segundo, "a segunda chamada criou uma linha nova"


def test_ensure_topic_nomeia_o_raiz_conforme_o_sinal_do_chat(db):
    """`thread_id=0` é a sentinela do raiz. O chat privado (`chat_id > 0`) e o
    'Geral' do supergrupo (`chat_id < 0`) colidiriam sem a UNIQUE composta —
    e é essa distinção que dá o nome automático."""
    from bot import topic_manager as tm

    privado = tm.ensure_topic(db, 0, chat_id=778_001)
    geral = tm.ensure_topic(db, 0, chat_id=-100_778_002)

    assert privado != geral
    assert db.scalar("SELECT current_name FROM topics WHERE id = %s", (privado,)) == "Private"
    assert db.scalar("SELECT current_name FROM topics WHERE id = %s", (geral,)) == "General"


def test_set_topic_name_devolve_none_ao_criar_e_o_anterior_ao_renomear(db, canal):
    """O retorno NÃO é decorativo: é ele que o caller usa pra detectar rename
    real e mover a pasta do tópico no filesystem."""
    from bot import topic_manager as tm

    chat_id, thread_id = canal

    assert tm.set_topic_name(db, chat_id=chat_id, thread_id=thread_id, name="Primeiro") is None
    assert tm.set_topic_name(db, chat_id=chat_id, thread_id=thread_id, name="Segundo") == "Primeiro"
    assert tm.set_topic_name(db, chat_id=chat_id, thread_id=thread_id, name="Segundo") is None


def test_set_topic_name_em_topico_novo_usa_a_unique_que_existe(db, canal):
    """Regressão direta do bug achado na conversão: o `ON CONFLICT` apontava
    para uma UNIQUE em `telegram_thread_id` sozinha, que `infra/schema.sql`
    REMOVE. A forma antiga levanta `InvalidColumnReference` aqui."""
    from bot import topic_manager as tm

    chat_id, thread_id = canal
    tm.set_topic_name(db, chat_id=chat_id, thread_id=thread_id, name="Nasceu pelo nome")

    assert tm.get_topic_slug(db, chat_id, thread_id) == "nasceu-pelo-nome"


def test_o_mesmo_thread_id_em_chats_diferentes_gera_topicos_distintos(db, canal):
    """A razão de a UNIQUE ser composta — e o que faz um supergrupo de dev
    conviver com o de produção sem colidir."""
    from bot import topic_manager as tm

    _, thread_id = canal
    a = tm.ensure_topic(db, thread_id, chat_id=-100_555_001)
    b = tm.ensure_topic(db, thread_id, chat_id=-100_555_002)

    assert a != b


def test_get_topic_slug_normaliza_acento_e_caixa(db, canal):
    from bot import topic_manager as tm

    chat_id, thread_id = canal
    tm.set_topic_name(db, chat_id=chat_id, thread_id=thread_id, name="Café & Livros")

    assert tm.get_topic_slug(db, chat_id, thread_id) == "cafe-livros"


def test_set_topic_status_devolve_o_id_e_none_quando_nao_ha_linha(db, canal):
    """Depende de `RETURNING id`: sem ele o retorno seria sempre `None`."""
    from bot import topic_manager as tm

    chat_id, thread_id = canal
    tm.ensure_topic(db, thread_id, chat_id=chat_id)

    assert tm.set_topic_status(db, chat_id=chat_id, thread_id=thread_id, status="archived")
    assert tm.set_topic_status(db, chat_id=-1, thread_id=-1, status="archived") is None


def test_mark_welcomed_tira_o_topico_da_lista_de_pendentes(db, canal):
    from bot import topic_manager as tm

    chat_id, thread_id = canal
    topic_id = tm.ensure_topic(db, thread_id, chat_id=chat_id)

    pendentes = {t["id"] for t in tm.list_unwelcomed_topics(db)}
    assert topic_id in pendentes

    tm.mark_welcomed(db, topic_id)

    assert topic_id not in {t["id"] for t in tm.list_unwelcomed_topics(db)}


def test_topico_arquivado_nao_entra_na_lista_de_pendentes(db, canal):
    """O filtro é `status = 'active'`, não só `welcomed_at IS NULL`."""
    from bot import topic_manager as tm

    chat_id, thread_id = canal
    topic_id = tm.ensure_topic(db, thread_id, chat_id=chat_id)
    tm.set_topic_status(db, chat_id=chat_id, thread_id=thread_id, status="archived")

    assert topic_id not in {t["id"] for t in tm.list_unwelcomed_topics(db)}


# ══ sessions ══════════════════════════════════════════════════════════════


def test_ensure_active_session_e_idempotente(db, topico):
    from bot import topic_manager as tm

    assert tm.ensure_active_session(db, topico) == tm.ensure_active_session(db, topico)


def test_get_active_session_devolve_none_sem_sessao(db, topico):
    from bot import topic_manager as tm

    assert tm.get_active_session(db, topico) is None


def test_archive_devolve_o_id_na_primeira_vez_e_none_na_segunda(db, topico, sessao):
    """Depende de `RETURNING id`. Sem ele, `/nova` sempre acharia que não há
    sessão pra arquivar."""
    from bot import topic_manager as tm

    assert tm.archive_active_session(db, topico) == sessao
    assert tm.archive_active_session(db, topico) is None


def test_archive_grava_o_resumo_quando_ele_vem(db, topico, sessao):
    from bot import topic_manager as tm

    tm.archive_active_session(db, topico, summary="o que foi conversado")

    assert db.scalar("SELECT summary FROM sessions WHERE id = %s", (sessao,)) == (
        "o que foi conversado"
    )


def test_archive_sem_resumo_nao_apaga_um_resumo_ja_gravado(db, topico, sessao):
    """A razão de o `SET` ser montado dinamicamente: um `summary = NULL`
    explícito apagaria o que já estava lá."""
    from bot import topic_manager as tm

    db.execute("UPDATE sessions SET summary = %s WHERE id = %s", ("resumo antigo", sessao))
    tm.archive_active_session(db, topico)

    assert db.scalar("SELECT summary FROM sessions WHERE id = %s", (sessao,)) == "resumo antigo"


def test_archive_recusa_status_invalido(db, topico, sessao):
    from bot import topic_manager as tm

    with pytest.raises(ValueError):
        tm.archive_active_session(db, topico, status="qualquer-coisa")


def test_depois_de_arquivar_uma_sessao_nova_nasce(db, topico, sessao):
    from bot import topic_manager as tm

    tm.archive_active_session(db, topico)

    assert tm.ensure_active_session(db, topico) != sessao


# ══ messages ══════════════════════════════════════════════════════════════


def test_insert_message_devolve_o_id(db, topico, sessao):
    from bot import topic_manager as tm

    novo = tm.insert_message(
        db, session_id=sessao, topic_id=topico, role="user", content="oi"
    )
    assert novo and db.scalar("SELECT content FROM messages WHERE id = %s", (novo,)) == "oi"


def test_insert_message_grava_os_campos_opcionais(db, topico, sessao):
    from bot import topic_manager as tm

    novo = tm.insert_message(
        db, session_id=sessao, topic_id=topico, role="user", content="áudio",
        telegram_message_id=4242, audio_transcribed=True,
    )
    linha = db.one(
        "SELECT telegram_message_id, audio_transcribed FROM messages WHERE id = %s", (novo,)
    )
    assert linha == {"telegram_message_id": 4242, "audio_transcribed": True}


def test_count_messages_conta_so_a_sessao_pedida(db, topico, sessao):
    from bot import topic_manager as tm

    outra = tm.ensure_active_session(db, tm.ensure_topic(db, 911_001, chat_id=-100_911_001))
    for i in range(3):
        tm.insert_message(db, session_id=sessao, topic_id=topico, role="user", content=str(i))

    assert tm.count_messages(db, sessao) == 3
    assert tm.count_messages(db, outra) == 0


def test_get_recent_messages_sai_em_ordem_cronologica_crescente(db, topico, sessao):
    """A consulta é DESC (pra pegar as mais recentes de uma sessão longa) e a
    lista é revertida. Inverter isso entregaria a conversa de trás pra frente
    no prompt — e nada acusaria."""
    from bot import topic_manager as tm

    for i, texto in enumerate(("primeira", "segunda", "terceira")):
        _msg(db, sessao, topico, texto, minuto=i)

    assert [m["content"] for m in tm.get_recent_messages(db, sessao)] == [
        "primeira", "segunda", "terceira",
    ]


def test_get_recent_messages_respeita_o_limite_e_mantem_as_MAIS_RECENTES(db, topico, sessao):
    from bot import topic_manager as tm

    for i in range(5):
        _msg(db, sessao, topico, f"m{i}", minuto=i)

    assert [m["content"] for m in tm.get_recent_messages(db, sessao, limit=2)] == ["m3", "m4"]


def test_get_messages_since_e_estritamente_posterior(db, topico, sessao):
    from bot import topic_manager as tm

    _msg(db, sessao, topico, "antes", minuto=0)
    marca = db.scalar("SELECT max(created_at) FROM messages WHERE topic_id = %s", (topico,))
    _msg(db, sessao, topico, "depois", minuto=1)

    novas = tm.get_messages_since(db, topico, marca)

    assert [m["content"] for m in novas] == ["depois"], "a marca-d'água tem que ser exclusiva"


def test_get_messages_since_devolve_vazio_com_marca_no_futuro(db, topico, sessao):
    from bot import topic_manager as tm

    tm.insert_message(db, session_id=sessao, topic_id=topico, role="user", content="x")

    assert tm.get_messages_since(db, topico, "2099-01-01T00:00:00+00:00") == []


def test_get_messages_since_le_por_topico_e_nao_por_sessao(db, topico, sessao):
    """De propósito: a sessão pode ter rotacionado entre o despacho de uma run
    de background e o momento de ela reler o que chegou."""
    from bot import topic_manager as tm

    _msg(db, sessao, topico, "na velha", minuto=0)
    tm.archive_active_session(db, topico)
    nova = tm.ensure_active_session(db, topico)
    _msg(db, nova, topico, "na nova", minuto=1)

    conteudos = [m["content"] for m in tm.get_messages_since(db, topico, "2000-01-01T00:00:00+00:00")]

    assert conteudos == ["na velha", "na nova"]


def test_ultima_mensagem_do_assistente_ignora_as_do_usuario(db, topico, sessao):
    from bot import topic_manager as tm

    _msg(db, sessao, topico, "resposta", minuto=0, role="assistant")
    _msg(db, sessao, topico, "pergunta depois", minuto=1)

    assert tm.get_last_assistant_message_of_session(db, sessao) == "resposta"


def test_meta_da_ultima_do_assistente_traz_carimbo_legivel(db, topico, sessao):
    """O `created_at` tem que voltar como texto ISO — é assim que o resto do
    Kobe o trata."""
    from bot import topic_manager as tm
    from bot.memory.aging import parse_ts

    tm.insert_message(db, session_id=sessao, topic_id=topico, role="assistant", content="oi")
    meta = tm.get_last_assistant_message_meta_of_session(db, sessao)

    assert isinstance(meta["created_at"], str)
    assert parse_ts(meta["created_at"]) is not None


def test_sem_mensagem_do_assistente_devolve_none(db, sessao):
    from bot import topic_manager as tm

    assert tm.get_last_assistant_message_of_session(db, sessao) is None
    assert tm.get_last_assistant_message_meta_of_session(db, sessao) is None


# ══ awaiting_slash_response (jsonb, one-shot) ════════════════════════════


def test_pop_awaiting_devolve_o_estado_e_o_apaga(db, sessao):
    """One-shot: quem fez a pergunta consome o estado nessa mensagem."""
    import psycopg
    from datetime import datetime, timezone

    from bot import topic_manager as tm

    estado = {
        "plugin": "teste",
        "question": "confirma?",
        "asked_at": datetime.now(timezone.utc).isoformat(),
        "expires_in_seconds": 600,
    }
    db.execute(
        "UPDATE sessions SET awaiting_slash_response = %s WHERE id = %s",
        (psycopg.types.json.Jsonb(estado), sessao),
    )

    assert tm.pop_awaiting_slash_response(db, sessao)["plugin"] == "teste"
    assert tm.pop_awaiting_slash_response(db, sessao) is None


def test_pop_awaiting_descarta_estado_vencido_mas_ainda_limpa(db, sessao):
    import psycopg

    from bot import topic_manager as tm

    db.execute(
        "UPDATE sessions SET awaiting_slash_response = %s WHERE id = %s",
        (
            psycopg.types.json.Jsonb(
                {"asked_at": "2020-01-01T00:00:00+00:00", "expires_in_seconds": 60}
            ),
            sessao,
        ),
    )

    assert tm.pop_awaiting_slash_response(db, sessao) is None
    assert db.scalar(
        "SELECT awaiting_slash_response FROM sessions WHERE id = %s", (sessao,)
    ) is None, "vencido ou não, o campo é zerado na leitura"


def test_pop_awaiting_sem_estado_devolve_none(db, sessao):
    from bot import topic_manager as tm

    assert tm.pop_awaiting_slash_response(db, sessao) is None


# ══ saved_artifacts ═══════════════════════════════════════════════════════


def test_save_artifact_devolve_id_e_grava_as_tags(db, topico):
    from bot import artifacts as ar

    novo = ar.save_artifact_from_messages(
        db, topic_id=topico, title="Reunião",
        messages=[{"role": "user", "content": "falamos de orçamento"}],
        tags=["um", "dois"],
    )

    assert db.scalar("SELECT tags FROM saved_artifacts WHERE id = %s", (novo,)) == ["um", "dois"]


def test_save_artifact_sem_tags_grava_nulo(db, topico):
    from bot import artifacts as ar

    novo = ar.save_artifact_from_messages(
        db, topic_id=topico, title="Sem tags", messages=[{"role": "user", "content": "x"}]
    )

    assert db.scalar("SELECT tags FROM saved_artifacts WHERE id = %s", (novo,)) is None


def test_save_artifact_de_sessao_vazia_nao_grava_nada(db, topico):
    from bot import artifacts as ar

    assert ar.save_artifact_from_messages(db, topic_id=topico, title="x", messages=[]) is None


def test_busca_acha_por_titulo_e_por_conteudo_sem_diferenciar_caixa(db, topico):
    from bot import artifacts as ar

    ar.save_artifact_from_messages(
        db, topic_id=topico, title="Ata de Planejamento",
        messages=[{"role": "user", "content": "decidimos o cronograma"}],
    )

    por_titulo = ar.search_artifacts(db, "PLANEJAMENTO", topic_id=topico)
    por_conteudo = ar.search_artifacts(db, "cronograma", topic_id=topico)

    assert [a["title"] for a in por_titulo] == ["Ata de Planejamento"]
    assert [a["title"] for a in por_conteudo] == ["Ata de Planejamento"]


def test_busca_filtrada_por_topico_exclui_o_topico_errado(db, topico):
    from bot import artifacts as ar
    from bot import topic_manager as tm

    ar.save_artifact_from_messages(
        db, topic_id=topico, title="Marcador único xyzzy",
        messages=[{"role": "user", "content": "conteúdo"}],
    )
    outro = tm.ensure_topic(db, 922_001, chat_id=-100_922_001)

    assert ar.search_artifacts(db, "xyzzy", topic_id=topico)
    assert ar.search_artifacts(db, "xyzzy", topic_id=outro) == []


def test_busca_em_branco_nao_varre_a_tabela(db, topico):
    """Sem a guarda, `%%` casaria com tudo."""
    from bot import artifacts as ar

    assert ar.search_artifacts(db, "   ") == []


def test_busca_sai_da_mais_nova_pra_mais_velha(db, topico):
    from bot import artifacts as ar

    # Mesmo motivo do `_msg`: `saved_artifacts.created_at` também é `now()`, e
    # dentro de uma transação os três nasceriam empatados.
    for minuto, titulo in enumerate(("velha zzq", "media zzq", "nova zzq")):
        novo = ar.save_artifact_from_messages(
            db, topic_id=topico, title=titulo, messages=[{"role": "user", "content": "c"}]
        )
        db.execute(
            "UPDATE saved_artifacts SET created_at = %s WHERE id = %s",
            (f"2026-08-26T10:{minuto:02d}:00+00:00", novo),
        )

    assert [a["title"] for a in ar.search_artifacts(db, "zzq", topic_id=topico)] == [
        "nova zzq", "media zzq", "velha zzq",
    ]


def test_busca_respeita_o_limite(db, topico):
    from bot import artifacts as ar

    for i in range(4):
        ar.save_artifact_from_messages(
            db, topic_id=topico, title=f"lim{i} wwq", messages=[{"role": "user", "content": "c"}]
        )

    assert len(ar.search_artifacts(db, "wwq", topic_id=topico, limit=2)) == 2


# ══ snapshot ══════════════════════════════════════════════════════════════


def test_snapshot_grava_le_e_some_depois_de_consumido(db, topico, sessao):
    from bot import snapshot as sn
    from bot import topic_manager as tm

    tm.insert_message(db, session_id=sessao, topic_id=topico, role="user", content="antes do restart")

    assert sn.save_pending_snapshots(db) >= 1

    meus = [s for s in sn.load_pending_snapshots(db) if s["topic_id"] == topico]
    assert len(meus) == 1
    assert [m["content"] for m in meus[0]["messages"]] == ["antes do restart"]

    sn.drop_snapshot(db, meus[0]["_artifact_id"])

    assert [s for s in sn.load_pending_snapshots(db) if s["topic_id"] == topico] == []


def test_snapshot_de_sessao_sem_mensagem_nao_e_gravado(db, topico, sessao):
    from bot import snapshot as sn

    sn.save_pending_snapshots(db)

    assert [s for s in sn.load_pending_snapshots(db) if s["topic_id"] == topico] == []


def test_cleanup_conta_o_que_apagou(db, topico, sessao, monkeypatch):
    """Depende de `RETURNING id`: um comando sem ele não devolve linha, e a
    contagem seria sempre zero — com o único sintoma sendo um log dizendo
    'limpei 0' pra sempre."""
    from bot import snapshot as sn
    from bot import topic_manager as tm

    tm.insert_message(db, session_id=sessao, topic_id=topico, role="user", content="x")
    sn.save_pending_snapshots(db)

    assert sn.cleanup_expired_snapshots(db) == 0, "nada venceu ainda"

    monkeypatch.setattr(sn, "SNAPSHOT_TTL_MINUTES", -100_000)

    assert sn.cleanup_expired_snapshots(db) >= 1


def test_cleanup_nao_toca_artefato_que_nao_e_snapshot(db, topico, monkeypatch):
    """O filtro é `tags @> [SNAPSHOT_TAG]`. Sem ele, a limpeza levaria junto
    tudo que o operador salvou com `/salvar`."""
    from bot import artifacts as ar
    from bot import snapshot as sn

    guardado = ar.save_artifact_from_messages(
        db, topic_id=topico, title="salvo pelo operador",
        messages=[{"role": "user", "content": "importante"}],
    )
    monkeypatch.setattr(sn, "SNAPSHOT_TTL_MINUTES", -100_000)
    sn.cleanup_expired_snapshots(db)

    assert db.scalar("SELECT title FROM saved_artifacts WHERE id = %s", (guardado,)) == (
        "salvo pelo operador"
    )


# ══ memória imediata ══════════════════════════════════════════════════════


def test_janela_imediata_sai_em_ordem_e_com_carimbo_de_texto(db, topico, sessao):
    """O consumidor que motivou o contrato de tipos da ponte: ele compara
    `created_at` como STRING contra um corte também string. Com `datetime`
    cru, `datetime >= str` levanta `TypeError` e a janela morre inteira."""
    from bot.memory import working_set as ws
    from bot import topic_manager as tm

    for i, texto in enumerate(("um", "dois", "três")):
        _msg(db, sessao, topico, texto, minuto=i)

    janela = ws.get_immediate_messages(db, topico)

    assert [m["content"] for m in janela] == ["um", "dois", "três"]
    assert all(isinstance(m["created_at"], str) for m in janela)


def test_janela_imediata_descarta_resumo_de_sessao_legado(db, topico, sessao):
    from bot.memory import working_set as ws
    from bot import topic_manager as tm

    _msg(db, sessao, topico, "[Resumo da sessão anterior] blá", minuto=0, role="system")
    _msg(db, sessao, topico, "de verdade", minuto=1)

    assert [m["content"] for m in ws.get_immediate_messages(db, topico)] == ["de verdade"]


def test_janela_imediata_de_topico_vazio_e_vazia(db, topico):
    from bot.memory import working_set as ws

    assert ws.get_immediate_messages(db, topico) == []


# ══ Conformidade: roda SEMPRE, com ou sem banco ═══════════════════════════


def test_nenhum_ponto_do_driver_antigo_sobrou_no_runtime():
    """A rede que pega o ponto esquecido.

    Um `.table(` ou um `create_client` que ficasse pra trás não daria erro de
    import — daria erro só no dia em que aquele caminho fosse exercitado, que
    pode ser semanas depois do corte. Esta varredura cobre `bot/` inteiro,
    incluindo os helpers de `bot/bin/` (que não têm extensão `.py` e escapam
    de uma busca por `*.py`).

    A única menção tolerada é a de `bot/config.py`: as linhas deliberadas que
    compõem a mensagem de erro guiada para quem ainda tem o `.env` de antes.
    """
    proibidos = re.compile(r"\.table\(|\.rpc\(|create_client|from supabase|import supabase")
    permitidos = {Path("bot/config.py")}

    achados: list[str] = []
    for caminho in sorted((RAIZ / "bot").rglob("*")):
        if not caminho.is_file() or "__pycache__" in caminho.parts:
            continue
        if caminho.suffix not in ("", ".py"):
            continue
        relativo = caminho.relative_to(RAIZ)
        if relativo in permitidos:
            continue
        try:
            texto = caminho.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, linha in enumerate(texto.splitlines(), start=1):
            if proibidos.search(linha):
                achados.append(f"{relativo}:{n}: {linha.strip()}")

    assert not achados, "ponto do driver antigo sobrou:\n" + "\n".join(achados)
