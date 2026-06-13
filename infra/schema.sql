-- Kobe — Schema do banco (Supabase / PostgreSQL)
--
-- Rode este arquivo no SQL Editor do projeto Supabase.
-- Pré-requisito: extensão "vector" habilitada em Database → Extensions.
-- As keys públicas do Supabase (publishable/anon ou secret/service_role)
-- não executam DDL via REST — por isso a execução é manual no painel.
--
-- REGRA DE IDEMPOTÊNCIA (importante pra upgrades):
-- Toda mudança neste arquivo deve ser segura pra re-execução. Padrões:
--   - Nova tabela:   CREATE TABLE IF NOT EXISTS ...
--   - Nova coluna:   ALTER TABLE x ADD COLUMN IF NOT EXISTS y TYPE;
--   - Novo índice:   CREATE INDEX IF NOT EXISTS ...
--   - Nova função:   CREATE OR REPLACE FUNCTION ...
--   - Destrutivo:    bloco DO $$ ... END $$ com guarda explícita +
--                    sinalização explícita nas notas de release.
--
-- A intenção é que o usuário possa colar este arquivo inteiro a cada
-- upgrade sem efeitos colaterais — só aplica o que ainda não foi aplicado.
-- O install.sh em modo upgrade vai consultar uma tabela de versão
-- (a implementar na Fase 9) pra pular este passo quando o banco já estiver
-- em dia.

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
  telegram_chat_id BIGINT,                     -- id do chat (supergrupo) — usado pra mensagens proativas
  current_name TEXT,
  welcomed_at TIMESTAMPTZ,                     -- v0.11: timestamp do envio da mensagem de boas-vindas (NULL = pendente)
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'deleted', 'archived')),
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Upgrades de instalações pré-v0.4: adiciona a coluna se ainda não existe.
ALTER TABLE topics ADD COLUMN IF NOT EXISTS telegram_chat_id BIGINT;
-- Upgrade v0.11: marca tópicos já onboardados (msg de instruções enviada).
ALTER TABLE topics ADD COLUMN IF NOT EXISTS welcomed_at TIMESTAMPTZ;

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

-- ============================================================================
-- Chat Manager — Fase 1 (2026-05-27)
-- Vide ~/.claude/plans/claude-sobre-o-chat-noble-dawn.md pro design completo.
--
-- Mudanças:
-- 1. UNIQUE composta em topics (chat_id, thread_id) — separa chat privado
--    do "Geral" do supergrupo (ambos teriam thread_id=0 antes, colidiam).
-- 2. Tabela conversations — tema longevo aninhando sessions.
-- 3. FK sessions.conversation_id (nullable enquanto Chat Manager está sendo
--    construído; classificação retroativa vai popular).
-- 4. Coluna messages.embedding (pro detector calcular similaridade).
-- 5. Renomear current_name do topic privado existente de 'Geral' → 'Private'
--    pra alinhar com o slug 'private' do `get_topic_slug` atualizado.
-- ============================================================================

-- 1. Topics: UNIQUE composta
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

-- 3. sessions.conversation_id (nullable até classificação retroativa popular)
ALTER TABLE sessions
  ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES conversations(id);
CREATE INDEX IF NOT EXISTS idx_sessions_conversation
  ON sessions(conversation_id);

-- 4. messages.embedding (sem índice ivfflat aqui — só populamos pra detector,
--    busca direta em messages não é caso de uso atual)
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS embedding VECTOR(1536);

-- 5. Renomear topic privado existente: 'Geral' → 'Private'
UPDATE topics
   SET current_name = 'Private'
 WHERE telegram_thread_id = 0
   AND telegram_chat_id > 0
   AND current_name IN ('Geral', 'geral');

