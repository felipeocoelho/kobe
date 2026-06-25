# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Fix — "digitando…" fantasma quando o turno foreground crasha (2026-06-25)

**Operador pediu:** corrigir o indicador "digitando…" que ficava preso/fantasma no
Telegram quando um turno do Kobe crashava (episódio com `LimitOverrunError` ~12:29).

**Por quê (duas camadas, confirmadas no código):**
- **Gatilho:** o reader do stdout do `claude` (`bot/claude_runner.py`) usava o buffer
  default do asyncio (64KB). O stream-json emite UMA linha por evento; um tool_result
  gordo (ler arquivo grande / fetch) estoura 64KB numa linha só → `readline()` levanta
  `ValueError`/`LimitOverrunError`, que NÃO é `ClaudeError` e derrubava o turno inteiro.
- **Por que o typing ficava preso:** o "digitando…" é um loop (`_keep_typing`) que
  reemite a chatAction a cada 4s. No caminho **foreground** (`_handle_user_text`) o
  `typing_task` era cancelado só no caminho feliz — SEM `try/finally`. Quando o turno
  morria antes do cancel (o `ValueError` cru fazia o próprio `_resolve_claude`
  re-levantar), o loop virava órfão e reemitia "digitando…" pra sempre até o bot
  reiniciar. O caminho **background** já tinha a proteção; a assimetria era o defeito.

**Foi feito:**
- **Buffer (gatilho):** `STDOUT_BUFFER_LIMIT_BYTES = 10MB` passado como `limit=` ao
  `create_subprocess_exec`. + degradação amigável: se mesmo assim uma linha estourar o
  limite, o overrun vira `ClaudeError` (mensagem amigável via `_resolve_claude`) em vez
  de crash — e o subprocess é morto/reapado (sem vazar processo nem `stderr_task`).
- **Blindagem (defeito):** novo context manager `_typing_indicator` em
  `bot/telegram_handler.py` que GARANTE o cancelamento do loop na saída do bloco —
  caminho feliz, `return` da promoção OU qualquer exceção. O foreground agora usa
  `async with`, espelhando a proteção que o background já tinha. Remove os cancels
  manuais duplicados.

**Testes (dev VPS, venv `/home/felipe/projetos/kobe/.venv`):**
- `tests/test_claude_runner_buffer.py` — fake-claude cuspindo linha JSON > 64KB; passa
  com o fix e (provado por monkeypatch a 64KB) falha sem ele. Teardown do subprocess no
  overrun validado (sem warning de "Event loop closed").
- `tests/test_typing_indicator.py` — cancela o typing na saída normal E quando o corpo
  levanta exceção (o caso do bug).
- Suíte completa: 67 passam, 4 falham — as 4 são DÉBITO PRÉ-EXISTENTE de `test_resume.py`
  (KeyError `curated_core`, do merge Highlander v2; falham idêntico no HEAD limpo, nada a
  ver com este fix).

**Reversão:** `git revert` dos 2 commits; zero migração/estado. Worktree isolado.

### Docs — runbooks: deploy rsync → git

Atualiza `docs/runbooks/keyko-e-missoes.md` e `ux-resposta-ack-despacho.md` pra
refletir a regra vigente "deploy é git, nunca rsync" (prod **puxa a versão** do repo
dev por `git pull`; `.env`/`user-data/` sobrevivem). Substitui os blocos `rsync
--delete` por `git push origin main` (dev) + `git pull` (prod). Mudanças que já
estavam no working tree do dev VPS (não-Highlander); commitadas pra destravar o
`publish.sh` (que exige árvore limpa). `docs/runbooks/` é excluído do repo público.

### Highlander v2 — F0: régua (arnês de regressão) entra no repo

**Operador pediu:** executar o Highlander v2 (redesenho do recall); F0 = "a régua primeiro,
gate de tudo: nada sobe sem responder 'resolve quantos dos casos?'".

**Por quê:** o arnês `infra/eval/` vivia untracked no dev VPS — sem ele no repo, todo
conserto anti-alucinação subia no escuro. A Auditoria da Verdade fixou a regra de ouro:
medir antes e depois.

**Foi feito:** `infra/eval/{harness.py,README.md,.gitignore}` versionados (os `cases/` e
`results/` ficam de fora pelo `.gitignore` interno — contêm trecho real de conversa, são
privados). O arnês reconstrói o prompt via o `build_prompt` REAL do bot e roda `claude -p`
sandbox (`--tools ""`) por caso, medindo se a alucinação reaparece (keyword_any | llm_judge).

**Testes (dev):** `--dry` valida a montagem dos 6 casos-âncora; baseline `--run --n 3`
rodado no worktree com o venv do dev VPS (gasto de Opus autorizado pelo operador). Número
registrado no commit de fechamento da régua.

**Limitação honesta:** o arnês reconstrói histórico + nota de background + `[Agora]` +
contrato (CLAUDE.md); ainda NÃO injeta as camadas Highlander (curated_core / grounding /
recall do Hindsight), que o `build_prompt` recebe como `None` no caminho do arnês. Logo a
régua mede a eficácia do CONTRATO e (na F5) dos GATES — não o efeito do recall do Hindsight.

**Reversão:** `git revert` do commit; o arnês é ferramenta de bancada, zero efeito em runtime.

### Highlander v2 — F5: gates de estado vivo (P1 background + P2 verificável-barato)

**Operador pediu:** a metade da alucinação que o Hindsight NÃO cobre — "estado vivo": o
"você tá dormindo" respondendo mensagem que ele acabou de mandar, e o status de sala/job
narrado de memória. A Auditoria marcou P2 como o MAIOR lever (~66% dos casos).

**Foi feito:**
- **P2 — gate de verificável-barato (estado do operador)** em `bot/memory/grounding.py`: numa
  retomada (gap > 30 min), além do "última msg há ~N", o bloco agora crava o FATO
  VERIFICÁVEL que o código conhece — "o operador acabou de te enviar a mensagem deste turno;
  ele está presente e falando contigo AGORA" — e NOMEIA o atalho proibido ("não afirme que
  está dormindo/ausente/ocupado; você não observa o estado dele"). Mata o caso-âncora "você
  está dormindo" na raiz: o prior do modelo ("tarefa longa → noite → dorme") perde pro fato.
  Atrás da flag existente `GROUNDING_SIGNALS_ENABLED`.
- **P1 — gate de estado de background vivo** (novo `bot/memory/background_state.py`): a cada
  turno o código LÊ os arquivos de estado dos trabalhos de background do tópico (Coder
  `coder-sessions/<thread>/*.json`), carimba a idade (last_activity, janela de 6h, cap 6) e
  injeta `[Estado de background vivo — LIDO AGORA]` com `state=` real + idade + a regra dura
  "use ISTO, não memória; o que não está aqui provavelmente terminou". É o conserto que o
  operador pediu pro usuário 2: o estado vivo EMPURRADO pelo código (como `[Alertas
  aguardando confirmação]`), não instrução mole que depende do agente lembrar de olhar.
  Novo param `background_state` no `build_prompt` (renderizado junto do grounding). Flag
  `BACKGROUND_STATE_GATE_ENABLED` (default-on, no-op sem trabalho recente).
- **Régua faithful ao P2:** `infra/eval/harness.py` passa a preservar `created_at` e injetar
  o grounding — assim casos COM timestamp medem o gate, não só o contrato.

**P5 (calibrar o daemon detector) = N/A:** o detector vivo é o daemon do Chat Manager, que a
F1 aposentou (`CHAT_MANAGER_ENABLED=false`) — não há daemon a calibrar.

**Honestidade sobre a régua (por que não há "resolve quantos" novo):** o baseline (1/18 = 6%)
já PASSA os casos de estado-do-operador (o contrato segura na reconstrução mínima do arnês);
o único que falha (`tenta-de-novo-receita`) é inércia-de-contexto (família F2), que NENHUM
gate de F5 mira (era território do P5, agora N/A). E os casos não têm timestamp, então o
arnês não dispara o P2. Ou seja: **a régua é estruturalmente cega aos gates** (cases
sintéticos, prompt mínimo, sem timestamp) — eles atacam modos de falha de PROD que o arnês
sub-reproduz. Validação real: unit tests (abaixo) + prod (operador). Não gastei Opus
re-rodando a régua pra reproduzir o mesmo 1/18.

