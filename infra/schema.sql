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
-- Identidade de tópico (2026-05-27)
--
-- Este bloco nasceu com o Chat Manager, mas o que sobrou dele NÃO é Chat
-- Manager: é a identidade de tópico, que o Kobe usa sempre.
--
-- 1. UNIQUE composta em topics (chat_id, thread_id) — separa chat privado
--    do "Geral" do supergrupo (ambos teriam thread_id=0 antes, colidiam).
--    É também o que faz um supergrupo separado (ex.: um ambiente de dev)
--    gerar linhas próprias, sem colidir com as da produção.
-- 2. Renomear current_name do topic privado existente de 'Geral' → 'Private'
--    pra alinhar com o slug 'private' do `get_topic_slug`.
--
-- O resto do bloco (tabela `conversations`, `sessions.conversation_id`,
-- `messages.embedding`) saiu na aposentadoria do Chat Manager — vide a nota
-- deliberada mais abaixo e `infra/migrations/005_remove_chat_manager.sql`.
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

-- 2. Renomear topic privado existente: 'Geral' → 'Private'
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
-- 1. contacts — catálogo unificado (pessoa OU grupo WhatsApp).
--               Reaproveitável por outros canais (email, ClickUp etc.).
--               É catálogo de DESTINATÁRIO, não histórico de conversa.
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

-- NÃO existe tabela de mensagem de WhatsApp aqui — e é de propósito (2026-08-24).
-- O Kobe não guarda conteúdo de WhatsApp: a fonte única é o banco da própria
-- Evolution API, que já registra o que entra e o que sai. A tabela
-- `whatsapp_messages` que morava neste bloco foi removida na v0.3.0 do plugin
-- apolo; ela era herança do backend WPPConnect, que não tinha banco nenhum.
--
-- Consequência desejada: uma instalação do Kobe **sem** Evolution não cria
-- tabela de WhatsApp nenhuma. O plugin apolo é opcional de verdade.
--
-- Pra remover a tabela de uma instalação que já a tem, vide
-- `infra/migrations/004_remove_whatsapp_messages.sql` — e leia o pré-requisito
-- de backup antes.

-- ============================================================================
-- NÃO existe estrutura de Chat Manager aqui — e é de propósito (2026-08-25).
--
-- O Chat Manager (v1 detector síncrono, v2 classificador-bibliotecário) agrupava
-- sessions por assunto. Foi aposentado; palavra do operador: "vamos aposentar o
-- Chat Manager… não quero um Frankenstein". Saíram deste arquivo:
--
--   - tabela `conversations` (+ índice de status e índice ivfflat do centroide)
--   - tabela `conversation_tags` (+ índice de tag)
--   - coluna `sessions.conversation_id` (+ índice)
--   - coluna `messages.conversation_id` (+ índice)
--   - coluna `messages.embedding` (+ índice ivfflat)
--
-- Consequência desejada: uma instalação nova do Kobe não cria nada disso. A
-- memória de trabalho (janela imediata) NÃO dependia deste bloco — ela lê
-- `messages` por `topic_id` e segue funcionando inteira.
--
-- `saved_artifacts.embedding` FICA. Apesar de hoje ter 0 linhas não-nulas, é
-- gancho declarado do `/salvar`/`/retomar`, que continuam vivos — mexer nela é
-- outra conversa, não esta.
--
-- Pra remover as estruturas de uma instalação que já as tem, vide
-- `infra/migrations/005_remove_chat_manager.sql` — e leia os dois
-- pré-requisitos antes. Ela APAGA DADO.
-- ============================================================================
