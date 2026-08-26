#!/usr/bin/env bash
# Sobe o ambiente de DESENVOLVIMENTO inteiro num comando: bot, despertador e a
# stack Hindsight de dev.
#
# O ambiente de dev fica normalmente sempre no ar (as unidades são `enabled` no
# boot). Este script existe para o caso oposto: depois de um `dev-down.sh` — a
# primeira linha do roteiro de emergência, quando a produção precisa da máquina
# inteira — é ele que devolve tudo ao ar sem ninguém ter que lembrar da ordem.
#
# Uso:
#   bash infra/dev-up.sh
#
# Onde é o "dev": por padrão, a raiz do repositório de onde este script foi
# chamado. `KOBE_DEV_HOME` sobrescreve. Nenhum caminho de máquina é embutido
# aqui — o repositório é público (ver tests/portability_guard.sh).

set -euo pipefail

KOBE_DEV_HOME="${KOBE_DEV_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

echo "[dev-up] ambiente de desenvolvimento em: $KOBE_DEV_HOME"

# ── Guarda: não subir "dev" apontando pra árvore de produção ──────────────
# Barato, e cobre o erro mais provável de todos — rodar isto da pasta errada.
if [[ ! -f "$KOBE_DEV_HOME/.env" ]]; then
  echo "[dev-up] ERRO: $KOBE_DEV_HOME/.env não existe. Ambiente de dev não configurado." >&2
  exit 1
fi
if ! grep -qE '^\s*KOBE_ENV\s*=\s*dev\s*$' "$KOBE_DEV_HOME/.env"; then
  echo "[dev-up] AVISO: KOBE_ENV=dev não está no .env de $KOBE_DEV_HOME." >&2
  echo "[dev-up] As unidades systemd de dev já forçam KOBE_ENV=dev, então o bot" >&2
  echo "[dev-up] sobe certo — mas comando rodado à mão nessa árvore vai se achar" >&2
  echo "[dev-up] em produção. Vale acrescentar a linha." >&2
fi

# ── Stack Hindsight de dev ────────────────────────────────────────────────
# `--env-file .env.dev` é o que escolhe a stack. SEM ele, o alvo é a PRODUÇÃO.
HINDSIGHT_DIR="$KOBE_DEV_HOME/infra/hindsight"
if [[ -f "$HINDSIGHT_DIR/.env.dev" ]]; then
  echo "[dev-up] subindo a stack Hindsight de dev..."
  ( cd "$HINDSIGHT_DIR" && sg docker -c "docker compose --env-file .env.dev up -d" )
else
  echo "[dev-up] pulando o Hindsight: $HINDSIGHT_DIR/.env.dev não existe."
  echo "[dev-up]   monte com: cp $HINDSIGHT_DIR/.env.dev.example $HINDSIGHT_DIR/.env.dev"
fi

# ── Serviços do usuário ───────────────────────────────────────────────────
# Não há apolo-webhook-dev: o ambiente de desenvolvimento NÃO recebe WhatsApp.
# Um webhook de dev competiria com o de produção pelo mesmo evento da Evolution,
# que tem um chip só.
for unidade in kobe-dev.service keyko-dev.service; do
  if systemctl --user list-unit-files "$unidade" --quiet >/dev/null 2>&1 &&
     systemctl --user cat "$unidade" >/dev/null 2>&1; then
    echo "[dev-up] iniciando $unidade..."
    systemctl --user start "$unidade"
  else
    echo "[dev-up] $unidade não está instalada — pulando."
    echo "[dev-up]   instale a partir de infra/${unidade%.service}.service.template"
  fi
done

echo "[dev-up] pronto. Estado:"
systemctl --user --no-pager --plain list-units 'kobe-dev.service' 'keyko-dev.service' 2>/dev/null || true
