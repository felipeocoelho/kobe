-- Migration 013 — a mensagem inteira em cada assunto (2026-10-31)
--
-- Highlander v3, F7. O desenho foi aprovado pelo operador em 31/10/2026, depois de
-- duas rodadas de medição (o "gabarito de assunto", rodada 2 da F7). A 012 deu ao
-- sistema uma LISTA de assuntos, mas só a AFIRMAÇÃO recebia assunto — a mensagem
-- que não gerou afirmação ficava fora de qualquer conversa. Esta migration faz a
-- MENSAGEM apontar para o assunto, inteira.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Ela CRIA uma tabela (`message_subjects`) e duas funções de trava,    │
-- │   ACRESCENTA colunas NULAS (`lucien_subjects.parent_id`, a sustentação │
-- │   literal em `lucien_claims`) e contadores com default zero em         │
-- │   `lucien_runs`, e ALARGA uma restrição (o modo de rodada aceita       │
-- │   `marcacao`). Não apaga linha, coluna nem tabela, não muda o texto de │
-- │   mensagem nenhuma, não toca nas travas de origem da 008. Reaplicá-la  │
-- │   não faz nada de novo.                                                │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- A MENSAGEM VAI INTEIRA — DECISÃO DO OPERADOR, 23/10/2026
-- --------------------------------------------------------
-- A proposta anterior era guardar um pedaço da mensagem em cada assunto. Ele
-- recusou: *"vai ser uma mensagem cortada pela metade… pegar assim um trecho eu
-- acho que é meio Frankenstein, é meio gambiarra"* — e deu a direção: *"levar a
-- mensagem inteira… sinalize que tal parte da mensagem é uma referência a outro
-- assunto"*. Por isso a unidade aqui é a MENSAGEM (`message_id`), e o "trecho"
-- é só uma MARCAÇÃO dentro dela (`parts`): posições em caracteres sobre o texto
-- de `messages`, que nunca muda. Nada aqui recorta, copia ou reescreve a fala.
--
-- POR QUE POSIÇÃO, E NÃO RESUMO
-- -----------------------------
-- Posição é exata e conferível: a parte É aquele pedaço do texto. Resumo seria
-- texto gerado por modelo, sem ninguém conferindo — a mesma família do "o céu é
-- azul" sem contexto que o operador recusou. E rótulo escrito dentro do texto
-- alteraria a fala literal, que é o bruto inviolável do Kobe.
--
-- `content_md5` guarda o hash do texto no instante da marcação. O texto não
-- muda por regra; se um dia mudar, a marcação sabe que ficou velha em vez de
-- apontar para posições que já não são aquelas.
--
-- UM PRINCIPAL POR MENSAGEM — TRAVA DO BANCO, NÃO DISCIPLINA
-- ----------------------------------------------------------
-- Medido na rodada 2: com a lista de assuntos fixa, dois leitores independentes
-- concordam sobre o assunto de cada parte em 98–99% dos casos — essa é a medida
-- que tem gabarito. Deduzir o principal da mensagem ("o assunto com mais texto")
-- caiu para ~97% por EMPATE. Então o leitor DECLARA o principal, e o banco
-- garante que há exatamente um: no máximo um pelo índice único parcial, ao
-- menos um pela trava diferida (que olha a mensagem inteira no fim da
-- transação, para que uma marcação possa ser escrita linha a linha).
--
-- SUBASSUNTO DE UM NÍVEL
-- ----------------------
-- "F7" dentro de "Highlander v3". Sem árvore funda: o operador recusou o
-- guarda-chuva "manutenção do Kobe", e árvore é onde ele nasce. A trava abaixo
-- recusa o pai que já tem pai e o filho que já tem filhos.
--
-- A SUSTENTAÇÃO LITERAL DA AFIRMAÇÃO
-- ----------------------------------
-- Aprovado em 31/10/2026: o curador devolve, com cada afirmação, as palavras da
-- mensagem que a sustentam. O código acha essas palavras no texto e grava ONDE
-- elas estão (`support_from`/`support_to`) — é isso que faz a afirmação herdar o
-- assunto da PARTE de onde saiu, por posição, e não por escolha do modelo.
-- As colunas são nulas por desenho: palavra não achada = herança pelo principal
-- da mensagem, contada em `lucien_runs.subject_fallbacks`.
--
-- ROLLBACK
-- --------
-- `LUCIEN_MARCACAO_ENABLED=false` (o padrão) faz o curador não marcar nada: o
-- prompt dele volta a ser, byte a byte, o da F7.1. Para desfazer uma marcação
-- retroativa: `kobe-assunto desfazer-marcacao <run_id>`. Para desfazer toda:
--     DELETE FROM message_subjects;
--     UPDATE lucien_claims SET support_quote = NULL, support_from = NULL, support_to = NULL;
-- — que é exatamente o estado em que esta migration deixa o banco. Sair de vez
-- seria uma migration de remoção, que não está prevista.

-- ============================================================================
-- message_subjects — a mensagem aponta para o assunto, com as partes marcadas
-- ============================================================================

CREATE TABLE IF NOT EXISTS message_subjects (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  message_id  UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  -- Sem CASCADE: assunto nunca é apagado pelo código; se alguém tentar, a
  -- marcação impede em vez de sumir calada. Fusão MOVE as linhas (assuntos.py).
  subject_id  UUID NOT NULL REFERENCES lucien_subjects(id),
  role        TEXT NOT NULL,
  -- [[início, fim], ...] em caracteres sobre `messages.content`, fim exclusivo.
  parts       JSONB NOT NULL,
  content_md5 TEXT NOT NULL,
  created_by  TEXT NOT NULL DEFAULT 'lucien',
  run_id      UUID REFERENCES lucien_runs(id) ON DELETE SET NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT message_subjects_unico UNIQUE (message_id, subject_id),
  CONSTRAINT message_subjects_role_check
    CHECK (role IN ('principal', 'secundario')),
  CONSTRAINT message_subjects_created_by_check
    CHECK (created_by IN ('lucien', 'agrupamento', 'operador')),
  CONSTRAINT message_subjects_parts_check
    CHECK (jsonb_typeof(parts) = 'array' AND jsonb_array_length(parts) >= 1)
);

