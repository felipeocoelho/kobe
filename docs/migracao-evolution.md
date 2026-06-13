# Runbook — migração WPPConnect → Evolution API v2.3.7

> Estado preparado em 2026-05-29 (madrugada). Quando você voltar, este runbook
> te leva do "stack pronto" ao "Evolution em produção" sem precisar do Claude
> do lado. Cada passo tem um esperado claro e um plano B.

## TL;DR

Toda a infra, código e config estão prontos. Só falta:

1. **Parear o QR code da Evolution** (passo M3-M4).
2. **Smoke test 30 min idle** (passo M5).
3. **Cutover via env** (passo M6) — 1 linha no `.env`.
4. **Restart serviços** (passo M7).

Total de tempo do operador: ~10 min ativos + 30 min de espera idle.

---

## Visão geral do que já foi feito

| # | Mudança | Status |
|---|---|---|
| 1 | `infra/evolution/docker-compose.yml` (Evolution v2.3.7 + Postgres + Redis) | ✅ criado |
| 2 | `infra/evolution/.env` populado com `AUTHENTICATION_API_KEY` (32 hex) e `POSTGRES_PASSWORD` (32 hex) | ✅ criado |
| 3 | Stack subido em background, `evolution-api` healthy | ✅ rodando |
| 4 | `lib/backend/evolution.py` adapter (status, send_text, send_media, list_groups, get_group) | ✅ escrito |
| 5 | `scripts/webhook_server.py` adaptado pra detectar formato Evolution OU WPPConnect (parser duplo) | ✅ atualizado |
| 6 | `lib/backend/__init__.py` selector com branch `evolution` | ✅ atualizado |
| 7 | Plugin Apolo bumpado para v0.2.0 + CHANGELOG | ✅ feito |
| 8 | README Apolo: status v0.2.0 | ✅ feito |
| 9 | `infra/wppconnect/docker-compose.yml` marcado como LEGADO (banner standby) | ✅ feito |
| 10 | `.env` dev e prod: bloco Apolo refeito com Evolution vars + WPPConnect mantido ATIVO em standby pra cutover seguro | ✅ feito |

**Nada foi restartado.** `kobe.service` e `apolo-webhook.service` seguem com env antigo em memória — você decide o momento do cutover.

---

## M1 — Pré-checks (3 min)

Antes de mexer, valide que está tudo de pé.

```bash
# Stack Evolution viva?
sg docker -c "docker ps --filter name=evolution --format 'table {{.Names}}\t{{.Status}}'"
# esperado: evolution-api, evolution-postgres, evolution-redis — todos "Up X (healthy)"

# Evolution responde?
curl -sS http://127.0.0.1:8080/ | jq .
# esperado: {"version":"2.3.7", "whatsappWebVersion": "2.3000.10403..."}

# WPPConnect ainda vivo (rollback disponível)?
sg docker -c "docker ps --filter name=wppconnect --format 'table {{.Names}}\t{{.Status}}'"
# esperado: wppconnect — Up X (healthy) — pode estar dropado de sessão, mas o container OK
```

Se algum container caiu:

```bash
# subir Evolution
cd /home/felipe/projetos/kobe/infra/evolution && sg docker -c "docker compose up -d"

# subir WPPConnect (só se for fazer rollback)
cd /home/felipe/projetos/kobe/infra/wppconnect && sg docker -c "docker compose up -d --build"
```

---

## M2 — Criar instância "kobe" na Evolution (1 min)

A instância concentra a conexão WhatsApp + config de proxy + webhook. Já está
desenhado pra usar o **mesmo proxy IPRoyal** que validamos no WPPConnect.

```bash
cd /home/felipe/projetos/kobe

# Carrega secrets locais sem ecoar
APIKEY=$(grep ^AUTHENTICATION_API_KEY infra/evolution/.env | cut -d= -f2)
WHSEC=$(grep ^APOLO_WEBHOOK_SECRET .env | cut -d= -f2)

# Lê IPRoyal (host:port:user:pass) sem ecoar — passa só via variáveis locais
PROXY_LINE=$(grep ^IPROYAL_CREDENTIALS= .env.test.local | cut -d= -f2 | tr -d '"' | tr -d "'")
PHOST=$(echo "$PROXY_LINE" | cut -d: -f1)
PPORT=$(echo "$PROXY_LINE" | cut -d: -f2)
PUSER=$(echo "$PROXY_LINE" | cut -d: -f3)
PPASS=$(echo "$PROXY_LINE" | cut -d: -f4)

# Cria instância "kobe" com proxy BR + webhook por instância (header X-Apolo-Secret)
curl -sS -X POST http://127.0.0.1:8080/instance/create \
  -H "apikey: $APIKEY" -H "Content-Type: application/json" \
  -d "{
    \"instanceName\": \"kobe\",
    \"integration\": \"WHATSAPP-BAILEYS\",
    \"qrcode\": true,
    \"rejectCall\": false,
    \"groupsIgnore\": false,
    \"alwaysOnline\": false,
    \"readMessages\": false,
    \"readStatus\": false,
    \"syncFullHistory\": false,
    \"proxyHost\": \"$PHOST\",
    \"proxyPort\": \"$PPORT\",
    \"proxyProtocol\": \"http\",
    \"proxyUsername\": \"$PUSER\",
    \"proxyPassword\": \"$PPASS\",
    \"webhook\": {
      \"url\": \"http://host.docker.internal:8787/wa-webhook\",
      \"webhookByEvents\": false,
      \"webhookBase64\": false,
      \"headers\": {\"X-Apolo-Secret\": \"$WHSEC\"},
      \"events\": [\"MESSAGES_UPSERT\", \"CONNECTION_UPDATE\", \"QRCODE_UPDATED\"]
    }
  }" | jq .
```

