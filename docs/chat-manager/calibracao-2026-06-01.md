# Calibração do New Chat Manager — detecção de borda

Gerado: 2026-06-01 22:46 -03

Método: algoritmo puro `detect_segments` contra corpus rotulado à
mão (doc §7), varrendo grade de knobs. Embeddings reais via
text-embedding-3-small. 1 segmento = 0 cortes; 2 = 1 corte.

## Casos do corpus
- **A_arquitetura_unica** — Papo de arquitetura: 1 assunto grosso, muitos beats finos → NÃO cortar (esperado: 1 seg)
- **B_resposta_curta** — Menu 'Flow ou Kobe?' → 'Kobe' (resposta curta) → NÃO cortar (esperado: 1 seg)
- **C_devkobe_para_olimpo** — Dev Kobe (técnico) → Olimpo (estratégia de lançamento) → DEVE cortar (esperado: 2 seg)
- **D_chatmanager_para_atrus** — Dentro de Dev Kobe: redesenho do chat manager → bug do atrus → DEVE cortar (esperado: 2 seg)

## Knobs escolhidos

- `CM_BORDER_SIM` = **0.4**
- `CM_SUSTAIN` = **3**
- `CM_CLUSTER_COHERENCE` = **0.35**
- `CM_INFO_MIN_WORDS` = 4, `CM_INFO_MIN_CHARS` = 24 (default)

Resultado por caso nos knobs escolhidos:

| Caso | Esperado | Obtido | OK |
|---|---|---|---|
| A_arquitetura_unica | 1 | 1 | ✅ |
| B_resposta_curta | 1 | 1 | ✅ |
| C_devkobe_para_olimpo | 2 | 2 | ✅ |
| D_chatmanager_para_atrus | 2 | 2 | ✅ |

Total de combinações que acertaram todos os 4 casos: **15** (de 48 testadas).

## Nota sobre ruído de transcrição (áudio)

Áudio transcrito mete ruído no vetor. O voto ponderado por
informação (piso de palavras/chars) atenua: respostas curtas e
vagas não votam abertura. Em produção, a flag `CM_*` permite
recalibrar sem deploy de código — só editar .env e reiniciar o keyko.
