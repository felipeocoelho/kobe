#!/usr/bin/env python3
"""Testes da resiliência de conexão do banco — bot/db.py (camada 1 do bug 2).

Repro do incidente real (3× em 30 dias de produção, o último em 19/08 17:08):
o operador volta depois de um tempo parado, manda uma mensagem, e a primeira
consulta ao banco leva `httpx.RemoteProtocolError: Server disconnected` — a
conexão ociosa foi derrubada do outro lado e ninguém tentava de novo.

O que estas travas protegem:
- erro de TRANSPORTE → recicla o cliente e tenta de novo (o conserto);
- erro de NEGÓCIO → NÃO repete (repetir mascararia bug de verdade);
- a consulta é REMONTADA no cliente novo (senão o retry usaria o pool morto);
- ociosidade → recicla ANTES de tentar (a prevenção que ataca a causa raiz);
- flag off → passe-livre, zero reciclagem e zero retry (rollback trivial);
- escrita respeita `DB_RETRY_WRITES` (o trade-off declarado no plano).

Rodar: .venv/bin/python -m pytest tests/test_db_resilience.py -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import db as dbmod


# ── Banco fake: registra a cadeia montada e pode falhar sob demanda ────────


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeBuilder:
    def __init__(self, client: "_FakeClient", chain: list[str]):
        self._client, self._chain = client, chain

    def _step(self, name):
        def _call(*args, **kwargs):
            return _FakeBuilder(self._client, self._chain + [name])

        return _call

    def __getattr__(self, name):
        return self._step(name)

    def execute(self):
        self._client.executed.append((self._client.generation, list(self._chain)))
        exc = self._client.pop_failure()
        if exc is not None:
            raise exc
        return _FakeResult([{"ok": True}])


class _FakeClient:
    """Um 'cliente de banco' por geração. `failures` é consumida por execute()."""

    def __init__(self, generation: int, shared: dict):
        self.generation, self._shared = generation, shared

    @property
    def executed(self):
        return self._shared["executed"]

    def pop_failure(self):
        fails = self._shared["failures"]
        return fails.pop(0) if fails else None

    def table(self, name):
        return _FakeBuilder(self, ["table"])

    def rpc(self, *a, **k):
        return _FakeBuilder(self, ["rpc"])


def _resilient(failures=None):
    """Devolve (ResilientClient, estado compartilhado). `built` conta reciclagens."""
    shared = {"executed": [], "failures": list(failures or []), "built": 0}

    def factory():
        shared["built"] += 1
        return _FakeClient(shared["built"], shared)

    return dbmod.ResilientClient(factory), shared


def _env(**over):
    base = {
        "DB_RESILIENCE_ENABLED": "true",
        "DB_RETRY_WRITES": "true",
        "DB_IDLE_RECYCLE_SECONDS": "999999",  # desliga a prevenção por default
    }
    base.update(over)
    return mock.patch.dict(os.environ, base, clear=False)


# ── Travas ────────────────────────────────────────────────────────────────


def test_leitura_normal_passa_sem_ruido() -> None:
    client, shared = _resilient()
    with _env():
        res = client.table("messages").select("*").eq("topic_id", "t1").execute()
    assert res.data == [{"ok": True}]
    assert shared["built"] == 1, "sem falha, não recicla"
    assert len(shared["executed"]) == 1


def test_repro_server_disconnected_recicla_e_reexecuta() -> None:
    """O incidente real: 1ª tentativa morre com Server disconnected, a 2ª passa."""
    client, shared = _resilient(
        failures=[httpx.RemoteProtocolError("Server disconnected")]
    )
    with _env():
        res = client.table("messages").select("*").execute()
    assert res.data == [{"ok": True}], "o turno sobrevive — era o que faltava"
    assert shared["built"] == 2, "o cliente foi reciclado uma vez"
    gens = [g for g, _ in shared["executed"]]
    assert gens == [1, 2], "a repetição rodou no cliente NOVO, não no pool morto"


def test_consulta_e_remontada_igual_no_cliente_novo() -> None:
    """Sem remontar a cadeia, o retry rodaria uma consulta diferente (ou morta)."""
    client, shared = _resilient(failures=[httpx.ReadError("boom")])
    with _env():
        client.table("messages").select("a").eq("x", 1).order("y").limit(5).execute()
    cadeias = [c for _, c in shared["executed"]]
    assert cadeias[0] == cadeias[1] == ["table", "select", "eq", "order", "limit"]


def test_desiste_apos_o_teto_e_propaga() -> None:
    """Banco fora de verdade: propaga pra camada 2 avisar o operador."""
    client, shared = _resilient(failures=[httpx.ReadError("x")] * 5)
    with _env():
        try:
            client.table("messages").select("*").execute()
        except httpx.ReadError:
            pass
        else:
            raise AssertionError("deveria propagar depois de esgotar as tentativas")
    assert len(shared["executed"]) == dbmod._MAX_RETRIES + 1


def test_erro_de_negocio_nao_e_repetido() -> None:
    """Dado inválido/permissão: repetir não adianta e mascararia bug."""
    client, shared = _resilient(failures=[ValueError("coluna inexistente")])
    with _env():
        try:
            client.table("messages").select("*").execute()
        except ValueError:
            pass
        else:
            raise AssertionError("erro de negócio deve propagar de cara")
    assert len(shared["executed"]) == 1, "não pode ter repetido"
    assert shared["built"] == 1, "não pode ter reciclado"


def test_reciclagem_por_ociosidade_acontece_antes_de_falhar() -> None:
    """A prevenção: voltou depois de um tempo parado → troca o pool ANTES."""
    client, shared = _resilient()
    with _env(DB_IDLE_RECYCLE_SECONDS="0"):
        client.table("messages").select("*").execute()
    assert shared["built"] >= 2, "reciclou preventivamente, sem erro nenhum"
    assert shared["executed"], "e a consulta rodou normalmente"


def test_escrita_repete_por_padrao() -> None:
    client, shared = _resilient(failures=[httpx.RemoteProtocolError("Server disconnected")])
    with _env():
        client.table("messages").insert({"a": 1}).execute()
    assert len(shared["executed"]) == 2


def test_escrita_nao_repete_com_flag_desligada() -> None:
    """DB_RETRY_WRITES=false: o operador escolhe nunca arriscar linha duplicada."""
    client, shared = _resilient(failures=[httpx.RemoteProtocolError("Server disconnected")])
    with _env(DB_RETRY_WRITES="false"):
        try:
            client.table("messages").insert({"a": 1}).execute()
        except httpx.RemoteProtocolError:
            pass
        else:
            raise AssertionError("com a flag off a escrita deve propagar")
    assert len(shared["executed"]) == 1
    # E a leitura continua repetindo — a flag é só sobre escrita.
    client2, shared2 = _resilient(failures=[httpx.ReadError("x")])
    with _env(DB_RETRY_WRITES="false"):
        client2.table("messages").select("*").execute()
    assert len(shared2["executed"]) == 2


def test_flag_off_e_passe_livre() -> None:
    """Rollback trivial: DB_RESILIENCE_ENABLED=false = comportamento legado."""
    client, shared = _resilient(failures=[httpx.ReadError("x")])
    with _env(DB_RESILIENCE_ENABLED="false", DB_IDLE_RECYCLE_SECONDS="0"):
        try:
            client.table("messages").select("*").execute()
        except httpx.ReadError:
            pass
        else:
            raise AssertionError("sem resiliência, o erro passa direto")
    assert len(shared["executed"]) == 1
    assert shared["built"] == 1, "nem reciclagem preventiva"


def test_identidade_do_wrapper_e_estavel() -> None:
    """O objeto `db` é passado adiante por todo o código: quem troca é o INTERNO."""
    client, shared = _resilient(failures=[httpx.ReadError("x")])
    with _env():
        client.table("messages").select("*").execute()
    assert shared["built"] == 2
    assert isinstance(client, dbmod.ResilientClient), "o wrapper continua o mesmo"


def test_kobe_so_usa_table_e_rpc() -> None:
    """Trava do isolamento de driver: se alguém passar a usar auth/storage, o
    wrapper deixa de cobrir a chamada — melhor descobrir aqui do que em produção."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        ["grep", "-rn", r"db\.\(auth\|storage\|functions\)", "bot/"],
        cwd=root, capture_output=True, text=True,
    )
    assert out.returncode != 0 or not out.stdout.strip(), (
        f"uso de API do banco fora de table()/rpc():\n{out.stdout}"
    )


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ok   {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {t.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERR  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passaram")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
