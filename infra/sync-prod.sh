#!/usr/bin/env bash
# Sync seguro dev VPS -> prod VPS do Kobe.
#
# TRAVA: nunca apaga em silêncio. Antes de sincronizar, simula (dry-run) e,
# se o --delete fosse remover arquivos que existem SÓ na produção, PARA e
# lista, exigindo --force-delete explícito.
#
# Causa do incidente de 2026-06-12: um `rsync --delete` apagou da produção
# código que só existia ali (prod-only, fora do git e sem backup). Esta trava
# torna esse cenário impossível de acontecer no escuro.
#
# Uso:
#   bash infra/sync-prod.sh                 # sync seguro (aborta se fosse apagar)
#   bash infra/sync-prod.sh --force-delete  # confirma as deleções e sincroniza
set -euo pipefail

SRC="${KOBE_DEV:-/home/felipe/projetos/kobe}/"
DST="${KOBE_PROD:-/home/felipe/kobe}/"

# Paths que NUNCA atravessam dev -> prod (dados do operador, segredos, runtime,
# repos à parte). Espelha a regra de sync registrada na memória do projeto.
EXCLUDES=(
  --exclude='.git/'
  --exclude='.venv/'
  --exclude='user-data/'     # memória/identidade/dados do operador
  --exclude='.env'
  --exclude='.env.*'
  --exclude='.local/'
  --exclude='**/.local/'
  --exclude='projetos/'      # workspace dinâmico de runtime
  --exclude='plugins/'       # cada plugin é repo próprio (deploy à parte)
  --exclude='__pycache__/'
  --exclude='*.pyc'
)

FORCE=false
[[ "${1:-}" == "--force-delete" ]] && FORCE=true

[[ -d "$SRC" ]] || { echo "ERRO: origem não existe: $SRC" >&2; exit 1; }
[[ -d "$DST" ]] || { echo "ERRO: destino não existe: $DST" >&2; exit 1; }

# 1. Dry-run: o que o --delete removeria da produção?
echo "→ Simulando sync (dry-run) $SRC -> $DST"
DELETIONS=$(rsync -a --delete --dry-run "${EXCLUDES[@]}" "$SRC" "$DST" \
  | grep '^deleting ' | sed 's/^deleting //' || true)

if [[ -n "$DELETIONS" ]] && ! $FORCE; then
  {
    echo ""
    echo "🔴 TRAVA: este sync APAGARIA da produção arquivos que não existem no dev:"
    printf '   - %s\n' $DELETIONS
    echo ""
    echo "Foi exatamente isso que causou o incidente de 2026-06-12."
    echo "  • Se essas deleções são INTENCIONAIS: rode  $0 --force-delete"
    echo "  • Se NÃO são: esses arquivos são prod-only — versione/backupeie ANTES"
    echo "    (vide repo kobe-prod-backup) e investigue por que só existem em prod."
  } >&2
  exit 2
fi

# 2. Sync real
if $FORCE && [[ -n "$DELETIONS" ]]; then
  echo "→ Sync COM deleções confirmadas (--force-delete)."
else
  echo "→ Sync (nenhuma deleção pendente)."
fi
rsync -a --delete "${EXCLUDES[@]}" "$SRC" "$DST"
echo "✓ Sync dev → prod concluído."