**Esperado:** JSON com `instance.instanceName: "kobe"`, `instance.status: "connecting"`, e talvez `qrcode.base64` ou `qrcode.code`.

**Se 409 (já existe):** instância foi criada em tentativa anterior. Apaga:

```bash
curl -sS -X DELETE http://127.0.0.1:8080/instance/delete/kobe -H "apikey: $APIKEY" | jq .
# e roda o POST acima de novo
```

---

## M3 — Pegar o QR e parear (2 min)

```bash
APIKEY=$(grep ^AUTHENTICATION_API_KEY infra/evolution/.env | cut -d= -f2)
mkdir -p .local/qr

# Gera QR (base64 ou string)
QR_RESPONSE=$(curl -sS -H "apikey: $APIKEY" http://127.0.0.1:8080/instance/connect/kobe)
echo "$QR_RESPONSE" | jq 'keys'   # pra ver o que veio (base64? code? pairingCode?)

# Caminho A — se vier campo `base64` (PNG)
B64=$(echo "$QR_RESPONSE" | jq -r '.base64 // empty' | sed 's|^data:image/png;base64,||')
if [ -n "$B64" ] && [ "$B64" != "null" ]; then
  TS=$(date '+%Y%m%d_%H%M%S')
  OUT=".local/qr/evolution_qr_${TS}.png"
  echo "$B64" | base64 -d > "$OUT"
  code "$OUT"
  echo "QR PNG aberto no VS Code: $OUT (válido ~50s)"
else
  # Caminho B — só veio `code` (string raw do QR). Imprime no terminal via Python.
  RAW=$(echo "$QR_RESPONSE" | jq -r '.code // empty')
  if [ -n "$RAW" ]; then
    /home/felipe/kobe/.venv/bin/python3 -c "
import sys
try:
    import qrcode
except ImportError:
    import subprocess; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', 'qrcode'])
    import qrcode
q = qrcode.QRCode(border=1); q.add_data('$RAW'); q.make()
q.print_ascii(invert=True)
"
  else
    echo "Resposta inesperada do QR — veja $QR_RESPONSE"
  fi
fi
```

**No celular:** WhatsApp Business → ⋮ → **Aparelhos conectados** → **Conectar um aparelho** → escaneia.

> Se o QR vencer antes de você abrir a câmera, é só rodar de novo o bloco `curl`+`code`.

---

## M4 — Confirmar conexão (1 min)

```bash
APIKEY=$(grep ^AUTHENTICATION_API_KEY infra/evolution/.env | cut -d= -f2)
curl -sS -H "apikey: $APIKEY" http://127.0.0.1:8080/instance/connectionState/kobe | jq .
# esperado: {"instance": {"instanceName": "kobe", "state": "open"}}
```

`state: open` = conectado. Marque o relógio — esse é o T0 do smoke.

Confirme também que pegou seu pushName/número:

```bash
curl -sS -H "apikey: $APIKEY" "http://127.0.0.1:8080/instance/fetchInstances?instanceName=kobe" | jq '.[0] | {ownerJid, profileName, status}'
```

---

## M5 — Smoke test 30 min idle (espera)

A regra do handoff anterior era 30 min sem `disconnectedMobile`. Aqui o evento
equivalente seria `state → close` espontâneo, ou eventos `CONNECTION_UPDATE`
reportando perda. Vou monitorar via log do container e re-polling de status.

```bash
LOG=/home/felipe/projetos/kobe/.local/logs/evolution-pareamento-$(date '+%Y%m%d_%H%M%S').log
mkdir -p "$(dirname "$LOG")"
echo "Monitor iniciado em $(date '+%H:%M:%S')" > "$LOG"
sg docker -c "docker logs -f --since 30s evolution-api 2>&1" \
  | grep --line-buffered -iE "disconnect|connection|qr|baileys|error|state" >> "$LOG" &
echo "PID=$!"
echo "Tail com: tail -F $LOG"
```

A cada 5 min, faça um status check:

```bash
curl -sS -H "apikey: $APIKEY" http://127.0.0.1:8080/instance/connectionState/kobe | jq .instance.state
```

**Critério de PASS:** 30 min consecutivos com `state: open`, sem queda no log.
Se cair, anota o tempo e o evento — me chama com isso.

