#!/usr/bin/env bash
# Sobe o stack do Hindsight, espera o serviço responder, e roda o smoke test.
# Highlander Frente 2.2 — EXECUTADO PELO HAL/OPERADOR no PROD VPS (não pela
# sessão Coder). Single-command pra evitar rodar o smoke antes do serviço subir.
#
# Uso (no prod VPS), a partir desta pasta:
#   sg docker -c "bash up_and_smoke.sh"
#
# Pré-requisito: .env preenchido (cp .env.example .env; senha + OPENAI_API_KEY).
# Reversível: ao final, pra apagar tudo -> sg docker -c "docker compose down -v".
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "ERRO: .env não existe. Rode: cp .env.example .env  e preencha"
  echo "  HINDSIGHT_DB_PASSWORD (openssl rand -hex 16) e OPENAI_API_KEY."
  exit 1
fi

echo "== [1/3] subindo o stack (docker compose up -d) =="
docker compose up -d

echo "== [2/3] esperando o serviço responder em 127.0.0.1:8888 (até 120s) =="
ok=0
for i in $(seq 1 60); do
  if curl -fsS -o /dev/null "http://127.0.0.1:8888/openapi.json" 2>/dev/null \
     || curl -fsS -o /dev/null "http://127.0.0.1:8888/" 2>/dev/null; then
    ok=1; echo "  serviço respondeu (tentativa $i)."; break
  fi
  sleep 2
done
if [[ "$ok" -ne 1 ]]; then
  echo "ERRO: serviço não respondeu em 120s. Diagnóstico:"
  docker compose ps
  echo "--- logs hindsight-app (últimas linhas) ---"
  docker compose logs --tail 40 hindsight-app || true
  exit 1
fi

echo "== [3/3] rodando o smoke test =="
python3 smoke_test.py
rc=$?
echo
if [[ "$rc" -eq 0 ]]; then
  echo "SMOKE PASS ✅ — stack funcional. Próximo: avisar o Coder pra seguir pra Frente 2.3."
else
  echo "SMOKE FAIL ❌ (exit $rc) — ver a saída acima e os logs (docker compose logs hindsight-app)."
fi
exit "$rc"
