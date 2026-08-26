"""Mission Control — as salas de missao do operador.

Uma **sala de missao** e um turno longo de raciocinio numa janela visivel:
uma sessao `claude` rodando em tmux (`--remote-control`), navegavel no Claude
Code Desktop, com prompt de estrategista. O operador abre por **linguagem
natural** ("abre uma missao sobre X") — nao ha slash command. A sala nunca se
auto-encerra: fica aberta ate ele mandar fechar, pelo Telegram ou digitando
dentro dela.

Modulos:
- `sala_dispatch` — abrir / retomar / encerrar (a porta de entrada).
- `sala_prompt` — montagem do system prompt de estrategista.
- `sala_worker` — processo que monitora a sala e reporta.
- `handoff` — quando a missao vira "vamos CONSTRUIR X", dispara o Coder.
- `storage` — paths, geracao de id e escrita atomica sob `user-data/missoes/<id>/`.

Fonte da verdade do estado de runtime: `user-data/missoes/<id>/sala.json`.

---

NOTA HISTORICA (2026-08-25) — leia antes de mexer em qualquer coisa com
"missao" no nome. Ate esta data, este pacote abrigava **dois** sistemas
distintos que dividiam o mesmo diretorio de estado:

1. **Sistema de Missoes v0.13** — orquestrador que quebrava um pedido em
   subtarefas e mantinha um painel de progresso no Telegram, acionado pelos
   comandos `/missao`, `/missao_status`, `/missao_abortar` e `/missao_lista`,
   com estado em `estado.json` + `eventos.jsonl`. Ultima atividade real:
   09/06/2026. **Aposentado** — palavra do operador: *"e codigo velho, pode
   morrer"*.
2. **As salas de missao** — o que esta descrito acima, e o que o operador usa.

Os dois nunca se enxergaram (a listagem do v0.13 varria `*/estado.json`, e as
salas so gravam `sala.json`), mas dividiam pasta e pacote. Por isso a regra que
sobrevive a eles: **a separacao aqui e por ARQUIVO, nunca por pasta.** Um
`rm -rf` por caminho, ou um "removi o modulo mission_control", mata a
ferramenta viva sem deixar um erro sequer no log.
"""
