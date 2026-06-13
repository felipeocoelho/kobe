# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

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
