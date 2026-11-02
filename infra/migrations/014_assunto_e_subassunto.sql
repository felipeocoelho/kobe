-- Migration 014 — assunto (tema) e subassunto (conversa) (2026-10-31)
--
-- Highlander v3, F7. Decisão do operador em 31/10/2026 (~19:30), depois da medição
-- do principal declarado: os 140 temas largos da F7.1 viram o ASSUNTO (a pasta
-- grande), e cada mensagem recebe um SUBASSUNTO do tamanho de uma conversa — é ele
-- que manda na marcação.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   ACRESCENTA uma coluna com default (`lucien_subjects.kind`, 'tema'    │
-- │   para tudo que já existe) e TROCA o corpo de uma função de trava da   │
-- │   013 por um mais estrito. Não apaga linha, coluna nem tabela.         │
-- │   Reaplicá-la não faz nada de novo.                                    │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- O QUE A MEDIÇÃO MOSTROU
-- -----------------------
-- Com a lista de marcação feita dos 140 temas da F7.1, dois modelos independentes
-- (Opus e Gemini) concordaram sobre o assunto principal de 87,0% das 486 mensagens
-- da amostra. Quando o principal era um TEMA largo, 85,5%; quando era um assunto do
-- tamanho de CONVERSA, 94,0%. Temas largos se sobrepõem ("Coder — …", "Salas tmux —
-- defeitos…"), e uma mensagem cabe em vários. Por isso a marcação passa a usar
-- conversas, e o tema vira a pasta de cima.
--
-- AS DUAS NATUREZAS
-- -----------------
--   tema      — o assunto largo (os 140 da F7.1, e os que vierem). É a pasta.
--   conversa  — o subassunto: uma conversa sobre UMA coisa, que se retoma. A
--               mensagem é marcada com conversas; a conversa aponta para o tema
--               dela por `parent_id` (um nível, trava da 013).
--
-- A trava de um nível da 013 fica mais estrita: o pai tem que ser TEMA e o filho
-- tem que ser CONVERSA. Conversa sem tema (parent nulo) é permitida — nem toda
-- conversa cabe num tema que já existe.
--
-- ROLLBACK
-- --------
-- A coluna nova é aditiva e tem default; o código que não a lê continua igual.
-- Para voltar a função de trava à da 013, reaplique o corpo dela.

ALTER TABLE lucien_subjects
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'tema';

ALTER TABLE lucien_subjects DROP CONSTRAINT IF EXISTS lucien_subjects_kind_check;
ALTER TABLE lucien_subjects
  ADD CONSTRAINT lucien_subjects_kind_check CHECK (kind IN ('tema', 'conversa'));

CREATE INDEX IF NOT EXISTS idx_lucien_subjects_conversas_ativas
  ON lucien_subjects(last_touched_at DESC) WHERE status = 'ativo' AND kind = 'conversa';

CREATE OR REPLACE FUNCTION lucien_subjects_um_nivel() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.parent_id IS NOT NULL THEN
    IF NEW.kind <> 'conversa' THEN
      RAISE EXCEPTION 'só uma conversa (subassunto) pode ficar dentro de um tema'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM lucien_subjects
                    WHERE id = NEW.parent_id AND kind = 'tema' AND parent_id IS NULL) THEN
      RAISE EXCEPTION 'o pai de uma conversa tem que ser um tema de topo'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM lucien_subjects WHERE parent_id = NEW.id)
     AND (NEW.kind <> 'tema' OR NEW.parent_id IS NOT NULL) THEN
    RAISE EXCEPTION 'este assunto tem subassuntos: tem que continuar tema de topo'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_lucien_subjects_um_nivel ON lucien_subjects;
CREATE TRIGGER trg_lucien_subjects_um_nivel
  BEFORE INSERT OR UPDATE OF parent_id, kind ON lucien_subjects
  FOR EACH ROW EXECUTE FUNCTION lucien_subjects_um_nivel();
