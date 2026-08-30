"""Busca sobre a conversa — o que faz `kobe-remember` existir (Highlander v3, F2).

O PROBLEMA, EM UMA FRASE
------------------------
Toda pergunta sobre o passado era respondida de memória, e é daí que sai o
achismo. O que faltava não era um modelo melhor: era um **índice** sobre o que
foi realmente dito, e um número que o operador possa conferir.

AS TRÊS PERNAS, E POR QUE SÃO TRÊS
-----------------------------------
Três tipos de pergunta pedem estruturas diferentes. Isso foi **medido** numa
bancada com o acervo real (3.558 mensagens, 7.706 trechos) antes de virar
código, e a medição derrubou quatro versões do desenho:

1. **Literal** (`consulta.literais` → `ILIKE` sobre o índice trigrama).
   O dicionário `portuguese` faz *stemming*, e stemming em identificador é
   destruição: `kobe-recall-since` vira `kobe-recall-sinc` + `recall` + `sinc`,
   e aí `sinc` casa com "sincronizar". Medido: a busca por palavra devolveu
   resultados sobre **imagem no WhatsApp**. Custo da perna literal: 2 a 11 ms.
2. **Palavra** (`consulta.radicais` → `search_tsv`). Acha flexão, mas **só
   ordena** — não vota sobre existir (vide abaixo).
3. **Sentido** (`message_chunks.embedding`). É a única que acha paráfrase:
   *"impedir que a produção rodasse uma versão diferente da que o git dizia"*
   achou as mensagens certas de 12/06 sem compartilhar um termo com elas.

QUEM VOTA SOBRE "EXISTE OU NÃO" — e por que a de palavra NÃO vota
-----------------------------------------------------------------
O critério que reprova a fase inteira é a recusa: assunto que não existe tem que
produzir *"não tenho registro disso"*. Isso exige um **piso**, e medir qual perna
sustenta um piso foi a parte mais útil da bancada.

Medido sobre 16 perguntas (8 com resposta no acervo, 8 sobre assuntos que nunca
existiram — Salesforce, dieta da filha, viagem ao Japão, aluguel, maratona,
Kubernetes, financiamento de carro, aulas de piano):

    massa de IDF da perna de PALAVRA
      com resposta : [0,00  0,00  7,09  7,34  8,92  10,61  11,80  14,55]
      sem resposta : [3,06  5,91  7,16  7,56  7,60   8,89   8,90   8,95]
      → as faixas se sobrepõem. NÃO separa.

Duas perguntas legítimas tiraram **zero**, e quatro perguntas sobre assuntos
inexistentes tiraram entre 7,5 e 9 — porque "Japão", "piano" e "maratona"
existem no acervo, soltas, em contextos que não têm nada a ver. **Raridade não é
relevância.** Por isso:

    existe = (sentido acima do piso) OU (a perna literal achou o identificador)

A perna de palavra continua valiosa — é ela que coloca o trecho certo no topo —
mas não tem voto sobre existir. Um desenho em OU entre as três deixaria passar a
classe "Salesforce", que é exatamente a que não pode passar.

O RESÍDUO, NOMEADO EM VEZ DE ESCONDIDO
---------------------------------------
A perna literal responde *"a palavra aparece"*, não *"existe decisão sobre
isso"*. `Salesforce`, `Kubernetes` e `maratona` dão zero no acervo; `Japão` dá 7
e `piano` dá 2, soltas. Quando um resultado vem **só** da perna literal e o
sentido ficou abaixo do piso, ele sai rotulado `MENÇÃO LITERAL SEM APOIO` — e a
regra do `CLAUDE.md` obriga o agente a reportar exatamente isso, nunca a costurar
menções soltas numa resposta.
"""
