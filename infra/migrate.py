#!/usr/bin/env python3
"""Runner de migrations versionado do Kobe.

O PROBLEMA QUE ELE RESOLVE
--------------------------
Ate aqui, "aplicar o schema" era copiar `infra/schema.sql` e colar num painel
web, e as migrations de `infra/migrations/` eram cinco arquivos soltos sem
tabela de versao e sem ordem forcada. Ninguem — nem o codigo, nem o operador —
conseguia responder "em que versao este banco esta?" sem inspecionar tabela por
tabela. Com a ponte pro Postgres direto, essa pergunta passa a ter que ter
resposta mecanica: e o instalador, o ambiente de dev e o corte todos dependem
dela.

AS QUATRO GARANTIAS
-------------------
1. **Ordem determinista.** A ordem e a do prefixo NUMERICO do arquivo
   (`001_`, `002_`, ...), nunca a ordem alfabetica do filesystem — que
   colocaria `010` antes de `002`. `infra/schema.sql` e sempre a versao `000`,
   o alicerce sobre o qual as demais se aplicam.
2. **Idempotencia.** O que ja consta em `schema_migrations` e pulado. Rodar
   `up` duas vezes seguidas nao faz nada na segunda.
3. **Recusa de aplicar fora de ordem.** Se aparece uma pendente com numero
   MENOR que o maior ja aplicado (o caso classico: dois branches criam `006` e
   `007`, o `007` entra primeiro, e depois o `006` chega atrasado), o runner
   PARA. Aplicar `006` depois de `007` produz um banco que nenhuma outra
   instalacao tem, e o estrago aparece longe da causa.
4. **Deteccao de drift.** O checksum de cada arquivo aplicado fica gravado. Se
   o conteudo de uma migration ja aplicada mudar, o runner PARA — porque o
   banco tem o SQL antigo e o repo tem o novo, e ninguem mais sabe qual e a
   verdade. Migration aplicada e imutavel; correcao vira migration nova.

O BANCO QUE JA TEM O SCHEMA MAS NAO TEM O REGISTRO
---------------------------------------------------
Um banco montado de outro jeito — restaurado de dump, copiado de outro
ambiente, ou criado antes de este runner existir — tem as tabelas mas **nao tem
a tabela de controle**. Para o runner, ele parece um banco vazio: `status` diz
"tudo pendente", e um `up` tentaria aplicar a historia inteira.

As migrations idempotentes atravessariam isso sem estrago. **Uma migration
destrutiva, nao.** Ela apagaria de verdade, e a pessoa que so queria registrar
a versao veria dado sumir.

E para isso que existe o `baseline`: ele **registra** versoes como aplicadas
**sem executar nenhuma delas**. Diz ao runner "este banco ja esta neste ponto",
e a partir dali o `up` so aplica o que veio depois. Ele exige `--through`
explicito e se recusa a rodar num banco que ja tem registro — carimbar a versao
errada e pior que nao carimbar nenhuma.

O QUE ELE NAO FAZ, DE PROPOSITO
-------------------------------
Nao ha `down` / rollback automatico. Reverter DDL por script e a fonte
classica de perda de dado silenciosa: o `down` de um `ADD COLUMN` e um
`DROP COLUMN`, e ninguem quer isso rodando sozinho. O caminho de volta aqui e
o do resto do projeto — restaurar de backup, ou uma migration nova pra frente.

USO
---
    python infra/migrate.py status               # o que esta aplicado e o que falta
    python infra/migrate.py up --dry-run         # o plano, sem escrever nada
    python infra/migrate.py up                   # aplica as pendentes
    python infra/migrate.py baseline --through 004
                                                 # marca 000..004 como aplicadas
                                                 # SEM executar (banco vindo de dump)

O alvo vem de `--database-url` ou da env `DATABASE_URL`, nesta ordem. Nunca ha
alvo default — apontar pro banco errado tem que exigir um ato explicito.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Raiz do projeto: infra/migrate.py -> infra -> raiz. Derivada em runtime, nunca
# cravada — o repo de producao e publico e roda na maquina de qualquer um.
_INFRA_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _INFRA_DIR.parent

SCHEMA_FILE = _INFRA_DIR / "schema.sql"
MIGRATIONS_DIR = _INFRA_DIR / "migrations"

# `NNN_nome.sql`. O prefixo numerico e obrigatorio: e ele que define a ordem.
_VERSION_RE = re.compile(r"^(\d{3,})_(.+)\.sql$")

CONTROL_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text        PRIMARY KEY,
    filename    text        NOT NULL,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


class MigrationError(RuntimeError):
    """Falha que exige olho humano — nunca e contornada em silencio."""


@dataclass(frozen=True)
class Migration:
    version: str      # "000", "001", ... — string preservando os zeros a esquerda
    filename: str
    path: Path

    @property
    def sort_key(self) -> int:
        return int(self.version)

    def checksum(self) -> str:
        """sha256 do conteudo em bytes. Em bytes de proposito: normalizar
        encoding aqui esconderia exatamente a mudanca que se quer detectar."""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def discover() -> list[Migration]:
    """Todas as migrations conhecidas, em ordem numerica.

    `infra/schema.sql` e a versao `000` — o alicerce. As demais saem de
    `infra/migrations/` e precisam do prefixo numerico; arquivo `.sql` sem
    prefixo e ERRO, nao arquivo ignorado, porque "ignorado em silencio" e
    como uma migration deixa de ser aplicada sem ninguem notar.
    """
    found: list[Migration] = []

    if not SCHEMA_FILE.exists():
        raise MigrationError(f"schema base ausente: {SCHEMA_FILE}")
    found.append(Migration(version="000", filename=SCHEMA_FILE.name, path=SCHEMA_FILE))

    if MIGRATIONS_DIR.is_dir():
        for path in sorted(MIGRATIONS_DIR.iterdir()):
            if path.suffix != ".sql" or not path.is_file():
                continue
            m = _VERSION_RE.match(path.name)
            if not m:
                raise MigrationError(
                    f"migration sem prefixo numerico: {path.name} — renomeie para "
                    "NNN_descricao.sql (e o prefixo que define a ordem de aplicacao)"
                )
            found.append(Migration(version=m.group(1), filename=path.name, path=path))

    versions = [mig.version for mig in found]
    duplicates = {v for v in versions if versions.count(v) > 1}
    if duplicates:
        raise MigrationError(
            f"versao duplicada em infra/migrations/: {sorted(duplicates)} — "
            "duas migrations com o mesmo numero tornam a ordem indefinida"
        )

    return sorted(found, key=lambda mig: mig.sort_key)


def _connect(database_url: str):
    import psycopg  # import tardio: `status` de um repo sem venv ainda da erro util

    return psycopg.connect(database_url, autocommit=False)


def applied_map(conn) -> dict[str, str]:
    """`{version: checksum}` do que ja foi aplicado neste banco."""
    with conn.cursor() as cur:
        cur.execute(CONTROL_TABLE_DDL)
        cur.execute("SELECT version, checksum FROM schema_migrations")
        rows = cur.fetchall()
    conn.commit()
    return {version: checksum for version, checksum in rows}


def plan(migrations: list[Migration], applied: dict[str, str]) -> list[Migration]:
    """As pendentes, ja validadas contra drift e contra ordem quebrada.

    Levanta `MigrationError` antes de devolver qualquer coisa: um plano so e
    entregue quando o estado do banco e coerente com o repo por inteiro.
    """
    # 1. Drift — o repo mudou um arquivo que o banco ja aplicou.
    for mig in migrations:
        recorded = applied.get(mig.version)
        if recorded is not None and recorded != mig.checksum():
            raise MigrationError(
                f"drift na migration {mig.version} ({mig.filename}): o arquivo mudou "
                "depois de aplicado neste banco.\n"
                f"  banco: {recorded[:16]}...\n"
                f"  repo:  {mig.checksum()[:16]}...\n"
                "Migration aplicada e imutavel. Corrija com uma migration NOVA, "
                "para a frente — nunca editando uma que ja rodou."
            )

    pending = [mig for mig in migrations if mig.version not in applied]
    if not pending:
        return []

    # 2. Ordem — pendente com numero menor que o maior ja aplicado.
    if applied:
        highest_applied = max(int(v) for v in applied)
        late = [mig for mig in pending if mig.sort_key < highest_applied]
        if late:
            nomes = ", ".join(f"{mig.version} ({mig.filename})" for mig in late)
            raise MigrationError(
                f"migration fora de ordem: {nomes} — este banco ja esta na versao "
                f"{highest_applied:03d}.\n"
                "Aplicar uma versao anterior agora produziria um banco que nenhuma "
                "outra instalacao tem. Renumere a migration atrasada para depois da "
                "ultima aplicada."
            )

    return pending


def apply_one(conn, mig: Migration) -> None:
    """Aplica uma migration e registra a versao — na MESMA transacao.

    Os dois juntos e o ponto todo: se o SQL falha no meio, o registro nao fica;
    se o registro falha, o SQL volta. Nunca ha um banco que aplicou sem
    registrar (rodaria de novo) nem que registrou sem aplicar (nunca rodaria).
    """
    sql = mig.path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        # Sem parametros de proposito: psycopg so aceita multiplas instrucoes
        # num execute quando nao ha parametro ligado, e todo arquivo de
        # migration tem varias.
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (version, filename, checksum) "
            "VALUES (%s, %s, %s)",
            (mig.version, mig.filename, mig.checksum()),
        )
    conn.commit()


def resolve_url(explicit: Optional[str]) -> str:
    url = explicit or os.getenv("DATABASE_URL") or ""
    if not url.strip():
        raise MigrationError(
            "alvo ausente: passe --database-url ou defina DATABASE_URL. "
            "Nao ha alvo default de proposito — apontar pro banco errado tem "
            "que custar um ato explicito."
        )
    return url.strip()


def cmd_baseline(url: str, through: str, dry_run: bool) -> int:
    """Marca versoes como aplicadas SEM executar nenhuma delas.

    Para um banco que ja tem o schema por outro caminho (dump, copia, ou
    criado antes deste runner). Duas travas, e as duas importam:

    - **Recusa se ja houver registro.** Carimbar por cima de um historico
      existente esconderia exatamente a divergencia que o runner existe pra
      mostrar.
    - **`--through` e obrigatorio.** Sem ele, o default natural seria "marca
      tudo" — e "tudo" inclui as destrutivas, que passariam a NUNCA rodar
      naquele banco, em silencio. A pessoa tem que dizer ate onde.
    """
    migrations = discover()
    conhecidas = {mig.version for mig in migrations}
    if through not in conhecidas:
        raise MigrationError(
            f"versao {through!r} nao existe. Conhecidas: {sorted(conhecidas)}"
        )

    alvo = int(through)
    marcar = [mig for mig in migrations if mig.sort_key <= alvo]
    depois = [mig for mig in migrations if mig.sort_key > alvo]

    with _connect(url) as conn:
        aplicadas = applied_map(conn)
        if aplicadas:
            raise MigrationError(
                f"este banco ja tem {len(aplicadas)} migration(s) registrada(s) — "
                "`baseline` e so para banco SEM historico (vindo de dump, copia, "
                "ou anterior a este runner). Use `status` para ver o estado."
            )

        print("marcando como aplicadas, SEM executar:")
        for mig in marcar:
            print(f"  {mig.version}  {mig.filename}")
        if depois:
            print("\nseguem pendentes (e um `up` vai aplica-las de verdade):")
            for mig in depois:
                print(f"  {mig.version}  {mig.filename}")

        if dry_run:
            print("\n[dry-run] nada foi escrito.")
            return 0

        with conn.cursor() as cur:
            for mig in marcar:
                cur.execute(
                    "INSERT INTO schema_migrations (version, filename, checksum) "
                    "VALUES (%s, %s, %s)",
                    (mig.version, mig.filename, mig.checksum()),
                )
        conn.commit()

    print(f"\n{len(marcar)} versao(oes) registrada(s). Nenhum SQL foi executado.")
    return 0


def cmd_status(url: str) -> int:
    migrations = discover()
    with _connect(url) as conn:
        applied = applied_map(conn)
    for mig in migrations:
        recorded = applied.get(mig.version)
        if recorded is None:
            mark = "pendente"
        elif recorded != mig.checksum():
            mark = "DRIFT"
        else:
            mark = "aplicada"
        print(f"  {mig.version}  {mark:<9} {mig.filename}")
    faltam = sum(1 for mig in migrations if mig.version not in applied)
    print(f"\n{len(migrations)} conhecidas, {len(applied)} aplicadas, {faltam} pendentes.")
    return 0


def cmd_up(url: str, dry_run: bool) -> int:
    migrations = discover()
    with _connect(url) as conn:
        applied = applied_map(conn)
        pending = plan(migrations, applied)

        if not pending:
            print("nada a aplicar — o banco esta em dia.")
            return 0

        for mig in pending:
            print(f"  {mig.version}  {mig.filename}")
        if dry_run:
            print(f"\n[dry-run] {len(pending)} pendente(s). Nada foi escrito.")
            return 0

        for mig in pending:
            apply_one(conn, mig)
            print(f"  aplicada: {mig.version} {mig.filename}")

    print(f"\n{len(pending)} migration(s) aplicada(s).")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate.py", description="Runner de migrations versionado do Kobe."
    )
    parser.add_argument("command", choices=("status", "up", "baseline"))
    parser.add_argument("--database-url", default=None, help="alvo; senao usa DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true", help="so mostra o plano")
    parser.add_argument(
        "--through",
        default=None,
        help="baseline: ate que versao marcar como aplicada (ex.: 004)",
    )
    args = parser.parse_args(argv)

    try:
        url = resolve_url(args.database_url)
        if args.command == "status":
            return cmd_status(url)
        if args.command == "baseline":
            if not args.through:
                raise MigrationError(
                    "`baseline` exige --through <versao>. Sem ele o default seria "
                    "'marca tudo', e 'tudo' inclui as destrutivas — que passariam a "
                    "NUNCA rodar neste banco, em silencio."
                )
            return cmd_baseline(url, args.through, args.dry_run)
        return cmd_up(url, args.dry_run)
    except MigrationError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
