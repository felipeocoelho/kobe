# BUG 1 — Granularidade macro do detector: diagnóstico + proposta de calibração

**Data:** 2026-06-04
**Status:** 🟡 Proposta pronta — **aguardando decisão do operador** (muda comportamento em runtime).
**Card Flow:** `fb0bdaa3-d5e2-4c00-9f9a-554028128fee`

---

## TL;DR

No tópico **Dev Kobe** o detector quase nunca abre conversation nova — dias de
trabalho viram UMA conversation gigante (hoje: **104 msgs em 13h** numa só).
A causa, medida com dados reais, é que o knob `CM_BORDER_SIM=0.40` é baixo
demais pro vocabulário homogêneo do tópico: **só 3,2% das mensagens** ficam
abaixo de 0.40 de similaridade ao centroide. A mediana real é **0.63**.

**Recomendação:** subir `CM_BORDER_SIM` de **0.40 → 0.55**, mantendo
`CM_SUSTAIN=3`. Aplica-se via `.env` do prod + restart do keyko, **sem deploy
de código** e **reversível em uma linha**.

**Tensão honesta:** não existe threshold que recupere a granularidade que o
operador quer E mantenha intactas discussões longas e focadas — porque, em
Dev Kobe, são o mesmo tipo de conteúdo (vocabulário-adjacente). Subir o border
parte alguns raciocínios focados em ~3 pedaços. **A linha é uma escolha do
operador, não um valor "certo".** Por isso esta proposta para aqui.

---

## Correção de premissa do brief

O brief apontou a causa em `bot/conversation_detector.py` (`THRESHOLD_LOW=0.20`,
baixado de 0.35→0.20). **Esse detector está MORTO no caminho ativo.** Desde a
migração de 2026-06-01 (New Chat Manager), a detecção viva roda no daemon Keyko
via `bot/chat_manager/classifier.py` → `detect_segments`, cujo knob equivalente
é `border_sim_threshold` (default **0.40**, env `CM_BORDER_SIM`). O
`conversation_detector.py` só é importado por utilitários (`_parse_vector`,
`_title_and_slug_from_message`, `_get_openai`). Confirmado: `telegram_handler`
não chama nenhuma função de detecção dele; `registry.py` instancia
`ClassifierSource`, que usa o classifier novo.

Ou seja: o `0.20` do brief é irrelevante. O número que importa é o **0.40** do
classifier. **Importante:** a proteção contra msg curta/vaga no sistema novo
**não é o border** — é o gate `is_informative` (≥4 palavras OU ≥24 chars) +
`sustain` + `cluster_coherence`. Logo, subir o border recupera granularidade
**sem** reabrir o problema das msgs curtas que motivou o 0.20 original.

---

## Evidência 1 — distribuição de similaridade real (Dev Kobe)

Replay sobre 125 msgs de operador (com embedding), centroide evoluindo via EMA
(weight=0.15, igual à produção):

```
n=125  min=0.281  p10=0.477  p25=0.542  p50=0.629  p75=0.713  p90=0.775  max=0.831

sim < 0.40:   4/125  ( 3.2%)   <- com o border atual, quase nada vira candidato
sim < 0.45:   7/125  ( 5.6%)
sim < 0.50:  20/125  (16.0%)
sim < 0.55:  36/125  (28.8%)
sim < 0.60:  53/125  (42.4%)
```

A mediana de similaridade ao próprio assunto é **0.63**. Como o detector exige
sim **< 0.40** pra uma msg ser candidata a borda, e ainda pede **3** dessas
seguidas e coerentes, a borda quase nunca dispara. Daí o blob.

(Embeddings comprimem em PT, ainda mais com ruído de transcrição de áudio — por
isso o cosseno "mesmo assunto" fica alto. É esperado e conhecido; ver doc §4.2.)

## Evidência 2 — quantos segmentos por knob (replay do histórico real)

| border | sustain | segmentos | observação |
|---|---|---|---|
| **0.40** | **3** | **7** | **ATUAL** — blob de 104 msgs fica inteiro |
| 0.50 | 3 | 7 | ganho ~nulo |
| **0.55** | **3** | **10** | blob racha em ~4; cortes plausíveis ✅ |
| 0.60 | 3 | 16 | mais fino, começa a picotar |
| 0.55 | 2 | 17 | fragmenta demais (pedaços de 4-6 msgs) |
| 0.60 | 2 | 24 | ruído |

## Evidência 3 — ONDE os cortes caem (border=0.55 / sustain=3)

O blob de 104 msgs de hoje (que com 0.40 fica inteiro) racha em pontos que são
trocas de assunto REAIS — dá pra reconhecer pelo seed de cada segmento:

- `"Eu tô vendo aí que pelas instruções que você tá colocando no Progress Repor…"`
- `"Vou reenviar o áudio aqui"`
- `"Vamos entender de uma vez por todas, não mexe em porra nenhuma da sala de S[PR]…"`
- `"Entendido, era só que como quebrou a linha…"`

