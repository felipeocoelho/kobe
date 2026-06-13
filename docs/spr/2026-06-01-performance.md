# SPR — Kobe — Performance (latência percebida) — 2026-06-01

> Escopo: pipeline de mensagem do bot (`Telegram → claude -p → Telegram`), com
> foco no **tempo do envio do operador até ele ver a resposta**. Segurança
> entrou só como passada leve (ver final). Números reais extraídos de
> `journalctl --user -u kobe` (164 turnos logados) + estado de runtime da prod
> VPS (`/home/felipe/kobe`).

---

## Resumo executivo

Estado geral: **funcional, bem instrumentado, mas lento na percepção** — e o
motivo nº 1 não é um bug escondido, é **arquitetural e configurável**.

O operador espera a resposta **inteira** ser gerada antes de ver qualquer
caractere: o bot **não faz streaming** da resposta do Claude pro Telegram.
Como o turno médio de conversa pura leva **p50 = 20s** (p95 = 74s) e turnos com
ferramenta levam **p50 = 66s** (p95 = 217s), o operador fica olhando "digitando…"
por todo esse tempo. Pior: **7,7% dos turnos estouram o timeout de 300s** e o
operador recebe a mensagem de erro em vez de resposta.

Em linguagem de banco: hoje o Kobe roda a *query inteira* e só então devolve o
*result set* — sem cursor, sem fetch incremental. O usuário sente o tempo total,
não o tempo até a primeira linha.

Três alavancas explicam quase toda a dor, em ordem de impacto:

1. **Sem streaming** (operador espera o turno inteiro em silêncio).
2. **Modelo default = Opus em toda conversa** (tier mais lento; sem `--model`).
3. **Prompt dinâmico gigante** (~12k–20k tokens re-processados por turno; infla o
   tempo até o primeiro token).

Contagem de gaps: **0 P0** · **5 P1** · **6 P2**. Nenhum risco de perda de dado
ou vazamento crítico na passada leve de segurança.

Recomendação imediata (maior ganho / menor risco): **(a) Sonnet para turnos
conversacionais** e **(b) streaming incremental da resposta**. As duas juntas
derrubam a latência percebida de dezenas de segundos para poucos segundos sem
tocar em dado nem em credencial.

---

## Métricas de performance observadas

Fonte: 164 linhas `claude_run` no journal (30/05 a 01/06). `elapsed` = tempo do
spawn do `claude -p` até o `result` final. **Como não há streaming, `elapsed` ≈
latência percebida pelo operador.**

| Recorte | p50 | p90 | p95 | p99 | máx |
|---|---|---|---|---|---|
| **Conversa pura** (tool_calls=0) | **20,4s** | 64,8s | 73,8s | 104,9s | 128,6s |
| **Com ferramenta** (tool_calls>0) | **65,9s** | 176,4s | 217,3s | 257,0s | 291,6s |
| Geral (tudo) | 53,9s | 244,5s | 300,0s | 300,0s | 300,1s |

Conversa pura, decomposta por tamanho da resposta — a latência é dominada pela
**geração de tokens de saída** (throughput do modelo):

| tokens de saída | n | p50 elapsed | observação |
|---|---|---|---|
| 0–800 (resposta curta) | 24 | **9,1s** | já é lento pra um "oi, e aí?" |
| 800–2000 (resposta média) | 13 | **24,3s** | |
| 2000+ (resposta longa) | 15 | **56,6s** | |

| Outras métricas | valor |
|---|---|
| **Timeouts (300s)** | **12 de 156 turnos = 7,7%** |
| `prompt_len` (prompt dinâmico, só o stdin) | p50 = 49k chars (~12k tok) · p95 = 80k chars (~20k tok) · máx = 85k |
| `cache_create` por turno | p50 = 58k tok · p95 = 90k tok |
| `cache_read` por turno | p50 = 91k tok · p95 = 760k tok |
| Custo por turno | $0,28 – $1,60 (Opus; pago no plano Max, não em API key) |

