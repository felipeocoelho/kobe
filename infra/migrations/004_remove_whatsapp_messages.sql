-- Migration 004 — a tabela whatsapp_messages sai do Kobe (2026-08-24)
--
-- O Kobe para de guardar conteúdo de WhatsApp. A fonte única passa a ser o
-- banco da própria Evolution API, que já registrava tudo — inclusive o que o
-- Kobe envia. A cópia local era herança do backend WPPConnect (que não tinha
-- banco nenhum) e virou redundância depois da migração pra Evolution em
-- 30/05/2026. Vide o CHANGELOG do Kobe e o do plugin apolo (v0.3.0).
--
-- ⚠️ ESTA MIGRATION É DESTRUTIVA E IRREVERSÍVEL. Diferente das anteriores, ela
--    APAGA DADO. Leia os dois pré-requisitos abaixo antes de colar no editor.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ PRÉ-REQUISITO 1 — BACKUP CONFERIDO.                                    │
-- │   Rode antes:  .venv/bin/python infra/decommission_whatsapp_acervo.py  │
-- │   Ele faz o dump da tabela, CONFERE a contagem de linhas do arquivo    │
-- │   contra o banco, e move a mídia. Só depois disso rode este SQL.       │
-- │                                                                        │
-- │ PRÉ-REQUISITO 2 — CÓDIGO JÁ NO AR.                                     │
-- │   O plugin apolo v0.3.0 (que parou de escrever aqui) precisa estar     │
-- │   deployado, senão o webhook recria linha depois do drop.              │
-- │   Confira: o apolo-webhook em produção já roda o código novo.          │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- Banco compartilhado dev/prod: rodar isto atinge a produção na hora.
--
-- Como aplicar: colar no SQL Editor do Supabase (keys REST não rodam DDL).
--
-- Conferência antes (esperado ~15.6 mil na medição de 24/08/2026):
--   SELECT COUNT(*) FROM whatsapp_messages;
--
-- Conferência depois (esperado 0 linhas — a tabela não existe mais):
--   SELECT COUNT(*) FROM information_schema.tables
--    WHERE table_schema = 'public' AND table_name = 'whatsapp_messages';

DROP INDEX IF EXISTS wa_msgs_chat_ts;
DROP INDEX IF EXISTS wa_msgs_nao_lidas;
DROP TABLE IF EXISTS whatsapp_messages;

-- A tabela `contacts` FICA. Ela é core do Kobe (catálogo de destinatários,
-- reaproveitável por outros canais) e é o que resolve "manda pro Pedro".
-- Não a inclua aqui por associação.
