"""A salvaguarda do bruto — Highlander v3, F1.

Toda sala do Coder e do Mission Control é uma sessão do Claude Code, e o Claude
Code grava um transcript riquíssimo em
`~/.claude/projects/<projeto>/<session_id>.jsonl`: o pedido do operador, o
raciocínio, cada ferramenta chamada e cada resultado.

**Esse arquivo é apagado em 30 dias.** Medido em 29/08/2026: o mais antigo em
disco era de 30/07, exatamente a janela do `cleanupPeriodDays` padrão. É o
registro mais completo que existe do trabalho de engenharia deste sistema, e ele
estava sendo jogado fora continuamente. Os transcripts das salas de julho — as
que originaram esta missão — já não existem.

Este pacote é o que para de perder:

- `state`     — o estado do coletor, com trava de arquivo.
- `collector` — a cópia incremental por deslocamento de byte.
- `dossier`   — o dossiê por sala, regenerado por acúmulo.
- `source`    — a fonte do Keyko, que roda o coletor por relógio.

**Nada aqui tem "fechar a sala" como gatilho** (decisão E3 do briefing). Toda
captura é contínua, dirigida por relógio ou por acúmulo. Fechar sala é rótulo de
estado, não evento de sistema — e é por isso que uma sala que morre de forma feia
(cota, crash, OOM) não leva junto o registro do que ela fez.
"""
