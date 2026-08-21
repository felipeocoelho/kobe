#!/usr/bin/env bash
# Verificador de portabilidade do Kobe — o repositório de produção é PÚBLICO.
#
# O QUE ELE IMPEDE
# ----------------
# Que caminho absoluto da máquina de um operador reapareça em arquivo versionado
# que vai pro público. Isso não é estética: um `/home/<alguém>/...` cravado num
# doc, num script ou num default de código é **bug de portabilidade** — quem
# clona o repo cai numa pasta que não existe na máquina dele, e o erro aparece
# longe da causa. (Caso real: quatro defaults em `bot/apolo_handlers.py`.)
#
# POR QUE ELE EXISTE COMO TESTE, E NÃO COMO FAXINA DE UMA VEZ
# -----------------------------------------------------------
# Faxina sem trava volta. Este guard roda junto da suíte, então a sujeira é
# barrada no dia em que entra, por quem a escreveu — não dois meses depois,
# por outra pessoa, num release.
#
# ESCOPO: as MESMAS exclusões do `infra/publish.sh`
# -------------------------------------------------
# Só é vazamento o que efetivamente CHEGA ao público. `docs/runbooks/` é
# filtrado no publish (runbook interno de manutenção, cheio de caminho real de
# propósito), então alarmar ali seria alarme falso. As listas precisam andar
# juntas: **mexeu em EXCLUDE_PATHS no publish.sh, revise aqui.**
#
# Rodar sozinho:  bash tests/portability_guard.sh
# Roda na suíte:  tests/test_portability.py (que só invoca este script)
set -u

cd "$(dirname "$0")/.." || exit 2

# Espelho de EXCLUDE_PATHS do infra/publish.sh (o que não vai pro público)
# + este próprio script, que contém os padrões como texto.
EXCLUDES=(
  ':(exclude)docs/runbooks/'          # filtrado no publish (runbook interno)
  ':(exclude)infra/publish.sh'        # ferramenta do mantenedor
  ':(exclude)TESTE_*.md'              # roteiros de teste manual
  ':(exclude)tests/portability_guard.sh'
)

# Placeholders LEGÍTIMOS: são exemplo genérico, e é isso que queremos que exista
# no lugar do caminho real. `/home/x` aparece em fixture de teste como caminho
# fictício. Se você precisar de um placeholder novo, acrescente-o aqui — mas
# acrescente um GENÉRICO, não o nome de alguém.
PLACEHOLDERS='/home/(seu_usuario|usuario|user|voce|you|x|<[a-z_]+>)([/"'"'"'` ]|$)'

fail=0
check() {  # <descrição> <regex-egrep> [regex-permitida]
  local desc="$1" pat="$2" ok_pat="${3:-}" hits
  hits=$(git grep -nE "$pat" -- . "${EXCLUDES[@]}" 2>/dev/null)
  if [ -n "$ok_pat" ] && [ -n "$hits" ]; then
    hits=$(printf '%s\n' "$hits" | grep -vE "$ok_pat" || true)
  fi
  if [ -n "$hits" ]; then
    echo "❌ PORTABILIDADE — $desc:"
    printf '%s\n' "$hits"
    echo ""
    fail=1
  fi
}

check "caminho absoluto de máquina de operador (/home/<usuário>)" \
      '/home/[a-z][a-z0-9_-]*' "$PLACEHOLDERS"

if [ "$fail" -eq 0 ]; then
  echo "✅ portabilidade ok — nenhum caminho de máquina de operador no que vai pro público."
else
  echo "Conserte trocando por placeholder genérico (\$KOBE_HOME, \$KOBE_PROD, ~/kobe," \
       "/home/seu_usuario) ou derivando o caminho em runtime. Em CHANGELOG, troque só o" \
       "caminho — não reescreva o que foi dito."
fi
exit "$fail"
