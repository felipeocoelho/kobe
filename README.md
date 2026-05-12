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
├── bot/                       # Camada de transporte (Python)
├── user-data/                 # Dados do operador (gitignored, exceto .example)
│   ├── persona/SOUL.md        # Personalidade do agente (a partir de SOUL.md.example)
│   ├── identity/USER.md       # Quem é o operador (a partir de USER.md.example)
│   ├── identity/PREFERENCES.md# Preferências de comunicação
│   └── knowledge/             # Conhecimento curado pelo operador
├── projetos/                  # Workspace dinâmico (gitignored)
├── infra/                     # schema.sql, systemd template
├── CLAUDE.md                  # Cérebro mestre do agente
└── SPEC.md                    # Especificação completa
```

Tudo em `user-data/` é **do usuário** — fica fora do Git público. O instalador cria as cópias iniciais a partir dos `.example` distribuídos com o produto.

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

## Operação

Comandos do dia-a-dia depois de instalado:

```bash
# Status / logs
systemctl --user status kobe
journalctl --user -u kobe -f                    # follow live
journalctl --user -u kobe --since "1 hour ago"  # janela específica

# Reiniciar (necessário ao mudar o .env)
systemctl --user restart kobe

# Parar / iniciar
systemctl --user stop kobe
systemctl --user start kobe
```

Cada chamada do Claude emite uma linha de métricas no journal:

```
INFO kobe.handler: claude_run status=ok elapsed=12.4s prompt_len=3128
                   history_msgs=18 tool_calls=4 reply_len=812
```

`status` é `ok`, `timeout`, `not_found`, `exit_<N>` ou `error`. Útil pra grep rápido (`journalctl --user -u kobe | grep claude_run`).

### Comandos no chat

- `/nova` — arquiva a sessão ativa do tópico e abre uma nova (memória recente zera).
- `/contexto` — mostra um resumo da memória ativa.
- `/salvar <título>` — consolida a sessão num artefato pesquisável.
- `/retomar <termo>` — busca em artefatos salvos.

## Troubleshooting

### Bot responde "Tive um problema te respondendo agora" ou "Estourei o tempo limite"

Timeout do Claude CLI. Padrão é 300s. Em tarefas pesadas (Claude lendo arquivos, rodando comandos) pode estourar. Solução:

```bash
# No ~/kobe/.env
CLAUDE_TIMEOUT_SECONDS=900    # 15 min
```

Depois `systemctl --user restart kobe`. **O serviço NÃO recarrega o `.env` sozinho** — sem o restart o valor antigo continua em memória.

### "O CLI do Claude não está disponível pro serviço"

`systemd --user` sobe com PATH mínimo que pode não incluir `~/.local/bin` (onde o Claude Code é instalado). O template `infra/kobe.service.template` já força um PATH explícito; se você customizou a unit, garanta:

```ini
Environment=PATH=%h/.local/bin:%h/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
```

### Bot recebe mensagem mas não responde nada (nem erro)

Quase sempre é autorização. Confere `TELEGRAM_ALLOWED_USER_IDS` no `.env` — usuários não-listados são ignorados silenciosamente (por segurança). Pega seu user ID com [@userinfobot](https://t.me/userinfobot).

### Áudio não transcreve

Confere `GROQ_API_KEY` no `.env`. Groq valida extensão case-sensitive — o bot já força lowercase, mas se você mexeu no `transcribe.py`, mantém o cuidado. Voice notes do Telegram viram `.ogg`, áudios anexados mantêm a extensão original.

### Datas do tipo "amanhã" sendo interpretadas como passado

Já corrigido na v0.2 — o prompt agora injeta `America/Sao_Paulo` explícito. Se persistir, confirma que você está em v0.2+ (`git -C ~/kobe log --oneline -1`).

### Personalizar a alma do agente / dados pessoais

A partir da v0.5 todos os arquivos de identidade ficam em `user-data/` e são gitignored (não vão pro repo público). Edite à vontade:

```bash
# personalidade do agente
$EDITOR ~/kobe/user-data/persona/SOUL.md

# quem é você
$EDITOR ~/kobe/user-data/identity/USER.md

# preferências de comunicação
$EDITOR ~/kobe/user-data/identity/PREFERENCES.md
```

Após editar, `systemctl --user restart kobe` (o `.env` e o filesystem só são relidos no restart pra alguns paths).

## Licença

[MIT](./LICENSE)
