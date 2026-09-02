# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### docs(boletim): o CLAUDE.md ensina a ler o bloco novo (2026-09-01)

**Operador pediu:** a seção do boletim no `CLAUDE.md`, corrigindo o escopo da
autorização anterior. O que fica fora desta entrega é a **faxina** do arquivo
(enxugar e reorganizar), que é fase própria; documentar o que esta entrega
introduz é parte normal dela. Pedido dele, como conselho: **o mínimo necessário,
regra e ponteiro, sem narrativa** — o arquivo está em 56 mil caracteres e vai
passar por dieta.

**Por quê:** o bloco já se explica in-band (o cabeçalho declara que é curado, o
rodapé declara o recorte), e foi assim de propósito. Mas a regra que o liga ao
resto do contrato não cabe dentro do bloco: **o boletim não dispensa o
`kobe-remember`**. Sem isso escrito, um recorte de ~16 linhas convida
exatamente à conclusão que o Highlander inteiro existe para impedir — tomar
ausência no recorte por ausência no registro.

**Foi feito:** uma seção de **1.083 caracteres** (~1,9% do arquivo), em quatro
marcadores: é recorte e não índice; cada linha é julgamento de modelo com
origem; o cabeçalho tem data e o que vem depois dela o boletim não viu; bloco
ausente é normal e não se comenta.

**Testes:** `test_o_claude_md_ensina_que_o_boletim_e_recorte_e_nao_dispensa_o_remember`
guarda os **invariantes** da seção, não a redação — mesmo princípio de
`tests/test_claude_md_regra_remember.py`. Ele existe por causa da dieta que vem:
o enxugamento pode reescrever a prosa, mas se levar embora a distinção entre
"não está no boletim" e "não existe", fica vermelho. Suíte: **1.000 passando,
169 puladas.**

**Reversão:** `git revert <hash>` — o bloco continua funcionando sem a seção,
porque o aviso in-band é o que sustenta; a seção reforça.

### feat(memoria): os parâmetros da janela viram configuração, com travas e diagnóstico (2026-09-01)

**Operador pediu:** *"esse tipo de parâmetro que a gente está conversando — tamanho
da janela em tokens, tempo da janela, piso de mensagens, tamanho do boletim — se
isso não era para ser flag no arquivo de configuração. De modo que, se a gente
fosse fazer uma medição e você precisasse corrigir, você falaria: vou ajustar os
valores tais das flags tais, e está tudo resolvido. Não precisa alterar código na
mão."* (31/08/2026)

**Por quê:** ele está certo, e o levantamento factual é constrangedor. LUCIEN
(F3) tem **16 de 16** parâmetros por ambiente; a busca (F2) tem os dois que
importam; a janela imediata tinha **1 de 5** — e o único exposto
(`WORKING_MEMORY_TOKEN_CAP`) é justamente o que **nunca manda**: 0% dos turnos,
medido. Os três que governam de fato (600 · 8 · 60) e a régua de conversão
estavam todos cravados. **O único botão do painel era o que não estava ligado em
nada.**

O ponto de fundo dele — *"medição só serve se ajustar for barato"* — está certo,
e falta a metade que o completa: **ajustar só serve se der para ver o efeito.**

**Foi feito:**

- **Os quatro parâmetros da janela viram configuração**, com os valores de
  **sempre** como padrão: `WORKING_MEMORY_WINDOW_SECONDS=600`,
  `WORKING_MEMORY_MIN_COUNT=8`, `WORKING_MEMORY_HARD_CAP=60`,
  `WORKING_MEMORY_TOKEN_CAP=8000`, mais `WORKING_MEMORY_CHARS_PER_TOKEN=4`.
  **Esta entrega põe os botões no painel e não gira nenhum** — mudar os valores
  é decisão do operador, em fase própria. Há teste guardando isso
  (`test_os_defaults_sao_os_valores_de_hoje`): se ele quebrar, uma entrega que
  se declarou de risco zero mudou o prompt de todo turno.
- **Cada um preso em faixa, e o desvio REGISTRADO** — nunca corrigido em
  silêncio. A régua `CHARS_PER_TOKEN` é a mais estreita (3–5) de propósito: ela
  não é política, é medida. Valor alto ali não relaxa um orçamento, faz o código
  **contar errado** e estourar o teto achando que respeitou. É o mesmo perigo
  que `bot/search/embedder.py` já documenta sobre a dimensão do vetor — *"não dá
  erro, dá resposta errada com nota plausível"*.
- **`working_set.conferir()` — a conferência de invariante na subida.** Faixa por
  parâmetro não pega o problema real: **cada um pode ser válido e o conjunto ser
  impossível.** Piso de 80 com teto de 60 é um estado que o código obedeceria
  sem reclamar — a consulta traz 60, o piso pede 80, e o piso vira letra morta
  enquanto a configuração anuncia outra coisa. `main()` registra a configuração
  efetiva e grita a incoerência. **WARNING e não erro fatal**: um bot no ar com
  aviso alto se conserta em minutos; um bot que se recusa a subir por causa de
  um número deixa o operador sem canal.
- **`bot/bin/kobe-memoria diagnostico` (novo)** — o extra que o operador aprovou
  e chamou de item mais valioso do parecer. Reexecuta a lógica real da janela
  sobre os turnos que **já aconteceram** no banco e responde: quanto ela gasta,
  **qual parâmetro é o limitante efetivo**, e o que mudaria com outra
  configuração (`--simular tempo:piso:tetoMsgs:tetoTokens`). Só leitura.

**A armadilha que ele previu, medida.** Subir a janela para 6 h **sem** mexer no
teto de 8.000 faz o TETO virar o limitante em **47% dos turnos** — a "janela de
6 horas" entregaria 8.000 tokens quase metade das vezes, e o parâmetro novo não
entregaria o que promete, em silêncio. Está no `.env.example`, ao lado das
chaves, porque é onde alguém vai ler antes de calibrar.

**Testes:** `tests/test_kobe_memoria.py`, 9 casos. O central é a **trava contra a
cópia virar ficção**: o diagnóstico reimplementa a lógica da janela para poder
aplicá-la ao passado, e um diagnóstico que diverge do original mente **com
autoridade** — leva a calibrar na direção errada, com confiança. O teste roda as
duas implementações sobre os mesmos dados (rajada, conversa esparsa, rajada
longa, e o padrão misto real) e exige o mesmo resultado.

**Suíte inteira: 999 passando, 169 puladas.**

**Reversão:** `git revert <hash>`. Nenhuma mudança de comportamento para
reverter — os padrões são os valores anteriores, e é isso que o teste dos
defaults garante.

### feat(boletim): o boletim entra no prompt, atrás de BOLETIM_ENABLED (2026-09-01)

**Operador pediu:** que o boletim chegue de fato ao agente — e **ligado por
padrão**. Palavras dele em 01/09/2026: *"com certeza nasce ligado"*.

**Por quê:** as duas metades anteriores escreviam e sabiam ler um arquivo que
ninguém consumia. Este commit é o que fecha o circuito.

**Foi feito:**

- **`build_prompt` recebe `boletim`**, injetado logo depois do núcleo curado e
  antes da sala/alertas. A ordem se lê como frase: *quem é o operador* → *o que
  o agente sabe* → **o que vale hoje NESTE tópico** → o resto do contexto
  dinâmico. Trocar essa ordem não quebraria nada — e é por isso que há teste
  (`test_t7b`): ninguém perceberia.
- **`telegram_handler` e `resume` carregam e passam**, os dois. Uma retomada que
  não trouxesse o bloco mostraria ao agente um contexto diferente do de uma
  mensagem comum, e divergência entre os dois caminhos de montagem é o tipo de
  defeito que só aparece como resposta contraditória, meses depois.
- **Nenhuma consulta nova em nenhum dos dois.** O `topic_id` já estava em escopo
  — na L859 do handler e no `snap` do resume. Foi isso que tornou a chave por
  UUID (em vez de slug) gratuita do lado do leitor.
- **`bot/config.py`: `boletim_enabled`**, default `true`. Seguro em instalação
  nova porque lá não existe boletim nenhum — sem arquivo, o bloco é ausente e o
  prompt é o de sempre.
- **`.env.example`** com a chave e as duas de orçamento, documentando o que
  cada uma custa e por que estão presas em faixa.

**Testes:** 32 casos entre `test_boletim.py` e `test_boletim_escrita.py`, e
**suíte inteira verde: 989 passando, 169 puladas** (terminal limpo, sem o `.env`
de dev). Os dois novos que importam:

- **T-1d/T-1e** medem o crescimento do **prompt de verdade**, com e sem o bloco,
  inclusive com um arquivo 50× maior que o teto em disco — era exatamente o que
  o briefing cobrava ("verificado por teste, não por inspeção visual").
- **T-7** prova a reversão: com o bloco ausente, o prompt é **byte a byte** o de
  antes desta fase.

**Reversão:** `BOLETIM_ENABLED=false` + restart devolve o comportamento anterior
sem tocar em arquivo nem em banco (e o T-7 é a prova disso). Para desfazer o
código, `git revert <hash>`.

### feat(boletim): o lado ESCRITA — o worker projeta o registro em disco (2026-09-01)

**Operador pediu:** a outra metade da F4 — quem escreve o arquivo que o turno lê.

**Por quê:** o boletim é função pura de `lucien_claims` + `lucien_events` daquele
tópico, e a única coisa capaz de mudá-lo é uma rodada do LUCIEN gravando ali.
Então o gatilho natural é o fim da rodada: o processo já está de pé, já é
detached, e acabou de gastar dezenas de segundos falando com um modelo. Duas
consultas e um `write()` são ruído nessa conta. Uma fonte própria no Keyko faria
o daemon perguntar "mudou?" em todos os tópicos a cada tique para quase sempre
não fazer nada — e acrescentaria superfície de falha no processo de que os
**Alertas** dependem.

**Foi feito:**

- **`bot/lucien/boletim.py` (novo)** — as consultas dos três blocos e a escrita
  atômica (`tmp` + `os.replace`, porque o turno pode estar lendo o arquivo agora).
- **Gancho no `worker.uma_rodada`**, depois do commit e dentro de `try/except`,
  pela mesma regra que o embedder já segue ali: falhar ao escrever um arquivo de
  conveniência **nunca desfaz** uma rodada que já vale.
- **Gancho no `kobe-lucien reverter`** — é o único caminho que muda o registro
  fora de uma rodada. Sem ele, o arquivo continuaria mostrando o estado desfeito
  até a próxima rodada daquele tópico, e um boletim que contradiz o banco é pior
  que boletim nenhum.
- **`kobe-lucien boletim [--topico X] [--ver]`** — backfill da primeira
  instalação e inspeção do que o agente vai receber.
- **Nenhuma chamada de modelo.** Quem escolhe as linhas é código: SQL e
  formatação de string. O modelo já foi chamado antes, quando LUCIEN escreveu a
  afirmação. Isto responde com fato a uma preocupação real do operador em
  31/08/2026 (*"o LUCIEN vai ficar trabalhando igual um doido"*): o custo
  marginal de assinatura é **zero**, não "baixo".
- **O bloco "o que saiu de cena" NÃO lista `created`** — as afirmações
  recém-criadas já estão no topo dos outros dois blocos, que são ordenados por
  recência. O que os outros blocos estruturalmente não podem mostrar é o que
  deixou de valer, que é o sinal de "mudamos de ideia".

**Idempotência por construção — e foi ela que dispensou a migration.** O critério
5 da fase exige que gerar duas vezes sem conversa nova não mude o arquivo. A
forma ingênua (gravar `gerado_em = now()`) o quebra: na segunda passada o arquivo
mudaria sozinho. O conserto foi fazer **cada byte ser função do banco** — o
cabeçalho traz a marca d'água do próprio registro (`MAX(GREATEST(created_at,
valid_to))`), não o relógio de quem gerou. Como efeito colateral que vale mais
que o próprio critério, **não sobrou estado de geração para guardar: a F4 não
precisou de coluna, tabela nem migration.**

**Testes:** `tests/test_boletim_escrita.py`, 9 casos, verdes, com cursor falso
(sem banco). O T-5 confere bytes **e `mtime`** — reescrever conteúdo igual faria
a data do arquivo mentir para quem inspeciona a pasta.

**Um defeito achado no smoke, não na revisão.** Rodando contra o `kobe_dev` de
verdade, o rodapé saiu dizendo *"3 linha(s) de 2 afirmação(ões) vigente(s)"*. A
causa: contava **todas** as linhas contra o total de vigentes, e o terceiro bloco
é feito de afirmações **encerradas**, que por definição não estão no acervo
vigente. Um rodapé que se contradiz destrói a confiança exatamente na linha que
existe para ser confiável. Corrigido e travado por teste (`test_t3d`). Nenhum
teste de mesa teria pego isso — só dado real tinha as duas contagens divergentes.

**Reversão:** `git revert <hash>`. Em runtime, `BOLETIM_ENABLED=false` faz o
worker parar de gerar. Os arquivos já escritos são derivados e podem ser
apagados a qualquer momento (`user-data/lucien/boletins/`) — nada depende deles.

### feat(boletim): o lado LEITURA do boletim quente — render, teto e degradação (2026-09-01)

**Operador pediu:** a F4 do Highlander v3 — que o registro de estado que LUCIEN
escreve atrás (868 afirmações em produção) passe a estar **presente no prompt de
todo turno**, de graça, em vez de só chegar quando alguém roda `kobe-remember`.

**Por quê:** a dor original do projeto, nas palavras dele em 27/08/2026 — *"quando
eu pedia para retomar o assunto, existiam coisas que já tinham sido discutidas,
sobre as quais decisões já haviam sido tomadas, e que você retomava como questões
em aberto."* A F3 passou a REGISTRAR o que foi decidido e o que foi fechado; ela
não fez esse registro chegar ao agente sozinho. Este commit é a metade de leitura
do que faz chegar.

**Foi feito:**

- **`bot/memory/boletim.py` (novo)** — o que o turno executa: caminho do arquivo,
  teto, render puro e um `carregar()` que **nunca levanta**. Sem banco, sem rede:
  `open()` + `read()`. A direção da dependência é só uma — `lucien` importa
  `memory`, nunca o contrário —, e é ela que garante que o caminho quente não
  tenha como consultar nada.
- **O arquivo é chaveado pelo `topic_id` (UUID), não pelo slug.** Não é gosto, é
  correção: `get_topic_slug` devolve `None` para tópico sem `current_name` (os
  pré-v0.10), e dois nomes distintos podem slugificar igual ("Café" e "Cafe") —
  o que faria dois tópicos escreverem no mesmo arquivo, em silêncio. E custa
  zero: `topic_id` já está em escopo nos dois caminhos que montam prompt.
- **O orçamento é 800 tokens, e não os 1.200 do briefing — medido, e a medição
  apontou para BAIXO.** Os 1.200 eram valor inicial declarado, não medição.
  Medindo sobre os 968 turnos reais do operador no tópico de maior volume, com a
  régua que a F2 calibrou (`SEARCH_PISO_COS = 0,57`, sobre a própria tabela
  `messages`): um bloco de 25 linhas (= 1.200 tokens) entrega **24 linhas fora do
  assunto na mediana**, cruzando o marco de ~20 documentos irrelevantes em que a
  pesquisa de 27/08 mede a precisão caindo de ~72% para ~57%. 800 tokens
  (~16 linhas) fica abaixo com folga. A analogia é frouxa (o estudo mede
  documentos; aqui é frase) e por isso vale como ordem de grandeza — na dúvida,
  erra-se para baixo.
- **Os pesos dos blocos foram redistribuídos pela mesma medição:** "o que vale
  hoje" caiu de 55% para 35% por ser o único bloco aberto e escolhido por
  recência pura — o de pior perfil de ruído. "Pendências abertas" (40%) e "o que
  saiu de cena" (25%) cresceram porque valem **justamente por serem
  independentes da pergunta**: são a cura da dor original, e valem mesmo quando
  o turno é sobre outra coisa.
- **O teto se declara em tokens e trabalha em chars**, com razão conservadora
  (3,2 chars/token, abaixo da faixa usual do português) — erro seguro é supor
  poucos chars por token. Sem tokenizer: o único exato seria chamada de rede, e
  o `tiktoken` seria preciso sobre a coisa errada (é o BPE da OpenAI, não o
  tokenizer do Claude).
- **`BOLETIM_TOKEN_BUDGET` e `BOLETIM_CHARS_POR_TOKEN` são configuráveis, e
  presos em faixa no código** (400–1.000 e 2,5–5,0). A trava não é burocracia:
  um teto de 20.000 tokens não é configuração agressiva, é o bloco comendo o
  prompt; e uma régua de 10 chars/token não relaxa um orçamento, faz o código
  **contar errado** e estourar o teto achando que respeitou. Valor fora da faixa
  é preso **e registrado** — nunca corrigido em silêncio.
- **Degradar em voz alta, nos dois sentidos.** O rodapé declara quantas
  afirmações ficaram fora do recorte e por onde alcançá-las, porque um bloco de
  16 linhas *parece* o registro inteiro e "não está no boletim" virando "não
  existe" é a mentira que o Highlander existe para não contar. Já ausência do
  arquivo é `None` silencioso — é o caso normal de um tópico que LUCIEN ainda
  não leu.
- **Vaga vazia fica vazia** (`test_t8`). Completar o bloco com linha fora de
  assunto para "aproveitar o espaço" não é neutro: pela pesquisa citada, é pior
  que deixar em branco.

**Testes:** `tests/test_boletim.py`, 18 casos, **todos passando**, sem banco e
sem rede. Cobrem os critérios 1 a 4 do briefing da fase. Dois merecem nota: o
**T-2b** prova a ausência de latência pelo **desenho** (analisa a AST do módulo e
recusa `psycopg`/`openai`/`httpx`, e checa que `carregar()` não recebe conexão) —
o teste de tempo mede a máquina em que rodou, este mede a arquitetura e não
envelhece; e o **T-4** varre arquivo ausente, vazio, só-espaço e com bytes
inválidos, exigindo `None` em todos.

**Reversão:** `git revert <hash>`. Código novo e inerte — nada o chama ainda, e
nenhuma tabela, arquivo ou configuração existente foi tocada. Em runtime,
`BOLETIM_ENABLED=false`.

### fix(lucien): a varredura do passado avisa quando morre — parada normal continua muda (2026-08-31)

**Operador pediu:** que a varredura pare de morrer calada.

**Por quê:** a reconstrução tem um freio — N falhas seguidas do modelo e ela para
sozinha (`falhas_seguidas_max`). **O freio está certo**: três timeouts seguidos é
sinal de provedor instável, e insistir contra isso queima as vagas de lote que a
retomada vai precisar. O defeito era ela parar **em silêncio**. Aconteceu duas
vezes em 31/08/2026 — às 11:28, no lote 48, e de novo às 14:32, no lote 29 — e
nas duas o operador só descobriu porque perguntou outra coisa. **Uma varredura de
horas que para e não avisa é indistinguível de uma que está rodando**, que é o
mesmo mal que o `kobe-lucien status` existe para curar: um agendador que para não
produz erro, produz silêncio.

**Foi feito:**

- **`bot/lucien/aviso.py` (novo)** — o único caminho pelo qual LUCIEN fala com o
  operador. É o corpo do antigo `worker._gritar`, mudado de lugar sem mudança de
  comportamento: mesmo destino **declarado** (`LUCIEN_ALERT_CHAT_ID`), mesmo
  prefixo, mesma regra de nunca derrubar quem chamou. Eram dois lugares
  precisando da mesma coisa (a degeneração da T7 e a varredura), e duas cópias da
  regra de destino é como uma delas acaba divergindo em silêncio. `_gritar`
  virou uma linha delegando.
- **`rodar()` distingue parada normal de anormal**, e a distinção é o conserto:

  | parada | avisa? |
  |---|---|
  | bateu o teto de lotes | **não** — é a varredura terminando o que foi mandada fazer |
  | backlog esgotado | **não** — é o objetivo da coisa |
  | falha isolada | **não** — soluço de rede não é sintoma |
  | freio de falhas seguidas | **sim** |
  | exceção não tratada | **sim**, e a exceção continua subindo |
  | cadeado do banco tomado | **sim** — a passada acabou antes da hora por causa externa |

  Alarme que dispara ao terminar o trabalho é ruído, e ruído tem o mesmo destino
  do silêncio: ser ignorado. A varredura roda com teto por desenho, então ela
  bate no teto quase sempre — avisar ali ensinaria o operador a não ler o canal.
- **O aviso diz o que o operador precisa para decidir:** que parou, por quê,
  quantos lotes fez, quanto falta, e que **é retomável** (o cursor não andou).
  O "quanto falta" é um `SELECT` agrupado, sem modelo — e é **opcional**: se ele
  falhar, o aviso sai sem essa parte. Um aviso de parada que estoura ao montar a
  própria mensagem seria uma segunda falha em cima da primeira, e a que importa
  é a primeira.
- **Na exceção, o aviso sai ANTES do `raise`** — porque o `raise` pode ser a
  última coisa que o processo faz. E a exceção continua sendo exceção: engolir o
  erro para poder avisar trocaria um defeito por outro.
- **O motivo é encurtado em 200 caracteres no aviso (`MOTIVO_MAXIMO`) — achado
  no smoke, não previsto no plano.** Rodando a varredura de verdade contra um
  modelo inexistente, o erro da CLI veio com o envelope JSON junto: ~700
  caracteres de `usage`, `session_id` e `cache_creation` para dizer *"modelo não
  reconhecido"*. Despejar isso num chat é o mesmo que não avisar, porque ninguém
  lê. O motivo inteiro continua no `logger.warning`, que é onde trace pertence.
- **Sem `LUCIEN_ALERT_CHAT_ID` o aviso vai só para o log, e isso é caso
  esperado** — não erro. A varredura roda de um shell qualquer, e um aviso que
  não tem para onde ir não pode virar uma segunda fonte de crash. Mantida a
  regra do destino declarado, **sem** fallback para `KOBE_CHAT_ID`: escolher um
  tópico por conta própria faria uma mensagem de saúde do sistema cair numa
  conversa qualquer, o que é pior que não mandar.
- **Nota de contexto que o briefing supunha diferente:** a varredura **não** roda
  pelo daemon Keyko. A fonte do Keyko dispara a leitura corrente
  (`worker --uma-rodada`); `reconstrucao.rodar()` tem um único chamador em todo o
  repo, o `kobe-lucien reconstruir`. E o helper já carrega as chaves `LUCIEN_*`
  do `.env` antes de despachar (conserto `a79ed94`), então o aviso funciona de
  qualquer shell sem exportar nada.

**Testes:** `tests/test_lucien_divisao.py`, +8 (27 no arquivo), todos sem banco e
sem rede. Cobrem os três gatilhos que avisam, os três casos que **não** avisam, o
aviso que sobrevive à falha da própria medição, e o encurtamento. Conferido que
provam algo, **nas duas direções**: forçando o alarme a disparar sempre, os três
testes de "não avisa" ficam vermelhos; forçando-o a nunca disparar, os três de
"avisa" ficam vermelhos. Smoke de ponta a ponta no `kobe_dev`: varredura real
contra um modelo inexistente, freio disparado em 2 falhas, aviso montado com o
"quanto falta" correto (4 mensagens · 1 lote), **exit 0 sem canal declarado** — e
o envio de verdade pelo Telegram exercitado à parte. Suíte: **1.126 passados**,
`pytest tests`.

**Reversão:** `git revert` deste commit. Nada de schema, nada de migration, nada
de estado — o commit só acrescenta um módulo e um caminho de aviso. Revertido, a
varredura volta a parar em silêncio (o comportamento anterior), sem nenhum outro
efeito.

### feat(lucien): o marco da reconstrução passa a ser GRAVADO — o teto parou de fugir pra frente (2026-08-31)

**Operador pediu:** consertar o achado que ficou de fora da F3 por exigir
migration — *"o marco da reconstrução é derivado, não gravado; o alvo foge pra
frente"*.

**Por quê:** a varredura do passado lia de zero até um teto por tópico, e esse
teto não estava gravado em lugar nenhum: era o cursor `incremental` **lido na
hora** — o cursor que anda com a conversa. Em linguagem de banco, em vez de
fincar um snapshot no `init`, a rotina relia o `last_seq` corrente a cada
iteração, e o fim da tabela fugia enquanto ela lia. Cada mensagem que a leitura
corrente processava entrava também na conta do que faltava reconstruir, e o
número de "pendente" nunca chegava a zero enquanto houvesse conversa
acontecendo. **O estrago era de cota, não de dado** — a T8 (dedupe) segura a
duplicata, então o pior caso era reler lote já lido e pagar o modelo de novo.

**Foi feito:**

- **`infra/migrations/009_lucien_marco.sql`** — a restrição
  `lucien_cursor_scope_check` passa a aceitar um terceiro escopo, `marco`, e um
  backfill copia cada cursor `incremental` existente para ele. Aditiva e
  idempotente: a restrição é removida com `IF EXISTS` antes de ser recriada
  (`ADD CONSTRAINT` não tem `IF NOT EXISTS` no Postgres), e o backfill é
  `ON CONFLICT DO NOTHING`. Reaplicada duas vezes à mão num banco de apoio, sem
  erro e sem efeito.
- **A OUTRA restrição da 008 ficou como está.** `lucien_runs_mode_check` é sobre
  o **modo da rodada**, e nenhuma rodada roda com modo `marco` — fincar um marco
  não é ler um lote. Alargá-la seria afrouxar uma garantia sem ganho.
- **O backfill copia o valor CORRENTE do incremental, de propósito.** O marco
  original desses bancos nunca foi gravado, então não há como recuperá-lo.
  Copiar o que existe hoje congela o teto exatamente onde ele está: o backlog
  logo depois da migration é numericamente **igual** ao de logo antes — não
  encolhe (que seria repetir o achado 1 da F3) e não cresce. A migration **não
  devolve** a cota já comprometida; ela para a sangria. Medido no `kobe_dev`:
  3.605 mensagens antes, 3.605 depois, nos 8 tópicos.
- **`planejar()`** calcula `(reconstruction, marco]` — intervalo fixo, que
  converge. Tópico **sem** marco produz backlog zero, que é a mesma semântica de
  antes para o tópico onde o `init` nunca rodou.
- **`store.marco_incremental()` → `store.marco_reconstrucao()`**, lendo o escopo
  novo. E devolvendo **`0`, nunca `None`**: `montar_lote` trata `teto_seq=None`
  como "sem limite superior", então a versão antiga fazia a varredura de um
  tópico sem cursor sair lendo a conversa inteira, inclusive a de hoje. Falha
  aberta que virou falha fechada — buraco latente achado ao mexer, fechado junto.
- **`store.fincar_marco_cursor()`** grava com `ON CONFLICT DO NOTHING`, e não com
  o `GREATEST` do `_avancar_cursor`. A diferença é o conserto inteiro: os outros
  dois escopos marcam **progresso** e sobem conforme se lê; o marco marca uma
  **fronteira declarada**, e fronteira que anda não é fronteira. Sem essa regra,
  um `init` rodado meses depois empurraria o marco para o topo de hoje e
  **inflaria** o backlog com tudo que a leitura corrente já leu — o mesmo mal do
  defeito original, no sentido contrário.
- **`init` ficou enfim idempotente por inteiro.** A F3 curou a perna que
  encolhia; esta cura a que inflaria. E ele agora **relata** o marco de cada
  tópico e se ele já existia — a lição do achado 1: comando que mexe em cursor e
  não diz o que fez tem modo de falha silencioso.
- **`kobe-lucien status`** passa a contar o escopo `marco` para decidir se diz
  *"o marco ainda NÃO foi fincado"*. Contar o incremental ali afirmaria que a
  fronteira foi declarada num banco onde só a leitura corrente rodou — a mesma
  confusão entre progresso e fronteira, de outro ângulo.
- **Docstrings corrigidas junto**, não depois: o cabeçalho de `reconstrucao.py`
  afirmava *"ela lê de zero até o cursor incremental daquele tópico"*, que este
  commit torna falso.
- **`tests/fixtures/schema_expected.json` regenerada** de um banco de apoio novo
  (`kobe_ref_009`), erguido do zero por `provision_db.py` + `migrate.py up` — o
  caminho documentado. O diff tem exatamente duas linhas: a `009` na lista e a
  restrição alargada.

**Testes:** `tests/test_lucien_reconstrucao.py`, +5 testes de integração (10 no
arquivo). O central é
`test_o_backlog_nao_cresce_quando_a_leitura_corrente_anda`: finca o marco no
meio do acervo, faz o cursor incremental subir até o topo — o trabalho normal da
leitura corrente — e exige que o backlog **não mude**. Conferido que ele prova
algo: apontando `planejar()` de volta para o escopo `incremental`, ele e o
`test_sem_marco_nao_ha_backlog_mesmo_com_cursor_incremental` ficam **vermelhos**
(1.934 ≠ 0), e voltam ao verde com o código correto. Migration reaplicada duas
vezes à mão num banco de apoio (idempotência). Smoke no `kobe_dev`:
`kobe-lucien status` e `kobe-lucien init --ensaio`, backlog 3.605 → 3.605 e marco
preservado nos 8 tópicos. Suíte: **1.116 passados**, `pytest tests`.

**Reversão:** `git revert` deste commit desfaz o código. O banco precisa de dois
passos, **nesta ordem** (a restrição antiga recusa as linhas novas): restaurar
`lucien_cursor_scope_check` com os dois valores originais (`incremental`,
`reconstruction`) e, antes disso, remover as linhas de `lucien_cursor` onde
`scope = 'marco'`. Reverter é seguro: sem o marco o código volta a dizer *"marco
não fincado"* e a varredura fica sem o que fazer — **nenhuma afirmação já
gravada é afetada**, e a migration nunca apagou nada. Um banco que fique com a
`009` aplicada e o código revertido também não quebra: a restrição só está mais
larga do que o código usa.

### fix(f3): janela de eco na camada ESTADO, e critério de *speech act* na escrita (2026-08-31)

**Operador pediu:** consertar o terceiro achado do LUCIEN — *"a camada ESTADO
não tem janela de eco, e não distingue declaração de pergunta"* — **com a trava
de código**, não só com instrução de prompt (era a decisão em aberto do plano;
ele escolheu a recomendação).

São duas frentes independentes, e as duas entram aqui porque são o mesmo defeito
visto de dois lados: **o registro afirmando posição que o operador não tomou.**

#### (a) LEITURA — a janela de eco, que só a evidência tinha

A camada de EVIDÊNCIA ignora por padrão os últimos 90 s (`JANELA_ECO_S`, com
`--agora` para desligar) por um motivo mecânico: **o bot grava a mensagem do
operador em `messages` ANTES de rodar o turno**. Sem isso, a busca acha a própria
pergunta e responde com ela.

A camada ESTADO não tinha esse mecanismo, e aqui o buraco é pior: uma afirmação
nascida da mensagem que o operador acabou de mandar voltava, no mesmo turno,
dentro do bloco *"o que vale hoje"*, **com carimbo de curado e origem citada**.
Uma dúvida de trinta segundos atrás parecendo decisão vigente e conferível é o
falso positivo com mais autoridade que esta fase pode produzir.

- `buscar_estado()` ganhou `janela_eco`, com a **mesma constante da evidência**
  (`bot.search.query.JANELA_ECO_S` — uma fonte só, para as duas não divergirem
  em silêncio). Filtro pela data da **mensagem de origem**, nas quatro pernas e
  também nas superadas ligadas.
- `kobe-remember --agora` desliga **as duas camadas juntas**.
- E a saída **diz quantas a janela cobriu** (`ℹ️ a janela de eco cobre N
  afirmação(ões) nascida(s) nos últimos 90s`). Esconder em silêncio é o mesmo
  defeito visto do outro lado — a evidência já reportava isso.

#### (b) ESCRITA — pergunta não é decisão, e fala do agente não é posição do operador

Medido na bateria `f3-superacao`: LUCIEN registrou como **pendência em aberto**
uma pergunta do **próprio agente** — os três caminhos para o normalizador que o
agente ofereceu (`#3639`) e que o operador nunca pediu nem aceitou. Fui conferir
no banco de dev antes de escrever uma linha: das três afirmações lá, essa é a
única com origem em fala do agente, e é exatamente essa.

