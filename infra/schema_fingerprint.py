#!/usr/bin/env python3
"""Impressao digital de um banco Postgres — a entrada do portao de compatibilidade.

POR QUE ISTO EXISTE
-------------------
Tres divergencias de AMBIENTE entre o banco de dev e o de producao passaram por
100% de uma suite de 456 testes sem acender nada. Nenhuma delas e bug de codigo;
todas fariam "testei em dev" mentir:

- **Collation.** O `initdb` do Ubuntu cria o banco em `C.UTF-8`, que ordena por
  byte cru; a producao esta em `en_US.UTF-8`. O dado e o mesmo e a ORDEM DE
  SAIDA de `ORDER BY <texto>` muda — acento e maiuscula caem em lugar
  diferente. Ha caso vivo no codigo: a lista de contatos e ordenada por nome.
- **Ordem fisica das colunas.** Duas colunas de `topics` entraram por migration
  na producao (e portanto ficaram NO FIM) e estao NO MEIO no `infra/schema.sql`.
  Mesmo nome, mesmo tipo, posicao diferente. Nao afeta o Kobe (o codigo acessa
  por nome), mas quebra qualquer carga posicional — e quebra EM SILENCIO,
  empurrando texto pra dentro de campo numerico. Um diff "por nome/tipo/nulo"
  da vazio aqui: e preciso comparar POSICAO.
- **`data_checksums`.** Ligado na producao, desligado por default no `initdb`
  do Ubuntu. Liga-lo depois exige parar o cluster e reescrever tudo.

Este arquivo transforma um banco num JSON canonico. `infra/compat_gate.py`
compara dois desses JSON e falha nomeando a classe da divergencia. O objetivo e
que a PROXIMA armadilha desta familia morra num teste vermelho, e nao numa
madrugada de investigacao.

O JSON e ordenado e deterministico de proposito: e ele que vai versionado em
`tests/fixtures/schema_expected.json`, e um diff de git nele tem que ser
legivel por gente.

USO
---
    python infra/schema_fingerprint.py --database-url postgresql:///kobe_dev
    python infra/schema_fingerprint.py --database-url ... --out arquivo.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Optional

FINGERPRINT_VERSION = 2

# Schema do Kobe. `information_schema` e `pg_catalog` ficam de fora; a tabela de
# controle de migration tambem — ela e do runner, nao do modelo de dados, e
# incluir seu `applied_at` faria a impressao digital mudar a cada aplicacao.
#
# A LISTA DE VERSOES dela, porem, VAI (chave `migrations`, versao 2 da
# impressao digital). A distincao e o ponto todo: `applied_at` muda a cada
# aplicacao e viraria ruido; a lista de versoes so muda quando o MODELO muda —
# e e exatamente ela que faltava para o portao perceber que a referencia ficou
# velha. A F1 acrescentou a migration `006` sem regenerar a referencia, e uma
# suite de 691 testes passou verde enquanto o portao acusava 4 divergencias
# falsas nos DOIS ambientes. Nada travava, porque nada olhava para isto.
TARGET_SCHEMA = "public"
IGNORED_TABLES = frozenset({"schema_migrations"})


# ── Consultas de introspeccao ─────────────────────────────────────────────

_Q_DATABASE = """
SELECT
    pg_encoding_to_char(d.encoding)        AS encoding,
    d.datcollate                           AS collate,
    d.datctype                             AS ctype,
    current_setting('data_checksums')      AS data_checksums,
    current_setting('TimeZone')            AS timezone,
    current_setting('server_version')      AS server_version,
    current_setting('server_version_num')  AS server_version_num
FROM pg_database d
WHERE d.datname = current_database()
"""

_Q_EXTENSIONS = """
SELECT extname, extversion FROM pg_extension ORDER BY extname
"""

# `attnum` e a posicao FISICA real. Colunas removidas deixam buraco na
# numeracao, entao `attisdropped` e filtrado e a posicao logica e recontada —
# mas o `attnum` cru vai junto, porque um buraco na numeracao e ele proprio um
# sinal (a tabela sofreu um DROP COLUMN em algum ambiente e no outro nao).
_Q_COLUMNS = """
SELECT
    c.relname                                        AS table_name,
    a.attnum                                         AS attnum,
    a.attname                                        AS column_name,
    pg_catalog.format_type(a.atttypid, a.atttypmod)  AS data_type,
    a.attnotnull                                     AS notnull,
    pg_get_expr(ad.adbin, ad.adrelid)                AS default_expr,
    a.attidentity                                    AS identity,
    coll.collname                                    AS collation
FROM pg_attribute a
JOIN pg_class c        ON c.oid = a.attrelid
JOIN pg_namespace n    ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum
LEFT JOIN pg_collation coll ON coll.oid = a.attcollation
                            AND coll.collname <> 'default'
WHERE n.nspname = %s
  AND c.relkind = 'r'
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

