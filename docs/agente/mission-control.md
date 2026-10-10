# Mission Control — como operar uma sala de missão

> **Satélite do `CLAUDE.md`.** Este arquivo **não** entra no prompt — ele é
> carregado sob demanda, por gatilho obrigatório declarado na seção
> "Gatilhos taxativos" do `CLAUDE.md`.
>
> O corpo abaixo é **idêntico** ao que estava no `CLAUDE.md`: a etapa que criou
> este arquivo não reescreveu nada, só mudou de lugar.

---

## Mission Control — salas de missão (estrategista)

Uma **missão** é um turno longo de raciocínio numa **sala visível** (tmux `--remote-control`, navegável no Claude Code Desktop): pensar fundo, analisar (ex.: "analisa a pesquisa dos alunos do Olimpo segundo X e Y"), encadear tarefas. NÃO é "código" — é uma janela de pensamento, com prompt de **estrategista** (não dev), rodando em bypass. Atrás da flag `MISSION_CONTROL_SALA_ENABLED` (se off, o dispatcher devolve `sala_disabled` e você avisa que o Mission Control está desligado).

**O comando (sempre o mesmo binário):** `.venv/bin/python -m bot.mission_control.sala_dispatch <abrir|retomar|encerrar> ...` (rode da raiz do Kobe; `chat-id`/`thread-id` saem do env automaticamente).

### Abrir (por linguagem natural — NÃO há comando slash)
Quando o operador pedir em linguagem natural pra abrir/pensar uma missão — "abre uma missão sobre X", "quero pensar fundo sobre Y", "monta uma sala pra analisar Z" — você abre:
```
.venv/bin/python -m bot.mission_control.sala_dispatch abrir --objetivo "<o tema, fiel ao que ele pediu>" \
    [--system "<Sistema>" --subsystem "<Subsistema|none>"]
```
O resultado traz o `missao_id`. Confirme em uma linha ("abri a missão `<id>`, a sala já está pensando — te reporto por aqui"). Abrir **não** redireciona o tópico.

**Uma missão pode ou não ser sobre um sistema — e quem decide é você, pelo pedido.**
O operador continua pedindo do jeito que pede; ele não digita nada disso.

- **Não é sobre sistema nenhum** (debriefing, estratégia, copy, pesquisa, análise
  de negócio) → **omita `--system` e `--subsystem`**. A sala abre normalmente,
  fora do catálogo de desenvolvimento (`catalogado: false`). Não invente sistema
  pra "satisfazer" o comando.
- **É sobre um sistema** (pensar o Kobe, um plugin, o app Flow) → **declare os
  dois**. Aí a sala ganha linha no catálogo, e a declaração é conferida: sistema
  desconhecido recusa, e o subsistema passa a ser obrigatório (ou `none`).
  Subsistema sem sistema também recusa.

(Sessão **Coder** é outra coisa: lá o sistema é sempre obrigatório.)

| A missão é… | Declare |
|---|---|
| **não é sobre sistema** (debriefing, estratégia, copy) | nada — omita os dois |
| sobre o **Kobe** (o framework: core, `bot/`, `infra/`, `keyko/`) | `--system Kobe --subsystem none` |
| sobre um **plugin do Kobe** (Coder, Atrus, Apolo, Monet, o plugin Flow) | `--system Kobe --subsystem <Coder\|Atrus\|Apolo\|Monet\|Flow>` |
| sobre **outro sistema** (o app web Flow, por exemplo) | `--system Flow --subsystem none` |

Quando a missão declara sistema, quatro regras fecham o desenho:

- **A pasta não decide.** Um plugin do Kobe mora em pasta própria e ainda assim o
  sistema é o **Kobe** — o plugin é o *subsistema*. O caso que prova: existe um
  **plugin** `flow` e existe o **app web Flow**; pela pasta seriam a mesma coisa,
  pela declaração não há confusão.
- **`none` é declaração; omitir o subsistema é recusado.** Seis meses depois, uma linha sem
  subsistema tem que querer dizer *"não tinha subsistema"*, e não *"ninguém
  preencheu"*.
- **Em dúvida genuína, pergunte UMA linha antes de abrir** — *"isso é o plugin
  Flow ou o app Flow?"*. Ambiguidade não é o mesmo que informação óbvia faltando:
  *"pensar sobre o gate do plano do Coder"* não é ambíguo, é `Kobe / Coder`.
- **Sistema novo é EVENTO.** Se o dispatch recusar por sistema desconhecido, **não
  tente outro nome até colar** — pergunte ao operador se é sistema novo pra
  registrar. É o que impede um erro de digitação seu de virar sistema fantasma.

**Como ler a recusa — são três coisas diferentes.** Olhe `refusal` e `unavailable`
no retorno: `refusal: true` é **recusa de regra** (corrija a declaração; o campo
`message` diz como); `unavailable: true` é **falha de instrumento** (o banco não
respondeu — **não redeclare**, nenhuma declaração conserta um Postgres fora do ar;
avise o operador). Nos dois casos **nenhuma sala foi aberta** — o que muda é o que
fazer a respeito.

### Roteamento — a sala NÃO captura o canal (regra dura)
Ter uma sala ativa no tópico **não muda** a conversa: por padrão, **você (Hal) responde normal**, como se a sala não existisse. Quando há sala ativa, seu prompt traz uma linha `[Sala de missão ativa neste tópico: <id> — "<obj>"]` — isso é só **ciência**, não ordem de repassar.

- **Repasse pra sala SÓ quando o operador for EXPLÍCITO** ("manda pra sala", "pra missão", "fala pra sala que…", endereçamento claro). Aí sim:
  ```
  .venv/bin/python -m bot.mission_control.sala_dispatch retomar --missao <id> --texto "<o que ele mandou pra sala>"
  ```
- **Se você só DESCONFIA** que a mensagem era pra sala mas não tem certeza: **NÃO repasse direto — pergunte primeiro** ("isso é pra missão `<id>` ou pra mim?"). Confirmou → repassa; não confirmou → fica contigo, no tópico.
- Nunca repasse por inferência silenciosa. Default é sempre conversa contigo.

### Encerrar — só o operador fecha (ato explícito, dois canais)
A sala **nunca se auto-encerra** e você **nunca a fecha por conta própria**. Ela fica aberta até o operador mandar fechar. Quando ele disser "encerra a missão X" (ou equivalente claro):
```
.venv/bin/python -m bot.mission_control.sala_dispatch encerrar --missao <id>
```
O operador também pode encerrar **direto dentro da sala** (digitando lá) — os dois canais são equivalentes; não assuma que o fim só vem pelo Telegram.

### Aprovação/handoff
Se a missão virar "vamos construir X", o estrategista prepara um brief e PARA pedindo o "go". Esse "go" (e qualquer destrave) pode vir por aqui (você repassa via `retomar`) **ou** direto na sala — trate igual.
