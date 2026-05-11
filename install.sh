#!/usr/bin/env bash
#
# Kobe — Instalador
#
# Roda os 9 passos do SPEC.md §7. Idempotente: pode ser executado várias
# vezes; só refaz o que faz sentido refazer.
#
set -euo pipefail

KOBE_VERSION="0.1.0"
REPO_URL="https://github.com/felipeocoelho/kobe.git"
LOG_FILE="$HOME/.kobe-install.log"

# Inicializa logging (append, e mantém pasta home como dono)
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

# Variáveis preenchidas durante o fluxo
KOBE_HOME=""
TMP_ENV=""

# ----------------------------------------------------------------------------
# Utilitários
# ----------------------------------------------------------------------------

log() {
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

err() {
  echo "ERRO: $*" >&2
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERRO: $*" >>"$LOG_FILE"
  exit 1
}

confirm() {
  # confirm "Pergunta?" [default Y|N]
  local prompt="$1"
  local default="${2:-N}"
  local response
  read -r -p "$prompt [$default]: " response
  response=${response:-$default}
  [[ "$response" =~ ^[Yy]$ ]]
}

cleanup() {
  if [[ -n "${TMP_ENV:-}" && -f "$TMP_ENV" ]]; then
    rm -f "$TMP_ENV"
  fi
}
trap cleanup EXIT

# ----------------------------------------------------------------------------
# [1/9] Boas-vindas
# ----------------------------------------------------------------------------
print_welcome() {
  cat <<EOF
================================================================
  Kobe — Assistente Pessoal IA via Telegram
  Instalador v${KOBE_VERSION}
================================================================

ANTES DE CONTINUAR, você vai precisar de:

  1. Bot Telegram criado via @BotFather (token em mãos)
  2. Supergrupo Telegram com tópicos habilitados (você como admin)
  3. Conta Supabase com projeto criado:
       - URL e anon key copiados
       - Extensão "vector" habilitada (Database → Extensions)
  4. Conta Groq com API key (https://console.groq.com)
  5. Claude Code instalado e autenticado
     (https://docs.claude.com/en/docs/claude-code/setup)

A instalação dura de 5 a 10 minutos. Você será guiado passo a passo.
O Kobe será instalado como projeto do seu usuário ($USER), em
$HOME/projetos/kobe (você pode customizar).

Log da instalação: $LOG_FILE

================================================================
EOF
  confirm "Continuar com a instalação?" "Y" || { echo "Cancelado."; exit 0; }
}

# ----------------------------------------------------------------------------
# [2/9] SO
# ----------------------------------------------------------------------------
check_os() {
  log "[2/9] Verificando sistema operacional..."
  [[ "$(uname -s)" == "Linux" ]] || err "Kobe só roda em Linux."
  log "OS: $(uname -srm)"
}

# ----------------------------------------------------------------------------
# [3/9] Dependências de sistema
# ----------------------------------------------------------------------------
install_system_deps() {
  log "[3/9] Verificando dependências do sistema..."
  local missing=()
  for cmd in python3 git curl ffmpeg; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done

  # python3-venv não é detectável via 'command -v'; testamos importando
  if command -v python3 >/dev/null 2>&1; then
    if ! python3 -c "import venv" >/dev/null 2>&1; then
      missing+=("python3-venv")
    fi
  fi

  if [[ ${#missing[@]} -eq 0 ]]; then
    log "Dependências OK."
    return
  fi

  log "Faltando: ${missing[*]}"
  if ! command -v apt-get >/dev/null 2>&1; then
    err "Faltam dependências (${missing[*]}) e este sistema não usa apt. Instale manualmente e rode o instalador de novo."
  fi

  if confirm "Instalar agora via apt? (precisa sudo)" "Y"; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip git curl ffmpeg build-essential
  else
    err "Instale manualmente (${missing[*]}) e rode o instalador de novo."
  fi
}

# ----------------------------------------------------------------------------
# [4/9] Claude Code
# ----------------------------------------------------------------------------
check_claude_code() {
  log "[4/9] Verificando Claude Code..."
  if ! command -v claude >/dev/null 2>&1; then
    cat <<EOF

Claude Code não foi encontrado. Instale antes de continuar:

  curl -fsSL https://claude.ai/install.sh | bash

Depois de instalar e autenticar (rode 'claude' uma vez no terminal),
execute o instalador do Kobe novamente.

EOF
    exit 1
  fi
  log "Claude Code OK: $(claude --version 2>&1 | head -1)"
}

# ----------------------------------------------------------------------------
# [5/9] Local de instalação
# ----------------------------------------------------------------------------
choose_install_path() {
  log "[5/9] Configurando local de instalação..."

  if [[ $EUID -eq 0 ]]; then
    cat <<EOF

==================================================================
  ATENÇÃO — Você está rodando como ROOT
==================================================================

Recomendamos FORTEMENTE rodar este instalador como usuário comum
(não-root) e instalar o Kobe no home desse usuário.

Rodar o Kobe como root expõe seu sistema a riscos caso ocorra prompt
injection, comando equivocado ou MCP comprometido.

EOF
    if ! confirm "Tem CERTEZA que quer continuar como root?" "N"; then
      err "Saia da sessão root, logue como seu usuário comum, e rode novamente."
    fi
  fi

  local default_path="$HOME/projetos/kobe"
  local input
  read -r -p "Onde instalar o Kobe? [$default_path]: " input
  KOBE_HOME=${input:-$default_path}

  if [[ -d "$KOBE_HOME" ]]; then
    if [[ -d "$KOBE_HOME/.git" ]]; then
      local existing_origin
      existing_origin=$(git -C "$KOBE_HOME" remote get-url origin 2>/dev/null || echo "")
      if [[ "$existing_origin" != *"kobe"* ]]; then
        err "$KOBE_HOME existe e é um repo Git, mas não parece ser o repo do Kobe (origin: $existing_origin). Mova ou apague antes."
      fi
      log "Diretório já existe e é um clone do Kobe — vai ser atualizado."
    else
      err "$KOBE_HOME já existe e não é um repo Git. Mova ou apague antes."
    fi
  fi

  log "Instalando em: $KOBE_HOME"
  mkdir -p "$(dirname "$KOBE_HOME")"
}

# ----------------------------------------------------------------------------
# [6/9] Credenciais → .env
# ----------------------------------------------------------------------------
collect_credentials() {
  log "[6/9] Coletando credenciais..."
  cat <<EOF

==================================================================
  Credenciais
==================================================================

Você vai colar 5 valores. Eles serão salvos em $KOBE_HOME/.env
com permissão 600 (só seu usuário consegue ler).

EOF

  local tg_token tg_users supa_url supa_key groq_key

  read -r -p "Telegram Bot Token: " tg_token
  [[ -n "$tg_token" ]] || err "Bot token vazio."

  echo "Pra descobrir seu user ID, mande /start pro @userinfobot no Telegram."
  read -r -p "Telegram User IDs permitidos (CSV, ex: 12345,67890): " tg_users
  [[ -n "$tg_users" ]] || err "Lista de user IDs vazia (Kobe ignoraria todo mundo)."

  read -r -p "Supabase URL: " supa_url
  [[ -n "$supa_url" ]] || err "URL do Supabase vazia."

  read -r -p "Supabase Anon Key: " supa_key
  [[ -n "$supa_key" ]] || err "Chave do Supabase vazia."

  read -r -p "Groq API Key: " groq_key
  [[ -n "$groq_key" ]] || err "Groq API key vazia."

  echo ""
  if ! confirm "Claude Code já está autenticado (você rodou 'claude' antes)?" "Y"; then
    cat <<EOF

Rode 'claude' no terminal, faça o login OAuth, e depois execute o
instalador do Kobe novamente.

EOF
    exit 1
  fi

  TMP_ENV=$(mktemp)
  cat >"$TMP_ENV" <<EOF
# Gerado pelo install.sh em $(date +'%Y-%m-%d %H:%M:%S')

# Telegram
TELEGRAM_BOT_TOKEN=$tg_token
TELEGRAM_ALLOWED_USER_IDS=$tg_users

# Supabase
SUPABASE_URL=$supa_url
SUPABASE_KEY=$supa_key

# Groq (transcrição)
GROQ_API_KEY=$groq_key

# Paths
KOBE_HOME=$KOBE_HOME
KOBE_CLAUDE_CWD=$KOBE_HOME

# Config
LOG_LEVEL=INFO
CLAUDE_TIMEOUT_SECONDS=300
RECENT_MESSAGES_LIMIT=20
EOF
  chmod 600 "$TMP_ENV"
  log "Credenciais coletadas."
}

# ----------------------------------------------------------------------------
# [7/9] Clone + virtualenv
# ----------------------------------------------------------------------------
install_kobe() {
  log "[7/9] Clonando repositório..."
  if [[ -d "$KOBE_HOME/.git" ]]; then
    git -C "$KOBE_HOME" pull --ff-only
  else
    git clone "$REPO_URL" "$KOBE_HOME"
  fi

  # Se já existe .env no destino, preserva e oferece sobrescrever
  if [[ -f "$KOBE_HOME/.env" ]]; then
    if confirm ".env já existe em $KOBE_HOME — sobrescrever?" "N"; then
      mv "$TMP_ENV" "$KOBE_HOME/.env"
    else
      log ".env preservado. Os valores novos NÃO foram aplicados."
      rm -f "$TMP_ENV"
    fi
  else
    mv "$TMP_ENV" "$KOBE_HOME/.env"
  fi
  TMP_ENV=""
  chmod 600 "$KOBE_HOME/.env"

  log "Criando virtualenv..."
  if [[ ! -d "$KOBE_HOME/.venv" ]]; then
    python3 -m venv "$KOBE_HOME/.venv"
  fi
  "$KOBE_HOME/.venv/bin/pip" install --upgrade pip
  "$KOBE_HOME/.venv/bin/pip" install -r "$KOBE_HOME/bot/requirements.txt"
  log "Virtualenv pronto."
}

# ----------------------------------------------------------------------------
# [8/9] Banco
# ----------------------------------------------------------------------------
setup_database() {
  log "[8/9] Configurando banco..."
  cat <<EOF

==================================================================
  Schema do banco Supabase
==================================================================

Você precisa rodar o arquivo abaixo no SQL Editor do seu projeto
Supabase (a anon key não tem permissão pra DDL — usar painel web é
mais seguro):

  $KOBE_HOME/infra/schema.sql

PASSOS:
  1. Abra https://app.supabase.com → seu projeto
  2. Menu lateral: Database → Extensions → habilite "vector"
  3. Menu lateral: SQL Editor → New query
  4. Cole o conteúdo de schema.sql e clique em "Run"

EOF
  read -r -p "Pressione ENTER quando tiver rodado o schema..."
}

# ----------------------------------------------------------------------------
# [9/9] systemd --user
# ----------------------------------------------------------------------------
setup_systemd() {
  log "[9/9] Configurando systemd (modo --user)..."

  if ! command -v systemctl >/dev/null 2>&1; then
    log "systemctl não encontrado — pulando setup de serviço."
    log "Pra rodar manualmente: cd $KOBE_HOME && ./dev-run.sh"
    return
  fi

  local service_dir="$HOME/.config/systemd/user"
  mkdir -p "$service_dir"

  local template="$KOBE_HOME/infra/kobe.service.template"
  [[ -f "$template" ]] || err "Template do systemd não encontrado: $template"

  sed "s|{{KOBE_HOME}}|$KOBE_HOME|g" "$template" >"$service_dir/kobe.service"

  systemctl --user daemon-reload
  systemctl --user enable kobe

  if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
    if confirm "Habilitar 'lingering' (Kobe roda mesmo sem você logado)?" "Y"; then
      sudo loginctl enable-linger "$USER"
    fi
  fi

  if confirm "Iniciar o Kobe agora?" "Y"; then
    systemctl --user start kobe
    sleep 2
    systemctl --user status kobe --no-pager || true
  fi
}

# ----------------------------------------------------------------------------
# Resumo final
# ----------------------------------------------------------------------------
print_summary() {
  cat <<EOF

==================================================================
  Instalação concluída
==================================================================

Kobe instalado em: $KOBE_HOME
Rodando como usuário: $USER

COMANDOS ÚTEIS:

  Ver logs:        journalctl --user -u kobe -f
  Reiniciar:       systemctl --user restart kobe
  Parar:           systemctl --user stop kobe
  Status:          systemctl --user status kobe
  Desinstalar:     bash $KOBE_HOME/uninstall.sh

PRÓXIMOS PASSOS:

  1. Adicione seu bot ao supergrupo Telegram com permissão de admin
  2. Habilite "Topics" nas configurações do supergrupo
  3. Mande uma mensagem num tópico pra testar
  4. Edite $KOBE_HOME/memoria/identidade/USER.md com seu contexto

Log da instalação: $LOG_FILE

==================================================================
EOF
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
main() {
  print_welcome
  check_os
  install_system_deps
  check_claude_code
  choose_install_path
  collect_credentials
  install_kobe
  setup_database
  setup_systemd
  print_summary
}

main "$@"
