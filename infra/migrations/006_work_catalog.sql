-- Migration 006 — o catálogo de desenvolvimento (2026-08-29)
--
-- Highlander v3, F1. As quatro tabelas do §6.2 do briefing: `work_systems`,
-- `work_subsystems`, `work_sessions` e `work_session_artifacts`. Elas registram
-- CADA SALA de trabalho — do Coder ou do Mission Control — no momento em que ela
-- NASCE, com o sistema e o subsistema DECLARADOS pelo dispatch.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Ela só CRIA. Não apaga linha, não apaga coluna, não apaga tabela, e  │
-- │   não altera nenhuma tabela existente. Um banco que a aplique e depois │
-- │   queira voltar atrás só precisa parar de escrever nas quatro tabelas  │
-- │   (a chave `WORK_CATALOG_ENABLED=false` faz isso sem tocar no banco).  │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- POR QUE O SISTEMA É DECLARADO E NÃO INFERIDO
-- --------------------------------------------
-- Regra do operador (§6.1): código do Kobe é `system=Kobe, subsystem=(nenhum)`;
-- código de PLUGIN do Kobe é `system=Kobe, subsystem=Coder|Atrus|Apolo|Monet|Flow`.
-- A pasta de trabalho NÃO decide nada — no caso do plugin ela é outra, e ainda
-- assim o sistema é o Kobe. Um desenho que derivasse o sistema do diretório
-- erraria exatamente no caso que mais interessa. Por isso `cwd` entra aqui como
-- METADADO (serve pra achar o transcript e pra saber onde a sala rodou) e nunca
-- como chave.
--
-- O caso que prova o desenho: existe um PLUGIN privado chamado `flow` E existe o
-- app web Flow. Pela pasta, os dois seriam a mesma coisa. Pela declaração, não há
-- confusão: o plugin é `Kobe / Flow`; o app é `Flow / (nenhum)`.
--
-- A INTEGRIDADE É DO BANCO, NÃO DA DISCIPLINA DE NINGUÉM
-- ------------------------------------------------------
-- `work_sessions.system_id` é NOT NULL com chave estrangeira. Não é convenção: se
-- o dispatch não trouxer um sistema válido, a linha não entra e a sala não nasce.
-- Não depende de ninguém lembrar nem de ninguém revisar.
--
-- Além do que o briefing escreveu, esta migration acrescenta UMA guarda que o
-- esquema de lá deixava passar: a CHAVE ESTRANGEIRA COMPOSTA
-- `(subsystem_id, system_id) -> work_subsystems (id, system_id)`. Sem ela, nada
-- impediria gravar `system=Flow` com `subsystem=Coder` — dois campos
-- individualmente válidos formando um par impossível. É a mesma lógica do
-- NOT NULL, aplicada ao par.
--
-- POR QUE A CHAVE DA SESSÃO É O IDENTIFICADOR DO CLAUDE CODE
-- ----------------------------------------------------------
-- `work_sessions.id` NÃO tem default: ele é FORNECIDO por quem registra, e é o
-- `session_id` que o dispatch já gera e passa ao `claude --session-id`. Verificado
-- em disco: o transcript da sala se chama `<session_id>.jsonl`. Ele já existe dos
-- dois lados (Coder e Mission Control), já nomeia o arquivo e é o que permite
-- retomar a sala. Qualquer outra chave criaria uma tabela de-para sem motivo.
--
-- SOBRE `topic_id`
-- ----------------
-- Nulo é permitido de propósito: um dispatch fora de tópico (linha de comando,
-- teste, script) não pode ser impedido de nascer por falta de tópico. O que NÃO
-- pode faltar é o sistema. Nota de campo: `topics` é único por
-- (telegram_chat_id, telegram_thread_id) — o thread_id sozinho NÃO identifica um
-- tópico (há thread_id=2 em dois chats distintos). Quem resolve o `topic_id` tem
-- que usar o PAR.

