# infra/

Arquivos de infraestrutura do Kobe.

## `schema.sql`

Schema completo do banco Supabase: tabelas (`topics`, `topic_name_history`,
`sessions`, `messages`, `saved_artifacts`), extensões (`vector`, `uuid-ossp`),
índices.

**Como aplicar:**

1. Abra o painel do seu projeto no Supabase
2. Vá em **Database → Extensions** e habilite `vector`
3. Vá em **SQL Editor → New query**
4. Cole o conteúdo de `schema.sql`
5. **Run**

O instalador (`install.sh`) não roda esse SQL automaticamente porque a anon
key não tem permissão pra DDL. Use o painel web.

## `kobe.service.template`

Template do unit file do systemd (modo `--user`). O instalador substitui
`{{KOBE_HOME}}` pelo caminho real e copia pra
`~/.config/systemd/user/kobe.service`.

Comandos úteis depois de instalado:

```bash
systemctl --user status kobe          # status atual
systemctl --user restart kobe         # reiniciar
systemctl --user stop kobe            # parar
journalctl --user -u kobe -f          # ver logs em tempo real
journalctl --user -u kobe --since "10 min ago"
```

Pra rodar mesmo sem login SSH ativo:

```bash
sudo loginctl enable-linger $USER
```

O instalador oferece essa opção.
