-- Migration 007 — o índice de busca sobre a conversa (2026-08-30)
--
-- Highlander v3, F2. É o que faz `kobe-remember` existir: um índice sobre as
-- FALAS LITERAIS de `messages`, para que uma pergunta sobre o passado deixe de
-- ser respondida de memória e passe a ser respondida COM CITAÇÃO — trecho, data
-- e número da mensagem.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Só ACRESCENTA: duas colunas em `messages` (ambas no FIM), três       │
-- │   índices, duas tabelas novas e uma sequência. Não apaga linha, não    │
-- │   apaga coluna, não apaga tabela, e não muda o tipo nem o default de   │
-- │   nada que já existia. O único UPDATE que ela roda preenche uma coluna │
-- │   NOVA, e só quando ela está inteiramente vazia.                       │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- AS TRÊS PERNAS DE BUSCA, E POR QUE SÃO TRÊS
-- --------------------------------------------
-- Três tipos de pergunta pedem estruturas diferentes, e isso foi MEDIDO numa
-- bancada com o acervo real (3.558 mensagens, 7.706 trechos) antes de virar
-- código:
--
--   1. IDENTIFICADOR EXATO (`compat_gate`, `working_set.py`, `HINDSIGHT_RECALL`).
--      O dicionário `portuguese` faz stemming, e stemming em nome de arquivo é
--      destruição: `kobe-recall-since` vira `kobe-recall-sinc` + `recall` +
--      `sinc`, e aí `sinc` casa com "sincronizar". Medido: a busca por palavra
--      devolveu resultados sobre imagem no WhatsApp. Quem resolve é substring
--      literal sobre o índice trigrama — 2 a 11 ms, com índice.
--   2. PALAVRA com flexão. É o `search_tsv` com o dicionário `portuguese`.
--   3. PARÁFRASE, sem nenhuma palavra em comum com o registro. Só vetor resolve.
--      Medido: a pergunta "como impedir que a produção rodasse uma versão
--      diferente da que o git dizia" achou as mensagens certas de 12/06 sem
--      compartilhar um termo com elas.
--
-- POR QUE O TSVECTOR É COLUNA GERADA, E O VETOR NÃO
-- --------------------------------------------------
-- `search_tsv` é `GENERATED ALWAYS AS ... STORED`: o Postgres a mantém sozinho, e
-- NENHUM caminho de código pode esquecer de atualizá-la. Custa microssegundos
-- dentro do banco, no próprio INSERT.
--
-- O vetor não pode ser assim — ele exige uma chamada externa. Então ele mora em
-- `message_chunks`, preenchido ATRÁS, por um indexador dirigido por relógio
-- (decisão E3 do briefing: nada tem "fechar a sala" como gatilho; tudo é
-- contínuo). Consequência desejada: **a gravação de uma mensagem nunca espera
-- por embedding**. Uma mensagem fica buscável por palavra na hora, e por sentido
-- em até ~1 minuto.
--
-- POR QUE O ÍNDICE É POR TRECHO E NÃO POR MENSAGEM
-- -------------------------------------------------
-- Medido no acervo: 30% das mensagens passam de 1.500 caracteres e o p99 é
-- 6.322. Modelo de embedding corta a entrada e DESCARTA O RESTO EM SILÊNCIO —
-- a metade de baixo de 1 em cada 3 mensagens ficaria fora do índice sem ninguém
-- perceber. A citação continua sendo da MENSAGEM; o trecho é só o que se mostra.
--
-- POR QUE `seq` EXISTE
-- ---------------------
-- A chave de `messages` é UUID, que não serve pro operador conferir nada.
-- "Citar com data e número da mensagem" (texto do briefing) exige um número
-- legível. `seq` é preenchida em ordem cronológica no histórico e segue por
-- sequência daí pra frente.
--
-- ROLLBACK
-- --------
-- `SEARCH_INDEX_ENABLED=false` para o indexador: `message_chunks` fica INERTE e
-- nada mais é escrito. As colunas e índices são aditivos e continuam corretos
-- sozinhos (a gerada é do banco). Sair de vez seria uma migration de remoção,
-- que não está prevista.

-- ============================================================================
-- Extensões
-- ============================================================================
-- `pg_trgm` já é criado pelo `infra/schema.sql` (ele serve a busca fuzzy de
-- `contacts` desde antes). A linha aqui existe pelo caminho de UPGRADE: um banco
-- que aplicou um `schema.sql` mais antigo, de quando a extensão ainda não estava
-- lá, chegaria nesta migration sem ela e quebraria no índice trigrama abaixo —
-- com um erro sobre `gin_trgm_ops` que não diz o que fazer.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- messages.seq — o número que se cita
-- ============================================================================

ALTER TABLE messages ADD COLUMN IF NOT EXISTS seq BIGINT;

