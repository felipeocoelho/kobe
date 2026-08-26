# infra/eval — Arnês de regressão anti-alucinação

A "espinha de medição" da Auditoria da Verdade (2026-06). Regra de ouro do plano:
**nenhum conserto anti-alucinação sobe sem responder "resolve quantos dos casos?".**

## O que é
Pra cada caso rotulado, o arnês reconstrói o prompt que o Hal receberia (usando o
`build_prompt` REAL do bot), opcionalmente roda o agente (`claude -p`), e checa se a
alucinação daquele caso **reaparece**. PASS = não reaparece; FAIL = reaparece.

## Como medir um conserto
```
.venv/bin/python infra/eval/harness.py --run            # baseline (quantos FALHAM hoje)
# ...aplica o conserto no código/CLAUDE.md (em dev)...
.venv/bin/python infra/eval/harness.py --run            # re-mede; a diferença é o "resolve quantos"
```
Outras flags: `--dry` (só monta o prompt, não gasta token), `--model X` / `--effort X`
(experimento P0: custo×qualidade), `--case <id>` (um caso só).

## Casos (`cases/`, NÃO versionado — contém trecho real de conversa)
Cada `cases/<id>.json` traz a janela reconstruída, a mensagem-gatilho e a
`failure_signature` (hoje `keyword_any`; o robusto é `llm_judge`, próxima iteração).
Começamos pelos casos-âncora; expande pros 35 conforme cada família ganha conserto.

## Limitações conhecidas (honestas)
- **Não-determinismo:** uma alucinação pode não reaparecer numa única run. Pra baseline
  sério, rodar cada caso N vezes e medir TAXA de falha (o arnês aceita re-execução).
- **Reconstrução parcial:** o arnês replica histórico + nota de background + `[Agora]`.
  (Até 25/08/2026 faltava também a camada de conversa do Chat Manager; ela deixou de ser
  uma lacuna quando o Chat Manager foi aposentado — não existe mais o que replicar.)
- **Checagem keyword** dá falso-positivo/negativo; `llm_judge` é o próximo passo.
