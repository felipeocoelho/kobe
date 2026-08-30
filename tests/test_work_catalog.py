#!/usr/bin/env python3
"""O catálogo de desenvolvimento — a integridade é do banco, não da disciplina.

Highlander v3, F1. Este arquivo é o **Bloco B** do plano de testes da fase, e o
que ele prova não é "o código não levantou exceção": é que **o banco recusa**
uma linha mal declarada, e recusa mesmo que o código do dispatcher esteja errado,
distraído ou tenha sido reescrito por outra pessoa em outro repositório.

POR QUE ISSO PRECISA DE TESTE DE BANCO DE VERDADE
--------------------------------------------------
A garantia central da F1 é uma restrição de integridade (`system_id NOT NULL`
com chave estrangeira, mais uma FK composta que amarra o subsistema ao sistema).
Restrição de integridade **não existe em teste com dublê** — um `fake_db` que
aceita qualquer coisa passaria verde exatamente no cenário que a fase existe
pra impedir. Ou se testa contra Postgres, ou não se testou.

ISOLAMENTO
----------
Mesmo desenho de `tests/test_db_integration.py`: uma transação por teste,
sempre revertida no teardown. A suíte é repetível (rodar duas vezes dá o mesmo
resultado) e não há um único comando destrutivo aqui.

COMO RODAR
----------
    createdb kobe_test   # uma vez
    .venv/bin/python infra/migrate.py up --database-url postgresql:///kobe_test
    KOBE_TEST_DATABASE_URL=postgresql:///kobe_test .venv/bin/python -m pytest -q \\
        tests/test_work_catalog.py

Sem `KOBE_TEST_DATABASE_URL` os testes de banco são pulados, para que um clone
limpo siga verde. Os testes que leem só o repositório rodam sempre.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_URL = os.getenv("KOBE_TEST_DATABASE_URL", "")


@pytest.fixture
def db():
    """A ponte real, numa transação que sempre volta atrás."""
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
        conn.rollback()
        conn.close()


def _sid() -> str:
    return str(uuid.uuid4())


# ══════════════════════════════════════════════════════════════════════════
# B2 — a integridade é do BANCO
#
# Cada teste aqui tenta gravar uma linha que NÃO PODE existir, indo direto no
# SQL, sem passar pelo `work_catalog`. É de propósito: se a recusa dependesse do
# módulo Python, ela sumiria no dia em que alguém escrevesse na tabela por outro
# caminho — e "outro caminho" é o caso normal, porque um dos dois dispatchers
# vive em repositório separado.
# ══════════════════════════════════════════════════════════════════════════

_INSERT = (
    "INSERT INTO work_sessions (id, system_id, subsystem_id, kind) "
    "VALUES (%s, %s, %s, 'coder')"
)


def test_sessao_sem_sistema_e_recusada_pelo_banco(db):
    """`system_id` nulo: a sala não nasce. É a trava-mãe da fase."""
    import psycopg

    with pytest.raises(psycopg.errors.NotNullViolation):
        db.execute(_INSERT, (_sid(), None, None))


def test_sistema_inexistente_e_recusado_pelo_banco(db):
    """Um uuid que não está em `work_systems` não vira sessão.

    É o que impede um erro de digitação do agente de virar um sistema fantasma:
    ele não consegue inventar um id, e um id inventado não passa.
    """
    import psycopg

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(_INSERT, (_sid(), _sid(), None))


def test_subsistema_de_outro_sistema_e_recusado(db):
    """O par impossível: `system=Flow` com `subsystem=Coder`.

    Este é o teste que justifica a FK composta — a guarda que o esquema escrito
    no briefing não tinha. Os dois campos são individualmente válidos: `Flow` é
    um sistema de verdade, `Coder` é um subsistema de verdade. O que não existe
    é o PAR. Sem a FK composta o banco aceitaria, e o catálogo passaria a
    afirmar que o plugin Coder é subsistema do app Flow.
    """
    import psycopg

    flow = db.one("SELECT id FROM work_systems WHERE slug = 'flow'")
    coder = db.one("SELECT id FROM work_subsystems WHERE slug = 'coder'")
    assert flow and coder, "sementes da migration 006 ausentes"

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(_INSERT, (_sid(), flow["id"], coder["id"]))


def test_o_par_certo_passa(db):
    """A contraprova: `Kobe` + `Coder` entra. Sem isto, o teste acima só
    provaria que a tabela recusa tudo."""
    kobe = db.one("SELECT id FROM work_systems WHERE slug = 'kobe'")
    coder = db.one("SELECT id FROM work_subsystems WHERE slug = 'coder'")
    sid = _sid()
    db.execute(_INSERT, (sid, kobe["id"], coder["id"]))
    assert db.one("SELECT id FROM work_sessions WHERE id = %s", (sid,))


def test_sistema_sem_subsistema_passa(db):
    """`subsystem_id` nulo é legítimo — é o "código do Kobe em si".

    A obrigatoriedade de declarar `none` é do DISPATCH, não do banco: no banco,
    nulo é um valor válido. Quem transforma omissão em recusa é o `work_catalog`,
    e é assim de propósito — o banco guarda fatos, a política mora acima dele.
    """
    kobe = db.one("SELECT id FROM work_systems WHERE slug = 'kobe'")
    sid = _sid()
    db.execute(_INSERT, (sid, kobe["id"], None))
    assert db.one("SELECT subsystem_id FROM work_sessions WHERE id = %s",
                  (sid,))["subsystem_id"] is None


@pytest.mark.parametrize("coluna,valor", [("kind", "outro"), ("status", "zumbi")])
def test_valores_fora_do_dominio_sao_recusados(db, coluna, valor):
    import psycopg

    kobe = db.one("SELECT id FROM work_systems WHERE slug = 'kobe'")
    campos = {"kind": "coder", "status": "running", coluna: valor}
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO work_sessions (id, system_id, kind, status) "
            "VALUES (%s, %s, %s, %s)",
            (_sid(), kobe["id"], campos["kind"], campos["status"]),
        )


def test_artefato_de_tipo_invalido_e_recusado(db):
    import psycopg

    kobe = db.one("SELECT id FROM work_systems WHERE slug = 'kobe'")
    sid = _sid()
    db.execute(_INSERT, (sid, kobe["id"], None))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO work_session_artifacts (session_id, path, kind) "
            "VALUES (%s, 'x', 'planilha')",
            (sid,),
        )


# ══════════════════════════════════════════════════════════════════════════
# A camada de política — `bot/work_catalog.py`
# ══════════════════════════════════════════════════════════════════════════

def test_omissao_de_subsistema_e_recusa_mas_none_e_aceito(db):
    """**Omissão é esquecimento; `none` é decisão.** A assimetria é o §6.3.

    Um catálogo que aceitasse a omissão não teria como distinguir "esta sala não
    tem subsistema" de "ninguém preencheu" — e seis meses depois ninguém saberia
    quais linhas confiar.
    """
    from bot import work_catalog as wc

    with pytest.raises(wc.CatalogRefusal) as e:
        wc.register_session(db, session_id=_sid(), kind="coder",
                            system="Kobe", subsystem="")
    assert e.value.code == "subsistema_nao_declarado"

    out = wc.register_session(db, session_id=_sid(), kind="coder",
                              system="Kobe", subsystem="none")
    assert out["subsystem"] is None and out["created"] is True


def test_sistema_desconhecido_recusa_e_diz_o_que_existe(db):
    """Sistema novo é EVENTO. A recusa tem que ensinar, não só reclamar —
    quem a lê é um agente, e ele precisa saber que a saída é *perguntar ao
    operador*, não tentar outro nome até colar."""
    from bot import work_catalog as wc

    with pytest.raises(wc.CatalogRefusal) as e:
        wc.register_session(db, session_id=_sid(), kind="coder",
                            system="Radar", subsystem="none")
    assert e.value.code == "sistema_desconhecido"
    assert "Kobe" in e.value.hint and "pergunte ao operador" in e.value.hint.lower()


def test_declaracao_aceita_nome_slug_e_caixa_trocada(db):
    """`Kobe`, `kobe`, `KOBE` e `  Kobe  ` são a mesma coisa.

    Quem escreve a declaração é um agente escrevendo prosa, não um programa
    manipulando identificadores. Exigir grafia exata transformaria uma questão
    de maiúscula numa sala que não abre — fricção sem nenhuma integridade em
    troca, porque o slug já é único.
    """
    from bot import work_catalog as wc

    for grafia in ("Kobe", "kobe", "KOBE", "  Kobe  "):
        s = wc.resolve_system(db, grafia)
        assert s["slug"] == "kobe", grafia
    for grafia in ("Coder", "coder", "CODER"):
        sub = wc.resolve_subsystem(db, wc.resolve_system(db, "Kobe"), grafia)
        assert sub["slug"] == "coder", grafia


def test_registro_e_idempotente_por_session_id(db):
    """Reexecutar o dispatcher (`--force`, um start interrompido) não pode
    duplicar linha nem virar erro."""
    from bot import work_catalog as wc

    sid = _sid()
    a = wc.register_session(db, session_id=sid, kind="coder",
                            system="Kobe", subsystem="none")
    b = wc.register_session(db, session_id=sid, kind="coder",
                            system="Kobe", subsystem="none")
    assert a["created"] is True and b["created"] is False
    assert db.scalar("SELECT count(*) FROM work_sessions WHERE id = %s", (sid,)) == 1


def test_cwd_e_metadado_e_nao_influencia_a_declaracao(db):
    """**O caso que prova o desenho inteiro** (§6.1).

    Mesmo `cwd` — o repositório do plugin Coder — para duas sessões que declaram
    sistemas diferentes. O catálogo obedece à declaração, não à pasta. Se um dia
    alguém "melhorar" isto inferindo o sistema do diretório, este teste quebra —
    e ele quebra dizendo por quê.
    """
    from bot import work_catalog as wc

    # Caminho genérico de propósito: o repositório é público, e um caminho de
    # máquina de operador aqui vaza ambiente pra dentro do que é versionado
    # (é o que o `tests/portability_guard.sh` existe pra pegar — e pegou).
    pasta = "/qualquer/raiz/kobe/plugins/public/coder"
    a = wc.register_session(db, session_id=_sid(), kind="coder", cwd=pasta,
                            system="Kobe", subsystem="Coder")
    b = wc.register_session(db, session_id=_sid(), kind="mission", cwd=pasta,
                            system="Flow", subsystem="none")
    assert (a["system"], a["subsystem"]) == ("Kobe", "Coder")
    assert (b["system"], b["subsystem"]) == ("Flow", None)


def test_topico_e_resolvido_pelo_par_chat_thread(db):
    """`thread_id` sozinho NÃO identifica um tópico.

    A restrição real de `topics` é `UNIQUE (telegram_chat_id, telegram_thread_id)`,
    e existe `telegram_thread_id = 2` em dois chats distintos no banco de dev.
    Resolver só pelo thread devolveria o tópico de outra conversa — e a linha do
    catálogo apontaria pra lugar errado, em silêncio.
    """
    from bot import work_catalog as wc

    chat_a, chat_b, thread = -100777001, -100777002, 4242
    ids = {}
    for chat in (chat_a, chat_b):
        ids[chat] = db.execute(
            "INSERT INTO topics (telegram_chat_id, telegram_thread_id, current_name) "
            "VALUES (%s, %s, %s) RETURNING id",
            (chat, thread, f"prova-{chat}"),
        )[0]["id"]

    assert wc.resolve_topic_id(db, chat_a, thread) == ids[chat_a]
    assert wc.resolve_topic_id(db, chat_b, thread) == ids[chat_b]
    assert ids[chat_a] != ids[chat_b]


def test_falta_de_topico_nao_impede_a_sessao_de_nascer(db):
    """O que não pode faltar é o SISTEMA. Tópico é enriquecimento.

    Um dispatch de linha de comando ou de teste não tem tópico, e recusá-lo por
    isso transformaria um metadado ausente numa sala que não abre.
    """
    from bot import work_catalog as wc

    out = wc.register_session(db, session_id=_sid(), kind="coder",
                              system="Kobe", subsystem="none",
                              chat_id=-1, thread_id=999999)
    assert out["topic_id"] is None and out["created"] is True


def test_artefato_e_idempotente_e_recusa_tipo_invalido(db):
    from bot import work_catalog as wc

    sid = _sid()
    wc.register_session(db, session_id=sid, kind="coder",
                        system="Kobe", subsystem="none")
    a = wc.add_artifact(db, session_id=sid, path="p.md", kind="test-report")
    b = wc.add_artifact(db, session_id=sid, path="p.md", kind="test-report")
    assert a["created"] is True and b["created"] is False

    with pytest.raises(wc.CatalogRefusal):
        wc.add_artifact(db, session_id=sid, path="p.md", kind="planilha")


def test_touch_em_sessao_inexistente_nao_e_erro(db):
    """As ~24 salas anteriores à F1 não têm linha. O coletor precisa poder
    colher o transcript delas do mesmo jeito — o catálogo é enriquecimento, e
    o que é perecível se salva primeiro."""
    from bot import work_catalog as wc

    assert wc.touch_session(db, session_id=_sid(), status="idle") is False


def test_fechar_sala_e_rotulo_de_estado(db):
    """E3 do briefing: fechar sala é rótulo, nunca evento de sistema.

    O teste garante o lado observável disso — `close_session` muda `status` e
    `outcome_summary` e mais nada. Se um dia alguém pendurar coleta ou
    destilação aqui, a asserção do transcript intacto quebra.
    """
    from bot import work_catalog as wc

    sid = _sid()
    wc.register_session(db, session_id=sid, kind="coder",
                        system="Kobe", subsystem="none")
    wc.touch_session(db, session_id=sid, transcript_bytes_copied=1234)
    assert wc.close_session(db, session_id=sid, outcome_summary="entregou X") is True

    linha = wc.get_session(db, sid)
    assert linha["status"] == "closed"
    assert linha["outcome_summary"] == "entregou X"
    assert linha["transcript_bytes_copied"] == 1234  # fechar não mexeu na coleta


# ══════════════════════════════════════════════════════════════════════════
# B3 — as consultas que o §6.4 promete
# ══════════════════════════════════════════════════════════════════════════

def test_consultas_do_console_funcionam(db):
    """As consultas que o briefing usa pra justificar o esquema. Se alguma
    precisasse de uma quinta tabela, o desenho estaria errado.

    **Toda consulta aqui é filtrada pelos ids que ESTE teste criou.** A primeira
    versão afirmava sobre a tabela inteira e passou na primeira execução,
    quebrando na segunda por causa de uma linha commitada pelos testes de CLI
    logo abaixo — a mesma armadilha que `tests/test_db_integration.py` documenta
    ("suíte que só passa uma vez não é suíte"). Uma consulta de console de
    verdade também nunca vai rodar contra tabela vazia.
    """
    from bot import work_catalog as wc

    a, b, c = _sid(), _sid(), _sid()
    meus = [a, b, c]
    wc.register_session(db, session_id=a, kind="coder", system="Kobe",
                        subsystem="Coder", title="gate do plano",
                        motivation="o gate não segurava edição")
    wc.register_session(db, session_id=b, kind="coder", system="Kobe",
                        subsystem="none", title="README")
    wc.register_session(db, session_id=c, kind="mission", system="Flow",
                        subsystem="none", title="kanban")

    # "todas as sessões de código de PLUGINS do Kobe" — o subsistema não-nulo é
    # o que separa plugin de core, e é por isso que ele existe.
    plugins = db.query(
        "SELECT s.title, sub.name AS sub, s.started_at, s.outcome_summary "
        "  FROM work_sessions s "
        "  JOIN work_systems sys ON sys.id = s.system_id "
        "  LEFT JOIN work_subsystems sub ON sub.id = s.subsystem_id "
        " WHERE sys.name = 'Kobe' AND sub.id IS NOT NULL AND s.id = ANY(%s) "
        " ORDER BY s.started_at",
        (meus,),
    )
    assert [(p["title"], p["sub"]) for p in plugins] == [("gate do plano", "Coder")]

    # "tudo que já foi feito no sistema Flow" — e o plugin Flow NÃO aparece aqui,
    # que é o caso em que a inferência por diretório erraria.
    flow = db.query(
        "SELECT s.title FROM work_sessions s "
        "  JOIN work_systems sys ON sys.id = s.system_id "
        " WHERE sys.name = 'Flow' AND s.id = ANY(%s)",
        (meus,),
    )
    assert [f["title"] for f in flow] == ["kanban"]

    # "qual era a motivação daquela sessão"
    assert db.scalar("SELECT motivation FROM work_sessions WHERE id = %s",
                     (a,)) == "o gate não segurava edição"

    # "quantas sessões o plugin Coder consumiu"
    assert db.scalar(
        "SELECT count(*) FROM work_sessions s "
        "  JOIN work_subsystems sub ON sub.id = s.subsystem_id "
        " WHERE sub.slug = 'coder' AND s.id = ANY(%s)",
        (meus,),
    ) == 1


# ══════════════════════════════════════════════════════════════════════════
# Conformidade — lê o repositório, roda sempre (sem banco)
# ══════════════════════════════════════════════════════════════════════════

def test_catalogo_nasce_desligado():
    """A chave é o rollback nomeado no briefing, e ela tem que nascer OFF.

    Se `WORK_CATALOG_ENABLED` tivesse default ligado, o código novo dos
    dispatchers chegando num ambiente sem a migration 006 derrubaria TODA
    abertura de sala — o pior modo de falha desta fase, e um que se previne com
    um default.
    """
    from bot import work_catalog as wc

    anterior = os.environ.pop("WORK_CATALOG_ENABLED", None)
    try:
        # Sem a env, o módulo cai no `.env` da instalação; o que se afirma aqui
        # é só que "ligado" exige um valor afirmativo explícito.
        assert wc._env("WORK_CATALOG_ENABLED_INEXISTENTE_" + uuid.uuid4().hex) == ""
        os.environ["WORK_CATALOG_ENABLED"] = "false"
        assert wc.catalog_enabled() is False
        os.environ["WORK_CATALOG_ENABLED"] = "true"
        assert wc.catalog_enabled() is True
    finally:
        os.environ.pop("WORK_CATALOG_ENABLED", None)
        if anterior is not None:
            os.environ["WORK_CATALOG_ENABLED"] = anterior


# ══════════════════════════════════════════════════════════════════════════
# O CONTRATO DE SAÍDA DO CLI — é ele que os dispatchers leem
#
# Os dois dispatchers não importam `work_catalog`: eles executam
# `bot/bin/kobe-work-session` e leem o **código de saída** e o JSON. Então o que
# precisa estar preso por teste é isso, e não só a função Python — um refactor
# que trocasse os códigos de saída passaria por toda a suíte acima e quebraria
# a abertura de sala em produção, longe da causa.
#
# Estes testes rodam subprocess, então NÃO participam da transação revertida do
# fixture `db`. Em vez de limpar depois — o que exigiria apagar dado, coisa que
# este arquivo não faz em lugar nenhum —, cada um usa um `session_id` novo. O
# resíduo é uma linha por execução num banco que existe só pra teste, e a suíte
# continua repetível: rodar duas vezes dá o mesmo resultado.
# ══════════════════════════════════════════════════════════════════════════

def _cli(*args, **env_extra):
    """Executa o helper como os dispatchers executam: subprocess + JSON + exit."""
    import json
    import subprocess

    if not _URL:
        pytest.skip("KOBE_TEST_DATABASE_URL não definida")

    env = dict(os.environ)
    env["WORK_CATALOG_ENABLED"] = env_extra.pop("enabled", "true")
    env["DATABASE_URL"] = _URL
    # O helper herda chat/thread do ambiente; num teste isso apontaria pro
    # tópico real de quem estiver rodando. Fora.
    env.pop("KOBE_CHAT_ID", None)
    env.pop("KOBE_THREAD_ID", None)
    env.update(env_extra)

    proc = subprocess.run(
        [sys.executable, str(RAIZ / "bot" / "bin" / "kobe-work-session"), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )
    try:
        payload = json.loads(proc.stdout)
    except Exception:  # noqa: BLE001 — saída não-JSON é falha, e o teste mostra
        payload = {"_stdout": proc.stdout, "_stderr": proc.stderr}
    return proc.returncode, payload


def test_cli_chave_desligada_sai_4_e_nao_e_erro():
    """Exit 4 quer dizer "siga sem registrar", não "aborte".

    É o estado de rollback: com a F1 desligada os dispatchers voltam a abrir sala
    sem declaração, como antes. Se o dispatcher tratasse 4 como falha, desligar a
    chave deixaria de ser rollback e viraria uma parada total — o oposto do que
    um rollback tem que ser.
    """
    code, out = _cli("systems", enabled="false")
    assert code == 4
    assert out["disabled"] is True and out["ok"] is False


def test_cli_recusa_sem_sistema_sai_2():
    code, out = _cli("register", "--session-id", _sid(), "--kind", "coder",
                     "--subsystem", "none")
    assert code == 2
    assert out["code"] == "sistema_nao_declarado"
    assert "NENHUMA sala foi aberta" in out["note"]


def test_cli_recusa_sistema_desconhecido_sai_2():
    code, out = _cli("register", "--session-id", _sid(), "--kind", "coder",
                     "--system", "Radar", "--subsystem", "none")
    assert code == 2 and out["code"] == "sistema_desconhecido"


def test_cli_recusa_subsistema_omitido_sai_2():
    code, out = _cli("register", "--session-id", _sid(), "--kind", "coder",
                     "--system", "Kobe")
    assert code == 2 and out["code"] == "subsistema_nao_declarado"


def test_cli_recusa_par_impossivel_sai_2():
    code, out = _cli("register", "--session-id", _sid(), "--kind", "coder",
                     "--system", "Flow", "--subsystem", "Coder")
    assert code == 2 and out["code"] == "subsistema_desconhecido"


def test_cli_falha_de_instrumento_sai_3_e_se_distingue_da_recusa():
    """**A distinção que este helper existe pra fazer.**

    Banco inalcançável não é "você não declarou o sistema". As duas pedem
    reações opostas do agente que lê a saída, e confundi-las o faria inventar um
    sistema pra satisfazer um erro que não era sobre sistema nenhum. Mesma lição
    do conserto do `kobe-reflect` de 29/08/2026, onde um timeout era impresso com
    a frase de "não há registro".
    """
    code, out = _cli("register", "--session-id", _sid(), "--kind", "coder",
                     "--system", "Kobe", "--subsystem", "none",
                     DATABASE_URL="postgresql://ninguem@127.0.0.1:1/inexistente")
    assert code == 3
    assert out["unavailable"] is True
    assert "FALHA DE INSTRUMENTO" in out["note"]
    assert "NENHUMA sala foi aberta" in out["note"]


def test_cli_caminho_feliz_registra_e_e_idempotente():
    sid = _sid()

    code, out = _cli("register", "--session-id", sid, "--kind", "coder",
                     "--system", "Kobe", "--subsystem", "Coder",
                     "--title", "prova do contrato do CLI",
                     "--motivation", "provar exit 0 e idempotência",
                     "--cwd", "/tmp/qualquer")
    assert code == 0 and out["created"] is True
    assert (out["system"], out["subsystem"]) == ("Kobe", "Coder")

    code2, out2 = _cli("register", "--session-id", sid, "--kind", "coder",
                       "--system", "Kobe", "--subsystem", "Coder")
    assert code2 == 0 and out2["created"] is False

    code3, out3 = _cli("show", "--session-id", sid)
    assert code3 == 0
    assert out3["session"]["title"] == "prova do contrato do CLI"
    assert out3["session"]["system_name"] == "Kobe"