_Q_INDEXES = """
SELECT c.relname AS table_name, i.indexname, i.indexdef
FROM pg_indexes i
JOIN pg_class c ON c.relname = i.tablename
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = i.schemaname
WHERE i.schemaname = %s AND c.relkind = 'r'
ORDER BY i.tablename, i.indexname
"""

_Q_CONSTRAINTS = """
SELECT c.relname AS table_name, con.conname, pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class c     ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = %s AND c.relkind = 'r'
ORDER BY c.relname, con.conname
"""


# So a coluna `version`. `filename`, `checksum` e `applied_at` ficam de fora de
# proposito: os dois primeiros ja sao vigiados pelo proprio runner (ele recusa
# aplicar com checksum divergente), e o terceiro mudaria a impressao digital a
# cada aplicacao.
_Q_MIGRATIONS = """
SELECT version FROM schema_migrations ORDER BY version
"""

_Q_TEM_CONTROLE = """
SELECT to_regclass('public.schema_migrations') IS NOT NULL AS existe
"""


def _rows(cur, sql: str, params: tuple = ()) -> list[dict]:
    cur.execute(sql, params) if params else cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fingerprint(conn) -> dict[str, Any]:
    """O JSON canonico deste banco."""
    with conn.cursor() as cur:
        db = _rows(cur, _Q_DATABASE)[0]
        extensions = {r["extname"]: r["extversion"] for r in _rows(cur, _Q_EXTENSIONS)}
        columns = _rows(cur, _Q_COLUMNS, (TARGET_SCHEMA,))
        indexes = _rows(cur, _Q_INDEXES, (TARGET_SCHEMA,))
        constraints = _rows(cur, _Q_CONSTRAINTS, (TARGET_SCHEMA,))
        # `None` (banco nunca tocado pelo runner) e `[]` (tabela de controle
        # vazia) sao coisas DIFERENTES, e o portao trata cada uma do seu jeito:
        # a primeira e "nao da pra julgar", a segunda e "esta zerado".
        if _rows(cur, _Q_TEM_CONTROLE)[0]["existe"]:
            migrations: Optional[list[str]] = [
                r["version"] for r in _rows(cur, _Q_MIGRATIONS)
            ]
        else:
            migrations = None

    tables: dict[str, dict] = {}

    for row in columns:
        name = row["table_name"]
        if name in IGNORED_TABLES:
            continue
        entry = tables.setdefault(name, {"columns": [], "indexes": [], "constraints": []})
        entry["columns"].append(
            {
                # `position` e a ordem logica entre as colunas VIVAS — e ela que
                # governa uma carga posicional. `attnum` vai junto pra expor
                # buraco de coluna removida.
                "position": len(entry["columns"]) + 1,
                "attnum": row["attnum"],
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": not row["notnull"],
                "default": row["default_expr"],
                "identity": row["identity"] or None,
                "collation": row["collation"],
            }
        )

    for row in indexes:
        name = row["table_name"]
        if name in IGNORED_TABLES or name not in tables:
            continue
        tables[name]["indexes"].append(
            {"name": row["indexname"], "definition": row["indexdef"]}
        )

    for row in constraints:
        name = row["table_name"]
        if name in IGNORED_TABLES or name not in tables:
            continue
        tables[name]["constraints"].append(
            {"name": row["conname"], "definition": row["definition"]}
        )

    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "database": {
            "encoding": db["encoding"],
            "collate": db["collate"],
            "ctype": db["ctype"],
            "data_checksums": db["data_checksums"],
            "timezone": db["timezone"],
            # A versao COMPLETA vai como informacao; o portao compara so o
            # MAIOR. 16.15 -> 16.16 e atualizacao de seguranca, nao classe de
            # incompatibilidade; 16 -> 17 e.
            "server_version": db["server_version"],
            "server_version_major": int(db["server_version_num"]) // 10000,
        },
        "extensions": dict(sorted(extensions.items())),
        "migrations": migrations,
        "tables": {name: tables[name] for name in sorted(tables)},
    }


def dumps(fp: dict[str, Any]) -> str:
    """Serializacao canonica: indentada, sem reordenar (a ordem das colunas E o
    dado), e com quebra de linha no fim pra o diff de git ficar limpo."""
    return json.dumps(fp, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def from_url(database_url: str) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(database_url) as conn:
        return fingerprint(conn)


def resolve_url(explicit: Optional[str]) -> str:
    url = explicit or os.getenv("DATABASE_URL") or ""
    if not url.strip():
        raise SystemExit(
            "erro: alvo ausente — passe --database-url ou defina DATABASE_URL."
        )
    return url.strip()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="schema_fingerprint.py",
        description="Gera a impressao digital de um banco Postgres do Kobe.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--out", default=None, help="arquivo de saida; padrao stdout")
    args = parser.parse_args(argv)

    texto = dumps(from_url(resolve_url(args.database_url)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(texto)
        print(f"impressao digital escrita em {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(texto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
