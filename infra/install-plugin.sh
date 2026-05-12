#!/usr/bin/env bash
#
# Instala um plugin do Kobe a partir de um repo Git.
#
# Uso: bash infra/install-plugin.sh <git-url> [--name <override>]
#
# Sequência:
#  1. Clona o repo numa pasta temporária
#  2. Lê o manifest `kobe-plugin.md` pra obter `name` e `visibility`
#  3. Move o clone pra plugins/<visibility>/<name>/
#  4. Avisa o operador a reiniciar o bot (descoberta de plugins
#     acontece no startup; aqui não conseguimos pingar o processo).
#
# O script NÃO instala dependências Python automaticamente — se o
# plugin declara deps, é responsabilidade do operador decidir se
# adiciona ao venv principal ou cria sandboxing por plugin.

set -euo pipefail

# Descobre KOBE_HOME (assume script rodado de dentro da instalação)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KOBE_HOME="$(dirname "$SCRIPT_DIR")"

GIT_URL="${1:-}"
NAME_OVERRIDE=""

if [[ -z "$GIT_URL" ]]; then
  echo "uso: $0 <git-url> [--name <override>]" >&2
  exit 1
fi

shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME_OVERRIDE="$2"; shift 2 ;;
    *) echo "ERRO: flag desconhecida: $1" >&2; exit 1 ;;
  esac
done

# Clona temporário
TMP_DIR=$(mktemp -d -t kobe-plugin-install-XXXXXX)
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo "→ clonando $GIT_URL em pasta temporária..."
git clone --depth 1 "$GIT_URL" "$TMP_DIR/plugin" >&2

MANIFEST="$TMP_DIR/plugin/kobe-plugin.md"
if [[ ! -f "$MANIFEST" ]]; then
  echo "ERRO: $MANIFEST não encontrado — esse repo não parece um plugin Kobe." >&2
  exit 1
fi

# Extrai name e visibility do frontmatter YAML (parser simples — só
# precisa de duas chaves). Não chamamos Python pra evitar dependência
# do venv; sed/awk dão conta de YAML plano.
extract_field() {
  local field="$1" file="$2"
  # Considera só o primeiro bloco entre '---'. Aceita "name: foo",
  # "name:foo", "name: 'foo'", "name: \"foo\"".
  awk -v field="$field" '
    /^---$/ { count++; if (count == 2) exit; next }
    count == 1 {
      if ($1 == field":" || $1 == field":"  ) {
        $1=""; gsub(/^[ \t]+|[ \t]+$/, ""); gsub(/^["'"'"']|["'"'"']$/, ""); print; exit
      }
    }
  ' "$file"
}

NAME=$(extract_field name "$MANIFEST")
VISIBILITY=$(extract_field visibility "$MANIFEST")

if [[ -n "$NAME_OVERRIDE" ]]; then
  NAME="$NAME_OVERRIDE"
fi

if [[ -z "$NAME" ]]; then
  echo "ERRO: manifest sem campo 'name'." >&2
  exit 1
fi
if [[ "$VISIBILITY" != "public" && "$VISIBILITY" != "private" ]]; then
  echo "ERRO: manifest sem 'visibility' válido (deve ser 'public' ou 'private')." >&2
  exit 1
fi

DEST="$KOBE_HOME/plugins/$VISIBILITY/$NAME"
if [[ -d "$DEST" ]]; then
  read -r -p "Plugin '$NAME' já instalado em $DEST. Sobrescrever? [y/N]: " r
  [[ "$r" =~ ^[Yy]$ ]] || { echo "Cancelado."; exit 0; }
  rm -rf "$DEST"
fi

mkdir -p "$(dirname "$DEST")"
mv "$TMP_DIR/plugin" "$DEST"

echo ""
echo "✅ Plugin '$NAME' instalado em $DEST"
echo ""
echo "Pra ativar:"
echo "  systemctl --user restart kobe   # descoberta acontece no startup"
echo ""
echo "Se o plugin declarou dependências Python no manifest, instale com:"
echo "  $KOBE_HOME/.venv/bin/pip install <pkg1> <pkg2> ..."
