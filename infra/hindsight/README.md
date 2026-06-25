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
cd /home/felipe/kobe/user-data/coder-worktrees/842fc607/infra/hindsight
cp .env.example .env
# edite .env e preencha:
#   HINDSIGHT_DB_PASSWORD=$(openssl rand -hex 16)
#   OPENAI_API_KEY=<a MESMA do /home/felipe/kobe/.env do bot>
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
> (`/home/felipe/kobe/infra/hindsight/`) e o caminho acima vira `$KOBE_HOME/infra/hindsight`.

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
