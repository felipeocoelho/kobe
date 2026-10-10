# Revisão da memória — como responder

> **Satélite do `CLAUDE.md`.** Não entra no prompt. É carregado por gatilho
> obrigatório: quando o prompt do turno trouxer o bloco
> `[Revisão da memória aguardando tua resposta]`.

---

## O que é

Uma vez por semana o LUCIEN (o curador da tabela de fatos) manda ao operador,
no **privado** dele com o bot, **uma** mensagem com dois blocos, numerados de 1
a N atravessando todos os tópicos:

- **o que ele gravou** desde a última revisão, deduzido da conversa — *"confere
  pra mim?"*;
- **pendências abertas há muito tempo** — *"alguma já morreu?"*.

A mensagem é montada por código; não foi você que escreveu. Enquanto ela está
aberta, o prompt do privado traz o bloco com os números e os títulos.

**A regra do operador, e ela manda em tudo abaixo:** grava primeiro, verifica
depois. **Silêncio não é consentimento nem recusa** — se ele não responder, nada
acontece, e você **não cobra, não lembra, não pergunta "e a revisão?"**.

## Quando agir

Só quando a mensagem dele **se refere à revisão**: cita número de item, ou diz
algo como *"tá tudo certo"*, *"o 3 tá errado"*, *"a 12 eu já entreguei"*, *"o 5 é
só pra dev"*, *"aquela do Pixel ainda vale"*. Se a mensagem é sobre outra coisa,
**ignore o bloco** e responda normal.

Na dúvida se ele está falando da revisão ou de outra coisa, **pergunte** — não
aplique correção por palpite.

## Como traduzir

Você só traduz linguagem. Quem grava é o comando:

```bash
bot/bin/kobe-revisao ver        # os números e títulos, se precisar conferir
echo '{"frase": "<o que ele disse, com as palavras dele>",
       "operacoes": [
         {"item": 3,  "acao": "contestar"},
         {"item": 5,  "acao": "corrigir", "texto": "<o que passa a valer, numa frase>"},
         {"item": 12, "acao": "fechar"}
       ]}' | bot/bin/kobe-revisao aplicar
```

| ele diz | ação |
|---|---|
| "tá certo", "pode deixar" | `confere` (ou nada — não precisa chamar) |
| "tá errado", "não é isso", "nunca falei isso" | `contestar` |
| "não é X, é Y", "isso é só pra dev", "o certo é…" | `corrigir`, com `texto` = a afirmação **inteira** como passa a valer (20 a 400 caracteres, que se sustente sozinha daqui a seis meses — não *"só pra dev"*, e sim *"O uso de X vale só para o ambiente de desenvolvimento"*) |
| "já entreguei", "já resolvi", "fechou" (pendência) | `fechar` |
| "desisti", "não vou fazer", "esquece essa" (pendência) | `abandonar` |
| "ainda vale", "continua aberta" | `manter` |
| "desfaz", "me enganei no 3" | `desfazer` no item |

Regras:

- **`frase` é a fala dele**, literal ou bem próxima. É o que fica no histórico
  explicando por que o registro mudou.
- **Não invente correção.** Só o que ele disse. Item que ele não citou, não toca.
- **Áudio com trecho ilegível** no ponto que decide a correção → pergunte antes.
- **Número que não bate** (ele diz "o 40" e a lista vai até 25) → pergunte. Não
  chute o mais parecido.
- **Tudo ou nada:** se o comando recusar, nada foi gravado. Leia o motivo,
  corrija a tradução e chame de novo — ou pergunte a ele.
- Só funciona **no privado** (o comando recusa em outro chat), porque a origem da
  correção é a fala dele ali. Se ele responder sobre a revisão em outro tópico,
  diga em uma linha que a revisão se responde no privado.

## A resposta que você dá

O comando devolve a **foto de como ficou**, começando com
`🗂️ Revisão da memória — como ficou`. Responda com essa foto **inteira, sem
reescrever** (a marca no começo é o que impede o curador de reler a foto como
fato novo), precedida de uma linha no espírito de:

> *"Ok, corrigi. Segue como vou deixar os registros. Confirma pra mim?"*

É **confirmação de execução, não pedido de permissão** — você já corrigiu. Não
pergunte "posso corrigir?". Se ele apontar outra coisa, o ciclo repete; se ele
disser que errou, é `desfazer`.

## O que NÃO fazer

- Não cobrar resposta, não lembrar da revisão em outro assunto.
- Não editar `user-data/lucien/revisao.json` nem escrever SQL. Sempre o comando.
- Não usar `kobe-fato revogar` para responder à revisão — ele não amarra a
  correção à revisão nem permite desfazer pelo número.
- Não usar `kobe-revisao enviar` — é operação/teste; a revisão sai sozinha.
- Não tocar no `MEMORY.md` por causa da revisão.

## Detalhes que você pode precisar

- **Resposta atrasada**: a revisão fica aberta até a próxima. Se ele responder
  a uma revisão que já foi substituída, os números são da nova — diga isso e
  mostre a nova (`kobe-revisao ver`). Correção de fato fora da revisão segue
  pelo caminho normal (`kobe-fato`).
- **Status**: `contestada` = nunca foi verdade; `abandonada` = desistiu do que
  valia; `fechada` = pendência resolvida; `superada` = trocada por versão nova.
  Nada é apagado: tudo fica no histórico e se desfaz.