-- ============================================================================
-- work_systems — os sistemas. Poucas linhas, estáveis.
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_systems (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name       TEXT NOT NULL,
  slug       TEXT NOT NULL UNIQUE,
  notes      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- work_subsystems — os subsistemas de cada sistema.
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_subsystems (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  system_id  UUID NOT NULL REFERENCES work_systems(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  slug       TEXT NOT NULL,
  notes      TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- o slug do subsistema é único DENTRO do sistema, não globalmente: pode haver
  -- um `Flow` subsistema do Kobe sem colidir com nada de outro sistema.
  CONSTRAINT work_subsystems_system_slug_unique UNIQUE (system_id, slug),
  -- alvo da chave estrangeira composta de `work_sessions` (ver abaixo). Um
  -- UNIQUE redundante com a PK, existindo só pra ser referenciável.
  CONSTRAINT work_subsystems_id_system_unique UNIQUE (id, system_id)
);

CREATE INDEX IF NOT EXISTS idx_work_subsystems_system ON work_subsystems(system_id);

-- ============================================================================
-- work_sessions — UMA sala: Coder OU Mission Control.
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_sessions (
  -- = o identificador da sessão do Claude Code. SEM DEFAULT, de propósito.
  id                      UUID PRIMARY KEY,
  system_id               UUID NOT NULL REFERENCES work_systems(id),
  subsystem_id            UUID REFERENCES work_subsystems(id),
  kind                    TEXT NOT NULL CHECK (kind IN ('coder', 'mission')),
  topic_id                UUID REFERENCES topics(id),
  title                   TEXT,
  slug                    TEXT,
  briefing                TEXT,          -- o briefing integral que abriu a sala
  motivation              TEXT,          -- por que ela foi aberta (o pedido)
  cwd                     TEXT,          -- METADADO: onde ela rodou. Nunca chave.
  started_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_activity_at        TIMESTAMPTZ,
  status                  TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'idle', 'closed', 'dead')),
  transcript_path         TEXT,          -- ponteiro pro arquivo colhido
  transcript_bytes_copied BIGINT NOT NULL DEFAULT 0,
  dossier_path            TEXT,
  outcome_summary         TEXT,          -- um parágrafo: o que a sessão entregou
  -- A guarda do PAR: o subsistema tem que pertencer ao sistema declarado.
  CONSTRAINT work_sessions_subsystem_belongs_to_system
    FOREIGN KEY (subsystem_id, system_id)
    REFERENCES work_subsystems (id, system_id)
);

CREATE INDEX IF NOT EXISTS idx_work_sessions_system     ON work_sessions(system_id);
CREATE INDEX IF NOT EXISTS idx_work_sessions_subsystem  ON work_sessions(subsystem_id);
CREATE INDEX IF NOT EXISTS idx_work_sessions_topic      ON work_sessions(topic_id);
CREATE INDEX IF NOT EXISTS idx_work_sessions_started_at ON work_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_work_sessions_status     ON work_sessions(status);

-- ============================================================================
-- work_session_artifacts — o que a sessão PRODUZIU.
--
-- `test-report` é tipo próprio de propósito: é o resultado do plano de testes que
-- toda sessão Coder passa a executar (§3.4 do briefing). É o que se vai querer
-- abrir no console depois.
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_session_artifacts (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  session_id  UUID NOT NULL REFERENCES work_sessions(id) ON DELETE CASCADE,
  path        TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN
                ('code', 'doc', 'diagram', 'commit', 'migration', 'test-report')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  description TEXT
);

CREATE INDEX IF NOT EXISTS idx_work_session_artifacts_session
  ON work_session_artifacts(session_id);
CREATE INDEX IF NOT EXISTS idx_work_session_artifacts_kind
  ON work_session_artifacts(kind);

-- ============================================================================
-- SEMENTES — o mínimo pra o dispatch funcionar no dia 1, e nada além.
--
-- Sistema fora desta lista é EVENTO: o dispatch recusa, e o agente pergunta ao
-- operador antes de registrar. Isso impede que um erro de digitação vire um
-- sistema fantasma — o mesmo motivo pelo qual não se deixa aplicação criar
-- tabela sozinha.
--
-- `ON CONFLICT DO NOTHING` mantém a migration idempotente e, mais importante,
-- não pisa em `notes` que alguém tenha editado depois.
-- ============================================================================

INSERT INTO work_systems (name, slug, notes) VALUES
  ('Kobe', 'kobe', 'O framework Kobe — core e plugins.'),
  ('Flow', 'flow', 'O app web Flow (kanban pessoal). NÃO confundir com o plugin Flow, que é subsistema do Kobe.')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO work_subsystems (system_id, name, slug, notes)
SELECT s.id, v.name, v.slug, v.notes
  FROM work_systems s
  CROSS JOIN (VALUES
    ('Coder', 'coder', 'Plugin Coder — sessões remotas de Claude Code.'),
    ('Atrus', 'atrus', 'Plugin Atrus — transcrição de áudio/vídeo.'),
    ('Apolo', 'apolo', 'Plugin Apolo — envio de WhatsApp.'),
    ('Monet', 'monet', 'Plugin Monet — geração de imagem.'),
    ('Flow',  'flow',  'PLUGIN Flow (a ponte pro app). O APP Flow é sistema próprio.')
  ) AS v(name, slug, notes)
 WHERE s.slug = 'kobe'
ON CONFLICT (system_id, slug) DO NOTHING;
