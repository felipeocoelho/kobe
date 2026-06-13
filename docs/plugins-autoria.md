# Autoria de plugins do Kobe

Guia mínimo de como escrever um plugin do Kobe. Foco desta versão: o
**Kobe Integrations** — como um plugin PROVÊ e CONSOME uma capacidade.

## O manifest `kobe-plugin.md`

Todo plugin tem, na raiz, um `kobe-plugin.md` que começa com um frontmatter
YAML (bloco entre `---`). Campos principais:

```yaml
---
name: meu-plugin              # obrigatório, slug único
visibility: public            # public | private
version: 0.1.0
description: "o que o plugin faz, em uma frase"
triggers:                     # quando o agente deve acionar o plugin
  - "operador pede X"
slash_commands:               # comandos no menu do Telegram (opcional)
  - name: meu_comando
    description: "..."
agent_definition: claude/agents/meu-plugin.md   # subagente (opcional)
integrations:                 # ← Kobe Integrations (esta seção)
  provides: [...]
  consumes: [...]
---
```

O bot descobre os plugins no startup (`bot/plugins.py`), lê o frontmatter e
monta, entre outras coisas, o índice de capacidades.

---

## Kobe Integrations — cooperação sem acoplamento

### O problema que resolve

Plugins precisam cooperar, mas **não podem se conhecer pelo nome**. Se o
plugin A chama o plugin B pelo nome, eles ficam grudados: trocar B quebra A,
e a malha de dependências vira N×N. A regra de ouro é:

> **Plugin depende de uma CAPACIDADE genérica do core, nunca de um
> plugin-irmão pelo nome.** A seta de dependência aponta do específico pro
> genérico.

### Os três conceitos

- **Capacidade** (`capability`): um nome abstrato pra uma habilidade
  (`code-tracking`, e outras no futuro). É o "contrato" — define quais verbos
  existem e o formato de entrada/saída. Os contratos vivem em
  `docs/integrations/<capacidade>.md`.
- **Provedor**: um plugin que diz "eu sei fazer essa capacidade". Ele aponta
  um **handler** (executável) que responde os verbos.
- **Consumidor**: um plugin que diz "eu preciso dessa capacidade". Ele pede
  pela switchboard, sem nunca saber quem provê.

### A switchboard

A central que liga consumidor ↔ provedor é o helper
`bot/bin/kobe-integrations`. Ela faz duas coisas:

```bash
# "Quem provê a capacidade X?" — imprime o provedor, ou sai !=0 se não há.
bot/bin/kobe-integrations provider <capacidade>

# "Execute o verbo V da capacidade X com este payload" — roteia pro handler
# do provedor e devolve o JSON dele. O consumidor NUNCA vê o nome do provedor.
echo '<payload-json>' | bot/bin/kobe-integrations invoke <capacidade> <verbo>
```

Indireção de propósito: o consumidor fala só com a switchboard. Cegueira
total entre os atores.

---

## Como PROVER uma capacidade

1. Declare no manifest:

   ```yaml
   integrations:
     provides:
       - capability: code-tracking
         handler: bin/rastreio    # path relativo à raiz do plugin
   ```

   - `capability`: nome da capacidade (minúsculo, `[a-z0-9-]`).
   - `handler`: um **executável** dentro da raiz do plugin (precisa de bit de
     execução: `chmod +x`). Por segurança, o handler **tem que morar dentro**
     da pasta do plugin — paths com `../` que escapam da raiz são rejeitados.

2. Escreva o handler. Contrato universal, agnóstico de linguagem:

   - Recebe o **verbo** no primeiro argumento (`$1` / `argv[1]`).
   - Recebe o **payload** no **stdin** (JSON).
   - Devolve **um objeto JSON no stdout**.
   - Sai com código `0` em sucesso; `!=0` em falha (mensagem no stderr).

   O handler herda o ambiente do bot (as variáveis `KOBE_*`), então pode usar
   os outros helpers (`kobe-notify`, etc.) normalmente.

   Exemplo funcional completo:
   `examples/integrations/code-tracking-stub/` (manifest + `bin/track`).

3. Os verbos e o formato exato de entrada/saída são definidos pelo **contrato
   da capacidade** — leia/escreva `docs/integrations/<capacidade>.md`.

### Conflito de provedor

Se **dois plugins** declararem a **mesma capacidade**, ela fica **travada**:
o sistema NÃO escolhe um vencedor sozinho. A switchboard passa a recusar a
capacidade (saída `!=0`, mensagem clara) e o conflito é logado como erro no
startup. O operador resolve removendo a duplicidade. (Decisão de design da
v1.)

---

## Como CONSUMIR uma capacidade

1. Declare no manifest (etiqueta declarativa):

   ```yaml
   integrations:
     consumes:
       - code-tracking
   ```

   Na v1, `consumes` é **só documentação** — registra a dependência, mas
   **não bloqueia** o plugin de rodar se não houver provedor instalado. Serve
   pra deixar explícito o que o plugin gostaria de usar (e pra validação
   futura).

2. No código do plugin, pergunte e use:

   ```bash
   if bot/bin/kobe-integrations provider code-tracking >/dev/null 2>&1; then
     resp=$(echo '{"briefing":"..."}' \
              | bot/bin/kobe-integrations invoke code-tracking ensure)
     # ... usa o resp ...
   fi
   ```

   Sempre trate o caso "não há provedor" (saída `!=0`): decida se é fatal ou
   se o plugin segue sem aquela capacidade. Nunca assuma que o provedor existe.

---

## Resumo dos códigos de saída da switchboard

| Código | Significado |
|---|---|
| `0` | sucesso |
| `1` | nenhum provedor pra capacidade |
| `2` | uso inválido (argumentos errados) |
| `3` | capacidade travada por conflito (mais de um provedor) |
| `4` | handler do provedor ausente ou não executável |
| `5` | handler falhou, estourou o tempo, ou não pôde ser executado |
| `6` | handler devolveu algo que não é JSON válido |
