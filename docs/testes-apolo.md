# Manual de testes — Plugin Apolo (WhatsApp)

> Estilo "runbook + execução guiada" (vide `feedback_runbooks`): cada cenário
> é uma sequência clara de passos pro operador rodar e validar o resultado
> visualmente. Não é teste automatizado.

**Pré-requisitos** (todos validados antes de começar):

- `feature/apolo` mergeada (ou rodando) no dev VPS
- Stack WPPConnect rodando (`docker compose ps` em `infra/wppconnect/` mostra
  containers `wppconnect` healthy)
- Sessão `kobe` pareada (`curl http://127.0.0.1:21465/api/kobe/status-session`
  retorna `CONNECTED` ou `inChat`)
- Proxy IPRoyal ativo (`infra/wppconnect/_src/src/config.ts` ou payload de
  start-session com proxy BR)
- `bot` do Kobe restartado pra carregar handlers Apolo:
  `systemctl --user restart kobe`
- `apolo-webhook` service rodando: `systemctl --user start apolo-webhook`
- Webhook configurado no WPPConnect apontando pro endpoint local
  (vide A.4 abaixo)

---

## A — Infraestrutura

### A.1 — Schema aplicado no Supabase

```bash
# No SQL Editor do Supabase, rodar:
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' AND table_name IN ('contacts','whatsapp_messages');
```

Esperado: 2 linhas (contacts, whatsapp_messages).

### A.2 — WPPConnect responde

```bash
curl http://127.0.0.1:21465 | jq
```

Esperado: `{"status":200,"message":"Welcome to the Evolution API, it is working!","version":"2.10.0", ...}`
(O "Evolution API" no welcome é só o template — versão real do WPPConnect é 2.10.x.)

### A.3 — Sessão pareada e geo BR

```bash
# Status
SECRET=$(grep WPPCONNECT_SECRET_KEY infra/wppconnect/.env | cut -d= -f2)
TOKEN=$(curl -sS -X POST "http://127.0.0.1:21465/api/kobe/${SECRET}/generate-token" | jq -r .token)
curl -sS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:21465/api/kobe/status-session | jq

# Geo do proxy (deve ser BR)
python3 -c "
with open('.env.test.local') as f:
  for l in f:
    if l.startswith('IPROYAL_CREDENTIALS='): v=l.split('=',1)[1].strip().strip(chr(34)).strip(chr(39)); break
h,p,u,pw=v.split(':')
import subprocess,json
r=subprocess.run(['curl','-sx',f'http://{u}:{pw}@{h}:{p}','http://ip-api.com/json'],capture_output=True,text=True)
print(json.dumps(json.loads(r.stdout),indent=2))
"
```

Esperado: `status: CONNECTED` (ou `inChat`); proxy responde `country: Brazil`.

### A.4 — Webhook IN configurado

```bash
TOKEN=...  # já gerado acima
curl -sS -X POST "http://127.0.0.1:21465/api/kobe/webhook" \
  -H "Authorization: Bearer ${TOKEN}" -H "Content-Type: application/json" \
  -d '{"webhook":"http://host.docker.internal:8787/wa-webhook"}'

# Validar
curl -sS -H "Authorization: Bearer ${TOKEN}" "http://127.0.0.1:21465/api/kobe/all-webhook" | jq
```

Esperado: webhook URL retornado.

### A.5 — apolo-webhook service vivo

```bash
systemctl --user status apolo-webhook
curl http://127.0.0.1:8787/healthz
```

Esperado: `active (running)` + `{"status":"ok"}`.

---

## B — Importação de contatos (export manual)

### B.1 — Importar vCard do Google

1. Em aba anônima, logar em `contacts.google.com` na **conta pessoal** (não a
   profissional Virtus).
2. Selecionar contatos (ou label específico).
3. Exportar → vCard.
4. No Telegram, anexar o `.vcf` numa conversa qualquer com o Hal.
5. Aguardar resposta: bot deve responder com estatísticas e anexar de volta
   um arquivo `.md` (peneira).

Validar:
- ✅ Estatísticas batem (total no arquivo ≈ contatos exportados).
- ✅ Arquivo `.md` aparece anexado de volta.
- ✅ Arquivo salvo em `$KOBE_HOME/user-data/imports/google_vcard-YYYY-MM-DD.md`.

### B.2 — Peneirar e promover

1. Baixar o `.md` recebido, abrir no VS Code (ou editor de texto).
2. Apagar linhas (ou seções inteiras) dos contatos que NÃO quer.
3. Salvar e anexar o arquivo modificado de volta no Telegram.
4. Mandar: `/contatos_promover <nome-do-arquivo.md>` (use o nome retornado).

Validar:
- ✅ Bot responde "Promoção concluída" com estatísticas.
- ✅ `/contatos_listar pessoas` mostra os contatos promovidos.
- ✅ `/contatos_buscar <nome>` acha um contato específico.

### B.3 — Importar Google CSV

Repetir B.1-B.2 com export em CSV (Google Contacts → Exportar → Google CSV).

---

## C — Busca e ambiguidade

### C.1 — Match único

