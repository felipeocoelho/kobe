-- Migration 009 — o marco da reconstrução, gravado (2026-08-31)
--
-- Highlander v3, pós-F3. Conserta um defeito de MODELAGEM, não de código: o teto
-- da varredura do passado nunca existiu como dado. Ele era derivado na hora, e
-- por isso se mexia.
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Ela ALARGA uma restrição (passa a aceitar um valor a mais) e         │
-- │   ACRESCENTA linhas a `lucien_cursor`. Não apaga linha, não apaga      │
-- │   coluna, não apaga tabela, não muda tipo nem default, e não encosta   │
-- │   em nenhuma outra tabela. Reaplicá-la não faz nada de novo.           │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- O DEFEITO, EM UMA FRASE DE BANCO
-- ---------------------------------
-- A varredura do passado lia de zero até um teto por tópico, e esse teto era o
-- cursor `incremental` **lido na hora** — o cursor que anda com a conversa. Em
-- vez de fincar um snapshot no `init`, a rotina relia o `last_seq` corrente a
-- cada iteração: o fim da tabela fugia enquanto ela lia. Cada mensagem nova que
-- a leitura corrente processava entrava **também** na conta do que faltava
-- reconstruir, e o número de "pendente" nunca chegava a zero enquanto houvesse
-- conversa acontecendo.
--
-- O estrago é de COTA, não de dado: a T8 (dedupe) segura a duplicata, então o
-- pior caso é reler lote já lido e pagar o modelo de novo. Não houve perda nem
-- corrupção em momento nenhum.
--
-- O CONSERTO: UM TERCEIRO ESCOPO, FINCADO UMA VEZ E IMÓVEL
-- ---------------------------------------------------------
-- `marco` guarda "daqui pra trás é passado", por tópico. A reconstrução passa a
-- calcular `(reconstruction, marco]` — um intervalo FIXO, que converge.
--
-- Ele não é um cursor no sentido dos outros dois: os outros marcam progresso e
-- sobem conforme se lê; este marca uma FRONTEIRA declarada e não sobe. Mora na
-- mesma tabela porque é a mesma chave (escopo, tópico) e o mesmo `last_seq`;
-- separá-lo numa tabela própria seria uma tabela de duas colunas para dizer o
-- que esta já diz.
--
-- POR QUE O BACKFILL COPIA O INCREMENTAL, E POR QUE ISSO NÃO É UM CHUTE
-- ----------------------------------------------------------------------
-- Há bancos com reconstrução EM ANDAMENTO — em 31/08/2026, 1.251 mensagens em 7
-- tópicos ainda por varrer em produção. O marco original desses bancos nunca foi
-- gravado em lugar nenhum, então não há como recuperá-lo: o que existe hoje é o
-- cursor incremental, já corrido para a frente.
--
-- Copiá-lo congela o teto EXATAMENTE onde ele está no instante da aplicação.
-- Logo o backlog logo depois da migration é numericamente IGUAL ao de logo
-- antes: não encolhe (que seria repetir o achado 1 da F3, o `init` que apagava o
-- passado em silêncio) e não cresce. A migration não devolve a cota já
-- comprometida — ela para a sangria. Daí em diante o número só desce.
--
-- Tópico sem cursor incremental não ganha marco, e isso é o certo: é um tópico
-- onde o `init` nunca rodou, e a resposta correta continua sendo "o marco ainda
-- NÃO foi fincado".
--
-- COMO REVERTER
-- -------------
-- Restaurar a restrição de dois valores e remover as linhas de escopo `marco`.
-- Nesta ordem, porque a restrição antiga recusa as linhas novas. O comando exato
-- está no CHANGELOG, na entrada desta migration. Reverter é seguro: sem o marco,
-- o código volta a dizer "marco não fincado" e a varredura simplesmente para de
-- ter o que fazer — nenhuma afirmação já gravada é afetada.

-- ============================================================================
-- 1. A restrição passa a aceitar o escopo novo
-- ============================================================================
-- Removida e recriada, nesta ordem, para que reaplicar a migration não estoure.
-- `ADD CONSTRAINT` não tem `IF NOT EXISTS` no Postgres; o `DROP ... IF EXISTS`
-- antes é o que faz o par ser idempotente de verdade, e não "idempotente desde
-- que ninguém rode duas vezes".
--
-- A OUTRA restrição da 008 — `lucien_runs_mode_check`, sobre o MODO da rodada —
-- fica como está, de propósito: nenhuma rodada roda com modo `marco`. Fincar um
-- marco não é ler um lote; alargar aquela trava seria afrouxar uma garantia sem
-- ter o que ganhar com isso.

ALTER TABLE lucien_cursor
  DROP CONSTRAINT IF EXISTS lucien_cursor_scope_check;

ALTER TABLE lucien_cursor
  ADD CONSTRAINT lucien_cursor_scope_check
  CHECK (scope IN ('incremental', 'reconstruction', 'marco'));

-- ============================================================================
-- 2. O marco dos bancos que já estão rodando
-- ============================================================================
-- `ON CONFLICT DO NOTHING` e não `DO UPDATE`: o marco, uma vez fincado, não se
-- mexe. É a mesma regra que o código aplica no `init` — e é ela que impede a
-- migration de empurrar o teto para a frente se for reaplicada depois de a
-- leitura corrente ter andado mais.

INSERT INTO lucien_cursor (scope, topic_id, last_seq)
SELECT 'marco', topic_id, last_seq
  FROM lucien_cursor
 WHERE scope = 'incremental'
ON CONFLICT (scope, topic_id) DO NOTHING;