**Testes (dev):** unit completo (`.local/test_f5.py`, fora do git): P2 dispara no gap com o
fato de presença + nomeia "dormindo"; gap curto → None. P1 lê os JSONs, filtra job velho
(>6h), ordena por idade, renderiza running+completed com a regra dura; sem job / thread None
→ None. `py_compile` de todos os 6 arquivos. Harness roda `--dry` limpo pós-edição.

**Reversão:** `GROUNDING_SIGNALS_ENABLED=false` (P2) e `BACKGROUND_STATE_GATE_ENABLED=false`
(P1); `git revert` desfaz. Read-only, nada destrutivo.

### Highlander v2 — F4: janela imediata bounded por TOKEN (anti-rajada-de-áudio)

**Operador pediu:** janela com teto de TOKEN — "hoje o teto é 60 mensagens; uma rajada de
áudios longos estoura" (queima o teto de 5h e dilui o contrato).

**Foi feito:**
- `bot/memory/working_set.py`: além do piso híbrido (10 min OU N msgs) e do hard cap de 60
  MENSAGENS, agora há `IMMEDIATE_TOKEN_CAP` (default 8000 tokens, env
  `WORKING_MEMORY_TOKEN_CAP`). `_bound_by_tokens` mantém as msgs mais RECENTES que cabem no
  teto e descarta as mais antigas da janela. Garante ao menos a última msg (cortar o
  contexto imediato do turno seria pior que o estouro). Estimativa barata (~4 chars/token,
  sem tokenizer). Cap ≤ 0 desliga (volta ao pré-F4).
- O núcleo curado (CURATED_CORE) já fica no TOPO do prompt (Frente 1.2) — a metade
  "núcleo estável cacheável" do plano já estava satisfeita.

**Deferido honestamente (precisa de observação/experimento, não de código pontual):**
- **P0b — cache do prefixo:** medir `cache_read_input_tokens`/`input_tokens` por turno
  exige `claude -p` em `--output-format` com usage + ~1 semana de telemetria. Mexer no
  formato de saída arrisca o parsing da resposta do bot — NÃO toquei. Fica como
  instrumentação a fazer com calma (não bloqueia o resto).
- **P6 — ENCOLHER os ~58 KB:** é experimento (subtrair texto do contrato e medir pela
  régua se a alucinação cai), não edição cega. Fica pra uma rodada própria com a régua.

**Testes (dev):** unit do `_bound_by_tokens` — under-cap mantém tudo; over-cap corta os
mais antigos preservando os recentes; última msg gigante sozinha é mantida; ordem
cronológica preservada; cap=0 vira no-op; vazio → vazio. `py_compile`.

**Reversão:** `WORKING_MEMORY_TOKEN_CAP=0` desliga o teto; `git revert` desfaz.

### Highlander v2 — F3: Hindsight assume o recall (recall cru + reflect citado)

**Operador pediu:** "uma roda de recall só (Hindsight)"; recall cru pro caminho barato,
reflect citado pro caminho confiável; aposentar o kobe-recall (vai junto com o CM).

**Foi feito:**
- **recall (caminho barato)** já wired no turno, atrás de `HINDSIGHT_RECALL` (sub-flag da
  F1). Re-ligar = `HINDSIGHT_RECALL=true` + restart. **Mantido OFF no deploy** (ver nota).
- **reflect (caminho confiável)**: novo helper `bot/bin/kobe-reflect "<pergunta>"` — resposta
  sintetizada e CITADA (`based_on.memories`) do bank do tópico atual, cético por construção
  (skepticism=5/literalism=5 + directive de Fundamentação). Resolve o tópico via
  `get_topic_slug` (KOBE_CHAT_ID/THREAD_ID) → bank `kobe-<slug>`. Best-effort: serviço fora =
  avisa e sai. Quando não há registro, diz "não há base; não afirme de memória" (em vez de
  confabular). `reflect_mission` força resposta em português.
- **kobe-recall aposentado junto com o CM**: o helper depende das tabelas `conversations`
  (populadas pelo daemon do Chat Manager). Com `CHAT_MANAGER_ENABLED=false`, o daemon fica
  inerte → o kobe-recall degrada (sem dado novo). O papel de recall durável passa pro
  Hindsight. O script fica (aposentar = desligar, não remover).

**Nota honesta sobre a régua (por que recall fica OFF no deploy):** o arnês `infra/eval/`
NÃO injeta o bloco de recall (o `build_prompt` recebe `durable_memory=None` no caminho do
arnês), e os casos são conversas sintéticas SEM memória durável correspondente — então a
régua **não consegue medir** o efeito do recall. O plano (§6) é explícito: "não religar a
injeção de recall sem a régua medir (risco da dor nº1)". Logo: o código está pronto e
reversível, mas o FLIP `HINDSIGHT_RECALL=true` fica pro operador validar em prod (onde há
memória real) — é a validação-de-produto dele, não automatizável aqui. O reflect (helper)
não tem esse risco (é on-demand, cético+citado, diz "não sei" sem inventar).

