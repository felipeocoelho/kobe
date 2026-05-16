# Sugestões futuras — Kobe

Ideias que faria sentido explorar quando você sentir falta, mas não são
prioridade hoje. Não confunda com `SPEC.md` (escopo definido e versionado) —
isto aqui é arena de ideias.

Quando uma ideia daqui virar trabalho real, ela migra pra `SPEC.md` como uma
fase nova e ganha (se necessário) runbook em `docs/runbooks/`.

---

## Busca semântica em `saved_artifacts` (embeddings)

**O que é**: hoje `/retomar <palavra>` faz `ILIKE %palavra%` em `saved_artifacts.content`.
Funciona pra match literal, falha pra sinônimo, paráfrase, "aquele papo que tivemos
sobre X" quando X foi dito de outro jeito.

**A feature**: ao salvar (`/salvar` ou auto-snapshot), gerar embedding do conteúdo
e gravar em `saved_artifacts.embedding VECTOR(1536)` (coluna já existe + índice
ivfflat). No `/retomar`, embedda a query e busca por proximidade.

**Custo (motivo de não estar no roadmap atual)**:
- Provider: OpenAI `text-embedding-3-small` (~$0.02/1M tokens, dimensão 1536) ou
  Voyage `voyage-3-lite` (~$0.02/1M, mais barato pra batch). Algum cadastro/key novo,
  mais 1 secret pra gerenciar.
- Estimativa de uso real: 1 artefato/dia × 1.000 tokens × 365 dias = 365k tokens/ano.
  Em OpenAI seriam ~$0.007 ao ano. **O custo é desprezível**.
- Então por que travou? Não é o dinheiro, é a **decisão de onboarding** — operador
  novo precisaria configurar +1 API key. Quebra a promessa de "1 instalador, 1 .env,
  funciona". A solução real é deixar embeddings **opcional** (env var `EMBEDDINGS_PROVIDER`
  vazio desliga a feature; `/retomar` cai pro ILIKE silenciosamente).

**Quando reabrir**: quando você (ou outro operador) acumular >100 `saved_artifacts`
e o ILIKE começar a falhar regularmente. Marcador: você procurar algo e não achar
mesmo sabendo que salvou.

**Esboço de implementação**:
1. Nova função `embed(text) -> list[float]` em `bot/embeddings.py` com dispatch
   por provider (lê `EMBEDDINGS_PROVIDER` do env).
2. No `save_artifact_from_messages`, se provider configurado: gera embedding e
   grava em `embedding`.
3. No `search_artifacts`, se provider configurado: usa `<->` (cosine distance)
   contra embedding da query; fallback ILIKE se embedding falha ou desligado.
4. Migration retroativa pra artefatos antigos: comando `/reembed` ou script
   one-shot em `infra/`.

---

## Detecção real de tópico deletado

**O que falta**: hoje o handler `forum_topic_closed` marca `topics.status='archived'`,
mas se o operador **apaga** o tópico no Telegram (não é "close", é "delete"),
o Telegram **não emite evento**. A linha em `topics` fica órfã.

**Soluções possíveis**:
1. **Check periódico**: job que roda `bot.get_forum_topic_icon_stickers` (ou similar)
   pra cada `topics.status='active'` a cada N horas. 404 → marca `deleted_at`.
   Custo: 1 chamada/tópico/N horas. Limpo, mas é polling.
2. **Comando manual**: `/deletar-topico` que o operador roda no próprio tópico
   antes de deletar. Frágil — depende de operador lembrar.
3. **Lazy**: marca `last_seen_at` em todo evento. Job de limpeza marca como
   deletado tópicos sem atividade há >X meses. Não detecta delete imediato,
   mas resolve o lixo eventualmente.

**Recomendação quando reabrir**: começar com (3) — barato, sem efeito colateral.
(1) só se você passar a ter dezenas de tópicos e precisar de UI dashboard.

---

## Tabela `metrics` no Supabase

**Estado atual (v0.12)**: tokens, latência e status saem como log estruturado
(`claude_run status=... tokens_in=... tokens_out=...`). `journalctl ... grep claude_run`
permite análise ad-hoc.

**Quando vale uma tabela**:
- Você quer dashboard contínuo (ex: gasto de tokens/mês por tópico) sem ter
  que rodar grep/awk.
- Logs do journal são rotacionados — perde histórico longo.

**Como ficaria**: tabela `metrics` com `(timestamp, topic_id, session_id,
elapsed_ms, tokens_in, tokens_out, cache_read, cache_create, status,
error_class)`. Insert ao final de cada `claude_run`. Dashboard via Supabase
SQL editor ou Metabase apontando pro mesmo banco.

**Custo**: 1 INSERT por mensagem do operador. Insignificante.

---

## Comandos `/instrucoes` e `/kb` explícitos

Hoje o operador pode pedir conversacionalmente "o que tem aqui na base?" e o
agente lista. Mas é descoberta — quem nunca viu a feature pode não saber.

**A feature**: comandos explícitos:
- `/instrucoes` — mostra `prompt.md` do tópico atual (ou aviso se não tem)
- `/kb` — lista `knowledge/*.md` do tópico com tamanho de cada
- `/kb show <nome>` — mostra conteúdo de um arquivo específico

Sem chamar Claude — operação direta sobre filesystem, baixo custo.

---

## Web dashboard (futuro distante)

Ler Supabase + filesystem do operador e mostrar:
- Lista de tópicos + estado de cada (mensagens, última atividade, KB carregada)
- Editor inline de `prompt.md`/`knowledge/*` (com preview)
- Histórico de mensagens por tópico/sessão
- Métricas (se tabela existir)

Trade-off: mais 1 serviço pra manter (Next.js? Streamlit?). Hoje o Telegram já
cobre 95% — dashboard só compensa se Kobe virar produto pra mais gente.
