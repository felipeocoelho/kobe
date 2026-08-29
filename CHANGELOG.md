# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Highlander v3 — F0.6-B: a transcrição para de jogar fora os próprios sinais (2026-08-29)

**Operador pediu:** as recomendações da F0.6, item (a) — *"CONSERTAR a instrumentação. É a
raiz."* — e, na aprovação, *"Plano aprovado"*.

**Por quê:** três defeitos de **configuração**, não de lógica, todos achados na investigação.
O Whisper já calcula os sinais que denunciam a própria degeneração e a gente os descartava na
porta; a temperatura ficava solta; e o guard do prompt de hints media **bytes** onde a Groq
limita **tokens** — ou seja, existia um buraco por onde um prompt acima do limite passava.

**Foi feito** (`bot/transcribe.py`):
- `response_format="text"` → **`"verbose_json"`**. Custo: **zero** — mesma chamada, mesmo
  preço, texto idêntico. O que muda é que a resposta passa a trazer `segments` com
  `avg_logprob`, `compression_ratio` e `no_speech_prob`, e uma linha `whisper_signals` de log
  passa a registrar os agregados por transcrição.
- **`temperature=0`** (recomendação da própria Groq). O decoder do Whisper continua subindo a
  temperatura sozinho ao bater os thresholds internos — o que se fixa é o ponto de partida.
- **Guard de hints agora respeita os DOIS tetos**, o que vier primeiro: 850 bytes (contagem
  exata) **e ~224 tokens** (contagem estimada). O teto de tokens simplesmente não existia.
- **O corte passa a cair em fronteira de item/palavra.** Não é enfeite: o arquivo de hints é
  lista de vocabulário, e cortá-la no meio de um nome injeta um fragmento inexistente
  justamente no campo que biasa o reconhecimento — o mesmo vetor de prompt-bleeding que a
  F0.6 investiga (um áudio de 11/06 voltou com o texto do arquivo de hints DENTRO da
  transcrição).
- `bot/transcription_normalizer.py`: uma linha de docstring que afirmava só o teto de bytes.

**A fronteira desta entrega, que é o principal:** ela **SÓ COLHE**. Nenhuma linha decide coisa
alguma com base nos três sinais. Julgar exige conhecer a faixa normal deles nos áudios do
operador, e essa faixa só existe depois de coleta em produção — decidir é a fase seguinte.
**O detector genérico de degeneração continua NÃO construído**, como o relatório recomendou
(71% de detecção a 29% de falso positivo) e o plano marcou.

**Como se conta token sem tokenizer — e por que não se conta:** não há tokenizer no ambiente,
e o do Whisper é um BPE próprio; puxar dependência que ainda baixaria vocabulário pela rede
DENTRO do caminho quente da transcrição seria pior que o problema. A contagem é uma
**estimativa pessimista** (`chars/2`, contra 3–4 chars/token reais em pt-BR): erra sempre pro
lado seguro — corta cedo demais, nunca estoura o teto — e **avisa em WARNING** quando corta,
porque um corte baseado em palpite tem que ser visível.

**Testes:** `tests/test_f06_defesas.py`, **19 verdes**, mais a regressão. Sem rede e **US$ 0,00**
— o cliente da Groq é um fake que devolve uma resposta `verbose_json` de mentira.
- **T1**: `verbose_json` e `temperature=0` chegam mesmo na chamada (não ficaram na docstring).
- **T2**: o texto sai idêntico ao de antes e os três sinais viram log agregado, com o segmento
  degenerado puxando `avg_logprob_min` e `compression_ratio_max`. Inclui o caso em que
  `segments` só existe no `model_dump()` (extras do pydantic).
- **T2b** (5 variações): sem `segments`, com lista vazia, sem os campos, resposta em `dict`, e
  resposta ainda em `str` (formato antigo) — **nenhuma derruba a transcrição**. É a propriedade
  que importa em produção: se a Groq mudar o payload, perde-se o log, não o áudio do operador.
- **T2c**: o log **não vaza conteúdo**. Só números saem — o que o operador fala não vai pro
  `journalctl` pra render telemetria.
- **T3** (7 casos): o arquivo real de hints passa intacto; o teto de tokens morde antes do de
  bytes num texto ASCII de 718 bytes (o buraco que existia); o teto de bytes segue valendo com
  acento; o corte nunca cai no meio da palavra; o WARNING sai; e o prompt **efetivamente
  enviado** já vai truncado.
- **Regressão verde:** `test_transcribe_latency.py` (inclui a assertiva de que duas
  transcrições rodam concorrentes — a latência do turno não regrediu), mais
  `test_transcription_normalizer.py`, `test_hindsight_f0.py`, `test_hindsight_bank_environment.py`,
  `test_hindsight_recall_gate.py` e `test_dev_inject.py`: **66 verdes**. `bot.telegram_handler`
  importa, e o arquivo de hints REAL do operador (237 bytes hoje) atravessa o guard novo **sem
  truncar um byte**.

**O que NÃO foi testado, e é honesto dizer:**
- **A perna áudio→texto não é testável aqui** — `dev_inject` não injeta áudio (lacuna 9.2).
  Fica dependendo de áudio real em produção: (1) que a Groq devolve `segments` populados para
  `whisper-large-v3` neste plano de conta; (2) que `temperature=0` não muda o texto que sai;
  (3) **qualquer** julgamento sobre os valores dos sinais.
- **O roteiro conversacional do `dev_inject` foi deliberadamente NÃO rodado.** O `kobe-dev`
  roda a partir da árvore de dev, não desta worktree — o roteiro exercitaria código que não
  contém estas mudanças, gastando cota de assinatura para provar nada. A regressão do turno
  está coberta pelo import do handler e pelas suítes acima; o roteiro cabe **depois do merge**,
  se o operador quiser.

**Reversão:** commit limpo na branch `coder/17f51797`. `git revert` volta `response_format`,
`temperature` e o guard de uma vez; nenhuma das três muda estado fora do processo, então não há
nada a desfazer além do código.

### Highlander v3 — F0.6-A: a regra anti-ruído entra no bank (2026-08-29)

**Operador pediu:** *"Sobre as defesas da F0.6, eu vou seguir com todas as recomendações que me
foram dadas."* — e, na aprovação do plano desta sessão, *"Plano aprovado"*.

**Por quê:** das três camadas pesadas na investigação da F0.6, a diretiva do bank é a **única
medida funcionando ponta a ponta** (2/2 → 0/2 no caso reprodutível do "Cade"). O extrator
sozinho não salva: escolher o melhor modelo só faz o lixo ser gravado com mais elegância.

**Foi feito:**
- `RETAIN_MISSION` (`bot/hindsight_client.py`) ganhou um 2º parágrafo: rejeitar trecho com
  **forma de definição de um termo que não é desenvolvido no resto da fala**, fechando com
  *"na dúvida entre gravar e descartar, DESCARTE: memória durável errada é pior que memória
  incompleta"*. 588 caracteres, ~163 tokens estimados.
- Comentário de bloco acima da constante registrando custo, efeito colateral aceito e o
  caminho de volta — pra ninguém "limpar" a regra daqui a três meses sem saber o que ela era.

**Ressalva honesta, e ela importa:** a **redação literal** que foi medida (+175 tokens de
entrada, 2/2 → 0/2) **não ficou registrada em lugar nenhum** — nem no relatório, nem no brief.
Os documentos descrevem a regra e citam só a frase de fechamento. O texto que entrou é uma
reconstrução fiel à descrição, do mesmo tamanho, **aprovada verbatim pelo operador no plano**.
Equivalência com o que foi medido não é demonstrável; semelhança é.

**Efeito colateral conhecido e ACEITO (não é bug):** a regra comprime — 8 → 5 fatos na amostra
medida, com duas fusões legítimas e uma perda parcial. Decisão do operador. Não foi compensada
com nenhuma outra regra, de propósito.

**Testes:** `tests/test_f06_defesas.py`, 3 verdes, no ambiente de dev.
- **T4a** (unitário, sem rede): a constante carrega as duas partes — a missão original e a
  regra nova. Trava contra alguém derrubar a regra numa reescrita.
- **T4** (contra o Hindsight de **dev**, `:8890`, bank descartável): **respondida a pergunta
  que o brief mandava conferir — o bank EXISTENTE recebe a missão nova no restart.** A
  encenação é a real: cria o bank, configura com uma missão velha, limpa `_configured_banks`
  (que é o que o restart faz), chama `_ensure_bank` e **lê o config de volta do servidor**. A
  missão nova está lá, com a regra dentro, e as disposições céticas seguem de pé. Não era
  óbvio: se tivesse ficado a velha, os banks vivos jamais receberiam a defesa.
- **T4c**: trava que recusa rodar a suíte apontada para a produção do Hindsight.
- **Produção (`:8888`) não foi tocada** — zero POST, zero PATCH, zero leitura. **US$ 0,00** de
  API: nenhuma chamada paga a provedor nesta entrega.
- **O que NÃO foi testado, e não dá pra testar aqui:** a *qualidade* da extração sob a regra
  nova. O Hindsight de dev está vazio e julgar isso exigiria chamada paga de extração. A
  eficácia (2/2 → 0/2) é dado da investigação anterior, não coisa reprovada nesta sessão.

**Reversão:** commit limpo na branch `coder/17f51797` — `git revert` volta a missão anterior.
Como é texto de config aplicado por `PATCH` idempotente, o bank vivo volta ao estado antigo no
restart seguinte, sem migration e sem perda de memória.
### Hindsight: o LLM da memória durável passa a ser Anthropic `claude-haiku-4-5` (2026-08-29)

**Operador decidiu:** *"pode ligar o Haiku em produção via API da Anthropic já fornecida (não
use OpenRouter) para ser usado como nosso 'modelo mini padrão'"* — aplicando em runtime a
recomendação medida na **F0.5-D** (`claude-haiku-4-5` nas duas pontas, R$ 27,81/mês).

**O que impedia:** o `docker-compose.yml` amarrava a chave do LLM literalmente à
`OPENAI_API_KEY`. Não havia como apontar o LLM para outro provider sem que os **embeddings**
fossem junto — e essa é a armadilha silenciosa que a F0.5 já tinha encontrado rodando: a chave
de embedding do Hindsight **cai por padrão na chave do LLM**, então o embedding passaria a
mandar a chave errada para a OpenAI, tomaria 401, e o `reflect` **não falharia** — responderia
*"não há registro"*, porque as ferramentas de busca dele engolem o erro. Resposta vazia com
latência bonita.

**Foi feito:** o compose de produção ganhou os três eixos independentes que o override de dev
(`docker-compose.models.yml`) já tinha provado:

- `HINDSIGHT_LLM_API_KEY` — chave do LLM, com default na `OPENAI_API_KEY`;
- `HINDSIGHT_LLM_MODEL` — modelo explícito (vazio cai no default do provider, que o Hindsight
  resolve com `or`, então string vazia é segura);
- `HINDSIGHT_API_EMBEDDINGS_OPENAI_API_KEY` — chave dos embeddings, amarrada **separadamente**
  e sempre à OpenAI.

**Nada muda para quem não define as variáveis novas:** os defaults renderizam, byte a byte, a
configuração anterior — verificado com `docker compose config` nos dois cenários.

**O que NÃO mudou, de propósito: os embeddings.** Seguem em `text-embedding-3-small` da
OpenAI. Trocar embedding com o bank já povoado não é configuração, é migração — os vetores
antigos ficam com outra dimensão e param de ser encontrados. A F0.5 mediu o embedding local
como viável; ligá-lo é decisão separada, com re-indexação.

**Escopo:** apenas o Hindsight de **produção**. O de dev continua em `openai`.

**Reversão:** `HINDSIGHT_LLM_PROVIDER=openai` no `.env` + recriar o container (~2 min). O
`.env` anterior está carimbado em `.local/backups/`.


### Highlander v3 — F0.6: "Cade", o envenenamento da memória na entrada (2026-08-28)

**Operador decidiu (e isto é decisão DELE, não recomendação minha):** `claude-haiku-4-5` nas
duas pontas da memória — gravação/consolidação **e** `reflect`. Palavra dele: *"Vou seguir a
sua recomendação de modelos, Haiku nos 2 casos (gravação/consolidação e reflect)."* Registrado
em `user-data/knowledge/kobe/decisoes/2026-08-modelos-da-memoria.md`.

**Operador pediu, no mesmo turno:** investigar um achado dele — *"'Cade' é envenenamento de
memória (não é o 'cadê', que é uma palavra usual), eu NUNCA falo isso"* — e testar a tese de
que a memória está sendo envenenada **na entrada**, não só na extração.

**Por quê:** conecta direto com o defeito medido na F0.5-D, onde o Haiku *"fabricou 1
definição em 21 fatos sobre ruído de transcrição"*. Escolher o melhor extrator não resolve
lixo de origem — o extrator bom só grava o lixo com mais elegância.

**Foi feito:** relatório
`user-data/knowledge/kobe/status/2026-08-28-f06-envenenamento-por-transcricao.md`, com o dano
medido, as três camadas de defesa pesadas na mesma régua e uma recomendação única.

**O fenômeno é real, reincidente e agora tem nome e origem.** Em **31 de 861** mensagens de
voz (**3,6%**), de 28/05 a 28/08, o Whisper **insere uma frase que o operador não disse**, com
forma de definição. **"Cade" é "CLAUDE" colapsado** — e quem diagnosticou isso foi o próprio
operador, em 15/06 (*"Cope Online"* = `CLAUDE.md`). As nove definições capturadas são
**mutuamente contraditórias**, porque a alucinação **ecoa uma frase do próprio áudio** dentro
de um molde de definição.

**Dano gravado: ZERO** dos 1.059 fatos do bank de produção. **Mas o mecanismo é
reprodutível:** num trecho real, o Haiku gravou *"Cade é um código usado para enviar mensagens
no ambiente de desenvolvimento"* em **2 de 2** tentativas. Não chegou ao bank por sorte de
amostragem, não por defesa.

**Correção de uma afirmação minha, feita durante a própria apuração:** reportei ter achado um
fato envenenado em produção porque o predicado batia com a definição alucinada. **Fui ler a
fonte e o fato é fiel** — a frase era do operador, e a alucinação a copiou. Eu tinha cravado
sem abrir a mensagem. O erro melhorou o diagnóstico, porque revelou o mecanismo de eco.

**Suspeita principal, com uma evidência direta e não provada:** o próprio
`user-data/transcription-hints.md` pode estar **primando** o artefato. Um áudio de 11/06
devolveu o **texto do arquivo de hints como se fosse transcrição**, com "HAL" trocado por
"Cade" — prompt-bleeding do Whisper. **Provar exige áudio real, e o `dev_inject` só injeta
texto** (lacuna já declarada na F0).

**As três camadas, medidas:**
- **(a) transcrição** — a raiz, custo zero, **eficácia não medida** (precisa de áudio);
- **(b) detector genérico de degeneração** — **medido e NÃO funciona**: 71% de detecção custa
  **29% de falso positivo**, porque fala real repete mais que alucinação (a repetição máxima
  nas mensagens limpas é 9; nas alucinadas, 7). **Não recomendo construir.**
- **(c) diretiva do bank** — **funciona**: A/B com a missão como única variável levou a
  fabricação de 2/2 para **0/2**. Custa +175 tokens de entrada (~R$ 0,35/mês) e **a saída
  caiu**, então provavelmente se paga. Efeito colateral medido: consolida fatos (8 → 5).

**O falso positivo do "cadê" é ZERO** — o acento separa as palavras, e em 1.818 mensagens
nenhuma ocorrência legítima foi confundida.

**Dois defeitos de configuração achados de passagem em `bot/transcribe.py`** (não corrigidos —
não é escopo desta bateria): `response_format="text"` joga fora `compression_ratio`,
`avg_logprob` e `no_speech_prob`, que a Groq entrega de graça em `verbose_json` e que são os
sinais nativos de degeneração do Whisper; e o código trunca os hints em **850 bytes** enquanto
a Groq documenta o limite do `prompt` em **224 tokens** — unidades diferentes.

**Recomendação:** ligar a **(c)** já (é configuração, reversível numa chamada), tratar a
**(a)** como a fase seguinte, e **não construir a (b)**. Filtrar o token "Cade" tem mira
perfeita mas trata o sintoma de um caso — "Colby", "RAPL" e "Cope Online" são o mesmo defeito
com outra roupa.

**Testes:** 861 mensagens de áudio reais como corpus, 31 alucinadas como gabarito, e A/B de
2×2 rodadas na camada (c). Suíte do Kobe: **`595 passed, 53 skipped`**.

**Custo: US$ 0,05.** Acumulado das quatro baterias: **US$ 0,877** do teto de US$ 1,00.

**Estado:** **zero POST** na produção do Hindsight (só leituras `GET`); leitura do Postgres de
produção **somente `SELECT`**, autorizada na missão; embeddings intocados; `.env.dev`
restaurado com `diff` vazio; nenhuma chave escrita em disco, artefato ou commit.

**Reversão:** nenhuma linha de runtime foi escrita — o que existe é medição e documento.

### Highlander v3 — F0.5-D: Gemini direto × Anthropic, a combinação por operação (2026-08-28)

**Operador pediu:** entrar com o Gemini **direto** (provider nativo, chave dele) em vez de
passar pela OpenRouter — porque só o Gemini tem Batch API **e** cache explícito, os dois
descontos que somem no caminho da OpenRouter. E, na sequência, *"testes extensos comparando
modelos Gemini vs Anthropic para achar a MELHOR COMBINAÇÃO"* entre as duas operações da
memória, sob o critério **"o máximo de qualidade e o máximo de performance com o mínimo de
custo"**, com as três dimensões **medidas e tabeladas na mesma régua**.

**Por quê:** gravação e `reflect` têm perfis opostos — uma roda no daemon (latência não
aparece), a outra roda dentro do turno (o operador espera na frente da tela). O melhor modelo
para uma pode não ser o da outra.

**Foi feito:** seção 10 do relatório
`user-data/knowledge/kobe/status/2026-08-28-f05b-latencia-e-custo-por-provedor.md`, com o
sweep de qualidade em **4 trechos reais** do operador e as três dimensões lado a lado.

**Esta bateria INVERTE a recomendação de gravação da seção 4** — e a inversão é o achado
principal. Lá o `gemini-2.5-flash-lite` foi indicado porque extraía **mais fatos**. Com 4
trechos e **leitura do conteúdo, não da contagem**, mais fatos virou fatos piores: ele grava
conteúdo **efêmero** (que a missão do bank manda ignorar), **narração do fluxo da conversa**,
duplicatas — e, num trecho, **transformou uma pergunta do operador em decisão** (*"é
restaurar a produção no ambiente Dev?"* virou *"a estratégia atual envolve restaurar a
produção…"*). É a dor que originou o Highlander de cabeça para baixo. O `claude-haiku-4-5`
extrai menos e melhor, preservando condicionais — **mas fabricou 1 definição em 21 fatos**
em cima de ruído de transcrição, e isso está registrado para não vender modelo.

**Três verificações que o brief pedia, e as três mudaram alguma premissa:**
- **A chave Gemini é do plano PAGO**, provado objetivamente: ela chama o
  `gemini-3.1-pro-preview`, que o Google lista como indisponível no gratuito. **Logo a
  política de "o conteúdo é usado para melhorar produtos" — que é a do gratuito — não se
  aplica**, e não há trava de privacidade para usar Gemini na memória pessoal.
- **Cache explícito: ativa na gravação, não ativa no `reflect`.** Medido no `usage`: **98,4%
  da entrada em cache a partir da 2ª chamada** na extração (entrada 10× mais barata), e
  **zero** no `reflect` — cujo loop injeta resultado de ferramenta a cada iteração e não tem
  prefixo estável. O desconto existe exatamente onde está o volume.
- **Batch NÃO é automático**, ao contrário do que o brief supunha:
  `HINDSIGHT_API_RETAIN_BATCH_ENABLED` vem **`false`** e **exige retain assíncrono** — os
  −50% custam a memória entrar minutos depois.

**Recomendação: `claude-haiku-4-5` nas duas pontas, R$ 27,81/mês.** Na gravação, pagar
**R$ 19/mês a mais** para não gravar dúvida como decisão é o melhor negócio da tabela. No
`reflect`, latência empata na prática (4,7 s contra 2,2 s, os dois folgados no `timeout` de
20 s), então decide o mesmo defeito. **Alternativa barata declarada:** tudo em
`gemini-2.5-flash-lite` custa **R$ 1,38/mês** (R$ 0,78 com Batch) e mede pior nos 4 trechos.
`gemini-3.1-flash-lite` funde decisões distintas — **newer não é better nesta tarefa**.

**Testes:** 4 trechos reais × 3 modelos no sweep de qualidade, mais N=3 de latência por
operação. Suíte do Kobe: **`595 passed, 53 skipped`**.

**Gasto: US$ 0,1161 nesta bateria; acumulado US$ 0,8266 (R$ 4,25) do teto de US$ 1,00.** O
que sobrou **não** foi gasto de propósito: o sweep excluiu Sonnet e `gemini-3.5-flash` porque
os dois já estavam eliminados (o Sonnet mede pior e custa 2,9× o Haiku; o `3.5-flash` custa
15× o flash-lite e o `3.1` da mesma família já tinha medido pior que o `2.5`). Gastar o teto
em candidato eliminado seria comprar informação inútil.

**Estado do ambiente:** **embeddings não foram tocados** (o bank segue vetorizado como
estava); `.env.dev` restaurado com `diff` vazio; produção do Hindsight no mesmo container
`a122c528…` com 35 h de uptime e zero POST; **nenhuma chave escrita em disco, artefato ou
commit** — todas passaram por variável de ambiente na linha do `up`.

**Reversão:** nenhuma linha de runtime foi escrita; o que existe é medição e documento.

### Highlander v3 — F0.5-C: o pool de workers `claude` quentes resolve? (2026-08-28)

**Operador pediu:** depois da F0.5-B, levou a hipótese adiante com uma pergunta de
arquitetura, na analogia de DBA dele — *"se eu tivesse um determinado número de binários do
Claude rodando, esperando serem chamados, como funcionários ociosos (…) qual seria o
resultado?"*. É buffer pool: pagar memória parada para não pagar o custo de estabelecer a
conexão a cada chamada. **A motivação é financeira e explícita:** ele já paga a assinatura e
não quer pagar API por cima. O entregável pedido **não é um pool**, é o número que permite
decidir se vale construir.

**Por quê:** se o tempo estiver no spawn, pool resolve; se estiver na rota, não resolve nada.
O brief mandava **decompor os 40 s antes de montar qualquer pool** e parar se o overhead fixo
fosse pequeno.

**Foi feito:** a decomposição, e ela **encerrou a bateria** — nenhum pool foi construído,
como o próprio brief mandava nesse caso. Mediram-se, ainda assim, o worker quente real, a
memória e o risco de estado, porque tudo isso roda na assinatura e custa **US$ 0,00**.
Seção 9 do relatório
`user-data/knowledge/kobe/status/2026-08-28-f05b-latencia-e-custo-por-provedor.md`.

**A resposta: não vale. O pool funciona e ataca a fatia errada.**
- **spawn + bootstrap = 1,67 s** (N=3, prompt trivial, no ambiente de isolamento do provider);
- cada chamada de LLM pela assinatura custa **9 a 11 s** — o Hindsight imprime a própria
  decomposição, e um `reflect` são **3 a 5 chamadas**, 95–98% da parede;
- worker quente **medido de verdade** (processo `claude -p --input-format stream-json` vivo,
  recebendo despachos pelo stdin): economia real de **2,24 s por chamada**, o que levaria o
  `reflect` de **46,8 s para ~35,6 s** — contra **4,7 s** do mesmo Haiku pela API direta;
- **o número que fecha a discussão:** um worker **quente** da assinatura (10,7 s numa
  extração) continua **mais lento que uma chamada fria de API** (7,4 s), e ~4× mais lento que
  o `gemini-2.5-flash-lite` (2,8 s).

**Correção de atribuição na F0.5-B:** a TL;DR daquela seção dizia que os 40 s eram "o spawn
do binário a cada chamada". **Errado** — o spawn é 17% disso. O texto foi corrigido no mesmo
documento, com a nota dizendo o que mudou e por que importa.

**Risco de estado entre chamadas — confirmado, e ao contrário do que o brief supunha, tem
conserto.** O worker lembrou, na 3ª chamada, um número dito na 2ª — num pool servindo o Kobe
isso seria um `reflect` de um bank enxergando o do tópico anterior. Mas despachar `/clear`
entre chamadas **zerou o contexto em 0,026 s**, verificado com a mesma armadilha. O que mata
a ideia é a aritmética, não o vazamento.

**Memória, medida e não assumida:** worker ocioso **159 MB**, **270 MB** em regime, e **não
cresce** entre chamadas. Cabem ~3 workers na VPS — mas **o limite real são as salas do Coder**
(~500 MB cada), não o pool.

**Veredito:** paga a API. O pool economizaria **R$ 3,93/mês** (o custo inteiro do mix
recomendado) e custaria um shim compatível com o protocolo da OpenAI, um supervisor de
workers e uma fila — um serviço com estado na frente da memória durável — com 11 segundos
ainda na mesa.

**Testes:** a própria bateria é a medição; cada número traz N e a dispersão (p50, min–max).
Suíte do Kobe: **`595 passed, 53 skipped`**.

**Custo: US$ 0,00.** Rodou toda na assinatura; o gasto acumulado segue nos US$ 0,7105 da
F0.5-B.

**Estado do ambiente:** todos os workers de teste mortos e **confirmados mortos por `ps`**;
memória disponível terminou em 4.548 MB (contra 4.318 MB no início) e o swap ficou onde
estava (1.392 MB); nenhum serviço do bot reiniciado, nenhuma sala tocada; `.env.dev`
restaurado com `diff` vazio; produção do Hindsight no mesmo container `a122c528…`.

**Reversão:** nada a reverter — nenhuma linha de runtime foi escrita. O que existe é medição
e documento.

### Highlander v3 — F0.5-B: é o modelo ou o caminho? e quanto custa por mês (2026-08-28)

**Operador pediu:** depois de ler o resultado da F0.5, mandou isolar a variável — o `reflect`
de ~40 s pela assinatura era **o modelo da Anthropic sendo lento** ou **o caminho da
assinatura**? E, junto, estudar a OpenRouter atrás do melhor custo × qualidade, recomendar
**por operação**, e projetar o custo **mensal em reais** com volume real e cotação do dia.
Teto de gasto para a bateria inteira: **US$ 1,00**.

**Por quê:** a F0.5 mudou duas coisas ao mesmo tempo (modelo e caminho) e concluiu sobre as
duas juntas. Uma conclusão dessas não sustenta decisão de arquitetura.

**Foi feito:**
- `infra/hindsight/docker-compose.models.yml` ganhou o repasse da **chave do provider**
  (`HINDSIGHT_API_LLM_API_KEY`, que o compose principal amarrava à `OPENAI_API_KEY`, e por
  isso impedia apontar para `anthropic`/`openrouter`) e o amarramento **separado** da chave
  de embedding — ver a armadilha abaixo. A chave nunca é escrita em disco: o Compose lê o
  ambiente do shell com precedência sobre o `--env-file`.
- 8 caminhos medidos com **N=3** por combinação, mesmo bank e mesmo trecho da F0.5, nas duas
  operações (extração e `reflect`), reportados em **p50 (min–max)**.
- Relatório em
  `user-data/knowledge/kobe/status/2026-08-28-f05b-latencia-e-custo-por-provedor.md`
  (+ linha no índice detalhado e índice curto regerado).

**A resposta: é o CAMINHO.** Mesmo `claude-haiku-4-5`, mesmo bank, mesmos embeddings:
`reflect` em **4,7 s pela API direta** contra **~40 s pela assinatura** — **~8×**. O provider
`claude-code` faz spawn do binário `claude` e bootstrap do SDK **a cada chamada**, e um
`reflect` são 4 a 6 chamadas. **Isso desfaz o dilema da F0.5:** usar o mesmo Haiku pela API
paga cabe folgado no `timeout=20.0` do `kobe-reflect`, sem tocar em código nenhum.

**Achado inesperado:** `google/gemini-2.5-flash-lite` pela OpenRouter extraiu **mais fatos
que o Sonnet** (6/5/5 contra 4/4/4), foi **o mais rápido de todos** (2,8 s de extração,
3,4 s de `reflect`) e custa **R$ 2,27/mês**.

**Confiabilidade virou critério eliminatório:** três dos cinco candidatos da OpenRouter
falharam a extração. `qwen3.5-flash` **nunca** completou — devolve um float no lugar do
objeto do schema, três tentativas, zero fatos e tokens gastos. `mistral-small` e
`deepseek-v4-flash` falharam 1 em 3. **Declarar `structured_outputs` no catálogo não é
honrar o schema**, e para memória durável uma extração que falha é um fato que não é
gravado, em silêncio.

**⚠️ Armadilha registrada:** a chave de embedding do Hindsight **cai por padrão na chave do
LLM**. Ao apontar o LLM para outro provider, os embeddings mandam a chave errada para a
OpenAI, voltam 401 — e o `reflect` **não falha**: responde "não há registro" porque as
ferramentas de busca engolem o erro. **Latência bonita, resposta oca.** A primeira rodada da
Anthropic saiu assim e foi **descartada e refeita**; só foi pega porque a resposta do modelo
denunciou o erro de autenticação. Extração não é afetada (não usa embedding).

**Correção de uma conclusão da F0.5:** lá ficou escrito que "o Haiku errou por inversão" no
fato mais difícil. **Era N=1 e não se sustenta**: pela API direta, as três corridas
acertaram. O erro foi de amostragem minha, não característica do modelo.

**Custo, com volume medido e não chutado:** leitura somente de contagem na produção (30 dias,
todos os tópicos) = **390 mensagens/mês**. Câmbio buscado e citado (Frankfurter/BCE,
27/08/2026, R$ 5,142). Do mais barato credível (**R$ 2,27/mês**) ao mais caro imaginável
(Sonnet em tudo, **R$ 80,79/mês**). **Recomendação por operação** — gravação e consolidação
no `gemini-2.5-flash-lite`, `reflect` no `claude-haiku-4-5` via API: **R$ 3,93/mês**. A
leitura que importa mais que o ranking: **neste volume, dinheiro não é a restrição.**

**Testes:** as 8 baterias **são** o teste, e cada linha traz `ok/3` de confiabilidade junto
da latência. Suíte do Kobe: **`595 passed, 53 skipped`**, igual à linha de base.

**Gasto real: US$ 0,7105 (R$ 3,65) — 71% do teto de US$ 1,00.** Dois erros meus, registrados
porque são de método: projetei US$ 0,19 e gastei 3,7× isso (subestimei o `reflect` em
tokens de entrada — o real é 10–20 mil, não 5 mil — e tive de refazer as baterias da
Anthropic depois da armadilha acima); e declarei um ponto de parada em US$ 0,60 que **não
respeitei**, porque o gasto da Anthropic só é observável depois da última chamada. Ponto de
parada que depende de número que só existe no fim não é ponto de parada.

**Estado do ambiente:** zero POST na produção do Hindsight (container `a122c528…`, uptime
contínuo); `.env.dev` restaurado com `diff` vazio; as chaves da Anthropic e da OpenRouter
**nunca escritas em disco**; nenhum código de runtime do Kobe alterado — o `timeout=20.0` do
`bot/hindsight_client.py` foi **medido e recomendado, não mexido**.

**Reversão:** commits limpos na branch `coder/85ecc404`; backup carimbado em
`.local/backups/` (`20260828-001343`).

### Highlander v3 — F0.5: provar a assinatura e escolher os modelos (2026-08-28)

**Operador pediu:** a F0.5 do briefing aprovado (`07-briefing-v2.md`, seção 5) — três
medições: (1) provar que o Hindsight consegue usar **a assinatura** do operador em vez da
API paga (`HINDSIGHT_API_LLM_PROVIDER=claude-code`); (2) provar **embeddings locais**
(modelo multilíngue rodando na própria VPS); (3) **comparativo `mini × Haiku × Sonnet`** na
extração de fatos, com conversa real do operador e sem sujar o bank de produção.

**Por quê:** o resultado define o orçamento de todas as fases seguintes. O item 1 é o de
**maior incerteza do plano inteiro** — o Hindsight roda em container e a credencial da
assinatura vive no host, e ninguém tinha testado se ela atravessa.

**Foi feito:**
- `tests/roteiros/f05-regressao.txt` — a bateria de **regressão** conversacional da fase
  (2 cenários da seção 9.4 do briefing), na mesma convenção dos quatro roteiros da F0.
- `infra/hindsight/docker-compose.models.yml` — **override** de compose que existe só para
  o ambiente de dev, com `.env.dev.example` documentando cada variável nova. Ele repassa o
  `HINDSIGHT_API_LLM_MODEL` (que o compose principal não repassava, tornando impossível
  comparar modelos), parametriza o modelo de embedding local, e monta a assinatura do
  operador **read-only**. **Não age sozinho**: só entra com um `-f` explícito, então a
  produção pode receber o arquivo por `git pull` e continuar exatamente como está — foi por
  isso que o nome auto-carregado `docker-compose.override.yml` foi rejeitado.
- **As três medições**, com o relatório curto em
  `user-data/knowledge/kobe/status/2026-08-28-f05-assinatura-embeddings-modelos.md`
  (+ linha no índice detalhado e índice curto regerado).

**Os três resultados, em uma linha cada:**
1. **A assinatura atravessa o container — SIM.** O Hindsight extraiu fato real com
   `HINDSIGHT_API_LLM_PROVIDER=claude-code`, sem API paga. A imagem 0.8.3 **já traz** o
   `claude_agent_sdk` (e toda a pilha de embedding local); o que falta é só o binário
   `claude`, montado do host read-only. **Nenhuma imagem derivada foi necessária.**
   **Mas cobra ~10× em latência:** um `reflect` sai de **~4 s** (`openai`) para **~40 s**
   (`claude-code`), medido com uma variável só mudando — e o `kobe-reflect` usa
   `timeout=20.0`, então **ligar a assinatura como está quebra o `kobe-reflect`**.
2. **Embeddings locais — SIM.** `intfloat/multilingual-e5-small` via ONNX, 384 dimensões,
   carga em 4,5 s. Prova real: recuperou um fato em português por **paráfrase sem palavra
   em comum** e por **pergunta em inglês** — o que o default `bge-small-en` não faz.
   **Mas adotar não é trocar uma variável:** o Hindsight **recusa** mudar a dimensão do
   vetor com dado gravado (erro explícito na subida, sem migração no lugar), então em
   produção significa **re-embeddar os ~1,25 mil fatos**.
3. **`mini × Haiku × Sonnet`** sobre a **mesma fala real do operador** (sha256 registrado):
   4 / 5 / 5 fatos. No fato mais difícil — uma negação embaralhada pela transcrição —
   **`mini` acertou, `Haiku` inverteu a decisão, `Sonnet` acertou**.

**Três armadilhas de uid que fazem a assinatura parecer quebrada sem estar** (todas
consertadas e comentadas ao lado da linha no override): credencial de outro uid é ilegível
e o erro que aparece é `Not logged in`; um uid sem entrada no `/etc/passwd` estoura
`getpass.getuser()` dentro do `torch`; e sem `HOME` declarado o Docker o resolve como `/`,
o `huggingface_hub` tenta criar `/.cache` e o serviço entra em **crash-loop**. Nenhuma das
três se apresenta como o que é.

**Achado registrado, não consertado (fora de escopo, como o brief pediu):** a pasta
`infra/hindsight/` da árvore de **dev** não tem `.env.dev`, só o `.example`, enquanto a da
prod tem o real — a stack de dev do Hindsight **não é reproduzível a partir da árvore de
dev**. Ela funciona porque o operador a montou à mão, não porque o produto a monta. Mesma
família da pendência `2026-08-27-indice-curto-usuario-novo.md`.

**Testes: 22 cenários, 21 verdes e 1 vermelho.** Linha de base antes de tocar em qualquer
coisa e ao final: **`595 passed, 53 skipped`** nas duas pontas. O vermelho é o cenário
**D2** — o `kobe-reflect` estourando o timeout de 20 s contra um `reflect` de ~40 s — e ele
**é o achado mais útil da fase**, não um defeito da entrega: foi a bateria de regressão
fazendo o que existe para fazer. O turno **não morreu**; a regra "o Hindsight nunca derruba
um turno" continua de pé (cenário D3 verde). Placar completo no relatório.

**Duas coisas que a própria casa pegou:** o `tests/portability_guard.sh` reprovou um caminho
de máquina que tinha ficado como default no compose novo — consertado tirando o literal e
tornando a variável obrigatória, não silenciando o teste. E a produção do Hindsight foi
conferida **antes e depois de cada subida**: mesmo container `a122c528…`, uptime contínuo,
do começo ao fim. O total de fatos dela subiu de 1.246 para 1.254 na janela, e **essa
diferença não é desta sessão**: os 8 entraram no bank `kobe-dev-kobe`, gravados pelo bot de
produção nas conversas do operador. Nenhuma escrita foi emitida para a porta 8888; os outros
quatro banks ficaram idênticos.

**Estado do ambiente:** `.env.dev` do Hindsight e `.env` do Kobe de dev restaurados dos
backups carimbados, os dois com `diff` vazio; Hindsight de dev de volta em `openai` +
`text-embedding-3-small`. Ficam dois resíduos **aditivos e inertes**, à escolha do operador
remover: o banco vazio `hindsight_f05` no Postgres de dev (criado para provar o item 2 **sem
apagar nada**) e o volume `hindsight-hf-cache-dev` com o modelo de 470 MB.

**Reversão:** commits limpos na branch `coder/85ecc404`; toda mudança de valor fora do git
tem backup carimbado em `.local/backups/` (`20260828-001343`).

### Highlander v3 — F0: placar do plano de testes (2026-08-27)

**Operador pediu:** a diretriz de 27/08 às 22:29 — *"quem roda esse plano é o Kobe, no
ambiente de desenvolvimento; a mensagem 'tá pronto' passa a significar **tá pronto e já
testei**"* —, com teste de comportamento **ponta-a-ponta pelo bot**, via `infra/dev_inject.py`,
e o plano de testes nascendo como **arquivo de roteiro**.

**Por quê:** as três camadas de dev (código, banco `kobe_dev`, e agora o bot
`kobe-dev.service`/`@hal_dev_bot`) tornaram possível provar comportamento sem depender do
operador digitar. Sem isso, metade da F0 seria "passou no pytest" — que não é a mesma coisa
que "funciona conversando".

**Foi feito:** quatro roteiros versionados em `tests/roteiros/` (`f0-01-nucleo`,
`f0-02-kb`, `f0-03-hindsight`, `f0-04-regressao`) — são o artefato executável, e as fases
seguintes reusam. **42 cenários executados: 41 verdes, 0 vermelhos, 1 com ressalva; 1 não
executado.** Destes, **13 rodaram pelo bot**. Relatório completo com a evidência de cada um
em `.local/f0-testes/RESULTADO.md`.

**Linha de base medida ANTES de tocar em qualquer coisa:** `555 passed, 53 skipped`, mais
uma bateria `f0-04` pelo bot. **Ao final: `595 passed, 53 skipped`** — 40 testes novos,
nenhuma regressão.

**As três provas que valem ser lidas:**
- **A7/A8** — canário no `MEMORY.md`: mesma pergunta, sem o arquivo o bot disse *"não
  aparece em lugar nenhum do meu núcleo curado"*; com o arquivo citou a linha e **notou a
  própria contradição** — *"a resposta que te dei há um minuto era falsa"*. `prompt_len`
  6.696 → 8.792.
- **B7/B8** — o bot listou as 12 seções da base com `tool_calls=0` (*"direto do índice curto
  que já vem no meu prompt"*) e, mandado abrir o detalhado, contou **13 entradas** — o mesmo
  número que o gerador determinístico tinha contado. Modelo e código concordando sobre a
  mesma fonte.
- **F7** — perguntado sobre o passado, o agente **foi buscar sozinho**: o log do Hindsight de
  dev registra `reflect` às 02:16:03–02:16:08, dentro da janela do turno, com a consulta
  *"bateria de testes F0 Highlander v3"*. É o item 2 funcionando, verificado no servidor e
  não na resposta.

**Duas lacunas declaradas (nomeadas no plano antes de executar, não depois):**
- **L1** — a magnitude dos 4–7 s **não** é reproduzível em dev: o bank de dev tem 1
  documento e a chamada custa 0,33 s de mediana ali; os 4–7 s vêm dos 934 fatos de produção.
  Dev prova a direção, não o tamanho.
- **L3** — o normalizador não tem prova ponta-a-ponta: o `dev_inject` injeta texto e o
  normalizador só toca áudio. Não foi fechada baixando o critério.

**Estado do ambiente de dev:** tudo que foi mexido pra testar foi restaurado e conferido —
`.env` (`diff` idêntico ao backup), a KB do tópico, e o `MEMORY.md` (sem o canário e sem o
fato que o próprio agente gravou durante a bateria, que era artefato de teste).

**Reversão de tudo:** `git revert` do merge `coder/4d1d3791`; SHA pré-merge
`66fff37cbc0ce78fd1d7342cf2bb68e3747aea8f`.

### fix: o `kobe-reflect` estava quebrado no caminho em que o agente o chama (2026-08-27)

**Operador pediu:** nada — este apareceu **rodando o plano de testes da F0** (cenário F6),
e o conserto entrou porque o item 2 da F0 é justamente *documentar o `kobe-reflect` pro
agente usar*. Documentar uma ferramenta quebrada seria pior que não documentar.

**Por quê:** o helper re-executa no venv do projeto quando as dependências faltam no
interpretador atual, mas o guard conferia **só o `psycopg`**. Nesta VPS o python do sistema
**tem** psycopg e **não tem** httpx — então o guard concluía que estava tudo bem, não
re-executava, e o script morria duas linhas depois com
`ModuleNotFoundError: No module named 'httpx'`. Como o agente chama o helper por `Bash`
(mesmo python do sistema), ele falhava exatamente no caminho de uso real. Isso ajuda a
explicar os **7 usos em 280 turnos**: quem tentou, tomou traceback.

**Foi feito:** o guard passou a conferir **todas** as dependências (`psycopg`, `httpx`,
`dotenv`) — se qualquer uma faltar, re-executa no venv. Os outros helpers com o mesmo
padrão (`kobe-recall-since`, `kobe-await-response`, `_kobe_topic.py`) foram conferidos e
**não** são afetados: nenhum importa httpx.

**Testes:** F6 do plano da F0, **verde depois do conserto** (vermelho antes, com o
traceback acima). Rodado contra o Hindsight de dev, devolveu resposta **citada** —
inclusive recuperando a memória que a bateria E6 tinha gravado minutos antes, o que fecha
o par retain → reflect ponta a ponta.

**Reversão:** `git revert` deste commit (volta o guard antigo, e o bug junto).

### Highlander v3 — F0.2, F0.3 e F0.5: o que estava documentado errado (2026-08-27)

**Operador pediu:** itens 2, 3 e 5 da F0 — documentar o `kobe-reflect` no `CLAUDE.md`,
desligar a consulta automática de memória, e corrigir a frase do `~/.claude/CLAUDE.md` que
afirma que o índice da base é carregado todo turno.

**Por quê:** os três são o mesmo problema em três lugares — **a documentação descrevia um
sistema que não é o que roda.** O `kobe-reflect` existe, funciona, é o caminho citado da
memória durável, e foi usado em **7 de 280 turnos (1,8%)** porque nada mandava usar. O
manual global afirmava que o índice da base entrava em todo turno — falso desde que ele
passou dos 8.000 chars. E o comentário do `.env.example` justificava o recall desligado
pela confabulação, sem mencionar o custo medido, que é o argumento mais forte.

**Foi feito:**
- **F0.2** — seção nova no `CLAUDE.md`: o que o `kobe-reflect` é, quando usar, e **como ler
  a saída** — em especial que resposta vazia significa *"não há registro"*, e não licença
  pra responder de memória. Está dito também o que ele **não** é (não busca a conversa
  bruta; isso é o `kobe-remember` da F2), pra a doc não prometer o que não existe.
- **F0.3** — nenhuma mudança de comportamento: o default no código já era `false` e o
  `.env` de dev também. O `.env.example` passou a carregar o **número medido** (4–7 s por
  turno pra 0,3% do prompt, e piorando ~30 fatos por dia). Os dois gates viraram funções
  (`_retain_ativo` / `_recall_ativo`) pra que *"desligar a leitura não desliga a gravação"*
  seja afirmação **testável**, não comentário.
- **F0.5** — `~/.claude/CLAUDE.md` corrigido. **Backup carimbado antes de encostar**:
  `.local/backups/CLAUDE.md.global.20260827-231019` (SHA-256 conferido contra o original).
  A frase falsa saiu; entrou a descrição do mecanismo real (orçamento por arquivo, curto
  sempre injetado, detalhado sob demanda, aviso quando degrada).

**Testes:** bloco C, `tests/test_hindsight_recall_gate.py` — **5 verdes**. C4 é o que trava
o critério do briefing (*"a gravação continua"*). C2 prova nos dois sentidos: sem recall o
prompt não tem a seção de memória durável, e com recall tem — senão o teste passaria mesmo
se alguém removesse a funcionalidade inteira. A fixture monta um `.env` mínimo **explícito**
porque `load_config(None)` sobe procurando `.env` na árvore e mediria a máquina de quem
roda, não o default do código. **F0.5 é verificado por inspeção** (lacuna L2, declarada no
plano): `~/.claude/CLAUDE.md` é o manual do Claude Code, não do Kobe — nenhuma mensagem no
bot exercita aquela frase. Evidência: `grep` da frase antiga não devolve nada, e o `diff`
contra o backup mostra exatamente a linha trocada.

**Reversão:** `git revert` (F0.2/F0.3); para a F0.5, `cp` do backup carimbado de volta.
O passo que **de fato** devolve os 4–7 s é manual e **não foi executado** — está declarado
como P1: `HINDSIGHT_RECALL=false` no `.env` de produção + `systemctl --user restart kobe`.

### Highlander v3 — F0.6: normalizador determinístico de transcrição (2026-08-27)

**Operador pediu:** item 6 da F0 — normalizador de transcrição com glossário próprio,
aplicado antes de gravar a mensagem. Com portão humano: a lista do que ele corrigiria
passa pelo operador **antes** de entrar em produção.

**Por quê:** a única proteção contra erro de transcrição era o `prompt` do Whisper, lido
de `user-data/transcription-hints.md` e limitado a **850 bytes** (verificado em
`bot/transcribe.py:67`), já com 616 usados. Esse arquivo **já lista** "Kobi/Colby/Cobi →
Kobe" e mesmo assim a memória durável tem *"os plugins do Koby ficarão em home, Filipe e
Kobi"*. Dica é probabilística: empurra o modelo, não garante. E erro de transcrição, uma
vez gravado, vira **fato permanente** — volta meses depois como se fosse verdade.

**Foi feito:**
- `bot/transcription_normalizer.py` (novo): glossário `errado -> certo` em
  `user-data/transcription-glossary.md`, **sem limite de tamanho**, com `.example`
  versionado. Casamento por limite de palavra, insensível a caixa e acento, saída na
  grafia declarada. Aviso quando o glossário tem ciclo (que quebraria a idempotência).
- Aplicado **só ao texto vindo de áudio**, depois do Whisper e antes de gravar. Texto
  digitado é intenção do operador e não passa por ali — e há teste estrutural travando
  isso (o normalizador tem **um** ponto de chamada, dentro de `_download_and_transcribe`).
- Trilha de auditoria em `user-data/transcription-normalizer/<AAAA-MM>.jsonl` com o texto
  **original** e as regras que bateram. Nada se perde (§4.7 do briefing).
- Flag `TRANSCRIPTION_NORMALIZER_ENABLED`, **default OFF**.
- `bot/bin/kobe-normalize-report` (novo): modo relatório, **rigorosamente somente-leitura**
  — não existe caminho de `UPDATE` neste script, e não é pra existir.

**Bug achado ao rodar contra dado real, e consertado:** a prosa do próprio template explica
o formato usando uma seta (*"a regra `Kobi -> Kobe` pega…"*) e o parser transformou **a
frase inteira** em regra — ela apareceu na 1ª versão do relatório com 191 ocorrências
fantasma. Regra agora só vale sob o título `## Regras`, e `#` isolado é comentário (não
fecha a seção). Virou teste. Não aparecia em fixture nenhuma; apareceu na varredura real.

**Testes:** bloco D, `tests/test_transcription_normalizer.py` — **14 verdes**, cobrindo o
que ele NÃO pode tocar ("Kobierski", "cobiça", "Raulzito"), caixa/acento, idempotência,
flag off, glossário ausente e a trilha de auditoria. **D11 (o portão humano) executado**:
relatório contra as **3.521 mensagens** de `kobe_dev` — 395 substituições em 160 mensagens
(146 de áudio/user, que são as únicas que ele tocaria; 11 de resposta do agente e 3 de
texto digitado entram só como diagnóstico). Lista entregue ao operador por anexo.
**D12 (áudio ponta-a-ponta) NÃO executado** — é a lacuna L3, declarada no plano: o
`dev_inject` injeta texto e o normalizador só toca áudio. Aguarda decisão do operador.

**Reversão:** flag off (não precisa de deploy); ou `git revert`. Nenhum dado histórico foi
alterado por esta mudança, em nenhum ambiente.

### Highlander v3 — F0.7: carimbo de tempo no retain e `observation` na leitura (2026-08-27)

**Operador pediu:** item 7 da F0 — duas linhas no cliente do Hindsight: mandar o carimbo
de tempo no retain e incluir o tipo `observation` na leitura sob demanda.

**Por quê:** são dois anti-padrões que a **documentação do próprio Hindsight** nomeia e que
o Kobe cometia. (1) *"Missing `timestamp` on retain → disables temporal retrieval
strategies"* — sem eixo de tempo, "o que a gente decidiu em julho?" não tem como ordenar
nada, que é exatamente a dor que abriu a missão. (2) o default de `types` do Hindsight é só
`world`+`experience`, e o cliente replicava isso — deixando **507 dos 934 fatos** do bank do
operador invisíveis. `observation` é a camada consolidada, deduplicada e com contagem de
evidência: a mais útil justamente pra retomar assunto velho.

**Foi feito:**
- `bot/hindsight_client.retain()` ganhou o parâmetro `timestamp` (opcional, ISO 8601,
  retrocompatível — sem ele a chave nem vai no payload).
- `bot/telegram_handler.py` passa `message.date` — **quando o operador falou**, não quando
  a gravação aconteceu.
- `recall()` passa a pedir os três tipos por default; `types` explícito continua mandando.
- Campos confirmados no `/openapi.json` da instância 0.8.3 que está rodando, não de memória.

**Testes:** bloco E, `tests/test_hindsight_f0.py` — **5 verdes**, sendo E4/E5 contra o
**Hindsight de dev de verdade** (`:8890`, bank descartável por execução), que é o que prova
que o campo é **aceito** e não só enviado: um `422` silencioso viraria memória não gravada.
**E6 (ponta-a-ponta pelo bot), verde e com a evidência do lado do servidor** — uma mensagem
injetada com `dev_inject` virou documento no bank `kobe-dev-ambiente-dev` com
`retain_params.event_date = 2026-08-28T01:59:39+00:00` (o carimbo que mandamos) e as duas
memórias extraídas saíram com `mentioned_at` no mesmo instante: **o eixo temporal existe**,
onde antes não existia. E o documento nasceu com `nodes_by_fact_type: world 0, experience 1,
**observation 1**` — um fato que, sem a segunda linha desta mudança, seria invisível à
leitura. Suíte: **576 passed, 53 skipped** (base 555).

**Reversão:** `git revert` deste commit. As duas mudanças são independentes e reversíveis
uma sem a outra.

### Highlander v3 — F0.4: a base de conhecimento parou de sumir em silêncio (2026-08-27)

**Operador pediu:** item 4 da F0 — separar o índice da base em curto (sempre injetado) +
detalhado (sob demanda), subir o limite de injeção e **avisar quando degradar**, em vez de
degradar em silêncio.

**Por quê:** a pasta `knowledge/` do tópico era **tudo-ou-nada**. Se a SOMA passasse do
teto (8.000 chars), a pasta inteira virava lista de ponteiros e sumia do prompt — com um
`logger.info` que ninguém lê. No tópico `dev-kobe` isso é permanente: o índice cresceu pra
86.213 chars **num arquivo só**, então um arquivo grande derrubava todos os pequenos junto.
Resultado medido no briefing: a base curada foi aberta em **44 de 280 turnos (16%)**.

**Foi feito:**
- `bot/topic_manager.py`: o orçamento passou a ser gasto **arquivo a arquivo**, em ordem
  alfabética (a convenção `00-`, `01-`, … que a base já usa). Um `00-indice-curto.md` de
  3 KB entra **sempre**, por construção da ordem; o resto vai pro índice sob demanda, agora
  com o tamanho de cada arquivo declarado.
- Teto **8.000 → 12.000** (`TOPIC_KNOWLEDGE_INLINE_LIMIT`, env, rollback numa linha).
- Novo marcador interno de **degradação**, com payload (chars fora, teto, nomes), consumido
  pelo handler — que agora **avisa no Telegram** dizendo *quais* arquivos ficaram fora.
  Anti-ruído: um aviso por tópico por processo, reemitido só quando muda de faixa (~1 KB).
  `consume_truncated_marker` continua existindo (o `bot/resume.py` usa) sobre o novo
  `consume_markers`.
- `bot/bin/kobe-kb-shortindex` (novo): gerador **determinístico, sem LLM**, que deriva o
  índice curto do detalhado — títulos de seção, contagem de entradas e o caminho absoluto
  pro `Read`. Resumo por modelo apodrece quando alguém edita o detalhado e não regenera;
  este é função do arquivo, então rodar 2× dá byte a byte o mesmo.

**Testes:** bloco B, `tests/test_topic_knowledge_budget.py` — **9 verdes** em pytest, mais
**3 cenários ponta-a-ponta pelo bot de dev** (`dev_inject`, tópico AMBIENTE DEV), com a
fixture real: o curto gerado (1.302 chars) + uma cópia do índice de 86.475 chars.
- **B7 verde** — perguntando as seções *"sem abrir nenhum arquivo"*, o bot respondeu as
  **12 seções** com `tool_calls=0`: o curto estava inline. Palavra dele: *"Direto do índice
  curto que já vem no meu prompt (não abri nada)"*.
- **B8 verde** — mandando abrir o detalhado, `tool_calls=2` e a resposta certa: **13
  entradas** em "Componentes do bot", exatamente o número que o gerador tinha contado.
  Um modelo lendo a fonte confirmou a contagem do gerador.
- **B9 verde** — 2 turnos seguidos com a base degradada produziram **1** aviso, não 2.
- Suíte: **571 passed, 53 skipped** (linha de base 555). Sem regressão.

**Achado que fica pro operador (não consertado — é dado de produção):** o índice real tem
**66 das 153 entradas penduradas depois** do título "Regra de manutenção", sem seção
própria. O índice curto reporta isso fielmente, mas na hora de aplicar em produção (P3)
vale re-seccionar essas 66.

**Reversão:** `git revert` deste commit; ou `TOPIC_KNOWLEDGE_INLINE_LIMIT=8000` no `.env`
(volta o teto, mas mantém o orçamento por arquivo e o aviso).

### Highlander v3 — F0.1: tetos separados pra USER.md e MEMORY.md (2026-08-27)

**Operador pediu:** executar a F0 do Highlander v3; o item 1 é criar o `MEMORY.md` e
**separar os tetos** do núcleo curado.

**Por quê:** o núcleo curado tinha um teto único de 6.000 chars e o `USER.md` entrava
inteiro, deixando pro `MEMORY.md` **a sobra**. Com o `USER.md` real do operador (3.367
chars), a sobra era **2.633** — e o `MEMORY.md` é justamente a camada que precisa
acumular anos de convivência (frente (d) do briefing: "o agente conhecer o operador").
Pior: o arquivo **nunca tinha sido criado**, então a camada estava ligada, funcionando e
vazia desde o primeiro dia. E o empurrão de consolidação media o agregado, então um
`USER.md` grande cobrava o agente a consolidar uma memória durável que podia estar vazia.

**Foi feito:**
- `bot/memory/curated_core.py`: dois orçamentos independentes,
  `CURATED_CORE_USER_LIMIT` (4.000) e `CURATED_CORE_MEMORY_LIMIT` (6.000), cada arquivo
  truncado contra o **próprio** teto. `CURATED_CORE_CHAR_LIMIT` continua existindo como
  soma (é reexportado por `bot.memory`).
- O sinal de consolidação (~80%) passou a medir o `MEMORY.md` contra o teto **dele**.
- `user-data/identity/MEMORY.md` criado no dev VPS — andaime com o contrato de escrita
  (uma linha por fato, **com origem**), sem nenhum fato inventado sobre o operador.
  Preencher é trabalho da F5/LUCIEN.
- `.env.example` documenta as duas chaves e o rollback (4000/2000 devolve o de antes).

**Testes:** bloco A do plano de testes da F0, `tests/test_curated_core_budgets.py` — 7
cenários, **7 verdes**. A3 é o que prova o conserto: `USER.md` estourado não espreme mais
o `MEMORY.md`. A6 (real, contra o dev VPS): o bloco montado tem 5.063 chars e contém
`## MEMORY.md — fatos duráveis do agente` — o critério de pronto "existe **e** é
injetado". Suíte inteira: **562 passed, 53 skipped** (linha de base antes de mexer: 555
passed, 53 skipped). Sem regressão.

**Reversão:** `git revert` deste commit; ou, sem deploy, `CURATED_CORE_USER_LIMIT=4000` +
`CURATED_CORE_MEMORY_LIMIT=2000` no `.env`. Apagar o `MEMORY.md` volta ao estado de antes.

### Bateria funcional C3 executada — 13 verdes, 1 vermelho (2026-08-26)

**Operador pediu:** rodar a Camada 3 (cenários F1–F14) do Novo Ambiente Postgres contra o
bot de dev, registrando verde/vermelho com evidência, e atualizar o runbook com os números
que saíssem de F13 e F14.

**Por quê:** as Camadas 1 e 2 provam que o dado migrou íntegro e que a suíte não regrediu.
Nenhuma das duas prova que **o produto funciona rodando em cima do banco novo** — palavra
do operador em 26/08: *"Ah, legal, foi tudo ok com a migração. Então vamos ver se isso aqui
tá funcionando."* A Camada 3 era a lacuna.

**Foi feito:** os 14 cenários, contra `@hal_dev_bot` / `kobe_dev` com 3.469 mensagens
reais. Roteiro completo em `.local/migracao/bateria-c3.txt`; os cenários que não são
conversa viraram scripts próprios ao lado dele. Relatório com evidência por cenário em
`pendencias/2026-08-26-bateria-testes-exaustiva.md`; runbook atualizado com os números.

**O vermelho — e ele não é do ambiente novo.** F9 ("dev que constrói dev") falhou: a Trava 1
do dispatcher do Coder recusa o disparo com uma mensagem que se refuta sozinha — *"a cwd
`<árvore de dev>` está sob a raiz de PRODUÇÃO (`<a MESMA árvore de dev>`)"* — o caminho
recusado e a "produção" acusada são a mesma string.
A causa está em `plugins/public/coder/scripts/run_remote.py`, `_prod_cwd_reason`: ela deriva
"raiz de produção" de `$KOBE_HOME`, e o `.env` de dev define `KOBE_HOME` como a árvore de
dev. **Não é artefato do arnês** — o `kobe-dev.service` tem o mesmo `KOBE_HOME`, logo
nenhuma sessão Coder pode nascer a partir do bot de dev. **Não consertado de propósito:**
mudar a fonte de dado de uma trava de segurança é decisão de arquitetura, do operador.

**Quatro premissas da especificação que não se sustentaram**, todas verificadas na fonte
antes de virarem teste — nenhuma é regressão da migração:

1. **F6** — o Kobe não grava embedding (`bot/artifacts.py` diz que a coluna fica vazia até a
   "Fase 9"; 0 de 5 artefatos têm vetor). Não existe caminho de produto que escreva vetor,
   então o cenário passou a provar o **ambiente** (pgvector 0.6: escrita 1536-d, volta bit a
   bit, cosseno 0/1, índice ivfflat), com o escopo dito na cara.
2. **F7** — "Apolo lista contatos (ORDER BY nome)" não existe: a coluna é `nome_canonico` e
   o Apolo não tem `ORDER BY` nenhum. O caminho real é `nome_canonico ILIKE %s`, que depende
   do **ctype** — sob `C` um contato acentuado ficaria invisível **sem erro no log**. O
   cenário melhorou: testa ordenação e busca.
3. **F10** — o Keyko **não fala com o Postgres** (estado dos alertas é YAML). O segundo
   cliente do banco de verdade é o **Apolo**, que abre `psycopg.connect` próprio. Cenário
   partido em dois: o daemon (disparou, verde) e a concorrência real (dois clientes, 40
   rodadas cada, sem perda nem duplicata).
4. **F12** — `CONNECTION LIMIT 0` **não derruba** o banco em dev, porque não se aplica a
   superusuário e a conexão de dev é do `felipe`. Refeito pelo modo de falha real. Revelou
   uma assimetria que vale para o corte: em produção a role `kobe` **não** é superusuário.

**Testes:** os 14 cenários são o teste. Suíte de regressão: `pytest -q tests/` com **597
passando**. Latência medida do banco novo: p50 0,20 ms · p95 0,52 ms (n=300) — três ordens
de grandeza abaixo do turno completo (p50 33,5 s), que é dominado pelo LLM.

**Reversão:** nada a reverter — a bateria é leitura e escrita de sondas, todas apagadas ao
fim. Estado conferido: dev em `main`, serviço ativo, `kobe_dev` com `datconnlimit=-1`,
**produção intocada** e a **instância 5433 ainda vazia** (só as 6 linhas de
`schema_migrations`).

### Trava 0 do arnês: o `.env` da árvore vence o ambiente herdado (2026-08-26)

**Operador pediu:** nada — este apareceu **rodando**, na primeira execução real da bateria
C3, minutos depois do conserto das duas pernas. Vai registrado porque é da família dos
erros que a sessão do Coder existe pra não deixar passar.

**Por quê:** `load_dotenv()` **não sobrescreve** variável que já está no ambiente. Uma
sessão automatizada disparada pelo Kobe de **produção** herda o ambiente dele —
`TELEGRAM_BOT_TOKEN`, `GROQ_API_KEY`, `TELEGRAM_ALLOWED_USER_IDS`. Resultado observado: o
arnês subiu **falando como `@felipe_kobe_bot`** (o bot de produção) apontado para o chat de
**dev**.

Não houve estrago, e vale dizer exatamente por quê: o bot de produção **não é membro** do
grupo de dev, então o Telegram respondeu `BadRequest: Chat not found` e nada foi enviado.
Isso é sorte com cara de trava. Com os dois bots no mesmo grupo, a ferramenta teria falado
como produção sem avisar ninguém — que é precisamente a "porta dos fundos" que o docstring
deste arquivo diz temer.

**Foi feito:**

- **`forcar_env_do_arquivo(env_path)`** — aplica o `.env` da árvore **por cima** do
  ambiente e **devolve a lista do que atropelou**, que vira `WARNING` no log. Para uma
  ferramenta cujo propósito *é* rodar a configuração de dev, o arquivo manda e o ambiente
  não; e substituição silenciosa não serve nem quando está certa.
- **`get_me()` no arranque**, registrando `@usuário` e id do bot antes de qualquer injeção.
  Trocar de bot sem perceber passa a ser impossível **mesmo se a trava um dia falhar** —
  é a mesma lógica da reação 👀: um sinal primitivo que não depende do resto dar certo.
- Invólucro operacional `.local/migracao/injeta-dev.sh`, que limpa as variáveis herdadas e
  garante `~/.local/bin` no `PATH` (o turno chama o CLI do `claude`).

**Testes:** dois novos em `tests/test_dev_inject.py` — o `.env` sobrescreve token herdado e
a substituição é reportada; variável que só existe no arquivo **não** entra no aviso
(ruído em aviso é aviso que ninguém lê). Suíte inteira segue verde.

**Reversão:** `git revert 6111d2d`. Volta ao comportamento anterior — que é o que herdava o
token de produção.

### O arnês de injeção volta a funcionar — as duas pernas do conserto (2026-08-26)

**Operador pediu:** rodar a Bateria funcional C3 (F1–F14) do Novo Ambiente contra o bot de
dev, e — antes disso — consertar `infra/dev_inject.py`, "que monta o `Update` sem bot
associado e estoura em `_react_received`". É código de repositório, então vai com teste.

**Por quê:** o operador declarou que não vai testar o ambiente novo digitando. Sem um arnês
que funcione, a Camada 3 inteira depende dele — e o requisito não fecha. A ferramenta
existia, mas **nenhum turno chegava a começar**.

**Foi feito — e o bug era DOIS, o segundo escondido atrás do primeiro:**

1. **`Update` sem bot associado.** `montar_update` usava os construtores da
   python-telegram-bot; o objeto resultante não carrega bot, e `_react_received`
   (`bot/telegram_handler.py:391`) chama `message.get_bot()` na **primeira linha** do
   handler, pra reagir 👀. Resultado: `RuntimeError: This object has no bot associated
   with it` e o turno morria antes de existir. Agora monta por `Update.de_json(bruto,
   bot)` — o mesmo caminho que o PTB usa ao receber do Telegram, e que desce o bot pela
   árvore inteira do objeto. `set_bot()` não serviria: marca só a raiz e deixa os filhos
   órfãos pra estourar adiante.
2. **A mensagem sintética não existia do lado do Telegram.** Consertada a perna 1, o turno
   passava a andar e morria mais tarde: o bot responde **citando** a mensagem de entrada
   (`ProgressReporter` leva `reply_to_message_id`, `telegram_handler.py:1134`), e um
   `message_id` inventado faz o Telegram recusar com *"Message to be replied not found"*.
   Agora a ferramenta **publica** o texto no tópico (marcado com 🧪), usa o `message_id`
   real, e injeta. O `from` do update segue sendo o operador — quem julga é o `authz` de
   verdade; o eco só faz o objeto existir. Bônus alinhado ao que o arquivo sempre disse
   querer: o operador **assiste** a bateria ao vivo no grupo de dev. `--sem-eco` mantém o
   caminho antigo pra diagnóstico sem rede, com o risco documentado.

Mais duas peças que a bateria exige e que não existiam: **cronômetro por turno com resumo
p50/p95** (sem instrumento, latência vira achismo) e **modo `--rajada`**, que injeta sem
esperar o turno anterior fechar — sem ele não existe rajada, existe fila, e o FIFO por
tópico nunca seria exercitado.

Registrado também no docstring: o turno chama o CLI do `claude` em `~/.local/bin`, e um
shell sem esse `PATH` faz a bateria responder "o CLI do Claude não está disponível" —
falha do arnês, não do bot. Custou a primeira tentativa do dia.

**Testes:** `pytest -q tests/` — **597 passando** com `KOBE_TEST_DATABASE_URL`
(553 + 53 pulados sem banco), o mesmo número de referência do runbook, agora incluindo
**9 testes novos** em `tests/test_dev_inject.py`: o de regressão que amarra o bot
(`get_bot()` na mensagem, no chat e no usuário), o que garante que sem bot a montagem
funciona mas `get_bot()` recusa, os do `message_id` real, o da marca de teste no eco, e
quatro do percentil (amostra de um, ordenação, p95 que não estoura índice, amostra vazia
que recusa em vez de inventar 0.0).

**Achado fora do escopo, não consertado aqui:** com banco, 9 testes de
`tests/test_db_integration.py` falham — **e falham igual na árvore intocada**, antes de
qualquer mudança minha. A causa é resíduo no banco de rascunho `kobe_test` (65 mensagens,
54 tópicos de execuções anteriores): as fixtures derivam o canal de `crc32(nome do teste)`,
que é determinístico, então `ensure_topic` reencontra o tópico velho e conta as sobras
junto (`assert 9 == 3`). É limpeza de banco de rascunho, não código — e limpar banco é
operação destrutiva, que não faço sem a palavra do operador.

**Commits:** ver `fix(dev-inject)` abaixo.

**Reversão:** `git revert` do commit do conserto. A árvore de dev volta a `701f6cb0`, o
arnês volta a não funcionar, e nada mais no Kobe é afetado — `tests/test_dev_inject.py`
prova que nenhum arquivo de `bot/` importa este módulo.

### Diretriz do operador: o instalador PROVISIONA o banco, nao o pressupoe (2026-08-26)

**Operador pediu:** diretriz permanente, chegada depois do `C13` — *"O instalador nao vai partir do principio que existe um banco em algum lugar; ele vai criar esse banco."* Registrada em `.local/diretrizes/instalador-provisiona-o-banco.md`. A aplicacao a esta sessao e estreita e esta escrita la: **o `C13` nao pode cimentar a premissa contraria**. Construir o instalador completo e a sessao do instalador publico.

**Auditei o `C13` na fonte e ele CIMENTAVA a premissa, em tres lugares:**

1. `install.sh` anunciava, nos pre-requisitos, *"PostgreSQL 16+ acessivel, **com um banco vazio criado pro Kobe**"*.
2. Quando o runner falhava, a primeira causa sugerida era *"o banco nao existe ainda → **createdb kobe**"* — ou seja, mandava a pessoa fazer a mao exatamente o que o instalador deve fazer.
3. O `README.md` repetia a mesma exigencia.

**Foi feito — o degrau mais baixo e mais comum da diretriz, nada alem:**

- **`infra/provision_db.py`** — cria o banco do `DATABASE_URL` **se ele nao existir**. Conecta ao banco `postgres` do mesmo servidor (nao da pra criar um banco estando conectado a ele), confere `pg_database`, e cria. Idempotente: banco existente e reconhecido e **nao** e tocado. Tem `--dry-run`.
- **`install.sh` chama o provisionador ANTES do runner**, e os dois passos tem mensagem de falha propria — *"nao consegui preparar o banco"* e *"o banco existe, mas o schema falhou"* sao problemas diferentes e pedem coisas diferentes.
- Os tres textos foram corrigidos. O instalador agora diz, em maiuscula: **"VOCE NAO PRECISA CRIAR O BANCO — o instalador cria."**

**A parte que nao e burocracia: OS PARAMETROS DE CRIACAO.** Um banco criado com o default do `initdb` **nasce divergente**, e de dois jeitos que esta sessao ja tinha descoberto do jeito dificil:

- **Collation.** O `initdb` do Ubuntu cria em `C.UTF-8`, que ordena por byte cru. O dado e o mesmo; a ordem de `ORDER BY <texto>` muda — e o Kobe ordena a lista de contatos por nome. **Collation nao se troca depois sem recriar o banco.**
- **Fuso.** Todo banco herda o `TimeZone` do cluster, que fica no fuso local da maquina. Isso muda o TEXTO que o driver devolve pra `timestamptz` — e o Kobe compara `created_at` como string.

Se o instalador criasse com o default, **toda instalacao nova acenderia o portao T4 no primeiro dia**. Entao os parametros **nao sao cravados aqui**: saem de `tests/fixtures/schema_expected.json`, a **mesma** referencia que `infra/compat_gate.py` usa pra julgar. Uma fonte, dois consumidores — nao ha como o criador e o juiz discordarem, e ha teste exigindo que os dois apontem pro mesmo arquivo. Se a referencia estiver ilegivel (copia parcial do repo), o fallback ainda e um banco **sao**, nunca o default do `initdb`.

**A prova ponta a ponta, num banco que nao existia:** `provision_db` criou → `migrate.py up` aplicou as 6 versoes → `compat_gate` deu **verde** → a ponte gravou topico, sessao e mensagem. **De banco inexistente a Kobe funcional, sem ninguem rodar SQL a mao.**

**A FRONTEIRA, escrita no proprio modulo pra a proxima sessao herdar** — e com teste documental que falha se alguem apagar a lista. O que este arquivo **nao** faz: (1) detectar se ha PostgreSQL na maquina e qual versao, em vez de so tentar conectar; (2) decidir com a pessoa entre usar o cluster existente ou subir um dedicado; (3) criar cluster/instancia e criar **role** proprio com o minimo de privilegio — hoje o usuario da conexao e reaproveitado como dono; (4) instalar o PostgreSQL e o pgvector quando faltarem.

**Testes:** `tests/test_provision_db.py`, **21 testes**. Os que importam: os parametros de criacao batendo com a referencia; o criador e o juiz lendo o **mesmo** arquivo; um banco criado com esses parametros **passando pelo comparador de ambiente** (fecha o circulo sem tocar em banco); referencia ausente, corrompida e parcial caindo num default sao e **nunca** em `C.UTF-8`; leitura do nome do banco nas quatro formas usuais de string de conexao; URL sem nome de banco dando erro **explicado** em vez de `KeyError`; e a url de manutencao trocando **so** o banco, preservando host, porta e usuario — mudar o host por engano provisionaria no lugar errado.

Suite verde — **544 passando + 53 pulados**; com banco, **597 passando**. `tests/portability_guard.sh` verde. `bash -n install.sh` limpo.

**Reversao:** revert do commit. `provision_db.py` e arquivo novo; `install.sh` volta a so migrar.

### Fecho da Sessao #3: o contrato do projeto acompanha a ponte (2026-08-26)

**Operador pediu:** o R7 equivalente desta sessao — deixar a documentacao contando a verdade sobre o que mudou.

**Por que importa mais do que parece:** documentacao que descreve um sistema que nao existe mais **nao e ruido — mente com autoridade**. O `CLAUDE.md` e carregado a cada turno do agente; deixa-lo dizendo "memoria persistente no Supabase" faria o proprio Kobe raciocinar sobre um banco que ele nao usa mais.

**Foi feito:**

- **Backup datado do `CLAUDE.md`** em `.local/backups/`, feito **antes** de qualquer edicao (regra dura do operador). Foi o primeiro ato da sessao, junto da tag.
- **`CLAUDE.md`** — a secao de memoria persistente passou a dizer o que e verdade: **PostgreSQL acessado direto por `psycopg`**, sem PostgREST e sem adaptador; **onde o banco mora e configuracao, nao codigo** (uma linha `DATABASE_URL`); e `bot/db.py` e o unico arquivo que sabe qual banco e. Entraram as duas ferramentas de manutencao que passaram a existir, com o que cada uma garante: o runner (`status`/`up`/`baseline`, ordem, idempotencia, recusa de fora de ordem, drift, e **migration aplicada e imutavel**) e o portao de compatibilidade — com a **ordem fisica das colunas** nomeada, porque e a divergencia que nenhum diff por nome enxerga e a que quebra carga posicional em silencio.

**⚠️ A NOTA DE DEPLOY — o unico ponto desta sessao que exige leitura devagar.**

**Este merge nao se deploya sozinho: ele E o corte.** A partir do commit da ponte, o repositorio **nao sobe em producao** — a producao ainda fala com o banco antigo, e um `git pull` que traga isto sem uma `DATABASE_URL` valida faz o servico **nao subir**.

Isso e **por desenho, nao descuido**. Nao ha como ter a ponte direta e manter os dois caminhos vivos ao mesmo tempo sem um seletor de backend — que esta explicitamente banido desde 2026-07-25. O desenho do proprio operador ja respondia a isso, e e o que esta sendo seguido:

- **Rollback plano A** ("troca a connection string e reinicia") serve pra trocar de **alvo Postgres**. Ele **nao** leva de volta ao banco antigo: isso exigiria a senha do banco daquele servico, que e coisa diferente do token de administracao que existe. Registrado pra nao virar surpresa no pior momento.
- **Rollback plano B** e o caminho de volta de verdade, e funciona **sem senha nenhuma**: a tag **`pre-postgres-cutover`** foi criada **antes de o repositorio mudar**, apontando pro ultimo commit da era anterior. `git checkout` nela + reinicio devolve o Kobe como estava.
- **O corte leva codigo e dado juntos.** Mensagem gravada no banco novo depois do corte fica orfa se alguem voltar pela tag. Isso e assunto do bloco de corte, nao desta sessao — mas fica dito.
- **O Apolo corta junto.** Ele e repositorio separado, monta o proprio cliente, e le e escreve a **mesma tabela `contacts`**. Se um cortar e o outro nao, os dois escrevem em lugares diferentes **sem erro em log nenhum**. A branch dele esta pronta e nao foi mergeada nem publicada.

**Testes:** portoes finais, todos verdes — suite do Kobe **527 passando + 49 pulados** (sem banco) e **576 passando** (com banco de integracao); `tests/portability_guard.sh` verde; suite do plugin Apolo **35**; suite do plugin Coder **28**; e `infra/compat_gate.py` verde contra os bancos construidos pelo runner.

O portao contra o `kobe_dev` reporta **10 divergencias legitimas** — ele esta atras da migration `005`, exatamente como a producao. Isso e o portao **acertando**, e esta registrado como resultado, nao maquiado como verde.

**Reversao:** revert do commit devolve o texto anterior; o backup datado em `.local/backups/` guarda a versao original independente do git.

### `migrate.py baseline` — o buraco que so apareceu no portao final (2026-08-26)

**Operador pediu:** nada disto explicitamente — apareceu rodando os portoes finais da Sessao #3, e o silencio sobre ele custaria dado.

**O que eu vi.** Rodando `migrate.py status` contra o `kobe_dev` — que existe, tem o schema e esta carregado com a copia da producao — o runner respondeu **"6 conhecidas, 0 aplicadas, 6 pendentes"**. Correto pela logica dele: o `kobe_dev` foi montado a partir de um dump, nunca passou pelo runner, e portanto **nao tem a tabela de controle**.

**Por que isso e perigoso, e nao so estranho.** Quem visse esse `status` e rodasse `up` pra "acertar o registro" aplicaria a historia inteira. As migrations idempotentes atravessariam sem estrago — mas a **`005` apagaria de verdade** `conversations`, `conversation_tags` e as colunas do Chat Manager. A pessoa que so queria carimbar a versao veria dado sumir, sem ter pedido nada disso. **E o cenario chega em breve**: o banco local de producao vai nascer de uma carga de dados, nao do runner. Ele nasceria exatamente neste estado.

**Foi feito:** `migrate.py baseline --through <versao>` — **registra** versoes como aplicadas **sem executar nenhuma delas**. Diz ao runner "este banco ja esta neste ponto", e dali em diante o `up` so aplica o que veio depois. Duas travas, e as duas sao o ponto:

- **Recusa se o banco ja tiver registro.** Carimbar por cima de um historico existente esconderia exatamente a divergencia que o runner existe pra mostrar.
- **`--through` e obrigatorio.** Sem ele, o default natural seria "marca tudo" — e "tudo" inclui as destrutivas, que passariam a **nunca** rodar naquele banco, em silencio. Quem chama tem que dizer ate onde.

Com `--dry-run`, imprime o que marcaria **e o que continuaria pendente** — porque a pergunta que a pessoa realmente tem nessa hora e *"o que ainda vai rodar de verdade depois disso?"*.

**Conferido contra o caso real:** `baseline --through 004 --dry-run` no `kobe_dev` lista `000`..`004` pra marcar e mostra a **`005` seguindo pendente**, que e a resposta certa — o `kobe_dev` esta mesmo atras da aposentadoria do Chat Manager, e o `DROP` dela nao e desta sessao.

**Testes:** 3 novos (25 no arquivo). Um exige `--through`, um recusa versao inexistente sem sequer conectar, e um — ao vivo — prova a recusa em banco que ja tem historico.

Suite verde — **527 passando + 49 pulados**; com banco, **576 passando**. `tests/portability_guard.sh` verde.

**Reversao:** revert do commit. `baseline` e subcomando novo; `status` e `up` nao mudaram.

### O instalador provisiona Postgres e aplica o schema sozinho (2026-08-26)

**Operador pediu:** o default declarado no §6.2 do plano — *"o minimo coerente"* —, aprovado.

**Por que nao dava pra deixar quieto.** Depois da troca de driver, o `install.sh` pedia URL e chave do Supabase, gravava `SUPABASE_URL`/`SUPABASE_KEY` no `.env`, e mandava a pessoa **colar `schema.sql` num painel web**. Ou seja: provisionaria um banco que o runtime **nao le**, e produziria um `.env` que faz o bot **nao subir** — com a mensagem de erro guiada de `bot/config.py` como unico sinal. Um instalador assim nao e "fora de escopo": e defeito embarcado.

**Foi feito — o minimo coerente, nada de UX de instalador:**

- **Pergunta `DATABASE_URL`** em vez de URL + chave, mostrando as duas formas (socket unix sem senha, e TCP com usuario e senha).
- **Aplica o schema chamando `infra/migrate.py up`**, em vez do copiar-e-colar. Isso troca *"cole isto no painel e me avise quando terminar"* por uma operacao que o instalador **executa e verifica** — e que sabe em que versao o banco esta. Numa instalacao ja em dia, e no-op.
- **A deteccao "o schema ja foi aplicado?" sumiu, e some porque virou desnecessaria.** Ela era um `curl` no PostgREST checando se `topics` respondia 200 — heuristica que inferia o todo a partir de uma tabela. O runner tem tabela de versao: ele **sabe** o que falta, em vez de adivinhar.
- **Roda o portao de compatibilidade (T4) depois**, e **avisa sem bloquear**. A distincao e deliberada: um banco criado com collation diferente da referencia funciona — so ordena texto de outro jeito. Barrar a instalacao por isso seria remedio pior que a doenca; nao avisar seria deixar a pessoa descobrir daqui a seis meses que a lista de contatos ordena diferente da do vizinho.
- **Quando o runner falha, o instalador nao morre:** imprime as causas comuns (banco inexistente, usuario sem DDL, `pgvector` nao instalado), diz o comando exato pra retomar, e segue. A pessoa fica com um Kobe instalado e um passo pendente, em vez de sem Kobe.
- **Entrou um `warn()`** ao lado de `log()` e `err()`. `err()` **encerra o script**, e nenhum destes casos justifica abortar a instalacao inteira por algo que se conserta em um comando.
- **Os pre-requisitos anunciados no topo** deixaram de pedir conta em servico externo e passaram a pedir PostgreSQL 16+, as tres extensoes, e a string de conexao — com a nota de que **o instalador aplica o schema sozinho e a pessoa nao roda SQL a mao**.
- `README.md` e `uninstall.sh` acompanharam.

**Verificacao:** `bash -n` limpo nos dois scripts (o caminho interativo do instalador nao da pra exercitar sem uma instalacao de verdade — isto esta dito, nao maquiado). Nenhuma mencao ao servico antigo sobra em `install.sh`, `uninstall.sh` ou `README.md`.

Suite verde — **525 passando + 48 pulados**. `tests/portability_guard.sh` verde.

**Reversao:** revert do commit.

### O ultimo import do driver antigo sai, e `supabase` deixa o `requirements` (2026-08-26)

**Operador pediu:** o default declarado no §6.1 do plano — *"ou deleto, ou converto um script inerte"* —, aprovado.

**Por que nao dava pra deixar quieto.** `infra/decommission_whatsapp_acervo.py` mirava a tabela `whatsapp_messages`, que **nao existe mais** (saiu na `004`; conferido: `to_regclass('public.whatsapp_messages')` devolve vazio). Ele era codigo inerte — **e o ultimo arquivo do repositorio a importar o driver antigo**. Manter um script que nao roda, so pra prender uma dependencia que sai, e o pior dos dois mundos.

**Foi feito:**

- Removidos `infra/decommission_whatsapp_acervo.py` e `tests/test_decommission_whatsapp.py`.
- **`supabase>=2.0` sai de `bot/requirements.txt`.** Com isso o Kobe nao depende mais do driver antigo em lugar nenhum.
- `docs/runbooks/decomissionar-acervo-whatsapp.md` ganhou **banner de documento historico**, no mesmo espirito do que a Sessao #2 fez com os runbooks aposentados: diz que o procedimento ja foi cumprido, que o script nao existe mais, e que nada ali deve ser executado. Nao foi apagado de proposito — ele registra o raciocinio e as travas de seguranca de uma operacao que apagou dado de producao, e isso e o tipo de coisa que se quer poder reler daqui a um ano.

**Uma coisa que eu NAO fiz, e o motivo importa:** `infra/migrations/004_remove_whatsapp_messages.sql` cita o script no comentario dela, e agora aponta pra um arquivo que nao existe. **Deixei intacta.** Migration aplicada e imutavel — editar o arquivo faria o **runner construido nesta mesma sessao** acusar drift em qualquer banco que ja a rodou, e com razao: o banco teria o SQL antigo e o repo o novo. A ferramenta do Bloco 1 impediu, na pratica, uma edicao que pareceria arrumacao inofensiva. A explicacao ficou no banner do runbook.

**Verificacao:** alem da suite, um teste direto — importar `bot.db`, `bot.config`, `bot.topic_manager`, `bot.snapshot`, `bot.artifacts`, `bot.memory.working_set`, `bot.apolo_handlers`, `bot.telegram_handler`, `bot.resume`, `bot.compactor` e `bot.main` **com o pacote `supabase` bloqueado no `sys.meta_path`**. Os onze importam limpo. Nao e "removi as mencoes"; e "o runtime inteiro sobe sem o pacote existir".

Suite verde — **525 passando + 48 pulados** (539 antes; os 14 a menos sao os testes do script removido). `tests/portability_guard.sh` verde.

**Reversao:** revert do commit devolve script, teste e a linha do requirements.

### A prova: 46 testes de integracao contra Postgres de verdade (2026-08-26)

**Operador pediu:** implicitamente, pelo desenho da Sessao #3 — a conversao dos 45 pontos precisava de uma prova que a suite existente **nao dava**.

**Por que este arquivo e a prova, e nao um extra.** O resto da suite finge no nivel de **funcao**, nao de banco: os testes trocam `get_active_session`, `count_messages`, `get_recent_messages` por lambdas. Otimo pra velocidade e blast radius — e e exatamente o buraco desta migracao. **Reescrever o corpo de `get_recent_messages` em SQL nao e coberto por nada la.** Um `ORDER BY` invertido, um `eq` que virou `gte`, um `LIMIT` esquecido, um `RETURNING` faltando: tudo isso passa verde numa suite que substitui a funcao inteira por uma lambda. Os 538 verdes dos commits anteriores **nao provavam** o SQL; estes 46 provam.

**Foi feito:** `tests/test_db_integration.py`, **46 testes** cobrindo cada funcao convertida. Nao se testa "nao levantou excecao" — testa-se **semantica**:

- **topics:** idempotencia do `ensure_topic`; o nome automatico do raiz conforme o sinal do `chat_id` (`Private` x `General`); o `set_topic_name` devolvendo `None` ao criar e o nome **anterior** ao renomear (e o que dispara mover a pasta no filesystem); **o mesmo `thread_id` em chats diferentes gerando topicos distintos** — a razao de a UNIQUE ser composta; slug com acento e caixa; `RETURNING` no `set_topic_status`; e o filtro `status='active'` da lista de nao-onboardados.
- **Regressao direta do bug do `ON CONFLICT`:** um teste que cria topico **pelo nome** e falharia com `InvalidColumnReference` na forma antiga.
- **sessions:** `RETURNING` no archive (id na 1a vez, `None` na 2a); o resumo gravado quando vem e **preservado quando nao vem**; status invalido recusado; sessao nova nascendo depois de arquivar.
- **messages:** campos opcionais; contagem isolada por sessao; ordem cronologica crescente; o limite mantendo as **mais recentes**; a marca-d'agua **estritamente** posterior; e a leitura **por topico e nao por sessao** — de proposito, porque a sessao pode rotacionar entre o despacho de uma run de background e o momento de ela reler.
- **`awaiting_slash_response`:** `jsonb` de ida e volta, one-shot preservado, e o campo zerado **mesmo quando o estado ja venceu**.
- **artifacts:** tags gravadas, ausencia virando `NULL`, sessao vazia nao gravando nada, busca por titulo e conteudo sem diferenciar caixa, filtro por topico **excluindo o topico errado**, busca em branco nao varrendo a tabela, ordem e limite.
- **snapshot:** ciclo completo (grava, le, consome, some); sessao sem mensagem nao virando snapshot; a contagem do cleanup (que depende do `RETURNING`); e **o cleanup nao levando junto o que o operador salvou com `/salvar`** — sem o filtro por tag, levaria.
- **memoria imediata:** ordem, carimbo como texto, descarte do resumo de sessao legado.

**Dois erros meus, achados pelos proprios testes, e o que cada um ensinou:**

1. **A primeira versao passou na primeira execucao e falhou na segunda**, com dez testes quebrando por dado acumulado da rodada anterior — eu tinha derivado um `(chat_id, thread_id)` do nome do teste e nao limpava nada. **Suite que so passa uma vez nao e suite.** O conserto foi **uma transacao por teste, sempre revertida**: os testes nao se enxergam, a suite e repetivel, e **nao ha um unico comando destrutivo no arquivo**. Conferido: tres execucoes seguidas, todas 46 verdes, e o banco fica com **0 linhas em toda tabela** depois.

2. **Com a transacao, cinco testes de ORDEM passaram a falhar** — e a causa e uma propriedade do schema que vale a pena ter escrita: `created_at` tem `DEFAULT now()`, e `now()` no Postgres e o carimbo de **inicio da transacao**, nao o relogio de parede. Dentro de uma transacao so, tres inserts recebem o **mesmo** `created_at`, os `ORDER BY` empatam, e um teste de ordem passa ou falha **por acaso**. Em producao isso nao acontece (cada insert e sua propria transacao, via `autocommit`). O conserto foi o teste **dizer qual e o instante de cada mensagem** em vez de torcer pela resolucao do relogio — o que, de quebra, deixou as asserções de ordem deterministas.

**Mais a rede que pega o ponto esquecido.** Um teste de **conformidade** varre `bot/` inteiro — incluindo os helpers de `bot/bin/`, que nao tem extensao `.py` e escapam de uma busca por `*.py` — atras de `.table(`, `.rpc(`, `create_client` ou import do driver antigo. A unica mencao tolerada e a de `bot/config.py`, que compoe a mensagem de erro guiada.

Ele **roda sempre, com ou sem banco**, e isso e deliberado: o "pular sem banco" mora na fixture `db`, nao num `pytestmark` de modulo. Com a marca no modulo, a rede so existiria na maquina de quem tem o banco de integracao montado. **Provado que pega:** plantei um `db.table("messages")` num arquivo de `bot/` e o teste ficou vermelho nomeando arquivo e linha.

**Testes:** sem banco, **539 passando + 48 pulados** (o de conformidade roda). Com `KOBE_TEST_DATABASE_URL`, **587 passando, 0 pulados**. `tests/portability_guard.sh` verde. `infra/compat_gate.py` verde contra o banco de integracao.

**Reversao:** revert do commit. So acrescenta arquivo de teste.

### Os 4 helpers de `bot/bin/` atravessam a ponte — inclusive o que nenhum grep achava (2026-08-26)

**Operador pediu:** a conversao da superficie do Kobe para a ponte direta (Sessao #3). Os helpers de `bot/bin/` sao a parte da superficie *"facil de esquecer porque nao sao importados por ninguem"*.

**Correcao ao inventario, ja registrada e agora executada:** sao **quatro**, nao cinco. `kobe-alerta` **nao toca o banco** — o unico hit era um comentario sobre o re-exec no venv, e `bot/alertas/` guarda tudo em YAML no filesystem. Ele so teve o comentario corrigido.

**Foi feito:**

- **`kobe-await-response` (3 pontos).** Resolve `chat_id+thread_id -> topic -> session ativa` e grava o pedido de resposta. Conexao curta, sem pool — e um processo de vida curta. O `jsonb` do estado vai com `Jsonb()` **explicito**: um `dict` cru nao tem adaptacao automatica no psycopg, e sem isso a gravacao falharia.
- **`kobe-recall-since`** e **`kobe-reflect`.** Ambos montavam cliente proprio; agora usam a ponte (`KobeDB`). Os dois ganharam `close()` num `finally` — sao processos de vida curta, e sem isso a conexao ficaria pendurada no servidor ate o interpretador desmontar. O marcador de re-exec no venv do `kobe-reflect` mudou de `httpx` (que hoje vem junto de outras deps) para `psycopg`, que e o marcador estrito.
- **`_kobe_topic.py` — o que nenhum grep de `.table()` acha.** Ele falava **PostgREST cru por `urllib`**, montando `{SUPABASE_URL}/rest/v1/topics?select=...` na mao. E o resolvedor de `--topic` do `kobe-notify` e do `kobe-attach`. Se ficasse pra tras, o `--topic` das salas destacadas morreria **em silencio**, porque o caminho e tardio e so dispara quando alguem usa a flag.

**O cuidado que o `_kobe_topic` exigiu.** Ele era **stdlib puro de proposito**: os helpers rodam como subprocess de `claude -p` sob qualquer python3, nao necessariamente o do venv. Consultar por HTTP dava pra fazer com `urllib`; falar Postgres exige `psycopg`, que so existe no venv. A saida foi um **re-exec TARDIO**, dentro de `resolve_topic`:

- o caminho COMUM (`kobe-notify "texto"`, endereçado por env) segue **stdlib puro** e nao encosta em banco nenhum — nada mudou pra ele;
- so o caminho `--topic` re-executa sob o python do venv, e **so quando `psycopg` faltar**;
- re-executar ali e seguro porque `resolve_topic` roda **antes** de o helper enviar qualquer coisa: reiniciar o processo nao duplica mensagem nem anexo;
- **sem venv pra onde ir, o erro ENSINA** em vez de estourar um `ModuleNotFoundError` cru vindo de dentro de uma funcao de resolucao de topico — quem chamou `--topic` nao faria essa ligacao sozinho.

**Um bug encontrado ao provar o re-exec:** `os.execv` substitui o processo, e o que estiver em buffer de saida **some**. Fora de um terminal a saida e bufferizada por bloco, entao isso nao e hipotese — na primeira tentativa de prova as linhas impressas antes do re-exec sumiram, e foi assim que o problema apareceu. Entrou `flush()` de `stdout` e `stderr` antes do `execv`.

**Verificacao ponta a ponta, contra Postgres de verdade:**

- `kobe-await-response` gravou o estado, `pop_awaiting_slash_response` devolveu o plugin certo e a segunda chamada devolveu `None` (one-shot preservado); com `chat_id` inexistente saiu `rc=1` com mensagem clara, sem estourar;
- `resolve_topic` casou por nome exato, por slug e sem diferenciar maiuscula, e devolveu `LookupError` legivel pro inexistente;
- **contra o `kobe_dev` real** (7 topicos), `Dev Kobe`/`dev-kobe`/`DEV KOBE` resolveram todos para `(-…900, 475)`;
- o **re-exec foi provado numa arvore de mentira** com um venv pelado: invocado por um python sem `psycopg`, o processo reapareceu sob o python do venv e resolveu;
- e o `kobe-notify --topic "Dev Kobe"` **enviou de verdade**, com o destino resolvido pelo Postgres.

Suite verde — **538 passando + 3 pulados**. `tests/portability_guard.sh` verde.

De quebra, comentarios e docstrings que descreviam o driver antigo foram corrigidos em `bot/main.py`, `bot/telegram_handler.py`, `bot/turn_guarantee.py`, `bot/memory/working_set.py`, `kobe-notify`, `kobe-attach`, `kobe-dispatch` e `kobe-alerta`. Documentacao que descreve um sistema que nao existe mais nao e ruido: **mente com autoridade**.

**Reversao:** revert do commit.

### As anotacoes de tipo deixam de importar o driver antigo (2026-08-26)

**Operador pediu:** a conversao da superficie para a ponte direta (Sessao #3).

**Por que este commit existe separado:** `bot/compactor.py`, `bot/resume.py` e `bot/telegram_handler.py` **nunca falaram com o banco** — nenhum dos tres tem um ponto de consulta. Eles so importavam `Client` do pacote `supabase` para **anotar o tipo** do parametro `db`, que recebem e repassam. Fica num commit proprio justamente porque nao e conversao: e um import que ficaria pendurado num pacote que sai do `requirements`, e o diff nao deve se misturar com o dos arquivos que realmente mudaram de comportamento.

**Foi feito:** `from supabase import Client` virou `from bot.db import KobeDB`, e `db: Client` virou `db: KobeDB` nos tres. Um comentario em `telegram_handler.py` que explicava a seguranca de thread do driver antigo (*"supabase-py usa httpx.Client"*) foi atualizado pro que vale agora: a ponte usa um pool do `psycopg_pool`, tambem seguro entre threads — o que importa porque o Kobe fala com o banco de dentro de `asyncio.to_thread`.

**Com isto, `bot/` (fora dos helpers de `bot/bin/`) nao tem mais nenhuma mencao ao driver antigo.** As unicas ocorrencias que sobram sao **deliberadas**: as tres linhas de `bot/config.py` que compoem a mensagem de erro guiada, dita a quem ainda tem `SUPABASE_URL` no `.env` e nao tem `DATABASE_URL`.

**Testes:** suite verde — **538 passando + 3 pulados**. `tests/portability_guard.sh` verde. Como sao so anotacoes, a propria suite (que exercita os tres arquivos de verdade) e a verificacao adequada aqui.

**Reversao:** revert do commit.

### `artifacts.py`, `memory/working_set.py` e `apolo_handlers.py`: os 4 pontos restantes do nucleo (2026-08-26)

**Operador pediu:** a conversao da superficie do Kobe para a ponte direta (Sessao #3). Estes tres fecham os 33 pontos do nucleo.

**Foi feito:**

- **`bot/artifacts.py` (2).** O `.or_(f"title.ilike.{p},content.ilike.{p}")` virou `WHERE (title ILIKE %s OR content ILIKE %s)`. O insert nomeia as colunas e usa `RETURNING id`. `tags` vai como lista Python quando ha tags e como `NULL` quando nao ha — conferido no round-trip.
- **`bot/memory/working_set.py` (1).** A janela imediata de memoria. **E o consumidor que motivou o contrato de tipos da ponte:** ele filtra com `created_at >= cutoff` onde `cutoff` e string, e com `datetime` cru levantaria `TypeError`. Exercitado de verdade — as tres mensagens sairam na ordem cronologica certa.
- **`bot/apolo_handlers.py` (1).** O `/contatos`. Os filtros opcionais (`tipo`, `oculto`) montam o `WHERE` dinamicamente, com os valores ligados. Ficou uma **nota de ambiente no codigo**: este `ORDER BY nome_canonico` e o caso vivo que torna a collation do banco visivel pro operador — `C.UTF-8` ordena por byte cru e joga acento e maiuscula pra lugar diferente de `en_US.UTF-8`. E o T4 que vigia isso, e agora o codigo diz onde olhar.

**Uma decisao de nao-mexer, registrada em vez de tomada em silencio.** Ao traduzir a busca de artefatos eu escrevi o escape de `%` e `_` do LIKE — sem ele, um `%` digitado pelo operador vira curinga e o `/retomar` traz tudo. **Desfiz.** O comportamento antigo era exatamente esse, e trocar o conjunto de resultados de um comando que o operador usa **nao e assunto de uma migracao de driver**: seria mudanca de comportamento nao autorizada, decidida por mim, de madrugada, sem ele. A melhoria e defensavel e ficou **escrita no docstring** pra quem for decidir depois. Migracao de driver troca o transporte, nao a semantica.

**Verificacao contra Postgres de verdade:**

- `save_artifact_from_messages` com tags, sem tags, e com sessao vazia (devolve `None`, nao grava);
- `search_artifacts` achando por `content` e por `title`, case-insensitive, filtrado por topico (2 no topico certo, 0 num id inexistente), e devolvendo `[]` com entrada em branco;
- busca com virgula na entrada continua se comportando como antes;
- `get_immediate_messages` devolvendo as mensagens em ordem cronologica — **a prova pratica do contrato de tipos**;
- round-trip de `text[]`: `['teste','kobe']` volta lista, e ausencia volta `None`;
- as **4 combinacoes** de filtro do `/contatos` montadas e rodadas.

Suite verde — **538 passando + 3 pulados**. `tests/portability_guard.sh` verde.

**Reversao:** revert do commit. Tres arquivos, sem estado externo.

### `bot/snapshot.py`: os 7 pontos viram SQL (2026-08-26)

**Operador pediu:** a conversao da superficie do Kobe para a ponte direta (Sessao #3).

**Por que importa:** `snapshot.py` e o que faz o Kobe "voltar sabendo" depois de um restart — grava as ultimas mensagens das sessoes ativas antes de desligar e as reapresenta ao operador no boot. Ele falha de propriedade: qualquer excecao e logada e engolida pra nao derrubar o shutdown. **Isso e util em producao e perigoso numa migracao** — SQL errado aqui nao aparece como erro, aparece como o Kobe silenciosamente parar de lembrar.

**Foi feito:** as 7 cadeias viraram SQL. Dois pontos que precisaram de cuidado:

- **`.contains("tags", [SNAPSHOT_TAG])`** virou `tags @> %s`, o operador de continencia de array do Postgres — que e exatamente o que o PostgREST traduzia por baixo. O valor segue ligado como lista Python; `text[]` vai e volta nativo pelo psycopg.
- **`cleanup_expired_snapshots` precisou de `RETURNING id`, e isso nao e decorativo.** A funcao devolve a CONTAGEM de snapshots limpos, que antes vinha do `res.data` do delete do PostgREST. Um `DELETE` sem `RETURNING` nao devolve linha nenhuma — a contagem seria **sempre zero**, e a unica coisa a acusar seria um log dizendo "limpei 0" pra sempre. Provado no exercicio abaixo.

**Verificacao contra Postgres de verdade** (a suite nao cobre este arquivo no nivel de SQL):

- `save_pending_snapshots` gravou 1 snapshot de uma sessao ativa;
- `load_pending_snapshots` releu o payload e as mensagens saíram na ordem certa;
- `render_resume_message` montou a mensagem de retomada a partir do payload relido;
- `cleanup_expired_snapshots` devolveu **0** com nada vencido e **1** com o limiar forcado pro passado — que e a prova de que o `RETURNING` conta de verdade;
- `drop_snapshot` removeu e a lista de pendentes caiu a zero.

Suite verde — **538 passando + 3 pulados**. `tests/portability_guard.sh` verde.

**Reversao:** revert do commit. Arquivo autocontido.

### `bot/topic_manager.py`: os 22 pontos viram SQL — e um bug latente aparece (2026-08-26)

**Operador pediu:** a conversao da superficie do Kobe para a ponte direta (Sessao #3). `topic_manager.py` e o maior bloco: **22 dos 45 pontos vivos**.

**Por que este arquivo primeiro:** ele concentra quase metade da superficie e e o unico caminho por onde topic, session e message nascem. Converte-lo sozinho, num commit so, mantem o diff auditavel linha a linha contra as cadeias originais.

**Foi feito:** as 22 cadeias `.table().select().eq()` viraram SQL com parametros ligados. Mapeamentos nao-obvios, todos conferidos contra o comportamento original:

- `.update(...)` cujo chamador lia `res.data[0]["id"]` virou `UPDATE ... RETURNING id` — sem o `RETURNING`, `set_topic_status` e `archive_active_session` devolveriam `None` sempre e o Kobe passaria a achar que nunca ha sessao pra arquivar.
- `.select("id", count="exact")` virou `SELECT count(*)`. A forma antiga trazia uma linha inteira so pra ler um contador do cabecalho da resposta.
- `.is_("welcomed_at","null").not_.is_("telegram_chat_id","null")` virou `WHERE welcomed_at IS NULL AND telegram_chat_id IS NOT NULL`.
- Em `archive_active_session`, `summary` so entra no `SET` quando foi passado — um `None` explicito apagaria um resumo ja gravado, e o contrato ali e "nao mexe se nao veio". O `SET` e montado dinamicamente; os valores seguem ligados.

**O achado: um bug latente vivo em producao, encontrado ao traduzir.** `set_topic_name` fazia `.upsert(..., on_conflict="telegram_thread_id")` — apontando para uma UNIQUE em `telegram_thread_id` sozinha. Essa constraint **nao existe**: `infra/schema.sql` a **remove explicitamente** (`ALTER TABLE topics DROP CONSTRAINT topics_telegram_thread_id_key`) e a substitui pela composta `(telegram_chat_id, telegram_thread_id)` — justamente para separar o chat privado do "Geral" do supergrupo, que colidiriam com `thread_id=0`.

Conferido no banco, nao deduzido — as duas formas rodadas dentro de uma transacao revertida:

```
ON CONFLICT (telegram_thread_id)                    -> InvalidColumnReference:
    there is no unique or exclusion constraint matching the ON CONFLICT specification
ON CONFLICT (telegram_chat_id, telegram_thread_id)  -> OK
```

O caminho e raro (so dispara num `forum_topic_created`/`forum_topic_edited` de um topico que o bot **nunca viu**; qualquer mensagem anterior ja teria criado a linha por `ensure_topic`), e e por isso que ele nunca acusou. **Nao houve escolha de corrigir ou nao:** transcrever fielmente produziria SQL que levanta excecao. A traducao usa a UNIQUE que existe.

**Verificacao — e a distincao que importa aqui.** A suite ficou verde, e isso **nao prova nada** sobre este commit: ela finge no nivel de FUNCAO (`get_active_session`, `count_messages` sao trocados por lambdas nos testes), entao SQL errado passaria verde. A prova real foi exercitar **as 22 funcoes contra Postgres de verdade**, conferindo semantica e nao so ausencia de excecao:

- `ensure_topic` idempotente (segunda chamada devolve o mesmo id);
- `set_topic_name` devolve `None` ao criar e o nome **anterior** ao renomear — que e o que o caller usa pra detectar rename real e mover a pasta no filesystem;
- `list_unwelcomed_topics` cai de 1 para 0 depois do `mark_welcomed`;
- `get_recent_messages` sai em ordem cronologica crescente (a consulta e decrescente e a lista e revertida);
- `get_messages_since` com marca no futuro devolve vazio;
- `archive_active_session` devolve o id na primeira vez e `None` na segunda;
- `count_messages` bate.

Suite verde — **538 passando + 3 pulados**. `tests/portability_guard.sh` verde.

**Reversao:** revert do commit. O arquivo e autocontido; nada fora dele muda.

### A ponte pro Postgres: `bot/db.py` fala psycopg direto, e `DATABASE_URL` substitui o Supabase (2026-08-26)

**Operador pediu:** o coracao da Sessao #3 — *"Ponte DIRETA psycopg. Sem adaptador, sem DAL, sem backend plugavel"*, e *"Conexao por CONFIGURACAO, uma linha de `.env`"*. Decisao batida em 2026-07-25: *"nao faz sentido colocar um codigo na frente do outro codigo se eu posso ir direto pra ele."*

**Por que os dois juntos num commit so:** eles sao mutuamente dependentes. `bot/db.py` precisa de `config.database_url`, e `database_url` nao teria consumidor sem a ponte. Separa-los deixaria um commit intermediario que nao roda — o oposto de "suite verde a cada passo".

**Foi feito:**

- **`bot/db.py` reescrito.** Quatro verbos, `(sql, params)` em todos: `query` (varias linhas), `one` (a primeira ou `None`), `scalar` (o primeiro valor) e `execute` (escrita; devolve o `RETURNING`, ou lista vazia). Pool `psycopg_pool` — o Kobe fala com o banco de dentro de `asyncio.to_thread`, entao dois turnos batem ali ao mesmo tempo.
- **O proxy de cadeia MORREU, e essa e a prova de que ir direto foi a decisao certa.** A versao anterior tinha ~60 linhas que **gravavam a cadeia de chamadas** (`.table(...).select(...).eq(...)`) so pra poder remonta-la num cliente novo depois de reconectar — porque a consulta ja montada apontava pro pool morto. Com SQL, "a cadeia" e um par `(sql, params)`. **A ponte saiu menor que o involucro que substituiu.**
- **A classificacao leitura/escrita deixou de ser adivinhacao.** Antes, "isto e escrita?" era decidido farejando a cadeia atras de `insert`/`update`/`upsert`/`delete`. Agora e o **verbo que quem escreveu escolheu**. Ha teste provando a diferenca: um `SELECT` com a palavra *insert* dentro do texto continua sendo leitura.
- **`bot/config.py`:** entra `database_url` (obrigatoria), saem `supabase_url` e `supabase_key`. E um **leitor guiado**: se faltar `DATABASE_URL` mas o `.env` ainda tiver `SUPABASE_URL`, o erro nao diz "variavel ausente" — diz que este Kobe fala Postgres direto, aponta o `infra/migrate.py up`, e avisa que o caminho de volta ao Supabase e a tag `pre-postgres-cutover`, nao uma variavel. Uma instalacao nessa situacao nao esta com uma chave faltando; esta com o `.env` de antes da migracao, e o erro tem que dizer isso.
- **`.env.example`** documenta as duas formas (socket unix sem senha, e TCP com usuario/senha) e aponta o runner e o portao.

**O contrato de tipos — a parte que quebraria longe e em silencio.** O PostgREST devolvia JSON: `uuid` como string, `timestamptz` como texto ISO. O psycopg devolve `UUID` e `datetime`. Medido contra o `kobe_dev` antes de escrever uma linha. A quebra concreta: `bot/memory/working_set.py` filtra a janela imediata com `created_at >= cutoff` onde **`cutoff` e string** — `datetime >= str` levanta `TypeError` e a memoria imediata morre inteira, a seis arquivos de distancia da causa. Mesmo padrao em `bot/memory/aging.py`, `bot/claude_runner.py`, `bot/resume.py` e `bot/telegram_handler.py`. Entao a ponte normaliza **dois tipos, e so dois**: `UUID` vira `str`, `datetime` vira `.isoformat()`. `text[]` e `jsonb` ja chegam nativos e passam intactos.

**O fuso e fixado na conexao.** `timestamptz` guarda instante absoluto, mas o TEXTO que o driver devolve sai no fuso da sessao — e todo banco criado no cluster do Ubuntu nasce herdando o fuso local da maquina (achado do T4). Sem fixar, o mesmo instante sairia como `...T00:46:25-03:00` em vez de `...T03:46:25+00:00`. A ponte manda `-c TimeZone=UTC` na conexao e fica imune ao que estiver configurado no cluster ou no banco.

**Resiliencia:** o *contrato* de env e o mesmo — `DB_RESILIENCE_ENABLED`, `DB_RETRY_WRITES`, `DB_IDLE_RECYCLE_SECONDS` (este virou o `max_idle` do pool) — com a semantica que o operador aprovou. Erro de TRANSPORTE (`psycopg.OperationalError`) repete; erro de NEGOCIO nao. **Nota honesta, escrita no cabecalho do arquivo:** a dor original (3 sumicos em 30 dias por socket ocioso derrubado por intermediario) **praticamente desaparece** com socket unix local — nao ha intermediario. A camada fica por rigor e porque o banco pode morar noutra maquina amanha, nao porque a dor persista.

**`tests/test_turn_guarantee.py` migrou junto** (3 testes falhavam): ele levantava `httpx.RemoteProtocolError`/`httpx.ReadError` pra simular conexao caida. `bot/turn_guarantee.py` pergunta a `bot/db.py` se um erro e de transporte — e o desacoplamento aguentou a troca de driver sem mudar uma linha; so os testes precisaram falar a lingua do driver novo.

**Testes:** `tests/test_db_resilience.py` reescrito — **35 testes** (eram 11). Alem de toda a politica de repeticao herdada, agora ha o bloco que **pina o contrato de tipos**: o formato ISO exato, o fecho do circulo (o texto que a ponte devolve volta a ser `datetime` pelos parsers que ja existem), a comparacao string-a-string que motivou tudo, e a coincidencia entre ordem lexicografica e cronologica — de que `working_set` depende. Mais: `repr` nao vaza a conninfo (ela pode carregar senha), a conexao fixa UTC, e a conexao pede linhas como dicionario.

Fumaca contra o `kobe_dev` real: `id` saiu `str`, `created_at` saiu `'2026-08-26T03:46:25.275243+00:00'`, `tags` saiu lista, `count` deu 3443, consulta vazia deu `None`.

Suite verde — **538 passando + 3 pulados**. `tests/portability_guard.sh` verde.

⚠️ **A PARTIR DESTE COMMIT O REPOSITORIO NAO E DEPLOYAVEL EM PRODUCAO.** A producao ainda fala Supabase; um `git pull` que traga isto e nao traga uma `DATABASE_URL` valida faz o `kobe.service` nao subir. Isso e **por desenho** — nao ha como ter a ponte direta e manter os dois caminhos vivos sem um backend plugavel, que esta explicitamente banido. **Este merge nao se deploya sozinho: ele e o corte**, e o corte leva codigo e dado juntos. O caminho de volta e a tag `pre-postgres-cutover`, criada antes de o repo mudar.

**Reversao:** `git checkout pre-postgres-cutover` + reinicio devolve o Kobe falando Supabase, sem exigir senha de banco nenhuma.

### T4 — portao permanente de compatibilidade de ambiente (2026-08-26)

**Operador pediu:** o T4 do adendo de 26/08 — *"um teste (ou script de guarda, no espirito do `portability_guard.sh`) que compara o banco alvo contra a referencia e falha quando divergir"*, cobrindo collation/ctype, ordem fisica das colunas, `data_checksums`, encoding, `TimeZone`, versao do servidor e das extensoes, e schema-versionado x banco-real. *"Deve rodar sem exigir a producao no ar."*

**Por que:** tres divergencias de AMBIENTE entre o banco de dev e o de producao atravessaram 100% de uma suite de **456 testes sem acender nada**. Nenhuma e bug de codigo; todas fazem *"testei em dev"* mentir — e duas delas um diff por nome/tipo/nulo nao enxerga:

- **Collation.** O `initdb` do Ubuntu cria em `C.UTF-8` (ordena por byte cru); a producao esta em `en_US.UTF-8`. O dado e o mesmo; a **ordem de saida** de `ORDER BY <texto>` muda. Ha caso vivo: a lista de contatos e ordenada por nome.
- **Ordem FISICA das colunas.** Duas colunas de `topics` entraram por migration na producao (e foram parar no fim) e estao no meio no `infra/schema.sql`. Mesmo nome, mesmo tipo, posicao diferente. Nao afeta o Kobe (o codigo acessa por nome), mas **quebra carga posicional em silencio**, empurrando texto pra dentro de campo numerico.
- **`data_checksums`.** Ligado na producao, desligado por default no `initdb`.

**Foi feito:** tres pecas, mais um achado.

- **`infra/schema_fingerprint.py`** — introspecta um banco num JSON canonico e deterministico: propriedades do banco, extensoes com versao, e por tabela as colunas **com a posicao fisica** (`attnum` cru vai junto, porque buraco na numeracao e ele proprio um sinal), tipo, nulabilidade, default e collation de coluna; mais indices e restricoes. A tabela de controle do runner fica **de fora** de proposito — o `applied_at` dela faria a impressao digital mudar a cada aplicacao e o portao viraria ruido.
- **`tests/fixtures/schema_expected.json`** — a referencia versionada, **gerada de um banco de apoio erguido pelo proprio runner** a partir de `infra/schema.sql` + `infra/migrations/`. E isso que faz o item *"schema versionado x banco real"* ser verdade **por construcao**, e nao por promessa. E e isso que dispensa a producao no ar.
- **`infra/compat_gate.py`** — compara e falha nomeando a classe: `ambiente`, `extensao`, `tabela`, `coluna`, **`ordem-de-coluna`** (classe propria, com as duas ordens impressas e o aviso de que quebra calado), `indice`, `restricao` e `pgvector`.

**O achado: uma QUARTA armadilha da mesma familia, encontrada pelo portao na primeira execucao — `TimeZone`.** O cluster do Ubuntu fica no fuso local da maquina, e **todo banco criado nele nasce herdando esse fuso**, enquanto a producao esta em UTC. O valor guardado e o mesmo (timestamptz e absoluto), mas o **texto** que o driver devolve muda de `+00:00` pro deslocamento local — e o Kobe compara `created_at` como **string** em pelo menos um caminho (`bot/memory/working_set.py`). Medido: sob `America/Sao_Paulo` o mesmo instante sai como `2026-08-26T00:46:25-03:00`; sob UTC, `2026-08-26T03:46:25+00:00`. Consequencia adotada: os bancos de apoio foram fixados em UTC, e a ponte vai fixar o fuso da propria conexao — nao basta confiar no default do cluster.

**Sobre o item do `pgvector`, com a ressalva escrita no codigo:** *"o codigo usa recurso acima da 0.6"* **nao e introspectavel do banco** — a extensao nao expoe quais dos seus simbolos alguem chamou. O que existe e uma **lista de proibidos** dos identificadores que so passaram a existir da 0.7 em diante (`halfvec`, `sparsevec`, `binary_quantize`, opclasses novas...), varrida no SQL e no Python do repo, comparada contra a versao que a referencia fixa. Pega o caso realista; nao e prova. Esta dito assim no cabecalho da funcao.

**Testes:** `tests/test_compat_gate.py`, **39 testes**. Cada armadilha do §C tem um teste que a **injeta de proposito** e exige o portao vermelho — inclusive um que prova que a de ordem fisica e **invisivel** para um diff por nome/tipo/nulo (asseverando primeiro que os dois bancos sao identicos por esse criterio, e so entao que o portao acende). Ha teste tambem para o que NAO pode acender: `16.15 -> 16.16` e atualizacao de seguranca, nao classe de incompatibilidade — alarmar ali treinaria todo mundo a ignorar o portao. Os testes de logica usam fingerprint sintetico e rodam **sem banco nenhum**, porque "pulado" e verde por ausencia, que e o modo de falhar que o portao existe pra impedir.

**Prova ao vivo, com estrago injetado:** um banco criado exatamente como o `initdb` do Ubuntu criaria (`C.UTF-8`, herdando o fuso do cluster) e com uma coluna acrescentada no fim acendeu **4 divergencias de uma vez** — collation, ctype, `TimeZone` e coluna sobrando. E o portao rodado contra o `kobe_dev` real reportou **10 divergencias legitimas**: ele esta atras da migration `005` (ainda tem `conversations`, `conversation_tags`, `messages.embedding`, os dois `conversation_id` e seus indices e chaves estrangeiras). O `kobe_dev` **nao foi alterado** — ele e copia fiel da producao, que tambem nao rodou a `005`; o portao esta certo em acusa-lo, e a mensagem aponta pro `migrate.py status`.

Suite verde — **514 passando + 3 pulados** (456 + 22 + 39, com os 3 ao vivo pulando sem env de banco). Com `KOBE_TEST_DATABASE_URL` setada: **517 passando**. `tests/portability_guard.sh` verde; a referencia versionada nao carrega caminho absoluto nenhum.

**Reversao:** revert do commit. So acrescenta arquivos; nada aqui e importado pelo runtime do bot.

### Runner de migrations versionado (2026-08-26)

**Operador pediu:** dentro da Sessao #3 — *"Runner de migrations versionado e teu escopo. Hoje `infra/migrations/` sao 5 SQLs soltos (001..005), sem runner e sem tabela de versao. Construa: tabela de controle, aplicacao idempotente, ordem deterministica, e recusa de aplicar fora de ordem."*

**Por que:** ate aqui "aplicar o schema" era copiar `infra/schema.sql` e colar num painel web, e as migrations eram arquivos soltos. Ninguem — nem o codigo, nem o operador — conseguia responder *"em que versao este banco esta?"* sem inspecionar tabela por tabela. Com a ponte pro Postgres direto, essa pergunta passa a precisar de resposta mecanica: o instalador, o ambiente de dev e o corte dependem dela.

**Foi feito:** `infra/migrate.py`, com quatro garantias e uma ausencia deliberada.

- **Tabela de controle** `schema_migrations(version, filename, checksum, applied_at)`.
- **Ordem deterministica** pelo prefixo numerico, nunca pela ordem alfabetica do filesystem — que poria `010` antes de `002` no dia em que o projeto passar de 9 migrations. `infra/schema.sql` e sempre a versao `000`, o alicerce.
- **Idempotencia:** o que ja consta no controle e pulado; `up` duas vezes seguidas nao faz nada na segunda.
- **Recusa de aplicar fora de ordem:** pendente com numero menor que o maior aplicado faz o runner parar. E o caso de dois branches criarem `006` e `007`, o `007` entrar primeiro e o `006` chegar atrasado — aplica-lo produziria um banco que nenhuma outra instalacao tem.
- **Deteccao de drift:** checksum sha256 de cada arquivo aplicado fica gravado. Se o conteudo de uma migration ja aplicada mudar, o runner para — o banco tem o SQL antigo e o repo tem o novo, e ninguem mais sabe qual e a verdade. Migration aplicada e imutavel; correcao vira migration nova, pra frente.
- **Atomicidade:** o SQL da migration e o registro da versao vao na MESMA transacao. Nunca ha banco que aplicou sem registrar (rodaria de novo) nem que registrou sem aplicar (nunca rodaria).
- **Sem `down`, de proposito.** Reverter DDL por script e fonte classica de perda de dado silenciosa: o `down` de um `ADD COLUMN` apaga a coluna. O caminho de volta e o do resto do projeto — backup, ou migration nova pra frente.
- **Sem alvo default:** vem de `--database-url` ou `DATABASE_URL`. Apontar pro banco errado tem que custar um ato explicito.
- **Arquivo `.sql` sem prefixo numerico e versao duplicada sao ERRO**, nao arquivo ignorado — "ignorado em silencio" e como uma migration deixa de ser aplicada sem ninguem notar.

Conferido de quebra: as 5 migrations do repo sao idempotentes por construcao (todas guardadas por `IF EXISTS`/`IF NOT EXISTS` ou bloco `DO $$`), e nenhuma usa `CREATE INDEX CONCURRENTLY` — que nao rodaria dentro de transacao.

**Testes:** `tests/test_migrate.py`, **22 testes**. 20 de logica pura, sem banco nenhum (arvores sinteticas em `tmp_path`), cobrindo ordem numerica com o caso `010` × `002`, zeros a esquerda preservados, recusa de atrasada, drift, drift checado antes da ordem, prefixo ausente, versao duplicada e resolucao do alvo. 2 ao vivo contra `KOBE_TEST_DATABASE_URL`, pulados quando ela nao existe — asseveram idempotencia real e que **um banco novo nasce sem `conversations`/`conversation_tags`**.

Ponta a ponta em banco de verdade: o runner construiu `kobe_schemaref` do zero (6 versoes aplicadas em ordem), e a segunda execucao devolveu "nada a aplicar". Suite verde — **476 passando + 2 pulados** (456 + 22, dos quais os 2 ao vivo pulam sem a env). Com `KOBE_TEST_DATABASE_URL` setada: 478 passando. `tests/portability_guard.sh` verde.

**Reversao:** revert do commit. So acrescenta arquivos; o bot nao importa `infra/migrate.py` em lugar nenhum, entao o runtime nao muda.

### Ponte pro Postgres — dependencias do driver (2026-08-26)

**Operador pediu:** Sessao #3 do Projeto Novo Ambiente Kobe — a ponte pro Postgres (§2.9 do briefing V4 + o T4 do adendo de 26/08).

**Por que:** o Kobe fala com o banco por PostgREST sobre HTTP (cliente Supabase). A ponte direta em psycopg — decisao batida em 2026-07-25, *"nao faz sentido colocar um codigo na frente do outro codigo se eu posso ir direto pra ele"* — precisa do driver antes de qualquer outra coisa. Primeiro commit da sessao, deliberadamente sozinho e 100% aditivo: nada de runtime muda, o repo segue deployavel.

**Foi feito:**
- `psycopg[binary]>=3.2` e `psycopg_pool>=3.2` em `bot/requirements.txt`, instalados no venv de dev (resolveram para 3.3.4 e 3.3.1).
- `supabase>=2.0` **fica por enquanto** — sai so quando o ultimo import dele morrer, no fecho da sessao. Remover agora quebraria o runtime no meio do caminho.

**Testes:** suite verde — 456 passando. `tests/portability_guard.sh` verde. Import de `psycopg`/`psycopg_pool` conferido no venv de dev.

**Reversao:** revert do commit + `pip uninstall psycopg psycopg-binary psycopg_pool`. Nao ha estado fora do git.

### Os quatro comandos de memoria ganham teste (2026-08-25)

**Operador pediu:** implicitamente, pelo criterio de aceite da sessao — *"`/nova`, `/contexto`, `/salvar`, `/retomar` respondem — comportamento pre-Chat-Manager, **com teste**"*.

**Por que:** na conferencia final do aceite, o grep mostrou que os quatro **nao tinham teste nenhum**. Eles sempre foram do Kobe, nunca do Chat Manager — mas o Chat Manager tinha enxertado ramos condicionais em tres deles: `/nova` fechava a *conversation* junto e devolvia outro texto, `/contexto` imprimia um bloco de meta dela (ou o "sem conversa ativa ainda"), e `/retomar` sugeria `/conversa` quando nao achava artefato. Esses ramos sairam nesta sessao. Sem teste, a remocao poderia ter mudado o texto, a ordem ou o caminho de erro **sem nada acusar** — e so se descobriria pelo operador digitando o comando e recebendo coisa errada.

**Foi feito:** `tests/test_comandos_memoria.py`, **10 testes** que exercitam os quatro handlers de verdade (com dubles de mensagem e das folhas de banco) e travam:

- `/nova` arquiva a sessao e responde; sem sessao ativa, avisa que ja esta zerado.
- `/contexto` mostra a sessao ativa, a contagem e as ultimas mensagens — e **nao** imprime mais `Conversa:` nem `Sem conversa ativa`.
- `/salvar` consolida em artefato e cobra o titulo quando falta (nao foi tocado nesta sessao; o teste e rede contra regressao colateral).
- `/retomar` pede o termo e reporta "nao achei" **sem sugerir `/conversa` ou `/conversas-global`**.
- Um teste de **conformidade** que le o corpo das quatro funcoes e falha se a palavra `conversation` reaparecer em qualquer uma — rede contra um ramo voltar por copy-paste.

A `Config` falsa e **derivada da dataclass real** (mesmo truque de `test_resume.py`), entao campo novo ou removido na producao nao passa despercebido aqui.

**Testes:** suite verde — **456 passando** (446 + 10). `tests/portability_guard.sh` verde.

**Reversao:** so acrescenta arquivo de teste; o revert o remove e nao toca em codigo de runtime.

### Documentacao da aposentadoria — e a varredura que achou o README mentindo (2026-08-25)

**Operador pediu:** o R7 — tirar a secao de Chat Manager do `CLAUDE.md`, registrar tudo no changelog, e varrer `docs/`.

**Por que importa mais do que parece:** documentacao que descreve um sistema morto nao e so ruido — ela mente com autoridade. Um runbook de junho com um `sed -i` escrevendo uma flag inexistente no `.env` de producao e uma armadilha, nao um arquivo velho.

**Foi feito:**

- **Backup do `CLAUDE.md` com timestamp** em `.local/backups/` **antes** de editar, com o md5 conferido contra o original (regra dura do operador).
- **`CLAUDE.md`** — a secao "Chat Manager — persistencia inteligente de conversa por assunto" (com mecanica, comandos e limitacoes) deu lugar a uma **nota historica** que diz o que existiu, o que morreu e — o que importa pro agente no dia a dia — **o que continua vivo**: `/nova`, `/contexto`, `/salvar` e `/retomar` nunca foram do Chat Manager, e a memoria de trabalho tambem nao era. Corrigidas de quebra duas coisas menores que a secao arrastava: a descricao de `/retomar` (dizia "busca semantica"; e ILIKE no titulo) e a mencao a uma camada de *conversation* acima das sessions.
- **`README.md` — aqui estava o pior achado, e ele NAO estava na lista do escopo.** O README, que e a cara publica do projeto, anunciava `/missao`, `/missao_status`, `/missao_abortar` e `/missao_lista` como **comandos vivos**, e dedicava uma secao inteira ao fan-out da v0.13, com painel e tudo. Seria a primeira coisa que um instalador novo leria — e nao funcionaria. A lista de comandos foi reescrita **conferida contra o menu real** (`_CORE_SLASH_COMMANDS` carregado do codigo, nao de memoria), a secao virou "Keyko — o daemon de despertar por gatilho", e ficou dito em nota que a `Source` de missoes morreu e **que nao se deve confundir com as salas de missao**.
- **`docs/mission-control.md`** — perdeu a "forma fan-out" inteira e virou o guia so das salas. O layout de estado passou a descrever `sala.json`/`workspace/`, com a nota de que os `estado.json` antigos **continuam no disco** (sao dado do operador; ninguem os apagou — so nao ha mais codigo que os leia).
- **`docs/runbooks/ligar-new-chat-manager.md`** e **`docs/runbooks/keyko-e-missoes.md`** ganharam **banner de obsolescencia no topo**, dizendo o que nao pode mais ser executado e por que. O primeiro e o mais perigoso (o `sed -i` no `.env` de producao); no segundo ficou marcado, com precisao, o que dele **continua valendo**: o daemon Keyko, o padrao de `Source` nova, e a secao final das salas.
- **`docs/chat-manager/*`** (calibracao e diagnostico do bug 1) ganharam banner de **documento historico**. Nao foram apagados de proposito: registram analise e raciocinio da epoca, que e o tipo de coisa que se quer ler daqui a um ano.
- Referencias soltas corrigidas em `docs/runbooks/despacho-turno-pesado.md` (a `OPENAI_API_KEY` nao e mais "a mesma do Chat Manager" — agora aponta pra `bot/openai_client.py`), `docs/spr/2026-06-01-performance.md` (o item "detector de conversa no caminho critico" fechou — **resolvido por remocao**) e no README do arnes de avaliacao em `infra/` (a lacuna de replicar a camada de conversa deixou de ser lacuna: nao ha mais o que replicar).

**Testes:** suite verde — **446 passando**, `tests/portability_guard.sh` verde. A lista de comandos do README foi **conferida contra o menu carregado do codigo**, nao contra memoria.

**Reversao:** so documentacao — o revert desta mudanca devolve os textos. O `CLAUDE.md` tem, alem disso, backup timestamped em `.local/backups/`.

### As estruturas do Chat Manager saem do schema — migration 005 escrita, NAO executada (2026-08-25)

**Operador pediu:** tirar do schema o que o Chat Manager deixou, e deixar o `DROP` **escrito e bloqueado**, para o Hal executar com ele ciente.

**Por que nao executo:** apagar tabela e apagar dado, e o banco e compartilhado dev/prod — rodar o DDL atinge a producao na hora. A sala escreve a migration; quem roda e o Hal, depois do dump conferido. Mesmo rito do decomissionamento do acervo de WhatsApp (migration `004`).

**Foi feito:**

- **`infra/schema.sql`** — saem a tabela `conversations` (+ indice de status e o ivfflat do centroide), a tabela `conversation_tags` (+ indice), as colunas `sessions.conversation_id` e `messages.conversation_id` (+ indices) e a coluna `messages.embedding` (+ ivfflat). No lugar do bloco entrou uma **nota de ausencia deliberada**, no mesmo padrao do que foi feito com `whatsapp_messages`: quem chegar depois le por que nao ha nada ali, em vez de achar que faltou.
- **Duas coisas do mesmo bloco NAO sairam, porque nao sao Chat Manager**, apesar de terem entrado junto com ele na migration `001`: a **UNIQUE composta `topics(telegram_chat_id, telegram_thread_id)`** — que e o que separa o chat privado do "Geral" do supergrupo, e o que vai permitir um segundo ambiente conviver no mesmo banco — e o **rename `Geral` → `Private`**. O cabecalho do bloco foi reescrito para dizer isso.
- **`saved_artifacts.embedding` FICA.** Tem 0 linhas nao-nulas hoje e e tentador levar junto, mas e gancho declarado do `/salvar`/`/retomar`, que continuam vivos. Esta escrito na migration para ninguem incluir por associacao.
- **Decisao explicita sobre `messages.embedding`: REMOVER.** O grep confirmou que so o Chat Manager escrevia e lia essa coluna, e o unico consumidor de leitura (`kobe-recall`) foi aposentado junto. Sem eles, sao ~726 vetores carregando um indice ivfflat sem ninguem para consultar. E o dado que mais doi nesta migration, e por isso esta isolado numa secao propria, com o aviso em cima.
- **`infra/migrations/005_remove_chat_manager.sql`** — DDL destrutivo com os **dois pre-requisitos escritos na cara** (dump conferido, com as tres contagens esperadas; e codigo novo ja no ar, senao o bot recria linha depois do drop), mais as queries de conferencia de antes e de depois. Ordem deliberada: colunas de vinculo primeiro (FK), depois `messages.embedding`, depois as tabelas — e `conversation_tags` antes de `conversations`, que e quem ela referencia.

**Testes:** suite verde — **446 passando**, portability verde. **E o schema foi exercitado de verdade, num Postgres limpo e descartavel** (container `pgvector/pgvector:pg18` com `--rm`, sem volume, sem porta publicada e com `--network none` — nao encostou em nenhuma stack rodando; removido ao fim, conferido por `docker ps -a`):

1. **`infra/schema.sql` novo num banco zerado:** aplica com `ON_ERROR_STOP=1`, sai limpo. As tabelas criadas sao `contacts`, `messages`, `saved_artifacts`, `sessions`, `topic_name_history`, `topics` — **nenhuma** `conversations`/`conversation_tags`. Zero colunas `conversation_id`/`centroid_embedding`, zero `messages.embedding`. E o que tinha de sobreviver sobreviveu: `saved_artifacts.embedding` e a UNIQUE de `topics`, ambos presentes.
2. **A migration `005` foi rodada de verdade**, sobre o schema ANTIGO (tirado do git) e com **dado semeado**: uma `conversation` com centroide, uma tag, uma `session` vinculada e uma `message` com vetor. Depois do DDL: 0 tabelas do Chat Manager, 0 colunas `conversation_id`, 0 `messages.embedding` — e a mensagem, a sessao e o topico **continuaram la**, junto com `saved_artifacts.embedding` e a UNIQUE. Ou seja: apaga o que deve e nao leva junto o que nao deve.

**Reversao:** o revert desta mudanca devolve o `schema.sql` anterior e remove o arquivo da migration. **Nada foi executado no banco do operador** — as tabelas continuam la, intactas, ate ele mandar rodar. O dump previo ja existe em `user-data/backups/chat-manager-2026-08-25/` (conversations, conversation_tags e messages), o que satisfaz o PRE-REQUISITO 1 quando ele for conferir.

### O Sistema de Missoes v0.13 e aposentado — as SALAS de missao ficam intactas (2026-08-25)

**Operador pediu:** aposentar o sistema antigo de Missoes. Palavra dele: *"e codigo velho, pode morrer"*. E, com enfase maxima, o inverso: *"tenha um extremo cuidado pra voce nao matar o sistema de missoes errado. O antigo pode matar se a gente nao ta usando. Mas o novo, por favor, nao me arrume problemas."*

**Por que a enfase.** Este repositorio tinha **dois** sistemas chamados "missao", dividindo o mesmo diretorio de estado (`user-data/missoes/`) e o mesmo pacote Python (`bot/mission_control/`):

| | Missoes v0.13 — MORREU | Salas de missao (Mission Control) — VIVE |
|---|---|---|
| Como se aciona | comandos `/missao`, `/missao_status`, `/missao_abortar`, `/missao_lista` | linguagem natural ("abre uma missao sobre X") — **sem slash command** |
| Estado | `estado.json` + `eventos.jsonl` | `sala.json` |
| Ultima atividade | 09/06/2026 | 24/08/2026 |

Os dois nunca se enxergaram (a listagem do v0.13 varria `*/estado.json`; as salas so gravam `sala.json`), mas **a separacao sempre foi por ARQUIVO, nunca por pasta**. Uma delecao pelo caminho da pasta, ou um "removi o modulo mission_control", mataria a ferramenta viva sem deixar um erro sequer no log. Por isso o corte foi feito arquivo a arquivo, a partir de um mapa levantado na fonte — nunca por grep de "missao"/"mission".

**Foi feito — saiu:**

- `bot/mission_control/{handlers,orquestrador,executor,painel,prompts,source,models}.py`.
- A camada `estado.json` de `storage.py`: `existe`, `carregar`, `salvar`, `mutar`, o log append-only (`append_evento`, `ler_eventos_a_partir`), `listar_missoes` e `find_missao_ativa`, mais os paths de log/output de subtarefa.
- **`bot/telegram_handler.py`: a triagem sincrona inteira** (`_triagem_missao_se_ativa` + a sentinela `_TRIAGEM_RESPONDEU`). Ela nao estava na lista do escopo e foi achada na reconferencia: com uma missao v0.13 ativa, ela **bloqueava o handler do topico por ate 90 segundos** chamando o orquestrador antes do agente.
- `missao_ativa_info` do prompt (`bot/claude_runner.py`) e da retomada (`bot/resume.py`); a `MissoesSource` do Keyko (`bot/keyko/registry.py`); os 4 comandos do menu e do roteamento (`bot/main.py`).

**Foi feito — ficou, e esta provado:**

- `sala_dispatch.py`, `sala_prompt.py`, `sala_worker.py`, `handoff.py` e `bot/sala/*`: **zero linha de diff**, verificado por `git diff --stat` contra a base do passo **e** por conferencia de md5 dos 9 arquivos.
- De `storage.py`, a camada de paths inteira, que e de onde as salas vivem: `missoes_root`, `missao_dir`, `path_sala_json/sysprompt/launcher/log`, `workspace_dir`, `ensure_workspace`, `gerar_id`, `_file_lock`, `_write_atomic`, `now_iso`.
- O docstring de `storage.py` e do `__init__.py` do pacote agora **carregam a nota historica** e a regra "a separacao aqui e por arquivo, nunca por pasta" — pra quem chegar depois nao repetir o risco.

**Testes:** suite verde — **446 passando**. `tests/portability_guard.sh` verde.

**Provas do gate bloqueante (execucao, nao leitura):**

1. `git diff --stat` dos arquivos de sala contra a base: **vazio**. Md5 dos 9: todos `OK`.
2. `test_mission_control_sala.py`, `test_sala_core.py`, `test_mission_control_handoff.py`: **27 passando**.
3. **Uma sala real foi aberta e encerrada DEPOIS da remocao.** O `abrir` devolveu `ok: true`, criou `sala.json` + `workspace/`, subiu a sessao tmux `mission-prova-vida-sala-depois-remocao-2-72618196` e o `sala.json` foi a `status: running` com PID real. O `encerrar` devolveu `ok: true, tmux_morta: true`, o `sala.json` foi a `status: encerrada` e a sessao tmux sumiu. Nenhum `estado.json` foi criado — como esperado, ja que o v0.13 nao existe mais.
   *(Na primeira tentativa o worker morreu com `ModuleNotFoundError: supabase`. Nao era regressao: `sala_dispatch:146` procura o interpretador em `<KOBE_HOME>/.venv`, e o teste apontava `KOBE_HOME` pra uma arvore sem venv. Corrigido o setup, a sala subiu. Fica registrado porque a distincao entre "quebrou" e "meu teste estava torto" e exatamente o que nao se deve varrer pra debaixo do tapete.)*
4. O dourado do prompt perdeu **uma unica linha** — `[Missao ativa: ...]`. A linha `[Sala de missao ativa neste topico: ...]` **continua la**, o que e a prova, no proprio artefato de teste, de que a sala sobreviveu ate no prompt.

O teste de conformidade da trava de canal desceu de 18 para 14 pontos gateados (os 4 comandos `/missao*`), com o historico escrito na docstring. As salas nunca apareceram nessa lista porque **nao tem handler de comando** — sao abertas por linguagem natural.

**Reversao:** o revert desta mudanca devolve os arquivos do v0.13 (estao na historia). Nenhum dado do operador foi tocado: `user-data/missoes/` segue intacto, com os `estado.json` antigos onde estavam.

### O codigo do Chat Manager e apagado (2026-08-25)

**Operador pediu:** o apagao em si, depois que o caminho vivo ja estava limpo.

**Por que:** com os consumidores removidos no passo anterior, os modulos ficaram sem nenhum importador. Codigo morto atras de flag desligada e pior que codigo removido: continua aparecendo em toda busca, em toda leitura e em todo raciocinio sobre arquitetura, cobrando atencao sem entregar nada.

**Foi feito - apagados:**

- `bot/chat_manager/` (o pacote inteiro: `__init__`, `activity`, `classifier`, `context`, `source`), `bot/chat_manager_commands.py`, `bot/conversation_detector.py`, `bot/embedding.py`.
- **`bot/bin/kobe-recall`** - nao estava na lista original do escopo; **entrou por decisao do operador nesta sessao**, na palavra dele: *"nao faz sentido ter um helper que nao vai ter codigo correspondente"*. Ele e 100% Chat Manager (le `conversations` e `messages.embedding`, importa `bot.embedding`) e viraria um helper quebrado no primeiro uso. **`bot/bin/kobe-recall-since` FICA** - apesar do nome parecido, e janela temporal sobre `messages`, sem nada de Chat Manager.
- `infra/calibrate_chat_manager.py` e `infra/migrate_sessions_to_conversations.py` (one-shot, ja rodou).
- `tests/test_chat_manager_classifier.py`, `tests/test_chat_manager_transition.py`, `tests/test_chat_manager_tail_flush.py`.

**Antes de apagar `bot/embedding.py`, o grep foi refeito** (o levantamento era de horas antes e o codigo tinha mudado): os unicos consumidores eram o proprio Chat Manager, os dois scripts orfaos e o `kobe-recall` - todos indo embora junto.

Os comentarios que citavam o pacote morto foram corrigidos em `bot/authz.py`, `bot/memory/__init__.py` e `bot/memory/working_set.py`, em vez de deixados apontando pra um endereco que nao existe.

**Testes:** suite verde - **447 passando** (462 menos os 15 dos tres arquivos de teste do Chat Manager). `tests/portability_guard.sh` verde. Smoke de import de `bot.main`, `bot.keyko.registry` e `bot.resume`. **Gate do escopo conferido:** o grep de `chat_manager` / `conversation_detector` / `CHAT_MANAGER_ENABLED` em `bot/`, `infra/` e `tests/` devolve **zero ocorrencia funcional** - o que sobra e nota historica em docstring, os literais do proprio teste de conformidade que impede o acoplamento voltar, e a migration `003` (que e historia e nao se reescreve).

**Reversao:** o revert desta mudanca devolve todos os arquivos - eles estao na historia do repositorio. Nada de banco foi tocado: as tabelas continuam la ate o Hal rodar a migration `005`.

### O Chat Manager sai do caminho vivo (2026-08-25)

**Operador pediu:** aposentar o Chat Manager. Palavra dele: *"vamos aposentar o Chat Manager… não quero um Frankenstein"*.

**Por quê:** a flag estava `false` em produção e o sistema não voltou a ser ligado. Manter um subsistema de ~2.800 linhas atrás de uma flag desligada custa em toda leitura do código e em toda decisão de arquitetura. Este commit tira o Chat Manager do **caminho de execução** — os arquivos ainda existem, e são apagados no commit seguinte. A ordem é deliberada: remover os consumidores antes dos módulos; o contrário quebraria import no meio do caminho.

**Foi feito:**

- **`bot/telegram_handler.py`** — saem o `touch_activity`, o `_load_chat_manager` (o `asyncio.gather` do turno volta de 3 para 2 vias) e os argumentos de prompt. **`/nova` e `/contexto` voltam ao comportamento pré-Chat-Manager**: a primeira arquiva a sessão e ponto, a segunda mostra a sessão e ponto. As duas dicas de `/conversa` no `/retomar` saem. **`/salvar` e `/retomar` não foram tocados** — não são do Chat Manager.
- **`bot/resume.py`** — sai o bloco de ponteiros da retomada; **`bot/claude_runner.py`** — saem os três blocos de prompt (residente, header de conversation e cronologia comprimida); **`bot/keyko/registry.py`** — sai o registro da `ClassifierSource`; **`bot/config.py`** e **`.env.example`** — sai `CHAT_MANAGER_ENABLED`.
- **`bot/topic_manager.py`** — saem as 4 funções de conversation. **Ficaram** `get_last_assistant_message_of_session` e a variante `_meta_`: nasceram para o detector e perderam o consumidor, mas não são acopladas a conversation nenhuma (leem `messages` direto) — a docstring de cada uma agora diz isso, em vez de citar um sistema que não existe mais.
- **`bot/main.py`** — 4 comandos fora do menu, 4 `CommandHandler` fora do roteamento, e o `MessageHandler` regex de `/retomar_<id>`. Esse regex era o **único** handler registrado entre os `CommandHandler` e o `filters.TEXT` genérico; ele sai inteiro, então a ordem relativa do que sobrou não muda.
- **`WORKING_MEMORY_ENABLED` fica, intacta.** A memória de trabalho foi desacoplada da flag de conversas na Frente 0 do Highlander — foi esse desacoplamento, feito meses antes, que permitiu a memória sobreviver inteira à aposentadoria.

**Testes:** suíte verde — **462 passando**. `tests/portability_guard.sh` verde. `tests/test_rajada_fifo.py` verde (7), que é o aceite do roteamento depois da remoção do regex. Smoke de import do `bot.main` e conferência do menu: os 4 comandos sumiram. **O fixture dourado do prompt foi regenerado e o diff, auditado linha a linha** — ele perde **exatamente** os três blocos do Chat Manager (`[bloco do chat manager]`, `[Conversation ativa: ...]`, `[Cronologia comprimida ...]`) e **nada mais se move**, o que é a prova de que a montagem do prompt não foi deslocada. O teste `test_a_contagem_de_pontos_gateados_nao_encolheu` desceu de 23 para 18 pontos (os 5 de `chat_manager_commands.py`), com o histórico e o motivo escritos na própria docstring — encolher em silêncio é o que aquele teste existe pra pegar.

**Reversão:** `git revert` deste commit. Os módulos do Chat Manager ainda existem neste ponto da história, então o revert é suficiente e não depende de nenhum outro.

### A fábrica do cliente OpenAI sai do detector de conversa (2026-08-25)

**Operador pediu:** o primeiro passo da Sessão #2 do "Projeto Novo Ambiente Kobe" — desacoplar, ANTES de aposentar o Chat Manager, a peça viva que mora dentro dele.

**Por quê:** `_get_openai()` (a fábrica singleton do client `AsyncOpenAI`) e a constante `JUDGE_MODEL` moravam em `bot/conversation_detector.py` por acidente de história — o judge do detector foi o primeiro consumidor de OpenAI do Kobe. Com o tempo, **duas funções que não têm nada a ver com Chat Manager** passaram a importar de lá, em import tardio dentro da função: o ack semântico da borda (`bot/liveness.py`, provider `openai`, que é o default e o que roda hoje) e o desempate de zona cinza do roteador de turno (`bot/turn_classifier.py`). Apagar o detector com elas apontando pra lá quebrava o ack em runtime (`ImportError` sobe) e degradava o classificador **em silêncio** — a chamada dele está dentro de um `except` que devolve `None`. **Nenhum teste da suíte anterior pegava isso**, porque os dois imports são tardios e um é engolido.

**Foi feito:**

- **`bot/openai_client.py`** — endereço neutro, sem dono: `JUDGE_MODEL`, `_openai_client`, `_get_openai()`. Mudança de endereço, zero lógica nova. O módulo não depende de nada do Kobe, só do `OPENAI_API_KEY` no ambiente.
- **`bot/liveness.py` e `bot/turn_classifier.py` repontados.** `bot/conversation_detector.py` e `bot/chat_manager/classifier.py` também — eles morrem depois, mas não podem ficar quebrados no meio do caminho.
- **`tests/test_openai_client.py`** — a trava que não existia. Cobre o caminho do ack com `provider=openai` (prova que sai o texto do modelo, não o fallback), o caminho da zona cinza (prova que volta **veredito**, não `None`), o singleton, o erro sem chave, e uma conformidade por grep que impede reintroduzir o import do detector.
- **Os 9 `mock.patch("bot.conversation_detector._get_openai")` de `tests/test_edge_liveness.py`** repontados pro endereço novo.

**Testes:** suíte inteira verde — **463 passando** (eram 457; +6 do arquivo novo). `tests/portability_guard.sh` verde. **E a trava foi provada, não prometida:** simulei a regressão na árvore (os dois consumidores voltando a importar do detector + o detector removido) e o arquivo novo **falhou em 4 testes**, incluindo os dois caminhos vivos. Restaurado em seguida; `git diff` conferido.

**Reversão:** este commit é revertível sozinho por construção — `git revert` dele devolve os imports antigos, e o detector ainda existe neste ponto da história.

### Camada de ambiente — o Kobe passa a saber em que ambiente roda (2026-08-25)

**Operador pediu:** a Sessão #1 do "Projeto Novo Ambiente Kobe" — a camada que permite o Kobe rodar em **dois ambientes independentes** na mesma máquina (bot, canal, configuração, memória durável e alcance de WhatsApp separados), **sem alterar em nada o comportamento da produção com o `.env` que ela tem hoje**.

**Por quê:** hoje não existe ambiente de desenvolvimento. São duas pastas de código, o **mesmo bot** e o **mesmo banco** — "testei em dev" significa rodar outro arquivo, no mesmo bot, escrevendo na base de verdade do operador. Antes de migrar banco ou aposentar qualquer coisa, o código precisa saber onde está.

**A invariante que governa a entrega inteira:** aditividade total. Nenhuma variável nova é obrigatória; com o `.env` de produção de hoje o comportamento é **idêntico** — e isso é provado com evidência (diff, teste, execução), não com argumento.

**Foi feito:**

- **Baseline do prompt (dourado).** `tests/fixtures/prompt_baseline_prod.txt` guarda o prompt que a produção monta hoje, gerado a partir do código de **antes** da camada existir, com todas as seções opcionais preenchidas e o relógio congelado. `tests/test_prompt_environment_banner.py` compara byte-a-byte. Não é asserção pontual de propósito: uma asserção do tipo "não começa com `[Ambiente]`" passaria mesmo se a mudança tivesse deslocado o meio do prompt; o dourado pega qualquer alteração, em qualquer seção.

- **`bot/environment.py`** — a noção de ambiente, num módulo só. `KOBE_ENV` aceita `prod` (default) e `dev`; valor desconhecido **derruba o start com mensagem**, nunca cai calado em produção. Fica separado de `bot/config.py` porque nem todo consumidor tem um `Config` na mão: `bot/bin/kobe-reflect` e o cliente do Hindsight rodam fora do processo do bot e leem daqui. `Config` ganhou o campo `environment`, e a falha é reembrulhada como `ConfigError` para o start morrer com "Configuração inválida: ..." em vez de um traceback cru.

- **O agente sabe que é dev.** Em `dev`, o prompt ganha uma primeira linha avisando o ambiente e proibindo deploy/publicação; em `prod`, **nenhum caractere é acrescentado** — o dourado prova. O aviso vem antes até da nota de handoff de background: é a moldura de tudo que vem depois, e um agente que descobre no rodapé que está em dev já leu o pedido inteiro achando que era produção. E `KOBE_ENV` passou a descer no env do subprocesso do `claude`, junto de `KOBE_CHAT_ID`/`KOBE_THREAD_ID`/`KOBE_TELEGRAM_BOT_TOKEN`, pra subagentes, helpers de `bot/bin/` e scripts de plugin não precisarem adivinhar onde estão.

- **Trava de canal opcional** (`TELEGRAM_ALLOWED_CHAT_IDS`). Vazia ou ausente → não filtra nada, que é como a produção roda hoje. Preenchida → mensagem de qualquer outro chat é ignorada **em silêncio**, não recusada com educação: responder confirma que o bot existe e está ali. A verificação foi para um módulo só (`bot/authz.py`) porque estava **copiada em quatro lugares** — `telegram_handler`, `alertas/handlers`, `chat_manager_commands`, `mission_control/handlers` — somando **23 pontos de chamada**. Quatro cópias de uma trava de segurança são quatro chances de ela falhar ABERTA, e um teste de conformidade agora varre `bot/` por grep para impedir a quinta.
- **A trava alcança também 8 pontos que nunca tiveram autorização nenhuma:** os quatro handlers de evento de fórum (que **escrevem no banco** o nome de tópico de qualquer chat onde o bot esteja) e os quatro comandos do Apolo. Só a dimensão de **canal**, nunca a de usuário — ver o achado abaixo.
- **Aviso no start.** Lista branca de canal preenchida emite WARNING dizendo quantos chats a instância atende. Se ela for setada em produção por engano, o bot fica mudo; um bot mudo com uma linha no journal se diagnostica em trinta segundos, um bot mudo em silêncio total, não.

**🔴 Achado de segurança registrado nesta entrega, e deliberadamente NÃO corrigido aqui.** Os comandos `/contatos_buscar`, `/contatos_listar`, `/contatos_promover` e `/whatsapp_grupos` (`bot/apolo_handlers.py`) **não verificam usuário** e nunca verificaram: hoje, qualquer pessoa num chat onde o bot esteja pode listar os contatos de WhatsApp do operador. Acrescentar a verificação **mudaria o comportamento da produção**, o que a invariante de aditividade desta sessão proíbe — e contaminaria a prova de não-regressão que é o produto principal dela. Receita do conserto, para a sessão própria: trocar a chamada de `authz.chat_allowed_for` por `authz.update_authorized` nos quatro handlers (o módulo e a função já existem), acrescentar teste de recusa por usuário e conferir se algum fluxo legítimo dependia da ausência da trava.

- **Bank do Hindsight prefixado por ambiente.** Em `dev`, `kobe-<slug>` vira `kobe-dev-<slug>`. É **cinto de segurança, não o isolamento** — o isolamento é de servidor (instância própria, abaixo). O prefixo cobre um erro de uma linha e plausível: o `.env` de dev vir com `HINDSIGHT_BASE_URL` ainda apontando para a produção. Sem ele, esse erro contamina a memória viva do operador em silêncio, porque o Hindsight aceita a escrita sem reclamar. Um teste assevera a propriedade toda de uma vez: nenhum bank de dev colide com nenhum de prod — inclusive no caso capcioso do tópico chamado `dev-kobe`, que em produção já vira `kobe-dev-kobe`.

- **Stack do Hindsight parametrizada por ambiente** — o item mais perigoso da entrega. Nome de projeto, containers, rede, volume e as duas portas publicadas eram literais; agora saem de variável, com **default idêntico ao literal de hoje**. A linha que o briefing não pediu e sem a qual isto seria uma armadilha é o `name:` do projeto: sem ela, o Compose deriva o nome da pasta, as duas stacks caem no mesmo projeto e nos mesmos nomes de serviço, e um `up` do dev **recriaria os containers da produção com a configuração do dev** — levando a memória durável junto.
  - `infra/hindsight/.env.dev.example` (sufixo `-dev`, portas 8890/9991, conferidas livres) e uma seção nova no README com a tabela das duas stacks, o passo a passo e a armadilha do `--env-file` dita na cara: sem ele, o alvo é a produção, o que é irrelevante num `ps` e catastrófico num `down -v`.
  - **A stack de dev NÃO foi subida** — isso é operação, e é do Hal.

- **Verificador de paridade de `.env`** (`infra/env_parity.py`), que compara os dois ambientes **pelos nomes das chaves e nunca pelos valores**. A regra governa o desenho inteiro, não é cuidado ao imprimir: o parser **descarta o lado direito no ato da leitura**, então o valor não chega a existir como variável nomeada em lugar nenhum do arquivo — não há o que vazar. Rodável à mão (sai `1` em qualquer divergência) e chamável no start como AVISO, atrás de `KOBE_ENV_PARITY_REFERENCE`; sem essa variável, nem o código de paridade é tocado, e ele **nunca** derruba o start.

- **Unidades systemd e scripts de ciclo de vida do ambiente de dev.** `infra/kobe-dev.service.template` e `infra/keyko-dev.service.template` trazem `Environment=KOBE_ENV=dev` **antes** do `EnvironmentFile`: no systemd quem vem depois vence, então o `.env` ainda pode sobrescrever se houver motivo, mas um `.env` de dev incompleto não faz mais o bot de desenvolvimento se comportar como produção. **Não** existe `apolo-webhook-dev.service` — dev não recebe WhatsApp, e um webhook de dev competiria com o de produção pelo mesmo evento da Evolution, que tem um chip só.
- `infra/dev-up.sh` e `infra/dev-down.sh` sobem e descem o ambiente inteiro (bot, despertador e a stack Hindsight de dev). O `dev-down` **confere o alvo antes de agir** — lê a primeira linha do `docker compose config` e aborta se não for `name: hindsight-dev` —, e nunca usa `-v`: derrubar o ambiente é reversível, apagar o volume não é.
- `infra/sync-identity-dev.sh` copia identidade **num sentido só**, prod → dev, e **recusa por evidência, não por confiança no que foi digitado**: o destino tem de provar que é dev (`KOBE_ENV=dev` no `.env` ou a unidade de dev instalada). Sem prova, recusa — falha fechada. A lista é **branca** (`identity/`, `persona/`) e não negra, porque com lista negra uma pasta nova em `user-data/` nasceria sendo copiada por omissão, e é lá que moram alertas, conversas, sessões do Coder e backups.

- **Arnês de injeção de `Update`** (`infra/dev_inject.py`): monta uma mensagem como o Telegram a entregaria e a passa ao mesmo `Application` que roda em produção. Exercita **todo o código do Kobe** — roteamento, autorização, FIFO por tópico, prompt, resposta; o único trecho de fora é a entrega Telegram→bot, que não é código nosso. Aceita roteiro em arquivo (com espera entre mensagens), então a bateria de aceite roda sozinha e o operador apenas assiste — que era o requisito de fundo, já que ele declarou que não vai testar digitando.
  - **É uma porta de entrada no bot, e recusa como tal.** Fora de `dev`, nem carrega configuração. E a whitelist de chat **vazia também é recusa**, não liberação: em `bot/authz.py` vazia significa "esta instância não filtra canal", aqui significa "ninguém me disse onde é seguro bater". A assimetria é deliberada. As três recusas foram exercitadas na linha de comando de verdade, e todas saem com código 3 e razão nomeada.

- **`.env.example`** documenta as três chaves novas — `KOBE_ENV`, `TELEGRAM_ALLOWED_CHAT_IDS` e `KOBE_ENV_PARITY_REFERENCE` — as três **comentadas**, porque ausente é o default e o default é a produção de hoje.

**Testes:** suíte inteira verde (333 → 457 testes)

**Duas peças desta entrega moram no repositório do plugin Apolo**, que é repo separado e nem existe na worktree do Kobe (`plugins/` está no `.gitignore`): o backend `evolution_restrito`, com lista branca de destinos e falha fechada, e o `APOLO_WEBHOOK_HOST`. Ver o CHANGELOG do Apolo. O repo não tinha teste nenhum; a suíte mínima nasceu junto, com 35 testes verdes.

**Divergências entre o briefing e a máquina, registradas porque a máquina venceu:**

1. **O default `127.0.0.1` pedido para o bind do webhook quebraria o WhatsApp em produção.** O próprio briefing condicionava o item a verificar se `host.docker.internal` resolve pro loopback neste setup. Não resolve — é mapeado via `extra_hosts: host-gateway` e aponta pra bridge do Docker. O default ficou sendo o valor de hoje, e o endurecimento virou receita documentada.
2. **`_user_authorized` estava duplicado em 4 módulos, não em 1** (23 pontos de chamada, não "10+"). Tratar só um teria feito a trava de canal falhar aberta.
3. **`infra/systemd/keyko.service` já era template**, o que reduziu o trabalho do P8.

**Registrado para a Sessão #2 (aposentadoria do Chat Manager), não feito aqui:** passar a usar o nome **"Mission Control"** nas superfícies visíveis ao operador. Hoje o sistema vivo e o sistema v0.13 a ser aposentado dividem a palavra "missão", o pacote `bot/mission_control/` e o diretório `user-data/missoes/` — a separação é por **arquivo**, não por pasta. Nesta sessão só o `_user_authorized` de `bot/mission_control/handlers.py` foi tocado, junto com os outros três; **nada com `missao`/`missoes`/`mission` foi apagado, movido ou renomeado.**

**A prova do verificador de paridade**, rodado à mão contra os dois `.env` reais desta máquina: acusa **28 chaves faltando** no ambiente de desenvolvimento e **3 sobrando** (`APOLO_WPPCONNECT_URL/SECRET/SESSION`, resto do backend removido na v0.3.0 do Apolo) — exatamente o alvo de aceite. Nenhum valor apareceu na saída. O teste automatizado roda sobre `.env` sintéticos, com um valor reconhecível plantado nos dois lados e a asserção de que ele não aparece no relatório, no log nem na exceção.

**A prova do Hindsight, que era o critério bloqueante** (comandos rodados nesta máquina, contra o `.env` real da produção):

- `docker compose config` da produção **antes** e **depois** da mudança: **diff vazio**. A saída resolve, letra por letra, `name: hindsight`, `hindsight-app`, `hindsight-postgres`, `hindsight-net`, `hindsight-postgres-data`, portas `8888`/`9999` — os nomes de hoje.
- Antes disso, verifiquei que a própria forma de medir era neutra: a invocação com `--project-directory` produz saída idêntica à invocação simples, senão o "diff vazio" estaria medindo o instrumento, não a mudança.
- O mesmo compose resolvido com o `.env.dev` (com senha de mentira) → `hindsight-dev`, `hindsight-app-dev`, `hindsight-postgres-dev`, `hindsight-net-dev`, `hindsight-postgres-data-dev`, portas `8890`/`9991`. **Interseção com o conjunto de produção: vazia.** na worktree de desenvolvimento; os 2 novos exercitam o dourado com `KOBE_ENV` ausente e com `KOBE_ENV=prod` explícito.

**Reversão:** o trabalho vive em duas branches — `coder/cc546d4b` no Kobe e `coder/cc546d4b-env-layer` no Apolo. Em código versionado o commit limpo **é** o caminho de volta; backup extra seria peso sem ganho. **Nenhum arquivo da árvore de produção foi tocado** (só lido, para inspecionar), **nenhum serviço foi reiniciado**, **nenhuma unidade systemd foi instalada ou habilitada**, **a stack Hindsight de dev não foi subida** e **nenhuma mensagem de WhatsApp foi enviada**. Como consequência, o comportamento em execução hoje é bit-a-bit o de antes desta entrega: o que mudou foi o código, não o que está no ar.


### O Kobe para de guardar conteúdo de WhatsApp — a tabela sai do core (2026-08-24)

**Operador pediu:** acabar com a cópia de conteúdo de WhatsApp dentro do Kobe, recebido e enviado. Palavra dele: *"tudo que é WhatsApp, conteúdo do lado de dentro do WhatsApp, tem que estar na base da Evolution e não no Kobe. Se for necessário guardar algo que a gente envie por aqui, se puder ficar na base da Evolution, melhor. Até porque o Kobe pode ser utilizado por um segundo usuário que poderia existir sem Evolution API."*

**Por quê:** a gravação nasceu com o backend WPPConnect, que não tinha banco nenhum — se o Kobe não anotasse, o dado sumia. Em 30/05/2026 o backend virou Evolution, que tem banco próprio, e ninguém desligou a anotação. Virou cópia redundante rodando por ~3 meses. E, pior que a redundância: `whatsapp_messages` estava declarada no schema do **core**, então uma instalação do Kobe que nunca vai falar com WhatsApp criava tabela de WhatsApp assim mesmo.

**Os números, medidos na fonte antes de mexer** (não herdados de memória):

- `whatsapp_messages`: **15.642 linhas** (15.362 `in`, 280 `out`), 1.266 com mídia. Varri a tabela inteira paginando de mil em mil — `.limit(20000)` no PostgREST devolve 1.000 e mente por omissão.
- Mídia em disco: **2,0 GB**, 1.259 arquivos.
- Evolution: **15.540 mensagens**, das quais **286 `fromMe=true`**. Conferido caso a caso que o que o Kobe envia **já fica registrado lá** com o texto completo.
- Concentração do acervo: os dois grupos "Networking" — que **nem estão no catálogo do operador** — somam **11.546 linhas (73,8%)** e 814 dos 1.266 arquivos de mídia.

**Foi feito (no core):**

- **`/whatsapp_inbox` removido** — handler, registro em `bot/main.py` e a entrada no menu do Telegram (que vinha do manifest do plugin). Decisão do operador: *nunca usou*. Ele pergunta ao agente, que consulta a Evolution na hora. Junto morreu um filtro que era decorativo: o `nao-lidas` filtrava por `lida=false`, e **nada no código jamais marcou mensagem como lida** — as 15.362 estavam `false` desde maio.
- **`whatsapp_messages` saiu de `infra/schema.sql`**, junto com os dois índices. No lugar ficou um comentário explicando que a ausência é deliberada — senão alguém "conserta" a falta daqui a seis meses. **`contacts` fica**: é catálogo de destinatário, não histórico, e é o que resolve "manda pro Pedro".
- **`infra/migrations/004_remove_whatsapp_messages.sql`** — a remoção da estrutura, com os dois pré-requisitos escritos na cara (backup conferido + código novo já no ar, senão o webhook antigo recria linha depois).
- **`infra/decommission_whatsapp_acervo.py`** — dump da tabela, conferência, mídia movida e linhas apagadas. Detalhe abaixo.
- **`docs/runbooks/decomissionar-acervo-whatsapp.md`** — a ordem de execução pro Hal, com a armadilha do `APOLO_MIDIA_DIR` travada por escrito.
- Docs: `docs/testes-apolo.md` e `docs/migracao-evolution.md` corrigidos nos pontos que mandavam validar contra a tabela.

**O script de decomissionamento — o objeto perigoso desta entrega.** Ele apaga 15 mil linhas e move 2 GB contra um banco que é **o mesmo de dev e de produção**. O desenho segue a condição que o operador colocou (*"não existe a menor possibilidade de fazer qualquer coisa que não seja reversível"*):

- **ensaio é o default** — sem `--executar` ele não escreve nada, só relata;
- **o backup é gate, não etapa**: ele dumpa, **relê o arquivo gravado** e compara a contagem com o banco. Não bateu, aborta sem apagar nada;
- a mídia é **movida**, não copiada — `mv` no mesmo disco, instantâneo, e desfazível com um `mv` de volta;
- **não entra em laço infinito se o `DELETE` não tiver efeito** (permissão/RLS negando em silêncio): ver o mesmo lote de ids duas vezes aborta, em vez de rodar pra sempre contra a produção imprimindo progresso falso. Achado na revisão do próprio diff, com teste;
- ele **recusa sobrescrever** um backup que já existe;
- na conferência final, avisa se a contagem voltou a crescer — sintoma de webhook antigo ainda no ar.

**Eu não executei o script, de propósito.** Quem roda é o Hal, com o operador ciente. Runbook em `docs/runbooks/decomissionar-acervo-whatsapp.md`.

**Testes (ambiente de desenvolvimento):**

- **Suíte completa: 333 passaram, 0 falharam** (eram 319; entraram 14 novos). Verificador de portabilidade verde.
- **Os 14 testes novos miram no gate, não no caminho feliz** (`tests/test_decommission_whatsapp.py`): que `conferir_dump` acusa dump truncado e estoura em arquivo corrompido — se ele mentir, libera-se uma deleção sem rede; e que `apagar_linhas` esvazia tudo, **termina** em tabela vazia e pagina em lotes (um loop que não avança rodaria pra sempre contra a produção).
- **Backup exercido de verdade contra o banco real**: dump das 15.650 linhas → 984 KB comprimidos, conferência passou (15.650 no arquivo = 15.650 no banco), amostra do conteúdo verificada. Escrita só num diretório descartável; nada foi apagado.
- **Ensaio conferido nas duas árvores**: apontado pra produção, o script enxerga os 1.261 arquivos / 2,0 GB reais. Foi assim que apareceu um detalhe que o runbook precisava fixar — `APOLO_MIDIA_DIR` do `.env` manda no caminho da mídia, então **rodar com o `.env` errado faria backup de pasta vazia**.
- Do lado do plugin (repo separado, v0.3.0): webhook respondeu 200 a payloads **reais** da Evolution sem gravar linha nenhuma; envio real chegou, apareceu na Evolution com o texto completo e **não** entrou na tabela.

**O que o operador perde, dito na cara:** mídia recebida deixa de existir no servidor — fica só no aparelho. Testei antes de ele decidir: buscar mídia antiga sob demanda pela Evolution **falhou nas 3 amostras** (link do CDN do WhatsApp expira; `S3_ENABLED=false` e a tabela `Media` dela está vazia). Risco aceito por ele — *"pede pra pessoa mandar de novo"*.

**Reversão:** `git revert` dos commits desta entrada devolve o código (estado limpo anterior: `a7187a7`). O **dado** só volta pelo backup que o script produz, enquanto a pasta existir — depois de apagada, não há volta. Por isso o backup é gate.

### Suíte inteira verde — fixtures do resume derivados da fonte, e a trava que faltava (2026-08-21)

**Operador pediu:** consertar as 4 falhas de `tests/test_resume.py` que arrastavam há semanas — revogando a instrução anterior de "não encostar nelas". Com três condições: **confirmar o diagnóstico na fonte** antes de mexer (se fosse bug real de produção, parar e reportar); **derivar o fixture do formato real** em vez de repetir literal à mão; e uma **trava dura — proibido alterar `bot/resume.py` ou qualquer código de produção para fazer teste passar** (teste que só passa mexendo em produção é achado, não conserto).

**Por quê:** a suíte tinha virado sinal morto. Quando 4 testes estão sempre vermelhos, ninguém repara no quinto — e foi exatamente o que aconteceu na véspera, quando `test_prompt_aging.py` quebrou sozinho e só apareceu por acaso.

**O diagnóstico levantado estava incompleto, e a diferença importa.** A hipótese era "os 4 morrem com `KeyError: 'curated_core'`". Confirmando na fonte, são **duas causas distintas**:

- **Causa A** (`test_load_resume_context_cm_on_uses_immediate_and_pointers`, `..._cm_off_uses_session_history`): não é `KeyError`, é **`AttributeError: 'SimpleNamespace' object has no attribute 'working_memory_enabled'`**. A `Config` falsa tinha **4 campos** escritos à mão; a `Config` real é uma dataclass com **41**, e o produtor passou a ler três flags que a falsificação não tinha.
- **Causa B** (`test_resume_invokes_agent_and_sends_synthesis`, `..._falls_back_to_ping_when_agent_fails`): aí sim o pacote de contexto fabricado à mão tem **6 chaves** enquanto `_load_resume_context` devolve **8** (faltavam `curated_core` e `grounding_signals`). O `KeyError` é engolido pelo `except` largo de `resume_one_snapshot`, que cai no ping — por isso o teste falhava com `assert 0 == 1`, um sintoma que esconde a causa.

**Produção está sã — verificado, não presumido.** `resume_one_snapshot` monta o `ctx` num **único ponto** (`bot/resume.py:305`, via `_load_resume_context`), que sempre devolve as 8 chaves; não existe caminho em que um dict montado à mão chegue à produção. Logo, `ctx["curated_core"]` não pode dar `KeyError` em produção, e a condição de "parar e reportar" não disparou. **Nenhuma linha de produção foi tocada** — o diff desta entrada é `tests/test_resume.py` e nada mais.

**Foi feito** (só `tests/test_resume.py`):

- **`_config()` derivada da dataclass real.** Todo campo de `Config` entra automaticamente, com default escolhido pelo tipo; o teste sobrescreve só o que exercita. Campo novo em produção não quebra mais estes testes — e um campo *removido* também não passa despercebido, porque o teste que o sobrescrever vai apontar para um nome inexistente.
- **`_ctx()` derivada do produtor real.** Em vez de repetir o dict à mão, chama `_load_resume_context` com as folhas neutralizadas e usa a forma que ele devolver. Chave nova chega sozinha. Tem ainda uma guarda: sobrescrever chave que o produtor não devolve falha com mensagem explícita, em vez de criar uma chave fantasma.
- **A trava que faltava** (`test_produtor_e_consumidor_do_contexto_nao_se_desencontram`). O que deixou isto apodrecer dois meses foi **não haver nada amarrando produtor e consumidor**. Agora há: o teste lê o fonte de `bot/resume.py` com `ast`, junta todo `ctx["..."]` que o consumidor lê, e confirma que o produtor entrega todas. Mira no lado que importa — **chave lida e não produzida é `KeyError` em produção, não só teste vermelho**. Verificada por mutação: removendo uma chave do produtor, ela acusa.
- **Um achado colateral, corrigido:** `test_load_resume_context_cm_off_uses_session_history` testava com o nome errado. O ramo legado é governado pela flag de **memória** (`working_memory_enabled`), não pela de **conversas** — as duas foram desacopladas na Frente 0 e o nome do teste ficou para trás. As duas flags agora vêm explícitas, com o porquê comentado.
- **Uma bomba armada desarmada:** `test_resume_skips_when_activity_after_snapshot` tinha o mesmo `ctx` capenga de 6 chaves e **passava por sorte** — retorna na guarda de atividade antes de chegar na linha que quebra. Quebraria no dia em que a guarda mudasse. Agora usa o fixture derivado.

**Testes (ambiente de desenvolvimento):** **319 passaram, 0 falharam — suíte inteira verde.** Era 314 passando e 4 falhando. Os 4 antigos passaram e entrou 1 teste novo (a trava de acoplamento). O verificador de portabilidade segue verde.

**Não implementado, por instrução — proposta em `.local/proposta-alarme-suite-vermelha.md`:** alarme de suíte vermelha (item 5-B). Hoje ninguém é avisado quando a suíte quebra, e é por isso que estes 4 testes ficaram vermelhos desde 24/06 sem ninguém ver. Recomendação: alerta diário no Keyko que roda a suíte e **só fala se estiver vermelha**, avisando na virada verde→vermelho para não virar ruído — custo zero em dinheiro, ~7s de CPU/dia, e chega no Telegram. Aguardando decisão do operador.

**`CLAUDE.md`:** não foi tocado nesta rodada (nenhuma mudança precisou dele), portanto não houve o que fazer backup. O backup da rodada anterior segue em `.local/backups/CLAUDE.md.20260820-212405.bak`.

**Deploy:** **não feito**, por instrução. Entrega na branch `coder/b7bec0ad`.

**Reversão:** `git revert` do commit. Só testes mudaram — sem risco de runtime, sem migração, sem estado.

### Regra temporal no CLAUDE.md + filtro de âncora + faxina de portabilidade (2026-08-20)

**Operador pediu:** quatro frentes numa leva. (1) A regra nova de referência temporal no `CLAUDE.md` mais o filtro de âncora no gate — *"concordo 130%, você já tem o meu ok"* —, com o corretor automático **morto** (foi reprovado por medição na rodada anterior e ele vetou o desenho). (2) `infra/sync-prod.sh`: *"precisa sumir da face do planeta. Não quero vestígios disso."* (3) Os 4 caminhos absolutos cravados em `bot/apolo_handlers.py`: *"tem que ser detonado"*. (4) A faxina do resto das ocorrências e um verificador de portabilidade que impeça a sujeira de voltar.

**Por quê:** as quatro coisas são a mesma classe de problema — **dado de uma máquina específica vazando para dentro do que é público ou permanente**. O caminho `/home/<operador>` num default de código quebra quem clona o repo; o script de rsync é o método de deploy que já congelou uma produção; e a referência temporal sem lastro é o "caminho da máquina" da linguagem: parece precisa e não é.

**Foi feito:**

- **Filtro de âncora no gate temporal** (`bot/temporal_gate.py`, `bot/temporal_markers.toml`). Se a frase já traz o dado absoluto ao lado da referência relativa — *"no ar desde 14/07 **às 23:03**"*, *"última atividade **às 14:09 UTC** (uns 5 min atrás)"*, *"datado de ontem (**2026-06-25**)"* —, a relativa é glosa de algo verificável e **não acende**. O escopo é a **frase**, não a resposta: uma resposta pode ter uma frase ancorada e outra solta, e só a solta interessa. Padrões (hora, data com barra, data por extenso, ISO, PID/hash/versão) ficam no TOML, editáveis.
  **Efeito medido no mesmo corpus de 1.644 respostas: acendimento caiu de 9,9% → 6,0% dos turnos**; custo do nível 1 no caminho comum: **160 µs por resposta** (era 140 µs — a diferença é a alternância extra na lista de exclusões; o filtro em si só roda nos turnos que já acenderam).
  Junto, uma exclusão de falso positivo real colhido no corpus: *"prioridade **pra ontem**"* é expressão de urgência, não data.
  **Mudança de comportamento deliberada:** *"desde 24 de junho"* deixou de acender. A data está escrita, é conferível e não envelhece — não é o caso que o gate procura; o caso é *"desde ontem"* seco. Fixado num teste dedicado para ficar explícito que foi decisão, não regressão.
- **Regra nova no `CLAUDE.md`** (seção *Fundamentação*): *"Referência temporal só sai com âncora — ou não sai."* Escrita como **procedimento de decisão em três perguntas**, não como proibição: (1) a frase precisa mesmo dela? quase sempre não — **corte**, que é a saída mais barata e a única que não tem como mentir; (2) se precisa, ancore num **fato**, não no relógio; (3) se o fato é data/hora, ela tem que ter vindo de uma **fonte olhada naquele turno**, e sai **junto** da relativa. Inclui o exemplo real apontado pelo operador (*"desligado desde ontem, de propósito"*). O bullet antigo (*"Nada relativo ao TEMPO sem conferir o tempo"*) **permanece**: ele cobre "vou afirmar um estado"; o novo cobre "vou escrever um advérbio de tempo", que é outro gesto.
  **Por que reescrever em vez de só endurecer o tom:** a regra antiga manda **conferir**, que é caro, e não oferece a saída barata que os dados mostram ser a correta na maioria dos casos — não escrever a referência. Ela era ignorada por custo, não por ênfase.
- **`infra/sync-prod.sh` removido.** Varredura prévia no repo inteiro: **nada o invocava além dele mesmo**. As demais menções a rsync são a regra e a história (CHANGELOG, runbooks) e ficam — a lição permanece, o script sai.
- **Proibição de rsync escrita como regra** no `CLAUDE.md` (seção nova *"Deploy é git — rsync não é método de deploy de nada"*), que **não a tinha**. Nomeia a causa: o incidente de 12-13/06/2026, em que cópia crua congelou o git da produção numa tag velha e um `rsync --delete` cego apagou arquivo sem caminho de volta.
- **`bot/apolo_handlers.py`: 4 cópias de `os.environ.get("KOBE_HOME", <caminho de uma máquina>)` viraram um helper único.** Resolve nesta ordem: `$KOBE_HOME` **se apontar para uma raiz de verdade** → derivação da localização do próprio módulo → **`RuntimeError` com o motivo escrito**. Nunca mais um caminho de máquina como default. Ganho lateral: env setada mas errada agora **avisa e deriva** em vez de obedecer calado — que era como o bug se disfarçava.
- **Faxina:** 25 ocorrências de caminho do operador substituídas por placeholder, por contexto (`$KOBE_HOME`, `$KOBE_PROD`, `$KOBE_DEV`, `/home/seu_usuario`, `/opt/kobe` em fixture de teste), em `CHANGELOG.md`, `SPEC.md`, `docs/migracao-evolution.md`, `docs/spr/`, `docs/chat-manager/`, `infra/hindsight/README.md`, duas migrations e três testes. **No `CHANGELOG` trocou-se só o caminho, sem reescrever o que foi dito.** O runbook de migração ganhou uma linha definindo `$KOBE_DEV`/`$KOBE_PROD`, para continuar executável.
- **Verificador de portabilidade** (`tests/portability_guard.sh` + `tests/test_portability.py`), espelhando o do plugin Coder. Roda **junto da suíte** (faxina sem trava volta), usa `git grep` sobre o tree rastreado, e **espelha as exclusões de `EXCLUDE_PATHS` do `infra/publish.sh`** para não dar alarme falso em `docs/runbooks/`, que não vai ao público. Placeholders genéricos (`/home/seu_usuario`, `/home/x`) são explicitamente permitidos — se o guard os acusasse, empurraria a correção certa de volta para a errada.

**Fora dos quatro blocos, e sinalizado:** `tests/test_prompt_aging.py` passou a falhar **durante** esta sessão sem que eu tocasse nele. Diagnóstico: `NOW` estava **cravado** em `datetime(2026, 8, 20, 12, 0, 0)` enquanto o código sob teste calcula a idade contra o **relógio vivo**. As duas referências se afastam com as horas: `days=12` virou 12,5 dias reais e o `round()` levou para 13. Passou de manhã, quebrou à noite. Não é instabilidade — é bomba-relógio, e falharia para todo mundo a partir de agora. Corrigido em uma linha (`NOW = datetime.now(timezone.utc)`), preservando a intenção do teste. **Foi uma ampliação de escopo que eu fiz** para poder entregar a suíte verde que foi pedida; se o operador preferir reverter e tratar à parte, é `git revert` só desse trecho.

**Testes (ambiente de desenvolvimento):** **314 passaram, 4 falharam.** As 4 são as **mesmas** pré-existentes de `tests/test_resume.py` (`KeyError: 'curated_core'`), fora de escopo por instrução. **Nenhuma falha nova.** +31 testes nesta rodada: gate 68→87 (pares âncora-ancorada/gêmea-solta, escopo de frase, auto-âncora, idiom de urgência), 8 do resolvedor de raiz do Kobe, 4 do verificador de portabilidade.
Duas travas que não são decorativas: o verificador foi **testado por mutação** (planta um caminho de operador num repo descartável e confirma que ele acusa — um guard que não sabe falhar deixa o placar verde para sempre); e os casos de âncora são **pares** (frase ancorada silencia / gêmea sem âncora acende), porque sem o par um bug que silenciasse tudo passaria batido.

**Backup do `CLAUDE.md`:** `.local/backups/CLAUDE.md.20260820-212405.bak`, tirado **antes** da primeira letra, md5 conferido contra o original (`bc5a7d81…`). Regra inegociável do operador.

**Deploy:** **não feito**, por instrução. Entrega na branch `coder/b7bec0ad` com a suíte verde. O ciclo dev → repo dev → prod → público é chamada do operador.

**Reversão:** tudo aditivo/localizado, sem migração e sem estado. O gate segue com `TEMPORAL_GATE_ENABLED=false` por padrão (flag off + restart desliga sem tocar em código). `git revert` do commit desfaz regra, filtro, faxina e verificador; `infra/sync-prod.sh` volta pelo mesmo revert, se um dia for preciso.

### Gate de referência temporal na saída — modo observação, atrás de flag (2026-08-20)

**Operador pediu:** um gate que impeça o agente de afirmar **quando** algo aconteceu sem ter conferido ("desde ontem", "quando subimos isso", "semana passada"), sob **duas restrições duras**: (1) tem que ser **código, não instrução de prompt** — instrução já existe no contrato e vazou mesmo assim; (2) **não pode impactar a latência** — ele quer garantia, não promessa. E foi explícito: *"não aceito 'vamos codar e ver no que dá'. Se os números não fecharem, não é codado"*.

**Por quê:** a regra do contrato (*"Nada relativo ao TEMPO sem conferir o tempo"*) não tem **gatilho** — escrever um advérbio não parece uma ação, então não dispara verificação nenhuma. E não existia **nenhum passo** entre o agente terminar de escrever e o operador receber: o texto saía do `claude -p`, passava por `_resolve_claude` (que só trata erro/timeout), levava formatação de markdown e ia pro Telegram. Linha direta. Todo o grounding do Kobe vivia do lado da **entrada** (`bot/memory/grounding.py`); a saída não tinha nenhum.

**A medição veio antes do código, e mudou o desenho.** Corpus: **1.644 respostas reais do assistant** (tabela `messages`, mai→ago/2026; mediana de 1.546 caracteres).

- **A lista "óbvia" de marcadores está errada, e o corpus prova.** Uma sondagem ampla mostrou que os candidatos naturais são **mobília da linguagem do agente**, não afirmação temporal: `agora` aparece em **57,1%** das respostas, `antes de` em **39,0%**, `hoje` em **32,5%** (no Kobe "hoje" quase sempre significa *atualmente* — *"hoje cada conversa é meio amnésica"*), `quando você` em **18,0%** (futuro/condicional). Uma lista com esses acenderia em **36,4% dos turnos** — desenho errado. Apertando para só o retrospectivo duro: **9,9%**.
- **Falso positivo, classificado à mão** em 25 frases marcadas amostradas uniformemente: **3 de 25 (12%)** eram falso positivo puro (menção meta à própria palavra, hipótese condicional, conhecimento geral do mundo). Mas o achado que redesenhou tudo: **~2/3 das afirmações temporais verdadeiras ESTAVAM ancoradas** — o turno tinha rodado `systemctl`, `ps`, `git log`, `stat` ou consultado a agenda naquele mesmo turno. **A pergunta que importa não é "isto é afirmação temporal?" — é "isto tem lastro?"**.
- **A opção de segunda passada num modelo barato foi MEDIDA E REPROVADA.** Sobre só as frases marcadas (nunca a resposta inteira), `gpt-4o-mini`: **p50 667 ms · p95 1.646 ms · max 4.412 ms** por frase — e **23 de 25 vereditos voltaram "sim"**. Ela **confirma em vez de filtrar**: pagaria-se de 0,7 a 1,6 segundo por turno aceso para receber de volta o que o nível 1 já dizia de graça. Descartada por medição, não por preferência. A opção de devolver ao próprio agente cai pelo mesmo motivo, mais cara.
- **Custo do nível 1, medido:** o desenho "casa primeiro, mascara depois" custa **140 µs** por resposta no caminho comum (~90% dos turnos), contra 217 µs do "mascara sempre". Latência esperada por turno: **~0,15 ms** — cerca de 0,003% de um turno típico.

**Foi feito:**
- **`bot/temporal_gate.py`** (novo): dois níveis. **Nível 1** varre a resposta final atrás de marcador retrospectivo (regex compilada **uma vez no import**); se nada casa, devolve `None` e o turno segue — zero latência adicional, e é a maioria esmagadora dos turnos. **Nível 2 (a)**, só quando o nível 1 acende: cruza com o rastro de ferramentas do turno — afirmação com fonte consultada tem lastro; sem fonte nenhuma é confabulação quase certa. Custo: leitura de um `bool`. As opções (b) e (c) **não** foram implementadas, e o motivo com os números está no cabeçalho do módulo, para ninguém "melhorar" isso depois sem refazer a medição.
- **`bot/temporal_markers.toml`** (novo): a lista mora em **arquivo de configuração legível e editável**, não enterrada na lógica — apertar a malha é edição de config, não patch. Traz registrado *por que cada candidato ficou de fora*, com o percentual do corpus. Máscaras: bloco de código, código inline, linha de citação, trecho curto entre aspas e frase de ack — o gate age **só na resposta nova**, nunca em citação do operador nem dentro de código.
- **`bot/progress.py` e `bot/telegram_handler.py`**: sinal `touched_temporal_source` nos dois contadores de `tool_use` do turno, setado no **mesmo laço** onde a detecção de ack (`kobe-notify`) já lê o comando do Bash hoje. Nenhuma chamada nova, nenhum evento novo — uma comparação de substring por bloco. Precisa existir nos dois porque o caminho de background não tem `ProgressReporter`.
- **`bot/telegram_handler.py::_resolve_claude`**: o gancho vai imediatamente antes do `return reply_text`. É o **único ponto** por onde passam os dois caminhos de resposta (inline e background), então um lugar cobre tudo. `temporal_probe_fn` entra como kwarg **opcional** (default `None`) — nenhuma chamada existente quebra.
- **Modo observação: o gate SÓ LOGA.** `temporal_gate marked=N grounded=<bool> action=observe markers=[…] snippet="…"`, em WARNING quando não há lastro (o caso que ele existe pra pegar) e INFO quando há. **Não altera a resposta, não anexa ressalva, não devolve nada ao agente.** Deixar o gate **agir** é aprovação separada do operador, depois de ver os números reais de produção — foi assim que o plano foi aprovado.
- **`.env.example`**: `TEMPORAL_GATE_ENABLED=false` (default off). Rollback = flag off + restart, sem tocar em código.
- **Robustez:** TOML ilegível **não derruba o bot** — loga ERROR alto e desliga o gate (pior caso vira o comportamento de hoje, não indisponibilidade). E o gancho tem `try/except` largo e comentado: **um bug no gate nunca pode comer uma resposta do operador**.

**Testes (ambiente de desenvolvimento):** `tests/test_temporal_gate.py`, **68 testes, todos verdes**. Os casos que **não** podem acender não foram inventados: são os **falso-positivos reais colhidos no corpus** (menção meta, hipótese, conhecimento do mundo) mais as construções que a sondagem provou serem linguagem corrente do agente. Cobre: acende em afirmação retrospectiva; não acende em mobília/ack/bloco de código/código inline/citação; acende **fora** do bloco mesmo com bloco presente (a máscara descarta o marcador, não a resposta); `grounded` reflete o rastro de ferramentas; mapeamento de fonte temporal (15 casos, incl. Bash que **não** é fonte); os dois contadores marcam o sinal e a detecção de ack segue intacta; a flag liga/desliga nos formatos aceitos.

Três travas merecem destaque: **(1)** com a flag off, `observe` e `scan` são **sabotados para explodir** — se o caminho tocasse o gate, o teste falharia em vez de passar em silêncio; **(2)** com a flag on, o teste compara a saída caractere a caractere com a entrada, provando que o modo observação **não altera a resposta**; **(3)** um gate que levanta exceção **não derruba a entrega**. Há ainda um guarda-corpo de custo (teto folgado de 5 ms, alarme de incêndio e não benchmark) que pega regressão de desenho — alguém trocar o "casa-primeiro" por "mascara-sempre", ou compilar regex por chamada.

**Suíte completa: 283 passaram, 4 falharam.** As 4 são as **mesmas** pré-existentes de `tests/test_resume.py` (`KeyError: 'curated_core'`), medidas na árvore limpa **antes** de tocar em qualquer coisa (baseline: 215 passaram, as mesmas 4 falharam). **Nenhuma falha nova**; +68 testes. Fora de escopo por instrução do operador, não foram tocadas.

**Limites conhecidos, ditos sem maquiagem:**
- **Isto é uma rede, não um muro.** Regex sobre linguagem natural aberta tem recall finito. Perífrase ("lá pelo começo do ciclo"), construção nova e afirmação temporal implícita sem marcador ("o deploy que quebrou isso") **passam batido**. A lista em arquivo editável existe justamente pra apertar a malha quando um vazamento concreto aparecer.
- **Hipótese condicional ainda acende** ("se foi só algum erro de entrega da última vez"). Distinguir exige análise sintática, não regex; um padrão pra "se" abriria um buraco largo demais no recall. Está **fixado num teste nomeado como limite conhecido**, não escondido. Custo real em modo observação: uma linha de log a mais.
- **Os ~3% de turnos que afirmariam tempo sem fonte é ESTIMATIVA**, derivada da amostra de 25 — não medição direta. É exatamente esse número que o modo observação passa a medir de verdade em produção.
- **Não exercitado contra o Telegram real** (nenhuma mudança toca o envio; o gate é read-only sobre a resposta nesta fase).

**Commits:** ver o log da branch `coder/b7bec0ad`.

**Reversão:** dois níveis. Imediato e sem deploy: `TEMPORAL_GATE_ENABLED=false` + restart (default já é off). Definitivo: `git revert` dos commits da branch — as mudanças são aditivas e localizadas, sem migração, sem estado persistido, sem schema.

### Reação de "transcrição pronta" (✍) volta a funcionar — emoji normalizado e validado contra a lista do Bot API (2026-08-20)

**Operador pediu:** consertar um bug **em produção**, publicado no repo público na v0.21.0 hoje: a reação 👀 de recebimento funciona, mas a troca para ✍️ é **recusada pelo Telegram**. Junto com o conserto, blindar a **classe** de erro — trocar o emoji no `.env` no futuro nunca mais pode quebrar calado em produção.

**Por quê (causa provada, não hipótese):** o log de produção de hoje mostra, nos áudios das 11:57 e 11:59, `falha reagindo ✍️ … (Can't parse reactiontype: field "custom_emoji_id" must be a valid number)`. O emoji default em `bot/reactions.py` era `"✍️"` = `U+270D` **+** `U+FE0F` (VARIATION SELECTOR-16, o marcador invisível que pede renderização colorida). A lista de reações do Bot API registra esse emoji como `"✍"` = `U+270D` **puro**. Sem achar na lista, o Telegram tenta ler o valor como *custom emoji* (recurso de canal pago) e recusa. O operador já tinha provado empiricamente: `setMessageReaction` com `"✍"` retornou `ok:true` na **mesma mensagem** que havia falhado.

O agravante não é o caractere errado — é que a recusa era **muda**. Reação é decoração e a falha é engolida de propósito (`set_reaction` nunca levanta, senão uma reação recusada derrubaria a mensagem do operador). Ou seja: o recurso criado justamente para tornar visível o "chegou e morreu calado" estava, ele próprio, falhando caladamente.

**O achado que mudou o desenho do fix:** a regra intuitiva — *"tire sempre o VS16 antes de enviar"* — **estaria errada e trocaria um bug por outro**. Conferindo a lista real (73 emojis), **três exigem o VS16**: ❤️‍🔥, 🤷‍♂️ e 🤷‍♀️. A regra correta é comparar **ignorando** o marcador e enviar **a forma exata que a lista registra**, o que conserta os dois sentidos (sobrou marcador / faltou marcador).

**Foi feito:**
- `bot/reactions.py`: default corrigido para `"✍"`. Nova `normalize_reaction()` com uma tabela canônica (`chave = forma sem VS16`, `valor = forma exata do Bot API`) — uma consulta resolve os dois sentidos. `react()` passa a normalizar e conferir **antes** de falar com a API: emoji fora da lista **não vira chamada** (já sabemos que seria recusada) e grava aviso nomeando o valor.
- **Fonte de verdade da lista**: `telegram.constants.ReactionEmoji`, da própria `python-telegram-bot` (já instalada), em vez de uma lista digitada à mão — quando o Telegram muda a lista, ela chega junto com a atualização da lib, sem cópia velha apodrecendo no nosso código. Custo consciente: `bot/reactions.py`, que era livre de PTB, passa a importá-la.
- `bot/config.py`: leitura do `.env` deixa de ser crua. Ausente → default; **vazia → `""`**, que segue sendo "estágio desligado de propósito", sem aviso; **inválida → WARNING nomeando chave, valor recusado e o emoji usado no lugar**, caindo no default. Cair no default (em vez de não reagir) porque o que vale é o **sinal**, não o desenho: melhor o sinal aparecer com o padrão e o log explicando do que sumir calado.
- Corrigidos os textos que **afirmavam algo que o log desmente** — o docstring de `bot/reactions.py` dizia que "✍️ está na lista permitida e funciona". `.env.example` também trazia a forma com marcador (é o modelo que as pessoas copiam).
- **Produção não precisa de edição de `.env`**: verifiquei que `$KOBE_PROD/.env` só define `TELEGRAM_REACTIONS_ENABLED=true` e nenhum emoji — ou seja, usava exatamente o default do código. O fix no código resolve.

**Testes (ambiente de desenvolvimento):** `tests/test_reactions.py` foi de 7 para 18 testes. Travas novas: os dois defaults estão na lista do Bot API (a trava mais direta — é o que faltava); `"✍️"` → `"✍"` (o bug literal), inclusive com espaços em volta; ❤️‍🔥 / 🤷‍♂️ / 🤷‍♀️ **preservam** o VS16 (impede que o conserto ingênuo quebre estes três); `"❤‍🔥"` → `"❤️‍🔥"` (sentido inverso); 🎧/👂/lixo são rejeitados e **não** viram chamada à API; `.env` inválido cai no default **com** aviso; `.env` vazio continua desligando o estágio **sem** aviso; e a tabela canônica não tem colisão (se o Telegram acrescentar um emoji que colida sem o VS16, quebra no teste em vez de normalizar para o emoji errado silenciosamente).

**Prova de que as travas não são decorativas:** rodei uma checagem de mutação (script descartável em `.local/`) revertendo o código para os três jeitos errados — default com marcador, strip cego de VS16, e sem validação nenhuma. **As três mutações foram pegas** (por 1, 2 e 7 testes respectivamente).

**Suíte completa: 215 passaram, 4 falharam.** As 4 são as **mesmas** pré-existentes de `tests/test_resume.py` (`KeyError: 'curated_core'`), medidas na árvore limpa **antes** de tocar em qualquer coisa (baseline: 204 passaram, as mesmas 4 falharam). **Nenhuma falha nova**; +11 testes. Fora de escopo por instrução do operador, não foram tocadas.

**Não testado aqui (limite honesto):** o caminho real contra a API do Telegram não é exercitável na árvore de dev — os testes usam um bot fake. O que garante o comportamento real é a evidência empírica que o operador já produziu (`"✍"` → `ok:true` na mesma mensagem que falhava) somada à lista vinda da própria biblioteca. A validação final é o operador mandar um áudio depois do deploy e ver o 👀 virar ✍.

**Commits:** o commit desta entrada. Branch `coder/6225cf60`. **Sem deploy** — para no código testado, conforme o pedido; o ciclo (dev VPS → repo dev → prod VPS por `git pull` → repo público) fica para quando o operador autorizar.

**Reversão:** commit único → `git revert` desfaz tudo. Independente disso, `TELEGRAM_REACTIONS_ENABLED=false` + restart desliga o recurso inteiro em produção sem depender de código.

### Datar o que envelhece — o prompt para de apresentar frame congelado como presente — Item D das 4 correções (2026-08-20)

**Operador pediu:** o agente afirmou que uma sala de missão "segue rodando, te reporto quando entregar" — a sala estava `idle` havia 12 dias e já tinha entregado os documentos. Correção pedida: **timestamp visível nas linhas do histórico injetado** e **estado real + data da última atividade** em qualquer coisa que o prompt apresente como "ativo/rodando", lendo da **fonte viva** na hora de montar o prompt. Se estiver idle/encerrado, o prompt deve dizer isso com essas palavras.

**Por quê (três causas materiais, todas confirmadas no código — não era só indisciplina do modelo):**
1. **O histórico não tinha data.** `bot/claude_runner.py` buscava `created_at` do banco e **descartava** ao montar o texto: cada linha virava `papel: conteúdo`. Uma mensagem de 12 dias ficava visualmente **idêntica** à de agora.
2. **A linha da sala entrava sem estado e sem data**, com a palavra "ativa" no presente — e `idle` conta como status ativo em `_ACTIVE_STATUSES` (por design: a sala só fecha por ato do operador). O agente leu exatamente o que estava escrito.
3. **O bloco `[Estado de background vivo]`, que era o antídoto declarado contra "narrar status de memória", só olhava sessões do Coder** e escondia o que tinha mais de 6h. Sala de missão nunca entrava; job despachado nunca entrava. Esta terceira causa não estava no diagnóstico inicial do operador — foi achado da investigação.

**Foi feito:**
- `bot/memory/aging.py` (NOVO): formato ÚNICO de idade pro prompt inteiro (`carimbo`, `humanizar_idade`, `estado_com_idade`, `parse_ts`), pra o agente aprender um padrão só. `bot/memory/background_state.py` passou a delegar pra ele — antes cada bloco tinha o seu ("~13 dia(s)" num, "há ~13 dias" no outro).
- **Histórico datado** (`build_prompt`): `[dd/mm HH:MM]` no fuso do operador em **todas** as linhas (decisão do operador; a alternativa "só nos saltos" foi descartada por deixar brecha), mais a idade relativa (`— há ~12 dias`) na primeira linha e sempre que o salto pra linha anterior passa de 6h. Cabeçalho novo avisa que aquilo é **PASSADO**. Linha sem timestamp legível sai **sem carimbo** — nunca com data inventada.
- **Linha da sala honesta** (`render_sala_ativa`): lê estado e `last_activity` da fonte viva e escolhe o texto — `RODANDO … (Ela está trabalhando AGORA)` vs. `OCIOSA (idle) … Ela NÃO está trabalhando agora e NÃO vai te retornar sozinha — está parada esperando. NÃO diga que ela "segue rodando"`. A regra de roteamento (a sala NÃO captura o canal) foi preservada intacta — a correção é sobre datar, não sobre mudar comportamento.
- **Bloco de background ampliado**: passa a ler **salas de missão** (localizadas por chat+thread — daí o `chat_id` novo no `render_background_state`) e **jobs despachados**, além das sessões do Coder. Salas **não** são filtradas pela janela de 6h (só fecham por ato do operador, então uma sala de 26 dias é fato vivo, não arqueologia — o que ela precisa é do carimbo, não do sumiço). Cada fonte lê de forma independente: uma que falhe não apaga as outras. Instrução nova no cabeçalho: `state=idle` significa **PARADO esperando, NÃO trabalhando**.

**Dois defeitos que só apareceram no teste com dado REAL de produção** (e que a suíte sozinha não pegaria):
- **6 salas idle de julho enchiam o bloco inteiro** e teriam expulsado uma sessão do Coder rodando AGORA — informação bem mais urgente. Corrigido com teto próprio pras salas (`MAX_SALAS=2`, mais recentes primeiro), separado do teto geral. Travado por teste.
- **IDs de sala truncados em 12 chars colidiam**: três salas viravam todas `2026-07-09-d`, deixando o bloco inútil pra identificar qual é qual (o ID do Coder é UUID e 8 chars bastam; o da sala é slug com data). Agora vai inteiro. Travado por teste.

**Testes (ambiente de desenvolvimento):** `tests/test_prompt_aging.py` (23 novos): carimbo em todas as linhas; idade relativa na 1ª linha e nos saltos, ausente nas linhas coladas; cabeçalho avisando que é passado; linha sem timestamp não quebra nem inventa data; tag de áudio convivendo com o carimbo; flag off volta ao legado; fuso do operador (23:30 do dia 19 no BR, não 02:30 do dia 20 UTC); formato de idade; sala idle apresentada como OCIOSA com idade e com a frase proibida nomeada; sala running como RODANDO; regra de roteamento preservada; sala de outro tópico não vaza; bloco enxergando salas e jobs despachados; sala velha não escondida; sala encerrada fora; uma fonte quebrada não apaga as outras; compatibilidade sem `chat_id`; e as duas travas dos defeitos achados com dado real. **Smoke com o estado REAL de produção** (7 salas, todas idle desde julho/agosto): a linha antiga dizia "Sala de missão **ativa**"; a nova diz "Sala de missão **OCIOSA (idle)** … última atividade há ~26 dias (25/07 10:51) … NÃO diga que ela 'segue rodando'". **Suíte completa: 204 passaram, 4 falharam** — as 4 pré-existentes de `test_resume.py` (card `22911527`), baseline preservado.

**Ressalva honesta:** isto reduz muito, mas não elimina, o risco de o agente narrar frame congelado. O que o código garante é que o dado certo está na frente dele, datado e com o estado real. Se ele ignorar um "OCIOSA há 26 dias" escrito na tela, o conserto é outro (contrato/prompt do agente) — não é bala de prata.

**Observação fora de escopo (não corrigida):** no smoke com dado real, a sessão do Coder apareceu como `state=?`. É comportamento pré-existente de `_read_coder_jobs` (lê `data.get("state")`), não tocado por esta mudança; não pude investigar porque o guard do Coder bloqueia leitura de `user-data/coder-sessions` (plano de controle). Fica registrado pro operador decidir.

**Commits:** o commit desta entrada. Branch `coder/4e78925f`.

**Reversão:** `PROMPT_AGING_ENABLED=false` + restart desliga o carimbo do histórico. As correções da sala e do bloco de background são correções de **veracidade** (o texto antigo mentia), então não ficaram atrás de flag — revertem por `git revert` do commit.

### Ack do Liveness não inventa mais fato novo; modelo configurável — Item C das 4 correções (2026-08-20)

**Operador pediu:** o ack que a borda dispara em tarefas pesadas estava inventando plano, nome de arquivo e ferramenta que não seria usada. Textualmente, o que ele quer: **não** mensagem fixa/enlatada ("estou passando para o background"), **não** invenção — e sim uma mensagem que **não invente nada**, que diga que está **providenciando o que foi pedido**, ancorada nas palavras dele. Requisito adicional acordado: deixar o **modelo do ack configurável por `.env`**, pra trocar por Haiku depois sem obra, mantendo o modelo barato atual como default.

**Por quê (causa confirmada no código — não era indisciplina do modelo, era o desenho):** o modelo barato recebe **apenas a mensagem do operador**. Sem repositório, sem histórico, sem saber o que o agente principal vai fazer. E o prompt mandava, com todas as letras, *"NOMEANDO a ação (o que você vai fazer)"* — com um exemplo que era ele próprio uma invenção completa (*"vou varrer a VPS atrás dos arquivos elegíveis pra backup"*). Pedíamos especificidade a quem não tem informação nenhuma; ele obedecia, copiava a forma do exemplo, e preenchia a lacuna com chute.

**Foi feito** (`bot/liveness.py`):
- **Prompt invertido** (start e late): explicita que o modelo **NÃO SABE** o que será feito, com quais ferramentas, em quais arquivos nem quanto tempo leva, e **NÃO PODE SUPOR**; manda reconhecer o pedido **reusando as palavras do operador** e dizer que está providenciando; e **PROÍBE** citar arquivo, pasta, sistema, ferramenta, comando, etapa, número, prazo ou qualquer termo fora da mensagem dele. O exemplo virou exemplo de **forma**, com aviso explícito de não copiar o conteúdo.
- **Guarda-costas programático** (`_tem_invencao`), atrás de chave PRÓPRIA `LIVENESS_ACK_GUARD_ENABLED` (default off): rejeita o ack que traga (a) número que não está na mensagem do operador, (b) caminho/extensão de arquivo, ou (c) termo de uma lista **curta e fechada** de invenção clássica (arquivo, pasta, repositório, script, comando, ferramenta, log, tabela, banco, API, VPS, servidor, minuto, hora…) que não apareça na mensagem dele. A régua é sempre "está na mensagem do operador?" — se ele falou em arquivos, o ack pode falar em arquivos. Rejeitou → cai no texto fixo, que não inventa nada, e o log registra o texto rejeitado (é o que permite calibrar depois). Normaliza acento/caixa; o regex de caminho foi estreitado de propósito pra não confundir o "e/ou" do português com um caminho.
- **Modelo configurável**: `LIVENESS_ACK_PROVIDER` (`openai` | `anthropic`) e `LIVENESS_ACK_MODEL`, com default **exatamente o de hoje** (`openai` / `gpt-4o-mini`) — a correção do prompt fica avaliável isolada da troca de modelo, e a decisão de trocar continua sendo do operador, com resultado na mão. Provider desconhecido ou credencial faltando → fallback, nunca exceção (o ack é garantido).

**Testes (ambiente de desenvolvimento):** `tests/test_edge_liveness.py` passou de 5 pra **21** — trava do prompt (garante que ninguém reintroduza o "NOMEANDO a ação" nem o exemplo inventado numa edição futura); guarda-costas pegando arquivo/número/termo técnico inventados; guarda-costas **liberando** o que o operador disse e o ack ancorado normal; não confundir "e/ou" com caminho; ignorar acento e caixa; ack inventado cai no fallback com a chave ligada; ack ancorado passa; chave desligada deixa passar; modelo default é o de hoje; modelo e provider configuráveis por `.env`; provider `anthropic` usando a credencial certa; sem credencial e provider desconhecido caem no fallback.

**Medição contra o modelo REAL** (gpt-4o-mini, prompt antigo vs. novo, mesmo pedido, guarda-costas como juiz):
- Mensagens vagas mas fechadas ("resolve aquilo lá", "dá uma olhada nisso") — **nenhum dos dois inventou** (12 amostras de cada, ack tardio; 4 de cada, ack de início).
- Mensagens que **dão margem pra extrapolar** — que é o vetor real da dor: **prompt antigo inventou em 6 de 18 amostras**, sempre do mesmo jeito (ex.: "faz um backup do que importa aí" → *"vou fazer o backup dos **arquivos importantes**"*; "limpa o que tá sobrando na máquina" → *"vou limpar os **arquivos** que estão sobrando"*). **Prompt novo: 0 de 18.**
- Ancoragem verificada em 5 pedidos realistas: o ack novo devolve o pedido nas palavras do operador ("vou ver se dá pra melhorar o tempo de resposta do áudio e já te volto"), sem plano inventado e sem enlatado.

**Honestidade sobre a medição:** a invenção é **intermitente**, não determinística — depende de a mensagem deixar lacuna pro modelo preencher. Não dá pra dizer "antes era X% e agora é 0%" a partir de 36 amostras; o que dá pra dizer é que o padrão foi **reproduzido** com o prompt antigo e **não apareceu** com o novo, no mesmo conjunto de pedidos. A validação real é o uso do operador.

**Suíte completa: 180 passaram, 4 falharam** — as 4 pré-existentes de `test_resume.py` (card `22911527`), baseline preservado.

**Commits:** o commit desta entrada. Branch `coder/4e78925f`.

**Reversão:** três níveis independentes. `LIVENESS_ACK_GUARD_ENABLED=false` desliga só o guarda-costas (mantendo o prompt novo); `EDGE_LIVENESS_ENABLED=false` desliga o ack inteiro; `git revert` do commit volta ao prompt antigo. Trocar de modelo é `.env` + restart, sem deploy.

### Garantia de turno — a mensagem sobrevive à falha e o operador é avisado — Item B2 das 4 correções (2026-08-20)

**Operador pediu:** camada 2 de duas contra a perda silenciosa de mensagem — "**o turno nunca morre calado**". Se a persistência falhar, o operador é avisado e a mensagem não se perde. Requisito explícito: esta camada tem que ser **100% AGNÓSTICA DE BANCO** (vale pra Supabase, pro Postgres local, pra qualquer coisa depois), e a mensagem que chegou deve **SOBREVIVER** à falha (fila/retentativa), não só gerar notificação.

**Por quê:** o Kobe já tinha uma rede de segurança global (`on_error`, de 2026-06-08) que avisa "🔴 Travei processando isso aqui — reenvia" — e ela funciona: disparou 20× em 30 dias. Mas ela **nunca via** as 3 mortes silenciosas do Item B1. Motivo material, confirmado no código: com `EDGE_ASSEMBLER_ENABLED=true`, o turno roda numa task própria do montador (`bot/assembler.py`), e o `except Exception` da linha 251 **engolia** a exceção antes que ela pudesse chegar ao PTB. A rede existia; o buraco estava do lado de fora dela. E mesmo onde a rede pegava, "reenvia, por favor" só funciona se o operador ainda tiver o texto — a mensagem em si se perdia.

**Foi feito:**
- `bot/turn_guarantee.py` (NOVO): fila em **disco** (`user-data/pending-turns/*.json`, escrita atômica tmp+rename) — disco local de propósito, porque é justamente o banco que pode estar fora; `run_guarded()` que embrulha o turno; aviso ao operador que diz o que aconteceu e mostra o começo da mensagem guardada (com escape de HTML — um `<` na mensagem do operador derrubaria o próprio aviso, e a garantia falharia no ato de garantir); relatório de arranque agrupado por tópico; poda no teto de 200 arquivos.
- **PONTO DE NÃO-RETORNO** (`bot/telegram_handler.py::_handle_user_text`): o turno agora marca `turn_progress["committed"]=True` imediatamente antes de gravar a mensagem do operador. É o que torna a retentativa **precisa em vez de otimista**: antes dessa marca, re-executar é inócuo (nada gravado, nada respondido); depois, duplicaria a mensagem e poderia gerar resposta dupla. As 3 mortes observadas foram todas ANTES da marca (na leitura do histórico), então o caso real é coberto sem risco.
- **Retentativa automática, uma só**, ~3s depois, e **só** quando: (a) o erro é de transporte do banco (a pergunta é feita ao `bot/db.py`, o ponto único que conhece o driver — este módulo não sabe qual banco é) **e** (b) a marca não foi posta. Fora disso, não re-executa: avisa.
- **Fiação no montador** (`_make_flush_cb`): o buraco original agora é o ponto guardado. `bot/assembler.py` não foi tocado — a garantia vive no handler, que é quem tem config e canal; o montador segue genérico.
- **Fiação na rede global** (`on_error`): passa a **guardar a mensagem antes de avisar**. NÃO re-executa ali (uma exceção que escapou até o PTB pode ter morrido em qualquer ponto do turno). Blindada: qualquer falha na própria garantia degrada pro aviso simples de sempre — nunca pro silêncio.
- **Arranque** (`bot/main.py`): o que sobrou na fila é **reportado** por tópico, antes de qualquer acesso ao banco (é leitura de disco e tem que funcionar com o banco fora — o cenário que enche a fila). Deliberadamente **não reprocessa**: uma mensagem de horas atrás não deve virar resposta do nada; o operador vê e decide.
- `CancelledError` é **propagado**, não tratado como falha de turno — engolir travaria o encerramento do bot (lição direta do bug do auto-cancelamento do assembler, que trocou um bug por outro).

**Testes (ambiente de desenvolvimento):** `tests/test_turn_guarantee.py` (15 novos): turno OK não deixa rastro; **repro do incidente** (morre antes do ponto seguro → retenta → o operador nem percebe, e a pendência some do disco); retentativa que falha também → avisa e guarda; **não retenta depois do ponto seguro**; não retenta erro que não é de transporte; cancelamento propaga e não vira pendência; aviso escapa HTML; falha de disco ainda avisa; falha do próprio aviso não propaga; flag off = legado; poda tira os mais velhos; relatório agrupa por tópico e não reprocessa; JSON legível e sem `.tmp` órfão. E as duas travas-mãe da fiação: **o flush do assembler não engole mais** (o operador é avisado e a mensagem vai pro disco) e a **retentativa transparente pelo caminho do assembler** (silêncio, porque deu certo). `tests/test_turn_error_handler.py`: +2 (config sem `kobe_home` degrada pro aviso simples; com config completa a mensagem é guardada antes do aviso). **Suíte completa: 164 passaram, 4 falharam** — as 4 pré-existentes de `test_resume.py` (card `22911527`), baseline preservado. Validação observável (ver o aviso chegar num turno que trava de verdade) é do operador na produção.

**Commits:** o commit desta entrada. Branch `coder/4e78925f`.

**Reversão:** `TURN_GUARANTEE_ENABLED=false` + restart (segundos) — a exceção volta a subir como antes (travado por teste). Alternativa definitiva: `git revert` do commit. A fila em disco fica em `user-data/`, que é gitignored — nada dela vai pro repo.

### Resiliência de conexão do banco — Item B1 das 4 correções (2026-08-20)

**Operador pediu:** camada 1 de duas contra a perda SILENCIOSA de mensagem — detectar conexão morta, reciclar e tentar de novo. Com uma restrição dura: a migração Supabase → Postgres local **vai acontecer**, então isto não pode ser desenhado de um jeito que precise ser refeito. "Isole num ponto só, pra ser barata de trocar."

**Por quê:** o operador volta depois de um tempo parado, manda uma mensagem, e ela não produz resposta nenhuma. Investigação do log de produção (30 dias): aconteceu **3 vezes** — 14/08 18:51, 14/08 18:52 e 19/08 17:08 — e as três são o mesmo caminho, `bot/memory/working_set.py:69` (`get_immediate_messages`) → `httpx.RemoteProtocolError: Server disconnected`. Clássico de conexão ociosa: o pool guarda o socket, o outro lado derruba, a primeira requisição depois da ociosidade morre. **Não havia retry em lugar nenhum** — nem no cliente, nem na camada de memória.

**Foi feito** (tudo dentro de `bot/db.py`, que tinha 17 linhas e é o **ponto único de isolamento do driver**):
- **Reciclagem por ociosidade (prevenção, custo zero):** se a última conversa bem-sucedida com o banco foi há mais de `DB_IDLE_RECYCLE_SECONDS` (120s), o cliente é trocado ANTES de tentar. Construir o cliente não faz I/O de rede (verificado) — é só descartar um pool que provavelmente já está morto. Isto sozinho ataca a causa raiz das 3 falhas.
- **Repetição com espera crescente (o remédio):** erro de **transporte** (`httpx.TransportError` — inclui `RemoteProtocolError`, `ReadError`, `ConnectError`, timeouts) → recicla e tenta de novo, até 2 vezes extras (0,2s / 0,8s). Erro de **negócio** (dado inválido, permissão, constraint) **não** é repetido: repetir não adiantaria e só mascararia bug.
- **Remontagem da consulta (`_QueryProxy`):** ao trocar de cliente, a consulta já montada aponta pro pool velho. O proxy grava a cadeia (`.table().select().eq()…`) e a **remonta no cliente novo**. É isso que permite que os ~70 pontos do código que falam com o banco não saibam de nada.
- Thread-safe (o Kobe chama o banco de dentro de `asyncio.to_thread`): lock protege a troca do cliente interno, com contador de geração pra duas threads que morreram na MESMA conexão não reciclarem duas vezes seguidas.
- Identidade estável: o objeto `db` é passado adiante por todo o código e continua o mesmo — quem troca é o cliente interno.

**Trade-off declarado e aprovado pelo operador:** repetir uma LEITURA é 100% seguro; repetir uma ESCRITA tem risco teórico de duplicar uma linha (se o servidor gravou mas a resposta se perdeu). Na prática é remotíssimo — a conexão estava morta ANTES do pedido sair, que é o motivo do erro — e a reciclagem preventiva praticamente elimina o cenário. Uma linha duplicada no histórico é bem menos grave que uma mensagem perdida. `DB_RETRY_WRITES=false` desliga só essa parte, mantendo o retry de leitura.

**Migração pro Postgres local:** nenhum outro arquivo do Kobe sabe que o banco é Supabase (o código só usa `.table()` — há um teste que trava isso). No dia da migração, `bot/db.py` é reescrito e **nada mais muda**.

**Testes (ambiente de desenvolvimento):** `tests/test_db_resilience.py` (11 novos, com banco fake que registra a cadeia montada e falha sob demanda): leitura normal sem ruído; **repro do incidente** (`Server disconnected` na 1ª, sucesso na 2ª, com prova de que a repetição rodou no cliente NOVO); cadeia remontada idêntica; desiste e propaga após o teto; erro de negócio não repetido nem reciclado; reciclagem por ociosidade; escrita repete por padrão e não repete com a flag off; **flag off é passe-livre total**; identidade do wrapper estável; e um teste que varre `bot/` garantindo que ninguém usa `auth`/`storage`/`functions` (se usar, o isolamento do driver deixou de ser único). Um desses testes pegou um furo real durante o desenvolvimento: com `DB_RESILIENCE_ENABLED=false` a reciclagem preventiva ainda rodava — não era rollback de verdade; corrigido. **Smoke test contra o banco REAL de produção** (leitura, cadeia longa, reciclagem preventiva, erro de negócio propagando) e, o mais importante, **simulação do incidente real contra o driver real**: injetei `RemoteProtocolError` no `session.send` do cliente vivo e a consulta sobreviveu com o dado correto (log: `db: erro de transporte em limit (tentativa 1/3) — reciclando`). **Suíte completa: 149 passaram, 4 falharam** — as 4 pré-existentes de `test_resume.py` (card `22911527`), baseline preservado.

**Commits:** o commit desta entrada. Branch `coder/4e78925f`.

**Reversão:** `DB_RESILIENCE_ENABLED=false` + restart (segundos) — passe-livre total, comportamento idêntico ao de antes (travado por teste). Alternativa definitiva: `git revert` do commit.

### Reações de recebimento no Telegram (👀 / ✍️) — Item A das 4 correções (2026-08-20)

**Operador pediu:** um sinal de "chegou" IMEDIATO e IMPOSSÍVEL DE ALUCINAR, disparado no instante em que a mensagem entra — antes do montador da borda, antes do classificador. Item A de uma missão de 4 correções de UX/robustez (ordem de prioridade definida pelo operador: reação → perda silenciosa de mensagem → ack que inventa → prompt que não data o que envelhece).

**Por quê:** todo sinal de vida que o Kobe tinha até aqui passa por MODELO (o LIV-ack do Liveness) ou por código que roda DEPOIS do montador da borda. Se o turno morre antes disso — e morre: `assembler.py` engolia a exceção, ver o item do turno-morre-calado — não sai absolutamente nada, e do lado do operador é indistinguível de "o agente me ignorou". A reação é o sinal mais primitivo possível: chamada direta à API do Telegram, sem modelo no caminho (logo, sem o que alucinar) e sem depender do turno sobreviver. Efeito colateral desejado e pedido pelo operador: com ela, a perda silenciosa de mensagem fica VISÍVEL — o 👀 aparece e o silêncio depois dele grita.

**Foi feito:**
- `bot/reactions.py` (NOVO, ~80 linhas): `set_reaction()` (best-effort absoluto — engole qualquer exceção, nunca derruba o turno) e `react()` (fire-and-forget síncrono — quem recebe a mensagem não paga latência nenhuma pelo sinal). Emoji vazio/`None` é no-op, o que permite desligar um estágio pelo `.env` sem tocar em código.
- `bot/telegram_handler.py`: helpers `_react_received` (👀) e `_react_transcribed` (✍️) + fiação em **5** pontos — entrada de texto, entrada de áudio, **pós-transcrição nos DOIS caminhos** (assembler ligado e legado), entrada de foto e entrada de documento. Os dois caminhos de áudio importam: sem o legado, o semáforo mentiria com `EDGE_ASSEMBLER_ENABLED=false`.
- `bot/config.py` + `.env.example`: `TELEGRAM_REACTIONS_ENABLED` (default **off** — muda comportamento visível no chat, então merece validação do operador antes de ligar), `TELEGRAM_REACTION_RECEIVED` e `TELEGRAM_REACTION_TRANSCRIBED` (a lista de emojis aceitos é do Telegram e pode mudar sem aviso).

**Testes (ambiente de desenvolvimento):** teste de plataforma ANTES de escrever qualquer código, contra a API real, no tópico de fórum Dev Kobe (thread 475) — a lib suporta `setMessageReaction` (python-telegram-bot 22.7); funciona em tópico de fórum; funciona em mensagem **do operador**, não só do bot; 👀 e ✍️ aceitos; 👀→✍️ substitui na mesma mensagem; 🎧 rejeitado (`REACTION_INVALID`), confirmando a restrição que o operador já suspeitava. Reações de teste removidas ao fim. Suíte automatizada: `tests/test_reactions.py` (7 novos — chama a API, NUNCA levanta em erro, dispara sem esperar, no-op sem emoji, flag off = zero chamada, semáforo usa o MESMO `message_id`, config lê emoji do `.env`). **Suíte completa: 138 passaram, 4 falharam** — as 4 são as pré-existentes de `test_resume.py` (`KeyError: 'curated_core'`, card `22911527` no Flow), vermelhas antes desta mudança; baseline preservado (era 131/4). Validação observável (o 👀 aparecendo no chat) é do operador na produção, com a flag ligada.

**Commits:** o commit desta entrada. Branch `coder/4e78925f`.

**Reversão:** `TELEGRAM_REACTIONS_ENABLED=false` + restart (segundos, sem deploy) — volta ao comportamento de hoje, nenhuma reação. Alternativa definitiva: `git revert` do commit (código versionado, nenhum dado fora do git).

### FIX CRÍTICO — Assembler cancelava o próprio timer e matava TODO turno normal (2026-07-14)

**Operador pediu:** consertar o bug em produção que deixou o Hal mudo desde que as 4 flags da borda nova foram ligadas (`.env` 22:15, restart 22:16): mensagem chega, áudio é transcrito, e o turno morre em silêncio — sem log, sem exceção. Só comando slash respondia. Causa raiz já vinha diagnosticada e provada com repro isolado.

**Por quê:** `bot/assembler.py::_flush` chamava `buf.timer.cancel()` — e quando o flush vinha pelo timer de debounce (o caminho de TODA mensagem normal), `buf.timer` É o próprio task que está executando `_flush`. Ele cancelava a si mesmo; o `CancelledError` era entregue no primeiro ponto de suspensão, que é justamente o `await cb(...)` que despacha o turno; e o `except Exception` não pega `CancelledError` (é `BaseException` desde o 3.8). O turno morria calado. O caminho `flush_now` (slash) não tinha o bug: ali `buf.timer` é outro task e o cancel é legítimo — daí só slash funcionar. Bug de origem: `ccaf90e` (Peça A). **Por que a suite não pegou:** `tests/test_edge_assembler.py` já cobria o caminho do timer e passava 9/9 com a produção quebrada — falso verde. O callback de teste era um `async def` que nunca suspende, e um `await` sobre corrotina que não suspende não devolve o controle ao event loop; sem ponto de suspensão, o `CancelledError` marcado nunca era entregue. Em produção o callback faz I/O e suspende.

**Foi feito:**
- **Fix** (`bot/assembler.py`): helper `_cancel_timer(buf)` que cancela o timer pendente **exceto quando ele é o task corrente** (`timer is not asyncio.current_task()`). Substitui os **três** call sites do cancel (`_arm`, poll de fragmento pendente, caminho final do flush). Escolhido sobre "só não cancelar no caminho final" porque cobre também o poll de pendente — que tinha o mesmo self-cancel e só escapava por sorte (retornava antes de qualquer await) — e preserva o cancelamento legítimo do `flush_now`, sem deixar task de sleep pendurado.
- **Blindagem** (`bot/assembler.py`): `except asyncio.CancelledError` em volta do `await cb(...)` → `logger.warning` + **`raise`**. Loga e **propaga**: engolir faria o task ignorar o pedido de cancelamento e poderia travar o encerramento do bot no shutdown — trocaria um bug por outro pior. A partir daqui, um flush que morre por cancelamento deixa rastro no journal em vez de sumir.
- **Testes** (`tests/test_edge_assembler.py`): `_suspending_collector` — callback que suspende (`await asyncio.sleep(0)`) e registra a conclusão DEPOIS do await, matando o falso verde. 4 testes novos: flush por timer completa o callback (o repro do bug), flush_now completa, poll de pendente completa, e `flush_now` preserva o cancelamento legítimo do timer (guarda contra um fix preguiçoso que só apagasse o cancel).
- **Varredura das outras 3 peças da borda** (pedida no briefing): o padrão de auto-cancelamento é **exclusivo do assembler**. `bot/uploads.py` e `bot/liveness.py` não criam nem cancelam task alguma; a resposta limpa idem; os `create_task`/`cancel` de `telegram_handler.py`, `progress.py`, `claude_runner.py`, `cleanup.py` e `main.py` são todos pai→filho (watchdog de ACK, typing indicator), nenhum self-cancel. Nada corrigido fora de escopo. Ressalva: isso atesta a ausência DESTE padrão, não a corretude geral das outras peças — elas seguem sem teste isolado.

**Testes (ambiente de desenvolvimento):** vermelho-antes-do-verde respeitado — commit `9ca1908` (`[wip]`) traz os testes com 2/13 falhando (os dois caminhos que flusham pelo timer; `test_timer_flush_completes_callback` mostra o callback COMEÇANDO e não completando, a morte na suspensão). Após o fix: `tests/test_edge_assembler.py` **13/13** (standalone e sob pytest). Não-regressão das outras peças: `test_edge_uploads` 9/9, `test_edge_uploads_flow` 5/5, `test_edge_liveness` 5/5, `test_rajada_fifo` ok, `test_edge_clean_response` + `test_ux_resposta_despacho` verdes via pytest (25 passed). Validação final é do operador na prod (flags já ligadas): mandar mensagem normal e o Hal responder.

**Commits:** `9ca1908` (`[wip]`, testes do repro) + o commit desta entrada. Branch `coder/de4b1171`.

**Reversão:** `git revert` do commit do fix (código versionado, sem dado fora do git). Rollback operacional alternativo e imediato: `EDGE_ASSEMBLER_ENABLED=false` + restart — desliga o assembler e a borda volta ao caminho de 1 mensagem = 1 turno, que nunca teve o bug.

### Nova arquitetura de borda — Fase 3 (Peças B+C: Liveness + resposta limpa) (2026-07-14)

**Operador pediu:** Fase 3 (o coração da comunicação). **Peça C** — a resposta final chega LIMPA: o texto de raciocínio ("deixa eu olhar o handler…") não vem grudado; o rascunho vai pro canal de progresso (efêmero) e a resposta limpa no canal principal (norte de UX: a setinha colapsável do Claude Desktop). **Peça B** — o ACK semântico por duração vira GARANTIA da borda: tarefa trivial → responde direto (sem ack); tarefa pesada → recebe no início um "entendi, vou [ação] e já te retorno" consistente E semântico. O aviso enlatado de background é aposentado.

**Por quê:** (C) a concatenação de todos os blocos `text` (fix de 2026-06-01) trazia a prosa pré-ferramenta grudada na resposta — o operador queria isso separado. (B) o ACK era instrução de prompt que o modelo tinha que *lembrar* de cumprir → inconsistente (o "não sei te instruir"); e o "passei pra background" era uma desculpa enlatada que tocava o dia todo (num assistente pessoal com Opus, turno > 30s é o NORMAL, não exceção). A raiz: "ACK" conflava progresso mecânico com ACK semântico. A correção separa QUANDO (borda, determinístico) de O QUÊ (modelo barato, semântico).

**Foi feito:**
- **Peça C** (`EDGE_CLEAN_RESPONSE_ENABLED`, default off): `bot/claude_runner.py` — `run(clean_response=)` particiona os blocos de texto do agente pela ÚLTIMA ferramenta de trabalho; resposta = só o texto PÓS-ferramenta; prosa pré-tool fica fora. Guard anti-engolir (sem texto pós-tool → join completo, nunca vazio — não reintroduz o bug de 2026-06-01) e caso sem-ferramenta idêntico ao legado (papo puro, o mais comum). Housekeeping (TodoWrite/ScheduleWakeup) não move o corte. `bot/progress.py` — `ProgressReporter(show_reasoning=)` mostra a prosa pré-tool ao vivo e efêmera (bufferizada: só vira status se uma ferramenta a seguir de fato, então a resposta de papo-puro nunca é mostrada — sem flicker/dedup).
- **Peça B** (`EDGE_LIVENESS_ENABLED`, default off): `bot/liveness.py` (NOVO) — `write_ack(intent, late=)`: modelo barato (gpt-4o-mini, fora da cota do plano Max) escreve o LIV-ack nomeando a ação; fallback consistente se indisponível (nunca levanta). `bot/telegram_handler.py` — na previsão (classificador crava pesado) a BORDA dispara o LIV-ack e a nota de handoff manda a run de bg NÃO ackar de novo (anti-duplo) + watchdog aposentado; na promoção (~30s, tarefa leve que rendeu) o LIV-ack TARDIO substitui o enlatado (suprimido se o Hal já ackou). O enlatado `_send_background_notice` só roda com a flag off.
- **Reconciliação `CLAUDE.md`** (seção "Avisa antes de agir"): SUAVIZADA (não removida) — nota de reconciliação explicando que, com o Liveness ligado, a borda garante o ack pesado e o modelo não deve duplicar; a regra segue valendo para a flag off e tarefas médias em foreground (senão quebraria o comportamento default). Backup em `.local/CLAUDE.md.backup-antes-liveness-ccaf90e`; doc antes/depois entregue ao operador via kobe-attach.
- `bot/config.py` + `.env.example`: flags `EDGE_CLEAN_RESPONSE_ENABLED` e `EDGE_LIVENESS_ENABLED` (default off).
- Tensão do fix de 2026-06-01 tratada explicitamente (guard anti-engolir + teste de regressão). LEGADO (Chat Manager) não tocado.

**Testes (ambiente de desenvolvimento):** `tests/test_edge_clean_response.py` (5, REAL via fake-claude: clean drop pré-tool, off concatena tudo, papo-puro idêntico, housekeeping não afeta, anti-engolir) + `tests/test_edge_liveness.py` (5: fallback sem key start≠late, usa texto do modelo, tira aspas, vazio→fallback, erro→fallback nunca levanta). **Suíte completa: 126 passaram, 4 falharam** (as 4 pré-existentes do `test_resume`). Validação observável (ACK trivial silencioso, ACK pesado semântico, resposta sem rascunho, nada duplicado) é do operador no staging com as flags ligadas — runbook na entrega.

**Commits:** o commit desta entrada, branch `coder/49b992f1`.

**Reversão:** `EDGE_CLEAN_RESPONSE_ENABLED=false` e `EDGE_LIVENESS_ENABLED=false` + restart voltam ao comportamento de hoje (concatenação + ack model-driven/enlatado). A nota do CLAUDE.md é aditiva (revert do commit a remove). Aditivo e reversível.

### Nova arquitetura de borda — Fase 2 (Peça A: Message Assembler) (2026-07-14)

**Operador pediu:** Fase 2 da nova arquitetura de borda: **agregação de mensagens picadas**. Quando o operador manda o pedido em vários envios curtos ("oi Hal" / "sabe aquele problema…" / "então é o seguinte"), a borda deve esperar ele terminar de falar e responder ao pedido INTEIRO num turno só — não 3 respostas a fragmentos. Isso também correlaciona anexo+instrução na mesma janela e corta o nº de turnos pesados.

**Por quê:** a borda colava 1 mensagem = 1 turno = 1 chamada do modelo. Sem agregação: pensamento picado virava N turnos; instrução + anexo em mensagens separadas nunca se encontravam; cada fragmento cruzava o teto de tempo e disparava mais background. Debounce por tempo é a resposta (o "debouncer" clássico) — o Telegram NÃO entrega "usuário digitando" pro bot, então é inferência por ritmo, não observação real (constraint dura, documentada).

**Foi feito:** (tudo atrás de `EDGE_ASSEMBLER_ENABLED`, default off)
- `bot/assembler.py` (NOVO): `MessageAssembler` — buffer de debounce por tópico ANTES do FIFO. `reserve()` carimba a ordem de chegada de forma SÍNCRONA (antes do preparo); `fill()` preenche o slot com o texto pronto e (re)arma o debounce; `release()` libera slot abortado (transcrição falhou) sem travar; `flush_now()` força flush (ex.: comando slash). Janela ADAPTATIVA (frase terminada em pontuação → janela curta) + teto de espera máximo. **Ordem preservada mesmo com fill fora de ordem** (voz lenta reservada 1º, preenchida por último, ainda concatena na posição certa) — mesmo invariante que o ticket FIFO garante, sem regredir.
- `bot/telegram_handler.py`: `on_text` (não-slash) e `on_voice` passam a alimentar o Assembler quando ligado; slash flusha o buffer e processa imediato. Refatoração: transcrição extraída pra `_download_and_transcribe` (compartilhada pelo caminho Assembler e o legado por ticket — sem duplicar). `on_photo`/`on_document` com caption roteiam a caption pelo Assembler (agrega/ordena com texto vizinho; anexo drenado no flush). Singleton `_get_assembler` + `_make_flush_cb` (o flush roda o turno agregado dentro da seção crítica do FIFO: um flush = um ticket = um turno).
- `bot/config.py`: flags `edge_assembler_enabled` + janelas tunáveis (`EDGE_ASSEMBLER_QUIET_MS`=2500, `QUIET_TERMINATED_MS`=700, `MAX_WAIT_MS`=9000).
- Invariantes preservados: ordem de chegada por tópico (Assembler senta ANTES do FIFO; `test_rajada_fifo` segue verde), isolamento entre tópicos, transcrição de áudio fora da seção crítica.

**Testes (ambiente de desenvolvimento):** `tests/test_edge_assembler.py` (9) — agregação na janela, bursts separados → flushes separados, **ordem preservada com fill fora de ordem**, release sem travar, release-total sem vazar, isolamento entre tópicos, teto de espera força flush, flush_now, pontuação final dispara janela curta. **Suíte completa: 116 passaram, 4 falharam** (as 4 pré-existentes do `test_resume`, não introduzidas aqui). Validação observável (mandar 3-4 envios curtos → 1 resposta) é do operador no staging com a flag ligada — runbook na entrega.

**Commits:** o commit desta entrada, branch `coder/49b992f1`.

**Reversão:** `EDGE_ASSEMBLER_ENABLED=false` + restart volta ao disparo imediato (1 msg = 1 turno) sem tocar código. Ou `git revert`. Aditivo e reversível.

### Nova arquitetura de borda — Fase 1 (Peça D: anexos multimodais) (2026-07-14)

**Operador pediu:** transformar a borda do Kobe (a camada Python que atende no Telegram) de um "cano que repassa" num "balcão que atende". Fase 1 das 4 peças do handoff-brief da missão `2026-07-09-desenhar-uma-nova-arquitetura-borda-4`: **anexos**. Aceitar qualquer arquivo/imagem (paridade single-tenant com o Claude Desktop), salvar numa pasta `uploads/` própria + catálogo central legível, e fazer a imagem/arquivo chegar ao modelo no mesmo turno da instrução. Fatiado por risco (D primeiro, menor risco); tudo atrás de flag default-off.

**Por quê:** a borda antiga só aceitava `.txt/.md/.pdf/.docx` (peneira por extensão), forçava tudo pra `.md`, jogava na `knowledge/` curada do tópico, ignorava foto (sumia sem feedback) e descorrelacionava anexo da instrução (a legenda era descartada; o upload só virava contexto estático na PRÓXIMA msg). Resultado: "manda a imagem numa msg + 'faça X com ela' em outra" nunca se encontrava, e imagem nem chegava ao Claude (que é multimodal e lê imagem por path — faltava a borda entregar o caminho).

**Foi feito:** (tudo atrás de `EDGE_UPLOADS_ENABLED`, default off)
- `bot/uploads.py` (NOVO): Normalizer multimodal — `classify_kind` (imagem/documento/outro), `extract_text_from_bytes` (movido de `telegram_handler._extract_text` pra ser compartilhado sem import circular), `ingest_upload` (salva o ORIGINAL, extrai texto se doc, cataloga), `render_attachments_section` (injeta `[Anexos deste turno]` no prompt — imagem via Read, doc com texto inline). Catálogo central ÚNICO e agnóstico de tópico em `user-data/uploads-catalogo.md` (markdown legível pro operador ver/gerenciar/apagar e liberar espaço).
- `bot/topic_manager.py`: `topic_uploads_dir` + `unique_upload_path` (pasta `uploads/` separada do `knowledge/`, EXTENSÃO ORIGINAL preservada, dedupe `-2/-3`).
- `bot/telegram_handler.py`: handler `on_photo` (NOVO — foto comprimida, hoje ignorada); `on_document` reescrito atrás da flag (aceita QUALQUER tipo, salva em `uploads/`, captura `caption`); `_handle_media_upload` comum às duas origens; buffer de anexos pendentes por tópico (`_push_pending_upload`/`_drain_pending_upload`) que correlaciona anexo↔instrução dentro da seção crítica do FIFO (ordem preservada). Guards de RECURSO mantidos (teto de download 20MB, teto de texto extraído) — não é peneira de tipo. Intercept Apolo (`.vcf/.csv`) preservado.
- `bot/claude_runner.py`: `build_prompt` ganha `attachments_section` (injetado colado à mensagem nova).
- `bot/config.py`: flag `edge_uploads_enabled` (`EDGE_UPLOADS_ENABLED`, default off). `bot/main.py`: registra `on_photo` (`filters.PHOTO`).
- Componentes LEGADO (Chat Manager) e [a confirmar] (compactor/snapshot/hindsight) **não tocados**.

**Testes (ambiente de desenvolvimento):** `tests/test_edge_uploads.py` (8) + `tests/test_edge_uploads_flow.py` (5) — helpers de path (uploads/ separado, extensão preservada, dedupe), `classify_kind`, `ingest_upload` (original cru + extração + catálogo único com header uma vez), `render_attachments_section`, buffer push/drain (ordem + isolamento entre tópicos), `build_prompt` injeta anexos antes da msg nova. **Suíte completa: 107 passaram, 4 falharam** — as 4 falhas são do `test_resume.py`, **pré-existentes** (fakes `SimpleNamespace`/`FakeClaude` desatualizados, faltam `curated_core`/`working_memory_enabled`; confirmado idêntico no dev tree limpo em 1358f7e), não introduzidas por esta mudança. Validação observável (foto funciona, anexo+instrução no mesmo turno, catálogo) é do operador no staging com a flag ligada — runbook na entrega. Rodar: `.venv/bin/python -m pytest tests/ -q`.

**Commits:** `89727e3` (wip Fase 1a) + o commit desta entrada (Fase 1b). Branch `coder/49b992f1` (worktree isolado; merge pro dev é do operador, no rito §13.1).

**Reversão:** `EDGE_UPLOADS_ENABLED=false` + restart volta ao comportamento legado (on_document `.md`→knowledge/, foto ignorada) sem tocar código. Ou `git revert` dos commits da branch. Aditivo e reversível.

### Trava 2 — pedido de código ⇒ sessão Coder, sempre (regra dura no CLAUDE.md) (2026-07-09)

**Operador pediu:** garantir que, sempre que ele pede pra codificar algo (escrever/refatorar/corrigir código) — em qualquer forma, e sobretudo quando usa a palavra "Coder" — o Hal **abra uma sessão Coder**, nunca code na mão no próprio turno, nunca reinterprete o pedido de código de outro jeito. Exceção única: o operador dizer EXPLICITAMENTE que NÃO quer sessão Coder. Travar o rito de disparo do lado do Hal (não mexer em runtime de nada). Decisão do operador: enforcement **só via regra no CLAUDE.md** (sem hook `PreToolUse` no Hal).

**Por quê:** a decisão "isto é pedido de código?" é julgamento sobre a INTENÇÃO da mensagem, que só o Hal (LLM) lê no início do turno — um hook vê chamadas de ferramenta, não intenção, e o Hal (agente mais externo) não tem canal de aprovação externa como o Coder tem, então um hook no Hal ou fica rígido demais (bloqueia edições legítimas de memória/knowledge) ou é auto-bypassável (inútil). Logo a trava certa e durável é o **system prompt do Hal** (o `CLAUDE.md` do Kobe), que ele lê todo turno. Além disso, o próprio CLAUDE.md tinha trechos que **mandavam** o Hal codar na mão ("Criação de projeto novo: crie a pasta, monte a estrutura…", "Continuação de projeto: retome de onde parou") — conflito a reconciliar.

**Foi feito:** (só `CLAUDE.md`)
- Nova seção **"Pedido de código ⇒ sessão Coder, sempre (regra dura)"** antes de "Plugins": caminho único pro Coder em pedido de código; exceção única (operador dispensar o Coder explicitamente); escopo preciso do que é "código" (runtime: `bot/`, `plugins/`, `infra/`, `keyko/`, scripts, `projetos/` de código); e o que a regra NÃO alcança (memória/identidade/knowledge, projeto não-código, ler/greppar código, docs puras). "Na dúvida, pergunte" nos dois lados.
- Reconciliados os três subtópicos de "Comportamento por tipo de solicitação" que contradiziam a regra: "Criação de projeto novo" e "Continuação de projeto" passam a rotear trabalho **de código** pro Coder e manter só o **não-código** com o Hal; "Disparo de processo empacotado" esclarece que **rodar** um pipeline existente segue com o Hal, mas **mexer no código** dele é Coder.

**Testes (ambiente de desenvolvimento):** mudança é de instrução (governa o comportamento do Hal, um LLM) — não tem teste unitário automatizável. Verificação = revisão do texto + reconciliação dos conflitos internos do CLAUDE.md (feita) + runbook de cenários pro operador validar no uso real (pedido de código → dispatcha; "edita você mesmo" → coda na mão; editar knowledge/memória → segue direto; rodar pipeline → segue direto). Não há como um hook decidir "é pedido de código?" de forma confiável (indecidível por código) — por isso a decisão do operador de ficar só na regra dura.

**Commits:** não commitado nesta sessão (proibido por rito da missão — deploy é do operador, na mão).

**Reversão:** aditiva/localizada — `git revert` do commit quando houver, ou remover a seção nova e restaurar os três subtópicos. Nenhum estado/runtime envolvido (é só texto de instrução).

### Fix — Mission Control: monitor da sala + contagem de slot (2026-07-07)

**Problema:** duas falhas silenciosas no Mission Control. (1) O monitor da sala era
ligado com um argumento `kobe_home=` que a função-alvo não aceita — a exceção matava
o worker enquanto o Claude da sala seguia vivo, congelando o status numa mentira
(sensor morto). (2) A regra de "sala ocupa slot" confiava só no campo de status, então
uma sala presa em `running` com worker morto travava o slot indefinidamente.

**Foi feito:**
- `bot/mission_control/sala_worker.py`: remove o argumento `kobe_home=` inválido das 2
  chamadas do monitor (confirmado contra a assinatura real da função).
- `bot/sala/cleanup.py`: "ocupa slot" agora exige status `running` **E** worker vivo de
  verdade (checagem de PID) — sala com worker morto não trava mais slot. Regra sagrada
  "retomar nunca barra" preservada e coberta por teste.
- Testes novos cobrindo a transição `running→idle` do monitor (antes sem cobertura).

### Docs — Mission Control: guia + runbook + README (2026-06-26)

**Operador pediu:** commit 7 — documentação do Mission Control.

**Foi feito:**
- `docs/missoes.md` → `docs/mission-control.md` (git mv): título "Mission Control", seção
  "Duas formas", e a seção **Sala estrategista** (abrir por linguagem natural, roteamento
  que não captura o tópico, encerrar só pelo operador nos dois canais, handoff, layout
  `workspace/` + flag). A forma fan-out (`/missao*`) segue documentada abaixo.
- `docs/runbooks/keyko-e-missoes.md`: subseção "Mission Control — sala estrategista" (flag
  `MISSION_CONTROL_SALA_ENABLED`, validação no prod VPS, encerrar só por ato do operador,
  CLI de debug, layout da sala, nota da migração do Coder como follow-up).
- `README.md`: referência atualizada `docs/missoes.md` → `docs/mission-control.md`.

**Nota:** o doc de arquitetura na KB (`user-data/knowledge/kobe/arquitetura/
08-sistema-missoes-keyko.md`) é gitignored e não sincroniza via git — atualização fica como
tarefa de memória do lado prod (não entra neste commit).

**Testes:** N/A (docs). Links/âncoras conferidos.

### Feat — Mission Control: handoff "nasce aqui → vira Coder" (2026-06-26)

**Operador pediu:** commit 6 — handoff condicional + semi-manual (decisões 4/8): quando a
missão vira "vamos construir X", o estrategista prepara um brief, PARA no "go" do operador,
e só então dispara o Coder no projeto-alvo. O "go" vale pelos dois canais.

**Foi feito:**
- `bot/mission_control/handoff.py` — `coder_run_remote` (resolve o CLI do plugin Coder, ou
  None se não instalado), `build_handoff_command` (puro), `disparar` (lê
  `workspace/handoff-brief.md`, valida brief/Coder/cwd, dispara o Coder com o brief como
  `--task` e o projeto-alvo como `--cwd`) + CLI.
- `bot/mission_control/sala_prompt.py` — o estrategista, no "go", roda
  `.venv/bin/python -m bot.mission_control.handoff disparar --missao <id> --cwd <alvo>` e
  avisa 🤝 [mission]. Condicional: missão não-código ignora o handoff.

**Testes (dev VPS):** `tests/test_mission_control_handoff.py` — `build_handoff_command`
(puro) e os guards de `disparar` (brief inexistente/vazio, Coder ausente, cwd inexistente —
tudo antes de invocar o Coder). Verdes. O dispatch real do Coder é validado no prod VPS.

**Reversão:** `git revert`; só é alcançável de dentro de uma sala (flag on).

### Feat — Mission Control: entrada por linguagem natural + roteamento via Hal (2026-06-26)

**Operador pediu:** commit 5 — a porta da sala estrategista por linguagem natural (decisão
7) e o roteamento de mensagens, com a regra fechada por ele: a sala **não captura o canal**
(default = conversa com o Hal); repasse só por ato explícito do operador, e se o Hal apenas
desconfia, **pergunta antes**. Encerramento e aprovação valem pelos **dois canais
equivalentes** (Telegram via Hal OU direto na sala).

**Por quê:** a decisão de rotear/confirmar é do Hal (LLM), não do código da sala — então o
handler crítico **não ganha bloco de roteamento**; só uma injeção de ciência read-only.
Isso de-risca a integração no fluxo de mensagens vivo.

**Foi feito:**
- **`CLAUDE.md` (instruções do Hal — coração do commit):** nova seção "Mission Control —
  salas de missão": abrir por linguagem natural (`sala_dispatch abrir`), regra dura de
  roteamento (sala não captura o canal; repasse só explícito; desconfiou → pergunta),
  encerrar só por ato do operador (dois canais), handoff/go pelos dois canais.
- **`bot/telegram_handler.py` (única mudança no handler — ADITIVA, read-only, atrás da
  flag):** injeta `[Sala de missão ativa neste tópico: …]` no prompt do Hal via
  `sala_dispatch.render_sala_ativa`. **Sem desvio de fluxo** — não intercepta mensagens.
- **`bot/claude_runner.build_prompt`:** novo param `sala_ativa_info` (renderiza a linha de
  ciência; omitido quando ausente).
- **`bot/mission_control/sala_dispatch.py`:** `render_sala_ativa` (ciência + instrução de
  não-repasse), `encerrar_sala` (marca `encerrada` + mata tmux — ato explícito), CLI ganha
  `encerrar` e defaults de `--chat-id/--thread-id` do env. Faxina passa a `ttl_hours=None`.
- **Ressalva "só o operador fecha" costurada no ciclo de vida:**
  - `bot/sala/cleanup.should_kill`: `ttl_hours=None` desliga o fecho-por-idade — Mission
    Control nunca encerra sala viva por inatividade; a faxina só reaproveita tmux de salas
    já encerradas/mortas.
  - `bot/sala/room.monitor_sala`: guarda de status terminal — não confunde fecho intencional
    (`encerrada`) com morte (não dispara `on_death`) e não sobrescreve status terminal com
    `idle`.
  - `bot/mission_control/sala_prompt.py`: estrategista sabe encerrar só a pedido do operador
    (dois canais) e tratar o "go" pelos dois canais.

**Testes (dev VPS):** `test_sala_core.py` (+`should_kill` com `ttl_hours=None`, +2 testes do
monitor: guarda terminal e reporte de morte) e `test_mission_control_sala.py`
(+`render_sala_ativa`, +`encerrar_sala`; `_ttl_hours` removido). Todos verdes;
`build_prompt` renderiza/omite `sala_ativa_info`; `import bot.telegram_handler` OK.
Comportamento de tmux/claude vivo validado no prod VPS atrás da flag.

**Reversão:** `git revert`; flag off (default) deixa tudo inerte (a injeção de ciência só
ocorre com flag on + sala ativa).

### Feat — Mission Control: sala estrategista (forma b) sobre `bot/sala/` (2026-06-26)

**Operador pediu:** a sala-única estrategista — prioridade do plano (forma b): janela
longa de raciocínio numa sala visível, prompt de estrategista (não dev), sem gates de
codificação, com handoff condicional pro Coder. Commit 4 da sequência.

**Por quê:** trazer a visibilidade do Coder pro orquestrador, mas pra PENSAR (analisar
pesquisa, estratégia, encadear raciocínio), não pra codar — a missão é um turno longo de
raciocínio (decisão 1), roda em bypass de verdade sem rito do Coder (decisões 3/4).

**Foi feito (camada A — código do core):**
- `bot/mission_control/sala_prompt.py` — `sala_name` (prefixo `mission-`), o **system
  prompt de estrategista** (conversa, registra raciocínio em `workspace/raciocinio.md`,
  handoff condicional/semi-manual que PARA no "go" do operador, honestidade, disciplina de
  turno) e o brief de abertura.
- `bot/mission_control/sala_worker.py` — worker detached que usa o núcleo `bot.sala`:
  `_start` escreve sysprompt+brief, abre a sala via `room.open_sala` com
  **`settings_path=None` (bypass, sem guard)** e monitora; `_resume` injeta input via
  `room.resume_deliver` e trata os outcomes. Notify com prefixos `[mission]` (🧭/💡/🤝/🟡/🟢).
- `bot/mission_control/sala_dispatch.py` — `abrir_sala`/`retomar_sala` + flag
  `MISSION_CONTROL_SALA_ENABLED` (default off), faxina por TTL e teto de salas ativas
  (`MISSION_CONTROL_MAX_SALAS`, default 2), `find_sala_ativa` (localiza sala viva do tópico
  por `sala.json`). **Não cria `estado.json`** nesta fase pra não ligar a triagem headless
  antiga — o roteamento das msgs do tópico pra sala é o commit 5.
- `bot/mission_control/storage.py` — paths da sala (`sala.json`, `sala.sysprompt.txt`,
  `sala-launch.sh`, `sala.log`) + `workspace/` (`ensure_workspace` cria `rascunhos/`).

**Testes (dev VPS):** `tests/test_mission_control_sala.py` — 8 testes: nome da sala,
prompt+brief (prefixos, handoff condicional, bypass), layout/workspace, flag+tuning,
`find_sala_ativa`, guards de `abrir_sala` (flag off / objetivo vazio / limite — retornam
antes de spawnar), e **wiring do worker `_start`** (tmux monkeypatchado: escreve
sysprompt+brief, spec sem `--settings`, patcha status=running+pid). Todos verdes; core
(`test_sala_core.py`) sem regressão. O launch real (tmux+claude) é validado no prod VPS
atrás da flag — o bot não roda no dev VPS.

**Reversão:** `git revert`; ou flag off (default) deixa tudo inerte. Pacote novo, nada no
fluxo existente chama a sala ainda (entrada NL é o commit 5).

### Refactor — rename do pacote `bot/missoes/` → `bot/mission_control/` (2026-06-26)

**Operador pediu:** rename "pra valer" do Sistema de Missões pra Mission Control (commit 3
do plano aprovado, decisão 5). **Comandos slash `/missao*` ficam como estão** (decisão 6 —
operador quase não usa slash e não quer mudar muscle-memory).

**Por quê:** consistência do nome no projeto inteiro (arquivos + referências de import),
preparando o terreno pra a sala estrategista nascer já em `bot/mission_control/`.

**Foi feito:**
- `git mv bot/missoes bot/mission_control` (histórico preservado por arquivo).
- Imports internos do pacote (`bot.missoes` → `bot.mission_control`) e refs de path em
  docstrings (8 arquivos).
- Imports externos atualizados: `bot/keyko/registry.py`, `bot/main.py`, `bot/resume.py`,
  `bot/telegram_handler.py`; comentários em `bot/alertas/storage.py`; runbook
  `docs/runbooks/keyko-e-missoes.md`.
- **Mantidos (decisão 6 / decisão de runtime):** comandos `/missao*`, nomes de função
  `on_command_missao*`, classe `MissoesSource` e seu `nome="missoes"`, e o path on-disk
  `user-data/missoes/` (renomear o path orfanaria estado existente em prod).

**Testes (dev VPS):** `py_compile` de todo `bot/` OK; `import bot.mission_control` +
submódulos OK; `import bot.keyko.registry` e `bot.resume` (que importam o pacote) resolvem.
`grep` confirma zero referência remanescente a `bot.missoes`/`bot/missoes` no código.
`tests/test_resume.py` falha por motivo PRÉ-EXISTENTE (`config.working_memory_enabled`
ausente no fake do teste) — verificado idêntico no `main`, não é regressão deste rename.

**Reversão:** `git revert` do commit (renames + edits de import são revertíveis em bloco).

### Feat — Mission Control: núcleo de sala extraído pro core `bot/sala/` (2026-06-26)

**Operador pediu:** upgrade do Mission Control (Sistema de Missões/Keyko) trazendo a
visibilidade do Coder (sala tmux `--remote-control` + kobe-notify por marco) pro
orquestrador. Plano aprovado em `.local/plano-mission-control.md` (9 decisões). Este é o
**commit 1** da sequência: extrair a maquinaria de sala pra core (decisão A1).

**Por quê:** Mission Control é **core**, mas a mecânica de sala visível só existia no
**plugin Coder** (`plugins/public/coder/scripts/coder_worker.py`). Core não pode importar
de um plugin (plugin é opcional, repo separado) — então a sala é extraída pra core, de
onde tanto o Mission Control (agora) quanto o Coder (migração = commit 2, follow-up) a
usam. Uma fonte só de verdade.

**Foi feito:**
- Novo pacote **`bot/sala/`** — toolkit genérico e sem opinião sobre quem usa:
  - `state.py` — read/write atômico (tmp+rename) + `patch_state` com flock no
    read-modify-write (mata o lost-update entre workers concorrentes, incident-hardened
    do Coder 2026-06-23).
  - `tmux.py` — wrappers do tmux + helpers PUROS `pane_busy`/`extract_pane_last`.
  - `room.py` — `SalaSpec`, `open_sala` (launcher + new-session), `monitor_sala`
    (status/morte/heartbeat/owner-check), porteiro `wait_pane_idle`, entrega-com-
    confirmação `deliver_to_sala`, `resume_deliver`. **Gates plugáveis:** `settings_path`
    opcional → sem `--settings` = sala em bypass de verdade (caso do Mission Control); o
    Coder injeta o guard. Mensagens ao operador são **callbacks** (núcleo não conhece
    prefixos `[coder]`/`[mission]`).
  - `cleanup.py` — faxina por TTL + contagem de salas ativas, com decisões puras
    (`should_kill`, `is_active`).
- **Plugin Coder intacto** neste commit (de-risk do dispatch vivo de prod). A migração do
  Coder pra `bot/sala/` é o commit 2, declarado como follow-up no plano (§7/§9).

**Testes (dev VPS, venv `$KOBE_HOME/.venv`):**
- `tests/test_sala_core.py` — 10 testes da lógica pura + state atômico: roundtrip/patch,
  escrita atômica sem `.tmp` órfão, `pane_busy`/`extract_pane_last`, `turn_is_over`,
  montagem do launcher (com e sem `--settings`), `should_kill`, `is_active`,
  `count_active` (com `pid_alive` injetado). Todos passam. O comportamento que exige
  tmux/claude vivos (open/monitor/resume) será validado no prod VPS (staging) atrás de
  flag — o bot não roda no dev VPS.

**Reversão:** `git revert` do commit, ou apagar `bot/sala/` + `tests/test_sala_core.py`
(pacote novo, nada importa dele ainda — zero efeito colateral).

### Fix — "digitando…" fantasma quando o turno foreground crasha (2026-06-25)

**Operador pediu:** corrigir o indicador "digitando…" que ficava preso/fantasma no
Telegram quando um turno do Kobe crashava (episódio com `LimitOverrunError` ~12:29).

**Por quê (duas camadas, confirmadas no código):**
- **Gatilho:** o reader do stdout do `claude` (`bot/claude_runner.py`) usava o buffer
  default do asyncio (64KB). O stream-json emite UMA linha por evento; um tool_result
  gordo (ler arquivo grande / fetch) estoura 64KB numa linha só → `readline()` levanta
  `ValueError`/`LimitOverrunError`, que NÃO é `ClaudeError` e derrubava o turno inteiro.
- **Por que o typing ficava preso:** o "digitando…" é um loop (`_keep_typing`) que
  reemite a chatAction a cada 4s. No caminho **foreground** (`_handle_user_text`) o
  `typing_task` era cancelado só no caminho feliz — SEM `try/finally`. Quando o turno
  morria antes do cancel (o `ValueError` cru fazia o próprio `_resolve_claude`
  re-levantar), o loop virava órfão e reemitia "digitando…" pra sempre até o bot
  reiniciar. O caminho **background** já tinha a proteção; a assimetria era o defeito.

**Foi feito:**
- **Buffer (gatilho):** `STDOUT_BUFFER_LIMIT_BYTES = 10MB` passado como `limit=` ao
  `create_subprocess_exec`. + degradação amigável: se mesmo assim uma linha estourar o
  limite, o overrun vira `ClaudeError` (mensagem amigável via `_resolve_claude`) em vez
  de crash — e o subprocess é morto/reapado (sem vazar processo nem `stderr_task`).
- **Blindagem (defeito):** novo context manager `_typing_indicator` em
  `bot/telegram_handler.py` que GARANTE o cancelamento do loop na saída do bloco —
  caminho feliz, `return` da promoção OU qualquer exceção. O foreground agora usa
  `async with`, espelhando a proteção que o background já tinha. Remove os cancels
  manuais duplicados.

**Testes (dev VPS, venv `$KOBE_HOME/.venv`):**
- `tests/test_claude_runner_buffer.py` — fake-claude cuspindo linha JSON > 64KB; passa
  com o fix e (provado por monkeypatch a 64KB) falha sem ele. Teardown do subprocess no
  overrun validado (sem warning de "Event loop closed").
- `tests/test_typing_indicator.py` — cancela o typing na saída normal E quando o corpo
  levanta exceção (o caso do bug).
- Suíte completa: 67 passam, 4 falham — as 4 são DÉBITO PRÉ-EXISTENTE de `test_resume.py`
  (KeyError `curated_core`, do merge Highlander v2; falham idêntico no HEAD limpo, nada a
  ver com este fix).

**Reversão:** `git revert` dos 2 commits; zero migração/estado. Worktree isolado.

### Docs — runbooks: deploy rsync → git

Atualiza `docs/runbooks/keyko-e-missoes.md` e `ux-resposta-ack-despacho.md` pra
refletir a regra vigente "deploy é git, nunca rsync" (prod **puxa a versão** do repo
dev por `git pull`; `.env`/`user-data/` sobrevivem). Substitui os blocos `rsync
--delete` por `git push origin main` (dev) + `git pull` (prod). Mudanças que já
estavam no working tree do dev VPS (não-Highlander); commitadas pra destravar o
`publish.sh` (que exige árvore limpa). `docs/runbooks/` é excluído do repo público.

### Highlander v2 — F0: régua (arnês de regressão) entra no repo

**Operador pediu:** executar o Highlander v2 (redesenho do recall); F0 = "a régua primeiro,
gate de tudo: nada sobe sem responder 'resolve quantos dos casos?'".

**Por quê:** o arnês `infra/eval/` vivia untracked no dev VPS — sem ele no repo, todo
conserto anti-alucinação subia no escuro. A Auditoria da Verdade fixou a regra de ouro:
medir antes e depois.

**Foi feito:** `infra/eval/{harness.py,README.md,.gitignore}` versionados (os `cases/` e
`results/` ficam de fora pelo `.gitignore` interno — contêm trecho real de conversa, são
privados). O arnês reconstrói o prompt via o `build_prompt` REAL do bot e roda `claude -p`
sandbox (`--tools ""`) por caso, medindo se a alucinação reaparece (keyword_any | llm_judge).

**Testes (dev):** `--dry` valida a montagem dos 6 casos-âncora; baseline `--run --n 3`
rodado no worktree com o venv do dev VPS (gasto de Opus autorizado pelo operador). Número
registrado no commit de fechamento da régua.

**Limitação honesta:** o arnês reconstrói histórico + nota de background + `[Agora]` +
contrato (CLAUDE.md); ainda NÃO injeta as camadas Highlander (curated_core / grounding /
recall do Hindsight), que o `build_prompt` recebe como `None` no caminho do arnês. Logo a
régua mede a eficácia do CONTRATO e (na F5) dos GATES — não o efeito do recall do Hindsight.

**Reversão:** `git revert` do commit; o arnês é ferramenta de bancada, zero efeito em runtime.

### Highlander v2 — F5: gates de estado vivo (P1 background + P2 verificável-barato)

**Operador pediu:** a metade da alucinação que o Hindsight NÃO cobre — "estado vivo": o
"você tá dormindo" respondendo mensagem que ele acabou de mandar, e o status de sala/job
narrado de memória. A Auditoria marcou P2 como o MAIOR lever (~66% dos casos).

**Foi feito:**
- **P2 — gate de verificável-barato (estado do operador)** em `bot/memory/grounding.py`: numa
  retomada (gap > 30 min), além do "última msg há ~N", o bloco agora crava o FATO
  VERIFICÁVEL que o código conhece — "o operador acabou de te enviar a mensagem deste turno;
  ele está presente e falando contigo AGORA" — e NOMEIA o atalho proibido ("não afirme que
  está dormindo/ausente/ocupado; você não observa o estado dele"). Mata o caso-âncora "você
  está dormindo" na raiz: o prior do modelo ("tarefa longa → noite → dorme") perde pro fato.
  Atrás da flag existente `GROUNDING_SIGNALS_ENABLED`.
- **P1 — gate de estado de background vivo** (novo `bot/memory/background_state.py`): a cada
  turno o código LÊ os arquivos de estado dos trabalhos de background do tópico (Coder
  `coder-sessions/<thread>/*.json`), carimba a idade (last_activity, janela de 6h, cap 6) e
  injeta `[Estado de background vivo — LIDO AGORA]` com `state=` real + idade + a regra dura
  "use ISTO, não memória; o que não está aqui provavelmente terminou". É o conserto que o
  operador pediu pro usuário 2: o estado vivo EMPURRADO pelo código (como `[Alertas
  aguardando confirmação]`), não instrução mole que depende do agente lembrar de olhar.
  Novo param `background_state` no `build_prompt` (renderizado junto do grounding). Flag
  `BACKGROUND_STATE_GATE_ENABLED` (default-on, no-op sem trabalho recente).
- **Régua faithful ao P2:** `infra/eval/harness.py` passa a preservar `created_at` e injetar
  o grounding — assim casos COM timestamp medem o gate, não só o contrato.

**P5 (calibrar o daemon detector) = N/A:** o detector vivo é o daemon do Chat Manager, que a
F1 aposentou (`CHAT_MANAGER_ENABLED=false`) — não há daemon a calibrar.

**Honestidade sobre a régua (por que não há "resolve quantos" novo):** o baseline (1/18 = 6%)
já PASSA os casos de estado-do-operador (o contrato segura na reconstrução mínima do arnês);
o único que falha (`tenta-de-novo-receita`) é inércia-de-contexto (família F2), que NENHUM
gate de F5 mira (era território do P5, agora N/A). E os casos não têm timestamp, então o
arnês não dispara o P2. Ou seja: **a régua é estruturalmente cega aos gates** (cases
sintéticos, prompt mínimo, sem timestamp) — eles atacam modos de falha de PROD que o arnês
sub-reproduz. Validação real: unit tests (abaixo) + prod (operador). Não gastei Opus
re-rodando a régua pra reproduzir o mesmo 1/18.

**Testes (dev):** unit completo (`.local/test_f5.py`, fora do git): P2 dispara no gap com o
fato de presença + nomeia "dormindo"; gap curto → None. P1 lê os JSONs, filtra job velho
(>6h), ordena por idade, renderiza running+completed com a regra dura; sem job / thread None
→ None. `py_compile` de todos os 6 arquivos. Harness roda `--dry` limpo pós-edição.

**Reversão:** `GROUNDING_SIGNALS_ENABLED=false` (P2) e `BACKGROUND_STATE_GATE_ENABLED=false`
(P1); `git revert` desfaz. Read-only, nada destrutivo.

### Highlander v2 — F4: janela imediata bounded por TOKEN (anti-rajada-de-áudio)

**Operador pediu:** janela com teto de TOKEN — "hoje o teto é 60 mensagens; uma rajada de
áudios longos estoura" (queima o teto de 5h e dilui o contrato).

**Foi feito:**
- `bot/memory/working_set.py`: além do piso híbrido (10 min OU N msgs) e do hard cap de 60
  MENSAGENS, agora há `IMMEDIATE_TOKEN_CAP` (default 8000 tokens, env
  `WORKING_MEMORY_TOKEN_CAP`). `_bound_by_tokens` mantém as msgs mais RECENTES que cabem no
  teto e descarta as mais antigas da janela. Garante ao menos a última msg (cortar o
  contexto imediato do turno seria pior que o estouro). Estimativa barata (~4 chars/token,
  sem tokenizer). Cap ≤ 0 desliga (volta ao pré-F4).
- O núcleo curado (CURATED_CORE) já fica no TOPO do prompt (Frente 1.2) — a metade
  "núcleo estável cacheável" do plano já estava satisfeita.

**Deferido honestamente (precisa de observação/experimento, não de código pontual):**
- **P0b — cache do prefixo:** medir `cache_read_input_tokens`/`input_tokens` por turno
  exige `claude -p` em `--output-format` com usage + ~1 semana de telemetria. Mexer no
  formato de saída arrisca o parsing da resposta do bot — NÃO toquei. Fica como
  instrumentação a fazer com calma (não bloqueia o resto).
- **P6 — ENCOLHER os ~58 KB:** é experimento (subtrair texto do contrato e medir pela
  régua se a alucinação cai), não edição cega. Fica pra uma rodada própria com a régua.

**Testes (dev):** unit do `_bound_by_tokens` — under-cap mantém tudo; over-cap corta os
mais antigos preservando os recentes; última msg gigante sozinha é mantida; ordem
cronológica preservada; cap=0 vira no-op; vazio → vazio. `py_compile`.

**Reversão:** `WORKING_MEMORY_TOKEN_CAP=0` desliga o teto; `git revert` desfaz.

### Highlander v2 — F3: Hindsight assume o recall (recall cru + reflect citado)

**Operador pediu:** "uma roda de recall só (Hindsight)"; recall cru pro caminho barato,
reflect citado pro caminho confiável; aposentar o kobe-recall (vai junto com o CM).

**Foi feito:**
- **recall (caminho barato)** já wired no turno, atrás de `HINDSIGHT_RECALL` (sub-flag da
  F1). Re-ligar = `HINDSIGHT_RECALL=true` + restart. **Mantido OFF no deploy** (ver nota).
- **reflect (caminho confiável)**: novo helper `bot/bin/kobe-reflect "<pergunta>"` — resposta
  sintetizada e CITADA (`based_on.memories`) do bank do tópico atual, cético por construção
  (skepticism=5/literalism=5 + directive de Fundamentação). Resolve o tópico via
  `get_topic_slug` (KOBE_CHAT_ID/THREAD_ID) → bank `kobe-<slug>`. Best-effort: serviço fora =
  avisa e sai. Quando não há registro, diz "não há base; não afirme de memória" (em vez de
  confabular). `reflect_mission` força resposta em português.
- **kobe-recall aposentado junto com o CM**: o helper depende das tabelas `conversations`
  (populadas pelo daemon do Chat Manager). Com `CHAT_MANAGER_ENABLED=false`, o daemon fica
  inerte → o kobe-recall degrada (sem dado novo). O papel de recall durável passa pro
  Hindsight. O script fica (aposentar = desligar, não remover).

**Nota honesta sobre a régua (por que recall fica OFF no deploy):** o arnês `infra/eval/`
NÃO injeta o bloco de recall (o `build_prompt` recebe `durable_memory=None` no caminho do
arnês), e os casos são conversas sintéticas SEM memória durável correspondente — então a
régua **não consegue medir** o efeito do recall. O plano (§6) é explícito: "não religar a
injeção de recall sem a régua medir (risco da dor nº1)". Logo: o código está pronto e
reversível, mas o FLIP `HINDSIGHT_RECALL=true` fica pro operador validar em prod (onde há
memória real) — é a validação-de-produto dele, não automatizável aqui. O reflect (helper)
não tem esse risco (é on-demand, cético+citado, diz "não sei" sem inventar).

**Testes (dev, serviço vivo):** `kobe-reflect` sem arg → usage; com pergunta → reflete
contra o bank, devolve síntese citada ou "sem registro" (testado: respondeu "não tenho
informação" em vez de confabular). `py_compile` do helper + client.

**Reversão:** `HINDSIGHT_RECALL=false` (já é o default) mantém o recall mudo; `git revert`
remove o helper. Nada destrutivo.

### Highlander v2 — F2: re-fia o Hindsight pro best-practice (0.8.3)

**Operador pediu:** corrigir a fiação do Hindsight, que estava fora do manual (retain de
mensagem solta com id aleatório, sem context/tags, bank sem missão nem disposição).

**Por quê:** o anti-padrão do plano (§6) — "retain de mensagem solta com UUID aleatório
duplica documento; usar id estável" — era exatamente o que acontecia. E o bank não era
cético por construção, então o reflect (a peça-ouro anti-alucinação) não tinha como
"só responder do que está citado".

**Foi feito (verificado contra a API 0.8.3 ao vivo):**
- **retain agrupado:** `document_id` ESTÁVEL (= `session-<id>`) + `update_mode="append"` —
  a conversa vira UM documento que cresce, não N memórias soltas. + `context` ("Conversa
  Telegram, tópico X") e `tags` (`topic:<slug>`, `source:telegram`). Conservador de
  propósito: só a msg DO OPERADOR (ground truth) — NÃO a resposta gerada (anti-confabulação).
  A tensão "conversa inteira × só-operador" está documentada no módulo: a resolução grupa a
  conversa por id estável (conserta a duplicação, o defeito real) sem gravar texto gerado.
- **bank configurado (idempotente, 1× por processo via `_ensure_bank`):** disposições
  `skepticism=5`, `literalism=5` (cético+literal por construção) + `retain_mission` /
  `reflect_mission` (PATCH `/config`) + uma **directive** `kobe-fundamentacao` (POST
  `/directives`, criada só se não existe) que codifica a regra de Fundamentação como regra
  dura injetada em todo reflect.
- **recall melhorado:** `types=['world','experience']`, `budget='mid'`, filtro por `tags`,
  `include.source_facts` (rastreabilidade `document_id`/`chunk_id` em cada resultado).
- **reflect novo:** `reflect()` + `render_reflect_section()` — resposta sintetizada CITADA
  (`based_on.memories`), pro caminho confiável da F3.
- Gotcha 0.8.3 corrigido: `include.{source_facts|facts}` liga com `{}` (objeto vazio), não
  `true` (bool dá 422).

**Testes (dev, contra o serviço VIVO):** bank de teste isolado (`kobe-codertest-f2`):
retain (append, id estável) → recall (1 fato, doc=session-999, com tipo `experience`) →
reflect ("projeto é Kobe", `based_on:2` citações) → confirmado no `/config` que
skepticism/literalism=5 + as duas missões + a directive ficaram aplicados. Bank de teste
removido (DELETE 200) — Hindsight de prod intacto. `py_compile` de client+handler.

**Reversão:** `HINDSIGHT_RETAIN=false` para de gravar; `git revert` volta o client. As
disposições/missão/directive são config idempotente por bank — sem efeito destrutivo.

### Highlander v2 — F1: aposenta o Chat Manager + de-risca o recall do Hindsight

**Operador pediu:** aposentar o Chat Manager (reversível, sem remover código nem dropar
tabela) e parar de injetar o destilado do Hindsight todo turno, sem perder a construção da
memória.

**Por quê:** (1) o Chat Manager virou "armadilha do ponteiro" (título de assunto sem
conteúdo → o agente inventa o que tinha lá) e incha o prompt (quente/frio todo turno →
queima o teto de 5h). (2) o Hindsight estava com retain E recall na MESMA flag, ligados;
o recall injeta um bloco destilado por LLM a cada turno — a própria Auditoria nomeia
destilação automática como vetor de confabulação (a dor nº1).

**Foi feito:**
- `HINDSIGHT_ENABLED` continua como MASTER kill-switch; separadas duas sub-flags:
  `HINDSIGHT_RETAIN` (default ON — segue gravando em silêncio) e `HINDSIGHT_RECALL`
  (default OFF — para de injetar o destilado). Efetivo = master AND sub-flag.
  `bot/config.py` (campos + parse), `bot/telegram_handler.py` (gate do retain ~796 e do
  recall ~859), `.env.example` documentado.
- Chat Manager aposentado por flag: `CHAT_MANAGER_ENABLED=false` no prod `.env` (aplicado
  no deploy). O código fica; o daemon classifier vai inerte (checa a flag no tick); a
  janela imediata (working_memory, default ON) e o núcleo curado seguem intactos —
  decouple da Frente 0 garante que CM-off NÃO traz a compactação/amnésia de volta
  (`_load_history` keya em `working_memory_enabled`, não em `chat_manager_enabled`).

**Testes (dev):** `py_compile` de config+handler; teste dos novos campos no `Config` e dos
defaults (retain=ON, recall=OFF); varredura confirmando que a janela imediata e a
compactação não dependem de `chat_manager_enabled` (só de `working_memory_enabled`).

**Reversão:** `CHAT_MANAGER_ENABLED=true` religa o CM; `HINDSIGHT_RECALL=true` religa o
recall; `HINDSIGHT_ENABLED=false` desliga retain+recall. Tudo por env + restart, sem deploy.

### Corrigido — Highlander Frente 0: desacopla MEMÓRIA da flag de CONVERSAS

**Operador apontou:** "Chat Manager virou outra coisa, apenas classificação e gerenciamento
de conversas, não tem mais código sobre memória lá."

**Causa:** a Frente 0 moveu o *código* da memória pra `bot/memory/`, mas deixou o *controle*
ainda pendurado em `CHAT_MANAGER_ENABLED`: duas decisões de MEMÓRIA — qual janela de
histórico usar (imediata vs sessão legada) e se a compactação roda — pegavam carona na flag
de CONVERSAS. Spaghetti residual: trocar de assunto (conversa) e escolher janela de memória
estavam amarrados na mesma chave.

**Foi feito:** nova flag `WORKING_MEMORY_ENABLED` (default-on) governa SÓ memória —
`_load_history` (`telegram_handler.py:716`), a compactação (`:667`) e a janela do turno de
retomada (`resume.py:176`) passam a consultá-la. `CHAT_MANAGER_ENABLED` agora governa **só
conversas** (activity, ponteiros quente/frio, cronologia, comandos `/retomar` etc. —
verificados um a um). As duas chaves ficam independentes: dá pra ter memória-moderna com
conversas-off, e vice-versa.

**Comportamento preservado:** prod roda com `CHAT_MANAGER_ENABLED=true` (= janela imediata +
sem compactação); `WORKING_MEMORY_ENABLED` default-on entrega exatamente o mesmo. Zero
regressão; só separação limpa.

**Testes (dev):** import da cadeia; os dois campos coexistem no `Config`; default-on +
override `=false`; varredura confirmando que todo `chat_manager_enabled` restante é conversa.

**Reversão:** `WORKING_MEMORY_ENABLED=false` volta ao legado; `git revert` desfaz o decouple.

### Segurança — token do bot deixa de vazar nos logs (httpx → WARNING)

O `httpx`/`httpcore` logam a URL completa de cada request em nível INFO, e a URL da
API do Telegram embute o token do bot (`.../bot<TOKEN>/metodo`) — isso vazava o token
em texto puro no journal do systemd (que é persistente). `bot/main.py` agora sobe os
loggers `httpx`/`httpcore`/`telegram` pra `WARNING` logo após o `basicConfig`, cortando
o vazamento sem perder erros reais. Vale no próximo restart do bot. Reversível por
commit. (Descoberto numa sessão do plugin Coder ao investigar o cgroup das salas tmux.)

### Mudado — Highlander default-ON (decisão do operador 2026-06-24)

**Operador pediu:** "não deixe tudo atrás de flag-off esperando o operador apertar botão" —
as features do Highlander entram ligadas no ambiente de trabalho, prontas pra prod.

**Foi feito:** os defaults de `CURATED_CORE_ENABLED`, `GROUNDING_SIGNALS_ENABLED` e
`HINDSIGHT_ENABLED` passam a **on** quando a env não está setada (`os.getenv(..., "true")`),
em `config.py` + `.env.example`. Entram na prod pelo canal sancionado (merge-back + restart
do Hal) — não foi tocado o `.env` vivo da prod. Para desligar: setar a env como `false` +
restart.

**Segurança do default-on:** `curated_core` e `grounding` são puro-cômputo (no-op gracioso
se faltar arquivo/histórico); `hindsight` é best-effort — se o serviço estiver fora, falha
rápido (connection refused em ms) e o turno segue. **Tradeoff conhecido (repo potencialmente
público):** instalação fresca sem o serviço Hindsight loga um warning por turno até setar
`HINDSIGHT_ENABLED=false` — documentado no `.env.example`.

**Reversão:** env=false + restart, ou `git revert`.

### Adicionado — Highlander Frente 2.3: cliente Hindsight no bot (recall + retain)

**Operador pediu:** depois do smoke do Hindsight passar no prod, fiar a memória durável no
turno do bot.

**Por quê:** com o serviço de pé e o contrato REST verificado ao vivo, o bot pode trazer
fato durável de volta (recall) e destilar fato novo (retain) — o "trazer assunto velho de
volta" sem a maquinaria do Chat Manager.

**Foi feito:**
- `bot/hindsight_client.py`: `retain` / `recall` / `render_recall_section` sobre REST
  (httpx async), best-effort (qualquer falha → vazio/False, nunca levanta — Hindsight
  jamais derruba um turno) e por tópico (`bank_id_for_topic`, isolamento como o resto da
  memória). Coage `metadata` a `dict[str,str]` (o servidor dá 422 com valor int).
- Wiring no `telegram_handler`: **recall na entrada** → bloco `[Memória durável recuperada]`
  no prompt (moldura cética: é PISTA, confirme contra a fonte — contrato anti-mentira);
  **retain fire-and-forget** após persistir a msg, destilando fato **da mensagem do
  operador** (ground truth), não da resposta gerada (anti-alucinação). Fonte rastreável na
  metadata (tópico + message_id). Helper `_fire_and_forget` segura a ref da task (senão o GC
  coleta antes de rodar). `build_prompt` ganha o param `durable_memory`.
- Flags em `config.py` + `.env.example`: `HINDSIGHT_ENABLED` (default off), `_BASE_URL`
  (`http://127.0.0.1:8888`), `_TIMEOUT_SECONDS` (10), `_RECALL_LIMIT` (5).

**Testes (contra o serviço VIVO no prod, imagem 0.8.3):** retain → recall ponta a ponta
(o fato plantado volta renderizado); coerção de metadata (resolve o 422 real); best-effort
com serviço fora-do-ar (retorna vazio/False sem exceção); `build_prompt` injeta
`durable_memory` com dado e omite sem. Banks de teste criados foram **deletados** (serviço
ficou com zero banks).

**Tradeoff conhecido:** o retain roda por mensagem (não por silêncio). Custo do retain é
mini-tier OpenAI (~2.8k tokens in/retain), negligível, mas pra operador muito tagarela pode
valer mover pro daemon-por-silêncio depois. Atrás de flag — validar no prod-staging.

**Reversão:** flag off + restart = Kobe como hoje. `git revert` (sem banco do Kobe; o
Hindsight tem storage próprio isolado).

### Corrigido — tag da imagem do Hindsight (`v0.8.3` → `0.8.3`)

**Sintoma:** `docker compose up -d` falhou no prod com `failed to resolve reference
"ghcr.io/vectorize-io/hindsight:v0.8.3": not found`.

**Causa (verificada na GHCR):** os releases do GitHub usam tag com `v` (`v0.8.3`), mas a
**imagem Docker** no GHCR é tagueada **sem o `v`** (`0.8.3`). Pinei pela versão errada.

**Foi feito:** `HINDSIGHT_VERSION` corrigido pra `0.8.3` no compose + `.env.example`, com
nota explícita do gotcha; referências em README/CHANGELOG/plano alinhadas. Confirmado via
GHCR registry API que `0.8.3` resolve (HTTP 200) e `v0.8.3` não (404).

**Reversão:** `git revert` (só troca de string de tag).

### Adicionado — Highlander Frente 1.1: sinal de grounding temporal na entrada

**Operador pediu:** continuar a Frente 1 (memória confiável) — os gates de grounding
baratos resolvidos no código (P2 do v4).

**Por quê:** o contrato manda "nada relativo ao TEMPO sem conferir o tempo" e alerta que
"retomada depois de um tempo: o contexto recente pode não ser sobre o que ele quer agora".
O cabeçalho já dá o `[Agora]`, mas faltava **há quanto tempo foi a última troca** — sinal
que o agente senão narraria de memória (fonte clássica de confabulação ao retomar).

**Foi feito:**
- `bot/memory/grounding.py`: `render_grounding_signals(history)` lê o `created_at` que já
  veio no histórico imediato (sem query nova, sem LLM) e injeta uma linha `[Grounding]`
  com o gap humanizado (min/horas/dias). Só fala quando o gap passa de 30 min (retomada);
  num papo contínuo fica calado pra não virar ruído. A msg nova ainda não está no histórico
  na hora da montagem, então o gap é de fato "tempo desde a última troca" (verificado: o
  handler persiste a msg depois de montar o contexto).
- Fiado em `build_prompt` (logo após `[Agora]`, mesma natureza temporal) + `telegram_handler`
  + `resume`. Flag `GROUNDING_SIGNALS_ENABLED` (`config.py` + `.env.example`), default off.

**Testes (dev VPS, venv da prod):** import da cadeia OK; campo `grounding_signals_enabled`
no `Config`; teste de `render_grounding_signals` (gap curto = None; min/horas/dias;
histórico vazio/sem timestamp = None); `build_prompt` injeta após `[Agora]` com a flag e
omite sem ela (off = no-op).

**Reversão:** flag off + restart = comportamento de hoje. `git revert` (sem banco).

**Pendente em 1.1 (não nesta entrega):** o gate P1 (injetar estado de trabalho em
background lido do `.json`) — overlapa a maquinaria de missão/despacho existente e pede
cuidado; fica pra um passo seguinte.

### Adicionado — Highlander Frente 1.2: núcleo curado global (USER.md + MEMORY.md auto-injetado)

**Operador pediu:** atacar a confiança na memória — começando pelo núcleo curado
estilo Hermes (identidade + fatos duráveis auto-injetados).

**Por quê:** hoje o USER.md **não** entra no prompt — depende da instrução "leia o
USER.md" no CLAUDE.md, que o agente pode pular. O operador não confia numa memória que
o agente "às vezes lê". Núcleo curado pequeno e estável no topo, por construção todo
turno, é a base de identidade que faltava (e, de bônus, prefixo mais cacheável).

**Foi feito:**
- `bot/memory/curated_core.py`: `load_curated_core(kobe_home)` lê
  `user-data/identity/USER.md` + `MEMORY.md`, monta o bloco `[Núcleo curado]` com TETO
  fixo (~6000 chars) — USER.md tem prioridade, MEMORY.md espreme — e, perto de 80% do
  teto, anexa um empurrão pro agente CONSOLIDAR (esquecimento ativo). O código nunca
  apaga fato sozinho (anti-alucinação): quem consolida é o agente, editando o arquivo.
  Read-only e tolerante a ausência (None = no-op).
- `build_prompt` (`claude_runner.py`) e o turno de retomada (`resume.py`) ganham o param
  `curated_core` e injetam a seção logo após o cabeçalho `[Agora]`, como base de identidade.
- Flag `CURATED_CORE_ENABLED` (`config.py` + `.env.example`), default **off** = Kobe de
  hoje. Coletado em `telegram_handler.py` e `resume.py` só quando ligada.
- `user-data/identity/MEMORY.md.example`: template versionado do núcleo do agente
  (como USER.md.example), com as regras de uso (pequeno, consolidar, fato confirmado).

**Testes (dev VPS, venv da prod):** import da cadeia (`bot.config`→`telegram_handler`/
`resume`) OK; campo `curated_core_enabled` presente no `Config`; teste de
`load_curated_core` com dir temporário (monta USER+MEMORY; trunca no teto; sinaliza
consolidação); `build_prompt` injeta a seção com a flag e omite sem ela (off = no-op),
e o núcleo vem antes da `[Mensagem nova]`.

**Reversão:** flag off + restart = comportamento de hoje. `git revert` do commit (sem
banco). Reversível por construção.

### Adicionado — Highlander Frente 2: infra do Hindsight (memória durável), provisão

**Operador pediu:** subir o Hindsight em modo serviço + Postgres dedicado (pgvector),
conforme o plano v4 §6, com validação no prod VPS.

**Por quê:** a memória durável (recall cross-sessão) é o que dá "memória infinita" e
"trazer um assunto velho de volta" sem a maquinaria do Chat Manager. Precisa de storage
próprio (pgvector) separado do Supabase e da Evolution.

**Foi feito (só infra/autoria — NÃO sobe container, NÃO roda SQL):**
- `infra/hindsight/docker-compose.yml`: `hindsight-postgres` (`pgvector/pgvector:pg18`,
  volume dedicado, healthcheck) + `hindsight-app` (`ghcr.io/vectorize-io/hindsight:0.8.3`
  pinado, Postgres externo via `HINDSIGHT_API_DATABASE_URL`, OpenAI LLM+embedding
  `text-embedding-3-small`, portas 8888/9999 em loopback). Volume montado no PGDATA do
  PG18 (`/var/lib/postgresql/18/docker`) pra persistir de verdade.
- `infra/hindsight/.env.example` (senha + chave OpenAI; `.env` real fica gitignored).
- `infra/hindsight/smoke_test.py`: smoke isolado via REST (retain→recall de fato plantado,
  mede latência + `usage`), stdlib só, descobre paths via `/openapi.json`. **Roda no prod VPS.**
- `infra/hindsight/README.md`: runbook (subir/derrubar/backup), SQL de contingência
  documentado (`CREATE EXTENSION vector`, rodado pelo operador, não por mim), troubleshooting.

- `infra/hindsight/up_and_smoke.sh`: wrapper que sobe o stack, espera o serviço responder
  e roda o smoke numa tacada (evita rodar o smoke cedo demais). **Executado pelo
  Hal/operador no prod VPS** — a sessão Coder não sobe container. README com o passo exato
  (rodando do worktree, que está fisicamente no prod, sem depender do merge-back).

**Decisões aplicadas:** pg18 default (não pg16); Hindsight 0.8.3 (imagem; release v0.8.3); OpenAI;
validação no prod VPS (operador não testa em dev).

**Testes (dev):** AST parse do smoke; YAML do compose válido (2 services). O teste real
(subir stack + smoke) roda no prod VPS pelo operador — é o aceite da Frente 2.2.

**Reversão:** `git revert` (só arquivos novos, nada ligado ainda); no runtime, o stack é
`docker compose down -v` (isolado, não toca Supabase/Evolution).

### Refatorado — Highlander Frente 0: memória de trabalho ganha casa própria (`bot/memory/`)

**Operador pediu:** implementar o Highlander (reforma da memória) conforme o plano v4
aprovado — começando por "arrumar a casa" (Frente 0).

**Por quê:** o contexto imediato (memória pura — consulta `messages` só por `topic_id`,
não toca `conversations`) morava dentro de `bot/chat_manager/`, o gerenciador de
**conversas**. Isso é o spaghetti que o v4 §0 manda desfazer: cada coisa faz uma coisa,
com fronteira clara. Regra de ouro (v4 §1): a memória pode consumir dado de conversa, mas
**conversa nunca monta a janela**.

**Foi feito:**
- Novo módulo `bot/memory/` (casa da memória de trabalho). `bot/memory/working_set.py`
  recebe `get_immediate_messages` + `_parse_ts` + constantes `IMMEDIATE_*`, movidos de
  `bot/chat_manager/context.py` **sem mudar comportamento** (movimento byte-idêntico).
- `chat_manager/context.py` fica só com os blocos de **conversa** (quente/frio/relações).
  Mantido o nome `render_chat_manager_section` (e **não** renomeado pra `memory_context`
  como o v4 sugeria à letra) porque esses blocos são de conversa, não de memória — decisão
  fundamentada no que o código de fato faz.
- Call sites atualizados: `telegram_handler.py` e `resume.py` importam
  `get_immediate_messages` de `bot.memory`.
- **Não** mexido: `conversation_detector.py` (o v4 dizia "morto", mas é importado por
  `context.py`/`classifier.py`/`turn_classifier.py` por utils compartilhados — corrigido).

**Testes (dev VPS, venv da prod):** AST parse dos 5 arquivos; import real da cadeia
completa (`bot.memory` → `bot.telegram_handler`) OK; teste de comportamento da janela
imediata com fake DB (janela 10 min = 11 msgs; piso sem inventar msg; filtro de
`[Resumo da sessão` preservado). Sem suite automatizada no repo pra isso ainda.

**Reversão:** `git revert` do commit (refactor puro, sem banco, sem flag). Reversível por
construção.

### Adicionado — guardrail de fundamentação (anti-confabulação)

Nova seção `## Fundamentação — a regra acima de todas` no topo do `CLAUDE.md`: o
agente só afirma como fato o que está no contexto ou que acabou de verificar; o
inverificável (estado do operador, comportamento de app externo, fato do mundo fora
do contexto) **não se afirma** — no máximo hipótese marcada. Sua própria sugestão não
é decisão do operador. O verificável (hora, status de trabalho, arquivo) confere antes
de afirmar.

Saiu da **Auditoria da Verdade** (2026-06). Medido com um arnês de regressão
(`infra/eval/`, juiz gpt-4o): no caso residual onde o Opus 4.8 ainda escorrega
(afirmar comportamento de sistema que não observa) a regra leva a confabulação de
**~40% → 0%** (n=5). Nas demais classes testadas (auto-confirmação, causa-inventada,
inércia de contexto) o Opus 4.8 já aterra sozinho. Conserto de framework (vale pra
todo usuário); reversível por commit. Auto-discovery do `CLAUDE.md` → vale no próximo
turno, sem reinício.

**(2026-06-23) Endurecido com os casos reais** (auto-confirmação pega ao vivo: Mnemosyne
22/06, modelo-escalonado 09/06): regra macro *"você não tem permissão de mentir, em
nenhuma circunstância"*; cláusula de proveniência afiada — **o erro mora nos RESUMOS**
(silêncio / "deixa eu pensar" / mudar de assunto não é aceite nem recusa; nunca escrever
"você topou/decidiu X" sem fala explícita); + regra de **retomada-após-tempo** (o contexto
recente pode não ser sobre a intenção atual); + verificar o que **muda com o tempo**
(status de sala/sessão pode estar defasado). Validação dessas classes é por **uso real** —
o harness de fixture dá falso-negativo nelas (são contexto-sensíveis).

**(2026-06-23, 2ª rodada) Disciplina de leitura de fonte dinâmica** — cascata real pega ao
vivo (Dev Kobe, **com o contrato já no ar** → guardrail mole recai, como a auditoria previu):
ao ler pane/`git`/log/processo/`.jsonl`, **só afirmar o que está literalmente no output**.
Proibido *input-fantasma* (inferir que o operador digitou algo num pane); `mtime` ≠ atividade;
output vazio/erro pode ser **falta-de-acesso, não ausência**; não cravar causa de evidência
parcial. Promovida da memória privada do agente pro contrato (vale pra toda instância). Reduz
a superfície da classe mais recorrente; não é garantia.

## [0.15.0] — 2026-06-12 — Consolidação da pilha no main (Apolo + chat-manager v2 + perf SPR + UX + alertas + integrations)

### Kobe Integrations v1 — broker de capacidades

### Adicionado — catálogo de capacidades + switchboard

Subsistema de core pra plugins cooperarem **sem se acoplar pelo nome**. Um
plugin se anuncia como provedor de uma capacidade abstrata (ex:
`code-tracking`); outro pede "quem provê X?" — e um roteador fino casa os dois
sem que nenhum saiba o nome do outro. Resultado N+M, não N×M. Seta de
dependência aponta do específico (plugin) pro genérico (capacidade do core).

O Integrations é **magro**: guarda só o índice `capacidade → quem provê`, a
definição de contrato (declarativa), e o roteador. Lógica de negócio mora no
plugin dono — é o `information_schema` + FK do banco, não a stored procedure.

- **`bot/plugins.py`** — estende o parser do manifest pra ler o bloco
  `integrations:` (`provides` = capacidade + handler; `consumes` = etiqueta).
  Trata o manifest como hostil: valida nome da capacidade (`[a-z0-9-]`) e
  rejeita handler que escape da raiz do plugin (`../`). Novo
  `build_capability_index()` monta o índice `capacidade → provedor`; conflito
  (dois plugins, mesma capacidade) **trava** a capacidade e loga ERROR — não
  escolhe vencedor sozinho.
- **`bot/bin/kobe-integrations`** — a switchboard (no padrão dos `kobe-*`):
  - `provider <capacidade>` → imprime o provedor (ou `!=0` se não há / travado).
  - `invoke <capacidade> <verbo> [payload]` → resolve o provedor, chama o
    handler dele (executável agnóstico de linguagem: verbo no argv, payload no
    stdin, JSON no stdout) e repassa a resposta. O consumidor nunca vê o nome
    do provedor. Erros explícitos (sem provedor / conflito / handler ausente /
    handler falhou / JSON inválido), cada um com código de saída próprio.
- **`bot/main.py`** — no startup, monta o índice de capacidades e loga
  quantas estão indexadas / em conflito (guarda em `bot_data`).
- **Contrato da 1ª capacidade `code-tracking`** (`docs/integrations/`): dois
  verbos — `ensure(briefing)→{rc,card_id,meta}` (achar-ou-criar é problema do
  provedor) e `finished(card_id,desfecho)→{rc}`. Só a definição; sem provedor
  real na v1.
- **`docs/plugins-autoria.md`** — manual de como um plugin provê/consome uma
  capacidade.
- **`examples/integrations/code-tracking-stub/`** — plugin de exemplo com
  handler stub, usado pra testar a switchboard ponta-a-ponta.

Decisões da v1: conflito de provedor trava + avisa (não auto-escolhe);
`consumes` é só declarativo (não bloqueia o plugin de rodar sem parceiro);
payload trafega por stdin (suporta briefing grande/multilinha sem quebrar).

### Pacote UX de resposta, chat-manager v2, Apolo e perf (SPR)

- **Pacote UX de resposta — ack que nomeia a ação + background narrado pelo
  Hal (Fases B/C, 2026-06-05).** Unifica foreground e background num padrão só:
  **ack que nomeia a ação → `digitando` vivo → entrega**. (B) Instrução no
  `CLAUDE.md` ("Avisa antes de agir"): quando o Hal vai usar ferramenta com
  latência perceptível e não responde na hora, emite primeiro um `kobe-notify`
  curto **nomeando** o que vai fazer, depois chama a ferramenta — gatilho é
  intenção de agir, não cronômetro. O `digitando` vivo já existia (foreground
  renova a cada 4s). (C) Background deixa de ter aviso enlatado: na **previsão**,
  a run de bg recebe uma **nota de handoff** no prompt (`build_prompt
  background_handoff`) que a manda abrir com um ack na própria voz e reler a
  **janela de frescor** (`bot/bin/kobe-recall-since '<ISO>'` +
  `topic_manager.get_messages_since`) antes de agir; no **promote** (retaguarda
  do teto), consome a run em voo (não recomeça) e **suprime o enlatado quando o
  Hal já ackou** (`ProgressReporter.acked` detecta `kobe-notify` no stream),
  caindo no enlatado só como rede quando não houve ack. Aditivo e reversível
  (revert volta ao notice enlatado). Runbook:
  `docs/runbooks/ux-resposta-ack-despacho.md`.
- **Despacho de turno pesado em background (cascata de filtros).** O lock por
  tópico serializa os turnos de um mesmo tópico — correto pra consistência,
  mas um turno pesado do Hal (editar código, varrer repo, análise longa)
  segurava a linha e prendia a próxima mensagem do operador atrás dele.
  Agora, com `HEAVY_DISPATCH_ENABLED=true`, a ENTRADA do turno classifica se o
  pedido vai ser pesado e, se for, despacha o `claude -p` em background FORA do
  lock — o atendente (Hal) fica livre pro próximo pedido na hora. Dois
  caminhos pro background, ambos com aviso imediato ao operador:
  (1) **previsão** — a cascata crava pesado na entrada (aviso antes de começar);
  (2) **retaguarda** — turno que entrou foreground mas estoura
  `HEAVY_DISPATCH_PROMOTE_AFTER_SECONDS` (default 12s) segurando o lock se
  promove sozinho (aviso no momento da promoção; o `claude` em voo **não
  recomeça**, continua e reporta no fim). A cascata (`bot/turn_classifier.py`):
  roteamento por tipo de slash → placar estrutural + léxico → GPT-4o-mini só na
  zona cinza (fora da cota do plano Max). Modelo de execução: `asyncio` task
  in-process fora do lock (mesmo padrão de handoff/compactor/resume), reusando
  o `ClaudeRunner` e a persistência/log do tail. Flag off → caminho clássico
  intacto (rollback trivial). Testes: `tests/test_turn_classifier.py`.
  Runbook de validação: `docs/runbooks/despacho-turno-pesado.md`.
- **Aviso discreto de troca de assunto (Chat Manager).** Quando o detector
  fecha a conversation ativa e abre uma nova por borda de assunto (transição
  real, não o bootstrap do 1º assunto do tópico), o daemon manda uma linha no
  Telegram: "📑 Novo assunto detectado — abri uma conversa nova pra isso."
  Antes a troca era silenciosa — com a calibração 0.55 ela passa a acontecer
  mais, então o operador precisa saber que o ponteiro "quente" mudou. O
  `classify_topic` reporta as transições em `ClassifyResult.new_conversations`;
  o `ClassifierSource` envia via subprocess `kobe-notify` (mesmo padrão do
  circuit breaker — envs `KOBE_*` + chat/thread vindos do topic), best-effort,
  fora do caminho do turno. Testes: `tests/test_chat_manager_transition.py`.

### Corrigido

- **Título da conversation agora é o TEMA, não a 1ª frase literal.** O título
  vinha de `_title_and_slug_from_message(seed)` — a primeira frase do seed
  truncada em 60 chars, irreconhecível depois ("Eu tô vendo aí que pelas
  instruções que você tá colocando…"). Agora um GPT-4o-mini nomeia o tema em
  3-6 palavras a partir das primeiras ~5 msgs do operador do segmento
  ("Formato do Progress Report", "Problemas com sessões caídas no tmux"). É a
  MESMA chamada que já gerava as tags — `_make_title_and_tags` devolve
  `{title, tags}` num call só, então **custo zero novo**; roda no daemon, fora
  do turno; fallback pro título literal se a chamada falhar (`title=None` →
  `_create_conversation` cai no seed). O slug é derivado do tema. O aviso de
  troca de assunto (acima) passa a incluir o tema: "📑 Novo assunto detectado —
  abri uma conversa nova: «tema»." Comparativo em dados reais mostrou a
  alternativa sem LLM (keywords) produzindo salada de palavras — o modelo
  barato ganhou o lugar. Testes: `tests/test_chat_manager_transition.py`.

- **Granularidade do Chat Manager calibrada (`CM_BORDER_SIM` 0.40 → 0.55).**
  Em tópicos de vocabulário homogêneo (Dev Kobe) o detector quase nunca abria
  conversation nova — dias de trabalho viravam um blob só (104 msgs em 13h num
  caso real). Diagnóstico com dados reais: a similaridade mediana ao centroide
  é 0.63 e só 3,2% das msgs ficavam abaixo do border 0.40, então a borda quase
  nunca disparava. Subir pra 0.55 (mantendo `CM_SUSTAIN=3`) dobra a
  granularidade real (7→10 conversations no replay) rachando os blobs em pontos
  que são trocas de assunto reais, sem reabrir o problema de msgs curtas/vagas
  (essa proteção é o gate `is_informative` + sustain + coherence, não o border).
  Aplicado via `.env` do prod + restart do keyko (`knobs_from_env`) — **sem
  deploy de código**, default do código segue 0.40 (testes intactos),
  reversível em uma linha. Só afeta classificação futura; blobs existentes
  ficam como estão. Diagnóstico, evidências e trade-off completos em
  `docs/chat-manager/bug1-granularidade-proposta-2026-06-04.md`.
  Nota: o knob vivo é `border_sim_threshold` do `bot/chat_manager/classifier.py`
  (daemon) — o `conversation_detector.py` (`THRESHOLD_LOW`) está morto no
  caminho ativo desde a migração de 2026-06-01.
  Card Flow: `fb0bdaa3-d5e2-4c00-9f9a-554028128fee`.

- **Compactação de sessão deixou de ser silenciosa.** Quando a sessão
  legada cruza `COMPACT_THRESHOLD_MESSAGES` (default 40) e compacta
  (`bot/compactor.py`, disparado em `_handle_user_text`), o operador agora
  recebe um aviso curto via Telegram **assim que a compactação começa** —
  antes da geração do resumo (que custa alguns segundos de Claude). O tom
  tranquiliza: nada se perde, a conversa continua de onde estava. Antes o
  único aviso saía DEPOIS de pronto (tom de "gerei um resumo"), deixando o
  operador no escuro durante o resumo. Implementado via callback `on_start`
  injetado no `compact_session` (best-effort: falha no aviso não derruba a
  compactação; dispara 1x por evento, nunca em sessão vazia). Testes:
  `tests/test_compactor_notify.py`.
  **Nota de escopo:** esta compactação legada só roda com
  `CHAT_MANAGER_ENABLED=false` — que é o **default do framework** (todas as
  instalações públicas). Com Chat Manager ligado (runtime do operador em
  prod) a compactação não roda; o aviso cobre o default público.
  Card Flow: `9b0b6638-c2d5-4602-887c-e9fa07aa2db3`.

- **Retomada de contexto após restart: o boot-resume agora RE-SITUA o
  agente, não só pinga o operador.** Até aqui, no boot o bot mandava um
  template fixo em Python (`render_resume_message`: "⏯️ Voltei, você tinha
  mandado X", citando só a última fala do operador) e **nunca invocava o
  agente**. Ele só voltava a se "inserir no fluxo" se/quando o operador
  mandasse uma mensagem nova — então, numa retomada, o contexto imediato
  (≈últimos 10 min) não chegava ao agente. Agora, pra cada tópico com
  snapshot pendente, o novo `bot/resume.py` monta o **mesmo contexto de um
  turno normal** (camada imediata via `get_immediate_messages` + ponteiros
  do Chat Manager + cronologia comprimida + KB do tópico + alertas/missão
  abertos) e invoca o agente com uma diretiva de retomada. Ele relê,
  entende onde a conversa estava e manda ao operador uma síntese real de
  onde param (em vez de um template). Salvaguardas: roda sob o lock do
  tópico (serializa com o handler normal), pula se o operador já voltou a
  falar pós-restart (guarda de atividade — sem ping duplo), cai no template
  antigo se o agente falhar (nunca regride a silêncio), e persiste a síntese
  como `messages` (role=assistant) pra entrar na janela imediata do próximo
  turno. Caminho do Chat Manager (injeção no turno normal) intacto. Compactação:
  com `CHAT_MANAGER_ENABLED=true` não roda (a janela imediata é reconstruída
  crua do tópico a cada turno, então o tail sobrevive); no legado o tail cru
  se perde no resumo, mas o legado está em desuso. Testes: `tests/test_resume.py`.
  Card Flow: `6cec4584-ee6e-41b4-a7e2-678022554a3c`.

## [0.15.0] — Tag de áudio transcrito + fix latência de áudio (2026-06-04)

### Adicionado

- **Tag visível de áudio transcrito no contexto do agente.** Quando o
  operador manda uma mensagem de voz, o bot transcreve via Whisper/Groq
  (ou AssemblyAI no fallback) e o texto resultante agora entra no prompt
  marcado com `🎤 [áudio transcrito]` — tanto na `[Mensagem nova do
  operador]` quanto nas linhas de `[Histórico recente]` que vieram de voz.
  Assim o Hal sabe que aquele conteúdo foi falado (tom de fala, possível
  ruído de transcrição), não digitado. A tag fica **só no prompt** — não é
  ecoada de volta no chat (o operador já sabe que mandou áudio; ecoar seria
  ruído). Aproveita o booleano `audio_transcribed` que já era persistido em
  `messages`, agora também carregado junto do histórico
  (`get_recent_messages` / `get_immediate_messages`) pra consistência turno
  a turno. Card Flow: `b9fe59fa-8351-40fc-84c4-db651095564c`.

### Corrigido

- **Latência de áudio: transcrição saiu de dentro do lock do tópico.** O
  handler `on_voice` pegava o lock por tópico ANTES de baixar/transcrever o
  áudio. Como o lock só libera quando o `claude_run` da mensagem anterior do
  mesmo tópico termina (60–300s), cada áudio ficava ENFILEIRADO atrás do LLM
  do áudio anterior antes de sequer poder ser transcrito. Nos logs: áudio de
  24s recebido às 22:42 só transcrito 241s depois; bursts de 5 voice notes
  em fila por ~5 min — embora a transcrição em si leve 3–4s. Agora o
  download + transcrição (função pura, sem estado compartilhado) rodam FORA
  do lock: áudios em fila no mesmo tópico transcrevem em paralelo (cada um em
  sua thread) enquanto um turno anterior ainda processa no Claude; o lock
  passa a cingir só o `_handle_user_text` (insert + claude). Some também o
  silêncio durante a transcrição — o "digitando…" dispara assim que o áudio
  chega. `transcribe()` passou a retornar `(texto, engine)` (em vez de só o
  atributo compartilhado `last_engine_used`) pra ser seguro sob concorrência;
  o aviso de fallback do AssemblyAI segue intacto. Novo log `audio_transcribe`
  mede download e transcrição separadamente. Card Flow:
  `027d3442-cc41-4e2a-b8b6-28d3ffbb85c2`.

## [Não lançado] — Reversão do streaming + fix de perda de resposta (2026-06-01)

Reverte o streaming token-a-token introduzido no mesmo dia (SPR P1 #1) e
corrige, na raiz, um bug que ele expôs: respostas longas com tool call no
meio chegavam truncadas ao operador.

### Corrigido

- **Resposta engolida antes de tool call.** O texto final vinha do campo
  `result` do stream-json, que carrega só a ÚLTIMA mensagem do assistant
  (o bloco emitido depois da última ferramenta). Quando o Hal escrevia
  prosa, rodava uma tool (ex.: gravar no Flow) e emitia um "Anotado em…"
  curto depois, a prosa era descartada — o operador via só o trecho final.
  Agora a resposta é a **concatenação de TODOS os blocos de texto do
  agente principal** no turno (`_join_texts` sobre os eventos `assistant`
  com `parent_tool_use_id` nulo); `result` segue lido só pra métricas e
  como fallback. Validado e2e (prosa pré-tool preservada, ruído de
  subagente filtrado, parcial de timeout recuperado dos blocos completos).

### Removido

- **Streaming token-a-token pro Telegram (era v0.15 / P1 #1).** Editar a
  mesma mensagem a cada ~1s rolava a tela e tirava o operador do ponto de
  leitura ("pior a emenda que o soneto" — decisão registrada em
  `user-data/knowledge/kobe/preferencias/design-arquitetura.md`). Saíram:
  flag `--include-partial-messages`, callback `on_text_delta` /
  `TextDeltaCallback` e a classe `_StreamingReply`. O sinal de vida volta
  a ser só o `ProgressReporter` (status por etapa) e a resposta sai
  **inteira de uma vez** via `_send_long_text` (com fatiamento no limite
  do Telegram). Card Flow: `afbee37e-d2db-45a3-9210-c05b4583c080`.

## [Não lançado] — New Chat Manager (2026-06-01)

Redesenho do Chat Manager pra matar a latência e a granularidade macro.
Princípio: **o turno é burro e rápido; toda inteligência cara roda atrás,
assíncrona**. Design completo em
`user-data/knowledge/kobe/brainstorms/new-chat-manager-arquitetura.md`;
calibração em `docs/chat-manager/calibracao-2026-06-01.md`. Tudo atrás da
flag `CHAT_MANAGER_ENABLED` (off = baseline estável; rollback = off + restart).

### Adicionado / Mudado

- **Detector sai do caminho crítico.** `telegram_handler` não chama mais
  `detect()` síncrono no turno (era a fornalha de latência/custo —
  embedding + LLM judge antes do 1º byte). O turno agora só toca um sinal
  de atividade e lê ponteiros residentes já mastigados pelo daemon.
- **Classificador-bibliotecário (`bot/chat_manager/`).** Novo ofício do
  Keyko (`ClassifierSource`): debounce por silêncio (~60s) + disjuntor de
  teto; roda atrás da resposta. Calcula embedding das msgs novas, detecta
  **bordas de assunto grosso em retrospecto** e carimba
  `messages.conversation_id` (a conversation vira FAIXA derivada). Nunca
  Opus, nunca no turno — embedding + álgebra de vetor + modelo barato só
  pra tags.
- **Detecção de borda (5 pilares + pista lexical).** Contra o acumulado,
  histerese (permanência), voto ponderado por informação, corte
  retrospectivo (tail ambíguo decide na próxima passada), hierarquia
  grosso/fino. Híbrido vetor + pista de troca explícita ("muda de
  assunto", "deixa X de lado") — embeddings de msg curta em PT têm cosseno
  comprimido, então a pista lexical é discriminador crítico. Viés
  deliberado contra over-cut.
- **Prompt em 4 camadas (`build_prompt`).** Imediato (últimos ~2 min OU N
  msgs do tópico, sempre, do disco — compactação vira não-evento) +
  ponteiro do quente + catálogo frio (tag cloud) + relações (similaridade
  de centroide on-the-fly). Verbatim do quente / busca fria sob demanda.
- **`bot/bin/kobe-recall`.** Helper pro agente puxar a faixa inteira de um
  assunto (`--conversation <id>`) ou buscar por tema (busca vetorial no
  tópico) sob demanda.
- **Migration 003 (aditiva).** `messages.conversation_id` + índice;
  ivfflat em `messages.embedding`; tabela `conversation_tags`. Banco
  compartilhado dev/prod → roda 1x, manual no Supabase (REST não faz DDL).
- **Calibração + testes.** Harness com corpus rotulado
  (`infra/calibrate_chat_manager.py`) escolheu border=0.40, sustain=3,
  coherence=0.35 (4/4 casos). Testes determinísticos em
  `tests/test_chat_manager_classifier.py` (7/7). Knobs ajustáveis por env
  (`CM_BORDER_SIM`, `CM_SUSTAIN`, `CM_CLUSTER_COHERENCE`, ...) sem deploy.

### Limitações conhecidas

- Troca de assunto SEM pista lexical e com vetor pouco distinto (assuntos
  vizinhos) tende a NÃO cortar — viés conservador (quente cresce; re-corte
  é de graça; operador pode `/nova`). Recalibrar via env se necessário.
- Busca vetorial do frio (`kobe-recall`) ranqueia em Python (escala de um
  operador) — sem RPC pgvector ainda.
- Notice de borda ao operador desligado nesta fase (UX limpa; o estado
  vive nos ponteiros do prompt).

## [Não lançado] — Performance percebida (SPR 2026-06-01, P1)

Diagnóstico em `docs/spr/2026-06-01-performance.md`. Implementados os P1 de
performance, exceto a troca de modelo (Opus mantido por decisão do operador).

### Adicionado / Mudado

- **Streaming da resposta (P1 #1)** — ⚠️ REVERTIDO no mesmo dia (ver seção
  "Reversão do streaming" no topo). UX ruim em mensageiro + expôs bug de
  perda de resposta. O sinal de vida ficou no `ProgressReporter`.
- **Timeout não descarta trabalho (P1 #4)** — `ClaudeTimeoutError` carrega
  `partial_text`; ao estourar o tempo, entrega o que o agente já completou
  + nota de interrupção, em vez de só a mensagem de erro. (Mantido após a
  reversão — agora o parcial vem dos blocos `assistant` completados, não
  mais dos deltas de streaming.)
- **KB de tópico sob demanda (P1 #3)** — `load_topic_context` mantém
  `prompt.md` inline mas injeta a pasta `knowledge/` grande como índice
  (caminho + prévia) acima de `TOPIC_KNOWLEDGE_INLINE_LIMIT` chars (env,
  default 8000); o agente lê com `Read` quando precisa. Corta ~12k
  chars/turno em olimpo e dev-kobe.
- **I/O fora do event loop (P1 #5)** — transcrição (Groq/AssemblyAI) e as
  leituras independentes (histórico + contexto de tópico) rodam em
  `asyncio.to_thread`/`gather`, sem travar o loop nem serializar tópicos.

### Pendente (decisão do operador)

- **Modelo (P1 #2)** — mantido Opus por ora; Sonnet no caminho
  conversacional fica para avaliação futura.
- olimpo guarda instruções dentro de `knowledge/` (não em `prompt.md`) —
  com o modo índice elas passam a ser lidas sob demanda. Mover para
  `prompt.md` se quiser presença garantida todo turno.

### Sistema de Alertas (Fase 1)

### Adicionado — agente proativo (Alertas como 2ª Source do Keyko)

Capacidade core: o operador pede em linguagem natural ("me lembra toda
terça…", "todo dia 7h faça X", "amanhã 15h…") e o Hal passa a disparar
sozinho no horário. Reusa o daemon Keyko — Alertas é a 2ª `Source`
(gatilho de tempo), ao lado de Missões (gatilho de evento).

Princípio reitor: lógica determinística (quando disparar, estado,
escalonamento) mora no código; o Claude/Hal só entra pra linguagem
(traduzir pedido→YAML, redigir o lembrete, julgar "já marquei"). Código é
dono do estado — espelha o padrão Missões (evento → transição).

- **`bot/alertas/`** (novo pacote):
  - `models.py` — dataclasses Alerta/Agenda/Canal/Limites/Confirmacao/
    Estado + enums StatusAlerta/TipoEvento/Acao. Serialização YAML em duas
    seções demarcadas (definição escrita pelo Hal · estado só pelo código),
    eventos em jsonl.
  - `storage.py` — CRUD YAML + eventos jsonl espelhando `missoes/storage`:
    lock fcntl, escrita atômica (tmp+rename), append-only com offset, fuso
    America/Sao_Paulo. Layout flat em `user-data/alertas/<id>.{yaml,eventos.jsonl}`.
  - `scheduler.py` — cálculo determinístico de próximo disparo via
    `croniter` (dep nova). Merge dos crons abertura/cobranca/limite resolve
    o escalonamento; one-shot via `quando` ISO.
  - `source.py` — `AlertasSource` (implementa `keyko.Source`, intervalo 30s).
    Máquina de estado ABERTO→CONFIRMADO/EXPIRADO, reabertura por ciclo,
    circuit breaker `disparos_dia` por alerta, backlog-skip se o daemon
    ficou fora do ar.
  - `prompts.py` / `context.py` — prompt de disparo (Hal redige+envia) e
    injeção de "alertas aguardando confirmação" no contexto do turno normal.
  - `handlers.py` — slash commands `/alerta_lista|_pausar|_retomar|_apagar`.
- **`bot/bin/kobe-alerta`** — helper CLI: `criar` (NL→YAML, calcula 1º
  disparo), `confirmar`/`dispensar` (emite evento que a source aplica),
  `listar`. Re-exec sob o venv pra ter as deps.
- **Integração**: `keyko/registry.py` registra a source; `claude_runner.
  build_prompt` ganha `alertas_abertos_info`; `telegram_handler` injeta a
  seção; `main.py` registra os 4 slash commands; `CLAUDE.md` documenta o
  fluxo pro agente. `croniter>=2.0` em `requirements.txt`.

Canais: Telegram funciona (reusa o Despertar do Keyko). WhatsApp é
aceito/validado mas o envio cai em fallback (avisa no Telegram) até o
Apolo expor envio por número.

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