- **Prompt** (`O_QUE_E_DURAVEL`): critério explícito de *speech act* — `decision`,
  `open` e `preference` são posições DO OPERADOR; pergunta não é decisão;
  proposta do agente que ele não aceitou **com palavras** não é estado; silêncio,
  "deixa eu pensar" e mudar de assunto **não são aceite**. Trava assimétrica
  declarada: na dúvida, **não registre**.
- **Trava T10** (`store.py`, e agora são dez): afirmação de `decision`, `open` ou
  `preference` nascida de fala do agente **sem nenhuma fala do operador
  sustentando** (origem ou evidência) é **recusada**. `fact` fica de fora de
  propósito — o agente descrevendo como o sistema funciona é origem legítima de
  fato.
- **A porta que fica aberta é o caso real:** quando a substância está na fala do
  agente e o operador aprovou ("pode", "fechado"), basta a mensagem dele estar em
  `evidence_seqs` — que é o que o prompt manda fazer. A trava não pergunta quem
  escreveu a frase; pergunta se **o operador está na conversa que a sustenta**.
- **A recusa é CONTADA** (vira `Recusa`, entra em `claims_rejected` e aparece no
  `relatorio`). Trava que descarta em silêncio é o mesmo defeito da origem
  inventada, visto do outro lado.

Assimétrica de propósito: uma decisão que ficou de fora reaparece na próxima
conversa; uma proposta do agente virando estado vigente só é descoberta quando
alguém agir sobre ela. **Nada do que já está gravado é alterado** — a trava vale
para escrita nova.

#### Dois consertos de teste que a T10 expôs (aprovados no plano)

- `_vigentes()` passou a recortar pelo `run_id` de uma rodada `model='teste'`, que
  só existe dentro da transação revertida. Sem isso, um banco de integração com
  afirmações no tópico fazia `assert not _vigentes(...)` falhar por **resíduo** —
  era o teste `test_T1_...` acusando uma gravação que não houve.
- A fixture `lote` passou a pegar **duas mensagens do operador**. No acervo real
  a mensagem de texto mais recente é quase sempre do agente, e com a T10 os
  testes das outras nove travas passariam a medir a T10 por acidente. Na mesma
  linha, os dois testes de cursor passaram a **fixar** o ponto de partida: o
  cursor real do tópico pode estar acima do lote, e `GREATEST` faria "avançou até
  o fim do lote" ser medido contra um número que a rodada não escreveu.

