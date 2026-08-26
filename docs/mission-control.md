# Guia do operador — Mission Control

> Antes "Sistema de Missões" (v0.13). Pra detalhes operacionais (deploy,
> troubleshoot, rollback), veja
> [`docs/runbooks/keyko-e-missoes.md`](./runbooks/keyko-e-missoes.md).

## O que e uma missao

Uma **missao** e um turno longo de raciocinio do agente: uma **sala visivel**
(tmux `--remote-control`, navegavel no Claude Code Desktop) pra pensar fundo,
analisar, encadear raciocinio. Prompt de estrategista (nao dev), roda em bypass,
sem rito de codificacao. Abre por **linguagem natural** ("abre uma missao sobre
X") — **nao ha comando slash**.

> **Nota historica (2026-08-25).** Ate esta data existia uma segunda forma: o
> **fan-out** do Sistema de Missoes v0.13, que quebrava o pedido em sub-tarefas
> paralelas com um painel vivo no Telegram, acionado pelos comandos `/missao`,
> `/missao_status`, `/missao_abortar` e `/missao_lista`. Ela foi **aposentada** —
> ultima atividade real em 09/06/2026; palavra do operador: *"e codigo velho,
> pode morrer"*. Os quatro comandos nao existem mais. **A sala estrategista, que
> e o que este guia descreve daqui pra baixo, nao foi tocada.**

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

## Conversa paralela com uma sala aberta

Voce fala normalmente no topico enquanto a sala existe. **A sala nao captura o
canal**: por padrao o agente principal responde, como se ela nao estivesse la.
So vai pra sala com **endereçamento explicito** ("manda pra sala…", "pra
missao…"). Em duvida, o agente **pergunta antes** de repassar.

## Onde mora o estado

```
user-data/missoes/<YYYY-MM-DD-slug>/
├── sala.json              ← estado de runtime da sala (status, pid, turn_count…)
├── sala.sysprompt.txt     ← prompt de estrategista
├── sala-launch.sh         ← script que vira o comando da sala tmux
├── sala.log               ← log do worker que monitora a sala
└── workspace/
    ├── raciocinio.md          o raciocinio registrado (memoria duravel da sala)
    ├── handoff-brief.md       o brief, quando a missao vira "vamos construir X"
    └── rascunhos/
```

Tudo dentro de `user-data/` e privado e ignorado pelo Git. Pra inspecionar uma
missao depois, basta abrir esses arquivos.

> Ate 25/08/2026 estas pastas tambem podiam conter `estado.json`,
> `eventos.jsonl`, `logs/`, `outputs/` e `prompts/` — artefatos do Sistema de
> Missoes v0.13, aposentado. Os que ja existem no disco **continuam la**: sao
> dado do operador, e ninguem os apagou. Simplesmente nao ha mais codigo que os
> leia ou escreva.

## Custo aproximado

Uma sala parada custa ~$0 — ela so gasta quando ha um turno rodando. O custo e
o de um turno longo de raciocinio, proporcional ao tamanho do tema e ao numero
de idas e vindas.
