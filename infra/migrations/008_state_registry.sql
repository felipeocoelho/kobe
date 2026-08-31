-- Migration 008 — o registro de estado (2026-08-30)
--
-- Highlander v3, F3. É a fase que cura a dor original do projeto, escrita pelo
-- operador em 27/08/2026: *"quando eu pedia para retomar o assunto, existiam
-- coisas que já tinham sido discutidas, sobre as quais decisões já haviam sido
-- tomadas, e que você retomava como questões em aberto."*
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Ela só CRIA: cinco tabelas novas, seus índices e uma função de       │
-- │   normalização. Não apaga linha, não apaga coluna, não apaga tabela, e │
-- │   NÃO ALTERA nenhuma tabela existente — em particular, não encosta em  │
-- │   `messages`. Um banco que a aplique e queira voltar atrás só precisa  │
-- │   parar de escrever (`LUCIEN_ENABLED=false`), sem tocar no banco.      │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- A CAUSA RAIZ QUE ISTO CORRIGE, EM UMA FRASE DE BANCO
-- -----------------------------------------------------
-- A memória do Kobe é um **log de INSERT sem UPDATE e sem tombstone**. Ela
-- registra todo pedido que o operador já fez, nunca registra que o pedido foi
-- respondido, e não tem como um fato novo invalidar um fato velho. **Falta o
-- `valid_from` / `valid_to`.** É uma dimensão de mudança lenta que nunca foi
-- modelada — e é exatamente isso que `lucien_claims` modela.
--
-- Medido no diagnóstico da missão: perguntando ao Hindsight de produção *"o que
-- já foi decidido sobre a arquitetura de borda?"*, ele devolveu **como "em
-- aberto" três coisas fechadas em julho**. Não porque esqueceu — porque não
-- existe, em lugar nenhum do sistema, a informação de que aquilo fechou.
--
-- ============================================================================
-- A DECISÃO DE DESENHO QUE SUSTENTA A FASE INTEIRA: ORIGEM É RESTRIÇÃO
-- ============================================================================
-- A F3 é a única fase do Highlander v3 em que **um modelo escreve estado que o
-- agente depois serve como se fosse conhecido**. O briefing declara isso como o
-- risco de verdade do projeto, e a mitigação number um é *"origem obrigatória em
-- toda linha"*.
--
-- Aqui isso NÃO é convenção nem revisão de código: `source_message_id` é
-- `NOT NULL` com chave estrangeira para `messages`. **Uma afirmação sem origem
-- real não entra porque o banco recusa a linha** — do mesmo jeito que a F1 fez
-- com `work_sessions.system_id`. Não depende de ninguém lembrar.
--
-- E há uma segunda trava, esta no código (`bot/lucien/store.py`): o `seq` citado
-- pelo modelo tem que estar **no lote que foi mostrado a ele**. O banco garante
-- que a mensagem existe; o código garante que o modelo a viu. Uma origem
-- plausível e inventada morre na segunda.
--
-- POR QUE A ORIGEM É `messages` E SÓ `messages`
-- ----------------------------------------------
-- LUCIEN, nesta fase, lê mensagens. Amarrar a origem a uma tabela real com
-- `NOT NULL` é o que transforma a promessa em mecânica. Quando um dossiê de sala
-- (F1) também precisar gerar estado, isso vira uma migration futura, com
-- `source_ref TEXT` e um `CHECK` de "pelo menos uma origem". Deixar essa
-- flexibilidade agora só serviria para enfraquecer a trava hoje, em troca de
-- nada.
--
-- NADA AQUI É ESPECÍFICO DE UM TÓPICO
-- ------------------------------------
-- Regra do operador, 30/08/2026: *"nada que a gente vai construir é específico do
-- Dev Kobe, exceto se eu falar o contrário"*. O registro é **global, com coluna
-- de tópico** (decisão E5): a injeção automática respeita a parede entre
-- tópicos; a recuperação sob demanda atravessa, sempre rotulando de onde veio.
-- Por isso `topic_id` é uma coluna, e não um banco de dados por tópico.
--
-- NADA É APAGADO — SUPERAR É DATAR, NÃO DELETAR
-- ----------------------------------------------
-- Uma afirmação superada ganha `valid_to`, muda de `status` e passa a apontar
-- para quem a substituiu. Ela continua legível, e é isso que permite responder
-- *"teve alguma decisão que a gente voltou atrás?"*. `lucien_events` guarda a
-- imagem ANTERIOR de cada mudança (`before`), que é o que faz
-- `kobe-lucien reverter` existir: o caminho de volta é um comando, não um
-- `UPDATE` à mão.
--
-- ROLLBACK
-- --------
-- `LUCIEN_ENABLED=false`: LUCIEN para de escrever e as cinco tabelas ficam
-- INERTES. Nada muda no prompt do turno (a F3 é consultada sob demanda, não
-- injetada). O `kobe-remember` volta a mostrar só EVIDÊNCIA, porque a camada de
-- ESTADO vem vazia. Sair de vez seria uma migration de remoção, que não está
-- prevista.