Leitura das duas últimas linhas: o **prefixo estável** (CLAUDE.md + tools) é
cacheado (`cache_read`), mas **todo turno re-cria ~58k tokens de cache**
(`cache_create`) — porque o prompt dinâmico muda a cada mensagem (nova msg + novo
histórico + KB do tópico inteira), bustando o cache de tudo que vem depois do
prefixo. É o equivalente a invalidar o buffer pool a cada query: o trabalho de
*prefill* é refeito sempre, e prefill é exatamente o que atrasa o primeiro token.

---

## Gaps críticos (P0)

Nenhum P0 encontrado. Não há perda de dado, indisponibilidade prolongada do
serviço (o bot em si está de pé há dias) nem vazamento crítico. Os timeouts de
300s são dolorosos, mas degradam um turno por vez com mensagem graciosa — entram
como P1, não P0.

---

## Gaps médios (P1 — corrigir no sprint corrente)

Ordenados por impacto provável na **latência percebida** (maior primeiro).

- [ ] **#1 — Resposta não é streamada; operador espera o turno inteiro** —
  `bot/claude_runner.py:165-274` (consome o stream mas só usa o evento `result`
  final) + `bot/telegram_handler.py:655` (`_send_long_text` só dispara depois do
  `await claude.run`). O `--output-format stream-json` já entrega blocos de texto
  `assistant` durante a geração, mas eles são jogados num buffer de fallback e
  nunca enviados. **Risco/UX:** é *o* ofensor da latência percebida — p50 9–24s,
  p95 74s de silêncio total. **Fix sugerido:** enviar/editar uma mensagem no
  Telegram conforme os blocos `assistant` de texto chegam (ou ao menos enviar a
  resposta no primeiro bloco), respeitando o throttle de edit que o
  `ProgressReporter` já implementa.

- [ ] **#2 — Modelo default (Opus) em toda conversa, sem `--model`** —
  `bot/claude_runner.py:117-125` (cmd sem flag de modelo → usa o default da conta,
  hoje Opus, conforme custo $0,4–1,6/turno). Opus é o tier mais lento em
  time-to-first-token e throughput. **Risco/UX:** turnos de bate-papo pagam preço
  de Opus em latência sem precisar. **Fix sugerido:** passar `--model sonnet` para
  o caminho conversacional (Sonnet ~2–3x mais rápido) e reservar Opus para missão
  / trabalho pesado declarado. Custo não é problema (plano Max), velocidade é.

- [ ] **#3 — Prompt dinâmico gigante re-processado a cada turno** —
  `bot/claude_runner.py:307-398` (`build_prompt`) + `bot/telegram_handler.py:483`
  (`load_topic_context` injeta a KB do tópico inteira). p50 ~12k / p95 ~20k tokens
  de prompt dinâmico, com ~58k tokens de `cache_create` por turno. **Risco/UX:**
  quanto maior o prompt, maior o prefill → maior o tempo até o primeiro token; e a
  parte volátil (KB + summaries) impede reuso de cache. **Fix sugerido:** parar de
  injetar a KB grande do tópico (ex.: `dev-kobe`) inteira a cada turno — deixar o
  agente lê-la sob demanda (ele tem `Read`), ou estabilizar o bloco pra ele cair no
  prefixo cacheável.

- [ ] **#4 — 7,7% dos turnos estouram o timeout de 300s** —
  `bot/config.py:80` (`CLAUDE_TIMEOUT_SECONDS=300`) +
  `bot/telegram_handler.py:579`. **Risco/UX:** ~1 em 13 turnos não entrega
  resposta — o operador recebe "estourei o tempo limite". Concentra-se em turnos
  tool-heavy (tool_calls ≥ 6) rodando em Opus. **Fix sugerido:** combinar com #2
  (Sonnet acelera) e sinalizar progresso parcial antes do corte; reavaliar se 300s
  é teto ou se o turno deveria ser quebrado.

