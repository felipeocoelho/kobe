# Knowledge base e user-data — edição conversacional

> **Satélite do `CLAUDE.md`.** Este arquivo **não** entra no prompt — ele é
> carregado sob demanda, por gatilho obrigatório declarado na seção
> "Gatilhos taxativos" do `CLAUDE.md`.
>
> O corpo abaixo é **idêntico** ao que estava no `CLAUDE.md`: a etapa que criou
> este arquivo não reescreveu nada, só mudou de lugar.

---

## Atualização conversacional de user-data (pós-onboarding)

Mesmo depois do onboarding, o operador pode (e deve) atualizar dados sobre ele mesmo conversando com você. Quando ele disser coisas como:

- "anota aí que prefiro X" / "lembra que sou Y" / "minha regra é Z"
- "agora eu trabalho com…", "mudei de área pra…"
- "me chama de [outro nome] daqui pra frente"
- "essa palavra você sempre transcreve errado, é assim…"

→ identifique qual arquivo em `user-data/` faz mais sentido (USER.md, PREFERENCES.md, agent-name, transcription-hints.md) e edite ali. Confirme em uma linha ("anotei em PREFERENCES.md") — sem alarde.

Princípio: edição manual dos arquivos é fallback; a forma natural de configurar o agente é conversando com ele.

## Edição conversacional da knowledge base do tópico

Cada forum topic do Telegram tem (opcionalmente) uma pasta `user-data/topics/<slug>/`:

- `prompt.md` — instruções permanentes deste tópico (system prompt local)
- `knowledge/*.md` — base de conhecimento (glossários, briefings, notas)

Você (agente) carrega tudo isso automaticamente no prompt — vide seção `[Contexto do tópico]`. O bot também aceita upload de `.txt/.md/.pdf/.docx` direto no chat (salva em `knowledge/` automaticamente). **Mas o operador também pode pedir edição conversando contigo**, e nesse caso você deve agir direto, sem cerimônia:

| Operador diz | Você faz |
|---|---|
| "anota como instrução: …" / "regra desse tópico: …" | append em `user-data/topics/<slug>/prompt.md` (ou cria) |
| "adiciona à base de conhecimento: …" / "anota na base: …" | cria arquivo novo em `knowledge/` com slug derivado do conteúdo (ex: `clientes-2026.md`) |
| "atualiza a instrução sobre X" / "muda a regra de Y" | localiza linha relevante em `prompt.md` ou no arquivo `knowledge/` certo e edita inline |
| "esquece a instrução X" / "remove o arquivo Y" | apaga linha/seção do `prompt.md` ou deleta arquivo de `knowledge/` |
| "o que tem na base?" / "quais as instruções daqui?" | lista `prompt.md` + `knowledge/*` com resumo de 1 linha de cada |

Princípios:
- **Slug do tópico**: vem do contexto da chamada. Quando em dúvida, leia o cabeçalho `[Telegram] tópico:` do prompt — o slug é derivado do nome registrado em `topics.current_name`.
- **Confirme em uma linha** após editar: "anotei em `prompt.md`" ou "salvei em `knowledge/clientes-2026.md`". Sem alarde.
- **Nomes de arquivos**: kebab-case, descritivo, com prefixo numérico se ordem importa (`01-glossario.md`, `02-clientes.md`). Não use timestamps.
- **Nada de criar pasta de tópico vazio**: só se o operador pediu pra adicionar conteúdo. Se ele falou "anota X" mas o tópico nem tem pasta ainda, crie-a com o arquivo adequado e ponto.
