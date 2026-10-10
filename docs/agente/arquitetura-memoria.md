# Arquitetura da memória — as camadas e onde cada coisa vive

> **Satélite do `CLAUDE.md`.** Este arquivo **não** entra no prompt — ele é
> carregado sob demanda, por gatilho obrigatório declarado na seção
> "Gatilhos taxativos" do `CLAUDE.md`.
>
> O corpo abaixo é **idêntico** ao que estava no `CLAUDE.md`: a etapa que criou
> este arquivo não reescreveu nada, só mudou de lugar.

---

## Sistema de memória

Você tem três camadas de memória:

### 1. Identidade e personalidade (arquivos `.md` em `user-data/`)

- `user-data/persona/SOUL.md` — sua personalidade base (alma do agente)
- `user-data/identity/USER.md` — quem é o operador
- `user-data/identity/PREFERENCES.md` — como o operador prefere ser tratado
- `user-data/identity/agent-name` — nome pelo qual o operador te chama (opcional)
- `user-data/knowledge/` — conhecimento curado pelo operador (livre estrutura)
- `user-data/topics/<slug>/` — quando existir, contém `prompt.md` e `knowledge/` específicos daquele tópico do Telegram. O `<slug>` é o **kebab-case minúsculo, sem acento** do nome do forum topic (ex: tópico "Café & Livros" → pasta `cafe-livros/`). Tópico no chat raiz (sem thread_id) usa slug fixo `general`. O bot lê `prompt.md` + tudo em `knowledge/` (ordem alfabética) e injeta no prompt como `[Contexto do tópico]` — limite de 20k chars, acima disso trunca e avisa via Telegram.

Esses arquivos pertencem ao **operador**, não ao framework. Ficam fora do repo público. Você pode atualizá-los quando ele autorizar.

### 2. Memória persistente (no Postgres do Kobe)

- **Tópicos** (forum topics do Telegram): cada um é um espaço de assunto (ex: "Olimpo", "Pessoal", "Projetos")
- **Sessões**: dentro de um tópico, conversas delimitadas no tempo. Não há mais
  uma camada de *conversation* acima delas — vide a nota sobre o Chat Manager
- **Mensagens**: histórico bruto de tudo que foi dito
- **Artefatos salvos**: documentos persistidos quando o operador disser "salva isso pra depois"

O banco é **PostgreSQL, acessado direto por `psycopg`** — sem PostgREST e sem
adaptador no meio. Onde ele mora é **configuração**, não código: uma linha
`DATABASE_URL` no `.env`. `bot/db.py` é o único arquivo que sabe qual banco é;
o resto do Kobe manda SQL.

Duas ferramentas de manutenção, ambas rodáveis à mão:

- **`infra/migrate.py`** — runner de migrations versionado (tabela de controle,
  ordem determinística, idempotente, recusa aplicar fora de ordem, detecta
  drift). `status` diz em que versão o banco está; `up` aplica o que falta.
  **Migration aplicada é imutável** — correção vira migration nova, pra frente.
- **`infra/compat_gate.py`** — o portão de compatibilidade de ambiente. Falha
  quando o banco diverge do schema versionado em collation, ctype, encoding,
  `data_checksums`, `TimeZone`, versão do servidor/extensões, ou na **ordem
  física das colunas**. Essa última é a que nenhum diff por nome enxerga e a
  que quebra carga posicional **em silêncio**.

### 3. Workspace (em `projetos/`)

Filesystem onde você cria/edita projetos do operador. Cada projeto tem seu próprio `CLAUDE.md` ou `README.md` descrevendo o quê é.
