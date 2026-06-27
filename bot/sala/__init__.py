"""bot.sala — núcleo genérico de "sala" visível (tmux ``--remote-control``).

Extraído da maquinaria do plugin Coder (`plugins/public/coder/scripts/
coder_worker.py` + `run_remote.py`) pra **core**, pra que o Mission Control
(também core) não fique refém de um plugin (decisão A1 do plano Mission
Control). É o mesmo modelo do Coder: em vez de `claude -p` headless e cego,
a sessão abre numa **sala tmux navegável** (`--remote-control`), com um worker
que LANÇA a sala e fica MONITORANDO (status + watcher de morte + heartbeat); o
"resume" injeta input na sala viva via `tmux send-keys`.

O núcleo é **parametrizado** e **sem opinião** sobre quem o usa:

- O nome da sala (prefixo `coder-`/`mission-`/…) é construído pelo CALLER.
- O system prompt é escrito pelo CALLER num arquivo; aqui só apontamos o
  `--append-system-prompt-file`.
- Os GATES são plugáveis: o caller passa (ou não) um `settings_path`. O Coder
  injeta o guard hook dele; o Mission Control **não injeta nenhum** (a missão
  roda em bypass de verdade — decisão do operador, camada B do plano).
- As mensagens ao operador (heartbeat, morte) são **callbacks** do caller —
  o núcleo não conhece prefixos `[coder]`/`[mission]`.

Camadas (cada uma testável em isolado):

- `state`   — leitura/escrita atômica do state JSON, com flock no
              read-modify-write (mata lost-update entre workers concorrentes).
- `tmux`    — wrappers finos do `tmux` + helpers PUROS de leitura de pane
              (`pane_busy`, `extract_pane_last`).
- `room`    — `SalaSpec`, abertura da sala (launcher + new-session), monitor,
              porteiro de prontidão, entrega-com-confirmação, resume.
- `cleanup` — faxina de salas abandonadas (TTL) e contagem de salas ativas,
              com a decisão pura isolada (`should_kill`).
"""

from __future__ import annotations

from bot.sala import cleanup, room, state, tmux
from bot.sala.room import SalaSpec

__all__ = ["state", "tmux", "room", "cleanup", "SalaSpec"]
