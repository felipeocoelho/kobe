-- Migration 003 — New Chat Manager (2026-06-01)
--
-- Redesenho do Chat Manager: detector síncrono sai do caminho crítico e
-- vira daemon classificador-bibliotecário (Keyko). A conversation deixa de
-- ser container-porteira e vira FAIXA derivada de mensagens. Vide doc:
--   /home/felipe/kobe/user-data/knowledge/kobe/brainstorms/new-chat-manager-arquitetura.md
--
-- TODA aditiva (novas colunas/tabelas/índices) — NÃO destrutiva. Banco é
-- compartilhado dev/prod; com CHAT_MANAGER_ENABLED=false essas estruturas
-- ficam ociosas (rollback trivial). Segura pra re-execução (IF NOT EXISTS).
--
-- Como aplicar: colar no SQL Editor do Supabase (keys REST não rodam DDL).

-- 1. Faixa de mensagens na conversation: carimba conversation_id direto em
--    messages (caminho recomendado no doc §5.1). Torna a faixa trivial de
--    consultar (WHERE conversation_id = ... ORDER BY created_at). NULL =
--    mensagem ainda não classificada pelo daemon (watermark natural).
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS conversation_id UUID REFERENCES conversations(id);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
  ON messages(conversation_id);

-- 2. Índice ivfflat em messages.embedding — pra busca vetorial da camada
--    FRIA escalar (doc §5.2). Antes só existia o índice no centroide das
--    conversations; o frio precisa buscar mensagem individual por vetor.
CREATE INDEX IF NOT EXISTS idx_messages_embedding
  ON messages USING ivfflat (embedding vector_cosine_ops);

-- 3. Tag cloud (catálogo frio, doc §5.3). Tag por beat fino dentro de uma
--    conversation (assunto grosso). weight = relevância acumulada. FK com
--    cascade: apagar conversation limpa as tags.
CREATE TABLE IF NOT EXISTS conversation_tags (
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (conversation_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_conversation_tags_tag
  ON conversation_tags(tag);

-- Arestas de relação (doc §5.4): decisão = calcular on-the-fly por
-- similaridade de centroide (já há índice ivfflat em conversations.
-- centroid_embedding). Sem tabela dedicada nesta fase — se a calibração
-- mostrar que o cálculo lazy é caro, criar conversation_links depois.
