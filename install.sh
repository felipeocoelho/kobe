#!/usr/bin/env bash
#
# Kobe — Instalador
#
# Roda os 9 passos do SPEC.md §7. Idempotente: pode ser executado várias
# vezes; só refaz o que faz sentido refazer.
#
set -euo pipefail

KOBE_VERSION="0.12.0"
REPO_URL="https://github.com/felipeocoelho/kobe.git"
LOG_FILE="$HOME/.kobe-install.log"

# Inicializa logging (append, e mantém pasta home como dono)
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

# Variáveis preenchidas durante o fluxo
KOBE_HOME=""
TMP_ENV=""
REEXEC_TARGET_USER=""  # setado se chamamos via --target-user=X (após re-exec)

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

# Aviso que NÃO aborta. `err` mata a instalação, e há casos (banco divergente,
# schema que dá pra aplicar depois) em que parar tudo seria remédio pior que a
# doença — a pessoa fica sem Kobe por causa de algo que ela conserta em um
# comando.
warn() {
  echo "AVISO: $*" >&2
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] AVISO: $*" >>"$LOG_FILE"
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

parse_flags() {
  for arg in "$@"; do
    case "$arg" in
      --target-user=*) REEXEC_TARGET_USER="${arg#--target-user=}" ;;
    esac
  done
}

create_target_user() {
  local newuser="$1"
  [[ -n "$newuser" ]] || err "Nome de usuário vazio."
  [[ "$newuser" =~ ^[a-z_][a-z0-9_-]*$ ]] || err "Nome inválido: '$newuser' (use a-z, 0-9, _ e -)."
  if id "$newuser" >/dev/null 2>&1; then
    err "Usuário '$newuser' já existe."
  fi
  log "Criando usuário '$newuser'..."
  if [[ $EUID -eq 0 ]]; then
    useradd -m -s /bin/bash "$newuser" || err "Falhou ao criar usuário."
    echo "Defina uma senha pra $newuser:"
    passwd "$newuser"
  else
    sudo useradd -m -s /bin/bash "$newuser" || err "Falhou ao criar usuário (precisa sudo)."
    echo "Defina uma senha pra $newuser:"
    sudo passwd "$newuser"
  fi
  log "Usuário '$newuser' criado."
}