- [ ] **#5 — I/O bloqueante no event loop (DB síncrono + transcrição síncrona)** —
  `bot/telegram_handler.py:236` (`transcriber.transcribe` síncrono dentro de
  `async`) e toda a cadeia de `ensure_topic`/`get_recent_messages`/`count_messages`/
  `get_topic_slug` (supabase-py síncrono, chamado sem `await`, linhas 351-507).
  Com `concurrent_updates(True)` (`bot/main.py:256`), uma chamada bloqueante
  trava **o loop inteiro** — anula o paralelismo entre tópicos que o lock por
  tópico foi desenhado pra permitir. São ~8–12 round-trips seriais ao Supabase
  **antes** do Claude começar. **Risco/UX:** soma 1–3s fixos por turno e serializa
  tópicos concorrentes. Em banco: é um `SELECT` bloqueante segurando a única
  conexão enquanto os outros esperam na fila. **Fix sugerido:** rodar os helpers
  síncronos em `run_in_executor` (ou cliente Supabase async) e paralelizar as
  leituras independentes (`history`, `topic_context`, `slug`) com `asyncio.gather`.

---

## Gaps baixos (P2 — backlog priorizado)

- [ ] **Detector de conversa no caminho crítico** —
  `bot/telegram_handler.py:404` (`detect`) chama embedding OpenAI (+ eventual juiz
  GPT-4o-mini) **antes** de invocar o Claude, serial. Custa ~0,3–2s por turno e
  está com `CHAT_MANAGER_ENABLED=true` na prod. Fix: sobrepor com as outras
  leituras (parte do #5) ou rodar especulativo.

- [ ] **Cold start do `claude -p` por turno** — `bot/claude_runner.py:136` sobe um
  processo Node novo a cada mensagem (boot do CLI + auto-load do CLAUDE.md de 26KB
  + SOUL/USER/PREFERENCES ~8KB). Custo fixo ~1–3s, estrutural ao modelo de
  invocação. *Nota boa:* os conectores MCP do projeto estão **vazios** (`proj
  /home/felipe/kobe → []`), então **não** há custo de cold-start de MCP — um
  suspeito que descartei com evidência.

- [ ] **Sem medição de TTFT nem do overhead pré-Claude** — a instrumentação atual
  (`claude_run`) mede o tempo total do Claude, não o tempo até o primeiro token
  nem quanto o detector/DB/transcrição custaram. Hoje "total ≈ percebido" porque
  não há streaming; **assim que o streaming (#1) entrar, será necessário instrumentar
  TTFT** e o breakdown pré-Claude (`detector_ms`, `db_ms`, `transcribe_ms`).

- [ ] **CLAUDE.md do projeto sem bloco de SLO** — o `performance-baseline.md` pede
  uma tabela de SLO no CLAUDE.md (alvo + ação). O Kobe não tem. Sem alvo declarado,
  "está lento" segue opinião. Fix: adicionar tabela com alvo de TTFT (ex.: p95 < 3s
  pós-streaming) e ação.

- [ ] **Saturação de CPU da VPS durante jobs Atrus** — no momento da coleta:
  `load average 4.29` em **2 vCPUs** (>200%), com dois drivers Atrus
  (transcrição/diarização) consumindo 159% + 85% de CPU e ~2,8GB RAM. Quando o
  operador manda mensagem nesse intervalo, o `claude -p` disputa 2 núcleos →
  **latência intermitente**. Memória não está crítica (1,1GB livre + 4GB swap
  praticamente intocado). Não é bug de código, é capacidade. Fix: rodar Atrus com
  `nice`/cgroup (prioridade baixa) ou serializar jobs pesados de transcrição.

- [ ] **Sessões Coder residuais** — 5 processos `claude --remote-control`
  (diagnósticos do dia 30/05) seguem residentes consumindo ~1,8GB somados, idle.
  Fix: encerrar sessões concluídas (limpeza de `user-data/coder-sessions`).

- [ ] **Dict de locks por tópico cresce sem TTL** — `bot/telegram_handler.py:129`.
  Irrelevante na escala atual; anotado pelo próprio autor.

---

## O que está bom (reforço)

- ✓ **Instrumentação de turno exemplar** — a linha `claude_run` loga `elapsed`,
  `prompt_len`, `tokens_in/out`, `cache_read/create`, `tool_calls`, `cost_usd`,
  `status` e `error_class`. É exatamente o que o baseline exige; foi o que
  permitiu este relatório ter número real em vez de palpite. (Por isso o "gap de
  instrumentação" que o briefing levantou **não** se confirmou como P1.)
- ✓ **Prompt caching ativo** — `cache_read` presente em quase todo turno; o
  prefixo estável é reaproveitado (corta custo e parte do prefill).
- ✓ **`ProgressReporter` (sinal de vida) bem desenhado** — `bot/progress.py`:
  lazy (só aparece se passar de 6s), throttled (anti rate-limit), uma mensagem só
  editada in-place, filtra ações de subagente. Mitiga o silêncio enquanto o
  streaming real não existe.
- ✓ **Concorrência conceitualmente correta** — `concurrent_updates(True)` + lock
  por `(chat_id, thread_id)` preserva ordem dentro do tópico e paraleliza entre
  tópicos. Só está prejudicada pelo I/O bloqueante (#5) — o desenho está certo.
- ✓ **Degradação graciosa** — timeout, CLI ausente, exit≠0 e resposta vazia têm
  mensagem clara pro operador + dump de diagnóstico; transcrição tem fallback
  Groq→AssemblyAI.

---

## Passada leve de segurança (não foi o foco)

- **P2 — Superfície de injeção de prompt com `bypassPermissions`** —
  `bot/claude_runner.py:120-121` roda o agente com shell completo na VPS. É por
  design (documentado) e o acesso é restrito a `allowed_user_ids`, mas conteúdo
  não-confiável que entra no prompt (áudio transcrito, KB do tópico, documentos
  enviados) poderia, em teoria, induzir execução. Aceitável pra ferramenta pessoal
  de um operador; anotar como risco consciente.
- **P2 — Confirmar `.env` no `.gitignore`** — há `.env` em prod com chaves
  (OpenAI, AssemblyAI). Convenção `user-data/`/`.env` deve estar ignorada; vale um
  `git log -p | grep` de verificação no fim de feature (não auditado a fundo aqui).
- Sem multiusuário/auth web (não se aplica — é serviço pessoal single-operator).

---

## Próximos passos (ordem sugerida, maior ganho primeiro)

1. **Sonnet no caminho conversacional** (#2) — uma flag `--model`, risco quase
   nulo, contingência = remover a flag. Maior ganho/menor esforço.
2. **Streaming incremental da resposta** (#1) — ataca diretamente a latência
   percebida; reaproveita o throttle do `ProgressReporter`. Esforço médio.
3. **Enxugar o prompt do tópico** (#3) — parar de injetar a KB grande inteira a
   cada turno; deixar leitura sob demanda. Esforço médio, melhora TTFT e cache.
4. **Tirar I/O bloqueante do event loop** (#5) — `run_in_executor` + `gather` nas
   leituras independentes. Esforço médio, destrava o paralelismo entre tópicos.
5. **Adicionar SLO de TTFT no CLAUDE.md + instrumentar TTFT** (P2) — fechar o ciclo
   de medição depois do streaming.
6. **Operacional**: `nice`/cgroup nos jobs Atrus e limpar sessões Coder residuais.

> Cada item acima é **diagnóstico**, não implementado — conforme regra do SPR.
> Implementação fica para uma sessão Coder com plano dedicado.
