# Guia do operador — Mission Control

> Antes "Sistema de Missões" (v0.13). Pra detalhes operacionais (deploy,
> troubleshoot, rollback), veja
> [`docs/runbooks/keyko-e-missoes.md`](./runbooks/keyko-e-missoes.md).

## Duas formas

Uma **missão** é um turno longo de raciocínio do agente. Tem duas formas:

- **Sala estrategista** (forma principal) — uma **sala visível** (tmux
  `--remote-control`, navegável no Claude Code Desktop) pra pensar fundo,
  analisar, encadear raciocínio. Prompt de estrategista (não dev), roda em
  bypass, sem rito de codificação. Abre por **linguagem natural** ("abre uma
  missão sobre X"). Detalhe na seção [Sala estrategista](#sala-estrategista).
- **Fan-out** (forma multi-tarefa) — o orquestrador quebra em sub-tarefas
  paralelas com um painel vivo. Acionado pelos comandos `/missao*`. Detalhe no
  resto deste guia.

---

## Sala estrategista

A sala estrategista é a forma de "pensar junto" longa: você abre uma missão, o
agente trabalha o tema numa sala visível e te reporta por `kobe-notify` (prefixos
🧭/💡/🤝/🟡/🟢 `[mission]`), registrando o raciocínio em `workspace/raciocinio.md`.

**Atrás da flag** `MISSION_CONTROL_SALA_ENABLED` (default off — ligue no `.env` e
reinicie o bot pra usar).

### Abrir (linguagem natural — sem comando slash)

Fale com o agente: *"abre uma missão sobre a pesquisa dos alunos do Olimpo"*,
*"quero pensar fundo sobre a migração Supabase→PostgreSQL"*. Ele abre a sala e te
confirma o `missao_id`.

### Roteamento — a sala NÃO captura o tópico

Ter uma sala aberta **não muda** a conversa: por padrão você continua falando com
o agente normal, como se a sala não existisse. Pra mandar algo pra sala, seja
**explícito** ("manda pra sala…", "pra missão…"). Se o agente ficar em dúvida se
uma mensagem era pra sala, ele **pergunta antes** de repassar. Nada vai pra sala
por inferência silenciosa.

### Encerrar — só você fecha (dois canais)

A sala fica aberta até **você** mandar fechar — ela nunca se auto-encerra nem é
fechada por idade. Você encerra de dois jeitos equivalentes: pedindo ao agente no
Telegram ("encerra a missão X") **ou** digitando dentro da própria sala. Mesma
coisa pra aprovações (o "go" de um handoff): vale pelos dois canais.

### Handoff pro Coder

Se a missão virar "vamos construir X", o estrategista prepara um brief
(`workspace/handoff-brief.md`), te mostra e **para pedindo o "go"**. Só depois do
teu OK é que ele dispara o Coder no projeto certo, carregando o brief. Missão que
não é sobre código não tem handoff.

### Onde mora

```
user-data/missoes/<id>/
├── sala.json              ← estado de runtime da sala (status, pid, turn_count…)
├── sala.sysprompt.txt     ← prompt de estrategista
└── workspace/             ← scratch da missão
    ├── raciocinio.md          o raciocínio registrado (memória durável da sala)
    ├── rascunhos/
    └── handoff-brief.md       brief pro Coder, quando houver handoff
```

Destilação durável sobre o Kobe sai do `workspace/` pra
`user-data/knowledge/kobe/<area>/` — passo explícito ao fim, não automático.

---

## O que é (forma fan-out)

**Missão fan-out** é trabalho multi-tarefa coordenado pelo agente. Você descreve
o que quer, o orquestrador quebra em sub-tarefas, dispara em paralelo
respeitando dependências, e mostra um **painel vivo** no Telegram que se
atualiza sozinho. Quando termina, manda o resultado consolidado como
anexo.

Diferente do papo normal, onde você pergunta uma coisa e o agente
responde uma coisa, missão é "vai lá, executa N etapas, me avisa quando
tiver pronto". Você pode sair do chat e voltar 1h depois — o painel vai
estar atualizado.

## Quando usar

**Use missão quando:**
- O trabalho tem 2+ etapas concretas (extrair → analisar → resumir).
- Algumas etapas dão pra fazer em paralelo.
- Não te interessa o passo-a-passo, só o resultado final.
- O trabalho pode demorar minutos / dezenas de minutos.

**NÃO use missão pra:**
- Pergunta simples (papo normal já dá conta).
- Coisa de 1 etapa só (cria uma missão de 1 tarefa é overhead à toa).
- Trabalho onde você quer ir guiando — você perde controle granular.

## Comandos

### `/missao <descrição>`

Cria uma missão nova. Só uma missão ativa por tópico.

```
/missao analise o debriefing da Fulana, extraia os 5 pontos críticos,
e me manda um resumo executivo em markdown
```

Em 1-2s você recebe o painel placeholder. Em 5-15s o orquestrador
planeja e dispara as primeiras tarefas. Depois é só observar o painel
atualizando sozinho.

### `/missao_status`

Snapshot do painel da missão ativa do tópico (não edita o painel vivo,
manda uma cópia separada). Útil pra revisar sem rolar o chat.

### `/missao_abortar`

Mata os processos das tarefas em execução e marca a missão como
abortada. O painel atualiza pra ⏸️.

### `/missao_lista`

Lista o que tem no tópico: ativas + 5 últimas encerradas.

## Painel — como ler

```
🎯 Missão: <objetivo curto>
▶️ Em andamento — 2/5 tarefa(s)

✅ T1 — Extrair pontos-chave
✅ T3 — Identificar bloqueios
▶️ T2 — Categorizar por tema (60%)
⏳ T4 — Redigir resumo (aguarda T2)
⏳ T5 — Enviar (aguarda T4)

💬 T1 e T3 prontas; T2 rodando; T4 e T5 esperam.

🕐 Atualizado: 15:31:42
```

**Glyphs por status da missão**: 🟡 planejando · ▶️ em andamento ·
🟢 concluída · 🔴 falhou · ⏸️ abortada.

**Glyphs por status da tarefa**: ⏳ pendente · ▶️ rodando · ✅ concluída ·
❌ falhou.

**Quando a missão termina**, o painel fica **read-only** com o status
final (🟢/🔴/⏸️). Não deletamos nem sobrescrevemos — fica de histórico
no chat. Os outputs de cada tarefa chegam como anexos.

## Conversa paralela durante missão ativa

Você pode mandar mensagem normal no mesmo tópico enquanto a missão roda:

- Se a msg é **sobre a missão** ("pula a T3", "como tá?", "redirecionar
  T2 pra X"), o orquestrador entende e age (responde, atualiza plano,
  aborta tarefa, etc.).
- Se é **sobre outro assunto** (papo, outra dúvida), o agente principal
  (Hal) responde — e sabe que existe missão rolando, mas não tenta
  gerenciar.

A triagem é automática. Custo: ~5-15s a mais de latência na resposta
quando há missão ativa (orquestrador peneira primeiro).

## Onde mora o estado

```
user-data/missoes/<YYYY-MM-DD-slug>/
├── estado.json         ← view materializada (status, tarefas, painel_msg_id)
├── eventos.jsonl       ← log append-only de tudo que aconteceu
├── orquestrador.log    ← stdout das invocações do orquestrador
├── logs/T<n>.log       ← stdout/stderr de cada tarefa
├── outputs/T<n>.md     ← resultado final de cada tarefa
└── prompts/T<n>.txt    ← prompt da tarefa (escrito pelo orquestrador)
```

Tudo dentro de `user-data/` é privado e ignorado pelo Git. Se quiser
inspecionar uma missão depois, basta abrir esses arquivos.

## Limitações conhecidas da Fase 1

- **1 missão ativa por tópico.** Tentou abrir outra com uma já rodando?
  O bot rejeita e mostra qual está ativa.
- **Sem retry automático** de tarefa falhada. O orquestrador decide
  pular ou fechar a missão como falhou; você decide se reabre.
- **Sem timer pra tarefa travada** (será adicionado em Fase 2). Timeout
  duro de 600s por tarefa pra não pendurar.
- **Sem detecção automática** — você sempre precisa invocar `/missao`
  explicitamente. Detecção pelo agente principal vem na Fase 2.
- **Sem persistência cross-missão** (busca, filtragem) — pra isso
  vamos pra Supabase na Fase 3.

## Custo aproximado

- Planejamento: 1 invocação do orquestrador (~$0,01).
- Cada subtarefa: 1 invocação `claude -p` (~$0,01-0,05 dependendo de
  complexidade).
- Cada marco (tarefa termina): 1 invocação do orquestrador (~$0,01).
- Triagem de msg do operador durante missão: 1 invocação (~$0,002-0,01).

Missão típica (3 tarefas, sem msg paralela do operador) ≈ $0,15-0,30.
Missão complexa (5 tarefas, várias trocas com operador) ≈ $1-2.
