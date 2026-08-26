#!/usr/bin/env python3
"""T4 — o portao permanente de compatibilidade de ambiente.

O QUE ELE E, EM UMA FRASE
------------------------
Compara a impressao digital de um banco (`infra/schema_fingerprint.py`) contra
a referencia versionada em `tests/fixtures/schema_expected.json` e FALHA
nomeando a classe da divergencia.

POR QUE ELE PRECISOU EXISTIR
----------------------------
Tres divergencias de ambiente entre dev e producao atravessaram 100% de uma
suite de 456 testes sem acender nada — collation do banco, ordem FISICA das
colunas, e `data_checksums`. Nenhuma e bug de codigo; todas fazem "testei em
dev" mentir. Um diff "por nome, tipo e nulo" da vazio em duas delas.

Rodando pela primeira vez, este portao achou uma QUARTA da mesma familia: o
`TimeZone`. O `initdb` do Ubuntu deixa o cluster no fuso local da maquina, e
todo banco criado nele nasce herdando esse fuso, enquanto a producao esta em
UTC. O valor guardado e o mesmo (timestamptz e absoluto), mas o texto que o
driver devolve muda de `+00:00` para o deslocamento local — e o Kobe compara
`created_at` como STRING em pelo menos um caminho. E exatamente o formato de
armadilha que motivou o portao, encontrado pelo portao.

AS SEIS CLASSES QUE ELE PEGA
----------------------------
1. `ambiente`   — collation, ctype, encoding, `data_checksums`, `TimeZone`,
                  versao MAIOR do servidor.
2. `extensao`   — ausente, sobrando, ou em versao diferente da referencia.
3. `tabela`     — ausente ou sobrando.
4. `coluna`     — ausente, sobrando, tipo, nulabilidade, default, e **ORDEM
                  FISICA**. A ordem e comparada separadamente das demais, com
                  mensagem propria, porque e a unica que um diff por nome nao
                  enxerga e a unica que quebra carga posicional em silencio.
5. `indice` / `restricao` — ausente, sobrando, ou com definicao diferente.
6. `pgvector`   — uso, no repositorio, de recurso que so existe acima da versao
                  de `vector` que a referencia fixa. Vide a ressalva honesta na
                  funcao correspondente: isto e uma lista de proibidos, nao uma
                  prova.

O QUE ELE NAO EXIGE
-------------------
A producao no ar. A referencia e um arquivo versionado, gerado do proprio
repositorio (`infra/schema.sql` + `infra/migrations/` aplicados por
`infra/migrate.py` num banco de apoio). E isso que faz o item "schema
versionado x banco real" ser verdade POR CONSTRUCAO, e nao por promessa.

USO
---
    python infra/compat_gate.py --database-url postgresql:///kobe_dev
    python infra/compat_gate.py --database-url ... --reference outro.json

Sai 0 quando o alvo confere, 1 em qualquer divergencia.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_INFRA_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _INFRA_DIR.parent

DEFAULT_REFERENCE = _PROJECT_ROOT / "tests" / "fixtures" / "schema_expected.json"

# Propriedades de ambiente comparadas, com o porque de cada uma na mensagem.
_AMBIENTE = {
    "encoding": "encoding do banco",
    "collate": "collation do banco (governa a ordem de ORDER BY em texto)",
    "ctype": "ctype do banco (classificacao de caractere)",
    "data_checksums": "data_checksums (liga-lo depois exige parar o cluster)",
    "timezone": "TimeZone (governa o texto que o driver devolve para timestamptz)",
    "server_version_major": "versao MAIOR do servidor",
}

# Identificadores que so existem em pgvector 0.7+. Se aparecerem no repositorio
# enquanto a referencia fixa uma versao menor, o portao acende.
_PGVECTOR_ACIMA_DE_06 = {
    "halfvec": "0.7.0",
    "sparsevec": "0.7.0",
    "binary_quantize": "0.7.0",
    "halfvec_l2_ops": "0.7.0",
    "halfvec_ip_ops": "0.7.0",
    "halfvec_cosine_ops": "0.7.0",
    "sparsevec_l2_ops": "0.7.0",
    "bit_hamming_ops": "0.7.0",
    "bit_jaccard_ops": "0.7.0",
    "hamming_distance": "0.7.0",
    "jaccard_distance": "0.7.0",
    "subvector": "0.7.0",
    "l1_distance": "0.7.0",
}


@dataclass(frozen=True)
class Finding:
    classe: str
    mensagem: str

    def __str__(self) -> str:
        return f"[{self.classe}] {self.mensagem}"


# ── Comparadores, um por classe ───────────────────────────────────────────


def _cmp_ambiente(ref: dict, tgt: dict) -> list[Finding]:
    out: list[Finding] = []
    r, t = ref.get("database", {}), tgt.get("database", {})
    for chave, descricao in _AMBIENTE.items():
        if chave not in r:
            continue
        if r[chave] != t.get(chave):
            out.append(
                Finding(
                    "ambiente",
                    f"{descricao}: esperado {r[chave]!r}, encontrado {t.get(chave)!r}",
                )
            )
    return out


def _cmp_extensoes(ref: dict, tgt: dict) -> list[Finding]:
    out: list[Finding] = []
    r, t = ref.get("extensions", {}), tgt.get("extensions", {})
    for nome in sorted(set(r) | set(t)):
        if nome not in t:
            out.append(Finding("extensao", f"{nome} ausente no alvo (esperada {r[nome]})"))
        elif nome not in r:
            out.append(Finding("extensao", f"{nome} {t[nome]} sobrando no alvo"))
        elif r[nome] != t[nome]:
            extra = ""
            if nome == "vector":
                extra = (
                    " — a busca de vizinhos e a renderizacao do vetor foram conferidas"
                    " entre versoes uma vez, a mao; trocar de versao pede refazer essa"
                    " conferencia antes de confiar em dev"
                )
            out.append(
                Finding(
                    "extensao",
                    f"{nome}: esperada {r[nome]}, encontrada {t[nome]}{extra}",
                )
            )
    return out


def _cmp_tabelas(ref: dict, tgt: dict) -> list[Finding]:
    out: list[Finding] = []
    r, t = ref.get("tables", {}), tgt.get("tables", {})
    for nome in sorted(set(r) - set(t)):
        out.append(Finding("tabela", f"{nome} ausente no alvo"))
    for nome in sorted(set(t) - set(r)):
        out.append(
            Finding(
                "tabela",
                f"{nome} existe no alvo e nao no schema versionado — o banco pode "
                "estar ATRASADO numa migration; confira `python infra/migrate.py status`",
            )
        )
    return out


def _cmp_colunas(ref: dict, tgt: dict) -> list[Finding]:
    out: list[Finding] = []
    r, t = ref.get("tables", {}), tgt.get("tables", {})

    for tabela in sorted(set(r) & set(t)):
        rc = {c["name"]: c for c in r[tabela]["columns"]}
        tc = {c["name"]: c for c in t[tabela]["columns"]}

        for nome in sorted(set(rc) - set(tc)):
            out.append(Finding("coluna", f"{tabela}.{nome} ausente no alvo"))
        for nome in sorted(set(tc) - set(rc)):
            out.append(
                Finding(
                    "coluna",
                    f"{tabela}.{nome} existe no alvo e nao no schema versionado — "
                    "confira `python infra/migrate.py status`",
                )
            )

        for nome in sorted(set(rc) & set(tc)):
            a, b = rc[nome], tc[nome]
            for campo, rotulo in (
                ("type", "tipo"),
                ("nullable", "nulabilidade"),
                ("default", "default"),
                ("collation", "collation da coluna"),
            ):
                if a.get(campo) != b.get(campo):
                    out.append(
                        Finding(
                            "coluna",
                            f"{tabela}.{nome} {rotulo}: esperado {a.get(campo)!r}, "
                            f"encontrado {b.get(campo)!r}",
                        )
                    )

        # A ORDEM FISICA, em classe propria — e a que nenhum diff por nome ve,
        # e a que quebra COPY/INSERT posicional empurrando texto pra dentro de
        # campo numerico, em silencio.
        ordem_ref = [c["name"] for c in r[tabela]["columns"] if c["name"] in tc]
        ordem_tgt = [c["name"] for c in t[tabela]["columns"] if c["name"] in rc]
        if ordem_ref != ordem_tgt:
            out.append(
                Finding(
                    "ordem-de-coluna",
                    f"{tabela}: a ordem FISICA das colunas diverge — mesmo nome, mesmo "
                    f"tipo, posicao diferente.\n"
                    f"        esperada:   {ordem_ref}\n"
                    f"        encontrada: {ordem_tgt}\n"
                    "        Isto nao afeta o Kobe (o codigo acessa por nome), mas "
                    "quebra qualquer COPY/INSERT posicional, EM SILENCIO.",
                )
            )
    return out


def _cmp_objetos(ref: dict, tgt: dict, chave: str, classe: str) -> list[Finding]:
    """Indices e restricoes — mesma forma, `{name, definition}`."""
    out: list[Finding] = []
    r, t = ref.get("tables", {}), tgt.get("tables", {})
    for tabela in sorted(set(r) & set(t)):
        ro = {o["name"]: o["definition"] for o in r[tabela].get(chave, [])}
        to = {o["name"]: o["definition"] for o in t[tabela].get(chave, [])}
        for nome in sorted(set(ro) - set(to)):
            out.append(Finding(classe, f"{tabela}: {nome} ausente no alvo"))
        for nome in sorted(set(to) - set(ro)):
            out.append(Finding(classe, f"{tabela}: {nome} sobrando no alvo"))
        for nome in sorted(set(ro) & set(to)):
            if ro[nome] != to[nome]:
                out.append(
                    Finding(
                        classe,
                        f"{tabela}: {nome} com definicao diferente\n"
                        f"        esperada:   {ro[nome]}\n"
                        f"        encontrada: {to[nome]}",
                    )
                )
    return out


def scan_pgvector(ref: dict, raiz: Path = _PROJECT_ROOT) -> list[Finding]:
    """Uso, no repositorio, de recurso de `vector` acima do que a referencia fixa.

    RESSALVA HONESTA, e ela importa: isto NAO e uma prova. "O codigo usa recurso
    acima da 0.6" nao e introspectavel do banco — a extensao nao expoe quais dos
    seus simbolos alguem chamou. O que da pra fazer e uma LISTA DE PROIBIDOS dos
    identificadores que so passaram a existir da 0.7 em diante, varrida no SQL e
    no Python do repositorio. Pega o caso realista (alguem escreve `halfvec` num
    schema novo e roda em dev com 0.6 sem perceber); nao pega o exotico.
    Amplie a lista quando um recurso novo entrar em uso.
    """
    instalada = (ref.get("extensions") or {}).get("vector")
    if not instalada:
        return []

    def como_tupla(v: str) -> tuple:
        return tuple(int(p) for p in re.findall(r"\d+", v)[:3])

    alvo = como_tupla(instalada)
    proibidos = {
        ident: minimo
        for ident, minimo in _PGVECTOR_ACIMA_DE_06.items()
        if como_tupla(minimo) > alvo
    }
    if not proibidos:
        return []

    padrao = re.compile(r"\b(" + "|".join(sorted(proibidos)) + r")\b")
    alvos: list[Path] = sorted(raiz.glob("infra/*.sql")) + sorted(
        raiz.glob("infra/migrations/*.sql")
    ) + sorted(raiz.glob("bot/**/*.py"))

    out: list[Finding] = []
    for caminho in alvos:
        try:
            texto = caminho.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, linha in enumerate(texto.splitlines(), start=1):
            for m in padrao.finditer(linha):
                ident = m.group(1)
                out.append(
                    Finding(
                        "pgvector",
                        f"{caminho.relative_to(raiz)}:{n} usa `{ident}`, que exige "
                        f"pgvector >= {proibidos[ident]}, mas a referencia fixa "
                        f"{instalada}. Suba a extensao no ambiente ANTES de mergear, "
                        "ou o codigo quebra so no banco que ficou pra tras.",
                    )
                )
    return out


def compare(ref: dict, tgt: dict, *, scan_repo: bool = True) -> list[Finding]:
    """Todas as divergencias, em ordem de gravidade (ambiente primeiro)."""
    if ref.get("fingerprint_version") != tgt.get("fingerprint_version"):
        return [
            Finding(
                "referencia",
                f"versao da impressao digital incompativel: referencia "
                f"{ref.get('fingerprint_version')}, alvo {tgt.get('fingerprint_version')}"
                " — regenere a referencia com infra/schema_fingerprint.py",
            )
        ]

    achados: list[Finding] = []
    achados += _cmp_ambiente(ref, tgt)
    achados += _cmp_extensoes(ref, tgt)
    achados += _cmp_tabelas(ref, tgt)
    achados += _cmp_colunas(ref, tgt)
    achados += _cmp_objetos(ref, tgt, "indexes", "indice")
    achados += _cmp_objetos(ref, tgt, "constraints", "restricao")
    if scan_repo:
        achados += scan_pgvector(ref)
    return achados


# ── Linha de comando ──────────────────────────────────────────────────────


def load_reference(caminho: Path) -> dict[str, Any]:
    if not caminho.exists():
        raise SystemExit(
            f"erro: referencia ausente em {caminho}.\n"
            "Gere-a a partir de um banco construido pelo runner:\n"
            "  python infra/migrate.py up --database-url <banco-de-apoio>\n"
            "  python infra/schema_fingerprint.py --database-url <banco-de-apoio> "
            f"--out {caminho}"
        )
    return json.loads(caminho.read_text(encoding="utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compat_gate.py",
        description="Portao de compatibilidade de ambiente do Kobe (T4).",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--reference", default=str(DEFAULT_REFERENCE))
    args = parser.parse_args(argv)

    url = (args.database_url or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("erro: alvo ausente — passe --database-url ou defina DATABASE_URL.")

    from infra.schema_fingerprint import from_url

    ref = load_reference(Path(args.reference))
    achados = compare(ref, from_url(url))

    if not achados:
        print("✅ compatibilidade ok — o banco confere com o schema versionado.")
        return 0

    print(f"❌ COMPATIBILIDADE — {len(achados)} divergencia(s):\n")
    for f in achados:
        print(f"  {f}")
    print(
        "\nCada uma destas e uma forma de 'testei em dev' mentir. Concerte o ambiente "
        "ou, se a mudanca for intencional, regenere a referencia com "
        "infra/schema_fingerprint.py."
    )
    return 1


if __name__ == "__main__":
    sys.path.insert(0, str(_PROJECT_ROOT))
    raise SystemExit(main())
