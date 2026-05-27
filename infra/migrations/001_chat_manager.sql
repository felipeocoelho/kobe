-- =============================================================================
-- Chat Manager — Fase 1 (2026-05-27)
-- Cole no Supabase Dashboard → SQL Editor → New Query e clique RUN.
-- Idempotente: pode rodar várias vezes sem efeitos colaterais.
--
-- Antes de rodar, opcionalmente faça backup adicional via:
--   Database → Backups → Create backup
-- (já temos backup lógico em /home/felipe/projetos/kobe/backups/)
-- =============================================================================

-- 1. Topics: UNIQUE composta (chat_id, thread_id)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'topics_telegram_thread_id_key'
  ) THEN
    ALTER TABLE topics DROP CONSTRAINT topics_telegram_thread_id_key;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname = 'topics_chat_thread_unique'
  ) THEN
    ALTER TABLE topics
      ADD CONSTRAINT topics_chat_thread_unique
      UNIQUE (telegram_chat_id, telegram_thread_id);
  END IF;
END $$;

-- 2. Tabela conversations
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  slug TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'dormant', 'archived')),
  centroid_embedding VECTOR(1536),
  parent_conversation_id UUID REFERENCES conversations(id),
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_conversations_topic_status
  ON conversations(topic_id, status);
CREATE INDEX IF NOT EXISTS idx_conversations_embedding
  ON conversations USING ivfflat (centroid_embedding vector_cosine_ops);

-- 3. sessions.conversation_id (nullable até Fase 5 popular)
ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES conversations(id);
CREATE INDEX IF NOT EXISTS idx_sessions_conversation
  ON sessions(conversation_id);

-- 4. messages.embedding (sem índice ivfflat ainda — populado pelo detector)
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS embedding VECTOR(1536);

-- 5. Renomear topic privado existente: 'Geral' → 'Private'
UPDATE topics
   SET current_name = 'Private'
 WHERE telegram_thread_id = 0
   AND telegram_chat_id > 0
   AND current_name IN ('Geral', 'geral');

-- =============================================================================
-- Verificação pós-aplicação (rode separado pra confirmar):
--
--   SELECT conname FROM pg_constraint WHERE conrelid='topics'::regclass;
--   -- esperado: topics_chat_thread_unique (composta)
--
--   SELECT count(*) FROM conversations;  -- 0 (tabela vazia, ainda)
--
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='sessions' AND column_name='conversation_id';
--   -- esperado: 1 linha
--
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='messages' AND column_name='embedding';
--   -- esperado: 1 linha
--
--   SELECT current_name FROM topics
--    WHERE telegram_thread_id=0 AND telegram_chat_id > 0;
--   -- esperado: 'Private'
-- =============================================================================
