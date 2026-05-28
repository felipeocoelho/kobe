-- =============================================================================
-- Chat Manager — bypass de slash command interativo (2026-05-28)
-- Cole no Supabase Dashboard → SQL Editor → New Query e clique RUN,
-- OU é executado automaticamente pelo helper local
--   `infra/migrations/run_002.py` (usa Management API + PAT).
-- Idempotente: pode rodar várias vezes sem efeitos colaterais.
--
-- Motivação: quando um plugin (Flow, Coder, etc.) faz pergunta interativa
-- ao operador e este responde algo curto ou desconexo do tema da
-- conversation, o detector tende a abrir conversation nova. Esta coluna
-- guarda o estado "estou esperando resposta de slash command" — quando
-- preenchida e dentro do TTL, o handler força continue na ativa sem
-- chamar o detector.
--
-- Estrutura do JSONB:
--   {
--     "plugin": "<nome ou 'unknown'>",
--     "question": "<texto da pergunta>",
--     "asked_at": "<ISO timestamp UTC>",
--     "expires_in_seconds": 600
--   }
--
-- Helper escreve: bot/bin/kobe-await-response.
-- Leitor/limpador: bot/telegram_handler.py (antes do detector rodar).
-- =============================================================================

ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS awaiting_slash_response JSONB;

-- Verificação:
--   SELECT column_name, data_type FROM information_schema.columns
--    WHERE table_name='sessions' AND column_name='awaiting_slash_response';
--   -- esperado: 1 linha, data_type='jsonb'
