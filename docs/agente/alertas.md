# Sistema de Alertas — como criar, fechar e gerir

> **Satélite do `CLAUDE.md`.** Este arquivo **não** entra no prompt — ele é
> carregado sob demanda, por gatilho obrigatório declarado na seção
> "Gatilhos taxativos" do `CLAUDE.md`.
>
> O corpo abaixo é **idêntico** ao que estava no `CLAUDE.md`: a etapa que criou
> este arquivo não reescreveu nada, só mudou de lugar.

---

## Sistema de Alertas — capacidade proativa (você acorda sozinho)

Você tem capacidade **proativa**: o operador pede em linguagem natural ("me lembra toda terça de marcar a barbearia", "todo dia 7h faça o briefing", "amanhã 15h me lembra de emitir a nota") e você passa a disparar sozinho no horário. É capacidade **core** (não plugin), construída sobre o daemon Keyko.

**Princípio reitor:** a lógica determinística (quando disparar, estado, escalonamento) é do CÓDIGO. Você só é invocado pra LINGUAGEM: traduzir o pedido em alerta na criação, redigir o lembrete no disparo, e julgar a confirmação. **Você nunca é o guardião do "lembrar" — confiabilidade é do código.** Nunca edite os arquivos de estado à mão; use sempre o helper `bot/bin/kobe-alerta`.

### Criar um alerta (quando o operador pede um lembrete)

Traduza o pedido pros campos e rode `bot/bin/kobe-alerta criar` passando um JSON no stdin. O helper valida, calcula o 1º disparo e persiste — ele te devolve o `id` e o `proximo_disparo`, que você confirma ao operador em uma linha.

```bash
echo '{"titulo":"Briefing matinal","instrucao":"Monte o briefing do dia: eventos de hoje no Google Calendar + tarefas do Todoist vencendo. Tópicos curtos.","agenda":{"abertura":"0 7 * * *"}}' | bot/bin/kobe-alerta criar
```

Campos do JSON:
- `titulo` (obrigatório), `instrucao` (obrigatório) — a instrução é o que VOCÊ vai executar quando acordar (pode pedir pra coletar dados de MCP/web/script).
- `agenda` — **um cron** em `abertura` (recorrente: `"0 7 * * *"`) **ou** um ISO em `quando` (one-shot: `"2026-05-31T15:00:00-03:00"`, dispara 1× e auto-arquiva).
- Para lembrete com **cobrança até confirmar** (modelo barbearia): `aguarda_confirmacao: true` + `agenda.abertura` (abre o ciclo) + `agenda.cobranca` (re-cobra enquanto aberto) + `agenda.limite` (para de cobrar) + `confirmacao.fecha_quando` (critério em linguagem natural). Todos crons de 5 campos.
- `canal` — `{"tipo":"telegram"}` (default, usa o tópico atual) ou `{"tipo":"whatsapp","destino":"+55..."}`. **WhatsApp ainda não envia** (depende do Apolo); cai em fallback que avisa no Telegram.
- `limites.disparos_dia` — teto de disparos/dia (circuit breaker; default 3).

Na dúvida sobre horário/fuso/fontes, pergunte ao operador ANTES de criar. Fuso é sempre America/Sao_Paulo. Se o cron disparar de madrugada, confirme ("vai tocar 3h da manhã, é isso mesmo?").

### Fechar o ciclo (confirmação por conversa normal)

Quando um alerta com confirmação está **ABERTO**, o prompt do seu turno traz a seção `[Alertas aguardando confirmação neste tópico]` com o `id` e o critério. Se a mensagem normal do operador indicar que ele JÁ resolveu (ex.: "já marquei", "agendei pra sexta"), feche o ciclo:

```bash
bot/bin/kobe-alerta confirmar <id> "o que ele disse"
```

Se ele disser pra deixar pra lá esta vez (sem ter feito), use `bot/bin/kobe-alerta dispensar <id> "..."`. **Não invente confirmação** — só feche se ele realmente sinalizou. A `AlertasSource` aplica a transição (você não edita estado).

### Quando você é ACORDADO por um alerta

O Keyko te invoca com um prompt de disparo dedicado (você está sozinho, sem histórico de conversa). Sua única tarefa: coletar o que a instrução pedir, redigir o lembrete no seu tom, e ENVIAR pelo canal (via `kobe-notify`). Sua resposta de texto não chega ao operador — só o que sair pelo helper chega. Detalhe do estado de qualquer alerta: leia `user-data/alertas/<id>.yaml` (definição + estado; só leitura).

### Comandos de gestão (slash, no Telegram)

`/alerta_lista` · `/alerta_pausar <id>` · `/alerta_retomar <id>` · `/alerta_apagar <id>`. Criar NÃO tem slash — é só conversando.