-- ============================================================================
-- Extensões
-- ============================================================================
-- `vector` já é criada pelo `infra/schema.sql` e usada pela 007. A linha existe
-- pelo caminho de UPGRADE, igual à `pg_trgm` da 007: um banco que aplicou um
-- `schema.sql` mais antigo chegaria aqui sem ela e quebraria na coluna de
-- embedding, com um erro sobre o tipo `vector` que não diz o que fazer.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- lucien_runs — uma rodada: o que ela viu e o que ela fez
-- ============================================================================
-- Vem primeiro porque `lucien_claims` e `lucien_events` a referenciam.
--
-- Ela é a resposta a duas perguntas que não se responde de outro jeito:
-- *"LUCIEN está rodando?"* (a marca do relógio — a lacuna L4 do briefing, a que
-- tem mais chance de virar surpresa, porque um agendador que para não produz
-- erro, produz SILÊNCIO) e *"o que ele fez semana passada?"* (o relatório, que é
-- mitigação declarada e não opcional).
--
-- `claims_rejected` é a coluna mais importante desta tabela. Toda afirmação que
-- uma trava recusou é contada aqui. **Recusa silenciosa é o mesmo defeito de
-- origem inventada, visto do outro lado**: se o modelo estiver alucinando
-- origem, isto sobe, e alguém enxerga.

CREATE TABLE IF NOT EXISTS lucien_runs (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  mode            TEXT NOT NULL,
  topic_id        UUID REFERENCES topics(id) ON DELETE SET NULL,
  from_seq        BIGINT,
  to_seq          BIGINT,
  messages_seen   INT  NOT NULL DEFAULT 0,
  claims_created  INT  NOT NULL DEFAULT 0,
  claims_superseded INT NOT NULL DEFAULT 0,
  claims_rejected INT  NOT NULL DEFAULT 0,
  model           TEXT,
  tokens_in       INT,
  tokens_out      INT,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at     TIMESTAMPTZ,
  ok              BOOLEAN,
  error           TEXT,
  CONSTRAINT lucien_runs_mode_check
    CHECK (mode IN ('incremental', 'reconstruction'))
);

CREATE INDEX IF NOT EXISTS idx_lucien_runs_recentes
  ON lucien_runs(started_at DESC);

-- ============================================================================
-- lucien_claims — o registro de estado
-- ============================================================================
-- Uma linha = uma afirmação durável, com vigência e origem.
--
-- `valid_from` É A DATA DO FATO, NÃO A DA GRAVAÇÃO. Ela recebe o `created_at` da
-- mensagem de origem. A distinção não é cosmética: a reconstrução do passado vai
-- criar, numa madrugada de agosto, afirmações cuja vigência começa em julho. Se
-- `valid_from` fosse `NOW()`, o registro inteiro nasceria dizendo que tudo foi
-- decidido no dia em que foi catalogado — que é a mesma classe de mentira que a
-- fase existe para matar.
--
-- `confidence` É PREENCHIDA PELO CÓDIGO, NUNCA PELO MODELO. Mitigação exigida
-- pelo briefing: *"fato vindo de áudio entrando com confiança menor"*. Medido no
-- acervo de dev: **816 das 3.620 mensagens (22,5%) vieram de áudio**, e o
-- Hindsight de produção tem fatos como *"os plugins do Koby ficarão em home,
-- Filipe e Kobi"* — erro de transcrição virando fato permanente. `messages`
-- tem a coluna `audio_transcribed`, então isto é dedutível e não precisa (nem
-- deve) ser perguntado a quem está extraindo.
--
-- `subject_slug` existe para deduplicar e agrupar. Sem ele, "arquitetura de
-- borda", "Arquitetura de Borda" e "arquitetura da borda" seriam três assuntos.

