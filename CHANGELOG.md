# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

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