Com sustain=2 (não recomendado) apareceriam ainda
`"CONSERTA O CHAT MANAGER ANTES DE QUALQUER COISA"` e
`"Hal, tem uma coisa que vc precisa entender: EU sou um ser humano, eu falo NÃO
LINEARMENTE"` como segmentos próprios — são assuntos distintos de fato, mas o
custo é picotar discussões focadas em fragmentos pequenos demais.

## Evidência 4 — o limite da calibração sintética (corpus A/B/C/D)

Varrendo o corpus rotulado de `infra/calibrate_chat_manager.py` num grid maior:

```
              A   B   C   D    (esperado: A=1 B=1 C=2 D=2)
border 0.40 sustain 3 ->  1   1   2   2   ✅  (único que passa)
border 0.45 sustain 3 ->  2   1   2   2   ❌  (corta o caso A à toa)
border 0.50 sustain 3 ->  3   1   2   2   ❌
border 0.55 sustain 3 ->  3   1   2   2   ❌
border 0.55 sustain 2 ->  5   1   3   3   ❌
```

O **caso A** ("uma discussão de arquitetura, assunto único, muitos beats finos →
NÃO cortar") quebra assim que o border passa de 0.40. E o caso A **é** uma
conversa estilo-Dev-Kobe. Tradução: o corpus sintético foi rotulado com a regra
"discussão técnica longa = 1 assunto", que é **exatamente o oposto** do que o
operador quer agora ("essas tarefas todas que fiz hoje deviam ser conversas
separadas"). Os dois não cabem no mesmo threshold. **É uma escolha de produto,
não um bug de número.**

---

## Recomendação

**`CM_BORDER_SIM = 0.55`, `CM_SUSTAIN = 3`** (coherence fica 0.35).

Por quê:
- Dobra a granularidade real (7→10 segmentos) e racha os blobs nos pontos certos.
- sustain=3 preserva a histerese: um lampejo off-subject não corta; precisa o
  assunto novo ASSENTAR em 3 msgs informativas coerentes. Evita o ruído do
  sustain=2.
- O "custo" (caso A virar ~3) é, na prática, alinhado com o uso real do operador
  em Dev Kobe, que pula entre tarefas mesmo dentro de "kobe interno".
- A pista lexical continua valendo de graça: dizer "muda de assunto", "outro
  assunto", "deixa o X de lado" SEMPRE força corte, em qualquer border.

### Decisão que preciso de você

1. **Aceita 0.55/3** (recomendado) — granularidade real, alguns raciocínios
   focados partem em ~3.
2. **Quer mais fino (0.55/2 ou 0.60/3)** — mais conversas, mais fragmentação.
3. **Quer conservador (mantém 0.40/3)** — sem blob-busting; confia na pista
   lexical pra cortar quando você sinalizar.

Não subo sozinho porque isso muda como a memória é fatiada em **todos** os
turnos. Mas é barato testar (ver runbook) e reverter.

---

## Runbook — aplicar / reverter (sem deploy de código)

Os knobs vêm de `knobs_from_env()`: editar o `.env` do prod e reiniciar o keyko
basta. **Nenhuma mudança de código, nenhum rsync.**

**Aplicar (prod VPS):**
```bash
# /home/felipe/kobe/.env  — adicionar:
CM_BORDER_SIM=0.55
CM_SUSTAIN=3
# (sustain=3 é o default; explicitar deixa registrado)
systemctl --user restart keyko.service
```

**Validar:** depois de algumas horas de uso, `/conversas_topico` em Dev Kobe deve
listar mais conversations, cada uma mais coesa. Conferir no log:
`journalctl --user -u keyko.service | grep "chat_manager classify"` —
`borders=N` > 0 indica cortes acontecendo.

**Reverter (instantâneo):**
```bash
# remover as duas linhas do .env (ou CM_BORDER_SIM=0.40)
systemctl --user restart keyko.service
```

**Notas:**
- Só afeta classificação **futura**. As conversations-blob já existentes ficam
  como estão (não há re-segmentação retroativa — seria destrutivo e fora de
  escopo).
- Os testes `tests/test_chat_manager_classifier.py` usam `Knobs()` default
  (0.40) e seguem verdes — não mexo no default do código. Se a gente quiser
  promover 0.55 a default permanente, aí sim atualizo código + corpus + testes
  juntos (segundo passo, depois de validar em runtime).
- O replay é aproximação (produção processa em batches carregando o centroide
  salvo; o replay roda do zero numa passada). A validação real é observar o
  runtime — que é reversível, então o risco é baixo.

## Limite conhecido / próximo passo possível (fora deste card)

A calibração tem teto porque o detector compara cada msg ao **centroide
acumulado** do assunto inteiro — que, num tópico homogêneo, vira um "blob médio"
do qual nada fica longe. Um fix mais robusto (card futuro) seria comparar a uma
**janela recente** (centroide local) ou detectar DRIFT sustentado, em vez do
acumulado global. Isso é mudança de algoritmo, não de threshold — fora do escopo
de "calibração" deste bug.