CREATE TABLE IF NOT EXISTS lucien_claims (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id       UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  subject        TEXT NOT NULL,
  subject_slug   TEXT NOT NULL,
  statement      TEXT NOT NULL,
  kind           TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'vigente',
  confidence     TEXT NOT NULL DEFAULT 'media',
  valid_from     TIMESTAMPTZ NOT NULL,
  valid_to       TIMESTAMPTZ,
  superseded_by  UUID REFERENCES lucien_claims(id) ON DELETE SET NULL,

  -- ORIGEM OBRIGATÓRIA. A linha inteira existe por causa deste par.
  source_message_id UUID   NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  source_seq        BIGINT NOT NULL,

  created_by     TEXT NOT NULL DEFAULT 'lucien',
  run_id         UUID REFERENCES lucien_runs(id) ON DELETE SET NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  -- A perna POR PALAVRA da busca de estado. Coluna GERADA pelo mesmo motivo da
  -- 007: o Postgres a mantém sozinho e nenhum caminho de código pode esquecer
  -- de atualizá-la.
  search_tsv     tsvector GENERATED ALWAYS AS (
                   to_tsvector('portuguese', subject || ' ' || statement)
                 ) STORED,

  -- A perna POR SENTIDO. Preenchida ATRÁS, como em `message_chunks`: exige
  -- chamada externa, e a escrita de uma afirmação não pode esperar por
  -- embedding nem falhar por causa dele. Mesmo modelo e mesma dimensão da 007
  -- (`text-embedding-3-small`, 1536d) — misturar dois espaços vetoriais no
  -- mesmo sistema não dá erro, dá resposta errada com nota plausível.
  embedding      VECTOR(1536),
  model          TEXT,
  embedded_at    TIMESTAMPTZ,

  CONSTRAINT lucien_claims_kind_check
    CHECK (kind IN ('decision', 'open', 'preference', 'fact')),
  CONSTRAINT lucien_claims_status_check
    CHECK (status IN ('vigente', 'superada', 'fechada', 'abandonada')),
  CONSTRAINT lucien_claims_confidence_check
    CHECK (confidence IN ('alta', 'media', 'baixa')),
  CONSTRAINT lucien_claims_created_by_check
    CHECK (created_by IN ('lucien', 'operador')),

  -- Vigência e data de fim são a MESMA informação, e não podem discordar. Uma
  -- linha "vigente" com `valid_to` preenchida seria simultaneamente válida e
  -- encerrada; uma "superada" sem `valid_to` não teria quando parou de valer —
  -- e é justamente o "quando" que a fase inteira existe para registrar.
  CONSTRAINT lucien_claims_vigencia_check
    CHECK ((status = 'vigente') = (valid_to IS NULL)),

  -- O ponteiro de substituição só faz sentido em quem foi substituído, e nunca
  -- aponta para si mesmo (um ciclo de um elemento seria um registro que se
  -- explica por si e não leva a lugar nenhum).
  CONSTRAINT lucien_claims_superseded_by_check
    CHECK (superseded_by IS NULL OR status = 'superada'),
  CONSTRAINT lucien_claims_nao_se_substitui
    CHECK (superseded_by IS NULL OR superseded_by <> id)
);

-- O índice que a leitura quente usa: "o que vale hoje neste tópico?". PARCIAL,
-- porque o registro cresce para sempre e a fatia vigente é a que se lê.
CREATE INDEX IF NOT EXISTS idx_lucien_claims_vigentes
  ON lucien_claims(topic_id, subject_slug)
  WHERE status = 'vigente';

CREATE INDEX IF NOT EXISTS idx_lucien_claims_tsv
  ON lucien_claims USING gin(search_tsv);

-- A perna LITERAL — `gin_trgm_ops` é o que faz `statement ILIKE '%compat_gate%'`
-- usar índice. Identificador (nome de arquivo, sigla, caminho) é destruído pelo
-- dicionário `portuguese`; foi medido na F2 e vale igual aqui.
CREATE INDEX IF NOT EXISTS idx_lucien_claims_statement_trgm
  ON lucien_claims USING gin(statement gin_trgm_ops);

-- A fila do embedder, pelo mesmo desenho da 007: índice PARCIAL sobre o que
-- falta, para que o custo seja proporcional à pendência e não ao acervo.
CREATE INDEX IF NOT EXISTS idx_lucien_claims_sem_vetor
  ON lucien_claims(id) WHERE embedding IS NULL;