> Se quiser acelerar o teste, mande uma mensagem do seu chip pessoal pro chip
> da Kobe — quando chegar, o `apolo-webhook.service` (mesmo com env antigo) vai
> tentar processar via parser dual. Se aparecer no `journalctl --user -u apolo-webhook -f` uma linha "backend=evolution", o webhook está OK também.

---

## M6 — Cutover (1 min)

Quando o smoke passar, edite **dois** arquivos `.env` (dev e prod):

```bash
# Em /home/felipe/projetos/kobe/.env  E  /home/felipe/kobe/.env:
# - comentar:    APOLO_BACKEND=wppconnect
# - descomentar: APOLO_BACKEND=evolution
```

Comando direto (faz dev e prod):

```bash
for ENV in /home/felipe/projetos/kobe/.env /home/felipe/kobe/.env; do
  sed -i 's|^APOLO_BACKEND=wppconnect|# APOLO_BACKEND=wppconnect|' "$ENV"
  sed -i 's|^# APOLO_BACKEND=evolution|APOLO_BACKEND=evolution|' "$ENV"
  echo "=== $ENV ==="
  grep '^APOLO_BACKEND\|^# APOLO_BACKEND' "$ENV"
done
```

**Esperado** depois:

```
APOLO_BACKEND=evolution
# APOLO_BACKEND=wppconnect
```

---

## M7 — Restart serviços (30s + downtime curto)

```bash
systemctl --user restart apolo-webhook
systemctl --user restart kobe
sleep 2
systemctl --user status kobe apolo-webhook --no-pager | head -20
```

Restart do `kobe.service` faz o bot do Telegram piscar por ~2-5s. Aceitável.

---

## M8 — Validação pós-cutover (3 min)

Smoke funcional do Apolo no Telegram:

1. No tópico **General** ou **Dev Kobe** do seu Telegram, mande:
   > "manda 'teste pós-migração Evolution' pro meu próprio número"

2. Aguarde resposta do Hal — deve confirmar envio.

3. No celular, confira que a mensagem chegou.

4. Resposta inversa: do celular (qualquer chat externo) → mande algo
   pro chip da Kobe.

5. No banco Supabase, confira que entrou:
   ```sql
   select id, direcao, jid_chat, conteudo, metadata->>'backend', timestamp
   from whatsapp_messages
   order by timestamp desc limit 5;
   ```
   Deve aparecer linhas com `metadata.backend = "evolution"`.

---

## M9 — Parar WPPConnect (opcional, libera ~200MB RAM)

Só faça depois de M8 passar e você ter usado a Evolution por algumas horas
sem incidente:

```bash
cd /home/felipe/projetos/kobe/infra/wppconnect
sg docker -c "docker compose down"
# volumes wppconnect-tokens, wppconnect-userdata MANTIDOS — rollback ainda possível
```

---

## Rollback (caso a Evolution dê pau no uso real)

```bash
# 1. Subir WPPConnect (se foi paro)
cd /home/felipe/projetos/kobe/infra/wppconnect
sg docker -c "docker compose up -d --build"

# 2. .env: voltar APOLO_BACKEND pra wppconnect
for ENV in /home/felipe/projetos/kobe/.env /home/felipe/kobe/.env; do
  sed -i 's|^APOLO_BACKEND=evolution|# APOLO_BACKEND=evolution|' "$ENV"
  sed -i 's|^# APOLO_BACKEND=wppconnect|APOLO_BACKEND=wppconnect|' "$ENV"
done

# 3. Restart
systemctl --user restart apolo-webhook kobe

# 4. Re-parear WPPConnect (procedimento antigo — vide handoff
#    .local/handoff-wppconnect-session-instability.md)
```

---

## Diferenças que você vai notar entre os backends

| | WPPConnect | Evolution |
|---|---|---|
| Aparelho conectado mostra | "wppconnect-server" | "Kobe Apolo" (definido em `CONFIG_SESSION_PHONE_CLIENT`) |
| Stack subjacente | wa-js + Chromium (~600MB RAM) | Baileys nativo (~150MB RAM) |
| Versão do WhatsApp Web | tentava `2.3000.10305x` (caía) | reconhece `2.3000.10403...` (estável) |
| Webhook header | só query `?secret=` (limitação WPPConnect) | `X-Apolo-Secret` header (Evolution suporta) |
| Mídia inbound | base64 inline no `body` | URL — `webhook_server` baixa via `/chat/getBase64FromMediaMessage` |

O subagente Apolo e os CLIs (`send.py`, `grupos_buscar.py`, etc.) **não mudam** —
adapter pattern absorve tudo isso.

---

## Próximos passos depois da migração estável

- Atualizar `user-data/knowledge/kobe/plugins/apolo.md` com data da migração e
  observações reais de uso.
- Considerar parar definitivamente o WPPConnect (M9) após 1 semana sem incidente.
- Importar contatos do Google vCard (`user-data/imports/google_vcard-2026-05-28.md` —
  arquivo existente, ~21 contatos curados — pendente de promover).
- Documentar no `docs/sysadmin.md` ou equivalente que a Evolution é o backend
  default daqui pra frente.
