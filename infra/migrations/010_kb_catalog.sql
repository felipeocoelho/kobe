-- Migration 010 — o catálogo da base de conhecimento do tópico (2026-09-04)
--
-- ┌────────────────────────────────────────────────────────────────────────┐
-- │ ESTA MIGRATION NÃO É DESTRUTIVA.                                       │
-- │   Ela só CRIA: uma tabela nova e seus índices. Não apaga linha, não    │
-- │   apaga coluna, não apaga tabela, e NÃO ALTERA nenhuma tabela          │
-- │   existente. Um banco que a aplique e queira voltar atrás só precisa   │
-- │   parar de consultar a tabela — nada no comportamento antigo depende   │
-- │   dela.                                                                │
-- └────────────────────────────────────────────────────────────────────────┘
--
-- O PROBLEMA QUE ISTO RESOLVE
-- ---------------------------
-- A pasta `user-data/topics/<slug>/knowledge/` não tem porteiro: **qualquer
-- arquivo que caia lá dentro entra no prompt do tópico, em todo turno**, e a
-- ordem de entrada é a ordem alfabética do nome do arquivo — que ninguém
-- escolheu, é acidente de implementação.
--
-- Foi assim que o formulário de debriefing de uma mentorada, com nome, e-mail e
-- telefone, passou a entrar em todo turno do tópico Olimpo entre 17/06 e
-- 04/09/2026. Ninguém decidiu: o arquivo caiu na pasta pelo caminho de upload
-- antigo e virou contexto. E, por a truncagem cortar pelo fim de uma lista
-- ordenada por acidente, o que sobrevivia ao corte eram justamente os primeiros
-- 1.156 caracteres do arquivo — o cabeçalho com os dados pessoais — enquanto as
-- respostas do formulário eram descartadas.
--
-- Em linguagem de banco: a base de conhecimento é hoje uma view sem `WHERE`,
-- ordenada pelo nome do arquivo. Esta tabela é o `WHERE`.
--
-- O QUE MORA AQUI, E O QUE **NÃO** MORA
-- --------------------------------------
-- Aqui mora o **registro de quem entra** — o inventário. O **conteúdo**
-- continua em arquivo `.md`, editável à mão, e isso é decisão declarada do
-- operador em 04/09/2026: *"eu não teria contra ter as coisas no file system
-- não"* — o argumento dele é sobre onde fica o registro, não sobre transformar
-- texto de knowledge base em linha de banco.
--
-- A divisão, então: **o arquivo guarda o texto; o banco governa quem entra no
-- prompt.**
--
-- POR QUE NO BANCO, E NÃO NUM MANIFESTO EM ARQUIVO
-- -------------------------------------------------
-- Esta sala chegou a recomendar um manifesto em arquivo, para que o catálogo não
-- dependesse do banco estar de pé. O operador encerrou a questão em 04/09, e o
-- argumento é dele: *"banco fora do ar é basicamente sinônimo de Kobe fora do ar
-- (…) o risco de eu deletar esse arquivo indevidamente é maior do que o risco do
-- banco dar pau"*.
--
-- E há um fato medido que reforça: o banco de produção tem backup diário,
-- semanal e mensal, verificados; a pasta `user-data/` **não tem rotina de backup
-- nenhuma** nesta VPS. Um dado no banco tem três redes; um dado no filesystem
-- não tem nenhuma.
--
-- NADA É APAGADO — SAIR É DATAR
-- ------------------------------
-- Tirar um documento da base é mudar o `status` para `removido` e datar a saída.
-- O arquivo continua em disco, intacto. É a mesma disciplina do registro de
-- estado: superar é datar, não deletar.
--
-- E a distinção que a linha `removido` carrega é de desenho, não de contabilidade:
-- **"nunca decidi" e "decidi que não" são estados diferentes.** Um arquivo sem
-- linha nenhuma está *batendo na porta* e o agente avisa; um arquivo com linha
-- `removido` já foi decidido, e voltar a perguntar transformaria uma decisão do
-- operador em pergunta recorrente.
--
-- ROLLBACK
-- --------
-- Parar de consultar a tabela devolve o comportamento anterior byte a byte (a
-- pasta inteira volta a entrar, por ordem alfabética). A tabela fica inerte.
-- Sair de vez seria uma migration de remoção, que não está prevista.

-- ============================================================================
-- kb_catalog — o inventário da base de conhecimento, por tópico
-- ============================================================================
--
-- `caminho` é o NOME DO ARQUIVO dentro de `knowledge/`, não um caminho
-- absoluto. Absoluto amarraria o catálogo à raiz da instalação e quebraria num
-- segundo usuário — e a raiz é valor de instalação, não do produto.
--
-- `ordem` existe para substituir o critério que hoje decide quem entra quando
-- não cabe tudo: a ordem alfabética do nome do arquivo. `NULL` significa "use a
-- data de admissão", que é o default sensato — quem foi admitido primeiro
-- entra primeiro.

CREATE TABLE IF NOT EXISTS kb_catalog (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  topic_id     UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  caminho      TEXT NOT NULL,
  titulo       TEXT NOT NULL,
  motivo       TEXT,
  ordem        INT,
  status       TEXT NOT NULL DEFAULT 'admitido',

  admitido_em  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  removido_em  TIMESTAMPTZ,

  -- Origem, quando existir: a mensagem em que o operador mandou admitir.
  -- NULO é permitido de propósito — diferente da tabela de fatos, isto aqui é
  -- um inventário de prateleira, não uma afirmação sobre o mundo. A semeadura
  -- inicial e o arquivo admitido por linha de comando não têm mensagem.
  source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,

  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT kb_catalog_status_check
    CHECK (status IN ('admitido', 'removido')),

  -- Estado e data de saída são a MESMA informação e não podem discordar. Uma
  -- linha "admitido" com data de saída seria simultaneamente dentro e fora; uma
  -- "removido" sem data perderia o quando — e o quando é metade do valor.
  CONSTRAINT kb_catalog_saida_check
    CHECK ((status = 'admitido') = (removido_em IS NULL)),

  -- Um arquivo tem UMA linha por tópico. Readmitir vira `UPDATE` da mesma
  -- linha, não linha nova: o inventário responde "o que está na prateleira
  -- agora", e duas linhas para o mesmo arquivo tornariam essa pergunta ambígua.
  CONSTRAINT kb_catalog_um_por_arquivo UNIQUE (topic_id, caminho)
);

-- O índice da leitura quente: "o que está admitido neste tópico?". PARCIAL,
-- porque o catálogo acumula removidos para sempre e a fatia admitida é a que se
-- lê em todo turno.
CREATE INDEX IF NOT EXISTS idx_kb_catalog_admitidos
  ON kb_catalog(topic_id, ordem, admitido_em)
  WHERE status = 'admitido';

CREATE INDEX IF NOT EXISTS idx_kb_catalog_topico
  ON kb_catalog(topic_id);
