#!/usr/bin/env python3
"""A ponte pro Postgres — contrato de tipos e resiliência de conexão (bot/db.py).

DUAS COISAS SÃO TRAVADAS AQUI, E A PRIMEIRA É A MAIS PERIGOSA.

**1. O contrato de tipos da fronteira.** O PostgREST devolvia JSON: `uuid`
chegava como string e `timestamptz` como texto ISO. O psycopg devolve `UUID` e
`datetime`. A diferença não quebra nada *aqui* — quebra longe, em silêncio:
`bot/memory/working_set.py` compara `created_at` **como string** contra um corte
também string, e `datetime >= str` levanta `TypeError`. A janela imediata de
memória morre inteira, e a causa fica a seis arquivos de distância. Por isso o
formato é **pinado**, não só "convertido".

**2. A política de repetição.** Herdada da ponte anterior, com a mesma semântica
que o operador aprovou:
- erro de TRANSPORTE → repete (o conserto de 3 sumiços em 30 dias de produção);
- erro de NEGÓCIO → NÃO repete (repetir mascararia bug de verdade);
- LEITURA repete sempre; ESCRITA obedece a `DB_RETRY_WRITES` (o trade-off);
- `DB_RESILIENCE_ENABLED=false` → uma tentativa e ponto (rollback trivial).

O que mudou foi só o mecanismo. Na ponte anterior, "isto é escrita?" era
adivinhado farejando a cadeia `.table().insert()`; agora é o verbo que quem
escreveu escolheu — `execute` é escrita, `query`/`one`/`scalar` são leitura.

Rodar: .venv/bin/python -m pytest tests/test_db_resilience.py -q
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from uuid import UUID

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import db as dbmod


# ── Ponte sem pool de verdade ─────────────────────────────────────────────


class _Ponte(dbmod.KobeDB):
    """`KobeDB` com o I/O trocado por um roteiro: nenhuma conexão é aberta.

    `roteiro` é a lista do que cada tentativa faz — uma exceção para levantar,
    ou uma lista de linhas para devolver.
    """

    def __init__(self, roteiro: list) -> None:
        self.roteiro = list(roteiro)
        self.chamadas: list[tuple[str, tuple]] = []
        # De propósito NÃO chama super().__init__: construir um ConnectionPool
        # tentaria abrir conexão de verdade.

    def _attempt(self, sql, params):
        self.chamadas.append((sql, tuple(params) if params else ()))
        passo = self.roteiro.pop(0) if self.roteiro else []
        if isinstance(passo, BaseException):
            raise passo
        return passo


def _linhas(n: int = 1) -> list[dict]:
    return [{"ok": True} for _ in range(n)]


def _transporte(msg: str = "server closed the connection unexpectedly"):
    return psycopg.OperationalError(msg)


def _negocio(msg: str = "violates check constraint"):
    return psycopg.errors.CheckViolation(msg)


@pytest.fixture(autouse=True)
def _env_limpo(monkeypatch):
    """Cada teste parte dos defaults, não do `.env` de quem rodou."""
    for chave in ("DB_RESILIENCE_ENABLED", "DB_RETRY_WRITES", "DB_IDLE_RECYCLE_SECONDS"):
        monkeypatch.delenv(chave, raising=False)
    # Sem espera de verdade: o backoff não é o que está sob teste.
    monkeypatch.setattr(dbmod.time, "sleep", lambda _s: None)


# ══ 1. O CONTRATO DE TIPOS ════════════════════════════════════════════════


def test_uuid_vira_texto():
    """O código carrega ids adiante como chave de dicionário e em f-string."""
    assert dbmod._normalize(UUID("b65278f6-714d-4123-9f43-c8f833a14916")) == (
        "b65278f6-714d-4123-9f43-c8f833a14916"
    )


def test_datetime_vira_iso_com_deslocamento_explicito():
    """A forma exata que o PostgREST devolvia — e que os parsers do Kobe
    esperam. Este é o teste que pina o formato."""
    dt = datetime(2026, 8, 26, 3, 46, 25, 275243, tzinfo=timezone.utc)
    assert dbmod._normalize(dt) == "2026-08-26T03:46:25.275243+00:00"


def test_o_iso_produzido_e_relido_pelos_parsers_do_kobe():
    """Fecha o círculo: o texto que a ponte devolve tem que voltar a ser
    `datetime` pelos parsers que já existem, senão a memória degrada calada."""
    from bot.memory.aging import parse_ts

    dt = datetime(2026, 8, 26, 3, 46, 25, 275243, tzinfo=timezone.utc)
    assert parse_ts(dbmod._normalize(dt)) == dt


def test_created_at_normalizado_e_comparavel_como_string():
    """A quebra concreta que motivou tudo isto: `working_set` filtra a janela
    com `created_at >= cutoff`, e `cutoff` é STRING. Com `datetime` cru,
    `datetime >= str` levanta TypeError."""
    cedo = dbmod._normalize(datetime(2026, 8, 26, 1, 0, tzinfo=timezone.utc))
    tarde = dbmod._normalize(datetime(2026, 8, 26, 3, 0, tzinfo=timezone.utc))

    assert isinstance(cedo, str) and isinstance(tarde, str)
    assert tarde >= cedo  # comparação de string, sem TypeError


def test_a_ordem_lexicografica_do_iso_bate_com_a_cronologica():
    """`working_set` depende disso: ele ordena texto e espera ordem de tempo."""
    momentos = [
        datetime(2026, 8, 26, h, m, tzinfo=timezone.utc)
        for h, m in ((1, 0), (1, 30), (3, 0), (23, 59))
    ]
    textos = [dbmod._normalize(m) for m in momentos]
    assert textos == sorted(textos)


def test_lista_e_dicionario_passam_intactos():
    """`text[]` (saved_artifacts.tags) e `jsonb` já chegam nativos do psycopg —
    convertê-los seria estragar o que já estava certo."""
    assert dbmod._normalize(["snapshot", "auto"]) == ["snapshot", "auto"]
    assert dbmod._normalize({"a": 1}) == {"a": 1}


def test_none_e_tipos_simples_passam_intactos():
    for valor in (None, 42, "texto", True, 3.5):
        assert dbmod._normalize(valor) is valor or dbmod._normalize(valor) == valor


def test_a_linha_inteira_e_normalizada():
    linha = {
        "id": UUID("b65278f6-714d-4123-9f43-c8f833a14916"),
        "created_at": datetime(2026, 8, 26, 3, 46, 25, tzinfo=timezone.utc),
        "role": "assistant",
        "tags": ["x"],
    }
    saida = dbmod._normalize_row(linha)
    assert saida == {
        "id": "b65278f6-714d-4123-9f43-c8f833a14916",
        "created_at": "2026-08-26T03:46:25+00:00",
        "role": "assistant",
        "tags": ["x"],
    }


# ══ 2. OS QUATRO VERBOS ═══════════════════════════════════════════════════


def test_query_devolve_todas_as_linhas():
    ponte = _Ponte([_linhas(3)])
    assert len(ponte.query("SELECT 1")) == 3


def test_one_devolve_a_primeira_linha():
    ponte = _Ponte([[{"id": "a"}, {"id": "b"}]])
    assert ponte.one("SELECT 1") == {"id": "a"}


def test_one_devolve_none_sem_linha():
    assert _Ponte([[]]).one("SELECT 1") is None


def test_scalar_devolve_o_primeiro_valor():
    assert _Ponte([[{"count": 7}]]).scalar("SELECT count(*) FROM x") == 7


def test_scalar_devolve_none_sem_linha():
    assert _Ponte([[]]).scalar("SELECT count(*) FROM x") is None


def test_execute_devolve_o_returning():
    ponte = _Ponte([[{"id": "novo"}]])
    assert ponte.execute("INSERT INTO x DEFAULT VALUES RETURNING id") == [{"id": "novo"}]


def test_execute_sem_returning_devolve_lista_vazia():
    assert _Ponte([[]]).execute("UPDATE x SET y = 1") == []


def test_parametros_chegam_como_tupla():
    """Interpolação de parâmetro é o caminho de injeção de SQL — os valores
    têm que ir ligados, nunca concatenados."""
    ponte = _Ponte([_linhas()])
    ponte.query("SELECT * FROM t WHERE id = %s AND n > %s", ["abc", 3])
    assert ponte.chamadas[0] == ("SELECT * FROM t WHERE id = %s AND n > %s", ("abc", 3))


# ══ 3. A POLÍTICA DE REPETIÇÃO ════════════════════════════════════════════


def test_leitura_normal_passa_sem_ruido():
    ponte = _Ponte([_linhas()])
    assert ponte.query("SELECT 1") == _linhas()
    assert len(ponte.chamadas) == 1


def test_repro_conexao_derrubada_reconecta_e_reexecuta():
    """O incidente real: 3× em 30 dias, o operador volta depois de um tempo
    parado e a primeira consulta morre porque o socket ocioso caiu."""
    ponte = _Ponte([_transporte(), _linhas()])

    assert ponte.query("SELECT 1") == _linhas()
    assert len(ponte.chamadas) == 2, "não tentou de novo"


def test_a_consulta_repetida_e_identica():
    """Na ponte anterior isto exigia gravar e remontar a cadeia de chamadas.
    Com `(sql, params)` é de graça — mas segue travado, porque repetir uma
    consulta DIFERENTE seria pior que não repetir."""
    ponte = _Ponte([_transporte(), _linhas()])
    ponte.query("SELECT * FROM messages WHERE topic_id = %s", ["t1"])

    assert ponte.chamadas[0] == ponte.chamadas[1]


def test_desiste_apos_o_teto_e_propaga():
    """Insistir além disso só atrasa o aviso ao operador."""
    ponte = _Ponte([_transporte(), _transporte(), _transporte(), _transporte()])

    with pytest.raises(psycopg.OperationalError):
        ponte.query("SELECT 1")

    assert len(ponte.chamadas) == dbmod._MAX_RETRIES + 1


def test_erro_de_negocio_nao_e_repetido():
    """Constraint violada não melhora na segunda tentativa — repetir só
    mascararia o bug e gastaria tempo."""
    ponte = _Ponte([_negocio(), _linhas()])

    with pytest.raises(psycopg.errors.CheckViolation):
        ponte.query("SELECT 1")

    assert len(ponte.chamadas) == 1


def test_escrita_repete_por_padrao():
    ponte = _Ponte([_transporte(), _linhas()])
    ponte.execute("INSERT INTO messages (id) VALUES (%s)", ["x"])
    assert len(ponte.chamadas) == 2


def test_escrita_nao_repete_com_a_flag_desligada(monkeypatch):
    """O outro lado do trade-off: quem prefere perder a escrita a arriscar uma
    linha duplicada desliga aqui."""
    monkeypatch.setenv("DB_RETRY_WRITES", "false")
    ponte = _Ponte([_transporte(), _linhas()])

    with pytest.raises(psycopg.OperationalError):
        ponte.execute("INSERT INTO messages (id) VALUES (%s)", ["x"])

    assert len(ponte.chamadas) == 1


def test_leitura_repete_mesmo_com_retry_writes_desligado(monkeypatch):
    """`DB_RETRY_WRITES` governa só escrita. Repetir leitura é seguro sempre."""
    monkeypatch.setenv("DB_RETRY_WRITES", "false")
    ponte = _Ponte([_transporte(), _linhas()])

    assert ponte.query("SELECT 1") == _linhas()
    assert len(ponte.chamadas) == 2


def test_flag_off_e_uma_tentativa_e_ponto(monkeypatch):
    """Rollback trivial: com a camada desligada, o erro sobe na hora."""
    monkeypatch.setenv("DB_RESILIENCE_ENABLED", "false")
    ponte = _Ponte([_transporte(), _linhas()])

    with pytest.raises(psycopg.OperationalError):
        ponte.query("SELECT 1")

    assert len(ponte.chamadas) == 1


def test_flag_off_nao_atrapalha_o_caminho_feliz(monkeypatch):
    monkeypatch.setenv("DB_RESILIENCE_ENABLED", "false")
    assert _Ponte([_linhas()]).query("SELECT 1") == _linhas()


def test_o_verbo_e_que_decide_se_e_escrita(monkeypatch):
    """A prova de que a classificação melhorou: na ponte anterior isto era
    adivinhado farejando a cadeia. Um SELECT com a palavra "insert" no texto
    continua sendo leitura, porque quem escreveu chamou `query`."""
    monkeypatch.setenv("DB_RETRY_WRITES", "false")
    ponte = _Ponte([_transporte(), _linhas()])

    ponte.query("SELECT 'insert' AS palavra")  # não levanta: é leitura

    assert len(ponte.chamadas) == 2


# ══ 4. Configuração e conformidade ════════════════════════════════════════


def test_ociosidade_padrao_e_dois_minutos():
    assert dbmod._idle_recycle_seconds() == 120.0


def test_ociosidade_ilegivel_cai_no_padrao(monkeypatch):
    """Um `.env` com lixo não pode derrubar o bot no start."""
    monkeypatch.setenv("DB_IDLE_RECYCLE_SECONDS", "dois minutos")
    assert dbmod._idle_recycle_seconds() == 120.0


def test_transport_errors_cobre_a_familia_do_psycopg():
    """`bot/turn_guarantee.py` pergunta a este módulo se vale re-tentar. Se a
    lista esvaziar, o turno volta a morrer calado."""
    assert psycopg.OperationalError in dbmod.TRANSPORT_ERRORS
    assert dbmod.TRANSPORT_ERRORS


def test_erro_de_negocio_nao_conta_como_transporte():
    assert not isinstance(_negocio(), dbmod.TRANSPORT_ERRORS)


def test_repr_nao_vaza_a_conninfo():
    """A string de conexão pode carregar senha. Ela não pode acabar num log
    por causa de um `%r` distraído."""
    ponte = _Ponte([])
    ponte._pool = mock.Mock(name="pool")
    ponte._pool.name = "kobe"
    assert "senha" not in repr(ponte)
    assert "postgresql" not in repr(ponte)


def test_build_client_usa_a_database_url_da_config():
    cfg = mock.Mock()
    cfg.database_url = "postgresql:///kobe_fake"

    with mock.patch.object(dbmod, "ConnectionPool") as pool:
        ponte = dbmod.build_client(cfg)

    assert isinstance(ponte, dbmod.KobeDB)
    assert pool.call_args.kwargs["conninfo"] == "postgresql:///kobe_fake"


def test_a_conexao_fixa_o_fuso_em_utc():
    """O cluster do Ubuntu fica no fuso local da máquina e todo banco criado
    nele herda isso. Sem fixar aqui, o mesmo instante sairia como
    `...T00:46:25-03:00` em vez de `...T03:46:25+00:00`, e o Kobe compara
    `created_at` como string."""
    cfg = mock.Mock()
    cfg.database_url = "postgresql:///kobe_fake"

    with mock.patch.object(dbmod, "ConnectionPool") as pool:
        dbmod.build_client(cfg)

    assert "TimeZone=UTC" in pool.call_args.kwargs["kwargs"]["options"]


def test_a_conexao_pede_linhas_como_dicionario():
    cfg = mock.Mock()
    cfg.database_url = "postgresql:///kobe_fake"

    with mock.patch.object(dbmod, "ConnectionPool") as pool:
        dbmod.build_client(cfg)

    from psycopg.rows import dict_row

    assert pool.call_args.kwargs["kwargs"]["row_factory"] is dict_row
