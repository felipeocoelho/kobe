# Hindsight — memória durável do Kobe (Highlander Frente 2)

Serviço de memória de longo prazo (**retain / recall / reflect**) da
[Vectorize/Hindsight](https://github.com/vectorize-io/hindsight) (MIT), em **modo
serviço** (container próprio), apontando pra um **Postgres dedicado novo** (pgvector),
separado do Postgres da Evolution e do Supabase.

> **Ambiente de validação: PROD VPS.** O operador não testa em dev VPS. Sobe o stack
> aqui e roda o smoke aqui. Tudo é reversível: `docker compose down -v` apaga o
> container + volume sem encostar no Supabase nem na Evolution.

## O que é o quê

| Container | Imagem | Porta (loopback) | Papel |
|---|---|---|---|
| `hindsight-postgres` | `pgvector/pgvector:pg18` | — (rede interna) | storage dedicado (pgvector) |
| `hindsight-app` | `ghcr.io/vectorize-io/hindsight:0.8.3` | `127.0.0.1:8888` (API MCP+REST), `127.0.0.1:9999` (admin UI) | serviço retain/recall/reflect |

- **LLM/embedding:** OpenAI (reusa a `OPENAI_API_KEY` do Kobe). Embedding =
  `text-embedding-3-small` (decisão v4 §6).
- **Portas em loopback** (como a Evolution). Admin UI só via túnel SSH.
- **Versão pinada** (imagem `0.8.3` — o release no GitHub é `v0.8.3`, a imagem
  Docker é sem o `v`) — nunca `:latest`.

## Passo exato pro Hal/operador (PROD VPS) — Frente 2.2

> **Quem executa:** Hal/operador. A sessão Coder NÃO sobe container nem roda o smoke
> (trava). Os arquivos já estão prontos nesta pasta.

**Onde rodar (agora, pra o smoke):** os arquivos vivem no worktree da sessão, que está
fisicamente no prod VPS. O container e o volume são **independentes do diretório** (volume
nomeado `hindsight-postgres-data`), então roda direto daqui — não depende do merge-back
(que está bloqueado pela árvore dev suja). Caminho exato:

```bash
cd $KOBE_PROD/infra/hindsight
cp .env.example .env
# edite .env e preencha:
#   HINDSIGHT_DB_PASSWORD=$(openssl rand -hex 16)
#   OPENAI_API_KEY=<a MESMA do $KOBE_PROD/.env do bot>
```

**Opção 1 — um comando (sobe + espera + smoke):**

```bash
sg docker -c "bash up_and_smoke.sh"
```

**Opção 2 — passo a passo:**

```bash
sg docker -c "docker compose up -d"
sg docker -c "docker compose ps"                       # esperar os 2 de pé
sg docker -c "docker compose logs -f hindsight-app"    # acompanhar o boot (Ctrl-C qd subir)
python3 smoke_test.py
```

O smoke sobe um bank de teste, faz **retain** de um fato plantado, **recall**, e exige que
o fato volte. `PASS` (exit 0) = stack funcional ponta a ponta. Imprime a **latência do
retain** e o **`usage`** (custo). **Manda o output do smoke pro Coder** — é o que destrava
a Frente 2.3 (cliente do bot). Mede também:
- **custo do retain** (campo `usage` da resposta, ou conta OpenAI).
- **se o reflect roda em background** (retain com `async:true` não bloqueia).

> Quando o Highlander for pro merge-back, `infra/hindsight/` passa a viver no prod main
> (`$KOBE_PROD/infra/hindsight/`) e o caminho acima vira `$KOBE_HOME/infra/hindsight`.

## Duas stacks na mesma máquina: produção e desenvolvimento

Desde a camada de ambiente (Sessão #1 do Projeto Novo Ambiente Kobe), este mesmo
`docker-compose.yml` sobe **duas instâncias independentes** do Hindsight — o que
muda entre elas é só o arquivo de ambiente.

**Por que instância separada e não um bank diferente no mesmo serviço.** Decisão
do operador em 24/08/2026, e a razão é operacional: poder testar um upgrade de
versão do Hindsight em desenvolvimento sem arriscar a memória de produção. Com um
serviço só, um upgrade que desse errado derrubaria a memória viva junto. É caso
particular de um princípio permanente — toda aplicação externa com Postgres
próprio ganha instância separada, igual em dev e em prod.

**Quanto custa:** ~517 MB de RAM para o par (serviço + banco), medido na produção.

### Como cada stack é endereçada

| | Produção | Desenvolvimento |
|---|---|---|
| Arquivo de ambiente | `.env` | `.env.dev` |
| Projeto do Compose | `hindsight` | `hindsight-dev` |
| Containers | `hindsight-app`, `hindsight-postgres` | `hindsight-app-dev`, `hindsight-postgres-dev` |
| Rede | `hindsight-net` | `hindsight-net-dev` |
| Volume (a memória) | `hindsight-postgres-data` | `hindsight-postgres-data-dev` |
| Portas no host | 8888 / 9999 | 8890 / 9991 |

Os nomes de dev saem do sufixo `HINDSIGHT_STACK_SUFFIX=-dev`. **Todos os
parâmetros têm default igual ao valor literal de produção**, então rodar sem
nenhuma variável nova resolve exatamente a configuração de sempre — o que é
verificável a qualquer momento:

```bash
# tem que sair vazio, sempre
diff <(sg docker -c "docker compose config") <(git show HEAD~1:infra/hindsight/docker-compose.yml > /tmp/antes.yml && sg docker -c "docker compose -f /tmp/antes.yml config")
```

### Subir e derrubar o dev sem encostar na produção

```bash
cd $KOBE_HOME/infra/hindsight/
cp .env.dev.example .env.dev     # e preencha senha própria + OPENAI_API_KEY

sg docker -c "docker compose --env-file .env.dev up -d"      # sobe o dev
sg docker -c "docker compose --env-file .env.dev ps"         # confere
sg docker -c "docker compose --env-file .env.dev down"       # derruba o dev
```

> ⚠️ **A armadilha, dita na cara: `--env-file` é o que escolhe a stack.**
> Um comando sem ele age sobre a **produção**. Isso é irrelevante num `ps` e
> catastrófico num `down -v`, que apaga o volume — a memória durável do
> operador. Antes de qualquer `-v`, confira o alvo com
> `docker compose --env-file .env.dev config | head -1`: tem que dizer
> `name: hindsight-dev`.

### Do lado do bot de dev

`HINDSIGHT_BASE_URL=http://127.0.0.1:8890` no `.env` do Kobe de desenvolvimento.
Se essa linha faltar, o bot de dev conversa com o Hindsight de **produção**. O
prefixo de bank por ambiente (`kobe-dev-<slug>`, em `bot/hindsight_client.py`)
existe exatamente para esse erro não contaminar a memória viva — mas ele é cinto
de segurança, não substituto de apontar a URL certa.

## SQL / migrations — quem roda é o operador (regra dura)

**O Hindsight cria e migra o próprio schema no startup** (tabelas + índices pgvector),
contra o `HINDSIGHT_API_DATABASE_URL`. Em condições normais **não há SQL manual**.

Contingência — se o boot reclamar que a extensão `vector` não existe (raro, a imagem
`pgvector/*` já a traz), rode **uma vez** no Postgres dedicado:

```bash
sg docker -c "docker compose exec hindsight-db \
  psql -U hindsight_user -d hindsight_db -c 'CREATE EXTENSION IF NOT EXISTS vector;'"
```

> **Esta sessão (Coder) NÃO roda SQL contra nenhum banco.** Qualquer SQL é executado
> pelo operador/Hal com o token em disco. Migração de banco do Kobe é confirmada com o
> operador antes. O Postgres dedicado do Hindsight é container novo e isolado — não é a
> migração da memória de trabalho (essa está fora de escopo nesta rodada).

## Backup / restore

A memória durável vive no volume `hindsight-postgres-data`. Backup por `pg_dump`
(espelhar o cron do padrão da Evolution):

```bash
sg docker -c "docker compose exec hindsight-db \
  pg_dump -U hindsight_user hindsight_db" > backup-hindsight-$(date +%F).sql
```

## Derrubar / reverter

```bash
sg docker -c "docker compose down"        # mantém o volume (memória preservada)
sg docker -c "docker compose down -v"     # APAGA a memória durável (irreversível)
```

`down -v` apaga dado — **pedir OK ao operador antes** (regra dura: deleção em massa).

## Integração com o bot (Frente 2.3 — só DEPOIS do smoke passar)

O bot fala com o serviço atrás da flag `HINDSIGHT_ENABLED` (default off = Kobe como hoje):
`retain` no fim do turno (ou no daemon, por silêncio) e `recall` quando volta um assunto.
Trava anti-alucinação (v4 §6): `retain` conservador e rastreável à fonte; o fato devolvido
**obedece o contrato** — o agente ainda verifica. Cliente em `bot/hindsight_client.py`.

## Troubleshooting

- **`verify_connection()` falha no boot com pg externo:** problema conhecido em alguns
  setups (Postgres externo + Docker non-root). Conferir `HINDSIGHT_API_DATABASE_URL`,
  que o `hindsight-db` está healthy, e os logs do `hindsight-app`. Fallback de último
  caso: Postgres embutido do Hindsight (volume `.pg0`) — mas aí perde-se o "pg dedicado
  é o futuro lar da memória"; decidir com o operador antes de cair nisso.
- **Embedding key:** com `HINDSIGHT_API_EMBEDDINGS_PROVIDER=openai`, o serviço reusa a
  chave do LLM. Se o boot reclamar de chave de embedding, conferir nos logs o nome exato
  do env esperado e ajustar o compose (iterar no smoke).
