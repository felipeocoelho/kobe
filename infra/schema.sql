-- Kobe — Schema do banco (Supabase / PostgreSQL)
--
-- Rode este arquivo no SQL Editor do projeto Supabase.
-- Pré-requisito: extensão "vector" habilitada em Database → Extensions.
-- A anon key NÃO tem permissão DDL — por isso a execução é manual via painel.

-- ============================================================================
-- Extensões
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- Tabela: topics
-- Cada tópico é um forum topic do Telegram. Lazy discovery: criado na primeira
-- mensagem com message_thread_id desconhecido.
-- ============================================================================

CREATE TABLE IF NOT EXISTS topics (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  telegram_thread_id BIGINT UNIQUE,
  current_name TEXT,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'deleted', 'archived')),
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_topics_telegram_thread ON topics(telegram_thread_id);
CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status);

-- ============================================================================
-- Tabela: topic_name_history
-- Auditoria de renomeações de tópicos (operador pode renomear no Telegram).
-- ============================================================================

CREATE TABLE IF NOT EXISTS topic_name_history (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_topic_name_history_topic ON topic_name_history(topic_id);

-- ============================================================================
-- Tabela: sessions
-- Uma "conversa" delimitada no tempo dentro de um tópico.
-- Cada tópico tem no máximo uma sessão com status='active' por vez.
-- ============================================================================

CREATE TABLE IF NOT EXISTS sessions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id UUID NOT NULL REFERENCES topics(id),
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ended_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'archived', 'compacted')),
  summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_topic ON sessions(topic_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

-- ============================================================================
-- Tabela: messages
-- Histórico bruto de mensagens (operador + Kobe).
-- ============================================================================

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  session_id UUID NOT NULL REFERENCES sessions(id),
  topic_id UUID NOT NULL REFERENCES topics(id),
  telegram_message_id BIGINT,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  audio_transcribed BOOLEAN NOT NULL DEFAULT FALSE,
  tokens_used INT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_topic ON messages(topic_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

-- ============================================================================
-- Tabela: saved_artifacts
-- Documentos persistidos por comando /salvar. Embedding pra busca semântica.
-- VECTOR(1536) = OpenAI text-embedding-3-small / Voyage padrão.
-- Ajuste a dimensão se trocar de provider.
-- ============================================================================

CREATE TABLE IF NOT EXISTS saved_artifacts (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id UUID REFERENCES topics(id),  -- nullable: artefato pode ser global
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  embedding VECTOR(1536),
  tags TEXT[],
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_artifacts_topic ON saved_artifacts(topic_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_embedding
  ON saved_artifacts USING ivfflat (embedding vector_cosine_ops);