-- O backfill roda uma vez só, e a guarda é o que o torna idempotente: ele exige
-- que a coluna esteja INTEIRAMENTE vazia. Numerar por `row_number()` sobre uma
-- tabela parcialmente numerada geraria colisão — e um UNIQUE que estoura no meio
-- de uma migration é o pior lugar pra descobrir isso.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM messages WHERE seq IS NOT NULL) THEN
    WITH ordenado AS (
      SELECT id, row_number() OVER (ORDER BY created_at, id) AS n FROM messages
    )
    UPDATE messages m SET seq = o.n FROM ordenado o WHERE m.id = o.id;
  END IF;
END $$;

CREATE SEQUENCE IF NOT EXISTS messages_seq_seq AS BIGINT;

-- `is_called = false` faz o próximo `nextval` devolver EXATAMENTE este valor.
-- Com a tabela vazia isso dá 1; com 3.558 linhas, 3.559. Com `true` a tabela
-- vazia começaria em 2, e o número 1 nunca existiria.
SELECT setval('messages_seq_seq', COALESCE((SELECT max(seq) FROM messages), 0) + 1, false);

ALTER TABLE messages ALTER COLUMN seq SET DEFAULT nextval('messages_seq_seq');
ALTER SEQUENCE messages_seq_seq OWNED BY messages.seq;
ALTER TABLE messages ALTER COLUMN seq SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_seq ON messages(seq);

-- ============================================================================
-- messages.search_tsv — a perna POR PALAVRA, e a perna LITERAL
-- ============================================================================

ALTER TABLE messages ADD COLUMN IF NOT EXISTS search_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('portuguese', content)) STORED;

CREATE INDEX IF NOT EXISTS idx_messages_search_tsv
  ON messages USING gin(search_tsv);

-- A perna LITERAL. `gin_trgm_ops` é o que faz `content ILIKE '%compat_gate%'`
-- usar índice em vez de varrer a tabela.
CREATE INDEX IF NOT EXISTS idx_messages_content_trgm
  ON messages USING gin(content gin_trgm_ops);

-- ============================================================================
-- message_chunks — a perna POR SENTIDO
-- ============================================================================
-- VECTOR(1536) = `text-embedding-3-small` da OpenAI, decisão do operador em
-- 30/08/2026 sobre número medido: é o único dos dois modelos comparados em que
-- as perguntas COM resposta e as SEM resposta ocupam faixas de similaridade
-- SEPARADAS (folga +0,061 contra -0,025 do modelo local) — e sem essa separação
-- não existe o piso que faz o sistema dizer "não tenho registro disso" em vez de
-- inventar. Trocar de modelo depois custa reindexar o que já foi indexado.

CREATE TABLE IF NOT EXISTS message_chunks (
  id          BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  idx         INT NOT NULL,
  body        TEXT NOT NULL,
  embedding   VECTOR(1536),
  model       TEXT,
  embedded_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (message_id, idx)
);

-- Índice PARCIAL: a pergunta que o indexador faz a cada tick é "o que ainda não
-- tem vetor?". Sobre a tabela inteira isso vira varredura crescente; sobre o
-- índice parcial, o custo é proporcional ao que falta — que no regime normal é
-- quase nada.
CREATE INDEX IF NOT EXISTS idx_message_chunks_pendentes
  ON message_chunks(id) WHERE embedding IS NULL;

CREATE INDEX IF NOT EXISTS idx_message_chunks_message
  ON message_chunks(message_id);

-- SEM índice de vizinhança (HNSW), de propósito e com número: no acervo de hoje
-- a varredura EXATA leva 67 ms e o HNSW, 2,8 ms — e os dois devolvem o mesmo
-- topo. Não vale trocar exatidão por 64 ms quando é justamente a exatidão que
-- sustenta o piso do "não tenho registro": um vizinho perdido pela busca
-- aproximada viraria uma recusa falsa. Vira uma linha de SQL no dia em que o
-- acervo passar de ~50 mil trechos (a construção custou 6,2 s).

-- ============================================================================
-- search_lexeme_df — a estatística que separa termo banal de termo raro
-- ============================================================================
-- Sem ela, a busca por palavra NÃO distingue "achei" de "não achei". Medido: a
-- pergunta "o que a gente decidiu sobre integração com o Salesforce?" — assunto
-- que nunca existiu — devolvia 30 resultados com nota equivalente à de uma
-- pergunta legítima, porque "decidiu", "a gente" e "sobre" estão em todo lugar.
-- `ts_rank` é uma nota LOCAL: mede o casamento dentro do documento e não sabe
-- que o termo é banal no acervo inteiro. É o equivalente a criar índice numa
-- coluna com dois valores distintos.
--
-- Esta tabela é o que falta pra saber a seletividade de cada radical. É
-- recalculada pelo indexador (`ts_stat` sobre o acervo levou 80 ms na bancada).

CREATE TABLE IF NOT EXISTS search_lexeme_df (
  word         TEXT PRIMARY KEY,
  ndoc         INT NOT NULL,
  refreshed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