```text
/contatos_buscar pedro
```

Se só tiver 1 Pedro: mostra direto.

### C.2 — Ambiguidade

Inserir 2+ pessoas com nome similar (ex: "Pedro Silva", "Pedro Costa") via
importação ou manualmente no Supabase. Depois:

```text
/contatos_buscar pedro
```

Esperado: lista numerada.

### C.3 — Telefone direto

```text
/contatos_buscar +5511987654321
```

Esperado: 1 match (se cadastrado) ou nenhum.

---

## D — Envio direto (subagente)

> Pré: chip 2 pareado, ao menos 1 contato com `whatsapp_jid` no catálogo.

No Telegram, peça pro Hal:

```text
manda "oi do Apolo, teste" pro Pedro
```

(Substitua "Pedro" por algum contato seu real, OU pelo seu próprio número.)

Esperado:
- Hal usa o subagente Apolo.
- Apolo resolve "Pedro" → JID.
- Mensagem chega no WhatsApp.
- Hal responde "✅ enviado pro <nome>".

Validar no Supabase:

```sql
SELECT * FROM whatsapp_messages WHERE direcao='out' ORDER BY timestamp DESC LIMIT 5;
```

### D.1 — Envio com anexo

Pedir:
```text
manda esse arquivo pro Pedro
```

(Anexando um PDF/imagem no mesmo turno do Telegram, ou citando um path
de artefato gerado por outro plugin.)

Esperado: arquivo chega no WhatsApp com caption opcional.

### D.2 — Composição com atrus

```text
transcreve em formato leitura https://youtu.be/<id-curto> e manda pro Pedro
```

Esperado:
- Hal aciona atrus → HTML transcrito.
- Hal aciona apolo com `anexo=<path-do-html>`.
- Mensagem WhatsApp chega com o arquivo HTML como anexo.

---

## E — Grupos (LAZY)

> Pré: chip 2 está em grupos WhatsApp.

### E.1 — Lazy de grupo NÃO cadastrado

```text
manda "oi" pro grupo família
```

Esperado:
- Apolo busca catálogo → 0 matches.
- Apolo busca via WPPConnect → lista grupos com "família" no nome.
- Hal pergunta qual.
- Você responde "1" (ou nome).
- Apolo grava grupo no catálogo (origem=`whatsapp_grupo_uso`), envia msg.

Validar:
- ✅ Mensagem chega no grupo.
- ✅ `/contatos_listar grupos` mostra esse grupo.
- ✅ Próxima vez ("manda pro grupo família") vai direto, sem perguntar.

### E.2 — Listagem de grupos (busca exploratória)

```text
/whatsapp_grupos
/whatsapp_grupos cliente
```

Esperado: lista numerada com flag ✅ pros grupos já no catálogo.

### E.3 — Batch inline

(Pós-`/whatsapp_grupos cliente`):
```text
adiciona ao catálogo 3, 7, 12
```

(Subagente intermedia — extrai índices da última listagem, chama
`grupos_promover.py` em batch.)

---

## F — Recebimento (IN)

### F.1 — Mensagem chega de contato conhecido

Pedir pra alguém (ou pro próprio número, se pareou conta secundária)
mandar msg pro chip 2.

Validar:
- ✅ `/whatsapp_inbox` mostra a mensagem.
- ✅ `whatsapp_messages` no Supabase tem linha com `direcao=in`.
- ✅ Mídia (se houver) está em `user-data/whatsapp/midia/<id>.<ext>`.

### F.2 — Mensagem de grupo desconhecido

Pedir alguém mandar msg num grupo que VOCÊ NÃO usou ainda.

Validar:
- ✅ Mensagem aparece em `/whatsapp_inbox`.
- ✅ Grupo NÃO entra no catálogo automaticamente (princípio lazy).

---

## G — Resiliência

### G.1 — Restart do WPPConnect

```bash
cd infra/wppconnect && docker compose restart wppconnect
```

Sessão deve restaurar sozinha do volume `wppconnect-tokens` (não precisa
de QR de novo).

Validar (depois de ~30s):
```bash
curl -sS -H "Authorization: Bearer ${TOKEN}" http://127.0.0.1:21465/api/kobe/status-session | jq
```

Esperado: `status: CONNECTED` ou `inChat`.

### G.2 — Restart do apolo-webhook

```bash
systemctl --user restart apolo-webhook
```

Mensagens em trânsito não devem duplicar (dedup por `message_id` UNIQUE).

### G.3 — Restart do bot Kobe

```bash
systemctl --user restart kobe
```

Comandos `/contatos_*` voltam a funcionar em < 5s.

---

## H — Limpeza

Se quiser zerar TUDO pra teste limpo:

```bash
# Apaga sessão pareada (vai precisar de QR de novo)
cd infra/wppconnect && docker compose down -v
# Apaga contatos do Supabase
psql ... -c "TRUNCATE contacts, whatsapp_messages;"
# Apaga arquivos de import
rm -rf $KOBE_HOME/user-data/imports/*
rm -rf $KOBE_HOME/user-data/whatsapp/midia/*
```
