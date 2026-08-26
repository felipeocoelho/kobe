-- Migration 005 — o Chat Manager sai do banco (2026-08-25)
--
-- O Chat Manager agrupava sessions por assunto: v1 era um detector síncrono no
-- caminho do turno, v2 um classificador-bibliotecário rodando atrás, no daemon
-- Keyko. Ficou com a flag `CHAT_MANAGER_ENABLED=false` e não voltou a ser
-- ligado. Decisão do operador em 25/08/2026, na palavra dele: "vamos aposentar
-- o Chat Manager… não quero um Frankenstein". O código já saiu do repositório;
-- isto aqui remove o que ficou no banco.
--
-- ⚠️ ESTA MIGRATION É DESTRUTIVA E IRREVERSÍVEL. Ela APAGA DADO — inclusive
--    dado que NÃO é do Chat Manager (vide a nota sobre `messages` abaixo).
--    Leia os dois pré-requisitos antes de colar no editor.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ PRÉ-REQUISITO 1 — DUMP CONFERIDO.                                      │
-- │   Antes de rodar, o dump das três fontes tem que existir E ter sido     │
-- │   CONFERIDO contra a contagem do banco (não basta o arquivo existir):   │
-- │                                                                        │
-- │     conversations         — esperado ~82 linhas                        │
-- │     conversation_tags     — esperado ~90 linhas                        │
-- │     messages(id, conversation_id, embedding)                           │
-- │                           — esperado ~726 linhas com embedding não-nulo │
-- │                                                                        │
-- │   Medições de 25/08/2026, via API de management do Supabase.           │
-- │   O dump vive em `user-data/backups/chat-manager-<data>/` (fora do git).│
-- │                                                                        │
-- │ PRÉ-REQUISITO 2 — CÓDIGO NOVO JÁ NO AR.                                │
-- │   O bot em produção precisa estar rodando a versão SEM Chat Manager,    │
-- │   senão ele recria linha em `conversations` depois do drop e o banco    │
-- │   volta a divergir do código. Confira que o `kobe.service` da produção  │
-- │   já subiu com o código que removeu o pacote `bot/chat_manager/`.       │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- Banco compartilhado dev/prod: rodar isto atinge a produção na hora.
--
-- Como aplicar: colar no SQL Editor do Supabase (keys REST não rodam DDL).
--
-- Conferência ANTES (guarde os números — são o que o dump tem que bater):
--   SELECT (SELECT COUNT(*) FROM conversations)                        AS conversations,
--          (SELECT COUNT(*) FROM conversation_tags)                    AS tags,
--          (SELECT COUNT(*) FROM messages WHERE embedding IS NOT NULL) AS msgs_com_vetor,
--          (SELECT COUNT(*) FROM sessions WHERE conversation_id IS NOT NULL) AS sessions_ligadas;
--
-- Conferência DEPOIS (esperado: 0 em todas as três linhas):
--   SELECT COUNT(*) FROM information_schema.tables
--    WHERE table_schema='public' AND table_name IN ('conversations','conversation_tags');
--   SELECT COUNT(*) FROM information_schema.columns
--    WHERE table_schema='public' AND column_name='conversation_id';
--   SELECT COUNT(*) FROM information_schema.columns
--    WHERE table_schema='public' AND table_name='messages' AND column_name='embedding';

-- ── 1. Colunas de ligação nas tabelas que FICAM ──────────────────────────
-- Saem antes das tabelas por causa das FKs. `sessions` e `messages` não são
-- tocadas em mais nada: perdem só a coluna de vínculo.

DROP INDEX IF EXISTS idx_sessions_conversation;
ALTER TABLE sessions DROP COLUMN IF EXISTS conversation_id;

DROP INDEX IF EXISTS idx_messages_conversation;
ALTER TABLE messages DROP COLUMN IF EXISTS conversation_id;

-- ── 2. messages.embedding ────────────────────────────────────────────────
-- ATENÇÃO, é aqui que se apaga o dado que dói: ~726 vetores. Só o Chat Manager
-- escrevia e lia esta coluna (o helper `kobe-recall`, que fazia a busca
-- vetorial em cima dela, foi aposentado junto). Sem ele, ela é peso morto
-- carregando um índice ivfflat. Confira o PRÉ-REQUISITO 1 antes desta linha.

DROP INDEX IF EXISTS idx_messages_embedding;
ALTER TABLE messages DROP COLUMN IF EXISTS embedding;

-- ── 3. As tabelas do Chat Manager ────────────────────────────────────────
-- `conversation_tags` primeiro: tem FK pra `conversations`.

DROP INDEX IF EXISTS idx_conversation_tags_tag;
DROP TABLE IF EXISTS conversation_tags;

DROP INDEX IF EXISTS idx_conversations_topic_status;
DROP INDEX IF EXISTS idx_conversations_embedding;
DROP TABLE IF EXISTS conversations;

-- ── O que NÃO entra aqui, e por quê ──────────────────────────────────────
--
-- `saved_artifacts.embedding` FICA. Ela tem 0 linhas não-nulas hoje e é
-- tentador levar junto, mas é gancho declarado do `/salvar`/`/retomar` — dois
-- comandos VIVOS, que não são do Chat Manager. Mexer nela é outra decisão, com
-- outra conversa. Não a inclua aqui por associação.
--
-- A UNIQUE composta `topics_chat_thread_unique` FICA. Ela entrou junto com o
-- Chat Manager na migration 001, mas não é dele: é o que separa o chat privado
-- do "Geral" do supergrupo, e é o que vai permitir um segundo ambiente conviver
-- no mesmo banco sem colidir.
--
-- `sessions` e `messages` FICAM inteiras. A memória de trabalho do agente lê
-- `messages` por `topic_id` e nunca dependeu de conversation nenhuma.
