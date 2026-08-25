# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

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
