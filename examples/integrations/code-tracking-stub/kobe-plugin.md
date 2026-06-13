---
name: code-tracking-stub
visibility: public
version: 0.1.0
description: "Plugin de EXEMPLO — provê a capacidade code-tracking com um handler stub, só pra demonstrar e testar a switchboard do Kobe Integrations ponta-a-ponta. Não faz nada de real: ecoa um card_id fake. Use como molde pra escrever um provedor de verdade."
integrations:
  provides:
    - capability: code-tracking
      handler: bin/track
  consumes: []
---

# code-tracking-stub — provedor de exemplo da capacidade `code-tracking`

Este plugin existe só pra **documentar e testar** o mecanismo do Kobe
Integrations. Ele se anuncia como provedor da capacidade `code-tracking`
e responde os dois verbos do contrato (`ensure`, `finished`) com dados
falsos.

Não é instalado em produção — vive em `examples/` como referência viva.
Pra virar um provedor de verdade (ex: o plugin Flow registrando cards de
verdade), basta trocar o conteúdo de `bin/track` pela lógica real,
mantendo o mesmo contrato de entrada/saída.

Veja `docs/integrations/code-tracking.md` (contrato da capacidade) e
`docs/plugins-autoria.md` (como prover/consumir uma capacidade).
