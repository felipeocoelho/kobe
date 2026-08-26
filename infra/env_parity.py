#!/usr/bin/env python3
"""Compara dois arquivos `.env` pelos NOMES das chaves. Nunca pelos valores.

O PROBLEMA
----------
Com dois ambientes na mesma máquina, os dois `.env` divergem sozinhos: alguém
acrescenta uma chave num lado, esquece do outro, e a diferença só aparece quando
um recurso falha em silêncio semanas depois. Este script torna a divergência
visível em um comando.

A REGRA QUE GOVERNA O DESENHO INTEIRO: **valor nenhum sai daqui.**
-----------------------------------------------------------------
Um `.env` é o arquivo mais sensível da instalação — token do bot, chaves de API,
senha de banco. Uma ferramenta de diagnóstico que imprima, logue ou compare
valores vira o caminho mais curto para um segredo acabar num journal, num
terminal compartilhado ou num anexo de Telegram.

Por isso o parser **descarta o lado direito no ato da leitura**: o valor não
chega a existir como variável nomeada em lugar nenhum deste arquivo. Não é
"cuidado ao imprimir" — é não ter o que imprimir. `tests/test_env_parity.py`
planta um valor reconhecível nos dois lados e assevera que ele não aparece em
nenhuma saída.

USO
---
    python infra/env_parity.py <referência.env> <alvo.env>

Sai com código 1 em qualquer divergência (nos dois sentidos), 0 em paridade. É também importável — `bot/main.py` o chama no start como AVISO,
quando `KOBE_ENV_PARITY_REFERENCE` aponta para um `.env` de referência. Ali ele
**nunca** derruba o start: um bot que não sobe porque falta uma chave opcional
seria remédio pior que a doença.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Uma linha de `.env` que declara chave: nome, `=`, e o que vier depois (que a
# gente joga fora). Comentário e linha em branco não casam. `export FOO=bar`
# também é aceito, porque `.env` de gente costuma ter isso.
_SEPARADOR = "="


def nomes_de_chave(caminho: Path) -> set[str]:
    """Os nomes de chave declarados no arquivo. **O valor é descartado aqui.**

    A fatia da direita do `split` nunca é atribuída a nada — é a diferença
    entre "tomar cuidado com o segredo" e "não ter o segredo em mãos".
    """
    chaves: set[str] = set()
    with caminho.open("r", encoding="utf-8", errors="replace") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha or linha.startswith("#") or _SEPARADOR not in linha:
                continue
            nome = linha.split(_SEPARADOR, 1)[0].strip()
            if nome.startswith("export "):
                nome = nome[len("export ") :].strip()
            if nome:
                chaves.add(nome)
    return chaves


@dataclass(frozen=True)
class Paridade:
    """O resultado da comparação — só nomes, por construção."""

    referencia: Path
    alvo: Path
    faltando_no_alvo: frozenset[str]
    sobrando_no_alvo: frozenset[str]

    @property
    def em_paridade(self) -> bool:
        return not self.faltando_no_alvo and not self.sobrando_no_alvo

    def linhas(self) -> list[str]:
        """Relatório legível. Nomes de chave, nunca valores."""
        if self.em_paridade:
            return ["paridade ok — as duas pontas declaram as mesmas chaves"]
        # Caminho inteiro, não só o nome do arquivo: os dois lados quase sempre
        # se chamam `.env`, e um relatório dizendo "faltam 28 chaves em .env"
        # com "não existem em .env" logo abaixo não diz nada a ninguém.
        out: list[str] = []
        if self.faltando_no_alvo:
            out.append(
                f"faltam {len(self.faltando_no_alvo)} chave(s) em {self.alvo}: "
                + ", ".join(sorted(self.faltando_no_alvo))
            )
        if self.sobrando_no_alvo:
            out.append(
                f"sobram {len(self.sobrando_no_alvo)} chave(s) em {self.alvo} "
                f"(não existem em {self.referencia}): "
                + ", ".join(sorted(self.sobrando_no_alvo))
            )
        return out


def comparar(referencia: Path, alvo: Path) -> Paridade:
    ref = nomes_de_chave(referencia)
    alv = nomes_de_chave(alvo)
    return Paridade(
        referencia=referencia,
        alvo=alvo,
        faltando_no_alvo=frozenset(ref - alv),
        sobrando_no_alvo=frozenset(alv - ref),
    )


def avisar_no_start(referencia: Path, alvo: Path, logger) -> None:
    """Gancho para o start do bot: relata divergência e **nunca** levanta.

    Best-effort de propósito. Arquivo ausente, sem permissão, ilegível — nada
    disso pode impedir o bot de subir; a paridade é diagnóstico, não requisito.
    Falhar aqui trocaria um problema pequeno (uma chave a menos) por um grande
    (bot no chão).
    """
    try:
        resultado = comparar(referencia, alvo)
    except OSError as exc:
        logger.warning(
            "paridade de .env: não deu pra comparar (%s) — seguindo sem o aviso", exc
        )
        return
    if resultado.em_paridade:
        logger.info("paridade de .env: ok contra %s", referencia)
        return
    for linha in resultado.linhas():
        logger.warning("paridade de .env: %s", linha)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        print(f"uso: {argv[0]} <referência.env> <alvo.env>", file=sys.stderr)
        return 2
    referencia, alvo = Path(argv[1]), Path(argv[2])
    for caminho in (referencia, alvo):
        if not caminho.is_file():
            print(f"arquivo não encontrado: {caminho}", file=sys.stderr)
            return 2

    resultado = comparar(referencia, alvo)
    if resultado.em_paridade:
        print(f"✅ paridade ok — {referencia} e {alvo} declaram as mesmas chaves")
        return 0
    for linha in resultado.linhas():
        print(f"⚠️  {linha}")
    print(
        "\n(só nomes de chave são lidos e exibidos — nenhum valor é comparado, "
        "impresso ou registrado)"
    )
    # Qualquer divergência sai != 0, nos dois sentidos. "Sobrando no alvo" é
    # apenas "faltando na referência" visto do outro lado — e costuma ser o caso
    # mais interessante dos dois: chave que ficou pra trás depois de um backend
    # ser removido, apontando pra configuração morta que ninguém limpou.
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
