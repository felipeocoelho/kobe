#!/usr/bin/env bash
# Copia a IDENTIDADE do operador de produção para o ambiente de desenvolvimento.
#
# Só isso: quem o operador é (`identity/`) e quem o agente é (`persona/`). O
# ambiente de dev precisa disso para o agente se comportar como o de verdade;
# não precisa — e não pode ter — mais nada.
#
# Uso:
#   bash infra/sync-identity-dev.sh <origem-prod> <destino-dev>
#   bash infra/sync-identity-dev.sh --dry-run <origem-prod> <destino-dev>
#
# TRÊS REGRAS DURAS, E O PORQUÊ DE CADA UMA
# -----------------------------------------
# 1. **Um sentido só: prod → dev.** O caminho inverso sobrescreveria a identidade
#    real do operador com o que estiver na árvore de testes. O script RECUSA
#    rodar invertido, e a recusa é por conteúdo (procura marcas de produção no
#    destino), não por confiança no que a pessoa digitou.
#
# 2. **Lista BRANCA, nunca lista negra.** Só `identity/` e `persona/` são
#    copiados. A diferença importa no futuro: com lista negra, uma pasta nova em
#    `user-data/` nasceria sendo copiada por omissão — e `user-data/` é onde
#    moram alertas, conversas, sessões do Coder, backups. Com lista branca, o
#    que aparecer amanhã nasce de fora.
#
# 3. **Nunca automático.** Rodado à mão, por decisão, com o que vai ser copiado
#    impresso antes. Sincronizar identidade não é rotina.
#
# `cp`, não `rsync`: aqui não há `--delete` nem espelhamento, e a regra de deploy
# do projeto bane `rsync` — usar outra ferramenta evita até a dúvida.

set -euo pipefail

# As ÚNICAS pastas de user-data/ que atravessam. Acrescentar item aqui é decisão
# consciente; ler esta lista tem que bastar para saber o que o script faz.
LISTA_BRANCA=(identity persona)

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi

if [[ $# -ne 2 ]]; then
  cat >&2 <<'USO'
uso: sync-identity-dev.sh [--dry-run] <origem-prod> <destino-dev>

  <origem-prod>   raiz do Kobe de PRODUÇÃO (a que tem user-data/identity/)
  <destino-dev>   raiz do Kobe de DESENVOLVIMENTO

Copia apenas user-data/identity/ e user-data/persona/. Recusa o sentido inverso.
USO
  exit 2
fi

ORIGEM="$(cd "$1" 2>/dev/null && pwd)" || { echo "origem não existe: $1" >&2; exit 2; }
DESTINO="$(cd "$2" 2>/dev/null && pwd)" || { echo "destino não existe: $2" >&2; exit 2; }

if [[ "$ORIGEM" == "$DESTINO" ]]; then
  echo "ERRO: origem e destino são a mesma árvore." >&2
  exit 2
fi

# ── A trava do sentido ────────────────────────────────────────────────────
# O destino tem que ser reconhecivelmente um ambiente de desenvolvimento. Duas
# evidências independentes servem: `KOBE_ENV=dev` no `.env`, ou a unidade de dev
# instalada. Nenhuma das duas presente = não dá para afirmar que é dev, e a
# resposta certa a "não dá para afirmar" é parar.
destino_e_dev=0
if [[ -f "$DESTINO/.env" ]] && grep -qE '^\s*KOBE_ENV\s*=\s*dev\s*$' "$DESTINO/.env"; then
  destino_e_dev=1
fi
if [[ -f "$DESTINO/infra/kobe-dev.service.template" ]] &&
   systemctl --user cat kobe-dev.service >/dev/null 2>&1; then
  destino_e_dev=1
fi

if [[ "$destino_e_dev" -eq 0 ]]; then
  cat >&2 <<ERRO
ERRO: não consigo afirmar que "$DESTINO" é um ambiente de DESENVOLVIMENTO.

Este script copia identidade num sentido só — prod → dev. Rodá-lo invertido
sobrescreveria a identidade real do operador com a da árvore de testes, e isso
não tem desfazer barato.

Para liberar, o destino precisa de UMA destas evidências:
  - KOBE_ENV=dev no .env do destino; ou
  - a unidade kobe-dev.service instalada no systemd --user.
ERRO
  exit 1
fi

# ── O que vai ser copiado, impresso antes de copiar ───────────────────────
echo "[sync-identity] origem  (prod): $ORIGEM"
echo "[sync-identity] destino (dev):  $DESTINO"
echo "[sync-identity] lista branca:   ${LISTA_BRANCA[*]}"
echo

algo_a_copiar=0
for pasta in "${LISTA_BRANCA[@]}"; do
  de="$ORIGEM/user-data/$pasta"
  if [[ ! -d "$de" ]]; then
    echo "  (ausente na origem, pulando) user-data/$pasta"
    continue
  fi
  algo_a_copiar=1
  echo "  user-data/$pasta/ →"
  find "$de" -type f -printf '      %P\n' 2>/dev/null | head -40
  total=$(find "$de" -type f 2>/dev/null | wc -l)
  [[ "$total" -gt 40 ]] && echo "      ... e mais $((total - 40)) arquivo(s)"
done

if [[ "$algo_a_copiar" -eq 0 ]]; then
  echo "nada a copiar." >&2
  exit 1
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo
  echo "[sync-identity] --dry-run: nada foi copiado."
  exit 0
fi

echo
for pasta in "${LISTA_BRANCA[@]}"; do
  de="$ORIGEM/user-data/$pasta"
  [[ -d "$de" ]] || continue
  para="$DESTINO/user-data/$pasta"
  mkdir -p "$para"
  # -a preserva modo e mtime; o `/.` copia o CONTEÚDO, não a pasta dentro da
  # pasta. Sem --delete: o que existir só no dev fica onde está.
  cp -a "$de/." "$para/"
  echo "[sync-identity] copiado: user-data/$pasta"
done

echo "[sync-identity] pronto. Nada foi copiado no sentido inverso, e nada foi apagado."
