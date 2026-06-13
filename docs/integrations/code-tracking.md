# Capacidade `code-tracking`

> Parte do **Kobe Integrations** — o catálogo de capacidades do core.
> Visão geral do mecanismo: `docs/plugins-autoria.md`.

## O que é

`code-tracking` é a capacidade abstrata de **registrar e acompanhar uma
jornada de código**: garantir que um trabalho de código tenha um cartão de
rastreio (criar se não existir, reaproveitar se já existir) e encerrá-lo
quando o trabalho terminar.

A capacidade é **genérica de propósito**. Quem consome (ex: um plugin que
codifica) só sabe que "existe alguém que rastreia código" — nunca sabe qual
plugin de fato implementa o rastreio (poderia ser um kanban, uma planilha,
um sistema de tickets). Quem provê (ex: um plugin de kanban) só sabe que
"alguém vai me pedir pra rastrear" — nunca sabe quem. Os dois conversam pela
switchboard (`bot/bin/kobe-integrations`), cegos um ao outro.

## Contrato — dois verbos

A semântica é fixa; os nomes dos verbos são estáveis (`ensure`, `finished`).

### `ensure` — garante o rastreio do trabalho

O consumidor manda um **briefing** (descrição do trabalho). O provedor
resolve **internamente** o "acha-ou-cria": se já existe um cartão pra esse
trabalho, devolve o existente; senão, cria um novo. Do ponto de vista do
consumidor é UMA pergunta só — "me garante o rastreio deste trabalho" — e o
"checa-se-existe-senão-cria" é problema do provedor (se vazasse pro
consumidor, os dois voltariam a se acoplar).

**Entrada** (payload JSON no stdin):

```json
{ "briefing": "texto descrevendo o trabalho de código a rastrear" }
```

**Saída** (JSON no stdout):

```json
{
  "rc": 0,
  "card_id": "identificador-do-cartao",
  "meta": { "...": "campos livres do provedor (opcional)" }
}
```

- `rc` — `0` em sucesso; diferente de `0` se o provedor não conseguiu.
- `card_id` — identificador opaco do cartão. O consumidor guarda e devolve
  no `finished`. NÃO tente interpretar o formato — é definido pelo provedor.
- `meta` — bloco livre do provedor (URL do cartão, status, etc.). Opcional.

### `finished` — encerra o rastreio

**Entrada** (payload JSON no stdin):

```json
{ "card_id": "o-mesmo-que-veio-do-ensure", "desfecho": "merged | abandonado | ..." }
```

**Saída** (JSON no stdout):

```json
{ "rc": 0 }
```

- `rc` — `0` em sucesso; diferente de `0` se o provedor não conseguiu encerrar.

## Como o consumidor chama

```bash
# Existe quem rastreie código nesta instalação?
if bot/bin/kobe-integrations provider code-tracking >/dev/null 2>&1; then
  # Garante o cartão (acha-ou-cria), capturando o card_id.
  resp=$(echo '{"briefing":"refatorar o lock do handler X"}' \
           | bot/bin/kobe-integrations invoke code-tracking ensure)
  card_id=$(echo "$resp" | python3 -c 'import sys,json;print(json.load(sys.stdin)["card_id"])')

  # ... faz o trabalho ...

  # Encerra.
  echo "{\"card_id\":\"$card_id\",\"desfecho\":\"merged\"}" \
    | bot/bin/kobe-integrations invoke code-tracking finished
else
  # Ninguém rastreia código aqui — segue sem rastrear (consumes é só etiqueta).
  :
fi
```

O consumidor **nunca** recebe nem usa o nome do provedor. Se não houver
provedor, o `invoke` sai com código diferente de `0` e mensagem clara — o
consumidor decide se isso é fatal ou se segue sem rastreio.

## Como um plugin provê esta capacidade

No `kobe-plugin.md` do provedor:

```yaml
integrations:
  provides:
    - capability: code-tracking
      handler: bin/rastreio   # executável dentro da raiz do plugin
```

O `handler` recebe o **verbo** no primeiro argumento (`ensure`/`finished`) e
o **payload** no stdin; deve devolver o JSON do contrato no stdout e sair com
código `0` em sucesso. Veja o exemplo funcional em
`examples/integrations/code-tracking-stub/`.

## Estado na v1

- A **definição** existe (este doc + o índice de roteamento).
- **Não há provedor real** embarcado na v1 — só o stub de exemplo. O provedor
  de verdade (ex: o plugin Flow registrando cards) é trabalho downstream.
- Conflito (dois plugins declarando `code-tracking`) **trava** a capacidade e
  avisa nos logs — o sistema não escolhe um vencedor sozinho.
