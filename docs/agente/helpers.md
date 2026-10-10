# Helpers — emitir progresso e anexos de dentro de um plugin

> **Satélite do `CLAUDE.md`.** Este arquivo **não** entra no prompt — ele é
> carregado sob demanda, por gatilho obrigatório declarado na seção
> "Gatilhos taxativos" do `CLAUDE.md`.
>
> O corpo abaixo é **idêntico** ao que estava no `CLAUDE.md`: a etapa que criou
> este arquivo não reescreveu nada, só mudou de lugar.

---

## Helpers do Kobe pra plugins emitirem progresso e anexos

Plugins (e o próprio agente principal, se útil) têm dois helpers em `bot/bin/` pra emitir mensagens e anexos durante a execução — sem precisar esperar a resposta final:

- **`bot/bin/kobe-notify "<texto>"`** — manda texto pro chat ativo. Use pra dar sinal de vida em tarefas longas: `bot/bin/kobe-notify "Transcrevendo URL 2 de 3..."`
- **`bot/bin/kobe-attach <path> [caption]`** — envia arquivo como documento. Use pra entregar artefatos (txt, html, pdf): `bot/bin/kobe-attach /tmp/transcricao.html "Transcrição em formato leitura"`

Os dois usam as envs `KOBE_TELEGRAM_BOT_TOKEN`, `KOBE_CHAT_ID` e `KOBE_THREAD_ID` injetadas pelo bot — não há credencial pra gerenciar.

Padrão de uso (subagente processando múltiplos itens):

```bash
for i, url in enumerate(urls, start=1):
  bot/bin/kobe-notify "[${i}/${total}] Processando ${url}..."
  python plugins/.../script.py "$url" > /tmp/out.txt
  bot/bin/kobe-attach /tmp/out.txt
done
```

A vantagem: o operador vê progresso em tempo real, em vez de esperar 15 minutos em silêncio. Cada notify/attach é uma mensagem separada no Telegram.
