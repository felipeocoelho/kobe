#!/usr/bin/env bash
#
# Kobe — Desinstalador
#
# Remove só o que o instalador criou. Não toca em Claude Code, dependências
# do sistema, dados no Supabase ou no bot do Telegram.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KOBE_HOME="${KOBE_HOME:-$SCRIPT_DIR}"
SERVICE_FILE="$HOME/.config/systemd/user/kobe.service"

cat <<EOF
================================================================
  Kobe — Desinstalador
================================================================

Vai remover:
  - Diretório:        $KOBE_HOME
  - Serviço systemd:  $SERVICE_FILE

NÃO vai remover:
  - Seu usuário Linux ($USER)
  - Claude Code
  - Dependências do sistema (Python, ffmpeg, git, etc.)
  - Dados no Supabase (delete o projeto manualmente se quiser)
  - Bot do Telegram (delete via @BotFather se quiser)
  - Log da instalação ($HOME/.kobe-install.log)

================================================================
EOF

read -r -p "Confirmar desinstalação? [n]: " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo "Cancelado."
  exit 0
fi

# Backup opcional do .env (preserva tokens caso queira reinstalar depois)
if [[ -f "$KOBE_HOME/.env" ]]; then
  read -r -p "Salvar backup do .env em ~/kobe-env-backup-<timestamp>.env? [Y/n]: " bk
  if [[ ! "$bk" =~ ^[Nn]$ ]]; then
    backup_path="$HOME/kobe-env-backup-$(date +%Y%m%d-%H%M%S).env"
    cp "$KOBE_HOME/.env" "$backup_path"
    chmod 600 "$backup_path"
    echo "Backup salvo em: $backup_path"
  fi
fi

# Parar e desabilitar o serviço (se systemctl --user estiver disponível)
if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user is-active --quiet kobe 2>/dev/null; then
    echo "Parando serviço..."
    systemctl --user stop kobe
  fi
  if systemctl --user is-enabled --quiet kobe 2>/dev/null; then
    echo "Desabilitando serviço..."
    systemctl --user disable kobe
  fi
  if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    systemctl --user daemon-reload
  fi
fi

# Sanidade: nunca apagar $HOME ou /, caso a env esteja zoada
case "$KOBE_HOME" in
  ""|"/"|"$HOME")
    echo "ERRO: KOBE_HOME inválido ('$KOBE_HOME'). Não vou apagar o diretório." >&2
    exit 1
    ;;
esac

if [[ -d "$KOBE_HOME" ]]; then
  echo "Removendo $KOBE_HOME..."
  rm -rf "$KOBE_HOME"
fi

echo ""
echo "Kobe desinstalado."