-- ============================================================================
-- Apolo — WhatsApp + catálogo de contatos (2026-05-27)
-- Vide ~/.claude/plans/claude-quero-conversar-com-iterative-sonnet.md pro design.
--
-- Tabelas:
-- 1. contacts          — catálogo unificado (pessoa OU grupo WhatsApp).
--                       Reaproveitável por outros canais (email, ClickUp etc.).
-- 2. whatsapp_messages — histórico bruto de OUT/IN do plugin apolo.
--
-- Extensão pg_trgm é usada pra busca fuzzy por nome ("Pedro" → "Pedrão").
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Tabela: contacts (core do Kobe, não amarrado ao plugin apolo)
CREATE TABLE IF NOT EXISTS contacts (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  tipo            TEXT NOT NULL CHECK (tipo IN ('pessoa', 'grupo')),
  nome_canonico   TEXT NOT NULL,
  telefone_e164   TEXT,                          -- pessoa: +5511XXX (E.164)
  whatsapp_jid    TEXT,                          -- pessoa: <num>@s.whatsapp.net; grupo: <id>@g.us
  email           TEXT,
  contexto        TEXT,                          -- ex: "sócio do projeto X"
  notas           TEXT,
  aliases         TEXT[] NOT NULL DEFAULT '{}',  -- ["Pedrão", "Pedro Silva"]
  origens         TEXT[] NOT NULL DEFAULT '{}',  -- ["google", "whatsapp_grupo_uso", "manual", ...]
  oculto          BOOLEAN NOT NULL DEFAULT FALSE,-- peneira reversível (não-delete)
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,  -- campos extra por origem
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS contacts_telefone_uq
  ON contacts(telefone_e164) WHERE telefone_e164 IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS contacts_jid_uq
  ON contacts(whatsapp_jid) WHERE whatsapp_jid IS NOT NULL;
CREATE INDEX IF NOT EXISTS contacts_nome_trgm
  ON contacts USING gin (nome_canonico gin_trgm_ops);
CREATE INDEX IF NOT EXISTS contacts_aliases_gin
  ON contacts USING gin (aliases);
CREATE INDEX IF NOT EXISTS contacts_tipo_oculto
  ON contacts(tipo, oculto);

-- Tabela: whatsapp_messages (histórico bruto do canal WhatsApp via Evolution)
-- direcao='out' = Apolo enviou; direcao='in' = webhook recebeu.
-- jid_chat sempre identifica o chat (pessoa OU grupo); jid_remetente difere em grupos.
CREATE TABLE IF NOT EXISTS whatsapp_messages (
  id              TEXT PRIMARY KEY,              -- message_id da Evolution (dedup natural)
  jid_chat        TEXT NOT NULL,
  jid_remetente   TEXT NOT NULL,
  direcao         TEXT NOT NULL CHECK (direcao IN ('in', 'out')),
  tipo            TEXT NOT NULL,                 -- text, image, audio, document, video, sticker, ...
  conteudo        TEXT,                          -- texto livre (caption pra mídia)
  midia_path      TEXT,                          -- path local relativo a user-data/whatsapp/midia/
  timestamp       TIMESTAMPTZ NOT NULL,
  lida            BOOLEAN NOT NULL DEFAULT FALSE,
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS wa_msgs_chat_ts
  ON whatsapp_messages(jid_chat, timestamp DESC);
CREATE INDEX IF NOT EXISTS wa_msgs_nao_lidas
  ON whatsapp_messages(timestamp DESC) WHERE lida = FALSE;

-- ============================================================================
-- New Chat Manager — Fase 2 (2026-06-01)
-- Detector síncrono → daemon classificador-bibliotecário. Conversation vira
-- FAIXA derivada de mensagens. Vide migration 003 e o doc de arquitetura:
--   user-data/knowledge/kobe/brainstorms/new-chat-manager-arquitetura.md
-- Tudo aditivo; ocioso com CHAT_MANAGER_ENABLED=false.
-- ============================================================================

-- Faixa de mensagens: carimba conversation_id direto em messages.
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES conversations(id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
  ON messages(conversation_id);

-- Índice ivfflat em messages.embedding (busca vetorial da camada fria).
CREATE INDEX IF NOT EXISTS idx_messages_embedding
  ON messages USING ivfflat (embedding vector_cosine_ops);

-- Tag cloud (catálogo frio) — tag por beat fino dentro da conversation.
CREATE TABLE IF NOT EXISTS conversation_tags (
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (conversation_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_conversation_tags_tag
  ON conversation_tags(tag);
