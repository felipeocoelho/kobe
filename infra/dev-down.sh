#!/usr/bin/env bash
# Derruba o ambiente de DESENVOLVIMENTO inteiro num comando: bot, despertador e
# a stack Hindsight de dev.
#
# É a primeira linha do roteiro de emergência: quando a produção precisar da
# máquina inteira, isto libera ~730 MB de RAM sem tocar em nada de produção.
#
# Uso:
#   bash infra/dev-down.sh
#
# ⚠️ A memória durável de dev NÃO é apagada. O `docker compose down` daqui é
#    sem `-v` de propósito: derrubar o ambiente é reversível, apagar o volume
#    não é. Se você realmente quiser zerar a memória de dev, rode o `-v` à mão,
#    conscientemente, conferindo antes que o alvo é `hindsight-dev`.

set -euo pipefail

KOBE_DEV_HOME="${KOBE_DEV_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

echo "[dev-down] ambiente de desenvolvimento em: $KOBE_DEV_HOME"

for unidade in kobe-dev.service keyko-dev.service; do
  if systemctl --user cat "$unidade" >/dev/null 2>&1; then
    echo "[dev-down] parando $unidade..."
    systemctl --user stop "$unidade" || true
  fi
done

HINDSIGHT_DIR="$KOBE_DEV_HOME/infra/hindsight"
if [[ -f "$HINDSIGHT_DIR/.env.dev" ]]; then
  echo "[dev-down] derrubando a stack Hindsight de dev (sem apagar o volume)..."
  # Confere o alvo ANTES de agir. Sem o --env-file o alvo seria a PRODUÇÃO, e
  # esta linha é o que impede que um erro de configuração vire um down na prod.
  alvo="$(cd "$HINDSIGHT_DIR" && sg docker -c \
      "docker compose --env-file .env.dev config" 2>/dev/null | head -1 || true)"
  if [[ "$alvo" != "name: hindsight-dev" ]]; then
    echo "[dev-down] ERRO: o alvo do compose não é a stack de dev (li: '${alvo:-nada}')." >&2
    echo "[dev-down] Abortando sem derrubar nada — conferir .env.dev." >&2
    exit 1
  fi
  ( cd "$HINDSIGHT_DIR" && sg docker -c "docker compose --env-file .env.dev down" )
else
  echo "[dev-down] stack Hindsight de dev não configurada — nada a derrubar."
fi

echo "[dev-down] pronto. A produção não foi tocada."
