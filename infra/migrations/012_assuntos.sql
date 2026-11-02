-- Migration 012 — o assunto como unidade (2026-10-13)
--
-- Highlander v3, F7.1. É a última frente da promessa original do operador, escrita
-- em 27/08/2026: *"quando a gente RETOME ALGUM ASSUNTO, você retome aquele assunto
-- PLENAMENTE CIENTE DE TUDO O QUE FOI CONVERSADO"*. A palavra que ele usou quatro
-- vezes foi ASSUNTO — e até esta migration o sistema não tinha assunto nenhum.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Ela CRIA duas tabelas, ACRESCENTA uma coluna NULA em `lucien_claims` │
-- │   e três contadores em `lucien_runs` (com default zero), e ALARGA uma  │
-- │   restrição (o modo da rodada passa a aceitar um valor a mais). Não    │
-- │   apaga linha, coluna nem tabela, não muda tipo nem default de nada    │
-- │   que já existia, não toca nas travas de origem da 008, e não altera   │
-- │   nenhuma linha existente. Reaplicá-la não faz nada de novo.           │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- O DEFEITO QUE ISTO CORRIGE, MEDIDO
-- -----------------------------------
-- `lucien_claims` nasceu com `subject` e `subject_slug`, indexados. Em 13/10/2026:
-- **1.166 afirmações e 1.163 assuntos distintos.** O campo existia e não agrupava
-- nada: era um rótulo que o modelo reescrevia a cada linha. Não faltava coluna —
-- faltava uma LISTA de assuntos que as afirmações reusassem.
--
-- Por isso o assunto aqui é LINHA EM TABELA PRÓPRIA, e não mais um campo de
-- texto: só assim ele tem data de abertura, último toque e status, e só assim
-- "retoma o assunto tal" tem uma chave de verdade para onde as afirmações
-- apontem. Os rótulos antigos (`subject`) **não são tocados** — continuam sendo o
-- rótulo curto de cada afirmação. O assunto é uma coluna ao lado.
--
-- A RÉGUA DE TAMANHO É DO OPERADOR
-- ---------------------------------
-- 07/10/2026 (#4029): assunto é o que, num aplicativo de chat, seria uma THREAD.
-- *"desenvolvimento da V3 do Highlander"* é bom; *"Manutenção do Kobe"* é lixo;
-- a referência de tamanho são os arquivos `.md` com data da base de
-- conhecimento. E: *"não é tudo que vai precisar ter um assunto"* — a pergunta
-- pontual não ganha assunto. Por isso `lucien_claims.subject_id` é NULO por
-- desenho, e não só durante a transição.
--
-- Nada disto é verificável pelo banco — tamanho de assunto é julgamento. O que
-- o banco garante é a parte mecânica: um slug por assunto, fusão que aponta para
-- quem absorveu, histórico de toda mudança.
--
-- GLOBAL E PLANO — DECISÃO DO OPERADOR EM 13/10/2026
-- ---------------------------------------------------
-- **Uma lista única para a casa inteira, sem hierarquia.** Um bug do Coder
-- conversado no Dev Kobe e no privado é UM assunto. A parede entre tópicos
-- continua onde sempre esteve — na coluna `topic_id` de cada afirmação, que é
-- o que a injeção automática respeita. `home_topic_id` só registra onde o
-- assunto nasceu; não restringe nada.
--
-- Sem hierarquia porque árvore é onde vocabulário controlado vira lixo: "Kobe >
-- Memória > Highlander" convida a pendurar tudo em "Kobe". Quando uma pergunta
-- for larga ("retoma a V3 do Highlander"), quem junta os vários assuntos é a
-- BUSCA sobre os títulos, não um nó-pai.
--
-- `system_id` / `subsystem_id` ligam, quando couber, ao catálogo de
-- desenvolvimento da 006 — para FILTRAR ("os assuntos do Coder"), nunca para
-- agrupar. A mesma chave composta da 006 impede o par impossível
-- (sistema Flow com subsistema Coder).
--
-- FUNDIR É APONTAR, NÃO APAGAR
-- -----------------------------
-- Um assunto fundido continua existindo, com `status = 'fundido'` e
-- `merged_into` apontando para quem o absorveu. As afirmações passam para o
-- assunto que ficou, e o evento guarda a lista delas — é o que faz a fusão ter
-- caminho de volta sem `UPDATE` à mão. Mesma disciplina da 008: superar é datar,
-- não deletar.
--
-- ROLLBACK
-- --------
-- `LUCIEN_ASSUNTOS_ENABLED=false` faz o curador parar de ler e escrever
-- assunto: o prompt dele volta a ser, byte a byte, o de antes desta migration.
-- Para desfazer também o agrupamento já gravado:
--     UPDATE lucien_claims SET subject_id = NULL;
-- — que é exatamente o estado em que esta migration deixa o banco. As duas
-- tabelas novas ficam inertes. Sair de vez seria uma migration de remoção, que
-- não está prevista.

-- ============================================================================
-- lucien_subjects — a lista de assuntos
-- ============================================================================
--
-- `opened_at` é a data do PRIMEIRO FATO do assunto, não a da gravação — pelo
-- mesmo motivo do `valid_from` da 008: o agrupamento retroativo vai criar, em
-- outubro, assuntos que começaram em maio. `last_touched_at` é a do fato mais
-- recente. As duas saem do código, das datas das afirmações, nunca do modelo.
--
-- `search_tsv` e `embedding` existem para resolver "retoma o assunto X" a
-- partir de uma frase: por palavra, por literal (trigrama sobre o título) e por
-- sentido. Mesmo modelo e mesma dimensão da 007/008 (`text-embedding-3-small`,
-- 1536d) — misturar espaços vetoriais não dá erro, dá resposta errada com nota
-- plausível. E SEM índice de vizinhança aproximada, pela mesma razão da 008: a
-- lista é pequena, e um vizinho perdido viraria um "não tenho registro" falso.

CREATE TABLE IF NOT EXISTS lucien_subjects (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  title           TEXT NOT NULL,
  slug            TEXT NOT NULL,
  description     TEXT,
  status          TEXT NOT NULL DEFAULT 'ativo',
  merged_into     UUID REFERENCES lucien_subjects(id) ON DELETE SET NULL,
  home_topic_id   UUID REFERENCES topics(id) ON DELETE SET NULL,
  system_id       UUID REFERENCES work_systems(id) ON DELETE SET NULL,
  subsystem_id    UUID,
  opened_at       TIMESTAMPTZ NOT NULL,
  last_touched_at TIMESTAMPTZ NOT NULL,
  created_by      TEXT NOT NULL DEFAULT 'lucien',
  run_id          UUID REFERENCES lucien_runs(id) ON DELETE SET NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  search_tsv      tsvector GENERATED ALWAYS AS (
                    to_tsvector('portuguese', title || ' ' || COALESCE(description, ''))
                  ) STORED,
  embedding       VECTOR(1536),
  model           TEXT,
  embedded_at     TIMESTAMPTZ,

  -- Um slug, um assunto. É a trava mecânica contra "Arquitetura de Borda" e
  -- "arquitetura de borda" virarem dois assuntos — o defeito de origem.
  CONSTRAINT lucien_subjects_slug_unique UNIQUE (slug),
  CONSTRAINT lucien_subjects_status_check
    CHECK (status IN ('ativo', 'fundido', 'arquivado')),
  CONSTRAINT lucien_subjects_created_by_check
    CHECK (created_by IN ('lucien', 'operador', 'agrupamento')),
  -- Fundido e "para onde foi" são a mesma informação e não podem discordar.
  CONSTRAINT lucien_subjects_fusao_check
    CHECK ((status = 'fundido') = (merged_into IS NOT NULL)),
  CONSTRAINT lucien_subjects_nao_se_funde
    CHECK (merged_into IS NULL OR merged_into <> id),
  CONSTRAINT lucien_subjects_datas_check
    CHECK (last_touched_at >= opened_at),
  CONSTRAINT lucien_subjects_titulo_check
    CHECK (char_length(title) BETWEEN 3 AND 120),
  -- A guarda do PAR, como na 006: o subsistema tem que pertencer ao sistema.
  CONSTRAINT lucien_subjects_subsystem_belongs_to_system
    FOREIGN KEY (subsystem_id, system_id)
    REFERENCES work_subsystems (id, system_id)
);

CREATE INDEX IF NOT EXISTS idx_lucien_subjects_ativos
  ON lucien_subjects(last_touched_at DESC) WHERE status = 'ativo';
CREATE INDEX IF NOT EXISTS idx_lucien_subjects_tsv
  ON lucien_subjects USING gin(search_tsv);
CREATE INDEX IF NOT EXISTS idx_lucien_subjects_title_trgm
  ON lucien_subjects USING gin(title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_lucien_subjects_sem_vetor
  ON lucien_subjects(id) WHERE embedding IS NULL;

-- ============================================================================
-- lucien_subject_events — o histórico de cada assunto. É o caminho de volta.
-- ============================================================================
-- Mesmo papel de `lucien_events` na 008: `before` guarda a imagem anterior, e
-- `detail` guarda o que mudou — numa fusão, a lista das afirmações que trocaram
-- de assunto; numa atribuição em lote, quais afirmações entraram. Desfazer é
-- ler isto, não adivinhar.

CREATE TABLE IF NOT EXISTS lucien_subject_events (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  subject_id UUID NOT NULL REFERENCES lucien_subjects(id) ON DELETE CASCADE,
  action     TEXT NOT NULL,
  before     JSONB,
  detail     JSONB,
  actor      TEXT NOT NULL DEFAULT 'lucien',
  run_id     UUID REFERENCES lucien_runs(id) ON DELETE SET NULL,
  at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT lucien_subject_events_action_check
    CHECK (action IN ('criado', 'atribuido', 'fundido', 'renomeado',
                      'arquivado', 'reativado', 'desfeito')),
  CONSTRAINT lucien_subject_events_actor_check
    CHECK (actor IN ('lucien', 'operador', 'agrupamento'))
);

CREATE INDEX IF NOT EXISTS idx_lucien_subject_events_subject
  ON lucien_subject_events(subject_id, at DESC);
CREATE INDEX IF NOT EXISTS idx_lucien_subject_events_run
  ON lucien_subject_events(run_id);

-- ============================================================================
-- lucien_claims.subject_id — a afirmação aponta para o assunto
-- ============================================================================
-- NULA por desenho (ver a régua do operador, acima). `ON DELETE SET NULL` e não
-- `CASCADE`: assunto nunca é apagado pelo código, mas se um dia for, a afirmação
-- perde o agrupamento, nunca a existência.

ALTER TABLE lucien_claims
  ADD COLUMN IF NOT EXISTS subject_id UUID REFERENCES lucien_subjects(id) ON DELETE SET NULL;

-- A leitura quente do dossiê: "o que vale hoje neste assunto?".
CREATE INDEX IF NOT EXISTS idx_lucien_claims_assunto
  ON lucien_claims(subject_id, valid_from DESC);

-- ============================================================================
-- lucien_runs — o modo `assuntos` e os três contadores
-- ============================================================================
-- O agrupamento retroativo é uma rodada como as outras: tem início, fim, modelo
-- e resultado — e é pelo `run_id` dela que ele se desfaz inteiro.
--
-- Os contadores existem pelo mesmo motivo do `claims_rejected` da 008: a trava
-- que impede o curador de criar um assunto por linha (T11) só é auditável se o
-- número de vezes que ela disparou estiver à vista. Se `subjects_avoided` subir
-- muito, o modelo está ignorando a lista; se `subjects_created` passar de
-- `subjects_reused`, o vocabulário está fragmentando de novo.

ALTER TABLE lucien_runs ADD COLUMN IF NOT EXISTS subjects_created INT NOT NULL DEFAULT 0;
ALTER TABLE lucien_runs ADD COLUMN IF NOT EXISTS subjects_reused  INT NOT NULL DEFAULT 0;
ALTER TABLE lucien_runs ADD COLUMN IF NOT EXISTS subjects_avoided INT NOT NULL DEFAULT 0;

ALTER TABLE lucien_runs
  DROP CONSTRAINT IF EXISTS lucien_runs_mode_check;
ALTER TABLE lucien_runs
  ADD CONSTRAINT lucien_runs_mode_check
  CHECK (mode IN ('incremental', 'reconstruction', 'assuntos'));
