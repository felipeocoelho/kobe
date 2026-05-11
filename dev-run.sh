#!/usr/bin/env bash
# Roda o bot Kobe em modo desenvolvimento (sem systemd).
# Pré-requisito: .env preenchido (use .env.example como base).

set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "ERRO: .env não encontrado. Copie .env.example pra .env e preencha." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "[dev-run] criando virtualenv..."
  python3 -m venv .venv
fi

echo "[dev-run] instalando/atualizando dependências..."
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r bot/requirements.txt

set -a
# shellcheck disable=SC1091
source .env
set +a

echo "[dev-run] iniciando bot..."
exec .venv/bin/python -m bot.main
