# Manual da memória — os comandos, as camadas e como ler cada saída

> **Satélite do `CLAUDE.md`.** Este arquivo **não** entra no prompt — ele é
> carregado sob demanda, por gatilho obrigatório declarado na seção
> "Gatilhos taxativos" do `CLAUDE.md`.
>
> O corpo abaixo é **idêntico** ao que estava no `CLAUDE.md`: a etapa que criou
> este arquivo não reescreveu nada, só mudou de lugar.

---

## `kobe-reflect` — a memória durável, sob demanda e COM CITAÇÃO

`bot/bin/kobe-reflect "<pergunta>"` é o caminho **confiável** da memória durável. Ele pergunta ao Hindsight e devolve uma resposta **sintetizada a partir só das memórias gravadas, com as fontes** (`document_id` + data). O bank é cético por construção (skepticism e literalism no máximo) e carrega uma *directive* com a regra de Fundamentação — ou seja, a ferramenta é configurada para **não** preencher lacuna com suposição.

```bash
bot/bin/kobe-reflect "o que o operador já decidiu sobre a arquitetura de borda?"
```

Escopo = o tópico atual (as envs `KOBE_CHAT_ID`/`KOBE_THREAD_ID` resolvem o bank). Dev Kobe não puxa Olimpo.

**Quando usar:** quando a pergunta é sobre o **passado** e a confiança pesa — *"o que a gente decidiu sobre X?"*, *"eu já tinha pedido isso?"*, *"em que ficou aquele assunto?"*. É trabalho de ir buscar, então vale o ack antes (§ "Avisa antes de agir").

**Como ler a saída — e isto é o que importa:**

- **Veio com citação** → é pista **fundamentada**. Continua valendo a regra de Fundamentação: pista não é verdade. Se der pra conferir contra a fonte viva (o arquivo, o commit, a mensagem), confira.
- **Veio "não há registro"** (o texto diz isso, ou vem a frase *"não há registro LEGÍTIMO"*, e o comando sai com **exit 0**) → **a resposta certa é dizer que não há registro.** Não é licença para responder de memória. Um "não achei" honesto vale mais que um resumo plausível inventado — e é exatamente aqui que o erro acontece.
- **Veio `(FALHA DO INSTRUMENTO …)`** (e o comando sai com **exit 3**) → **você não sabe se há registro ou não.** A consulta não chegou a ser respondida: timeout, serviço fora, ou HTTP de erro — o texto diz qual. **Não** relate isso como "não há registro"; diga ao operador que a consulta à memória durável falhou, e por quê. Se foi timeout, tentar de novo costuma resolver (a 2ª chamada é bem mais rápida que a fria). Este terceiro caso existe porque, até 29/08/2026, ele era indistinguível do anterior — o cliente desistia aos 20 s de um servidor que respondia bem aos 28 s, e a memória "dizia" que não havia registro.

**O que ele NÃO é:** busca sobre a conversa bruta. Ele lê o **destilado** do Hindsight, não as mensagens literais. Busca por assunto sobre o histórico — com as falas, citadas e datadas — é o **`kobe-remember`**, que existe desde 30/08/2026 (F2 do Highlander v3) e está documentado na seção seguinte. Os dois se completam e **não** se substituem.

> **Nota de contexto:** a consulta automática de memória a cada turno (`HINDSIGHT_RECALL`) está **desligada** — ela custava 4 a 7 segundos em todo turno para entregar 0,3% do prompt. A **gravação continua ligada**. Consequência prática: a memória durável hoje só chega até você se você **for buscar** com este comando.

## As três camadas do `kobe-remember` — ESTADO › EVIDÊNCIA › PISTAS

Desde 30/08/2026 (F3 do Highlander v3) o `kobe-remember` responde em **três
blocos visualmente separados**, e a separação **é** a informação:

| bloco | o que é | quanto vale |
|---|---|---|
| **ESTADO** | o que **vale hoje** — curado por LUCIEN, com vigência e origem | julgamento de um modelo, **conferível** pela origem |
| **EVIDÊNCIA** | o que foi **dito** — as falas literais, citadas por `#número` | verdade bruta: aquilo foi dito, ponto |
| **PISTAS** | o destilado do Hindsight | só aparece quando o estado está magro |

**Por que separados, e não num bloco só:** a evidência é fato; o estado é
conclusão. Servir os dois juntos faria o julgamento passar por transcrição — e é
exatamente essa indistinção que a F3 existe para matar.

### Como responder com cada um

- **ESTADO responde "o que vale hoje".** É o bloco certo para *"qual é a decisão
  vigente sobre X?"*, *"isso ficou decidido ou está em aberto?"*, *"a gente voltou
  atrás em alguma coisa?"*. Cada linha traz a **origem** (`← #número`) — **cite-a
  sempre**, e diga que a linha é curada, não literal.
- **EVIDÊNCIA responde "quais foram as palavras dele".** Continua sendo a prova.
  Quando o operador duvidar do ESTADO, o caminho é `kobe-remember --ver <número>`.
- **Uma linha de ESTADO nunca vira citação literal.** Ela é o resumo que um
  modelo escreveu. Se você for reproduzir a fala, abra a origem.

### O veredito continua sendo da EVIDÊNCIA

`ACHOU` / `MENÇÃO LITERAL` / `SEM REGISTRO` / `FALHA DO INSTRUMENTO` são da
camada de evidência, e as regras da seção anterior valem **inteiras e sem
mudança**. ESTADO não transforma um "não achei" em "achei", nem o contrário.

**A única exceção é de leitura, e ela tem carimbo próprio:**

> **`SEM REGISTRO na fala literal — MAS HÁ ESTADO REGISTRADO`**

Significa que LUCIEN registrou afirmações sobre o assunto, mas a busca não
localizou a conversa de origem **com essas palavras**. Conduta: responda pelo
ESTADO, citando o `#número` de cada linha, diga que não localizou a fala com
essas palavras, e **não diga que não há registro** — há, e está logo acima.

### Quando o ESTADO vier vazio

Vazio no ESTADO **não é** `SEM REGISTRO`. Quer dizer só que LUCIEN ainda não leu
aquele pedaço da conversa (ele roda por acúmulo, atrás, e a reconstrução do
passado é incremental). A resposta certa é a da EVIDÊNCIA, e vale dizer que a
camada curada ainda não alcançou aquele trecho.

### As flags

    kobe-remember "<assunto>"              as três camadas
    kobe-remember "<assunto>" --estado     só o que vale hoje
    kobe-remember "<assunto>" --sem-estado só a evidência (a saída da v1)
    kobe-remember --ver <número>           a vizinhança da mensagem

### Se aparecer o aviso de que a camada de ESTADO falhou

Ele diz `⚠️ a camada de ESTADO falhou` ou `não deu pra consultar o registro`. Isso
é **falha de instrumento**, não ausência: relate como tal e siga pela EVIDÊNCIA,
que não depende do registro. Um banco sem a migration 008 diz outra coisa — *"o
registro de estado ainda não existe neste banco"* —, e aí é ausência mesmo.
