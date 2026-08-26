#!/usr/bin/env python3
"""Cria o banco do Kobe se ele ainda nao existir — com os parametros certos.

A DIRETRIZ QUE ISTO ATENDE
--------------------------
O instalador **nao parte do principio de que existe um banco em algum lugar**.
Ele descobre a situacao, cria o que faltar, e executa o DDL em tempo de
execucao. Nada de "crie o banco antes" nem de "cole este SQL num painel".

Este arquivo cobre o degrau mais baixo e mais comum dessa diretriz: **o banco
do `DATABASE_URL` nao existe ainda**. O resto do alvo — detectar e escolher
entre clusters, subir um cluster dedicado, criar role proprio, instalar o
proprio PostgreSQL — esta declarado em `FRONTEIRA` no fim deste modulo e e
escopo da sessao do instalador publico.

POR QUE OS PARAMETROS DE CRIACAO IMPORTAM (e por que este arquivo os le da
referencia do portao, em vez de cravar)
--------------------------------------------------------------------------
Um banco criado com o default do `initdb` **nasce divergente**, e de dois
jeitos que ninguem percebe no dia:

- **Collation.** O `initdb` do Ubuntu cria em `C.UTF-8`, que ordena por byte
  cru. O dado e o mesmo; a ORDEM de saida de `ORDER BY <texto>` muda — acento e
  maiuscula caem em lugar diferente. Ha caso vivo no Kobe: a lista de contatos
  e ordenada por nome. E collation **nao se troca depois** sem recriar o banco.
- **Fuso.** Todo banco herda o `TimeZone` do cluster, e o cluster fica no fuso
  local da maquina. `timestamptz` guarda o mesmo instante, mas o TEXTO que o
  driver devolve muda de `+00:00` pro deslocamento local — e o Kobe compara
  `created_at` como string em pelo menos um caminho.

Criar errado aqui significaria toda instalacao nova acender o portao de
compatibilidade no primeiro dia. Por isso os parametros saem de
`tests/fixtures/schema_expected.json` — a MESMA referencia que
`infra/compat_gate.py` usa pra julgar. Uma fonte, dois consumidores: nao ha
como o criador e o juiz discordarem.

USO
---
    python infra/provision_db.py --database-url postgresql:///kobe
    python infra/provision_db.py --database-url ... --dry-run

Idempotente: se o banco ja existe, nao faz nada e diz isso. Sai 0 quando o
banco esta pronto pra receber o schema, != 0 quando precisa de gente.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

_INFRA_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _INFRA_DIR.parent

REFERENCE = _PROJECT_ROOT / "tests" / "fixtures" / "schema_expected.json"

# Usados so se a referencia nao puder ser lida. Sao os valores da referencia de
# hoje; ficam aqui pra que uma instalacao sem o arquivo (copia parcial, por
# exemplo) ainda crie um banco sao, em vez de cair no default do initdb.
FALLBACK = {"encoding": "UTF8", "collate": "en_US.UTF-8", "ctype": "en_US.UTF-8",
            "timezone": "UTC"}


class ProvisionError(RuntimeError):
    """Falha que exige gente — nunca e contornada em silencio."""


def parametros_de_criacao(referencia: Path = REFERENCE) -> dict:
    """Encoding, collation, ctype e fuso que um banco novo deve ter."""
    try:
        db = json.loads(referencia.read_text(encoding="utf-8"))["database"]
    except (OSError, ValueError, KeyError):
        return dict(FALLBACK)
    return {
        "encoding": db.get("encoding") or FALLBACK["encoding"],
        "collate": db.get("collate") or FALLBACK["collate"],
        "ctype": db.get("ctype") or FALLBACK["ctype"],
        "timezone": db.get("timezone") or FALLBACK["timezone"],
    }


def _conninfo(database_url: str):
    import psycopg

    try:
        return psycopg.conninfo.conninfo_to_dict(database_url)
    except Exception as exc:  # noqa: BLE001 — string malformada
        raise ProvisionError(f"DATABASE_URL invalida: {exc}") from exc


def nome_do_banco(database_url: str) -> str:
    """O banco alvo. Sem ele nao ha o que criar."""
    info = _conninfo(database_url)
    nome = info.get("dbname")
    if not nome:
        raise ProvisionError(
            "DATABASE_URL nao nomeia um banco. Ela precisa terminar com o nome "
            "(ex.: postgresql:///kobe), senao nao ha o que criar nem onde "
            "aplicar o schema."
        )
    return nome


def url_de_manutencao(database_url: str) -> str:
    """A mesma conexao, mas apontando pro banco `postgres`.

    Nao da pra criar um banco estando conectado a ele. O `postgres` existe em
    todo cluster e e o ponto de entrada convencional pra isso.
    """
    import psycopg

    info = _conninfo(database_url)
    info["dbname"] = "postgres"
    return psycopg.conninfo.make_conninfo(**info)


def servidor_alcancavel(database_url: str) -> tuple[bool, str]:
    """`(alcancavel, descricao)`. Nao levanta — quem chama decide o que fazer.

    Separado de `garantir` de proposito: "nao ha PostgreSQL aqui" e uma
    resposta diferente de "ha, mas o banco nao existe", e o instalador precisa
    dizer coisas diferentes nos dois casos.
    """
    import psycopg

    try:
        with psycopg.connect(url_de_manutencao(database_url), connect_timeout=8) as conn:
            versao = conn.execute("SHOW server_version").fetchone()[0]
        return True, f"PostgreSQL {versao}"
    except ProvisionError:
        raise
    except Exception as exc:  # noqa: BLE001 — rede, auth, servidor fora
        return False, str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)


def banco_existe(database_url: str) -> bool:
    import psycopg

    alvo = nome_do_banco(database_url)
    with psycopg.connect(url_de_manutencao(database_url), connect_timeout=8) as conn:
        return conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (alvo,)
        ).fetchone() is not None


def garantir(database_url: str, *, dry_run: bool = False) -> bool:
    """Cria o banco se faltar. Devolve `True` se criou, `False` se ja existia."""
    import psycopg
    from psycopg import sql

    alvo = nome_do_banco(database_url)

    if banco_existe(database_url):
        print(f"banco {alvo!r} ja existe — nada a criar.")
        return False

    p = parametros_de_criacao()
    print(
        f"banco {alvo!r} nao existe. Criando com "
        f"encoding={p['encoding']} collate={p['collate']} ctype={p['ctype']} "
        f"TimeZone={p['timezone']}."
    )
    if dry_run:
        print("[dry-run] nada foi criado.")
        return False

    # `TEMPLATE template0` e obrigatorio pra poder escolher collation: o
    # `template1` traz a do cluster e o Postgres recusa mudar em cima dele.
    # O nome do banco vai por `sql.Identifier` — ele nao pode ser parametro
    # ligado num comando DDL, e concatenar seria injecao.
    criar = sql.SQL(
        "CREATE DATABASE {} TEMPLATE template0 ENCODING {} LC_COLLATE {} LC_CTYPE {}"
    ).format(
        sql.Identifier(alvo),
        sql.Literal(p["encoding"]),
        sql.Literal(p["collate"]),
        sql.Literal(p["ctype"]),
    )
    ajustar = sql.SQL("ALTER DATABASE {} SET TimeZone TO {}").format(
        sql.Identifier(alvo), sql.Literal(p["timezone"])
    )

    try:
        # `autocommit` porque CREATE DATABASE nao roda dentro de transacao.
        with psycopg.connect(
            url_de_manutencao(database_url), autocommit=True, connect_timeout=8
        ) as conn:
            conn.execute(criar)
            conn.execute(ajustar)
    except psycopg.errors.InsufficientPrivilege as exc:
        raise ProvisionError(
            f"o usuario da conexao nao tem permissao pra criar banco ({exc}).\n"
            "Peca a alguem com privilegio pra rodar, uma vez:\n"
            f"  CREATE DATABASE {alvo} TEMPLATE template0 ENCODING '{p['encoding']}'"
            f" LC_COLLATE '{p['collate']}' LC_CTYPE '{p['ctype']}';\n"
            f"  ALTER DATABASE {alvo} SET TimeZone TO '{p['timezone']}';\n"
            "Os parametros importam: collation nao se troca depois sem recriar "
            "o banco."
        ) from exc
    except psycopg.errors.DuplicateDatabase:
        # Alguem criou entre a checagem e o comando. Nada a fazer.
        print(f"banco {alvo!r} apareceu no meio do caminho — seguindo.")
        return False
    except Exception as exc:  # noqa: BLE001
        raise ProvisionError(f"falha criando o banco {alvo!r}: {exc}") from exc

    print(f"banco {alvo!r} criado.")
    return True


# ── FRONTEIRA: o que este arquivo NAO faz, e fica pra sessao do instalador ──
#
# O alvo final da diretriz e maior que isto. O que falta, nomeado pra nao se
# perder:
#
#   1. DETECTAR se ha PostgreSQL na maquina (e qual versao), em vez de so
#      tentar conectar e reportar a falha.
#   2. DECIDIR com a pessoa entre usar o cluster existente ou subir um
#      dedicado — hoje se usa o que a DATABASE_URL apontar.
#   3. CRIAR cluster/instancia, e CRIAR ROLE proprio pro Kobe com o minimo de
#      privilegio. Hoje o usuario da conexao e reaproveitado como dono.
#   4. INSTALAR o PostgreSQL e o pgvector quando faltarem.
#
# O que ja esta atendido: nao se pressupoe banco existente, e o DDL do schema
# roda em tempo de execucao pelo runner (`infra/migrate.py`), nunca colado a
# mao num painel.


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="provision_db.py",
        description="Cria o banco do Kobe se ele ainda nao existir.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    url = (args.database_url or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        print(
            "erro: alvo ausente — passe --database-url ou defina DATABASE_URL.",
            file=sys.stderr,
        )
        return 1

    try:
        alcancavel, detalhe = servidor_alcancavel(url)
        if not alcancavel:
            print(
                f"erro: nao consegui falar com o servidor PostgreSQL.\n  {detalhe}\n"
                "Confira se ele esta instalado e no ar, e se a DATABASE_URL aponta "
                "pro lugar certo (host, porta, usuario).",
                file=sys.stderr,
            )
            return 2
        print(f"servidor alcancavel: {detalhe}")
        garantir(url, dry_run=args.dry_run)
    except ProvisionError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