-- No máximo um principal por mensagem.
CREATE UNIQUE INDEX IF NOT EXISTS idx_message_subjects_um_principal
  ON message_subjects(message_id) WHERE role = 'principal';
-- A leitura quente: "a conversa deste assunto", em ordem.
CREATE INDEX IF NOT EXISTS idx_message_subjects_subject
  ON message_subjects(subject_id);
CREATE INDEX IF NOT EXISTS idx_message_subjects_run
  ON message_subjects(run_id);

-- Ao menos um principal, conferido no FIM da transação (trava diferida).
CREATE OR REPLACE FUNCTION message_subjects_tem_principal() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  alvo UUID := COALESCE(NEW.message_id, OLD.message_id);
BEGIN
  IF EXISTS (SELECT 1 FROM message_subjects WHERE message_id = alvo)
     AND NOT EXISTS (SELECT 1 FROM message_subjects
                      WHERE message_id = alvo AND role = 'principal') THEN
    RAISE EXCEPTION 'mensagem % marcada sem assunto principal', alvo
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS trg_message_subjects_tem_principal ON message_subjects;
CREATE CONSTRAINT TRIGGER trg_message_subjects_tem_principal
  AFTER INSERT OR UPDATE OR DELETE ON message_subjects
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION message_subjects_tem_principal();

-- ============================================================================
-- lucien_subjects.parent_id — o subassunto de um nível
-- ============================================================================

ALTER TABLE lucien_subjects
  ADD COLUMN IF NOT EXISTS parent_id UUID REFERENCES lucien_subjects(id) ON DELETE SET NULL;

ALTER TABLE lucien_subjects DROP CONSTRAINT IF EXISTS lucien_subjects_nao_e_pai_de_si;
ALTER TABLE lucien_subjects
  ADD CONSTRAINT lucien_subjects_nao_e_pai_de_si
  CHECK (parent_id IS NULL OR parent_id <> id);

CREATE INDEX IF NOT EXISTS idx_lucien_subjects_parent
  ON lucien_subjects(parent_id) WHERE parent_id IS NOT NULL;

CREATE OR REPLACE FUNCTION lucien_subjects_um_nivel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.parent_id IS NOT NULL THEN
    IF EXISTS (SELECT 1 FROM lucien_subjects
                WHERE id = NEW.parent_id AND parent_id IS NOT NULL) THEN
      RAISE EXCEPTION 'o assunto-pai já é subassunto: só um nível'
        USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (SELECT 1 FROM lucien_subjects WHERE parent_id = NEW.id) THEN
      RAISE EXCEPTION 'este assunto já tem subassuntos e não pode virar subassunto'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_lucien_subjects_um_nivel ON lucien_subjects;
CREATE TRIGGER trg_lucien_subjects_um_nivel
  BEFORE INSERT OR UPDATE OF parent_id ON lucien_subjects
  FOR EACH ROW EXECUTE FUNCTION lucien_subjects_um_nivel();

-- ============================================================================
-- lucien_claims — a sustentação literal e onde ela está
-- ============================================================================

ALTER TABLE lucien_claims ADD COLUMN IF NOT EXISTS support_quote TEXT;
ALTER TABLE lucien_claims ADD COLUMN IF NOT EXISTS support_from  INT;
ALTER TABLE lucien_claims ADD COLUMN IF NOT EXISTS support_to    INT;

ALTER TABLE lucien_claims DROP CONSTRAINT IF EXISTS lucien_claims_support_check;
ALTER TABLE lucien_claims
  ADD CONSTRAINT lucien_claims_support_check
  CHECK ((support_from IS NULL) = (support_to IS NULL)
         AND (support_from IS NULL OR (support_from >= 0 AND support_to > support_from)));

-- ============================================================================
-- lucien_runs — os contadores e o modo `marcacao`
-- ============================================================================
-- Pelo mesmo motivo dos contadores da 012: a trava só é auditável se o número
-- de vezes que ela disparou estiver à vista. `quotes_found`/`quotes_missing` é a
-- taxa de palavras literais achadas (item 2 do plano de testes aprovado);
-- `subject_fallbacks` conta as afirmações que herdaram o principal por falta de
-- posição.

ALTER TABLE lucien_runs ADD COLUMN IF NOT EXISTS messages_marked   INT NOT NULL DEFAULT 0;
ALTER TABLE lucien_runs ADD COLUMN IF NOT EXISTS quotes_found      INT NOT NULL DEFAULT 0;
ALTER TABLE lucien_runs ADD COLUMN IF NOT EXISTS quotes_missing    INT NOT NULL DEFAULT 0;
ALTER TABLE lucien_runs ADD COLUMN IF NOT EXISTS subject_fallbacks INT NOT NULL DEFAULT 0;

ALTER TABLE lucien_runs
  DROP CONSTRAINT IF EXISTS lucien_runs_mode_check;
ALTER TABLE lucien_runs
  ADD CONSTRAINT lucien_runs_mode_check
  CHECK (mode IN ('incremental', 'reconstruction', 'assuntos', 'marcacao'));
