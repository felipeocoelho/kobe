# Kobe

> Assistente pessoal IA self-hosted que conecta Telegram ↔ Claude Code numa VPS Linux.

Cada usuário cria seu próprio bot Telegram, instala o Kobe na sua VPS, e passa a conversar com o Claude por mensagens (texto ou áudio) com memória persistente segmentada por tópicos.

## Princípios

- **Inteligência mora no Claude, não no Python.** O bot é só um carteiro.
- **Agnóstico de operador.** Quem clona instala com configuração mínima própria.
- **Memória em camadas.** Identidade (Git), histórico (Supabase), workspace (filesystem).

## Stack

- Python 3.11+ (bot)
- Claude Code CLI (`claude -p`)
- Supabase (PostgreSQL + pgvector)
- Telegram Bot API
- Groq Whisper Large-v3 (transcrição)
- systemd `--user`

## Status

Em construção. Veja [SPEC.md](./SPEC.md) pro escopo completo e ordem de implementação.

## Estrutura

```
kobe/
├── bot/                   # Camada de transporte (Python)
├── memoria/identidade/    # DNA do agente (SOUL, USER, PREFERENCES)
├── memoria/conhecimento/  # Conhecimento curado (operador popula)
├── projetos/              # Workspace dinâmico (gitignored)
├── infra/                 # schema.sql, systemd template
├── CLAUDE.md              # Cérebro mestre do agente
└── SPEC.md                # Especificação completa
```

## Instalação

Antes de rodar o instalador, tenha em mãos:

1. **Bot Telegram** criado via [@BotFather](https://t.me/BotFather) — token salvo
2. **Supergrupo Telegram** com tópicos habilitados (você como admin)
3. **Conta Supabase** com projeto criado, Project URL + **Secret Key** (Project Settings → API Keys → "Secret keys" / service_role — não use a publishable/anon) e extensão `vector` habilitada (Database → Extensions)
4. **Conta Groq** com API key ([console.groq.com](https://console.groq.com))
5. **Claude Code** instalado e autenticado ([docs](https://docs.claude.com/en/docs/claude-code/setup))

Depois:

```bash
git clone https://github.com/felipeocoelho/kobe.git
cd kobe
./install.sh
```

O instalador é guiado, idempotente, em texto puro, e instala como `systemd --user` (sem precisar root pra rodar o serviço). Veja [SPEC.md §7](./SPEC.md) pra detalhes do fluxo.

### Desinstalar

```bash
bash ~/kobe/uninstall.sh
```

(Ajuste o path se você customizou onde o Kobe foi instalado.)

Remove só o que o instalador criou (diretório + unit do systemd). Não toca em Claude Code, Supabase, Telegram nem nas dependências do sistema. Oferece backup do `.env` antes de apagar.

### Modo dev (sem instalar como serviço)

```bash
cd kobe
cp .env.example .env  # preencha as variáveis
./dev-run.sh          # roda em foreground, logs no terminal
```

## Licença

[MIT](./LICENSE)