CREATE INDEX IF NOT EXISTS idx_lucien_claims_origem
  ON lucien_claims(source_message_id);

CREATE INDEX IF NOT EXISTS idx_lucien_claims_seq
  ON lucien_claims(source_seq);

-- SEM índice de vizinhança (HNSW), pela mesma razão medida na 007: a exatidão é
-- o que sustenta o piso do "não tenho registro", e um vizinho perdido pela busca
-- aproximada viraria uma recusa falsa. Aqui o argumento é ainda mais forte — o
-- registro de estado é ordens de grandeza menor que o de trechos.

-- ============================================================================
-- lucien_claim_evidence — as OUTRAS mensagens que sustentam a afirmação
-- ============================================================================
-- Uma decisão raramente se estabelece numa mensagem só: ela é pedida numa,
-- argumentada noutra e fechada numa terceira. `source_message_id` guarda a que
-- ESTABELECE; esta tabela guarda as demais.
--
-- É tabela e não um `BIGINT[]` na linha de propósito: um array não tem chave
-- estrangeira, e um número de mensagem que não existe é precisamente o que não
-- pode entrar aqui.

CREATE TABLE IF NOT EXISTS lucien_claim_evidence (
  claim_id   UUID   NOT NULL REFERENCES lucien_claims(id) ON DELETE CASCADE,
  message_id UUID   NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  seq        BIGINT NOT NULL,
  PRIMARY KEY (claim_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_lucien_claim_evidence_msg
  ON lucien_claim_evidence(message_id);

-- ============================================================================
-- lucien_events — o que LUCIEN fez. É o caminho de volta.
-- ============================================================================
-- Duas funções, e as duas são exigência do briefing:
--
-- 1. **Reversibilidade.** `before` guarda a imagem da linha ANTES da mudança.
--    É o que permite desfazer uma superação errada com um comando
--    (`kobe-lucien reverter <id>`) em vez de um `UPDATE` à mão — e superação
--    errada é o modo de falha mais caro desta fase, porque ela ESCONDE uma
--    decisão que continua valendo.
-- 2. **Auditoria.** *"o relatório expondo o que ele fez"* está listado entre as
--    mitigações não-opcionais da F3. O ciclo semanal conversacional é F5; o
--    dado sobre o qual ele se monta é este.
--
-- `detail` guarda o motivo que o modelo deu e o `seq` que o motivou — para que
-- "por que esta decisão foi dada como superada?" tenha resposta em vez de
-- exigir fé.

CREATE TABLE IF NOT EXISTS lucien_events (
  id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  claim_id UUID NOT NULL REFERENCES lucien_claims(id) ON DELETE CASCADE,
  action   TEXT NOT NULL,
  before   JSONB,
  detail   JSONB,
  actor    TEXT NOT NULL DEFAULT 'lucien',
  run_id   UUID REFERENCES lucien_runs(id) ON DELETE SET NULL,
  at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT lucien_events_action_check
    CHECK (action IN ('created', 'superseded', 'closed', 'abandoned', 'reverted')),
  CONSTRAINT lucien_events_actor_check
    CHECK (actor IN ('lucien', 'operador'))
);

CREATE INDEX IF NOT EXISTS idx_lucien_events_claim
  ON lucien_events(claim_id, at DESC);

CREATE INDEX IF NOT EXISTS idx_lucien_events_periodo
  ON lucien_events(at DESC);

-- ============================================================================
-- lucien_cursor — até onde já se leu
-- ============================================================================
-- Por (escopo, tópico). Dois escopos: `incremental`, que caminha com a conversa,
-- e `reconstruction`, que varre o passado — separados porque a reconstrução é
-- retomável e não pode atropelar nem ser atropelada pela leitura corrente.
--
-- **O cursor NÃO avança quando a rodada falha.** É o que torna uma falha de
-- modelo, de rede ou de validação inofensiva: o mesmo lote é lido de novo na
-- próxima passada, em vez de ficar um buraco permanente no registro.

CREATE TABLE IF NOT EXISTS lucien_cursor (
  scope      TEXT   NOT NULL,
  topic_id   UUID   NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  last_seq   BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (scope, topic_id),
  CONSTRAINT lucien_cursor_scope_check
    CHECK (scope IN ('incremental', 'reconstruction'))
);
