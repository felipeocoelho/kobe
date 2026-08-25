#!/usr/bin/env python3
"""Decomissiona o acervo de WhatsApp que o Kobe guardava — com backup obrigatório.

CONTEXTO (2026-08-24)
---------------------
O Kobe mantinha uma cópia de tudo que passava no WhatsApp do operador: a tabela
`whatsapp_messages` no Supabase e os binários de mídia em disco. Isso era
herança do backend WPPConnect, que não tinha banco nenhum. Desde a migração pra
Evolution API (30/05/2026) a cópia virou redundância — a Evolution guarda tudo,
inclusive o que o Kobe envia. O plugin apolo v0.3.0 parou de escrever aqui;
este script apaga o que ficou pra trás.

A REGRA QUE GOVERNA ESTE SCRIPT
-------------------------------
    "não existe a menor possibilidade da gente fazer isso sem backup.
     Aliás, não existe a menor possibilidade de fazer qualquer coisa
     que não seja reversível."   — o operador

Por isso o backup não é uma etapa opcional aqui: é **pré-requisito bloqueante**.
O script se recusa a apagar qualquer coisa sem antes ter (a) dumpado a tabela
pra disco e (b) **conferido a contagem de linhas do arquivo contra o banco**.
Contagem que não bate = aborta, sem apagar nada.

O QUE ELE FAZ, NESTA ORDEM
--------------------------
    1. Dump da tabela  → <backup>/whatsapp_messages.jsonl.gz   (+ conferência)
    2. Mídia MOVIDA    → <backup>/midia/                       (mv, não cp)
    3. Linhas apagadas → em lotes, com contagem antes/depois
    4. Conferência final (inclui checar se algo voltou a escrever na tabela)

A mídia é **movida**, não copiada: é `mv` no mesmo filesystem, então é
instantâneo e não exige o dobro do espaço. Também é o que torna o passo 2
reversível por um `mv` de volta.

A estrutura da tabela (DDL) **não** é removida aqui — keys REST do Supabase não
rodam DDL. Isso é `infra/migrations/004_remove_whatsapp_messages.sql`, pra colar
no SQL Editor depois que este script terminar.

MODO DE USO
-----------
    # 1. Sempre comece pelo ensaio (é o default — não escreve nada):
    .venv/bin/python infra/decommission_whatsapp_acervo.py

    # 2. Só o backup, sem apagar nada (dá pra parar aqui e decidir depois):
    .venv/bin/python infra/decommission_whatsapp_acervo.py --so-backup

    # 3. Pra valer (backup + mover mídia + apagar linhas):
    .venv/bin/python infra/decommission_whatsapp_acervo.py --executar

⚠️  O Supabase é o MESMO banco de dev e de produção. `--executar` atinge a
    produção na hora. Rode com o operador ciente.

COMO DESFAZER
-------------
Enquanto a pasta de backup existir, dá pra voltar:
  - mídia: `mv <backup>/midia/* $KOBE_HOME/user-data/whatsapp/midia/`
  - linhas: reimportar o `.jsonl.gz` (a tabela precisa existir — se a migration
    004 já rodou, recrie-a a partir do bloco no histórico do `infra/schema.sql`).
Depois que a pasta de backup for apagada, não há volta.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

TABELA = "whatsapp_messages"
PAGINA = 1000          # PostgREST limita resposta a 1000 linhas por request
LOTE_DELETE = 200      # ids por request de DELETE (URL do PostgREST tem limite)


# ---------------------------------------------------------------------------
# Infra
# ---------------------------------------------------------------------------


def kobe_home() -> Path:
    """Raiz do Kobe: `$KOBE_HOME` se apontar pra uma raiz de verdade, senão
    deriva da localização deste arquivo (`<raiz>/infra/este.py`). Nunca há
    caminho de máquina como default."""
    bruto = (os.environ.get("KOBE_HOME") or "").strip()
    if bruto:
        cand = Path(bruto).expanduser().resolve()
        if (cand / "bot").is_dir():
            return cand
    derivado = Path(__file__).resolve().parent.parent
    if (derivado / "bot").is_dir():
        return derivado
    sys.exit("erro: não consegui resolver a raiz do Kobe. Defina KOBE_HOME.")


def cliente_supabase():
    try:
        from supabase import create_client
    except ImportError:
        sys.exit("erro: rode com o venv do Kobe (.venv/bin/python).")
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        sys.exit("erro: SUPABASE_URL/SUPABASE_KEY ausentes no env. Carregue o .env do Kobe.")
    return create_client(url, key)


def progresso(msg: str) -> None:
    """Linha de progresso. Em terminal, reescreve a mesma linha; num log/pipe,
    imprime a cada bloco — `\r` em arquivo vira lixo numa linha só."""
    if sys.stdout.isatty():
        print(f"    ... {msg}", end="\r", flush=True)
    else:
        print(f"    ... {msg}", flush=True)


def fim_progresso() -> None:
    if sys.stdout.isatty():
        print(" " * 60, end="\r")


def humano(n_bytes: int) -> str:
    for unidade in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024 or unidade == "GB":
            return f"{n_bytes:.1f} {unidade}"
        n_bytes /= 1024.0
    return f"{n_bytes:.1f} GB"


def tamanho_dir(p: Path) -> tuple[int, int]:
    """(quantidade de arquivos, bytes) de um diretório. (0, 0) se não existe."""
    if not p.is_dir():
        return 0, 0
    arquivos = [f for f in p.rglob("*") if f.is_file()]
    return len(arquivos), sum(f.stat().st_size for f in arquivos)


def contar_linhas(sb) -> int:
    return sb.table(TABELA).select("id", count="exact").limit(1).execute().count or 0


# ---------------------------------------------------------------------------
# Etapa 1 — backup da tabela, com conferência
# ---------------------------------------------------------------------------


def dump_tabela(sb, destino: Path, esperado: int) -> int:
    """Dumpa a tabela inteira em JSONL comprimido. Devolve quantas linhas gravou.

    Pagina de 1000 em 1000 porque o PostgREST não devolve mais que isso por
    request — pedir `.limit(20000)` devolve 1000 e mente por omissão.
    """
    gravadas = 0
    with gzip.open(destino, "wt", encoding="utf-8") as fh:
        offset = 0
        while True:
            linhas = sb.table(TABELA).select("*").range(offset, offset + PAGINA - 1).execute().data
            if not linhas:
                break
            for linha in linhas:
                fh.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
                gravadas += 1
            progresso(f"{gravadas}/{esperado} linhas")
            offset += PAGINA
    fim_progresso()
    return gravadas


def conferir_dump(arquivo: Path, esperado: int) -> int:
    """Reconta as linhas LENDO o arquivo gravado — não confia no contador da
    escrita. É esta conferência que autoriza apagar."""
    lidas = 0
    with gzip.open(arquivo, "rt", encoding="utf-8") as fh:
        for linha in fh:
            if linha.strip():
                json.loads(linha)   # se estiver corrompido, estoura aqui
                lidas += 1
    return lidas


# ---------------------------------------------------------------------------
# Etapa 3 — apagar as linhas
# ---------------------------------------------------------------------------


def apagar_linhas(sb, total: int) -> int:
    """Apaga em lotes de ids. Devolve quantas apagou.

    A trava do `lote_anterior`: se o DELETE não tiver efeito — permissão/RLS
    negando em silêncio, por exemplo — o SELECT seguinte devolve exatamente os
    mesmos ids, e sem esta guarda o laço rodaria para sempre contra a produção,
    imprimindo progresso falso. Ver o mesmo lote duas vezes = aborta.
    """
    apagadas = 0
    lote_anterior: list[str] | None = None
    while True:
        ids = [r["id"] for r in sb.table(TABELA).select("id").limit(LOTE_DELETE).execute().data]
        if not ids:
            break
        if ids == lote_anterior:
            fim_progresso()
            sys.exit(
                f"ABORTADO — o DELETE não teve efeito: os mesmos {len(ids)} ids voltaram "
                f"na segunda tentativa (apagadas até aqui: {apagadas}). Provável falta de "
                "permissão de escrita da chave do Supabase. O backup está intacto."
            )
        sb.table(TABELA).delete().in_("id", ids).execute()
        apagadas += len(ids)
        lote_anterior = ids
        progresso(f"{apagadas}/{total} linhas apagadas")
    fim_progresso()
    return apagadas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        description="Decomissiona o acervo de WhatsApp do Kobe (backup obrigatório antes).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--executar", action="store_true",
                   help="Executa de verdade. Sem esta flag, é só ensaio (não escreve nada).")
    p.add_argument("--so-backup", action="store_true",
                   help="Faz o backup e para — não move mídia nem apaga linha.")
    p.add_argument("--backup-dir",
                   help="Pasta de backup. Default: <kobe>/user-data/backups/whatsapp-<hoje>")
    args = p.parse_args()

    raiz = kobe_home()
    midia_dir = Path(os.environ.get("APOLO_MIDIA_DIR") or (raiz / "user-data" / "whatsapp" / "midia"))
    backup_dir = Path(args.backup_dir) if args.backup_dir else (
        raiz / "user-data" / "backups" / f"whatsapp-{date.today().isoformat()}"
    )
    dump_path = backup_dir / f"{TABELA}.jsonl.gz"
    ensaio = not args.executar

    sb = cliente_supabase()
    total_linhas = contar_linhas(sb)
    n_midia, bytes_midia = tamanho_dir(midia_dir)

    print("=" * 72)
    print("DECOMISSIONAMENTO DO ACERVO DE WHATSAPP" + ("  [ENSAIO — nada será escrito]" if ensaio else "  [EXECUÇÃO REAL]"))
    print("=" * 72)
    print(f"  tabela ............ {TABELA}: {total_linhas} linhas")
    print(f"  mídia ............. {midia_dir}")
    print(f"                      {n_midia} arquivos, {humano(bytes_midia)}")
    print(f"  backup vai pra .... {backup_dir}")
    print()

    if total_linhas == 0 and n_midia == 0:
        print("Nada a fazer: tabela vazia e sem mídia em disco.")
        return 0

    if ensaio:
        print("O que a execução real faria, nesta ordem:")
        print(f"  1. dump das {total_linhas} linhas em {dump_path.name} e CONFERÊNCIA da contagem")
        print(f"  2. mover {n_midia} arquivos ({humano(bytes_midia)}) pra {backup_dir / 'midia'}")
        if not args.so_backup:
            print(f"  3. apagar as {total_linhas} linhas da tabela")
            print("  4. conferir que sobrou 0 e que nada voltou a escrever")
        print()
        print("Nada foi tocado. Pra valer: adicione --executar")
        return 0

    # ---------------- execução real ----------------
    backup_dir.mkdir(parents=True, exist_ok=True)
    if dump_path.exists():
        sys.exit(f"abortado: {dump_path} já existe. Escolha outro --backup-dir "
                 "(não sobrescrevo backup por engano).")

    print(f"[1/4] dump de {total_linhas} linhas → {dump_path}")
    gravadas = dump_tabela(sb, dump_path, total_linhas)
    print(f"      gravadas: {gravadas} linhas ({humano(dump_path.stat().st_size)} comprimido)")

    print("[1/4] conferindo o arquivo (relendo, não confiando no contador)...")
    lidas = conferir_dump(dump_path, total_linhas)
    if lidas != total_linhas:
        sys.exit(f"ABORTADO — backup não confere: banco tem {total_linhas} linhas, "
                 f"arquivo tem {lidas}. NADA foi apagado. Investigue antes de tentar de novo.")
    print(f"      ✓ backup confere: {lidas} linhas no arquivo = {total_linhas} no banco")

    if n_midia:
        destino_midia = backup_dir / "midia"
        print(f"[2/4] movendo {n_midia} arquivos ({humano(bytes_midia)}) → {destino_midia}")
        shutil.move(str(midia_dir), str(destino_midia))
        midia_dir.mkdir(parents=True, exist_ok=True)   # deixa a pasta vazia no lugar
        n_dep, bytes_dep = tamanho_dir(destino_midia)
        if n_dep != n_midia:
            sys.exit(f"ABORTADO — mídia não confere: {n_midia} antes, {n_dep} depois. "
                     "Nenhuma linha foi apagada.")
        print(f"      ✓ {n_dep} arquivos no backup, origem vazia")
    else:
        print("[2/4] sem mídia em disco — nada a mover")

    if args.so_backup:
        print()
        print(f"Backup pronto em {backup_dir}. Nada foi apagado (--so-backup).")
        print("Pra apagar depois, rode de novo com --executar e --backup-dir apontando pra cá.")
        return 0

    print(f"[3/4] apagando {total_linhas} linhas...")
    apagadas = apagar_linhas(sb, total_linhas)
    print(f"      apagadas: {apagadas}")

    print("[4/4] conferência final")
    restante = contar_linhas(sb)
    print(f"      linhas na tabela agora: {restante}")
    if restante:
        print(f"      ⚠️  sobraram {restante} linhas. Se o número CRESCE a cada execução,")
        print("          algo ainda está escrevendo — provavelmente um apolo-webhook")
        print("          antigo em produção. Faça o deploy do plugin v0.3.0 e repita.")
    else:
        print("      ✓ tabela vazia")

    print()
    print("=" * 72)
    print(f"Backup preservado em: {backup_dir}")
    print("PRÓXIMO PASSO (opcional, remove a estrutura da tabela):")
    print("  colar infra/migrations/004_remove_whatsapp_messages.sql no SQL Editor do Supabase")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
