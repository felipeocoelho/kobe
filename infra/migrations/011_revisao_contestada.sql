-- Migration 011 — a revisão da memória: o status `contestada` (2026-10-10)
--
-- Highlander v3, F5. A revisão semanal do LUCIEN mostra ao operador o que foi
-- gravado e as pendências velhas, e ele responde solto: "o 3 tá errado", "a 12
-- já entreguei", "a 15 ainda vale". Esta migration dá ao registro o vocabulário
-- para guardar essas respostas sem mentir sobre o porquê.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Ela ALARGA duas restrições (passam a aceitar valores a mais). Não    │
-- │   apaga linha, coluna nem tabela, não muda tipo nem default, e não     │
-- │   altera nenhuma linha existente. Reaplicá-la não faz nada de novo.    │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- POR QUE UM STATUS NOVO, E NÃO `abandonada`
-- ------------------------------------------
-- "Abandonada" diz que o operador desistiu de algo que valia. "Contestada" diz
-- que a afirmação **nunca foi verdade** — o LUCIEN entendeu errado. Juntar as
-- duas faria o boletim mostrar um erro do curador como decisão do operador, e
-- apagaria a única medida que existe da qualidade do curador: quantas vezes ele
-- foi contestado.
--
-- AS DUAS AÇÕES DE HISTÓRICO
-- --------------------------
--   contested    a afirmação saiu de vigência por contestação do operador
--   reconfirmed  o operador disse que ela ainda vale. Não muda a linha — só o
--                histórico, para a revisão não perguntar de novo tão cedo e para
--                "última vez que alguém confirmou" ser um dado, não um palpite.
--
-- QUEM PEDIU E COM QUE FRASE não precisa de coluna nova: `lucien_events` já
-- guarda o ator, a hora, a imagem de antes e um `detail` livre. A frase do
-- operador, o número do item e a origem da fala vão ali.
--
-- REVERSÃO
-- --------
-- Só é possível enquanto nenhuma linha usar os valores novos. Com linhas
-- `contestada`, desfaça antes cada contestação (`kobe-revisao aplicar` com
-- `desfazer`, ou `store.reverter`), e então recoloque as restrições com as
-- listas antigas (as de 008).

ALTER TABLE lucien_claims
  DROP CONSTRAINT IF EXISTS lucien_claims_status_check;
ALTER TABLE lucien_claims
  ADD CONSTRAINT lucien_claims_status_check
  CHECK (status IN ('vigente', 'superada', 'fechada', 'abandonada', 'contestada'));

ALTER TABLE lucien_events
  DROP CONSTRAINT IF EXISTS lucien_events_action_check;
ALTER TABLE lucien_events
  ADD CONSTRAINT lucien_events_action_check
  CHECK (action IN ('created', 'superseded', 'closed', 'abandoned', 'reverted',
                    'contested', 'reconfirmed'));