**Testes:** `tests/test_lucien_consulta.py` +8 (4 de unidade sobre as quatro
pernas e a contagem; 2 de integração com **dado inserido e transação revertida**:
uma mensagem de agora e uma afirmação nascida dela, que **não** volta como "o que
vale hoje" e **volta** com `--agora`); `tests/test_lucien_store.py` +7 sobre a
T10 (os três tipos recusados, a recusa contada em `claims_rejected`, a evidência
do operador liberando, `fact` isento e a decisão do operador intocada). Removendo
o filtro de eco do código, os dois testes da janela quebram. Suíte: **947
passando + 164 pulados** sem banco; **1111 passando, 0 pulados** contra
`postgresql:///kobe_dev` — inclusive o teste de integração que estava vermelho
antes desta sessão. Fumaça na CLI: `kobe-remember --estado` contra o banco de
dev responde igual.
**Nenhum comando tocou o banco de produção.**

**Reversão:** `git revert` deste commit. Sem schema, sem migration, sem dado
convertido: a leitura volta a não filtrar e a T10 deixa de existir. As
afirmações escritas enquanto ela valia continuam válidas — ela só recusa, nunca
grava.

### fix(f3): `kobe-lucien` lê a chave no `.env` e **diz de onde leu** (2026-08-31)

**Operador pediu:** consertar o segundo dos três achados do LUCIEN —
*"`kobe-lucien status` diz DESLIGADA com a chave ligada (não lê o `.env`)"*.

**Por quê:** o helper lia `LUCIEN_ENABLED` do ambiente do **processo**. Rodado
de um shell qualquer — que é exatamente como se roda um comando de diagnóstico —
imprimia `chave LUCIEN_ENABLED: DESLIGADA` com a chave ligada no arquivo e a
fonte registrada no Keyko (conferido em 30/08/2026: o `journalctl` mostrava
`source registrada: lucien` no mesmo minuto). É o pior lugar possível para esse
erro: `status` existe justamente para **dar voz ao silêncio** — distinguir
"LUCIEN parou" de "não havia nada novo". Errar a primeira linha manda o operador
investigar problema que não existe, ou confirma um falso *"está desligado, pode
mexer"*.

**Foi feito:**

- `_kobe_topic.read_dotenv()` ganhou leitura **por prefixo**: `read_dotenv(set(),
  prefixo="LUCIEN_")` traz a configuração inteira do subsistema. Por prefixo e
  não por lista de chaves de propósito — lista é o que fica desatualizada, e é o
  mesmo erro que `_venv.py` documenta ter cometido três vezes com listas de
  dependências. O modo antigo (por lista) não mudou de comportamento.
- `kobe-lucien` carrega as chaves `LUCIEN_*` do `.env` **antes de despachar
  qualquer subcomando**. Vale para `rodada` e `reconstruir` também: rodados à
  mão, eles caíam nos defaults do código (modelo, tamanho de lote) enquanto o
  `.env` dizia outra coisa — a mesma divergência silenciosa, de outro ângulo.
- O ambiente do processo **vence** o arquivo: quem exportou na mão quis aquilo.
- `status` passa a imprimir a **procedência**: `chave LUCIEN_ENABLED: ligada
  (fonte: /caminho/.env)` · `(fonte: ambiente do processo)` · `DESLIGADA (fonte:
  não achei — nem no ambiente, nem em /caminho/.env)`.

**`DATABASE_URL` ficou deliberadamente FORA do carregamento.** O cabeçalho do
helper declara que não há banco default porque "apontar pro banco errado tem que
custar um ato explícito", e o próprio `bugs.md` que pede este conserto lembra que
já houve comando rodado da pasta de dev acertando o banco de produção. Carrega-se
**configuração**, nunca o **destino** — e há teste guardando isso, porque é o
jeito óbvio de este conserto virar um bug pior que o que ele consertou.

**Testes:** `tests/test_lucien_helper_env.py`, 7 testes **sem banco e sem rede**
(`.env` sintético em `tmp_path`), cobrindo: prefixo traz a config inteira e só
ela; ambiente vence arquivo; leitura por lista inalterada; procedência nos três
casos (arquivo, ambiente, ausente); e `DATABASE_URL` fora — com o `_url()` ainda
saindo com código 2. Fumaça na CLI contra o banco de dev, nos três caminhos: com
`.env` local (`fonte: …/.env`, `ligada`), com a chave exportada por cima
(`DESLIGADA (fonte: ambiente do processo)`) e sem nenhum dos dois (`não achei`).
Suíte completa sem banco: **943 passando + 155 pulados**.
**Nenhum comando tocou o banco de produção.**

**Reversão:** `git revert` deste commit. Não há schema, migração nem estado
persistido envolvidos — o conserto é leitura de arquivo de configuração.

### fix(f3): `kobe-lucien init` não encolhe mais o backlog de reconstrução (2026-08-31)

**Operador pediu:** consertar o primeiro dos três achados do LUCIEN catalogados
em `bugs.md` — *"`init` rodado DUAS VEZES apaga o passado a reconstruir, em
silêncio"* (severidade alta).

**Por quê:** `fincar_marco()` punha dois cursores — o incremental no topo do
tópico e o de reconstrução onde o incremental estava. Na segunda execução isso
apagava o backlog: com o incremental já no topo, o intervalo `(C, M]` vira vazio
e `planejar()` passa a responder **"nada pendente"**, com a mesma cara de quem
terminou o trabalho. Nenhum modelo foi chamado, nada foi escrito, e o passado
sumiu da vista. Visto ao vivo em 30/08/2026: o `status` do dev dizia `3595
mensagens · ~95 lotes`, um `init` rodado como pré-condição de roteiro passou a
dizer `nada pendente`. E a docstring **prometia idempotência** — verdade para o
cursor incremental (o `GREATEST` nunca anda para trás), falsa para o outro, onde
andar para a frente É o dano.

**Foi feito:**

- `fincar_marco()` passa a mexer **só no cursor incremental**. O cursor de
  reconstrução nunca sobe por obra do `init` — ele só sobe quando a varredura de
  fato lê.
- O efeito antigo vira **`kobe-lucien init --refincar`**: ato explícito de quem
  sabe que o passado anterior ao cursor incremental já foi lido.
- `cmd_init` passa a imprimir o **antes/depois do backlog** e, por tópico, o que
  fez com o cursor de reconstrução (`intocada` · `preservada em #N` ·
  `REFINCADA em #N`). Um comando que mexe em cursor tem que dizer o que fez com
  o trabalho pendente — o defeito era ser silencioso.
- A docstring mentirosa foi reescrita, com o registro do que ela prometia.

**A correção sugerida no `bugs.md` não bastava, e é o ponto do conserto.**
*"Só gravar se ainda não existir"* segura o dev (lá o cursor de reconstrução
existe), mas **na produção ele nunca chegou a ser criado** — o incremental
estava em zero na primeira fincada e a guarda `if ja_lido > 0` segurou. Uma
regra por existência deixaria a bomba armada para o `init` seguinte. O
invariante que sobra é mais forte e mais simples, e é o que os testes exercitam.

O preço de não usar `--refincar` no caso legítimo (alguém rodou uma passada
antes de fincar o marco) é a varredura reler `(0, C]`: **cota gasta, nunca
registro perdido** — a T8 (dedupe) segura a duplicata. É a assimetria certa para
um comando cujo modo de falha era silencioso e parecia sucesso.

**Testes:** `tests/test_lucien_reconstrucao.py`, 5 testes de integração
(transação revertida no teardown), **passando** contra `postgresql:///kobe_dev`
— incluindo o que o operador pediu por nome: rodar `init` duas (e três) vezes
**não** encolhe o backlog. Provados por **mutação**: devolvendo o comportamento
antigo ao código, 2 dos 5 falham; restaurado, todos passam. Suíte completa sem
banco: **936 passando + 155 pulados** (baseline 936 + 150 — os 5 novos pulam sem
`KOBE_TEST_DATABASE_URL`, como manda a praxe dos testes de integração da fase, e
passam com ela). Fumaça na CLI contra o banco
de dev, em `--ensaio`: `init` mantém o backlog em 3605 e `init --refincar` o leva
a 0 com o aviso em voz alta. Banco de dev conferido depois: intacto.
**Nenhum comando tocou o banco de produção.**

**Reversão:** `git revert` deste commit. Nada de schema mudou (o conserto é
lógica de cursor sobre a tabela `lucien_cursor` que já existia), então não há
migration a desfazer nem estado de banco a reparar.

### chore(f3): LUCIEN nasce LIGADO — o default vira `true` em todo ambiente (2026-08-30)

Ordem do operador: *"liga o Lucien, quero isso padrão em todos os ambientes,
repo público inclusive caso aplicável"*. A fase nasceu atrás de chave e desligada
de propósito — **publicar o código e ligar a chave são atos separados**, e o
segundo é dele. Ele acabou de exercer o segundo.

**O que muda:** `LUCIEN_ENABLED=true` no `.env.example`, que é o arquivo que uma
instalação nova copia. Os quatro ambientes ficam iguais: dev VPS, prod VPS, repo
dev e repo público.

**Por que isto é seguro num instalador, e não só na VPS de quem mandou ligar:**
instalação nova **não tem passado para ler**. LUCIEN lê por acúmulo, com teto por
hora, e o que ele lê é conversa que ainda não existe. A conta cara do sistema é a
**reconstrução** do histórico (`kobe-lucien reconstruir`) — e ela é um comando à
parte, rodado à mão, que esta chave **nunca** dispara.

**Como sair:** `LUCIEN_ENABLED=false` e reiniciar o Keyko. A fonte deixa de ser
registrada, as cinco tabelas ficam inertes, e nada do que já foi escrito é
apagado. O `.env.example` agora **diz isso no próprio bloco** — porque quem
herda um default ligado precisa achar o caminho de volta sem ler o fonte.

**O teste que guardava o default mudou de lado, e continua guardando o mesmo
valor.** `test_a_chave_e_desligada_no_env_de_exemplo` virou
`test_a_chave_e_ligada_no_env_de_exemplo_e_diz_como_desligar`: o que ele protege
não era o `false`, era a chave não ficar **implícita**. Default que só existe no
código é default que ninguém lê antes de instalar.

**Reversão:** `LUCIEN_ENABLED=false` nos `.env` e `revert` deste commit para o
`.env.example`.

#### A bateria conversacional da F3 rodou, e passou — com um conserto no roteiro

`tests/roteiros/f3-superacao.txt` rodou em dev com LUCIEN ligado. Cenário 3
devolveu **uma** decisão vigente (`#3637`), citada; cenário 4 devolveu a anterior
(`#3636`) como **superada**, com ponteiro para a substituta e data. É a régua da
fase, e ela fechou verde.

**A 1ª execução foi abortada por uma pré-condição que o roteiro não tinha.**
`LUCIEN_INTERVAL_S` (default 300 s) não é a cadência das rodadas — é de quanto em
quanto tempo o Keyko *pergunta* se há lote devido. Com ele no default, a decisão
e a reversão caem no **mesmo lote**: LUCIEN vê as duas juntas, registra só a
segunda, o cenário 3 passa e o cenário 4 dá **falso vermelho** por não haver
superação para mostrar. A pré-condição entrou no roteiro, com o porquê.

**O que a bateria ainda NÃO cobre:** `tests/roteiros/f3-regua.txt`, o teste
histórico contra o passado real. Ele exige que a reconstrução tenha atravessado
julho, e a reconstrução é comando à parte.


### fix(f3): a perna de relevância do estado vigente estava MORTA (2026-08-30)

**O defeito mais grave da fase, e ele só apareceu no uso real.** Nenhum teste de
unidade o pegaria: a função devolvia resultado, sem erro, sem log — só devolvia
**menos** do que devia, e o que faltava era invisível.

#### O sintoma: a régua da fase falhando no exemplo nomeado pelo operador

Depois de a varredura do passado atravessar o incidente de 12–13/06 (o
"Frankenstein"), a decisão **"a sincronização dev VPS → prod VPS deve ser feita
via rsync"** (16/05, `#440`) **continuou vigente**. A decisão que a proibiu
estava lá, gravada, datada, com o motivo — e as duas conviviam como se nada
tivesse acontecido.

**O modelo não errou. A afirmação de maio nunca lhe foi mostrada.**

#### A causa, e ela é de uma linha

`store.estado_vigente` selecionava as afirmações que o modelo pode contradizer
por **recência ∪ casamento por palavra**. O casamento usava
`plainto_tsquery('portuguese', <texto do lote>)` — e esse construtor liga os
termos por **E**.

Um lote de 8.000 caracteres virava uma consulta de **859 lexemas em conjunção**.
Medido: **zero** resultados, enquanto havia **15** afirmações vigentes falando do
assunto daquele lote. A perna estava morta desde o primeiro dia, e sobrava só a
recência — que, num tópico com centenas de afirmações, nunca alcança maio.

#### O conserto, em duas pernas — e a primeira tentativa também errou

**Por palavra**, com os radicais do lote ligados por **OU**. A primeira versão
pegou os radicais mais **raros** do acervo, e isso escolhe *hapax*, não assunto:
no lote do apagão, os 25 mais raros eram `cifr`, `restoring`, `crypt`, `pem`,
`decompiled`… e **`rsync` não entrava**, porque com 136 documentos ele não é raro
o bastante — enquanto o lote inteiro tratava dele. **O que um lote repete é do
que ele trata**, então agora entram duas listas: os mais **frequentes no lote**
(que trazem o assunto) e os mais **raros no acervo** (que trazem identificador e
nome próprio).

Junto veio um segundo erro de aritmética: o corte de banalidade usava
`MAX(ndoc)` como denominador em vez do **número de documentos**. Isso dava um
corte de 109 num acervo de 3.600 mensagens — `rsync` (136 documentos, 3,8% do
acervo) caía fora por um corte calculado sobre a palavra mais comum do acervo.
Agora é `COUNT(*) FROM messages`, como em `bot/search/query.radicais`.

**Por sentido**, com o vetor do lote. É ela que acha a afirmação escrita com
outras palavras — e foi ela que resolveu o caso da régua: o lote da reversão
fala de `git pull`, migrations e manifests, e **não diz "rsync" uma única vez**.

#### A prova, contra o acervo real

Reprocessado o trecho de 09–13/06 do Dev Kobe com a relevância consertada:

```
#440 · "A sincronização entre dev VPS e prod VPS deve ser feita via rsync"
  status: superada · vigente de 2026-05-16 até 2026-06-13
  substituída por #2147 (13/06): "O operador assumiu como erro próprio o uso de
  rsync e determinou que ele deixe de ser usado para qualquer deploy"
```

A data de fim é **13/06** — a data do fato, não a da gravação. E a pergunta da
bateria, pela porta que o operador usa:

> *"a gente pode sincronizar o dev VPS com o prod VPS usando rsync?"*

devolve a decisão de 13/06 como **vigente**, e embaixo, em "o que NÃO vale
mais", **duas** superações do rsync — a de 16/05 e a de 05/06 —, cada uma com o
ponteiro para o que ficou no lugar.

**As superações no registro saltaram de 46 para 79** só com o reprocessamento de
9 lotes.

#### Dois consertos menores, e os dois vieram do mesmo uso real

- **A varredura não parava numa falha que se repete.** Um limite transitório do
  modelo derrubou **70 lotes seguidos em 4 minutos**, ~3,5 s cada. O desenho
  segurou o que importava (nada gravado, cursor parado, cada falha registrada),
  mas o laço **queimou o orçamento inteiro de lotes** contra uma falha que não ia
  se curar três segundos depois — e as vagas gastas eram as da retomada. Agora
  para em **3 falhas seguidas**, com espera crescente. Falha **isolada** continua
  não interrompendo nada.
- **A mensagem de erro não diagnosticava.** `a CLI saiu com código 1: (sem
  stderr)` — porque o código levantava no código de saída **antes** de olhar o
  `stdout`, que era onde vinha o envelope de erro da própria CLI.

**Testes:** 6 novos. Os três da relevância guardam os três erros pelo nome (a
conjunção, o *hapax*, o termo banal); os três restantes cobrem o freio de falha
repetida, a falha isolada que não interrompe, e o diagnóstico que sobrevive ao
stderr vazio. Suíte: **936 sem banco, 1.086 com banco**.

**Reversão:** `git revert`. As afirmações escritas antes do conserto continuam
válidas — o que falta nelas são as **superações** que a relevância morta impediu.
Ver a recomendação de refazer a varredura no relatório da fase.

### fix(f3): o teto manda dividir, e a confiança mede corroboração (2026-08-30)

**Operador pediu** — as duas decisões saíram da leitura do piloto de 5 lotes:

> *"eu não queria que nada que fosse relevante ficasse de fora… na minha cabeça
> eu tinha comentário para 20"*

> *"o fato de vir de áudio precisa ser levado em consideração. Mas não dá pra
> falar sempre 'veio de áudio, confiança é baixa', em todo e qualquer caso. Eu
> posso te mandar uma mensagem curta, três palavras — 'sim, plano aprovado' —
> transcrita sem nenhuma espécie de ambiguidade."*

---

#### 1. O teto por lote: 8 → 20, e o descarte por posição SAI

**O número estava errado e o comportamento estava pior.** O piloto mediu: o
modelo quis escrever **9 a 15** afirmações por lote de 40 mensagens, e o teto
(8, chutado no plano) bateu em **5 de 5 lotes**. As excedentes eram cortadas
**por posição** — a trava não escolhia, e o que se perdia era invisível. Foram 20
afirmações embora.

O comportamento novo, e ele só é natural porque o cursor não tinha andado:

1. batendo no teto, **nada é gravado** e o cursor não avança;
2. o lote é **partido ao meio**, e cada metade vira uma rodada própria, com
   chamada e linha de registro próprias;
3. a divisão para no **piso de 5 mensagens**. Abaixo disso não é lote grande, é
   **degeneração** — aí grava-se o que cabe e a recusa T7 fica **barulhenta**:
   vai ao operador (`LUCIEN_ALERT_CHAT_ID`), não só ao log. É o único caminho em
   que algo se perde, e ele grita.

As metades são **contíguas e cronológicas** (testado): se não se encaixassem, o
cursor de uma passaria por cima da outra e o buraco seria permanente. E a
recursão tem dois freios — o piso e `LUCIEN_DIVISOES_MAX` —, porque um modelo em
laço viraria uma árvore de chamadas, e cada nó dela custa cota.

**Sobre "1.100 afirmações é registro demais":** não é. O registro é
**consultado**, não injetado. Quem tem teto de prompt é o boletim da F4.

#### 2. A confiança deixou de medir o canal e passou a medir corroboração

**Três defeitos numa linha só** (`"baixa" if origem.audio else "media"`), todos
visíveis no piloto:

- ela media o **canal**, não a confiabilidade da afirmação;
- `alta` **nunca era escrita por ninguém** — nível morto num `CHECK` de três;
- e como o operador usa áudio como canal principal, "baixa" saiu em **27 de 40**
  linhas. Um sinal que aparece em dois terços das linhas não distingue nada.

O efeito prático era o pior possível: ou o agente hedgeia tudo — e a F3 não cura
a doença que existe pra curar —, ou ignora a flag, e a mitigação vira teatro.

A régua nova:

| | quando |
|---|---|
| **alta** | há evidência **além** da origem principal (no piloto seriam 35 de 40) |
| **media** | origem única, sem corroboração |
| **baixa** | o trecho que **sustenta** a afirmação está ilegível ou ambíguo |

**O canal continua registrado** — sai como metadado neutro (*"origem em áudio"*),
derivado de `messages.audio_transcribed` na leitura, em vez de copiado para uma
coluna. Ele só deixou de **ser** a confiança.

Na exibição, os três níveis agora aparecem (`corroborada` · `origem única` ·
`⚠ trecho de origem ambíguo`), lado a lado com o canal. Antes era binário: áudio
ganhava carimbo de suspeita e texto não ganhava nada — o canal principal do
operador nascia de segunda classe.

#### 3. Como LUCIEN decide o rebaixamento — e isto é o centro da mudança

`legibility_doubt` é o **único** juízo de confiança que se pede ao modelo, e o
código o aceita **só como rebaixamento**. Não existe campo com que ele se
promova: o princípio da 008 (*"`confidence` é preenchida pelo código, nunca pelo
modelo"*) continua valendo onde importa.

**A pergunta é por AFIRMAÇÃO, não por mensagem:** *o trecho corrompido é
justamente o que **sustenta** esta afirmação?* Se a palavra deturpada está longe
do que sustenta, não rebaixa.

O exemplo no prompt é vivo, e veio do próprio áudio que trouxe esta decisão: a
transcrição chegou como *"o fato de vídeo e áudio precisa ser levado em
consideração"*, onde "vídeo" é ruído sobre "de vir de". **Há corrupção literal na
frase e ela não contamina a afirmação** — o sentido é recuperável e a decisão não
depende daquela palavra.

O prompt traz os dois lados. Marcar: identificador deturpado dentro do que
sustenta (o acervo tem *"Raul"* por *"Hal"*, *"DevCube"* por *"Dev Kobe"*,
*"Koby"*, *"Cade"*), número/data/versão/caminho vindo de áudio, frase truncada,
antecedente ambíguo sem referente. **Não** marcar: mensagem curta e inequívoca
(*"sim, plano aprovado"*), ruído fora do que sustenta, termo técnico correto, e —
explicitamente — **o simples fato de ter vindo por áudio, que sozinho nunca é
motivo**. Sem o lado negativo escrito, o modelo cai no reflexo de carimbar tudo
que é voz, e a ressalva vira ruído.

O operador usa áudio por escolha de produto. Um desenho que pune áudio por ser
áudio contradiz o produto: **o alvo é transcrição ruim, não voz.**

#### 4. O item de conferência do rsync, nomeado na régua

`tests/roteiros/f3-regua.txt` ganha o melhor caso de teste do acervo, e ele saiu
do próprio piloto: **"sincronização dev VPS → prod VPS via rsync"** foi gravada
como decisão vigente de **16/05** (`#440`) — e estava certo para maio. Em
**12–13/06** veio o incidente do "Frankenstein", e em 13/06 a regra se inverteu
em regra dura: *"deploy é git; rsync não é método de deploy de nada"*.

Exercita a fase inteira de uma vez: uma decisão real, datada, que valeu e foi
revertida por um motivo registrado. **Se ela sobreviver vigente depois de a
varredura atravessar junho, a fase falhou a própria régua** — e falhou no
exemplo em que servir a linha velha mandaria o agente fazer exatamente o que
causou o incidente.

**Testes:** 20 novos. `tests/test_lucien_divisao.py` (14) cobre a divisão sem
banco nem modelo — contiguidade, ordem cronológica, convergência, os dois freios
e o aviso barulhento. Os testes da T6 e da T7 em `test_lucien_store.py` foram
**reescritos** para as réguas novas, incluindo o que trava a régua velha (origem
em áudio, corroborada, tem que sair `alta`). Suíte: **932 sem banco, 1.079 com
banco**.

**Reversão:** `git revert`. Nada de banco — as mudanças são de default, de
validação e de exibição. O registro escrito sob a régua antiga fica com a
confiança velha (foi por isso que o piloto foi descartado e refeito).

### docs(claude) + test(f3): a lei das três camadas e os dois roteiros (2026-08-30)

**Operador pediu:** que o `kobe-remember` v2 não quebrasse a v1 — *"os desfechos
`SEM REGISTRO` / `MENÇÃO LITERAL` / `SEM REGISTRO PARCIAL` / `FALHA DO
INSTRUMENTO` continuam valendo e continuam sendo lei do `CLAUDE.md`"* — e que os
dois roteiros da bateria fossem entregues **junto** do plano (§9.6, regra 1).

**Foi feito:**

- **`CLAUDE.md`** ganha a seção das três camadas. Ela **acrescenta** e não
  reescreve: as regras dos quatro desfechos ficam onde estavam, íntegras, e a
  seção nova diz que **o veredito continua sendo da EVIDÊNCIA**. A única
  novidade de conduta é o carimbo `SEM REGISTRO na fala literal — MAS HÁ ESTADO
  REGISTRADO`, que existe porque, havendo estado, o texto antigo (*"não há nada
  sobre isso"*) seria falso.

  Também fica escrito o que **vazio no ESTADO não é**: não é `SEM REGISTRO`. Quer
  dizer que LUCIEN ainda não leu aquele pedaço. Sem essa linha, um bloco vazio
  viraria uma recusa que ninguém autorizou.

- **`tests/roteiros/f3-superacao.txt`** — a régua da fase, com os quatro cenários
  e as esperas `@60`/`@120` do briefing. **Uma adaptação de sintaxe, declarada:**
  o briefing escreve a espera em linha própria e o parser deste repositório lê
  `@N <texto>`. O conteúdo e os tempos são os do briefing; a forma é a que roda —
  roteiro que não parseia foi o defeito nº 9 da bateria da F2.

  O roteiro traz as **pré-condições** sem as quais o resultado não vale:
  `LUCIEN_ENABLED=true`, `LUCIEN_MAX_AGE_S` baixo (é o gatilho por idade que faz
  um lote de 3 mensagens ficar devido — o de acúmulo pede 12 e nunca dispararia),
  `keyko-dev` no ar e `kobe-lucien init` rodado.

  E traz um aviso que o briefing não tinha: **o agente pode acertar o cenário 3
  de memória**, porque a decisão e a reversão estão na janela imediata dele. O
  verde só conta se a resposta mostrar que a ferramenta rodou — citando o
  `#número`. É a mesma regra da F2, aplicada a uma armadilha nova.

- **`tests/roteiros/f3-regua.txt`** — o teste histórico, com o critério
  (**zero falsos "em aberto"**; hoje são três) e a pré-condição de que a
  reconstrução tenha alcançado julho. Se não tiver, isso é resultado legítimo e
  tem que ser **dito**, não virar vermelho da fase.

**Testes:** `tests/test_claude_md_regra_remember.py` (9) segue verde sem uma
alteração — é a prova de que a lei da F2 não foi tocada.
`tests/test_roteiros_parseiam.py` (19) cobre os dois roteiros novos, e as
esperas foram conferidas no parser real: `0 · 0 · 60 · 120 · 10`.

**Reversão:** `git revert`. Documentação e arquivos de roteiro; nada executável.

### feat(f3): `kobe-remember` v2 — ESTADO › EVIDÊNCIA › PISTAS (2026-08-30)

**Operador pediu:** *"`kobe-remember` v2 — passa a devolver três camadas
visualmente separadas: ESTADO (o que vale, curado) › EVIDÊNCIA (as falas
literais, já existentes na v1) › PISTAS (o destilado do Hindsight, só quando o
estado estiver magro). Não quebre a v1."*

**Foi feito:**

- **`bot/lucien/consulta.py`** — a leitura do registro, com **quatro pernas**. As
  três primeiras são as da F2 (literal, palavra, sentido); a quarta é nova e só
  existe aqui: **as afirmações cuja ORIGEM está entre as mensagens que a
  evidência achou**. É a mais precisa das quatro — se a evidência trouxe a `#3059`
  e há uma afirmação nascida dela, ela responde à pergunta por construção.

- **`bot/bin/kobe-remember` v2** — três blocos separados. **O veredito continua
  sendo da EVIDÊNCIA**: ESTADO não vota nele. A única concessão é de texto, e ela
  é obrigatória — quando há estado e a evidência não achou a fala, o carimbo diz
  *"SEM REGISTRO na fala literal — MAS HÁ ESTADO REGISTRADO"*, porque o texto
  antigo (*"não há nada sobre isso"*) seria **falso**. Flags novas: `--estado`,
  `--sem-estado`, `--sem-pistas`.

- **`bot/bin/kobe-lucien`** — `init`, `status`, `rodada`, `relatorio`,
  `reconstruir`, `reverter`. Sem banco default, como o `migrate.py`.

- **`bot/lucien/reconstrucao.py`** — e aqui entrou uma peça que **o plano não
  previu**: LUCIEN nasce com o cursor em zero e 3.620 mensagens atrás dele. Sem
  separar as leituras, a fonte incremental gastaria semanas mastigando julho a
  seis rodadas por hora — e **não veria a conversa de hoje**. Daí os dois
  cursores da 008 e o `kobe-lucien init`, que finca o marco. Ele mexe nos
  **dois**: o incremental vai para o topo, e o de reconstrução vai para onde o
  incremental estava — senão a varredura refaria o trecho que a leitura corrente
  já processou.

**Quatro defeitos achados TESTANDO com dado real, nenhum vindo de revisão:**

1. **O piso de similaridade herdado da F2 estava errado.** 0,57 foi calibrado
   sobre `messages` — texto longo e ruidoso. Afirmações são curtas e densas e
   separam muito melhor. Medido sobre as 40 do piloto, 8 perguntas com resposta e
   8 sobre assuntos inexistentes:

   | | com resposta | sem resposta | folga |
   |---|---|---|---|
   | estado (novo, 0,43) | 0,570 – 0,773 | 0,253 – 0,289 | **+0,281** |
   | evidência (F2, 0,57) | 0,598 – 0,693 | 0,428 – 0,536 | +0,061 |

   Com 0,57 o limiar caía **exatamente em cima** do verdadeiro-positivo mais
   fraco: *"o que a gente decidiu sobre o nome dos ambientes"* voltava vazia com
   a resposta certa a **0,570** no banco.

2. **A perna de palavra estava votando** — regra da F2 que foi replicada pela
   metade. *"o campeonato de xadrez"*, assunto que nunca existiu, voltava com uma
   preferência sobre anexo no Telegram: `campeonat` e `xadrez` são raríssimos,
   mas `decid` não é, e a consulta os unia por `OU`. Agora a palavra **ordena e
   não elege** — só sentido, literal e origem colocam uma afirmação na resposta.

3. **A perna de origem herdava lixo da evidência.** Com veredito `MENÇÃO
   LITERAL` a evidência diz *"não consigo confirmar que responde"*; passar
   aqueles `seq` adiante transformava uma incerteza declarada em afirmação. Os
   `seq` agora só atravessam quando o veredito é `ACHOU`.

4. **Banco fora era relatado como "a tabela não existe".** `to_regclass` devolve
   `NULL` para tabela ausente e **não estoura** — então qualquer exceção ali é o
   Postgres derrubado. Engolir a exceção fazia o comando dizer *"falta a
   migration 008"* com o banco morto. É a mesma mentira que a fase combate, e a
   que este sistema já cometeu duas vezes.

**A latência, que era o risco desta mudança.** A camada nova fazia uma **segunda**
chamada de embedding para a **mesma** pergunta: +0,3 s por comando, resultado bit
a bit idêntico. Memoizado por processo em `bot/search/embedder.embed_um` (falha
**não** é cacheada, senão um tropeço de rede condenaria o comando). Medido, 5
rodadas frias:

    v1, antes           1,55 – 1,88 s
    v2 sem o memo       1,79 – 2,12 s
    v2 com o memo       1,59 – 1,65 s

**A camada de ESTADO passou a sair de graça** — e o critério da F2 (3 s) segue
com folga.

**Calibração completa, 16 perguntas: 8/8 acharam, 8/8 recusaram.**

**Testes:** `tests/test_lucien_consulta.py` (15). Os quatro defeitos acima viraram
teste, cada um com o caso real no docstring. Suíte inteira: **910 passaram, 141
puladas**.

**Reversão:** `git revert`. Com `LUCIEN_ENABLED=false` e o registro vazio, a saída
do `kobe-remember` é a da v1 — os 15 testes de texto da F2 continuam verdes sem
uma alteração, que é a prova de que a v1 não foi tocada.

### feat(f3): a fonte do LUCIEN no Keyko, e a chave — desligada (2026-08-30)

**Operador pediu:** *"LUCIEN — nova fonte do daemon Keyko (o mesmo que já roda
`alertas`, `transcripts` e `search-index`). Roda **fora do caminho do turno**, por
acúmulo, nunca no turno."* E, na aprovação: *"pode criar o keyko-dev, claro.
Quanto mais a gente deixar igual desenvolvimento e produção, melhor."*

**Foi feito:**

- **`bot/lucien/worker.py`** — uma rodada de ponta a ponta, com **três fronteiras
  de transação**, cada uma por um motivo:
  1. a linha da rodada é aberta e **comitada na hora** — se o processo morrer no
     meio da chamada ao modelo, a rodada fica registrada como não-terminada em
     vez de sumir;
  2. a chamada ao modelo acontece **fora de transação** — segurar uma transação
     por 60 s prenderia recursos do banco à espera de algo que nem é do banco;
  3. a escrita é uma transação só.

  **A T9 mora aqui:** qualquer falha entre montar o lote e gravar — modelo fora,
  resposta torta, timeout — descarta a rodada e **NÃO avança o cursor**. O mesmo
  lote é relido depois. Meia rodada gravada seria um buraco permanente no
  registro, e ninguém saberia onde.

- **`bot/lucien/source.py`** — a 5ª fonte do Keyko, com **três proibições**, e a
  primeira é decisão de arquitetura, não economia:
  1. **nunca devolve `Despertar`** — o despertar acorda um `claude -p` que
     escreveria ele mesmo no registro, e a F3 existe para que o modelo proponha e
     o código decida;
  2. **nunca trabalha dentro do `tick()`** (diferente do coletor de transcripts e
     do indexador de busca, e a diferença é de ordem de grandeza: aqueles levam
     milissegundos, uma chamada de modelo leva dezenas de segundos). O Keyko é
     single-threaded — travar o laço travaria os **Alertas**, onde atraso é falha
     que o operador vê. O `tick()` faz só a pergunta barata e dispara um processo
     detached;
  3. **nunca levanta.**

- **A chave, desligada** (`LUCIEN_ENABLED=false`). Com ela off a fonte **não é
  registrada** — não basta existir e não fazer nada: uma fonte que aparece no log
  de inicialização sem trabalhar faz *"quem o Keyko está observando"* deixar de
  ser verdade.

- **`keyko-dev.service` instalado e no ar** (autorizado pelo operador), a partir
  do template que já existia. `TRANSCRIPT_COLLECTOR_ENABLED` fica `false` em dev,
  por causa do aviso de origem compartilhada do `.env.example` — `~/.claude/projects`
  é do host e é um só. **Efeito colateral bom:** o índice de busca de dev passa a
  se manter sozinho, o que fecha o item 5 do "o que fica na sua mão" da F2
  (*"não há keyko-dev instalado: em dev o índice não se mantém sozinho"*).

**Testes:** `tests/test_lucien_source.py`, 16 cenários. As três proibições têm
teste próprio; a chave desligada é provada em dois níveis (a fonte não é
construída **e**, se for construída por engano, o `tick()` não abre conexão); e o
registro do Keyko é conferido nos dois estados. Mais os casos chatos que derrubam
daemon: banco fora, consulta falhando, conexão vazando, `.env` com valor torto
(`LUCIEN_BATCH_MIN=doze` cai no padrão em vez de estourar) e valor absurdo
(`LUCIEN_INTERVAL_S=0` é contido pelo piso).

Suíte inteira: **896 passaram, 139 puladas**.

**Reversão:** `LUCIEN_ENABLED=false` (já é o default) desliga tudo sem tocar em
banco. Para tirar o `keyko-dev` do ar: `systemctl --user disable --now
keyko-dev.service` — ele é unidade de **desenvolvimento**, não existe em produção,
e a produção segue com o `keyko.service` de sempre.

### feat(f3): o núcleo do LUCIEN — o modelo propõe, o código decide (2026-08-30)

**Operador pediu:** o LUCIEN da F3 — *"ele lê o que entrou de novo e faz DUAS
perguntas: que afirmações duráveis isto estabelece? e — a que hoje não existe em
lugar nenhum — alguma delas contradiz, fecha ou abandona alguma das que já
valem?"*

**Por quê:** é a segunda pergunta que cura a dor. Hoje nada no Kobe registra que
um assunto fechou, e por isso decisões de julho voltam à mesa como se estivessem
em aberto.

**A decisão de arquitetura, e ela é a fase inteira:** o briefing declara a F3
como *"a única fase onde um modelo escreve estado que eu depois sirvo como se
fosse conhecido"*. A resposta deste código é uma só — **o modelo não escreve no
banco**. Ele recebe um lote e devolve JSON; quem escreve é `store.aplicar`, que
valida campo por campo. É o mesmo princípio que o `CLAUDE.md` já aplica aos
Alertas: *"a lógica determinística é do CÓDIGO; o modelo só é invocado para
LINGUAGEM"*.

Por isso LUCIEN **não** usa o `Despertar` do Keyko: aquele caminho acorda um
`claude -p` que escreveria ele mesmo — o modelo com a caneta na mão.

**Foi feito:**

- **`bot/lucien/store.py`** — a **única** porta de escrita do registro. Nove
  travas, e nenhuma confia no modelo:

  | | o que ela impede |
  |---|---|
  | **T1** | citar uma mensagem que não estava no lote mostrado |
  | **T2** | superar/encerrar uma afirmação que não lhe foi mostrada, ou já mudada na mesma rodada |
  | **T3** | superação sem motivo escrito |
  | **T4** | vocabulário fora do enum, texto fora do tamanho |
  | **T5** | `valid_from` da gravação em vez da data do fato |
  | **T6** | origem de áudio entrando com confiança normal |
  | **T7** | um lote virar 30 afirmações |
  | **T8** | releitura do mesmo lote duplicar |
  | **T9** | resposta torta gravar metade (fica no worker) |

  **A T1 é a que sustenta a fase.** O banco já garante que a mensagem citada
  existe; a T1 garante que **o modelo a viu**. Sem ela, uma citação plausível de
  um assunto que o modelo conhece de outro lugar entraria com cara de origem
  conferida — e origem conferida é o que faz o agente servir a linha como fato.

  Conexão própria e transação de verdade (a ponte do bot é um pool em
  autocommit): criar a afirmação nova, fechar a que ela supera, gravar os eventos
  e avançar o cursor **ou acontece inteiro, ou não acontece**. Um registro com a
  superação gravada e a substituta ausente diria que uma decisão foi revogada por
  nada.

- **`bot/lucien/prompts.py`** — o contrato. Não se pergunta ao modelo nada que o
  código saiba melhor: a data sai do `created_at` da origem, a confiança sai de
  `audio_transcribed`, e a afirmação a superar é indicada por **apelido**
  (`E1`, `E2`…) e não por UUID — um UUID quase-certo é indistinguível de um certo
  até a chave estrangeira estourar; um `E9` que não existe é recusado sem
  ambiguidade.

- **`bot/lucien/brain.py`** — a chamada e o parser. Pela **assinatura**, zero de
  API paga. Isolada como a F0.5 ensinou (cwd neutro, `CLAUDE_CONFIG_DIR`
  temporário, `CLAUDE_SECURESTORAGE_CONFIG_DIR` vazio) para não disparar os hooks
  e plugins do operador dentro de uma chamada de LLM.

  O parser aceita **três formas** (envelope da CLI, bloco cercado, JSON cru)
  porque o formato de saída era a premissa mais frágil do plano. E ele **levanta**
  em vez de devolver vazio: um JSON truncado parseado com tolerância viraria
  "nada durável", o cursor avançaria, e o pedaço da conversa se perderia **para
  sempre, sem rastro**. Este sistema já transformou falha de instrumento em "não
  há registro" duas vezes; `CerebroIndisponivel` existe para isso não ter uma
  terceira.

**Duas medições que mudaram o código, feitas na hora:**

1. **O contrato de saída da CLI (risco R1 do plano, que eu tinha declarado como
   não-verificado): CONFIRMADO.** `claude -p --output-format json` devolve um
   envelope com metadados e a resposta em `result`, e o parser leu.
2. **O contexto por chamada: 43.039 → 13.154 tokens**, com `--tools ""`,
   `--strict-mcp-config` e um `--system-prompt` curto. São 70% a menos, e a
   chamada seguinte leu os 13k do **cache** e criou zero — ou seja, uma
   reconstrução de ~145 chamadas seguidas paga o prefixo **uma vez**. Foi o que
   tornou a varredura do passado inteiro viável em cota, e não só em teoria.

**Testes:** **47 novos**, nenhum deles chamando modelo.

- `tests/test_lucien_store.py` (28) — cada trava é uma tentativa de fazer o
  LUCIEN escrever o que não deveria, com a asserção de que **não passou**. A
  proposta é construída à mão, que é exatamente como um modelo alucinando a
  entregaria. Tudo em transação revertida.
- `tests/test_lucien_brain.py` (19) — 5 respostas boas e 6 tortas; as tortas
  **levantam**, incluindo a truncada, que é a perigosa.

**E o smoke com conversa de verdade** (8 mensagens reais do tópico AMBIENTE DEV,
15 mil caracteres): o modelo devolveu 4 afirmações, **zero recusas**, e a
conferência linha a linha contra o banco mostrou origem dentro do lote e
`valid_from` igual à data da mensagem de origem em todas. Uma delas foi um
`open` correto — *"lista de oito defeitos aguardando decisão do operador"*.
Rodada revertida; nada ficou gravado.

**Reversão:** `git revert`. Nenhuma escrita em banco fora de teste revertido;
nada ligado (a chave vem no commit seguinte, desligada).

### feat(f3): migration 008 — o registro de estado (2026-08-30)

**Operador pediu:** a F3 do Highlander v3 — *"as tabelas do registro de estado,
com vigência (`valid_from`/`valid_to`), ponteiro de substituição, e origem
obrigatória em toda linha. Sem origem, a linha não entra."*

**Por quê:** a dor original do projeto, na palavra dele em 27/08/2026: *"quando
eu pedia para retomar o assunto, existiam coisas que já tinham sido discutidas,
sobre as quais decisões já haviam sido tomadas, e que você retomava como questões
em aberto."* Medido no diagnóstico: perguntando ao Hindsight de produção *"o que
já foi decidido sobre a arquitetura de borda?"*, ele devolveu **como "em aberto"
três coisas fechadas em julho**.

A causa raiz, em uma frase de banco: a memória é um **log de INSERT sem UPDATE e
sem tombstone**. Registra todo pedido, nunca registra que o pedido foi
respondido, e não tem como um fato novo invalidar um velho. **Falta o
`valid_from`/`valid_to`** — uma dimensão de mudança lenta que nunca foi modelada.

**Foi feito** — cinco tabelas novas, nenhuma alteração em tabela existente:

- **`lucien_claims`** — uma linha = uma afirmação durável, com vigência, ponteiro
  de substituição e **origem obrigatória**. `valid_from` recebe a data do FATO
  (o `created_at` da mensagem de origem), nunca a da gravação: a reconstrução do
  passado vai criar, numa madrugada de agosto, afirmações que passaram a valer em
  julho. Com `NOW()`, o registro inteiro nasceria dizendo que tudo foi decidido no
  dia em que foi catalogado — a mesma classe de mentira que a fase existe para
  matar.
- **`lucien_claim_evidence`** — as outras mensagens que sustentam a afirmação. É
  tabela e não `BIGINT[]` de propósito: array não tem chave estrangeira, e um
  número de mensagem que não existe é exatamente o que não pode entrar.
- **`lucien_events`** — a imagem ANTERIOR de cada mudança (`before`). É o que faz
  `kobe-lucien reverter` ser possível, e é o caminho de volta de uma superação
  errada — o modo de falha mais caro desta fase, porque ela **esconde** uma
  decisão que continua valendo.
- **`lucien_runs`** — o que cada rodada viu e fez, com `claims_rejected`. Essa
  coluna é a mais importante da tabela: se o modelo começar a inventar origem,
  ela sobe e **alguém enxerga**. Recusa silenciosa é o mesmo defeito, do outro
  lado.
- **`lucien_cursor`** — até onde se leu, por (escopo, tópico). O cursor **não
  avança quando a rodada falha**, então falha de modelo ou de rede vira releitura
  e não buraco permanente.

**A decisão de desenho que sustenta a fase:** `source_message_id` é `NOT NULL`
com chave estrangeira para `messages`. Origem obrigatória deixa de ser convenção
e vira **restrição de integridade** — mesma lógica do `work_sessions.system_id`
da F1. Há uma segunda trava, esta no código (vem no commit seguinte): o `seq`
citado tem que estar **no lote que foi mostrado ao modelo**. O banco garante que
a mensagem existe; o código garante que o modelo a viu.

Mais quatro `CHECK` que o esquema do briefing deixava passar: vigência e
`valid_to` não podem discordar; o ponteiro de substituição só existe em quem foi
substituído; nada substitui a si mesmo; e os quatro vocabulários (`kind`,
`status`, `confidence`, `created_by`) são fechados.

**Nada é específico de um tópico.** Regra do operador de 30/08: *"nada que a
gente vai construir é específico do Dev Kobe"*. O registro é global **com coluna
de tópico** (decisão E5).

**Testes:** `tests/test_state_registry_schema.py`, 24 cenários — 9 sobre o
arquivo (rodam em clone limpo, sem banco) e 15 sobre o banco, cada um numa
transação revertida. As travas foram **exercitadas, não prometidas**: origem
ausente, origem inventada, vigente-com-`valid_to`, superada-sem-`valid_to`,
auto-substituição e os quatro enums — **todas recusadas pelo banco**; e o caminho
feliz funciona (senão a tabela seria impossível de escrever). Suíte inteira:
**860 passaram, 112 puladas**. Migration aplicada em **`kobe_dev`** (alvo
conferido antes de cada comando), reaplicação inofensiva, `compat_gate` verde.
Referência regenerada — o diff é **puramente aditivo**: entrou a `008` na lista
de versões e as cinco tabelas; **nenhum campo de ambiente mudou** (collation,
`data_checksums`, encoding e fuso seguem idênticos), então o portão de produção
não vira vermelho por causa disto.

**Reversão:** `LUCIEN_ENABLED=false` — as cinco tabelas ficam **inertes**, nada é
apagado, e nada muda no prompt do turno (a F3 é consultada sob demanda, não
injetada). Para desfazer no banco de desenvolvimento, é preciso remover as cinco
tabelas (`lucien_cursor`, `lucien_events`, `lucien_claim_evidence`,
`lucien_claims`, `lucien_runs`, nesta ordem, por causa das chaves estrangeiras),
tirar a linha `008` de `schema_migrations` e regenerar a referência. Em produção
a 008 ainda **não foi aplicada** — é ato do agente principal.

### fix(helpers): o re-exec no venv deixa de conferir uma LISTA de dependências (2026-08-30)

**Operador pediu:** consertar o `bot/bin/kobe-recall-since`, que quebrava no
runtime do agente com `No module named 'psycopg_pool'` — *"de forma que funcione
por caminho relativo, de qualquer cwd, sem venv ativo"* —, e conferir o mesmo
defeito latente em `kobe-work-session` e `kobe-collect-transcripts`.

**Por quê:** `kobe-recall-since` é o comando que o protocolo de run em background
manda o agente rodar para ler a **janela de frescor** — o que o operador disse
DEPOIS que o pedido foi despachado. Ele quebrado significa que **toda run de
background perdia a chance de ver um follow-up ou um "deixa pra lá"**. Um
mecanismo de segurança desarmado, e em silêncio: `No module named 'psycopg_pool'`
não se lê como *"a janela de frescor está cega"*.

A causa imediata era o shebang `#!/usr/bin/env python3` (o Python do SISTEMA,
que tem `psycopg` e não tem `psycopg_pool`). A causa **de desenho** é outra, e é
ela que foi corrigida: cada helper tinha um guarda próprio que decidia o re-exec
conferindo uma **lista de dependências** — e a lista envelhece sozinha, sem
ninguém mexer nela, quando o helper passa a importar mais uma coisa. **É a
terceira vez que este mesmo defeito aparece:**

| quando | helper | a lista dizia | faltava de verdade |
|---|---|---|---|
| 27/08/2026 (F0.2) | `kobe-reflect` | `psycopg` | `httpx` |
| 27/08/2026 (F0.2) | `kobe-remember` | `psycopg` | `openai`, `dotenv` |
| **30/08/2026 (F3)** | **`kobe-recall-since`** | `psycopg` | **`psycopg_pool`** |

**Foi feito:**

- **`bot/bin/_venv.py`** — o preâmbulo, num lugar só, **sem lista**. A pergunta
  deixa de ser *"falta alguma dependência?"* e passa a ser *"já estou no venv do
  projeto?"*. Não há lista para ficar velha, então o bug não volta por
  esquecimento: um helper que passe a usar uma biblioteca nova amanhã já está
  coberto hoje.
- Quatro garantias, cada uma nascida de um modo de falha real: âncora em
  `__file__` (funciona por caminho relativo, de qualquer cwd); sentinela no
  ambiente contra laço de `exec`; **nunca levanta** (um preâmbulo que derruba o
  `kobe-notify` seria pior que a doença); e **só troca o interpretador de quem é
  o programa em execução**, nunca de quem apenas **importa** o helper.
- Convertidos: `kobe-recall-since`, `kobe-await-response`, `kobe-normalize-report`,
  `kobe-work-session`, `kobe-collect-transcripts`, `kobe-reflect`,
  `kobe-remember`, `kobe-alerta` e `kobe-integrations` (este não tinha guarda
  nenhum — funcionava por sorte). `_kobe_topic._garantir_psycopg` passa a usar o
  mesmo caminho, mantendo o re-exec **tardio**.
- **`kobe-notify` e `kobe-attach` NÃO foram tocados, de propósito.** Eles são o
  canal com o operador e são stdlib-pura no caminho comum; fazê-los depender do
  venv trocaria um defeito raro por um pior — o relatório não chegar. O teste
  cobra deles exatamente o contrário.
- **`tests/test_helpers_venv.py`** — a trava. Lê os arquivos e recusa (a) lista
  de dependências de volta, (b) `os.execv` reimplementado num helper, (c) helper
  com dependência de terceiros sem `_venv.ensure()`. Roda **sem banco e sem
  rede**, em todo `pytest`.
- `tests/test_kobe_remember.py` — o teste que cobrava a lista completa passa a
  cobrar o oposto: que não haja lista.

**Testes:** suíte inteira **847 passaram, 101 puladas** (dev VPS, `kobe_dev`).
Os três helpers nomeados pelo operador foram exercitados **no caminho real** —
`/usr/bin/python3`, cwd `/tmp`, e também por caminho relativo sem venv ativo:
`kobe-recall-since` voltou a ler a janela (50 mensagens num tópico real de dev,
antes `No module named 'psycopg_pool'`); `kobe-collect-transcripts status` e
`kobe-work-session systems` responderam contra o banco; `kobe-remember "rsync"`
(o de mais dependências) citou `#3584`/`#3599`. `kobe-notify` foi testado
rodando **no python do sistema, de `/tmp`** — o canal segue de pé.

Dois defeitos do próprio conserto foram achados testando, e viraram teste: o
re-exec incondicional **trocava o interpretador do pytest** ao carregar um
helper como módulo (a suíte morria em 38% com `rc=1` e sem uma linha de erro —
que é como um `execve` se parece de fora), e um `python -c` não tem script em
disco para re-executar.

**Reversão:** `git revert` do commit. Os helpers voltam ao guarda por lista —
com o defeito junto. Nada de banco, nada de estado, nada de configuração: a
mudança é só de código de inicialização de script.

### test(f2): a bateria executada — 8 cenários, 8 verdes, e 10 defeitos achados no caminho (2026-08-30)

**Operador pediu:** o §3.4 do briefing — *"toda sessão Coder escreve um plano de
testes que ela mesma executa"* — e o §9.4, que declara esta bateria a
**bateria-vitrine**: aqui o teste **é** a entrega.

**Foi feito:** a bateria rodou **quatro vezes**. As três primeiras foram
iterações de desenvolvimento — **cada uma achou defeito** —, e rodar de novo com
o código mudado no meio não vale como registro. A 4ª é o registro de aceite.

**A execução de aceite (30/08, 10:37–10:45), 8 cenários:**

| # | Cenário | Resultado |
|---|---|---|
| 0 | `/nova` | 🟢 |
| 1 | arquitetura de borda | 🟢 `#3059`, `#3104`, `#3421` — e **honesto** que a decisão está no arquivo, não em `messages`: sala de missão não vira histórico |
| 2 | `compat_gate` | 🟢 `#3433`…`#3436`, com as palavras literais do operador e a cadeia causal |
| 3 | rsync | 🟢 `#1828`, `#2074`, `#2075` |
| 4 | paráfrase (git × produção) | 🟢 aponta 12/06 |
| 5 | viagem ao Japão | 🟢 `MENÇÃO LITERAL` → *"não há registro"*, sem costurar |
| **6** | **maratona de São Silvestre — A RÉGUA** | 🟢🟢 **`SEM REGISTRO` por dois caminhos** |
| 7 | regressão | 🟢 **7,0 s** (contra 6,5 s antes da fase) |

**Critério de pronto do §5-F2, medido:** `kobe-remember "arquitetura de borda"`,
processo frio, 10 rodadas — **p50 1,46 s · p95 1,50 s · máx 1,56 s**. O critério
era 3 s.

**O valor principal da fase não foi o verde: foram os 10 defeitos que ela
achou** — nenhum vindo de revisão de código, vários apontados **pelo próprio
agente no meio do turno**. Os quatro mais graves:

1. `kobe-remember "rsync"` devolvia **`SEM REGISTRO`** para um termo em 116
   mensagens — e esse é o carimbo que o `CLAUDE.md` manda tratar como
   **afirmável**.
2. O carimbo `MENÇÃO LITERAL SEM APOIO` **afirmava "nada responde"** sobre um
   conjunto que respondia.
3. Pergunta repetida **canibalizava as próprias vagas** (0,825 do eco contra
   0,614 do melhor resultado real).
4. Um conserto meu quase matou a régua: `integr` (79 mensagens) é **mais raro**
   que `rsync` (116), então nenhum limiar de raridade separava os casos.

Cada um tem entrada própria acima, com o número que o provou.

**Duas coisas que eu afirmei e estavam erradas, corrigidas no registro em vez de
apagadas:** que `/nova` zera a janela de memória (é por `topic_id`, não por
sessão — o agente foi ao código e me mostrou), e o limiar de eco em 0,90,
escolhido com um lado só medido.

**Artefato:** relatório completo em
`user-data/knowledge/kobe/status/2026-08-30-f2-busca-sobre-a-conversa.md`.

**Reversão:** nada a reverter — este commit é registro.

### fix(bateria): correção — `/nova` NÃO zera a janela de memória (2026-08-30)

**Operador pediu:** nada. Isto corrige uma afirmação minha, já commitada.

**O que eu escrevi, e está errado.** Na entrada *"sessão limpa antes de rodar"*
eu afirmei que `/nova` *"arquiva a sessão e zera essa janela"*, e concluí que
com isso a bateria ficava **repetível**.

**Quem me corrigiu foi o agente**, na 4ª execução: ele foi ao código e mostrou
que `bot/memory/working_set.py` filtra a janela imediata por **`topic_id`**, não
por sessão — `get_immediate_messages(db, topic_id)`, teto de 60 mensagens.
Conferido no arquivo: ele está certo.

**O que `/nova` faz, então:** arquiva a **sessão** e faz o agente saber que
começou uma nova. A janela imediata continua enxergando as últimas mensagens do
**tópico**, inclusive as execuções anteriores da bateria. Ele segue dizendo *"de
novo"* e *"o conteúdo é o mesmo de 10:21"* — que foi exatamente o que se
observou.

**O que isolaria de verdade:** cada execução num **tópico próprio** do grupo de
dev (`--thread-id` diferente). Isso depende de o operador criar o tópico no
Telegram, então fica **registrado como recomendação**, não como passo
automático. O roteiro passou a dizer isso, com o aviso no lugar da afirmação
errada.

**E a ressalva do outro lado, para não corrigir demais:** agente quente não é
irreal — em uso normal ele está sempre quente. O que a bateria precisa evitar é
ele ter a resposta da **mesma pergunta** minutos antes.

Suíte: **823 passando**, 101 pulados.

**Reversão:** revert do commit.

### fix(bateria): a régua queima a FORMA da pergunta, não só o termo (2026-08-30)

**Operador pediu:** nada. Descoberto ao preparar a execução de aceite.

**Por quê:** o roteiro já avisava que o cenário anti-invenção queima o termo que
usa. Descobri que é **pior**: ele queima a **forma** da pergunta.

Depois de três execuções com *"o que a gente decidiu sobre integração com o
`<marca>`?"*, uma pergunta nova com marca **inédita** (`Databricks`, zero
ocorrências no acervo) passou a **ACHAR** — e o topo era a pergunta anterior
sobre `Zendesk`, com similaridade **0,661**, acima do piso de 0,57. Trocar só a
marca não basta: a próxima pergunta tem que ter **outra forma e outro domínio**.

**E há um segundo requisito, que só apareceu tentando:** para o desfecho ser o
`SEM REGISTRO` forte, a pergunta precisa conter uma palavra que **nunca**
apareceu no acervo. *"o que a gente combinou sobre o plano de saúde da empresa?"*
dá `MENÇÃO LITERAL` — "combinou", "empresa" e "saúde" existem soltas —, que é uma
recusa mais fraca. *"a maratona de São Silvestre"* dá `SEM REGISTRO`.

**Foi feito:** o roteiro documenta os dois requisitos, manda conferir a
**pergunta inteira** (não o termo) antes de rodar, e registra o que já foi gasto
— inclusive a forma. A pergunta da régua passou a ser *"me lembra o que a gente
combinou sobre a maratona de São Silvestre"*.

**E um aviso novo no desfecho `ACHOU`, que é geral e não da bateria:** um trecho
marcado como `operador` pode ser uma **pergunta** que ele fez, não uma decisão —
e uma pergunta parecida com a de agora é vizinho próximo **por construção**. O
comando manda ler antes de citar como se fosse resposta.

Suíte: **823 passando**, 101 pulados.

**Reversão:** revert do commit.

### fix(remember): pergunta repetida canibalizava as próprias vagas; citação em UTC; trecho vazio (2026-08-30)

**Operador pediu:** nada. Os três saíram da **3ª execução** da bateria, os dois
primeiros apontados pelo agente no meio do turno.

---

**(1) A pergunta repetida se reencontrava — e ganhava.** É o caso **estrutural**
que a janela de eco de 90 s não cobre: uma pergunta já feita antes — dez minutos
ou dez meses — está gravada em `messages`, e a semelhança de uma pergunta com
ela mesma é ~1. Medido no turno: as duas repetições da paráfrase vieram em **1º e
2º lugar, com 0,825**, contra **0,614** do melhor resultado de verdade. Sobraram
**3 vagas úteis de 8**. Palavras do agente: *"a busca recuperou a si mesma, duas
vezes, no topo"*.

**Conserto:** candidatos acima de `TETO_ECO_COS` são descartados — são a
pergunta, não uma resposta a ela. E o descarte acontece **antes** do cálculo do
topo, senão o eco decide o veredito.

**O teto tem os dois lados medidos, e a primeira versão dele não tinha:**

```
melhor resultado VERDADEIRO em todo o acervo (16 perguntas) ...  0,693
--------------------------------- 0,75 ---------------------------------
eco observado (a mesma pergunta se reencontrando) .............  0,825
```

Escrevi **0,90** primeiro, "com margem de sobra" sobre o 0,693 — e **sem olhar o
número do outro lado**. Não teria pego o caso real. Um limiar com um lado só
medido é chute com aparência de critério; o teste guarda os dois números.

---

**(2) A citação saía em UTC.** A ponte fixa `TimeZone=UTC` na conexão de
propósito, então o carimbo chega em UTC — e a citação *"#3059 · 13/07 18:23"*
**não batia com o que o operador viu na tela** (15:23 em Brasília). Numa
ferramenta cujo produto é ser conferível, isso não é detalhe. Passa a converter
para `America/Sao_Paulo` na exibição; o dado guardado não muda. Data torta
degrada para o texto cru em vez de derrubar a resposta no meio.

---

**(3) Citação em branco.** Um acerto literal numa mensagem que o indexador ainda
não tinha quebrado saía com o trecho **vazio** — ocupando a vaga de um resultado
e não dizendo nada. Agora cai no conteúdo cru da mensagem.

---

**Testes:** 4 novos. Suíte: **823 passando**, 101 pulados.

**Reversão:** revert do commit.

### fix(remember): o carimbo afirmava mais do que sabia — e a bateria o pegou fazendo isso (2026-08-30)

**Operador pediu:** nada. Achado pelo agente na **3ª execução** da bateria, e
é o defeito mais sério do dia — porque é a própria fase cometendo o pecado que
ela existe pra combater.

**O que aconteceu.** O desfecho se chamava `MENÇÃO LITERAL SEM APOIO` e o texto
dele dizia, com todas as letras: *"a palavra aparece no histórico, mas **NADA no
acervo responde à pergunta**"*. Na consulta *"portão permanente, ordem física das
colunas, carga posicional"*, ele imprimiu exatamente isso **enquanto trazia
`#3436`, `#3438` e `#3443` na lista** — que são precisamente as mensagens que
respondem.

Palavras do agente no turno: *"o carimbo declarou 'nada aqui responde' em cima de
um conjunto que respondia"*. E ele corrigiu o que tinha me dito oito minutos
antes: *"às 10:20 eu te disse que esse carimbo estava honesto. Estava errado
dizer isso."*

**Por que é grave, e não cosmético.** O que a ferramenta **sabe** é: *a palavra
aparece, e a busca por sentido não passou do piso*. O que ela estava
**afirmando** é: *nada aqui responde*. A segunda frase não decorre da primeira —
é exatamente o tipo de salto que o `CLAUDE.md` proíbe o agente de dar, escrito
por mim dentro da ferramenta que deveria impedi-lo.

**Foi feito:**
- O desfecho passa a se chamar **`MENÇÃO LITERAL`** — some o "sem apoio", que
  era a parte que soava a veredito sobre relevância.
- O texto diz o que se sabe: *"não consigo confirmar que estes trechos respondem
  — e **também não consigo afirmar que não respondem**. **LEIA e julgue.**"*,
  com as duas condutas explícitas (fora de contexto → diga e não costure;
  responde → cite pelo `#número` e data).
- **Os candidatos por sentido abaixo do piso deixam de ser escondidos** neste
  desfecho. Era a filtragem "só os literais" que produzia a cegueira: o comando
  descartava justamente a evidência que contradiria o próprio carimbo. Cada
  trecho sai com a nota ao lado.
- `CLAUDE.md` atualizado, com a nota histórica citando a frase velha de
  propósito — para que ninguém a reintroduza achando que está encurtando.

**Testes:** 3 novos (o texto não afirma ausência de resposta; manda ler e julgar;
a linha de instrução do `CLAUDE.md` não volta a afirmar). O teste do
`CLAUDE.md` confere a **linha de instrução**, não o arquivo — a nota histórica
cita a frase velha e não pode derrubar o teste. Suíte: **819 passando**.

**Reversão:** revert do commit.

### docs(claude): a janela de eco e a dica da frase, ditas para quem usa (2026-08-30)

**Operador pediu:** nada. Fecha duas lacunas entre o que a ferramenta **faz** e o
que o `CLAUDE.md` **conta**.

**Por quê:** as duas afetam diretamente a conduta do agente, e ele não tinha como
adivinhá-las.

1. **A janela de eco.** O comando ignora os últimos 90 segundos por padrão. Sem
   isso escrito, o agente não sabe que existe `--agora` — e a pergunta *"o que a
   gente falou agora há pouco?"* voltaria vazia, o que ele leria como ausência.
2. **A dica da frase.** Busca por termo isolado é a mais fraca das três pernas.
   Já aconteceu de `compat_gate` voltar "menção literal" com trechos
   irrelevantes e a conversa **aparecer inteira** ao reformular como *"camada de
   teste de compatibilidade de dados"*. O comando passou a avisar disso na
   saída; agora o `CLAUDE.md` manda **seguir o aviso antes de concluir
   ausência**.

Suíte: **817 passando**.

**Reversão:** revert do commit. Só documentação.

### fix(bateria): sessão limpa antes de rodar, e a dica que o próprio agente descobriu (2026-08-30)

**Operador pediu:** nada. Os dois vieram da **2ª execução** da bateria.

**(1) A bateria estava medindo a memória do agente, não a ferramenta.** Na 2ª
execução, a primeira pergunta recebeu: *"essa é a mesma pergunta que você me fez
às 10:06 — respondi às 10:08. Não vou refazer a busca."* Isso é o comportamento
**certo** de quem conversa e o comportamento **errado** para uma bateria: a
partir dali ela deixa de exercitar o `kobe-remember` e passa a exercitar a janela
imediata de memória.

**Conserto:** `/nova` como passo 0 do roteiro. Arquiva a sessão e zera a janela.
Custa um turno de nada e torna a bateria **repetível** — que é a propriedade que
faltava.

**(2) A dica que o agente descobriu sozinho, agora dita pela ferramenta.**
Buscando `compat_gate` como termo cru, o resultado foi `MENÇÃO LITERAL SEM
APOIO` com trechos irrelevantes. O agente **insistiu com frases** ("camada de
teste de compatibilidade de dados") e a conversa apareceu inteira — a discussão
de 26/08 sobre o portão, citada em `#3435`, `#3436`, `#3438` e `#3443`. Ele
relatou isso como *"a primeira mentiu por omissão"*.

Não é mentira: é **limitação de recall**, e ela tem explicação. Busca por um
termo isolado é a mais fraca das três pernas — a de sentido precisa de contexto
para achar paráfrase, e um termo solto embeda mal. O que estava errado era a
ferramenta **não dizer isso**, deixando a descoberta por conta de quem a usa.

Agora os dois desfechos fracos (`SEM REGISTRO` e `MENÇÃO LITERAL SEM APOIO`)
terminam com a orientação: *se o assunto pode ter sido dito com outras palavras,
tente de novo com uma frase descrevendo a ideia*. Transforma o achado ad-hoc de
um turno em comportamento da ferramenta.

Suíte: **817 passando**, 101 pulados.

**Reversão:** revert do commit.

### fix(calibragem): a medição do piso estava medindo o próprio eco (2026-08-30)

**Operador pediu:** nada. Apareceu ao rodar a assertiva **B6** do plano de
testes, logo depois da bateria.

**Por quê:** `bot/search/calibrar.py` acusou **folga −0,386** e imprimiu o alarme
*"as faixas se sobrepõem — o modelo parou de separar neste acervo"*. Era
**falso**, e o número denunciava: três perguntas do grupo "com resposta"
pontuaram **0,992 / 1,000 / 1,000**. Similaridade 1,000 não é semelhança — é a
pergunta encontrando **a si mesma**, palavra por palavra.

A causa é a mesma família dos consertos anteriores, e agora ficou claro que é
uma **lei do sistema**, não um caso isolado: rodar a bateria e rodar a
calibragem **escreve as perguntas em `messages`**. Elas viram histórico, o
indexador as embedda, e a sonda seguinte as encontra. **O acervo é a conversa, e
toda sonda que se roda entra nele.**

**Foi feito:** a calibragem ignora, por padrão, tudo que foi dito na **última
hora**. É largo o bastante para excluir a sessão de medição inteira (bateria +
calibragem) e curto o bastante para não descartar acervo de verdade. Com isso a
folga voltou a **+0,061** — exatamente o valor da bancada original, o que
confirma que o alarme era eco e não degradação.

O que este conserto evita não é um número feio: é **desligar uma trava boa por
causa de uma medição errada**. Um alarme falso de *"o modelo parou de separar"*
levaria a mexer no piso — ou a abandoná-lo.

**Testes:** 1 novo, exigindo que a calibragem aplique a janela. Suíte: **817
passando**, 101 pulados.

**Reversão:** revert do commit.

### fix(busca): dois defeitos que a BATERIA achou — e o mais grave era o carimbo mentindo (2026-08-30)

**Operador pediu:** nada. Os dois vieram da bateria conversacional da F2, que é
onde o teste é a entrega. O primeiro foi apontado **pelo próprio agente**, no
meio do turno, e ele ofereceu abrir uma sala Coder para investigar.

---

**Defeito 1 — `kobe-remember "rsync"` devolvia `SEM REGISTRO`. É falso: `rsync`
está em 116 mensagens.**

E o que torna isto o mais grave dos dois: `SEM REGISTRO` é justamente o carimbo
que o `CLAUDE.md` manda tratar como **afirmável** (*"a busca rodou até o fim,
pode dizer que não há"*). **O selo mais forte da ferramenta estava mentindo** —
o oposto exato do que a fase existe pra construir.

**A causa, e ela é de desenho, não de digitação.** A perna de palavra foi feita
para **não votar** sobre existência, com medição boa: sobre 16 perguntas ela dá
falso positivo em "Japão", "piano" e "maratona". Só que **todas as 16 eram
frases**. Quando a busca é um **termo cru**, o termo *é* a pergunta: uma frase de
uma palavra embeda mal, o sentido fica abaixo do piso, e não sobra ninguém para
votar. O caso nunca apareceu porque eu nunca o medi.

**Conserto:** toda palavra da pergunta que for rara no acervo entra também na
**busca literal** — sem modo especial para consulta curta. O corte de raridade é
o mesmo da perna de palavra, e é ele que impede "a gente" (24%) e "sobre" (23%)
de virarem busca literal casando com tudo.

---

**Defeito 2 — a régua da fase quase virou vítima do conserto do defeito 1.**

Com a perna literal ampliada, *"o que a gente decidiu sobre integração com o
**Salesforce**?"* passou de `SEM REGISTRO` para `MENÇÃO LITERAL SEM APOIO` —
porque `integração` existe no acervo. E aqui o número derrubou a saída óbvia:
**`integr` está em 79 mensagens e `rsync` em 116**. A palavra genérica é **mais
rara** que o termo técnico. **Nenhum limiar de raridade separa os dois casos.**

O que separa é outra coisa: `Salesforce` **não existe**. Daí a regra, que é a
mesma em uma frase: **se alguma coisa específica que o operador nomeou não está
no histórico, não dá pra afirmar que o assunto existe.** A perna literal só vota
quando **todo** token nomeado teve acerto.

O veto só torna essa perna mais conservadora — a de sentido continua votando
sozinha. Conferido: *"o que a gente decidiu sobre rsync e Zendesk?"* (um termo
presente, um inédito) segue respondendo `ACHOU` pelo caminho do sentido.

---

**Defeito 3 — a pergunta encontrava a si mesma.**

O bot grava a mensagem do operador em `messages` **antes** de rodar o turno.
Visto ao vivo: no cenário do `compat_gate`, a única "menção" que a busca achou
era a mensagem que o operador tinha acabado de mandar. O agente daquele turno
percebeu sozinho — *"o único trecho é a tua própria mensagem de agora, que
obviamente não conta"* — mas **depender de ele perceber** é exatamente o tipo de
garantia que este projeto não aceita.

**Conserto:** janela de eco de 90 s. Uma mensagem precisa ter 90 segundos para
contar como *passado*. `--agora` desliga. E o que a janela escondeu é
**contado e dito** na saída — esconder em silêncio seria o mesmo defeito, de
outro lado.

---

**Um achado sobre a própria bateria, que virou aviso no roteiro:** o cenário da
régua **queima o próprio termo**. Ao rodar, a pergunta entra em `messages`; da
segunda execução em diante a palavra existe no histórico e o resultado correto
deixa de ser `SEM REGISTRO`. "Salesforce" e "compat_gate" foram gastos na 1ª
execução. O roteiro agora diz isso, manda conferir que o termo é virgem antes de
rodar (`kobe-remember "<termo>"` tem que recusar), e lista o que já foi gasto.

**Testes:** 9 novos em `tests/test_search_query.py` — o termo cru virando busca
literal, a palavra banal não virando, o veto por token ausente, o veto **não**
bloqueando quando o sentido passa, a janela de eco (liga, desliga e conta).
Suíte: **816 passando**, 101 pulados.

**Conferido contra o acervo real de dev**, 12 consultas: `rsync` cru agora acha;
`Zendesk` (termo virgem) recusa; `Kubernetes`, `maratona` e `financiamento do
carro` recusam; `borda`, `rsync` em frase e a paráfrase citam.

**Reversão:** revert do commit. Sem chave nova, sem mudança de schema.

### fix(roteiros): todo roteiro versionado volta a ser executável — e um teste que garante isso (2026-08-30)

**Operador pediu:** nada. Isto apareceu ao escrever a bateria da F2.

**Por quê:** `tests/roteiros/f1-dispatch.txt` — a metade conversacional da F1,
versionada e citada no changelog daquela fase — **não parseava**. Ele foi escrito
na sintaxe do briefing, que põe a espera **depois** da mensagem (`mensagem` numa
linha, `@25` na seguinte), enquanto `infra/dev_inject.py` a espera **antes**,
prefixando (`@25 mensagem`). São a mesma pausa vista de lados diferentes, e o
arquivo estava numa convenção e a ferramenta na outra.

O sintoma é do pior tipo: o roteiro fica no repositório parecendo pronto e só
falha na hora em que alguém precisa dele — provavelmente sob pressão,
provavelmente meses depois, provavelmente sem o contexto de quem o escreveu. O
meu tinha o mesmo defeito, e foi assim que eu descobri o dele.

**Foi feito:**
- Os dois roteiros convertidos para a sintaxe da ferramenta. A conversão é fiel:
  cada `@N` solto passa a prefixar a **próxima** mensagem. Um `@N` no fim do
  arquivo não tem próxima mensagem para prefixar e some — ele já não fazia nada.
- **`tests/test_roteiros_parseiam.py`** — para **cada** arquivo de
  `tests/roteiros/`, um teste que exige que ele seja lido pelo `dev_inject`. Sem
  banco, sem rede, sem bot: só lê arquivo, então roda em qualquer máquina e em
  todo `pytest`. Há também um teste de vacuidade (se a pasta esvaziar, o
  parametrizado passaria sem exercitar nada) e um que reprova espera antes da
  **primeira** mensagem — não há turno anterior para aguardar, então é tempo
  morto, e num roteiro caro isso conta.

Fecha a **classe**, não o caso: o próximo roteiro escrito na convenção errada
fica vermelho no mesmo `pytest` em que for escrito. Roteiro é ferramenta de
operador, e ferramenta de operador que não abre é pior que ferramenta ausente.

**Testes:** 15 novos (2 arquivos × 7 roteiros + 1 de vacuidade). Suíte: **807
passando**, 101 pulados.

**Reversão:** revert do commit.

### docs(claude): a regra dura — não responder sobre o passado sem rodar o comando (2026-08-30)

**Operador pediu:** o terceiro entregável do §5-F2 do briefing, textual — *"regra
dura no `CLAUDE.md`: não responder sobre passado sem rodar o comando"*.

**Por quê:** sem a regra, o `kobe-remember` é um utilitário que ninguém chama, e
a dor original continua igual. A F2 entrega **duas** coisas: o comando e a
obrigação de usá-lo. A segunda é a que muda o comportamento.

**Foi feito:** seção nova no `CLAUDE.md`, logo depois da do `kobe-reflect`.

- **A regra**, com o motivo colado nela: toda pergunta sobre o passado exige
  rodar o comando **antes** de responder, e isso **não depende de achar que já
  sabe** — achar que já sabe é exatamente o estado mental em que a confabulação
  acontece.
- **A consequência**: resposta sobre o passado **sem citação é violação, mesmo
  que o conteúdo esteja certo**. Sem `#número` e data, nem o agente nem o
  operador conseguem distinguir o que foi lido do que foi lembrado — e essa
  indistinção *é* o problema.
- **Os quatro desfechos, com a conduta de cada um** numa tabela: citar; dizer
  "não tenho registro"; dizer "achei a palavra mas nada responde" **sem costurar
  as menções**; e — o que mais importa — tratar `FALHA DO INSTRUMENTO` como
  *"não sei se há registro"*, nunca como ausência.
- **O quinto caso, o mais traiçoeiro:** `SEM REGISTRO PARCIAL`, quando a busca
  por sentido (a única árbitra) estava fora.
- **Uma tabela `kobe-remember` × `kobe-reflect`**, porque os dois se completam e
  não se substituem — um devolve o destilado, o outro devolve a fala. Com a
  regra de desempate: **quando discordarem, a fala literal manda**, e o
  desacordo em si merece ser dito ao operador.

**Uma correção de doc que era mentira ativa:** o `CLAUDE.md` afirmava que o
`kobe-remember` *"ainda não existe — chega na F2"*. Deixar isso ali seria pior
que não ter doc nenhuma: ensinaria o agente a **não tentar**.

**Testes:** `tests/test_claude_md_regra_remember.py`, **8 testes**. Eles não
julgam a redação — guardam os invariantes: que a regra existe, que a frase
"mesmo que o conteúdo esteja certo" continua lá (sem ela a regra vira "acerte",
e acertar de memória é o que não dá pra distinguir de confabular), que os quatro
desfechos estão descritos, e que a separação entre "não há" e "não deu pra
saber" não foi enxugada por alguém encurtando a seção. Essa distinção já foi
perdida uma vez neste sistema.

Suíte: **792 passando**, 101 pulados.

**Reversão:** revert do commit. Só documentação.

### feat(remember): `kobe-remember` v1 — a fala literal, citada e conferível (2026-08-30)

**Operador pediu:** *"`kobe-remember "<assunto>"` devolve as falas literais, do
operador e do agente, citadas com data e número da mensagem"*, em menos de 3
segundos.

**Por quê:** o `kobe-reflect` devolve o **destilado** do Hindsight — fatos
consolidados, sintetizados. Ele não cobre *"quais foram as palavras dele?"*,
*"eu já tinha pedido isso?"*, *"mostra onde a gente falou disso"*. Os dois se
completam e não se substituem, e isso está escrito no cabeçalho do comando para
que ninguém troque um pelo outro.

**Foi feito:** `bot/bin/kobe-remember`, com **quatro** desfechos e um texto
próprio para cada um.

| saída | o que o agente pode afirmar |
|---|---|
| `exit 0` + trechos | achou — cite pelo `#número` e pela data |
| `exit 0` + `MENÇÃO LITERAL SEM APOIO` | a palavra aparece; **nada responde** — não costure |
| `exit 0` + `SEM REGISTRO` | procurou e não há — **pode** dizer "não tenho registro" |
| `exit 3` + `FALHA DO INSTRUMENTO` | **não se sabe** — nunca relate como ausência |

**O quarto desfecho é a razão do comando existir do jeito que ele é.** Foi
exatamente aqui que o `kobe-reflect` errou por meses: dois desfechos diferentes
no código e **um texto só na tela**, e um timeout virava a afirmação *"não há
registro sobre isso"*. Há teste asseverando o texto de cada caso, não só o
código de saída — porque quem lê esta saída é um agente, e o que ele faz depois
depende inteiramente das palavras que encontrar ali.

**Um quinto estado, que é o mais traiçoeiro:** `SEM REGISTRO` **com a busca por
sentido fora**. A busca por sentido é a única árbitra de existência; sem ela, um
"não achei" não é ausência confirmada. O comando marca esse caso como
**`SEM REGISTRO PARCIAL`** e manda dizer isso ao operador. Apresentá-lo como
ausência seria a mesma mentira do `FALHA`, só que mais difícil de notar.

**Outros detalhes com motivo:**
- **Atravessa os tópicos, rotulando** (decisão **E5**). Provado ao vivo: uma
  pergunta sobre "Japão" feita do tópico Dev Kobe trouxe mensagens do tópico
  **Pessoal**, identificadas como tais.
- **`--ver <n>`** abre a vizinhança de uma mensagem. É o que fecha o ciclo de
  conferência: o operador lê a citação, pede o número, e vê o contexto em volta.
- **Avisa quando o índice está atrasado** (mensagem gravada há dois minutos pode
  ainda não estar na busca por sentido) em vez de devolver menos e deixar
  parecer ausência.
- **`--desde` / `--ate`** aplicam o recorte de data nas **três** pernas, montado
  num lugar só: um recorte que valesse para duas e não para a terceira daria um
  resultado misturando períodos, e ninguém perceberia — cada linha continua
  verdadeira sozinha.

**Um conserto de honestidade achado rodando o comando de verdade.** A saída
dizia *"descartei (comuns demais no acervo): salesforc"* — e `salesforc` não é
comum, é **inexistente**. Dizer que "Salesforce é comum demais no acervo" é o
oposto exato da verdade, e é o tipo de frase que faz o operador desconfiar de
todo o resto. Agora são duas linhas distintas: **"nunca apareceu no histórico"**
(que é quase a resposta à pergunta) e **"ignorei — comuns demais para
discriminar"**.

**Testes:** `tests/test_kobe_remember.py`, **13 testes**, sobre o TEXTO de cada
desfecho e não só sobre o código de saída. Suíte: **784 passando**, 101 pulados.

**Critério de pronto do briefing — cumprido e medido.** `kobe-remember
"arquitetura de borda"` em processo frio, 10 rodadas contra o acervo real de dev:
**p50 1,58 s · p95 1,82 s · máx 1,85 s**. O critério era 3 s.

**Reversão:** revert do commit. O comando só lê.

### feat(busca): as três pernas, a fusão e o piso anti-invenção (2026-08-30)

**Operador pediu:** que a busca *"por palavra e por sentido, combinadas"* ache
nome próprio, sigla e caminho de arquivo **e** paráfrase — e que, quando não
houver registro, o sistema **diga que não há** em vez de inventar. O último item
é o que reprova a fase inteira se falhar.

**Por quê:** a dor original é responder sobre o passado de memória. Um índice que
sempre devolve alguma coisa não conserta isso — só troca achismo por achismo com
citação. O produto principal desta peça é o **veredito**, não a lista.

**Foi feito:** `bot/search/query.py` e `bot/search/calibrar.py`.

**As três pernas, e o que cada uma resolve:**
- **literal** (`ILIKE` sobre o índice trigrama) — identificadores e nomes
  próprios. Existe porque o dicionário `portuguese` **destrói** identificador:
  `kobe-recall-since` vira `kobe-recall-sinc` + `recall` + `sinc`, e `sinc` casa
  com "sincronizar".
- **palavra** (`search_tsv`, pontuada por raridade/IDF) — **ordena e não vota**.
- **sentido** (`pgvector`, varredura exata) — a única que acha paráfrase, e a
  **única árbitra** de existência.

**A decisão de desenho que veio de medição, e não de gosto.** Sobre 16 perguntas
(8 com resposta no acervo, 8 sobre assuntos que nunca existiram), a massa de IDF
da perna de palavra ficou assim:

```
com resposta : [0,00  0,00  7,09  7,34  8,92  10,61  11,80  14,55]
sem resposta : [3,06  5,91  7,16  7,56  7,60   8,89   8,90   8,95]
```

Duas perguntas legítimas tiraram **zero** e quatro perguntas sobre assuntos
inexistentes tiraram **entre 7,5 e 9** — porque "Japão", "piano" e "maratona"
existem no acervo, soltos, fora de contexto. **Raridade não é relevância.** Um
desenho em OU entre as três pernas deixaria passar exatamente a classe
"Salesforce". Daí a regra:

```
existe = (sentido acima do piso) OU (a perna literal achou o identificador)
```

**Os quatro desfechos**, e o quarto é o que impede o erro que este sistema já
cometeu duas vezes:

| desfecho | quando |
|---|---|
| `ACHOU` | o sentido passou do piso |
| `MENCAO_LITERAL_SEM_APOIO` | o token aparece, mas o sentido não passou |
| `SEM_REGISTRO` | procurou e não há |
| `FALHA` | **não deu pra saber** — banco fora, embedding fora |

**Sobre o `MENCAO_LITERAL_SEM_APOIO`:** a perna literal responde *"a palavra
aparece"*, não *"existe decisão sobre isso"*. Medido no acervo: `Salesforce`,
`Kubernetes` e `maratona` dão **zero**; `Japão` dá **7** e `piano` dá **2**,
soltas. O rótulo obriga o agente a dizer *"achei a palavra X em N mensagens, mas
nada que responda"* — nunca a costurar as menções numa resposta.

**Dois defeitos meus, achados testando contra o acervo real:**

1. Quando **todos** os radicais da pergunta eram banais, o fallback "use os N
   mais raros" reintroduzia exatamente o ruído que o corte tinha removido: em
   *"o que a gente falou sobre o working_set.py"* os três menos comuns são
   `sobre` (23%), `a gente` (24%) e `falou` (14%). Agora a perna de palavra
   simplesmente **fica de fora** dessa pergunta — quem a carrega é a literal e a
   de sentido, e nenhuma das duas depende dela.
2. A repesca dos "N mais raros" (que existe para salvar "arquitetura", em 5,2%,
   de um corte em 5%) **não tinha teto próprio**, então numa pergunta curta ela
   trazia os banais de volta pela porta dos fundos. Ganhou `FATOR_REPESCA = 2`:
   repesca até o dobro do corte, o que salva "arquitetura" e continua barrando
   "a gente".

**`bot/search/calibrar.py` — o piso não é literal no código, e há como remedi-lo.**
A folga entre "achou" e "não achou" é de **0,061**, e é honesto dizer que ela
envelhece conforme o acervo cresce. O comando roda as 16 perguntas contra o
acervo do dia e imprime a folga; folga negativa não se resolve espremendo o
número — é sinal de que o modelo parou de separar. As oito perguntas de controle
são deliberadamente **plausíveis** para este operador (dieta, viagem, aluguel,
maratona): um controle feito de absurdos daria uma folga bonita e falsa.

**Testes:** `tests/test_search_query.py`, **26 testes**, com ponte de mentira
roteirizada por perna. A maior parte não testa "achar" — testa **não achar**, e
as três formas de errar nisso: achar o que não existe, dizer que não existe
quando o instrumento falhou, e costurar menção solta em resposta. Há teste
explícito para *"a perna de palavra SOZINHA não faz existir"* (o caso
piano/Japão) e para *"banco fora é FALHA e não SEM_REGISTRO"*.

Suíte: **771 passando**, 101 pulados.

**Verificado contra o acervo real de dev (3.558 mensagens, 7.706 trechos),
sete cenários, sete corretos:**

| pergunta | veredito | |
|---|---|---|
| arquitetura de borda | `ACHOU` — #3059 e #3104, de julho | ✅ |
| `compat_gate` | `SEM_REGISTRO` — o termo não existe em dev | ✅ |
| rsync | `ACHOU` — #2146, o incidente de 12–13/06 | ✅ |
| paráfrase sobre git/produção | `ACHOU` — #2047, #2074, #2081, de 12/06 | ✅ |
| **Salesforce** | **`SEM_REGISTRO`** | ✅ |
| viagem para o Japão | `MENCAO_LITERAL_SEM_APOIO` | ✅ |
| `working_set.py` | `ACHOU` — #3312 e #3310, pela perna literal | ✅ |

**Uma limitação conhecida, nomeada:** a perna literal é **sensível a acento** —
`Japao` digitado sem acento não acha `Japão` gravado com. Não morde no caso que
motiva a perna (identificador é ASCII: `compat_gate`, `working_set.py`), e a
busca por sentido cobre a pergunta de qualquer forma. Fechar isso exigiria a
extensão `unaccent`, que não está instalada; fica registrado, não escondido.

**Reversão:** revert do commit. Sem chave nova — `query.py` só lê.

### feat(busca): o quebrador de trechos, o embedder e o indexador — tudo ATRÁS (2026-08-30)

**Operador pediu:** a carga do índice da F2 sobre o histórico inteiro, sem que a
conversa fique mais lenta — *"performance e qualidade vêm antes de orçamento"*.

**Por quê:** o vetor de um trecho exige uma chamada externa. Colocar isso no
caminho do turno faria toda mensagem do operador esperar por uma API antes de
ser gravada. A decisão **E3** do briefing já dizia como: *"nada tem 'fechar a
sala' como gatilho; tudo é contínuo, dirigido por relógio ou acúmulo"*.

**Foi feito:** o pacote `bot/search/`, com a divisão que garante a promessa —
**a gravação de uma mensagem nunca espera por embedding**.

- **`chunker.py`** — quebra em janelas de 900 caracteres, preferindo parágrafo.
  Existe porque 30% das mensagens passam de 1.500 caracteres e todo modelo de
  embedding corta a entrada **descartando o resto em silêncio**. O teste central
  é "não perde texto": se ele quebrasse, a metade de baixo de uma mensagem longa
  não estaria no índice e a busca responderia *"não tenho registro"* sobre algo
  gravado — sem erro nenhum na tela.
- **`embedder.py`** — a **única** peça que fala com o modelo. Concentrar aqui é
  o que torna "o mesmo modelo dos dois lados" propriedade do código: vetor de um
  modelo comparado com vetor de outro não erra por pouco, erra por completo, e
  erra calado. Confere a dimensão contra `VECTOR(1536)` e **falha alto** se
  divergir.
- **`indexer.py`** — quebra, embedda e recalcula a estatística de radicais. Todo
  estado vive no banco (`embedding IS NULL` **é** a fila), então reiniciar não
  perde nem duplica. Ganhou CLI (`status`, `carga`, `tick`, `df`).
- **`source.py`** — a fonte do Keyko, no molde já provado do coletor da F1: faz
  o trabalho **dentro do tick** e devolve lista vazia de despertares. **Custo de
  cota: zero** — nenhum `claude -p` é acordado. Cadência de 60 s, com piso de
  10 s para um valor torto no `.env` não virar laço apertado.
- **`SEARCH_INDEX_ENABLED`**, default **false**, documentada no `.env.example`
  junto com os pisos e o modelo.

**O contrato de falha, que é o coração desta entrega.** Toda falha do embedder
vira **`EmbeddingIndisponivel`** — exceção com nome próprio — e o indexador a
deixa subir em vez de gravar metade. **Nunca** se devolve lista vazia por erro:
vazio significa "não havia o que embeddar", e só isso. Este sistema já cometeu o
falso negativo silencioso duas vezes (a F0.5-B, com os embeddings tomando 401 e o
`reflect` respondendo "não há registro"; e o `kobe-reflect` de 29/08, com o
timeout indistinguível de acervo vazio). Não vou repetir.

**Um detalhe que não é detalhe:** a fonte recebe uma **fábrica** de ponte, não a
ponte pronta. O Keyko sobe antes de qualquer turno e pode ficar horas ocioso, e
uma conexão aberta desde a inicialização é exatamente o socket morto que já fez
mensagem do operador sumir três vezes em 30 dias.

**Testes:** `tests/test_search_indexer.py`, **31 testes** — o quebrador (não
perde texto, prefere parágrafo, fatia com sobreposição, respeita o teto), o
contrato de falha do embedder (com cliente de mentira: falha vira exceção,
dimensão errada falha alto, lote respeitado, precisão do literal do `pgvector`),
a chave (desligada, a ponte **não é nem lida** — provado com uma ponte que
registra toda chamada), o tick que não derruba o daemon, e a fonte que nunca
devolve despertar.

Suíte: **745 passando**, 101 pulados. `tests/portability_guard.sh` verde.

**Carga inicial em dev, executada:** 3.558 mensagens → **7.706 trechos, todos
com vetor, em 53,9 s** (US$ 0,026 de API). A estatística de radicais ficou com
**16.541** entradas. Números do acervo, por mensagem: "a gente" em 24%, "sobre"
em 23%, "conversa" em 16% — contra "rsync" em 3,3% e "borda" em 2,1%. É essa
distância que a busca usa pra saber o que é sinal.

**Reversão:** revert do commit; ou, sem tocar em código, `SEARCH_INDEX_ENABLED=false`
— o indexador para, `message_chunks` fica inerte, nada é apagado.

### feat(schema): migration 007 — o índice de busca sobre a conversa (2026-08-30)

**Operador pediu:** a F2 do Highlander v3 — *"índice de busca sobre `messages`,
por palavra e por sentido, combinadas"*, com o comando `kobe-remember` devolvendo
**as falas literais citadas com data e número da mensagem**. Esta entrada é só a
estrutura de banco; o código de busca vem em seguida.

**Por quê:** hoje toda pergunta sobre o passado é respondida de memória, e é daí
que sai o achismo. O que faltava não era um modelo melhor: era um **índice** sobre
o que foi realmente dito, e um número que o operador possa conferir.

**Foi feito:** `infra/migrations/007_message_search.sql`, estritamente aditiva.

- **`messages.seq`** — o número que se cita. A chave é UUID e não serve pra
  conferir nada. Preenchida em ordem cronológica no histórico e por sequência
  daí pra frente, com `DEFAULT nextval(...)`: **nenhuma linha de código do bot
  mudou** pra isso. Se dependesse do código, um caminho de INSERT esquecido
  gravaria NULL e a citação perderia o número.
- **`messages.search_tsv`** — coluna **gerada** (`GENERATED ALWAYS AS … STORED`)
  com `to_tsvector('portuguese', content)`, mais índice GIN. Gerada é o ponto: o
  Postgres a mantém sozinho e nenhum caminho de código pode esquecer de
  atualizá-la.
- **Índice GIN trigrama sobre `content`** — a perna **literal**. Ela existe
  porque o dicionário `portuguese` faz *stemming*, e stemming em identificador é
  destruição: medido, `kobe-recall-since` vira `kobe-recall-sinc` + `recall` +
  `sinc`, e a busca devolveu resultados **sobre imagem no WhatsApp**. Com o
  índice trigrama, `compat_gate`, `working_set.py` e `HINDSIGHT_RECALL` saem em
  2 a 11 ms, sem varredura.
- **`message_chunks`** — a perna por **sentido**, `VECTOR(1536)`
  (`text-embedding-3-small`, decisão do operador). É por **trecho** e não por
  mensagem porque 30% das mensagens do acervo passam de 1.500 caracteres (p99 =
  6.322) e todo modelo de embedding corta a entrada **descartando o resto em
  silêncio** — a metade de baixo de 1 em cada 3 mensagens ficaria fora do índice
  sem ninguém perceber. Índice **parcial** em `WHERE embedding IS NULL`: a
  pergunta do indexador é "o que falta?", e o custo dela passa a ser
  proporcional ao que falta, não ao acervo.
- **`search_lexeme_df`** — em quantas mensagens cada radical aparece. Sem essa
  estatística a busca por palavra **não distingue "achei" de "não achei"**:
  medido, *"o que a gente decidiu sobre integração com o Salesforce?"* — assunto
  que nunca existiu — devolvia 30 resultados com nota equivalente à de uma
  pergunta legítima, porque "decidiu", "a gente" e "sobre" estão em todo lugar.
  `ts_rank` é nota **local**: mede o casamento dentro do documento e não sabe que
  o termo é banal no acervo.

**Duas decisões com número, não com gosto:**

- **Sem índice aproximado de vizinhança (HNSW).** No acervo de hoje a varredura
  **exata** leva 67 ms e o HNSW, 2,8 ms — e os dois devolvem o mesmo topo. Não
  vale trocar exatidão por 64 ms quando é justamente a exatidão que sustenta o
  piso do *"não tenho registro"*: um vizinho perdido pela busca aproximada
  viraria uma **recusa falsa**. Vira uma linha de SQL quando o acervo passar de
  ~50 mil trechos (construir custou 6,2 s).
- **`VECTOR(1536)` e não 384.** Foi o único dos dois modelos comparados em que as
  perguntas COM resposta e as SEM resposta ocupam faixas de similaridade
  **separadas** (folga +0,061 contra −0,025 do modelo local, sobre 16 perguntas).
  Sem separação não existe o piso que faz o sistema dizer "não tenho registro"
  em vez de inventar. **Porta de saída:** trocar de modelo depois custa
  reindexar o que já foi indexado.

**Uma correção de rota, e ela vale registrar.** A primeira versão espelhava a
mudança em `infra/schema.sql`, como o plano previa. O runner recusou, e estava
certo: **`schema.sql` É a migration `000`**, e migration aplicada é imutável — o
guarda de drift acusa a mudança de checksum em todo banco que já a aplicou. A
migration `006` também não tocou nele. Estrutura nova vai **só** em migration
nova. Como bônus, reverter eliminou uma divergência real: com o `schema.sql`
alterado, uma instalação nova e um banco migrado ficavam com `attnum` diferente
nas colunas novas; com ele congelado, as duas impressões digitais são **idênticas
byte a byte** (conferido com dois bancos de apoio). Há teste guardando isso.

**Testes:** `tests/test_message_search_schema.py`, **18 testes** — 10 sobre o
arquivo (rodam sempre, sem banco: aditividade, colunas no fim, dimensão do vetor,
guarda do backfill, `is_called=false`, índice parcial, ausência de HNSW,
`schema.sql` congelado) e 8 sobre o banco (idempotência aplicando **duas vezes**,
`seq` único e cronológico, mensagem nova ganhando `seq` sozinha, tsvector se
mantendo no INSERT e no UPDATE, índice trigrama sendo usado em vez de varredura,
cascade de trecho, UNIQUE de `(message_id, idx)`).

Suíte: **811 passando, 4 pulados** com banco de integração; **714 passando** sem
banco. Os 18 testes de banco também foram rodados contra o **`kobe_dev` real**,
com as 3.558 mensagens: 18/18 verdes.

Aplicada em **dev**: 3.558 mensagens numeradas de 1 a 3.558 em ordem cronológica,
zero `search_tsv` nulo, em **2,7 s**. Portão de compatibilidade **verde em dev**.

**Produção segue na `006`, de propósito** — migration em produção é do agente
principal, não desta sessão. E o portão agora **diz isso com precisão**, em vez de
deixar inferir: `[migration] banco ATRASADO: falta(m) a(s) migration(s) 007`,
seguido dos 7 sintomas. Antes, só apareceriam os sintomas.

**Reversão:** `SEARCH_INDEX_ENABLED=false` deixa `message_chunks` inerte. A
estrutura é aditiva e se mantém sozinha (a coluna é gerada); sair de vez seria
uma migration de remoção, não prevista.

### chore(schema): a referência do portão regenerada, e a trava que impede ela nascer velha (2026-08-30)

**Operador pediu:** dentro do escopo da F2 — *"regenere a referência pelo caminho
documentado, deixe o portão verde, e — mais importante — feche a causa: hoje nada
obriga a referência a acompanhar uma migration nova"*.

**Por quê:** a F1 acrescentou a migration `006` e **não** regenerou
`tests/fixtures/schema_expected.json`. A consequência não foi um teste vermelho:
foi `infra/compat_gate.py` acusando **4 divergências falsas** — *"as tabelas
`work_*` existem no alvo e não no schema versionado"* — nos **dois** ambientes, ao
mesmo tempo em que uma suíte de 691 testes ficava verde. Um portão que vive
vermelho deixa de ser sinal e vira ruído que todo mundo aprende a ignorar. É
exatamente o defeito que o portão nasceu pra corrigir, reproduzido dentro dele.

E o erro não foi de disciplina, foi de desenho: **nada** olhava para essa
defasagem, e ela ainda por cima se apresentava **disfarçada de outra coisa**
(tabela sobrando), obrigando quem lê a inferir a causa.

**Foi feito:**

- **`infra/schema_fingerprint.py` — impressão digital versão 2.** Passa a gravar
  a chave `migrations`: a lista de versões da tabela de controle do runner. A
  distinção com o que ficou de fora é o ponto — `applied_at` mudaria a impressão
  digital a cada aplicação e viraria ruído; a lista de versões só muda quando o
  modelo muda. `None` (banco nunca tocado pelo runner) e `[]` (controle vazio)
  são valores diferentes, de propósito.
- **`infra/compat_gate.py` — duas classes novas**, que separam duas perguntas que
  antes se confundiam numa só:
  - **`migration`** — *"o BANCO está em dia com a referência?"*. Atrasado manda
    rodar `migrate.py up`; adiantado manda **regenerar a referência**, que é o
    conserto certo e o oposto do que a mensagem antiga sugeria. Ela é avaliada
    **antes** das tabelas: *"falta a migration 007"* é a causa de *"falta a
    tabela X"*, e a causa se lê primeiro (há teste exigindo essa ordem).
  - **`referencia`** — *"a REFERÊNCIA está em dia com `infra/migrations/`?"*.
    É a que fecha a causa, e é de propósito a peça mais burra do arquivo: duas
    listas de string, **sem banco, sem rede, sem ambiente**.
- **`tests/test_schema_reference.py` — 8 testes, nenhum toca no banco.** Por isso
  rodam em qualquer máquina, em todo `pytest`, inclusive num clone limpo — e não
  são do tipo que "pula", que é verde por ausência e foi assim que a `006` passou.
  Quem escrever a próxima migration vê vermelho **no mesmo pytest** em que a
  escreveu, com a receita da regeneração na mensagem. Quatro deles injetam o
  vermelho de propósito (migration nova, migration apagada, impressão digital sem
  a chave, mesma composição em ordem trocada).
- **`tests/fixtures/schema_expected.json` regenerada** de um banco de apoio
  erguido do zero por `infra/provision_db.py` + `infra/migrate.py up` — o caminho
  documentado, que é o que faz *"schema versionado × banco real"* ser verdade por
  construção e dispensa a produção no ar.
- **Um teste que era assertiva errada foi corrigido, não silenciado.**
  `test_referencia_tem_as_seis_tabelas_pos_aposentadoria` fixava a lista
  **fechada** de tabelas, então ele transformava *"entrou tabela nova"* — que é o
  trabalho normal — em falha. Virou
  `test_o_chat_manager_continua_aposentado_na_referencia`, que assevera o que o
  nome promete: `conversations` e `conversation_tags` fora, as seis originais
  dentro, `messages.conversation_id` inexistente. Quem vigia tabela
  entrando e saindo é o portão, contra a referência.

**Um conserto de caminho, achado ao escrever:** carregar `infra/migrate.py`
dinamicamente sem registrá-lo em `sys.modules` estoura com
`AttributeError: 'NoneType' object has no attribute '__dict__'` vindo de dentro
do `dataclasses` da biblioteca padrão — porque ele resolve as anotações via
`sys.modules[cls.__module__]`. O erro não diz nada sobre a causa; está comentado
ao lado da linha.

**Testes:** suíte **704 passando, 93 pulados** (era 691 — 13 testes novos: 8 do
arquivo novo e 5 da classe `migration`). `infra/compat_gate.py` **verde nos dois
ambientes** — dev e produção (produção apenas **lida**, zero escrita).
`tests/portability_guard.sh` verde: a referência regenerada não carrega nome de
banco de apoio nem caminho de máquina, e há teste exigindo isso.

**Reversão:** revert do commit. Nada aqui é importado pelo runtime do bot — as
três peças são ferramenta de operador e suíte.

### test(f1): a bateria executada — 99 testes novos, os cinco critérios provados (2026-08-29)

**Operador pediu:** o §3.4 do briefing, que vale a partir de já — *"toda sessão
Coder escreve um plano de testes que ela mesma executa"*, e reporta **verde/vermelho
por cenário** antes de dizer que terminou.

**Foi feito:** a bateria inteira da F1, executada por mim no dev VPS. **99 testes
novos** em cinco arquivos, mais as baterias contra o acervo e o bot reais. Relatório
completo entregue como artefato da sessão.

**Os cinco critérios de pronto do §5-F1, e o que provou cada um:**
- **(a) copiar de sala viva sem corromper** — coleta desta própria sessão, com ela
  trabalhando: 1.537.086 → 1.538.756 bytes entre duas passadas, 440 `uuid` de linha
  e 440 distintos, toda linha JSON válida, terminando em `\n`.
- **(b) rodar duas vezes não duplica** — 2ª passada sobre 387 transcripts copiou
  **0 bytes**.
- **(c) rodar duas vezes não sobrescreve** — os **387** prefixos com `sha256`
  idêntico. É mais forte que "o arquivo cresceu": um arquivo pode crescer e ter
  tido o miolo reescrito.
- **(d) dossiê legível antes de fechar** — provado em **duas** salas vivas: esta, e
  a `f4ad69ba` enquanto ela trabalhava.
- **(e) sem declaração, a sala não nasce** — 4 formas de errar no Coder e 6 no
  Mission Control, todas com as contagens (estado, tmux, worktrees, banco)
  **idênticas antes e depois**.

**A bateria conversacional, cenário a cenário:**
- **E0 (ambíguo, custo zero — e é o que prova a regra):** *"manda o Coder arrumar o
  Flow"* → o agente respondeu que *"existem dois Flows aqui"*, nomeou os dois e
  perguntou qual. **Nenhuma sala nasceu.**
- **E1:** código do framework → sala com `Kobe / (nenhum)`, com o `none` declarado
  **explicitamente**.
- **E2 — o cenário que prova o desenho inteiro:** código de plugin → `Kobe / Coder`,
  com o `cwd` apontando pro repositório do **plugin**. A pasta é uma; o sistema é
  outro. Quem derivasse o sistema do diretório erraria exatamente aqui.
- **E3 (regressão):** turno normal respondeu *"4."* em **6,4 s**.

As duas salas usaram tarefa trivial e foram **derrubadas logo após a assertiva**,
conforme a mitigação de custo do §9.6. Custo real: **2 salas, exatamente o
orçado**, mais 9 injeções (contadas no banco).

**Correção, escrita depois de o log do turno E2 fechar:** eu derrubei as duas salas
com o **turno do agente ainda em voo** — o do E2 só terminou 6 minutos depois do
kill. Consequência: o agente foi ler o estado, viu duas salas mortas sem commit, e
reportou ao operador **dois defeitos que não existem** (*"sala morre antes de fechar
o ciclo commit→merge"*, *"`resume` não cobre sala morta"*). Ele raciocinou certo
sobre uma evidência que **eu** produzi sem avisar — e o histórico do tópico de
desenvolvimento ficou com duas descobertas falsas anotadas como se fossem do
sistema, que é exatamente a dor que esta campanha existe pra curar.
Isto **não invalida as assertivas**: elas são sobre a linha do catálogo no
*nascimento* da sala e sobre o dossiê gerado com ela viva, e as duas coisas foram
colhidas antes do kill. O que a morte precoce impediu foi a sala *concluir a
tarefa*, que nunca foi o objeto do teste.
**Regra pras próximas fases:** derrubar a sala **depois de o turno do agente
fechar**, não depois da assertiva — custa minutos de relógio, zero de cota, e evita
plantar achado falso no histórico.

**E um resultado colhido sem querer, que vale mais que o cenário planejado:** o
agente repassou ao operador, por conta própria, o aviso
`collector_warning` que veio no `start` — *"o coletor de transcripts nunca
registrou uma coleta bem-sucedida, e transcript expira em 30 dias"*. É o
**terceiro degrau** da mitigação do relógio funcionando ponta a ponta, por um
caminho que não depende do agendador. Não estava no roteiro; apareceu sozinho.

**E as consultas do §6.4 rodaram sobre dado real gerado pela própria bateria** —
sessões de plugin do Kobe, quantas o Coder consumiu, artefatos por sessão,
ponteiros de transcript e dossiê. Nenhuma precisou de uma quinta tabela: **o
console é tela sobre dado que já existe.**

**Dois achados de ambiente, nenhum deles do código desta fase:**
1. **O `dev_inject` não entrega o resultado de turno promovido a background.** O
   cenário E0 voltou vazio três vezes (`reply_len=0`, 0 tokens) — e **não era o
   agente calado**: quando o turno estoura o teto de promoção, a continuação vai
   pra uma *task* `asyncio` do mesmo processo, e o `dev_inject` encerra o processo,
   matando a task. Subindo o teto, a resposta apareceu inteira. **É uma lacuna do
   arnês que o §9.5 não lista, e ela morde onde dói:** todo cenário conversacional
   caro é pesado por definição, logo promovido, logo invisível pro arnês.
2. **A bancada do Coder na instância de desenvolvimento não contém o repositório do
   Kobe nem o do plugin**, então os cenários 1 e 2 como o briefing os escreveu não
   abrem sala a partir do bot de dev — a trava de escopo recusa antes, e com razão.
   Adaptei os dois para cópias descartáveis dentro da bancada. **A semântica não se
   perdeu** — ao contrário: no E2 o alvo passou a ser uma cópia do repositório do
   plugin, e o sistema declarado continuou `Kobe`, que era precisamente o ponto.

**Testes:** suíte completa: **774 passando**. As 10 falhas de
`tests/test_db_integration.py` e `tests/test_compat_gate.py` são **pré-existentes**,
por dado residual de 26/08 no banco `kobe_test`, e reproduzem idênticas na árvore de
dev sem nenhum código desta fase. Consertar exige apagar dado — ação da lista dura —,
então ficam reportadas e não consertadas.

**O que NÃO foi provado, declarado e não maquiado:** que o coletor roda **toda
madrugada** (§9.5, L4 — exigiria esperar até a madrugada; o que se testa é a função,
e o agendamento se verifica por inspeção); a entrega Telegram→bot (L3); qualidade de
memória durável (L2, fora do escopo); e o comportamento sob carga ao longo do tempo
(L5) — **a bateria prova a entrega, não a saúde de longo prazo**.

**Commits:** este fecha a série da F1.

**Reversão:** nada aqui é código de runtime. As duas chaves da fase seguem
desligadas no `.env.example`, e o `.env` de desenvolvimento foi **restaurado do
backup** ao fim da bateria.

### docs+config: a camada 3 da declaração, as chaves da F1 e o roteiro da bateria (2026-08-29)

**Operador pediu:** fechar a Camada 3 do §6.3 do briefing — *"quem preenche sou eu,
não você"*. As camadas 1 (o banco recusa) e 2 (o dispatch exige) já estavam nos
commits anteriores; faltava escrever a regra onde o agente a lê.

**Por quê:** as duas primeiras camadas garantem que uma linha errada não entre.
Nenhuma delas faz a linha CERTA entrar — isso depende de o agente saber deduzir o
par a partir do pedido, e saber **perguntar** quando é genuinamente ambíguo. Sem
esta camada, o operador teria de digitar `--system` ele mesmo, que é exatamente o
oposto do desenho.

**Foi feito:**
- **`CLAUDE.md`**, seção do Mission Control — a tabela da regra, as quatro
  ressalvas (a pasta não decide; `none` é declaração e omitir é recusado; em dúvida
  genuína pergunte **uma** linha; sistema novo é evento), e **como ler os dois
  desfechos de recusa**, que pedem reações opostas: `refusal` se conserta
  declarando direito, `unavailable` **não se conserta redeclarando** — nenhuma
  declaração cura um Postgres fora do ar.
  Uma ressalva que vale citar, porque é onde a regra costuma virar fricção inútil:
  *"ambiguidade não é o mesmo que informação óbvia faltando"*. `"o gate do plano do
  Coder"` **não** é ambíguo — é `Kobe / Coder`. Pergunta-se só quando duas leituras
  honestas levariam a sistemas diferentes.
- **`.env.example`** — as chaves da fase, com o porquê de cada default. Três avisos
  que não são decorativos: **as duas chaves nascem desligadas**; a **ordem de
  ativação** é migration → chaves → restart (a inversa quebra); e
  `~/.claude/projects` é do **host e é um só**, então ligar o coletor em dois
  ambientes ao mesmo tempo produz duas verdades e o dobro do disco.
- **`tests/roteiros/f1-dispatch.txt`** — o roteiro versionado da bateria
  conversacional, com os quatro cenários e o custo de cada um escrito no próprio
  arquivo. **A ordem dos cenários está invertida em relação ao briefing, de
  propósito:** o caso ambíguo (custo zero) vem primeiro, porque é um dos dois que
  de fato provam a regra — se ele falhar, não vale gastar cota com os dois que
  abrem sala.

**Uma divergência de caminho, registrada:** o §9.6 do briefing propõe guardar os
roteiros na pasta de avaliação do `infra/`. O repositório já tinha
`tests/roteiros/` estabelecido, com os roteiros da F0 e da F0.5 lá dentro. Segui a
convenção que existe — espalhar roteiro por duas pastas custaria mais que a
diferença de nome.

**Testes:** `tests/test_portability.py` **pegou uma violação minha** — eu havia
deixado um caminho absoluto de máquina de operador (`/home/<usuário>/...`) dentro
de `tests/test_work_catalog.py`, num teste que usa a pasta do plugin como exemplo.
O repositório é público; isso é vazamento de ambiente pra dentro do que é
versionado. Trocado por caminho genérico. Depois: `test_portability`,
`test_env_parity` e `test_work_catalog` — **45 passando**.

**Commits:** este.

**Reversão:** só texto e configuração de exemplo. As chaves documentadas já nascem
desligadas; remover as seções não altera comportamento nenhum.

### feat: o dispatch do Mission Control declara sistema e subsistema — ou não abre sala (2026-08-29)

**Operador pediu:** o quarto entregável da F1 — *"`system` e `subsystem` como
entrada obrigatória do dispatch, nos dois dispatchers"*. Este commit é o lado do
Mission Control; o do Coder vem no próximo (é plugin, em repositório separado).

**Por quê:** a regra do operador (§6.1) é que o sistema seja **declarado antes de a
sala começar — nunca inferido, nunca em branco**. E a garantia tem três camadas: o
banco recusa (`system_id NOT NULL` com FK), **o dispatch exige**, e quem preenche é
o agente. Esta é a Camada 2.

**Foi feito:**
- `abrir_sala()` e o subcomando `abrir` ganham `--system` e `--subsystem`.
- **O registro vem ANTES do primeiro `mkdir`.** A ordem é o desenho inteiro: se o
  registro falha, **nada é criado** — nem a pasta da missão, nem o `sala.json`, nem
  o processo tmux. A frase *"NENHUMA sala foi aberta"*, que a recusa imprime, tem
  que ser literalmente verdade, inclusive no disco. O contrário produziria
  exatamente o que a F1 veio corrigir: sala trabalhando sem linha em lugar nenhum.
- **Recusa de regra e falha de instrumento chegam diferentes** (`refusal` vs.
  `unavailable`). Têm o mesmo desfecho e reações opostas: uma se resolve declarando
  direito, a outra consertando o serviço. Iguais, o agente tentaria redeclarar
  contra um Postgres morto.
- `--system`/`--subsystem` **não** são `required=True` no argparse, de propósito:
  quem recusa é o catálogo, com uma mensagem que ensina o que fazer. Aqui a recusa
  **é** a entrega, e quem a lê é um agente decidindo o próximo passo — um "the
  following arguments are required" o faria tentar outro nome até colar, que é como
  um erro de digitação vira sistema fantasma.

**Testes:** `tests/test_mission_control_catalog.py` — **14 passando**. A asserção
central de quase todos não é o conteúdo do retorno: é a **contagem de pastas de
missão antes e depois**, mais um dublê que registra se o worker chegou a ser
disparado. Cobre as 6 formas de errar a declaração, a distinção banco-fora, a
**ordem** (um espião dentro do dublê do spawn confere que a linha já existe no
instante em que a sala nasceria), `none` gravando nulo, `cwd` como metadado, e o
**rollback** (com a chave off, a sala abre sem declaração — se esse teste quebrar,
desligar a chave deixou de ser rollback).
O caminho feliz usa dublê no spawn, e não por preguiça: abrir sala de verdade
dispara sessão Claude, e cota é o recurso escasso da campanha. O dublê ainda prova
melhor, porque deixa observar o estado **antes** do spawn.

**Cenário C4 do plano, verde, rodado à mão contra o `KOBE_HOME` real:** as três
recusas (`sistema_nao_declarado`, `sistema_desconhecido`, `subsistema_nao_declarado`)
saíram com exit 1, e a contagem antes/depois ficou idêntica — **1 pasta de missão e
2 sessões tmux**, as mesmas de antes.
Regressão conferida: `test_mission_control_sala.py` e `test_mission_control_handoff.py`
seguem verdes (13 testes).

**Commits:** este.

**Reversão:** `WORK_CATALOG_ENABLED=false` — o dispatch volta a aceitar abertura sem
declaração, exatamente como antes.

### feat: o relógio do coletor — fonte do Keyko a custo de cota ZERO (2026-08-29)

**Operador pediu:** que o coletor da F1 *"rode por relógio (diário, pelo Keyko)"*,
e a mitigação da lacuna L4 recomendada pelo briefing: que a falha de agendamento
**apareça** em vez de passar em silêncio.

**Por quê:** o Keyko é o daemon de despertar — as fontes dele devolvem `Despertar`,
e cada `Despertar` dispara um `claude -p`. Se o coletor fosse uma fonte comum, o
Kobe passaria a acordar um modelo **todo dia pra copiar bytes de um arquivo pra
outro** — gastando cota, que é o recurso escasso desta campanha, na tarefa mais
burra do sistema.

**Foi feito:** `bot/transcripts/source.py` + registro em `bot/keyko/registry.py`.

- **A fonte devolve sempre lista vazia** e faz a coleta dentro do próprio `tick()`.
  Não é contorno: o protocolo `Source` prevê isso com todas as letras (*"Source faz
  seu trabalho colateral … Lista vazia é normal"*). **Custo de cota: zero.**
- **O primeiro tick é imediato** (o Keyko inicializa `proximo_tick` em zero), e isso
  resolve a metade mais provável da L4 sozinha: reiniciar o daemon deixa de "pular"
  o dia e passa a **causar** uma coleta.
- **A idade é medida ANTES de coletar.** A ordem é o ponto — depois de coletar a
  marca está sempre fresca e o aviso nunca dispararia. Olhando antes, o buraco
  aparece no instante em que ele acabou de terminar. Anti-repetição de 24 h, porque
  alerta que se repete a cada tick vira ruído, e ruído é ignorado: o mesmo destino
  do silêncio que ele veio combater.
- **O aviso não adivinha destino.** Usa `TRANSCRIPT_ALERT_CHAT_ID` (+ thread
  opcional); sem isso, fica no log. Uma mensagem de saúde do sistema caindo num
  tópico qualquer é pior que não mandar. A visibilidade não depende só daqui — o
  terceiro degrau da mitigação é o dispatcher do Coder lendo a marca a cada
  abertura de sala, porque **quem vigia não pode ser só o vigiado**: se o Keyko
  estiver fora, esta fonte não roda, e é exatamente aí que o degrau de fora salva.
- **`build()` devolve `None` com a chave desligada**, e a fonte não é registrada.
  Uma fonte registrada que não faz nada aparece no log de inicialização como se
  estivesse trabalhando, e "quem o Keyko está observando" deixaria de ser verdade.
- Cadência diária (`TRANSCRIPT_COLLECT_INTERVAL_S`), com **piso de 60 s** — um valor
  torto no `.env` não pode virar um laço apertado lendo dezenas de MB de cabeçalho
  por segundo.

**Testes:** `tests/test_transcript_source.py` — **17 passando**. Os dois que mais
importam não testam "o coletor roda": testam que **a fonte nunca devolve
despertar** (regressão silenciosa e cara, se alguém "melhorar" isso) e que **ela
nunca levanta** (o Keyko é single-threaded, e um coletor que morre calado é a L4 de
volta). Também: aviso uma vez por dia, a **ordem** avisar→coletar, o aviso sem
destino ficando no log, e a conformidade com o protocolo `Source` (que é
`runtime_checkable`, então verificar é barato).
Verificado à mão no registro do Keyko: com a chave off, `fontes: ['alertas']` e a
linha *"source transcripts NÃO registrada"*; com a chave on,
`[('alertas', 30.0), ('transcripts', 86400)]`.

**O que isto NÃO prova, e é honesto dizer** (§9.5, L4): que a coleta acontece **toda
madrugada**. Isso exigiria esperar até a madrugada. O agendamento se verifica por
inspeção da configuração; o que se testa é a função, disparada à mão — que é o que o
próprio briefing manda.

**Commits:** este.

**Reversão:** `TRANSCRIPT_COLLECTOR_ENABLED=false` — a fonte deixa de ser registrada
e o Keyko volta a ter só a de alertas.

### feat: o dossiê por sala — legível ANTES de ela fechar, e determinístico (2026-08-29)

**Operador pediu:** o terceiro entregável da F1 — *"`dossier.md` por sala: o que
decidiu, o que ficou aberto, o que produziu, regenerado por acúmulo, com
`status: em andamento | concluída`"*.

**Por quê:** o critério de pronto da fase pede, com essas palavras, que o dossiê
seja *legível **antes** de a sala fechar*. Não é conveniência — é a decisão E3
(*"nenhuma peça pode ter 'fechar a sala' como gatilho"*) aplicada ao artefato mais
visível da fase. Uma sala que morre feio (cota, crash, OOM) não pode levar junto o
registro do que fez, e é exatamente isso que acontece quando o resumo só nasce no
fim.

**Foi feito:** `bot/transcripts/dossier.py`, ligado ao coletor — cada sala que teve
novidade tem o dossiê regenerado na mesma passada.

**A decisão de desenho que importa: ele é DETERMINÍSTICO, sem LLM.** Três motivos,
e o terceiro é o que decide. (1) **Custo** — o §9.6 já marca a F1 como a fase mais
cara, e um resumo por sala regenerado a cada acúmulo é conta que cresce sozinha.
(2) **Escopo** — destilação com julgamento é a F3 (LUCIEN); antecipá-la faria a F1
depender de um LLM pra entregar um artefato que o critério só exige que seja
*legível*. (3) **Procedência** — o que um modelo escreveria sobre a sala é *texto
gerado*; o que está ali é *o que a sala de fato disse e fez*. A dor que esta missão
existe pra curar é tratar texto plausível como fato; seria irônico o artefato dela
inventar o resumo.

E as fontes determinísticas são melhores do que parecem: as mensagens do
`kobe-notify` **são**, por construção do rito, exatamente os marcos, bloqueios e
conclusões, escritos pela própria sala no momento em que aconteceram; as caixas não
marcadas do `.local/plano-*.md` **são** literalmente o que ficou aberto; os arquivos
escritos e os commits **são** o que ela produziu.

**Uma honestidade de rotulagem, deliberada:** numa sala do Coder a fala do operador
e os prompts injetados pelo sistema chegam ao transcript do mesmo jeito (linhas
`user`), e **não há como separá-los com confiança**. Por isso a seção se chama *"O
que entrou na sala"* e não *"o que o operador disse"* — e o dossiê diz isso, no
próprio texto. Rotular texto de máquina como fala do operador criaria exatamente o
tipo de falso que a F3 terá de desfazer.

**Dois defeitos achados rodando, não lendo:**
1. **O catálogo não chegava ao dossiê** — ele saía com `sistema: (não catalogada)`
   mesmo com a linha existindo no banco. Causa: o re-exec no venv do
   `kobe-collect-transcripts` conferia só `psycopg`, que **existe** no python do
   sistema desta VPS; `psycopg_pool`, que `bot/db.py` importa no topo, **não**
   existe. Sem re-exec, `bot.db` falhava lá dentro. **É o mesmo defeito que o
   `kobe-reflect` já teve em 27/08/2026, pela mesma causa** — e o comentário que
   documenta aquele conserto avisa exatamente isto: *"conferir TODAS as
   dependências, não uma delas"*. Agora confere as três.
2. **O silêncio que escondeu o item 1 por três execuções.** Um `except Exception:
   pass` engolia a falha do catálogo. Degradar é aceitável (a coleta é a parte que
   não pode falhar, e um Postgres fora não pode impedir a cópia de um transcript);
   degradar **calado** não é. Agora há `RunResult.catalog_note`, e o CLI imprime
   `⚠️ <motivo>`.
   Também corrigido: `f"{n:,}".replace(",", ".")` era aplicado à linha inteira e
   comia a vírgula seguinte — *"1.678.468 bytes. 726 linhas"*.

**Testes:** `tests/test_transcript_dossier.py` — **15 passando** (41 no conjunto com
o coletor). Cobre os quatro rótulos de status, dossiê sem catálogo, **D2** (acúmulo
sem duplicar), a procedência de cada seção, lembrete de sistema não virando pedido,
arquivo repetido contado uma vez, `thinking` contado mas não despejado, linha
corrompida não impedindo a geração, e o transcript nunca sendo tocado.

**A prova do critério (d), com a sala VIVA:** gerei o dossiê **desta** sessão
enquanto ela trabalhava. Saiu com `status: em andamento`, `sistema: Kobe`, 1.766.208
bytes / 767 linhas / 225 turnos / 64 blocos de raciocínio, os 7 marcos que eu havia
notificado (nas minhas palavras, na ordem), os 3 commits pela mensagem, os 12
arquivos escritos, os 3 artefatos catalogados, e **"5 de 16 concluídos"** com as 11
caixas abertas do plano listadas uma a uma.

**Commits:** este.

**Reversão:** o dossiê é derivado — apagá-lo custa uma regeração, e a fonte de
verdade (`.jsonl`) não é tocada. `TRANSCRIPT_COLLECTOR_ENABLED=false` desliga tudo.

### feat: `kobe-collect-transcripts` — o coletor incremental por deslocamento de byte (2026-08-29)

**Operador pediu:** a F1 da Highlander v3 — *"salvaguarda do bruto (captura das
salas)"*. Este é o terceiro dos onze passos, e é a peça que dá nome à fase.

**Por quê:** o Claude Code guarda, por sala, um registro riquíssimo em
`~/.claude/projects/<projeto>/<session_id>.jsonl` — o pedido do operador, o
raciocínio, cada ferramenta chamada, cada resultado — **e o apaga em 30 dias**.
Medido em 29/08/2026: o mais antigo em disco era de 30/07, exatamente a janela do
`cleanupPeriodDays` padrão. É o registro mais completo que existe do trabalho de
engenharia deste sistema, e estava sendo jogado fora continuamente. Os transcripts
das salas de julho — as que originaram esta missão — já não existem. Esta é a única
frente da campanha em que **esperar destrói valor de forma irreversível**.

**A premissa foi medida, não suposta.** No transcript de uma sala que estava
trabalhando naquele instante: em 15 s o tamanho foi de 550.264 → 552.071 bytes, o
**inode não mudou**, o SHA-256 dos primeiros 64 KB ficou **idêntico**, e o arquivo
terminava em `\n`. É isso que torna a cópia incremental segura sem travar nada.

**Foi feito:**
- **`bot/transcripts/state.py`** — o estado (deslocamento e impressões digitais por
  sessão) com trava de arquivo. A trava é **não-bloqueante** de propósito: quem
  chama é um relógio, e relógio que espera acumula fila; a passada que já está
  rodando faz o mesmo trabalho. E ela mora num arquivo **separado** do estado,
  porque o estado é reescrito por `rename` — travar um arquivo que vai ser
  substituído trava um inode que deixou de ser o arquivo, e duas passadas
  achariam que têm a trava, em silêncio.
- **`bot/transcripts/collector.py`** — as três garantias: **nunca corrompe** (a
  cópia para no último fim-de-linha completo; uma linha em escrita fica pra
  próxima passada, inteira), **nunca duplica** (o deslocamento vem do estado),
  **nunca sobrescreve** (a escrita é `'ab'`, sempre). Quando o começo do arquivo
  muda, o antigo é **preservado** como `.superseded-<carimbo>` e o novo é
  recopiado do zero — preservar em vez de apagar é reversibilidade aplicada a
  dado, e custa disco, o recurso abundante aqui.
- **A cópia é crua e íntegra.** Nenhum bloco é peneirado, nem `thinking` —
  requisito do operador, decidido por escrito em 27/08 e reafirmado em 29/08.
  Medido: `thinking` é **7,3%** do bruto e **63%** do peso é envelope/metadado,
  então descartar raciocínio não moveria o ponteiro do disco e custaria justamente
  o que a fase existe pra salvar. Sem compressão (E8). Efeito colateral bom: como
  a cópia é byte a byte, as três garantias viram verificáveis com `sha256sum`.
- **`bot/bin/kobe-collect-transcripts`** — `run` (padrão), `status`, `--dry-run`,
  `--session <short-id>`. Saídas: `0` ok · `3` erro · `4` chave off (não é erro) ·
  `5` já há coleta em andamento (também não é erro).
- **A mitigação da lacuna L4** — a única que o briefing classifica como *"a com
  maior chance de virar surpresa"*: um coletor que pare de rodar não produz erro,
  produz **silêncio**, indistinguível de "não havia nada novo". Agora há
  `last_run_at` e `last_success_at` — dois campos e não um, porque a diferença
  entre eles é o diagnóstico: se os dois envelhecem juntos, o relógio parou; se só
  o de êxito envelhece, o relógio bate e o coletor falha.
- **O catálogo é atualizado fora da trava e best-effort.** Um Postgres fora do ar
  não pode ser motivo pra o transcript de uma sala deixar de ser copiado.

**Um defeito sério, achado pelo próprio teste:** com o **estado perdido**
(corrompido ou apagado) e um destino já existente, o coletor **duplicava o acervo
inteiro** — `bytes_copied` voltava a 0 e o arquivo era ANEXADO por cima do que já
estava lá, porque a escrita é append e "recopiar" ali não substitui, dobra. O
destino ficava com o dobro do tamanho e cada linha duas vezes, sem nada denunciando.
Conserto: quando o estado se perde e há destino, o deslocamento é **reconstruído do
próprio destino**, verificando por comparação em blocos que ele é prefixo byte a
byte da origem; se não for, cai no caminho de preservar-e-recopiar.

**Testes:**
- **`tests/test_transcript_collector.py` — 26 passando.** Quase toda asserção é
  sobre `sha256` de prefixo, contagem de linhas e `json.loads` linha a linha, e não
  sobre valor de retorno: um coletor que devolvesse o dicionário certo e escrevesse
  lixo passaria num teste de retorno e reprovaria em todos estes. Cobre sala viva,
  idempotência, append puro, linha incompleta, começo trocado, inode novo, destino
  apagado, estado corrompido, estado perdido (os dois desfechos), a trava, e uma
  rede contra regressão de intenção que lê o próprio código do coletor procurando
  `unlink` e `'wb'`.
- **Bateria contra o acervo REAL — 8/8 verdes.** 387 transcripts, 94,4 MB em 0,6 s.
  `A8` nº de linhas do destino igual ao da origem nos **387** arquivos, 0 erros ·
  `A1a` **36.649 linhas** validadas como JSON, 0 mal-formadas, 0 arquivos sem `\n`
  final · `A2` 2ª passada copiou **0 bytes** e nenhum arquivo trocou de conteúdo ·
  `A3` os **387** prefixos com sha256 idêntico · `A1` a sala viva (esta) cresceu de
  1.537.086 → 1.538.756 bytes entre duas coletas, seguiu íntegra, 440 uuids
  distintos em 440 · `A7` a 2ª passada simultânea desistiu · `A6` coleta recente =
  ok, coleta forçada pra 3 dias atrás = STALE com aviso.

**Commits:** este.

**Reversão:** `TRANSCRIPT_COLLECTOR_ENABLED=false` (o default) — o comando responde
`exit 4` e nada é colhido. O que já foi colhido fica (a coleta não é destrutiva).

### feat: `kobe-work-session` — quem escreve no catálogo, e a distinção entre recusa e falha (2026-08-29)

**Operador pediu:** a F1 da Highlander v3. Este é o segundo dos onze passos: o motor
e o CLI que registram uma sala **antes** de ela ser aberta, nos dois dispatchers.

**Por quê:** os dois dispatchers que precisam registrar são de naturezas diferentes —
o do Mission Control é core, e o do Coder é um **plugin em repositório separado** que
hoje não importa `bot.*` e não fala com o Postgres. Esse limite é bom e vale manter:
plugin com driver de banco vira acoplamento que ninguém desfaz depois. Então o
conhecimento de banco fica no core e o plugin chama um processo — um contrato de saída,
não um `import`.

**Foi feito:**
- **`bot/work_catalog.py`** — resolução de sistema/subsistema (por slug **ou** nome, sem
  diferenciar caixa: quem escreve a declaração é um agente escrevendo prosa, e exigir
  grafia exata transformaria uma questão de maiúscula numa sala que não abre),
  `register_session`, `touch_session`, `close_session`, `add_artifact`.
- **`bot/bin/kobe-work-session`** — a casca. Quatro códigos de saída, e a distinção
  entre eles é o ponto do helper: `0` ok · **`2` recusa DE REGRA** (não declarou o
  sistema, ou declarou um que não existe) · **`3` falha DE INSTRUMENTO** (banco fora,
  migration não aplicada) · **`4` chave desligada**, que não é erro e sim o estado de
  rollback ("siga sem registrar"). Quem lê esta saída é um agente, e recusa e falha
  pedem reações **opostas**: confundi-las o faria inventar um sistema pra satisfazer um
  erro que não era sobre sistema nenhum. É a mesma lição do conserto do `kobe-reflect`
  de 29/08 — lá, um timeout do serviço saía com a frase de "não há registro".
- **Omissão de `--subsystem` é recusa; `--subsystem none` é aceito.** A assimetria é o
  §6.3 do briefing: sem ela o catálogo não distinguiria "esta sala não tem subsistema"
  de "ninguém preencheu".
- **`topic_id` resolvido pelo PAR `(chat_id, thread_id)`.** A restrição real de `topics`
  é `UNIQUE (telegram_chat_id, telegram_thread_id)` — e há `telegram_thread_id = 2` em
  dois chats distintos no banco de dev. Resolver só pelo thread apontaria a linha do
  catálogo pra conversa de outra pessoa, em silêncio.
- **Registro idempotente por `session_id`** — um `--force` ou um start reexecutado não
  pode virar linha dupla nem erro.
- **`tests/test_work_catalog.py`** — 27 testes. Os de banco rodam contra Postgres de
  verdade, numa transação revertida por teste (mesmo desenho de `test_db_integration.py`),
  porque restrição de integridade **não existe em teste com dublê**: um `fake_db` passaria
  verde exatamente no cenário que a fase existe pra impedir.

**Dois defeitos que só apareceram porque os testes rodaram:**
1. **O helper pendurava com o banco fora.** Ele usava o `KobeDB`, que é a ponte do bot —
   um processo LONGO, com pool, reciclagem de ociosa e repetição com espera progressiva.
   Num CLI de três consultas isso é a ferramenta errada: apontado pra uma porta fechada,
   o helper **travou até o teste estourar em 60 s**. Em produção isso significaria o
   dispatcher preso pra sempre no meio de abrir uma sala — o operador não recebe nem a
   sala nem o erro, que é o pior desfecho possível. Trocado por conexão direta com
   `connect_timeout` (5 s, `WORK_CATALOG_CONNECT_TIMEOUT`), reusando do `bot.db` o que
   importa reusar: o `_normalize_row`, o contrato de fronteira de tipos. Medido depois:
   a suíte caiu de **63 s para 3,3 s**, e "banco fora" virou `exit 3` em cinco segundos.
2. **O re-exec no venv não achava o venv quando o helper roda de uma worktree do Coder**
   (onde `.venv/` não existe, por ser gitignorado) — morria com
   `ModuleNotFoundError: psycopg_pool`. Agora procura em `$KOBE_HOME/.venv` primeiro.

**Testes:** `tests/test_work_catalog.py` — **27 passando**, contra `kobe_test` com a 006
aplicada. Cobre: as 5 recusas de integridade indo direto no SQL (sem passar pelo módulo
Python, de propósito — se a recusa dependesse do módulo, sumiria no dia em que alguém
escrevesse na tabela por outro caminho, e "outro caminho" é o caso normal aqui); a
assimetria omissão/`none`; o **caso que prova o desenho** (mesmo `cwd` do plugin Coder,
dois sistemas diferentes declarados — o catálogo obedece à declaração, não à pasta); a
resolução de tópico pelo par; a idempotência; e o **contrato de saída do CLI** exercitado
por subprocess, que é como os dispatchers de fato o chamam. **Rodada duas vezes seguidas
com o mesmo resultado** — a primeira versão do teste de consultas afirmava sobre a tabela
inteira, passou na 1ª execução e quebrou na 2ª por resíduo commitado; foi corrigida pra
filtrar pelos ids que ela mesma cria.

**Achado pré-existente, reportado e NÃO consertado aqui:** 10 testes de
`tests/test_db_integration.py` e `tests/test_compat_gate.py` falham em `kobe_test` por
**dado residual de 26/08/2026** (65 mensagens, 37 sessões, 54 tópicos acumulados). Nada
a ver com esta mudança: reproduzem idênticos na árvore de dev sem nenhum código meu.
Consertar exige apagar dado, o que é ação da lista dura — fica reportado ao operador.

**Commits:** este.

**Reversão:** `WORK_CATALOG_ENABLED=false` (o default) — o helper responde `exit 4` e
nada é registrado. Os arquivos novos podem ser removidos sem efeito: nenhum código
existente os importa ainda.

### feat: o catálogo de desenvolvimento — as quatro tabelas (migration 006) (2026-08-29)

**Operador pediu:** a F1 da Highlander v3 — *"salvaguarda do bruto (captura das salas) +
catálogo de desenvolvimento"*. Este commit é a primeira das quatro entregas: o esquema
que registra **cada sala de trabalho** (Coder ou Mission Control) no momento em que ela
nasce, com o sistema e o subsistema **declarados**, nunca inferidos.

**Por quê:** hoje não existe nenhum lugar que responda *"o que já foi feito no sistema
Flow?"* ou *"quantas sessões o plugin Coder consumiu desde que nasceu?"*. As salas nascem,
trabalham e morrem sem deixar linha em lugar nenhum — o que sobra é o arquivo de estado
do dispatcher, que some junto com a faxina. A regra que o operador deu (§6.1 do briefing):
código do Kobe é `system=Kobe, subsystem=(nenhum)`; código de **plugin** do Kobe é
`system=Kobe, subsystem=Coder|Atrus|Apolo|Monet|Flow`. E **a pasta não decide nada** — no
caso do plugin ela é outra, e ainda assim o sistema é o Kobe. Um desenho que derivasse o
sistema do diretório erraria exatamente no caso que mais interessa. Prova viva: existe um
plugin `flow` **e** o app web Flow; pela pasta seriam a mesma coisa, pela declaração não
há confusão.

**Foi feito:**
- **`infra/migrations/006_work_catalog.sql`** — `work_systems`, `work_subsystems`,
  `work_sessions` e `work_session_artifacts`, conforme o §6.2 do briefing.
- **`work_sessions.system_id` é `NOT NULL` com chave estrangeira.** Não é convenção, é
  restrição de integridade: se o dispatch não trouxer sistema válido, a linha não entra.
- **Uma guarda a mais do que o briefing escreveu: chave estrangeira COMPOSTA**
  `(subsystem_id, system_id) → work_subsystems (id, system_id)`. Sem ela nada impediria
  gravar `system=Flow` com `subsystem=Coder` — dois campos individualmente válidos
  formando um par impossível. Exigiu um `UNIQUE (id, system_id)` em `work_subsystems`,
  redundante com a chave primária e existindo só pra ser referenciável.
- **`work_sessions.id` sem default, de propósito:** é o `session_id` do Claude Code, que o
  dispatch já gera e já passa em `--session-id`. Verificado em disco: o transcript da sala
  se chama `<session_id>.jsonl`. Reaproveitar essa chave evita uma tabela de-para inútil.
- **`cwd` entra como metadado** (serve pra achar o transcript e pra saber onde a sala
  rodou), nunca como chave.
- **`topic_id` é nulo-permitido** — dispatch fora de tópico (linha de comando, teste) não
  pode ser impedido de nascer por falta de tópico; o que não pode faltar é o sistema.
  Nota de campo registrada no arquivo: `topics` é único por
  `(telegram_chat_id, telegram_thread_id)`, **não** por thread — há `thread_id=2` em dois
  chats distintos, então quem resolve `topic_id` tem que usar o par.
- **Sementes:** sistemas `Kobe` e `Flow`; subsistemas de Kobe `Coder`, `Atrus`, `Apolo`,
  `Monet`, `Flow`. Sistema fora da lista é **evento** — o dispatch recusa e o agente
  pergunta antes de registrar, pra que erro de digitação não vire sistema fantasma.

**Testes:** executados por mim no **dev VPS**, contra `kobe_dev`. Antes de aplicar em
definitivo (migration aplicada é imutável), rodei o arquivo **duas vezes dentro de uma
transação com `ROLLBACK`** — 2 sistemas e 5 subsistemas na 1ª execução, os mesmos 2 e 5 na
2ª, provando a idempotência do arquivo sem deixar resíduo. Na mesma transação descartável,
o **cenário B2** do plano de testes: `system_id` NULL → `NotNullViolation`; `system_id`
inexistente → `ForeignKeyViolation`; **subsistema de outro sistema → `ForeignKeyViolation`**
(a FK composta funcionando); `kind` inválido → `CheckViolation`; `status` inválido →
`CheckViolation`. **Os cinco recusados pelo banco.** E os dois caminhos felizes passando:
`Kobe/Coder` e `Flow` com subsistema nulo. Depois disso, o **cenário B1** com o runner de
verdade: `status` (006 pendente) → `up --dry-run` (1 pendente, nada escrito) → `up`
(aplicada) → `status` (7 conhecidas, 7 aplicadas, 0 pendentes) → `up` de novo
(*"nada a aplicar — o banco esta em dia"*). **B1 e B2 verdes.**
**Não aplicada em produção** — isso é do operador, com autorização dele.

**Commits:** este.

**Reversão:** a migration **não é destrutiva** — só cria, não apaga nem altera nada
existente. Voltar atrás é parar de escrever nas quatro tabelas, o que a chave
`WORK_CATALOG_ENABLED=false` faz **sem tocar no banco**. Se um dia quiserem sumir com as
tabelas, é migration nova pra frente (o runner não tem `down`, de propósito).

### fix: o `kobe-reflect` dizia "sem registro" quando o servidor tinha respondido bem (2026-08-29)

**Operador pediu:** consertar o falso negativo silencioso do `bot/bin/kobe-reflect`, com
escopo fechado em dois itens — subir o teto de espera e **distinguir "o serviço demorou/
falhou" de "não há registro"** na saída do helper. Palavra dele na aprovação: *"pode
executar as duas partes exatamente como propostas"*.

**Por quê:** o helper mentia. Um `httpx.ReadTimeout` era engolido por um `except Exception`
genérico, `reflect()` devolvia `None`, e o `None` era impresso como *"(sem registro durável
que responda isso…)"* — a mesma frase de um acervo legitimamente vazio. Como o `CLAUDE.md`
instrui o agente a tratar vazio como ausência de registro, uma falha de infraestrutura virava
a **afirmação** de que não havia registro. Medido em 29/08/2026: o servidor concluiu em
**28,1 s** (`Complete: 993 chars, 4 iterations, 3 tool calls`) com resposta boa e fontes
citadas, e o cliente cortou aos **20,0 s**. Agravante: `str(httpx.ReadTimeout)` é **string
vazia**, então o log do incidente saiu como `reflect falhou (best-effort): ` e mais nada — o
erro não deixava nem rastro do que tinha sido. Isto é exatamente o modo de falha que o
Highlander v3 existe para matar, e o `reflect` é o **instrumento de aferição** das fases
seguintes (o critério de pronto da F2 é "pergunta produz resposta com citação"): instrumento
que reporta falso negativo contamina o veredito de tudo o que vier depois.

**Foi feito:**
- **`bot/hindsight_client.py`** — `reflect()` deixa de devolver `Optional[dict]` e passa a
  devolver um **`ReflectOutcome`** (`status`, `data`, `detail`). O `except` único vira quatro,
  **em ordem significativa**: `HTTPStatusError` → `http_error`; `ConnectError`/`ConnectTimeout`
  → `servico_fora`; `TimeoutException` → `timeout`; `Exception` → `erro`. A ordem importa
  porque `ConnectTimeout` **é subclasse** de `TimeoutException` — invertida, um Hindsight fora
  do ar seria reportado como lentidão. O `detail` do timeout é montado no código (com os
  segundos), justamente porque `str(exc)` vem vazio.
- **Teto de espera 20,0 s → `REFLECT_TIMEOUT_DEFAULT = 90.0`**, sobrescrevível por
  `HINDSIGHT_REFLECT_TIMEOUT`. 90 é ~3,2× a pior medição real: a folga cobre um retry interno
  de provider (um 529 da Anthropic põe 28 s perto de 45 s) e o acervo crescendo a cada fase.
  **Custa latência a zero turno** — confirmado por `grep`: `reflect()` não tem call site em
  `bot/`, `keyko/` ou script nenhum fora do helper, e `HINDSIGHT_RECALL=false` em produção.
- **`render_reflect_section()`** aceita o outcome **ou** o dict cru de antes (retrocompat).
- **`bot/bin/kobe-reflect`** — lê o teto do env (valor torto degrada pro default e avisa, em
  vez de morrer) e passa a ter **três** saídas onde havia uma: seção citada (exit 0); *"não há
  registro LEGÍTIMO"* quando o serviço respondeu e o acervo não cobre (exit 0); e
  `(FALHA DO INSTRUMENTO — isto NÃO é "sem registro"…)` (**exit 3**) em timeout/serviço fora/
  HTTP de erro. O texto de falha é imperativo de propósito: quem o lê é um LLM treinado a
  tratar vazio como ausência, e uma frase branda deixaria o falso negativo voltar pela porta
  do comportamento.
- **`CLAUDE.md`** — a seção "Como ler a saída" tinha dois casos e passa a ter três. Sem isso o
  agente leria a saída nova pela regra velha, e o conserto não chegaria ao comportamento.
- **`.env.example`** — `HINDSIGHT_REFLECT_TIMEOUT=90` documentado, com o porquê do número e a
  distinção em relação ao `HINDSIGHT_TIMEOUT_SECONDS` (que é do retain/recall).

**A garantia que NÃO mudou:** `reflect()` continua sendo best-effort e **nunca levanta** —
todo caminho de erro vira um outcome. O que mudou não é a robustez; é que a razão da falha
deixou de ser jogada fora. Tem teste dedicado para isso.

**Testes (ambiente de desenvolvimento):**
- **18 testes novos** em `tests/test_reflect_outcome.py` (timeout, connect-error, o
  `ConnectTimeout` que prova a ordem dos `except`, HTTP 503, JSON quebrado, exceção
  arbitrária que não escapa, 2xx com e sem texto, retrocompat do render com dict cru, e o
  parsing do env com cinco valores tortos). **Suíte inteira: 632 passed, 53 skipped** — nada
  regrediu.
- **A/B contra o comportamento real**, com a árvore em `HEAD` extraída via `git archive` e um
  servidor de teste que aceita a conexão e nunca responde — que é o cenário do incidente
  (servidor trabalhando, cliente desistindo), reproduzido de forma determinística. Mesmo
  estímulo, mesmo teto de 20 s, os dois códigos:
  - **antes:** `reflect falhou (best-effort): ` (vazio) + *"(sem registro durável que responda
    isso…)"*, **exit 0**;
  - **depois:** *"o Hindsight não respondeu em 20s (teto do cliente, ReadTimeout)"* +
    `(FALHA DO INSTRUMENTO — isto NÃO é "sem registro"…)`, **exit 3**.
- **Integração contra o Hindsight de dev (`:8890`)**: caminho bom → resposta citada em 2,8 s
  com `Fontes:`; pergunta não coberta → *"Não há registro disso."* (exit 0); serviço fora
  (porta 1) → `servico_fora` (exit 3); teto de 0,05 s → `timeout` (exit 3).
- **Achado dos testes, que vale registrar:** na prática o Hindsight responde *em prosa*
  ("Não há registro disso.") quando o acervo não cobre — ele **não** devolve corpo vazio.
  Ou seja, a frase *"sem registro durável"* do código antigo, na prática, era quase sempre
  uma **falha disfarçada de acervo vazio**, e não o acervo vazio. O bug era pior do que o
  relato original supunha.
- **Não testado aqui (runbook para a produção):** a chamada fria real de ~28 s contra o
  Hindsight de produção com o teto novo. É um comando —
  `bot/bin/kobe-reflect "<pergunta>"` num bank frio — e é validação de operador.

**Reversão:** `git revert` dos commits desta entrada. O teto sozinho é reversível **sem
deploy**: basta `HINDSIGHT_REFLECT_TIMEOUT=20` no `.env`.

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