choose_target_user() {
  # Selecionar/criar o usuário que vai rodar o Kobe e, se for diferente
  # do executor atual, re-executar este script como ele.
  log "Configurando usuário do Kobe..."

  local target_user

  if [[ $EUID -eq 0 ]]; then
    cat <<'EOF'

==================================================================
  ATENÇÃO — Você está rodando como ROOT
==================================================================

Não recomendamos rodar o Kobe como root: prompt injection, MCP
comprometido ou comando equivocado têm impacto muito maior.

Vamos rodar o Kobe sob um usuário comum.

EOF
    local choice
    read -r -p "Usar usuário (E)xistente ou (C)riar um novo? [E/C]: " choice
    case "${choice,,}" in
      c|criar|novo)
        read -r -p "Nome do novo usuário: " target_user
        create_target_user "$target_user"
        ;;
      *)
        read -r -p "Nome do usuário existente: " target_user
        [[ -n "$target_user" ]] || err "Nome vazio."
        id "$target_user" >/dev/null 2>&1 || err "Usuário '$target_user' não existe."
        ;;
    esac
  else
    read -r -p "Qual usuário vai rodar o Kobe? [$USER]: " target_user
    target_user=${target_user:-$USER}
    if [[ "$target_user" != "$USER" ]]; then
      if ! id "$target_user" >/dev/null 2>&1; then
        if confirm "Usuário '$target_user' não existe. Criar agora? (precisa sudo)" "Y"; then
          create_target_user "$target_user"
        else
          err "Cancelado."
        fi
      fi
    fi
  fi

  log "Usuário-alvo: $target_user"

  if [[ "$target_user" == "$USER" ]]; then
    return
  fi

  # Linger precisa do executor (sudo/root) — o usuário-alvo pode não estar
  # em sudoers (ex.: criado agora). Habilita aqui antes do re-exec.
  if ! loginctl show-user "$target_user" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
    if [[ $EUID -eq 0 ]]; then
      loginctl enable-linger "$target_user" || log "Aviso: enable-linger falhou."
    else
      sudo loginctl enable-linger "$target_user" || log "Aviso: enable-linger falhou."
    fi
  fi

  # Copia o script pra /tmp pra garantir que o usuário-alvo tem leitura
  # (cobre o caso de instalador baixado em /root ou home restrito).
  local script_path tmp_script
  script_path="$(realpath "$0")"
  tmp_script="$(mktemp /tmp/kobe-install-XXXXXX.sh)"
  cp "$script_path" "$tmp_script"
  chmod a+rx "$tmp_script"

  log "Re-executando o instalador como '$target_user'..."
  echo ""
  echo "================================================================"
  echo "  Trocando contexto pra usuário '$target_user' e continuando."
  echo "  (A partir daqui, todas as operações rodam como $target_user.)"
  echo "================================================================"
  echo ""

  if [[ $EUID -eq 0 ]]; then
    exec runuser -l "$target_user" -- bash "$tmp_script" "--target-user=$target_user"
  else
    exec sudo -H -u "$target_user" bash "$tmp_script" "--target-user=$target_user"
  fi
}

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
  3. PostgreSQL 16+ no ar, e um usuário que possa criar banco nele.
     VOCÊ NÃO PRECISA CRIAR O BANCO — o instalador cria, com a codificação
     e o fuso corretos, e aplica o schema. Você não roda SQL à mão.
       - Extensões necessárias: vector, uuid-ossp, pg_trgm
         (Debian/Ubuntu: apt install postgresql-<versão>-pgvector)
       - Escolha um nome pro banco e monte a string de conexão, ex.:
           postgresql:///kobe                              (socket local)
           postgresql://usuario:senha@localhost:5432/kobe  (TCP)
  4. Conta Groq com API key (https://console.groq.com)
  5. Claude Code instalado e autenticado
     (https://docs.claude.com/en/docs/claude-code/setup)

A instalação dura de 5 a 10 minutos. Você será guiado passo a passo.
Você vai escolher qual usuário do sistema vai rodar o Kobe (default:
o usuário atual, $USER — mas você pode criar um dedicado, especialmente
se estiver instalando como root). O Kobe será instalado em \$HOME/kobe
do usuário escolhido (você pode customizar o path).

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

  local default_path="$HOME/kobe"
  local input
  read -r -p "Onde instalar o Kobe? [$default_path]: " input
  KOBE_HOME=${input:-$default_path}

  # `read` não expande ~; expandimos manualmente pra evitar criar pasta
  # literal "~" no diretório corrente (e systemd recusa ~ em paths).
  case "$KOBE_HOME" in
    "~")     KOBE_HOME="$HOME" ;;
    "~/"*)   KOBE_HOME="$HOME/${KOBE_HOME#\~/}" ;;
    "~"*)    err "Path com ~user (ex: ~outro) não é suportado. Use caminho absoluto." ;;
  esac

  [[ "$KOBE_HOME" = /* ]] || err "Path precisa ser absoluto (começar com /). Recebido: $KOBE_HOME"

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

  local tg_token tg_users database_url groq_key

  read -r -p "Telegram Bot Token: " tg_token
  [[ -n "$tg_token" ]] || err "Bot token vazio."

  echo "Pra descobrir seu user ID, mande /start pro @userinfobot no Telegram."
  read -r -p "Telegram User IDs permitidos (CSV, ex: 12345,67890): " tg_users
  [[ -n "$tg_users" ]] || err "Lista de user IDs vazia (Kobe ignoraria todo mundo)."

  echo ""
  echo "O Kobe fala Postgres direto (psycopg). Informe a string de conexão."
  echo "O banco NÃO precisa existir ainda — se faltar, eu crio."
  echo "  socket unix local, sem senha:  postgresql:///kobe"
  echo "  por TCP, com usuário e senha:  postgresql://usuario:senha@localhost:5432/kobe"
  read -r -p "DATABASE_URL: " database_url
  [[ -n "$database_url" ]] || err "DATABASE_URL vazia."

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

# Banco (Postgres) — a única coisa que diz onde o banco mora.
DATABASE_URL=$database_url

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

  init_user_data
  init_plugin_dirs
}

# Cria as pastas-base de plugins. Os plugins propriamente ditos são
# clonados depois via `infra/install-plugin.sh <git-url>`. Aqui só
# garantimos a árvore esperada pelo bot.
init_plugin_dirs() {
  log "Preparando estrutura de plugins..."
  mkdir -p "$KOBE_HOME/plugins/public" "$KOBE_HOME/plugins/private"
  mkdir -p "$KOBE_HOME/.claude/agents"
  log "  ↳ plugins/{public,private}/ prontos."
}

# Inicializa user-data/ a partir dos templates .example.
# Idempotente: só copia se o destino real ainda não existe (não sobrescreve
# personalizações do operador num upgrade).
init_user_data() {
  log "Inicializando user-data/ a partir dos templates..."
  mkdir -p "$KOBE_HOME/user-data/identity" \
           "$KOBE_HOME/user-data/persona" \
           "$KOBE_HOME/user-data/knowledge"

  local pairs=(
    "user-data/persona/SOUL.md.example:user-data/persona/SOUL.md"
    "user-data/identity/USER.md.example:user-data/identity/USER.md"
    "user-data/identity/PREFERENCES.md.example:user-data/identity/PREFERENCES.md"
  )
  for pair in "${pairs[@]}"; do
    local src="${pair%%:*}"
    local dst="${pair##*:}"
    if [[ -f "$KOBE_HOME/$dst" ]]; then
      log "  ↳ $dst já existe — mantendo personalização do operador."
    elif [[ -f "$KOBE_HOME/$src" ]]; then
      cp "$KOBE_HOME/$src" "$KOBE_HOME/$dst"
      log "  ↳ $dst criado a partir de $src"
    else
      log "  ↳ aviso: $src não encontrado no clone."
    fi
  done
}

# ----------------------------------------------------------------------------
# [8/9] Banco
# ----------------------------------------------------------------------------

# Aplica o schema pelo runner versionado (infra/migrate.py), em vez de mandar
# a pessoa colar SQL num painel web. O runner é idempotente e sabe em que
# versão o banco está — rodar de novo numa instalação em dia é no-op.
setup_database() {
  log "[8/9] Configurando banco..."

  local database_url
  database_url=$(grep -E "^DATABASE_URL=" "$KOBE_HOME/.env" | cut -d= -f2- | tr -d '\r')
  if [[ -z "$database_url" ]]; then
    warn "DATABASE_URL ausente no .env — pulando o schema."
    warn "Aplique depois com: $KOBE_HOME/.venv/bin/python $KOBE_HOME/infra/migrate.py up"
    return
  fi

  # Primeiro CRIAR o banco se ele não existir. O instalador não pressupõe
  # banco pronto: ele provisiona. E provisiona com a codificação, a collation
  # e o fuso da referência versionada — banco criado com o default do initdb
  # nasce divergente, e collation não se troca depois sem recriar.
  if ! "$KOBE_HOME/.venv/bin/python" "$KOBE_HOME/infra/provision_db.py" \
       --database-url "$database_url"; then
    cat <<EOF

==================================================================
  Não consegui preparar o banco
==================================================================

Causas comuns:

  - o servidor PostgreSQL não está no ar, ou a DATABASE_URL aponta
    pro host/porta/usuário errado
  - o usuário da conexão não pode criar banco
    (a mensagem acima diz o SQL exato pra alguém com privilégio rodar)

Depois de resolver, rode os dois:

  $KOBE_HOME/.venv/bin/python $KOBE_HOME/infra/provision_db.py
  $KOBE_HOME/.venv/bin/python $KOBE_HOME/infra/migrate.py up

EOF
    read -r -p "Pressione ENTER quando tiver preparado o banco..."
    return
  fi

  if ! "$KOBE_HOME/.venv/bin/python" "$KOBE_HOME/infra/migrate.py" up \
       --database-url "$database_url"; then
    cat <<EOF

==================================================================
  Não consegui aplicar o schema
==================================================================

O banco existe, mas o runner de migrations falhou. Causas comuns:

  - usuário sem permissão de DDL no banco
  - as extensões "vector", "uuid-ossp" e "pg_trgm" indisponíveis
    (no Debian/Ubuntu: apt install postgresql-<versão>-pgvector)

Depois de resolver, rode:

  $KOBE_HOME/.venv/bin/python $KOBE_HOME/infra/migrate.py up

Para ver em que versão o banco está:

  $KOBE_HOME/.venv/bin/python $KOBE_HOME/infra/migrate.py status

EOF
    read -r -p "Pressione ENTER quando tiver aplicado o schema..."
    return
  fi

  # Portão de compatibilidade (T4): pega collation, fuso, data_checksums e
  # ordem física de coluna divergentes — as coisas que fazem um banco parecer
  # certo e se comportar diferente. Avisa, não bloqueia: um banco recém-criado
  # com collation diferente ainda funciona, só ordena texto de outro jeito.
  if ! "$KOBE_HOME/.venv/bin/python" "$KOBE_HOME/infra/compat_gate.py" \
       --database-url "$database_url"; then
    warn "O banco diverge do schema versionado (veja acima)."
    warn "O Kobe funciona assim, mas ordenação de texto e carimbos de tempo"
    warn "podem se comportar diferente do esperado."
  fi
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
  4. Edite $KOBE_HOME/user-data/identity/USER.md com seu contexto
     (e $KOBE_HOME/user-data/persona/SOUL.md se quiser ajustar a personalidade)

Log da instalação: $LOG_FILE

==================================================================
EOF
}

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
main() {
  parse_flags "$@"

  if [[ -z "$REEXEC_TARGET_USER" ]]; then
    # Phase A — roda como executor (root ou sudoer). Faz as coisas que
    # exigem privilégio (apt-get, useradd, enable-linger), determina o
    # usuário-alvo e — se for diferente — re-executa via runuser/sudo.
    print_welcome
    check_os
    install_system_deps
    choose_target_user
    # Se chegou aqui, target_user == executor: segue inline.
  else
    # Phase B (re-executada como target). Já passamos pelo welcome e
    # pelos passos privilegiados na invocação anterior.
    [[ "$USER" == "$REEXEC_TARGET_USER" ]] \
      || err "Esperava rodar como '$REEXEC_TARGET_USER', mas estou como '$USER'."
    log "Continuando como '$USER' (re-exec)."
  fi

  check_claude_code
  choose_install_path
  collect_credentials
  install_kobe
  setup_database
  setup_systemd
  print_summary
}

main "$@"