**Testes (dev, serviço vivo):** `kobe-reflect` sem arg → usage; com pergunta → reflete
contra o bank, devolve síntese citada ou "sem registro" (testado: respondeu "não tenho
informação" em vez de confabular). `py_compile` do helper + client.

**Reversão:** `HINDSIGHT_RECALL=false` (já é o default) mantém o recall mudo; `git revert`
remove o helper. Nada destrutivo.

### Highlander v2 — F2: re-fia o Hindsight pro best-practice (0.8.3)

**Operador pediu:** corrigir a fiação do Hindsight, que estava fora do manual (retain de
mensagem solta com id aleatório, sem context/tags, bank sem missão nem disposição).

**Por quê:** o anti-padrão do plano (§6) — "retain de mensagem solta com UUID aleatório
duplica documento; usar id estável" — era exatamente o que acontecia. E o bank não era
cético por construção, então o reflect (a peça-ouro anti-alucinação) não tinha como
"só responder do que está citado".

**Foi feito (verificado contra a API 0.8.3 ao vivo):**
- **retain agrupado:** `document_id` ESTÁVEL (= `session-<id>`) + `update_mode="append"` —
  a conversa vira UM documento que cresce, não N memórias soltas. + `context` ("Conversa
  Telegram, tópico X") e `tags` (`topic:<slug>`, `source:telegram`). Conservador de
  propósito: só a msg DO OPERADOR (ground truth) — NÃO a resposta gerada (anti-confabulação).
  A tensão "conversa inteira × só-operador" está documentada no módulo: a resolução grupa a
  conversa por id estável (conserta a duplicação, o defeito real) sem gravar texto gerado.
- **bank configurado (idempotente, 1× por processo via `_ensure_bank`):** disposições
  `skepticism=5`, `literalism=5` (cético+literal por construção) + `retain_mission` /
  `reflect_mission` (PATCH `/config`) + uma **directive** `kobe-fundamentacao` (POST
  `/directives`, criada só se não existe) que codifica a regra de Fundamentação como regra
  dura injetada em todo reflect.
- **recall melhorado:** `types=['world','experience']`, `budget='mid'`, filtro por `tags`,
  `include.source_facts` (rastreabilidade `document_id`/`chunk_id` em cada resultado).
- **reflect novo:** `reflect()` + `render_reflect_section()` — resposta sintetizada CITADA
  (`based_on.memories`), pro caminho confiável da F3.
- Gotcha 0.8.3 corrigido: `include.{source_facts|facts}` liga com `{}` (objeto vazio), não
  `true` (bool dá 422).

**Testes (dev, contra o serviço VIVO):** bank de teste isolado (`kobe-codertest-f2`):
retain (append, id estável) → recall (1 fato, doc=session-999, com tipo `experience`) →
reflect ("projeto é Kobe", `based_on:2` citações) → confirmado no `/config` que
skepticism/literalism=5 + as duas missões + a directive ficaram aplicados. Bank de teste
removido (DELETE 200) — Hindsight de prod intacto. `py_compile` de client+handler.

**Reversão:** `HINDSIGHT_RETAIN=false` para de gravar; `git revert` volta o client. As
disposições/missão/directive são config idempotente por bank — sem efeito destrutivo.

### Highlander v2 — F1: aposenta o Chat Manager + de-risca o recall do Hindsight

**Operador pediu:** aposentar o Chat Manager (reversível, sem remover código nem dropar
tabela) e parar de injetar o destilado do Hindsight todo turno, sem perder a construção da
memória.

**Por quê:** (1) o Chat Manager virou "armadilha do ponteiro" (título de assunto sem
conteúdo → o agente inventa o que tinha lá) e incha o prompt (quente/frio todo turno →
queima o teto de 5h). (2) o Hindsight estava com retain E recall na MESMA flag, ligados;
o recall injeta um bloco destilado por LLM a cada turno — a própria Auditoria nomeia
destilação automática como vetor de confabulação (a dor nº1).

**Foi feito:**
- `HINDSIGHT_ENABLED` continua como MASTER kill-switch; separadas duas sub-flags:
  `HINDSIGHT_RETAIN` (default ON — segue gravando em silêncio) e `HINDSIGHT_RECALL`
  (default OFF — para de injetar o destilado). Efetivo = master AND sub-flag.
  `bot/config.py` (campos + parse), `bot/telegram_handler.py` (gate do retain ~796 e do
  recall ~859), `.env.example` documentado.
- Chat Manager aposentado por flag: `CHAT_MANAGER_ENABLED=false` no prod `.env` (aplicado
  no deploy). O código fica; o daemon classifier vai inerte (checa a flag no tick); a
  janela imediata (working_memory, default ON) e o núcleo curado seguem intactos —
  decouple da Frente 0 garante que CM-off NÃO traz a compactação/amnésia de volta
  (`_load_history` keya em `working_memory_enabled`, não em `chat_manager_enabled`).

**Testes (dev):** `py_compile` de config+handler; teste dos novos campos no `Config` e dos
defaults (retain=ON, recall=OFF); varredura confirmando que a janela imediata e a
compactação não dependem de `chat_manager_enabled` (só de `working_memory_enabled`).

**Reversão:** `CHAT_MANAGER_ENABLED=true` religa o CM; `HINDSIGHT_RECALL=true` religa o
recall; `HINDSIGHT_ENABLED=false` desliga retain+recall. Tudo por env + restart, sem deploy.

### Corrigido — Highlander Frente 0: desacopla MEMÓRIA da flag de CONVERSAS

**Operador apontou:** "Chat Manager virou outra coisa, apenas classificação e gerenciamento
de conversas, não tem mais código sobre memória lá."

**Causa:** a Frente 0 moveu o *código* da memória pra `bot/memory/`, mas deixou o *controle*
ainda pendurado em `CHAT_MANAGER_ENABLED`: duas decisões de MEMÓRIA — qual janela de
histórico usar (imediata vs sessão legada) e se a compactação roda — pegavam carona na flag
de CONVERSAS. Spaghetti residual: trocar de assunto (conversa) e escolher janela de memória
estavam amarrados na mesma chave.

**Foi feito:** nova flag `WORKING_MEMORY_ENABLED` (default-on) governa SÓ memória —
`_load_history` (`telegram_handler.py:716`), a compactação (`:667`) e a janela do turno de
retomada (`resume.py:176`) passam a consultá-la. `CHAT_MANAGER_ENABLED` agora governa **só
conversas** (activity, ponteiros quente/frio, cronologia, comandos `/retomar` etc. —
verificados um a um). As duas chaves ficam independentes: dá pra ter memória-moderna com
conversas-off, e vice-versa.

**Comportamento preservado:** prod roda com `CHAT_MANAGER_ENABLED=true` (= janela imediata +
sem compactação); `WORKING_MEMORY_ENABLED` default-on entrega exatamente o mesmo. Zero
regressão; só separação limpa.

**Testes (dev):** import da cadeia; os dois campos coexistem no `Config`; default-on +
override `=false`; varredura confirmando que todo `chat_manager_enabled` restante é conversa.

**Reversão:** `WORKING_MEMORY_ENABLED=false` volta ao legado; `git revert` desfaz o decouple.

### Segurança — token do bot deixa de vazar nos logs (httpx → WARNING)

O `httpx`/`httpcore` logam a URL completa de cada request em nível INFO, e a URL da
API do Telegram embute o token do bot (`.../bot<TOKEN>/metodo`) — isso vazava o token
em texto puro no journal do systemd (que é persistente). `bot/main.py` agora sobe os
loggers `httpx`/`httpcore`/`telegram` pra `WARNING` logo após o `basicConfig`, cortando
o vazamento sem perder erros reais. Vale no próximo restart do bot. Reversível por
commit. (Descoberto numa sessão do plugin Coder ao investigar o cgroup das salas tmux.)

### Mudado — Highlander default-ON (decisão do operador 2026-06-24)

**Operador pediu:** "não deixe tudo atrás de flag-off esperando o operador apertar botão" —
as features do Highlander entram ligadas no ambiente de trabalho, prontas pra prod.

**Foi feito:** os defaults de `CURATED_CORE_ENABLED`, `GROUNDING_SIGNALS_ENABLED` e
`HINDSIGHT_ENABLED` passam a **on** quando a env não está setada (`os.getenv(..., "true")`),
em `config.py` + `.env.example`. Entram na prod pelo canal sancionado (merge-back + restart
do Hal) — não foi tocado o `.env` vivo da prod. Para desligar: setar a env como `false` +
restart.

**Segurança do default-on:** `curated_core` e `grounding` são puro-cômputo (no-op gracioso
se faltar arquivo/histórico); `hindsight` é best-effort — se o serviço estiver fora, falha
rápido (connection refused em ms) e o turno segue. **Tradeoff conhecido (repo potencialmente
público):** instalação fresca sem o serviço Hindsight loga um warning por turno até setar
`HINDSIGHT_ENABLED=false` — documentado no `.env.example`.

**Reversão:** env=false + restart, ou `git revert`.

### Adicionado — Highlander Frente 2.3: cliente Hindsight no bot (recall + retain)

**Operador pediu:** depois do smoke do Hindsight passar no prod, fiar a memória durável no
turno do bot.

**Por quê:** com o serviço de pé e o contrato REST verificado ao vivo, o bot pode trazer
fato durável de volta (recall) e destilar fato novo (retain) — o "trazer assunto velho de
volta" sem a maquinaria do Chat Manager.

**Foi feito:**
- `bot/hindsight_client.py`: `retain` / `recall` / `render_recall_section` sobre REST
  (httpx async), best-effort (qualquer falha → vazio/False, nunca levanta — Hindsight
  jamais derruba um turno) e por tópico (`bank_id_for_topic`, isolamento como o resto da
  memória). Coage `metadata` a `dict[str,str]` (o servidor dá 422 com valor int).
- Wiring no `telegram_handler`: **recall na entrada** → bloco `[Memória durável recuperada]`
  no prompt (moldura cética: é PISTA, confirme contra a fonte — contrato anti-mentira);
  **retain fire-and-forget** após persistir a msg, destilando fato **da mensagem do
  operador** (ground truth), não da resposta gerada (anti-alucinação). Fonte rastreável na
  metadata (tópico + message_id). Helper `_fire_and_forget` segura a ref da task (senão o GC
  coleta antes de rodar). `build_prompt` ganha o param `durable_memory`.
- Flags em `config.py` + `.env.example`: `HINDSIGHT_ENABLED` (default off), `_BASE_URL`
  (`http://127.0.0.1:8888`), `_TIMEOUT_SECONDS` (10), `_RECALL_LIMIT` (5).

**Testes (contra o serviço VIVO no prod, imagem 0.8.3):** retain → recall ponta a ponta
(o fato plantado volta renderizado); coerção de metadata (resolve o 422 real); best-effort
com serviço fora-do-ar (retorna vazio/False sem exceção); `build_prompt` injeta
`durable_memory` com dado e omite sem. Banks de teste criados foram **deletados** (serviço
ficou com zero banks).

**Tradeoff conhecido:** o retain roda por mensagem (não por silêncio). Custo do retain é
mini-tier OpenAI (~2.8k tokens in/retain), negligível, mas pra operador muito tagarela pode
valer mover pro daemon-por-silêncio depois. Atrás de flag — validar no prod-staging.

**Reversão:** flag off + restart = Kobe como hoje. `git revert` (sem banco do Kobe; o
Hindsight tem storage próprio isolado).

### Corrigido — tag da imagem do Hindsight (`v0.8.3` → `0.8.3`)

**Sintoma:** `docker compose up -d` falhou no prod com `failed to resolve reference
"ghcr.io/vectorize-io/hindsight:v0.8.3": not found`.

**Causa (verificada na GHCR):** os releases do GitHub usam tag com `v` (`v0.8.3`), mas a
**imagem Docker** no GHCR é tagueada **sem o `v`** (`0.8.3`). Pinei pela versão errada.

**Foi feito:** `HINDSIGHT_VERSION` corrigido pra `0.8.3` no compose + `.env.example`, com
nota explícita do gotcha; referências em README/CHANGELOG/plano alinhadas. Confirmado via
GHCR registry API que `0.8.3` resolve (HTTP 200) e `v0.8.3` não (404).

**Reversão:** `git revert` (só troca de string de tag).

### Adicionado — Highlander Frente 1.1: sinal de grounding temporal na entrada

**Operador pediu:** continuar a Frente 1 (memória confiável) — os gates de grounding
baratos resolvidos no código (P2 do v4).

**Por quê:** o contrato manda "nada relativo ao TEMPO sem conferir o tempo" e alerta que
"retomada depois de um tempo: o contexto recente pode não ser sobre o que ele quer agora".
O cabeçalho já dá o `[Agora]`, mas faltava **há quanto tempo foi a última troca** — sinal
que o agente senão narraria de memória (fonte clássica de confabulação ao retomar).

**Foi feito:**
- `bot/memory/grounding.py`: `render_grounding_signals(history)` lê o `created_at` que já
  veio no histórico imediato (sem query nova, sem LLM) e injeta uma linha `[Grounding]`
  com o gap humanizado (min/horas/dias). Só fala quando o gap passa de 30 min (retomada);
  num papo contínuo fica calado pra não virar ruído. A msg nova ainda não está no histórico
  na hora da montagem, então o gap é de fato "tempo desde a última troca" (verificado: o
  handler persiste a msg depois de montar o contexto).
- Fiado em `build_prompt` (logo após `[Agora]`, mesma natureza temporal) + `telegram_handler`
  + `resume`. Flag `GROUNDING_SIGNALS_ENABLED` (`config.py` + `.env.example`), default off.

**Testes (dev VPS, venv da prod):** import da cadeia OK; campo `grounding_signals_enabled`
no `Config`; teste de `render_grounding_signals` (gap curto = None; min/horas/dias;
histórico vazio/sem timestamp = None); `build_prompt` injeta após `[Agora]` com a flag e
omite sem ela (off = no-op).

**Reversão:** flag off + restart = comportamento de hoje. `git revert` (sem banco).

**Pendente em 1.1 (não nesta entrega):** o gate P1 (injetar estado de trabalho em
background lido do `.json`) — overlapa a maquinaria de missão/despacho existente e pede
cuidado; fica pra um passo seguinte.

### Adicionado — Highlander Frente 1.2: núcleo curado global (USER.md + MEMORY.md auto-injetado)

**Operador pediu:** atacar a confiança na memória — começando pelo núcleo curado
estilo Hermes (identidade + fatos duráveis auto-injetados).

**Por quê:** hoje o USER.md **não** entra no prompt — depende da instrução "leia o
USER.md" no CLAUDE.md, que o agente pode pular. O operador não confia numa memória que
o agente "às vezes lê". Núcleo curado pequeno e estável no topo, por construção todo
turno, é a base de identidade que faltava (e, de bônus, prefixo mais cacheável).

**Foi feito:**
- `bot/memory/curated_core.py`: `load_curated_core(kobe_home)` lê
  `user-data/identity/USER.md` + `MEMORY.md`, monta o bloco `[Núcleo curado]` com TETO
  fixo (~6000 chars) — USER.md tem prioridade, MEMORY.md espreme — e, perto de 80% do
  teto, anexa um empurrão pro agente CONSOLIDAR (esquecimento ativo). O código nunca
  apaga fato sozinho (anti-alucinação): quem consolida é o agente, editando o arquivo.
  Read-only e tolerante a ausência (None = no-op).
- `build_prompt` (`claude_runner.py`) e o turno de retomada (`resume.py`) ganham o param
  `curated_core` e injetam a seção logo após o cabeçalho `[Agora]`, como base de identidade.
- Flag `CURATED_CORE_ENABLED` (`config.py` + `.env.example`), default **off** = Kobe de
  hoje. Coletado em `telegram_handler.py` e `resume.py` só quando ligada.
- `user-data/identity/MEMORY.md.example`: template versionado do núcleo do agente
  (como USER.md.example), com as regras de uso (pequeno, consolidar, fato confirmado).

**Testes (dev VPS, venv da prod):** import da cadeia (`bot.config`→`telegram_handler`/
`resume`) OK; campo `curated_core_enabled` presente no `Config`; teste de
`load_curated_core` com dir temporário (monta USER+MEMORY; trunca no teto; sinaliza
consolidação); `build_prompt` injeta a seção com a flag e omite sem ela (off = no-op),
e o núcleo vem antes da `[Mensagem nova]`.

**Reversão:** flag off + restart = comportamento de hoje. `git revert` do commit (sem
banco). Reversível por construção.

### Adicionado — Highlander Frente 2: infra do Hindsight (memória durável), provisão

**Operador pediu:** subir o Hindsight em modo serviço + Postgres dedicado (pgvector),
conforme o plano v4 §6, com validação no prod VPS.

**Por quê:** a memória durável (recall cross-sessão) é o que dá "memória infinita" e
"trazer um assunto velho de volta" sem a maquinaria do Chat Manager. Precisa de storage
próprio (pgvector) separado do Supabase e da Evolution.

**Foi feito (só infra/autoria — NÃO sobe container, NÃO roda SQL):**
- `infra/hindsight/docker-compose.yml`: `hindsight-postgres` (`pgvector/pgvector:pg18`,
  volume dedicado, healthcheck) + `hindsight-app` (`ghcr.io/vectorize-io/hindsight:0.8.3`
  pinado, Postgres externo via `HINDSIGHT_API_DATABASE_URL`, OpenAI LLM+embedding
  `text-embedding-3-small`, portas 8888/9999 em loopback). Volume montado no PGDATA do
  PG18 (`/var/lib/postgresql/18/docker`) pra persistir de verdade.
- `infra/hindsight/.env.example` (senha + chave OpenAI; `.env` real fica gitignored).
- `infra/hindsight/smoke_test.py`: smoke isolado via REST (retain→recall de fato plantado,
  mede latência + `usage`), stdlib só, descobre paths via `/openapi.json`. **Roda no prod VPS.**
- `infra/hindsight/README.md`: runbook (subir/derrubar/backup), SQL de contingência
  documentado (`CREATE EXTENSION vector`, rodado pelo operador, não por mim), troubleshooting.

- `infra/hindsight/up_and_smoke.sh`: wrapper que sobe o stack, espera o serviço responder
  e roda o smoke numa tacada (evita rodar o smoke cedo demais). **Executado pelo
  Hal/operador no prod VPS** — a sessão Coder não sobe container. README com o passo exato
  (rodando do worktree, que está fisicamente no prod, sem depender do merge-back).

**Decisões aplicadas:** pg18 default (não pg16); Hindsight 0.8.3 (imagem; release v0.8.3); OpenAI;
validação no prod VPS (operador não testa em dev).

**Testes (dev):** AST parse do smoke; YAML do compose válido (2 services). O teste real
(subir stack + smoke) roda no prod VPS pelo operador — é o aceite da Frente 2.2.

**Reversão:** `git revert` (só arquivos novos, nada ligado ainda); no runtime, o stack é
`docker compose down -v` (isolado, não toca Supabase/Evolution).

### Refatorado — Highlander Frente 0: memória de trabalho ganha casa própria (`bot/memory/`)

**Operador pediu:** implementar o Highlander (reforma da memória) conforme o plano v4
aprovado — começando por "arrumar a casa" (Frente 0).

**Por quê:** o contexto imediato (memória pura — consulta `messages` só por `topic_id`,
não toca `conversations`) morava dentro de `bot/chat_manager/`, o gerenciador de
**conversas**. Isso é o spaghetti que o v4 §0 manda desfazer: cada coisa faz uma coisa,
com fronteira clara. Regra de ouro (v4 §1): a memória pode consumir dado de conversa, mas
**conversa nunca monta a janela**.

**Foi feito:**
- Novo módulo `bot/memory/` (casa da memória de trabalho). `bot/memory/working_set.py`
  recebe `get_immediate_messages` + `_parse_ts` + constantes `IMMEDIATE_*`, movidos de
  `bot/chat_manager/context.py` **sem mudar comportamento** (movimento byte-idêntico).
- `chat_manager/context.py` fica só com os blocos de **conversa** (quente/frio/relações).
  Mantido o nome `render_chat_manager_section` (e **não** renomeado pra `memory_context`
  como o v4 sugeria à letra) porque esses blocos são de conversa, não de memória — decisão
  fundamentada no que o código de fato faz.
- Call sites atualizados: `telegram_handler.py` e `resume.py` importam
  `get_immediate_messages` de `bot.memory`.
- **Não** mexido: `conversation_detector.py` (o v4 dizia "morto", mas é importado por
  `context.py`/`classifier.py`/`turn_classifier.py` por utils compartilhados — corrigido).

**Testes (dev VPS, venv da prod):** AST parse dos 5 arquivos; import real da cadeia
completa (`bot.memory` → `bot.telegram_handler`) OK; teste de comportamento da janela
imediata com fake DB (janela 10 min = 11 msgs; piso sem inventar msg; filtro de
`[Resumo da sessão` preservado). Sem suite automatizada no repo pra isso ainda.

**Reversão:** `git revert` do commit (refactor puro, sem banco, sem flag). Reversível por
construção.

### Adicionado — guardrail de fundamentação (anti-confabulação)

Nova seção `## Fundamentação — a regra acima de todas` no topo do `CLAUDE.md`: o
agente só afirma como fato o que está no contexto ou que acabou de verificar; o
inverificável (estado do operador, comportamento de app externo, fato do mundo fora
do contexto) **não se afirma** — no máximo hipótese marcada. Sua própria sugestão não
é decisão do operador. O verificável (hora, status de trabalho, arquivo) confere antes
de afirmar.

Saiu da **Auditoria da Verdade** (2026-06). Medido com um arnês de regressão
(`infra/eval/`, juiz gpt-4o): no caso residual onde o Opus 4.8 ainda escorrega
(afirmar comportamento de sistema que não observa) a regra leva a confabulação de
**~40% → 0%** (n=5). Nas demais classes testadas (auto-confirmação, causa-inventada,
inércia de contexto) o Opus 4.8 já aterra sozinho. Conserto de framework (vale pra
todo usuário); reversível por commit. Auto-discovery do `CLAUDE.md` → vale no próximo
turno, sem reinício.

**(2026-06-23) Endurecido com os casos reais** (auto-confirmação pega ao vivo: Mnemosyne
22/06, modelo-escalonado 09/06): regra macro *"você não tem permissão de mentir, em
nenhuma circunstância"*; cláusula de proveniência afiada — **o erro mora nos RESUMOS**
(silêncio / "deixa eu pensar" / mudar de assunto não é aceite nem recusa; nunca escrever
"você topou/decidiu X" sem fala explícita); + regra de **retomada-após-tempo** (o contexto
recente pode não ser sobre a intenção atual); + verificar o que **muda com o tempo**
(status de sala/sessão pode estar defasado). Validação dessas classes é por **uso real** —
o harness de fixture dá falso-negativo nelas (são contexto-sensíveis).

**(2026-06-23, 2ª rodada) Disciplina de leitura de fonte dinâmica** — cascata real pega ao
vivo (Dev Kobe, **com o contrato já no ar** → guardrail mole recai, como a auditoria previu):
ao ler pane/`git`/log/processo/`.jsonl`, **só afirmar o que está literalmente no output**.
Proibido *input-fantasma* (inferir que o operador digitou algo num pane); `mtime` ≠ atividade;
output vazio/erro pode ser **falta-de-acesso, não ausência**; não cravar causa de evidência
parcial. Promovida da memória privada do agente pro contrato (vale pra toda instância). Reduz
a superfície da classe mais recorrente; não é garantia.

## [0.15.0] — 2026-06-12 — Consolidação da pilha no main (Apolo + chat-manager v2 + perf SPR + UX + alertas + integrations)

### Kobe Integrations v1 — broker de capacidades

### Adicionado — catálogo de capacidades + switchboard

Subsistema de core pra plugins cooperarem **sem se acoplar pelo nome**. Um
plugin se anuncia como provedor de uma capacidade abstrata (ex:
`code-tracking`); outro pede "quem provê X?" — e um roteador fino casa os dois
sem que nenhum saiba o nome do outro. Resultado N+M, não N×M. Seta de
dependência aponta do específico (plugin) pro genérico (capacidade do core).

O Integrations é **magro**: guarda só o índice `capacidade → quem provê`, a
definição de contrato (declarativa), e o roteador. Lógica de negócio mora no
plugin dono — é o `information_schema` + FK do banco, não a stored procedure.

- **`bot/plugins.py`** — estende o parser do manifest pra ler o bloco
  `integrations:` (`provides` = capacidade + handler; `consumes` = etiqueta).
  Trata o manifest como hostil: valida nome da capacidade (`[a-z0-9-]`) e
  rejeita handler que escape da raiz do plugin (`../`). Novo
  `build_capability_index()` monta o índice `capacidade → provedor`; conflito
  (dois plugins, mesma capacidade) **trava** a capacidade e loga ERROR — não
  escolhe vencedor sozinho.
- **`bot/bin/kobe-integrations`** — a switchboard (no padrão dos `kobe-*`):
  - `provider <capacidade>` → imprime o provedor (ou `!=0` se não há / travado).
  - `invoke <capacidade> <verbo> [payload]` → resolve o provedor, chama o
    handler dele (executável agnóstico de linguagem: verbo no argv, payload no
    stdin, JSON no stdout) e repassa a resposta. O consumidor nunca vê o nome
    do provedor. Erros explícitos (sem provedor / conflito / handler ausente /
    handler falhou / JSON inválido), cada um com código de saída próprio.
- **`bot/main.py`** — no startup, monta o índice de capacidades e loga
  quantas estão indexadas / em conflito (guarda em `bot_data`).
- **Contrato da 1ª capacidade `code-tracking`** (`docs/integrations/`): dois
  verbos — `ensure(briefing)→{rc,card_id,meta}` (achar-ou-criar é problema do
  provedor) e `finished(card_id,desfecho)→{rc}`. Só a definição; sem provedor
  real na v1.
- **`docs/plugins-autoria.md`** — manual de como um plugin provê/consome uma
  capacidade.
- **`examples/integrations/code-tracking-stub/`** — plugin de exemplo com
  handler stub, usado pra testar a switchboard ponta-a-ponta.

Decisões da v1: conflito de provedor trava + avisa (não auto-escolhe);
`consumes` é só declarativo (não bloqueia o plugin de rodar sem parceiro);
payload trafega por stdin (suporta briefing grande/multilinha sem quebrar).

### Pacote UX de resposta, chat-manager v2, Apolo e perf (SPR)

- **Pacote UX de resposta — ack que nomeia a ação + background narrado pelo
  Hal (Fases B/C, 2026-06-05).** Unifica foreground e background num padrão só:
  **ack que nomeia a ação → `digitando` vivo → entrega**. (B) Instrução no
  `CLAUDE.md` ("Avisa antes de agir"): quando o Hal vai usar ferramenta com
  latência perceptível e não responde na hora, emite primeiro um `kobe-notify`
  curto **nomeando** o que vai fazer, depois chama a ferramenta — gatilho é
  intenção de agir, não cronômetro. O `digitando` vivo já existia (foreground
  renova a cada 4s). (C) Background deixa de ter aviso enlatado: na **previsão**,
  a run de bg recebe uma **nota de handoff** no prompt (`build_prompt
  background_handoff`) que a manda abrir com um ack na própria voz e reler a
  **janela de frescor** (`bot/bin/kobe-recall-since '<ISO>'` +
  `topic_manager.get_messages_since`) antes de agir; no **promote** (retaguarda
  do teto), consome a run em voo (não recomeça) e **suprime o enlatado quando o
  Hal já ackou** (`ProgressReporter.acked` detecta `kobe-notify` no stream),
  caindo no enlatado só como rede quando não houve ack. Aditivo e reversível
  (revert volta ao notice enlatado). Runbook:
  `docs/runbooks/ux-resposta-ack-despacho.md`.
- **Despacho de turno pesado em background (cascata de filtros).** O lock por
  tópico serializa os turnos de um mesmo tópico — correto pra consistência,
  mas um turno pesado do Hal (editar código, varrer repo, análise longa)
  segurava a linha e prendia a próxima mensagem do operador atrás dele.
  Agora, com `HEAVY_DISPATCH_ENABLED=true`, a ENTRADA do turno classifica se o
  pedido vai ser pesado e, se for, despacha o `claude -p` em background FORA do
  lock — o atendente (Hal) fica livre pro próximo pedido na hora. Dois
  caminhos pro background, ambos com aviso imediato ao operador:
  (1) **previsão** — a cascata crava pesado na entrada (aviso antes de começar);
  (2) **retaguarda** — turno que entrou foreground mas estoura
  `HEAVY_DISPATCH_PROMOTE_AFTER_SECONDS` (default 12s) segurando o lock se
  promove sozinho (aviso no momento da promoção; o `claude` em voo **não
  recomeça**, continua e reporta no fim). A cascata (`bot/turn_classifier.py`):
  roteamento por tipo de slash → placar estrutural + léxico → GPT-4o-mini só na
  zona cinza (fora da cota do plano Max). Modelo de execução: `asyncio` task
  in-process fora do lock (mesmo padrão de handoff/compactor/resume), reusando
  o `ClaudeRunner` e a persistência/log do tail. Flag off → caminho clássico
  intacto (rollback trivial). Testes: `tests/test_turn_classifier.py`.
  Runbook de validação: `docs/runbooks/despacho-turno-pesado.md`.
- **Aviso discreto de troca de assunto (Chat Manager).** Quando o detector
  fecha a conversation ativa e abre uma nova por borda de assunto (transição
  real, não o bootstrap do 1º assunto do tópico), o daemon manda uma linha no
  Telegram: "📑 Novo assunto detectado — abri uma conversa nova pra isso."
  Antes a troca era silenciosa — com a calibração 0.55 ela passa a acontecer
  mais, então o operador precisa saber que o ponteiro "quente" mudou. O
  `classify_topic` reporta as transições em `ClassifyResult.new_conversations`;
  o `ClassifierSource` envia via subprocess `kobe-notify` (mesmo padrão do
  circuit breaker — envs `KOBE_*` + chat/thread vindos do topic), best-effort,
  fora do caminho do turno. Testes: `tests/test_chat_manager_transition.py`.

### Corrigido

- **Título da conversation agora é o TEMA, não a 1ª frase literal.** O título
  vinha de `_title_and_slug_from_message(seed)` — a primeira frase do seed
  truncada em 60 chars, irreconhecível depois ("Eu tô vendo aí que pelas
  instruções que você tá colocando…"). Agora um GPT-4o-mini nomeia o tema em
  3-6 palavras a partir das primeiras ~5 msgs do operador do segmento
  ("Formato do Progress Report", "Problemas com sessões caídas no tmux"). É a
  MESMA chamada que já gerava as tags — `_make_title_and_tags` devolve
  `{title, tags}` num call só, então **custo zero novo**; roda no daemon, fora
  do turno; fallback pro título literal se a chamada falhar (`title=None` →
  `_create_conversation` cai no seed). O slug é derivado do tema. O aviso de
  troca de assunto (acima) passa a incluir o tema: "📑 Novo assunto detectado —
  abri uma conversa nova: «tema»." Comparativo em dados reais mostrou a
  alternativa sem LLM (keywords) produzindo salada de palavras — o modelo
  barato ganhou o lugar. Testes: `tests/test_chat_manager_transition.py`.

- **Granularidade do Chat Manager calibrada (`CM_BORDER_SIM` 0.40 → 0.55).**
  Em tópicos de vocabulário homogêneo (Dev Kobe) o detector quase nunca abria
  conversation nova — dias de trabalho viravam um blob só (104 msgs em 13h num
  caso real). Diagnóstico com dados reais: a similaridade mediana ao centroide
  é 0.63 e só 3,2% das msgs ficavam abaixo do border 0.40, então a borda quase
  nunca disparava. Subir pra 0.55 (mantendo `CM_SUSTAIN=3`) dobra a
  granularidade real (7→10 conversations no replay) rachando os blobs em pontos
  que são trocas de assunto reais, sem reabrir o problema de msgs curtas/vagas
  (essa proteção é o gate `is_informative` + sustain + coherence, não o border).
  Aplicado via `.env` do prod + restart do keyko (`knobs_from_env`) — **sem
  deploy de código**, default do código segue 0.40 (testes intactos),
  reversível em uma linha. Só afeta classificação futura; blobs existentes
  ficam como estão. Diagnóstico, evidências e trade-off completos em
  `docs/chat-manager/bug1-granularidade-proposta-2026-06-04.md`.
  Nota: o knob vivo é `border_sim_threshold` do `bot/chat_manager/classifier.py`
  (daemon) — o `conversation_detector.py` (`THRESHOLD_LOW`) está morto no
  caminho ativo desde a migração de 2026-06-01.
  Card Flow: `fb0bdaa3-d5e2-4c00-9f9a-554028128fee`.

- **Compactação de sessão deixou de ser silenciosa.** Quando a sessão
  legada cruza `COMPACT_THRESHOLD_MESSAGES` (default 40) e compacta
  (`bot/compactor.py`, disparado em `_handle_user_text`), o operador agora
  recebe um aviso curto via Telegram **assim que a compactação começa** —
  antes da geração do resumo (que custa alguns segundos de Claude). O tom
  tranquiliza: nada se perde, a conversa continua de onde estava. Antes o
  único aviso saía DEPOIS de pronto (tom de "gerei um resumo"), deixando o
  operador no escuro durante o resumo. Implementado via callback `on_start`
  injetado no `compact_session` (best-effort: falha no aviso não derruba a
  compactação; dispara 1x por evento, nunca em sessão vazia). Testes:
  `tests/test_compactor_notify.py`.
  **Nota de escopo:** esta compactação legada só roda com
  `CHAT_MANAGER_ENABLED=false` — que é o **default do framework** (todas as
  instalações públicas). Com Chat Manager ligado (runtime do operador em
  prod) a compactação não roda; o aviso cobre o default público.
  Card Flow: `9b0b6638-c2d5-4602-887c-e9fa07aa2db3`.

- **Retomada de contexto após restart: o boot-resume agora RE-SITUA o
  agente, não só pinga o operador.** Até aqui, no boot o bot mandava um
  template fixo em Python (`render_resume_message`: "⏯️ Voltei, você tinha
  mandado X", citando só a última fala do operador) e **nunca invocava o
  agente**. Ele só voltava a se "inserir no fluxo" se/quando o operador
  mandasse uma mensagem nova — então, numa retomada, o contexto imediato
  (≈últimos 10 min) não chegava ao agente. Agora, pra cada tópico com
  snapshot pendente, o novo `bot/resume.py` monta o **mesmo contexto de um
  turno normal** (camada imediata via `get_immediate_messages` + ponteiros
  do Chat Manager + cronologia comprimida + KB do tópico + alertas/missão
  abertos) e invoca o agente com uma diretiva de retomada. Ele relê,
  entende onde a conversa estava e manda ao operador uma síntese real de
  onde param (em vez de um template). Salvaguardas: roda sob o lock do
  tópico (serializa com o handler normal), pula se o operador já voltou a
  falar pós-restart (guarda de atividade — sem ping duplo), cai no template
  antigo se o agente falhar (nunca regride a silêncio), e persiste a síntese
  como `messages` (role=assistant) pra entrar na janela imediata do próximo
  turno. Caminho do Chat Manager (injeção no turno normal) intacto. Compactação:
  com `CHAT_MANAGER_ENABLED=true` não roda (a janela imediata é reconstruída
  crua do tópico a cada turno, então o tail sobrevive); no legado o tail cru
  se perde no resumo, mas o legado está em desuso. Testes: `tests/test_resume.py`.
  Card Flow: `6cec4584-ee6e-41b4-a7e2-678022554a3c`.

## [0.15.0] — Tag de áudio transcrito + fix latência de áudio (2026-06-04)

### Adicionado

- **Tag visível de áudio transcrito no contexto do agente.** Quando o
  operador manda uma mensagem de voz, o bot transcreve via Whisper/Groq
  (ou AssemblyAI no fallback) e o texto resultante agora entra no prompt
  marcado com `🎤 [áudio transcrito]` — tanto na `[Mensagem nova do
  operador]` quanto nas linhas de `[Histórico recente]` que vieram de voz.
  Assim o Hal sabe que aquele conteúdo foi falado (tom de fala, possível
  ruído de transcrição), não digitado. A tag fica **só no prompt** — não é
  ecoada de volta no chat (o operador já sabe que mandou áudio; ecoar seria
  ruído). Aproveita o booleano `audio_transcribed` que já era persistido em
  `messages`, agora também carregado junto do histórico
  (`get_recent_messages` / `get_immediate_messages`) pra consistência turno
  a turno. Card Flow: `b9fe59fa-8351-40fc-84c4-db651095564c`.

### Corrigido

- **Latência de áudio: transcrição saiu de dentro do lock do tópico.** O
  handler `on_voice` pegava o lock por tópico ANTES de baixar/transcrever o
  áudio. Como o lock só libera quando o `claude_run` da mensagem anterior do
  mesmo tópico termina (60–300s), cada áudio ficava ENFILEIRADO atrás do LLM
  do áudio anterior antes de sequer poder ser transcrito. Nos logs: áudio de
  24s recebido às 22:42 só transcrito 241s depois; bursts de 5 voice notes
  em fila por ~5 min — embora a transcrição em si leve 3–4s. Agora o
  download + transcrição (função pura, sem estado compartilhado) rodam FORA
  do lock: áudios em fila no mesmo tópico transcrevem em paralelo (cada um em
  sua thread) enquanto um turno anterior ainda processa no Claude; o lock
  passa a cingir só o `_handle_user_text` (insert + claude). Some também o
  silêncio durante a transcrição — o "digitando…" dispara assim que o áudio
  chega. `transcribe()` passou a retornar `(texto, engine)` (em vez de só o
  atributo compartilhado `last_engine_used`) pra ser seguro sob concorrência;
  o aviso de fallback do AssemblyAI segue intacto. Novo log `audio_transcribe`
  mede download e transcrição separadamente. Card Flow:
  `027d3442-cc41-4e2a-b8b6-28d3ffbb85c2`.

## [Não lançado] — Reversão do streaming + fix de perda de resposta (2026-06-01)

Reverte o streaming token-a-token introduzido no mesmo dia (SPR P1 #1) e
corrige, na raiz, um bug que ele expôs: respostas longas com tool call no
meio chegavam truncadas ao operador.

### Corrigido

- **Resposta engolida antes de tool call.** O texto final vinha do campo
  `result` do stream-json, que carrega só a ÚLTIMA mensagem do assistant
  (o bloco emitido depois da última ferramenta). Quando o Hal escrevia
  prosa, rodava uma tool (ex.: gravar no Flow) e emitia um "Anotado em…"
  curto depois, a prosa era descartada — o operador via só o trecho final.
  Agora a resposta é a **concatenação de TODOS os blocos de texto do
  agente principal** no turno (`_join_texts` sobre os eventos `assistant`
  com `parent_tool_use_id` nulo); `result` segue lido só pra métricas e
  como fallback. Validado e2e (prosa pré-tool preservada, ruído de
  subagente filtrado, parcial de timeout recuperado dos blocos completos).

### Removido

- **Streaming token-a-token pro Telegram (era v0.15 / P1 #1).** Editar a
  mesma mensagem a cada ~1s rolava a tela e tirava o operador do ponto de
  leitura ("pior a emenda que o soneto" — decisão registrada em
  `user-data/knowledge/kobe/preferencias/design-arquitetura.md`). Saíram:
  flag `--include-partial-messages`, callback `on_text_delta` /
  `TextDeltaCallback` e a classe `_StreamingReply`. O sinal de vida volta
  a ser só o `ProgressReporter` (status por etapa) e a resposta sai
  **inteira de uma vez** via `_send_long_text` (com fatiamento no limite
  do Telegram). Card Flow: `afbee37e-d2db-45a3-9210-c05b4583c080`.

## [Não lançado] — New Chat Manager (2026-06-01)

Redesenho do Chat Manager pra matar a latência e a granularidade macro.
Princípio: **o turno é burro e rápido; toda inteligência cara roda atrás,
assíncrona**. Design completo em
`user-data/knowledge/kobe/brainstorms/new-chat-manager-arquitetura.md`;
calibração em `docs/chat-manager/calibracao-2026-06-01.md`. Tudo atrás da
flag `CHAT_MANAGER_ENABLED` (off = baseline estável; rollback = off + restart).

### Adicionado / Mudado

- **Detector sai do caminho crítico.** `telegram_handler` não chama mais
  `detect()` síncrono no turno (era a fornalha de latência/custo —
  embedding + LLM judge antes do 1º byte). O turno agora só toca um sinal
  de atividade e lê ponteiros residentes já mastigados pelo daemon.
- **Classificador-bibliotecário (`bot/chat_manager/`).** Novo ofício do
  Keyko (`ClassifierSource`): debounce por silêncio (~60s) + disjuntor de
  teto; roda atrás da resposta. Calcula embedding das msgs novas, detecta
  **bordas de assunto grosso em retrospecto** e carimba
  `messages.conversation_id` (a conversation vira FAIXA derivada). Nunca
  Opus, nunca no turno — embedding + álgebra de vetor + modelo barato só
  pra tags.
- **Detecção de borda (5 pilares + pista lexical).** Contra o acumulado,
  histerese (permanência), voto ponderado por informação, corte
  retrospectivo (tail ambíguo decide na próxima passada), hierarquia
  grosso/fino. Híbrido vetor + pista de troca explícita ("muda de
  assunto", "deixa X de lado") — embeddings de msg curta em PT têm cosseno
  comprimido, então a pista lexical é discriminador crítico. Viés
  deliberado contra over-cut.
- **Prompt em 4 camadas (`build_prompt`).** Imediato (últimos ~2 min OU N
  msgs do tópico, sempre, do disco — compactação vira não-evento) +
  ponteiro do quente + catálogo frio (tag cloud) + relações (similaridade
  de centroide on-the-fly). Verbatim do quente / busca fria sob demanda.
- **`bot/bin/kobe-recall`.** Helper pro agente puxar a faixa inteira de um
  assunto (`--conversation <id>`) ou buscar por tema (busca vetorial no
  tópico) sob demanda.
- **Migration 003 (aditiva).** `messages.conversation_id` + índice;
  ivfflat em `messages.embedding`; tabela `conversation_tags`. Banco
  compartilhado dev/prod → roda 1x, manual no Supabase (REST não faz DDL).
- **Calibração + testes.** Harness com corpus rotulado
  (`infra/calibrate_chat_manager.py`) escolheu border=0.40, sustain=3,
  coherence=0.35 (4/4 casos). Testes determinísticos em
  `tests/test_chat_manager_classifier.py` (7/7). Knobs ajustáveis por env
  (`CM_BORDER_SIM`, `CM_SUSTAIN`, `CM_CLUSTER_COHERENCE`, ...) sem deploy.

### Limitações conhecidas

- Troca de assunto SEM pista lexical e com vetor pouco distinto (assuntos
  vizinhos) tende a NÃO cortar — viés conservador (quente cresce; re-corte
  é de graça; operador pode `/nova`). Recalibrar via env se necessário.
- Busca vetorial do frio (`kobe-recall`) ranqueia em Python (escala de um
  operador) — sem RPC pgvector ainda.
- Notice de borda ao operador desligado nesta fase (UX limpa; o estado
  vive nos ponteiros do prompt).

## [Não lançado] — Performance percebida (SPR 2026-06-01, P1)

Diagnóstico em `docs/spr/2026-06-01-performance.md`. Implementados os P1 de
performance, exceto a troca de modelo (Opus mantido por decisão do operador).

### Adicionado / Mudado

- **Streaming da resposta (P1 #1)** — ⚠️ REVERTIDO no mesmo dia (ver seção
  "Reversão do streaming" no topo). UX ruim em mensageiro + expôs bug de
  perda de resposta. O sinal de vida ficou no `ProgressReporter`.
- **Timeout não descarta trabalho (P1 #4)** — `ClaudeTimeoutError` carrega
  `partial_text`; ao estourar o tempo, entrega o que o agente já completou
  + nota de interrupção, em vez de só a mensagem de erro. (Mantido após a
  reversão — agora o parcial vem dos blocos `assistant` completados, não
  mais dos deltas de streaming.)
- **KB de tópico sob demanda (P1 #3)** — `load_topic_context` mantém
  `prompt.md` inline mas injeta a pasta `knowledge/` grande como índice
  (caminho + prévia) acima de `TOPIC_KNOWLEDGE_INLINE_LIMIT` chars (env,
  default 8000); o agente lê com `Read` quando precisa. Corta ~12k
  chars/turno em olimpo e dev-kobe.
- **I/O fora do event loop (P1 #5)** — transcrição (Groq/AssemblyAI) e as
  leituras independentes (histórico + contexto de tópico) rodam em
  `asyncio.to_thread`/`gather`, sem travar o loop nem serializar tópicos.

### Pendente (decisão do operador)

- **Modelo (P1 #2)** — mantido Opus por ora; Sonnet no caminho
  conversacional fica para avaliação futura.
- olimpo guarda instruções dentro de `knowledge/` (não em `prompt.md`) —
  com o modo índice elas passam a ser lidas sob demanda. Mover para
  `prompt.md` se quiser presença garantida todo turno.

### Sistema de Alertas (Fase 1)

### Adicionado — agente proativo (Alertas como 2ª Source do Keyko)

Capacidade core: o operador pede em linguagem natural ("me lembra toda
terça…", "todo dia 7h faça X", "amanhã 15h…") e o Hal passa a disparar
sozinho no horário. Reusa o daemon Keyko — Alertas é a 2ª `Source`
(gatilho de tempo), ao lado de Missões (gatilho de evento).

Princípio reitor: lógica determinística (quando disparar, estado,
escalonamento) mora no código; o Claude/Hal só entra pra linguagem
(traduzir pedido→YAML, redigir o lembrete, julgar "já marquei"). Código é
dono do estado — espelha o padrão Missões (evento → transição).

- **`bot/alertas/`** (novo pacote):
  - `models.py` — dataclasses Alerta/Agenda/Canal/Limites/Confirmacao/
    Estado + enums StatusAlerta/TipoEvento/Acao. Serialização YAML em duas
    seções demarcadas (definição escrita pelo Hal · estado só pelo código),
    eventos em jsonl.
  - `storage.py` — CRUD YAML + eventos jsonl espelhando `missoes/storage`:
    lock fcntl, escrita atômica (tmp+rename), append-only com offset, fuso
    America/Sao_Paulo. Layout flat em `user-data/alertas/<id>.{yaml,eventos.jsonl}`.
  - `scheduler.py` — cálculo determinístico de próximo disparo via
    `croniter` (dep nova). Merge dos crons abertura/cobranca/limite resolve
    o escalonamento; one-shot via `quando` ISO.
  - `source.py` — `AlertasSource` (implementa `keyko.Source`, intervalo 30s).
    Máquina de estado ABERTO→CONFIRMADO/EXPIRADO, reabertura por ciclo,
    circuit breaker `disparos_dia` por alerta, backlog-skip se o daemon
    ficou fora do ar.
  - `prompts.py` / `context.py` — prompt de disparo (Hal redige+envia) e
    injeção de "alertas aguardando confirmação" no contexto do turno normal.
  - `handlers.py` — slash commands `/alerta_lista|_pausar|_retomar|_apagar`.
- **`bot/bin/kobe-alerta`** — helper CLI: `criar` (NL→YAML, calcula 1º
  disparo), `confirmar`/`dispensar` (emite evento que a source aplica),
  `listar`. Re-exec sob o venv pra ter as deps.
- **Integração**: `keyko/registry.py` registra a source; `claude_runner.
  build_prompt` ganha `alertas_abertos_info`; `telegram_handler` injeta a
  seção; `main.py` registra os 4 slash commands; `CLAUDE.md` documenta o
  fluxo pro agente. `croniter>=2.0` em `requirements.txt`.

Canais: Telegram funciona (reusa o Despertar do Keyko). WhatsApp é
aceito/validado mas o envio cai em fallback (avisa no Telegram) até o
Apolo expor envio por número.

## [0.14.4] — 2026-05-28

### Corrigido — Chat Manager: resposta curta a pergunta direta

Dois bypasses complementares no detector resolvem o caso em que o
operador respondia curto a uma pergunta direta do agente (ex:
`/flow_lista` → "Flow ou Kobe?" → operador "Kobe") e o Chat Manager
abria conversation nova indevidamente, perdendo contexto.

- **Heurística msg curta** (`bot/conversation_detector.py`): quando a
  msg do operador é curta (≤60 chars OU ≤6 palavras), a última fala
  do agente termina em `?` (ignorando pontuação composta como `?!`),
  o gap é ≤15 min e existe conversation ativa, força `continue` sem
  chamar embedding/judge. Centroide é atualizado com `msg_vec` limpo.
- **State explícito de slash command** (`sessions.awaiting_slash_response`
  JSONB): plugin declara via novo helper `bot/bin/kobe-await-response`
  que aguarda resposta. Handler lê e limpa a coluna antes do detector
  rodar; força `continue` com TTL default 600s. Cobre caso onde o
  bypass heurístico falharia (resposta longa mas conexa).
- **Plugin Flow**: agent definition atualizada em repo separado pra
  chamar `kobe-await-response` em perguntas interativas.
- **Migration 002**: `ALTER TABLE sessions ADD COLUMN IF NOT EXISTS
  awaiting_slash_response JSONB` (idempotente).
- **Testes**: 18/18 unit+smoke em `.local/teste-fix-resposta-curta.py`
  + 5/5 não-regressão dos cenários do fix de 2026-05-27.

Inclui também os dois fixes estruturais de 2026-05-27 que não tinham
chegado a `main` (estavam em `feature/apolo`): embedding contextual
no detector + judge GPT-4o-mini recebendo turnos da candidata.

## [0.13.0] — 2026-05-23

### Adicionado — Sistema de Missões + Keyko

- **Sistema de Missões**: novo pacote `bot/missoes/` com slash `/missao
  <descrição>` no Telegram, painel vivo que se atualiza sozinho, e
  orquestrador Claude rodando em background que planeja, reage a
  marcos, tria mensagens do operador e fecha a missão. Estado em
  `user-data/missoes/<id>/` (estado.json + eventos.jsonl append-only).
  Coordenação inter-processo via lock `fcntl.flock` + escrita atômica
  via `tempfile + os.rename`.
- **Keyko**: novo daemon `systemd --user` (`bot/keyko/`,
  `infra/systemd/keyko.service`). Observa fontes de gatilho via
  interface mínima `Source` (Protocol com `nome`, `intervalo_s`,
  `tick() -> list[Despertar]`) e dispara `claude -p` em background pra
  cada Despertar permitido pelo circuit breaker. Hardcoded com 1 source
  na Fase 1 (`MissoesSource`); Alertas e outras features futuras
  conectam apenas registrando nova Source. Nome em homenagem a um
  pastor alemão do operador (grafia com Y).
- **Circuit breaker**: 10 acordadas / 5min por (fonte, chave) — acima
  bloqueia por 30min e manda 1 mensagem no Telegram avisando o
  operador (sem spam).
- **Comandos auxiliares**: `/missao_status` (snapshot), `/missao_abortar`
  (kill PIDs + marca abortada), `/missao_lista` (ativas + 5 últimas
  encerradas no tópico).
- **Triagem modelo A** durante missão ativa: msg do operador passa
  primeiro pelo orquestrador (síncrono, timeout 90s, fail-safe). Se
  for sobre a missão, orquestrador responde via `kobe-notify` e
  encerra. Se não for, vai pro Hal com linha extra `[Missão ativa:
  <id> — "<obj>"]` no prompt (sem inflar contexto).
- **Painel final read-only**: ao terminar, painel fica com status
  ✅/🔴/⏸️ — não deleta, não sobrescreve, preserva histórico no chat.
- **Wrapper de subtarefa** (`bot/missoes/executor.py`): subtarefas
  rodam via `kobe-dispatch -- python -m bot.missoes.executor`. Timeout
  600s, captura stdout (output) e stderr (log), atualiza estado e
  appenda evento de fim automaticamente.
- **Runbook**: `docs/runbooks/keyko-e-missoes.md` cobre deploy,
  troubleshoot, rollback e como adicionar Source nova.
- **Guia do operador**: `docs/missoes.md` (a criar) — uso prático.

### Modificado

- `bot/claude_runner.build_prompt` ganhou kwarg opcional
  `missao_ativa_info` (string com a linha extra de ciência pro Hal,
  injetada no topo do prompt).
- `bot/telegram_handler.on_text` / `on_voice` agora chamam triagem de
  missão (`_triagem_missao_se_ativa`) ANTES de invocar o Hal.
- `bot/main.py` registra os 4 slashes de Missão + adiciona ao menu
  Telegram.

### Decisões batidas (vide `.local/plano-missoes-fase1.md`)

- 4.1 = **A** (orquestrador tria toda msg do operador em missão ativa)
- 4.2 = **sim** (comandos auxiliares inclusos na Fase 1)
- 4.3 = **A** (missões resilientes a restart do bot — Keyko independente)
- 4.4 = **Keyko** (com Y, homenagem ao pastor alemão)
