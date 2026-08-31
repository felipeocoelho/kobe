"""O prompt do LUCIEN e o contrato de saída.

O QUE SE PEDE AO MODELO, E O QUE NÃO SE PEDE
---------------------------------------------
Pede-se **julgamento de linguagem**: isto que foi dito estabelece alguma coisa
durável? isto contradiz alguma das que já valem?

Não se pede — e não se aceita — nada que o código saiba melhor:

- **a data de vigência** sai do `created_at` da mensagem de origem;
- **a confiança** sai de corroboração (há evidência além da origem?);
- **o identificador** da afirmação superada é um apelido que o próprio prompt
  atribuiu, não um UUID que o modelo teria de copiar certo.

Perguntar ao modelo qualquer uma dessas três seria pedir que ele adivinhasse
algo que está do lado, escrito, no banco.

POR QUE O APELIDO (`E1`) E NÃO O UUID
--------------------------------------
Um UUID de 36 caracteres gasta contexto, convida a erro de cópia e — o pior —
um UUID quase-certo é indistinguível de um certo até a chave estrangeira
estourar. Um apelido inexistente (`E9` quando só houve `E1`–`E5`) é recusado sem
ambiguidade nenhuma, e o número de apelidos possíveis é pequeno o bastante para
que inventar um seja improvável e detectável.

A INSTRUÇÃO MAIS IMPORTANTE DO PROMPT
--------------------------------------
*"Só cite `#número` que esteja na lista abaixo."* Ela não é a garantia — a
garantia é a trava T1 em `store.py`, que descarta o que não estiver. Ela existe
para que o modelo **acerte** em vez de ser recusado: uma trava que dispara muito
gasta cota e enche o relatório de ruído.
"""

from __future__ import annotations

from bot.lucien.models import Lote

CONTRATO = """\
Responda SOMENTE com um objeto JSON, sem texto antes nem depois, neste formato:

{
  "claims": [
    {
      "subject": "assunto curto, 3 a 80 caracteres",
      "statement": "a afirmação, UMA frase, 20 a 400 caracteres",
      "kind": "decision | open | preference | fact",
      "source_seq": 1234,
      "evidence_seqs": [1234, 1236],
      "supersedes": ["E1"],
      "supersede_reason": "por que esta supera aquela (obrigatório se supersedes)",
      "legibility_doubt": false,
      "legibility_reason": "só se legibility_doubt=true: que trecho está ilegível"
    }
  ],
  "closures": [
    {"claim_id": "E2", "action": "closed | abandoned",
     "source_seq": 1240, "reason": "por que deixou de estar aberta"}
  ],
  "nothing_durable": false
}

REGRAS DURAS:
- `source_seq` e todo `evidence_seqs` TÊM que ser números que aparecem na
  conversa abaixo. Número de fora é DESCARTADO — não invente, não estime, não
  cite de memória.
- `supersedes` e `closures.claim_id` só aceitam apelidos (E1, E2, …) da lista
  "O QUE JÁ VALE". Apelido que não está lá é DESCARTADO.
- Se nada durável foi estabelecido, devolva `{"claims": [], "closures": [],
  "nothing_durable": true}`. **Isso é a resposta certa na maioria das vezes** —
  a maior parte de uma conversa não estabelece nada permanente.
- NÃO invente data, NÃO invente confiança, NÃO repita o que já vale sem mudança.
"""

LEGIBILIDADE = """\
SOBRE `legibility_doubt` — O ÚNICO JUÍZO DE CONFIANÇA QUE SE PEDE A VOCÊ

A confiança de cada afirmação é calculada pelo código, por corroboração. A ÚNICA
coisa que você decide é se há **dúvida de legibilidade**, e ela só REBAIXA.

A PERGUNTA CERTA, e ela é por AFIRMAÇÃO e não por mensagem:

    **o trecho corrompido é justamente o que SUSTENTA esta afirmação?**

Não é "esta mensagem veio de áudio?". Não é "há erro de transcrição nesta
mensagem?". Se a palavra deturpada está longe do que sustenta a afirmação, NÃO
rebaixe. Se o trecho corrompido É o que sustenta — o número, o nome do arquivo,
a decisão, o identificador —, rebaixe.

EXEMPLO REAL, e ele é o caso que mais confunde. Uma transcrição chegou como
*"o fato de vídeo e áudio precisa ser levado em consideração"*, onde "vídeo" é
ruído sobre "de vir de". Há corrupção literal na frase — e ela **NÃO** contamina
a afirmação, porque o sentido é recuperável e a decisão não depende daquela
palavra. Este caso NÃO é dúvida de legibilidade.

MARQUE `legibility_doubt: true` quando:
- nome próprio, termo técnico ou identificador aparece deturpado DENTRO do que
  sustenta a afirmação (este acervo tem "Raul" por "Hal", "DevCube" por
  "Dev Kobe", "Koby" e "Filipe", "Cade" por "Kobe");
- número, data, versão, caminho de arquivo ou valor veio de áudio — família em
  que o erro de transcrição é silencioso e o dano é alto;
- a frase que sustenta está truncada, ou a pontuação troca o sentido dela;
- há antecedente ambíguo ("isso", "aquilo", "aquele negócio") sem referente
  resolvível dentro do trecho mostrado.

NÃO marque quando:
- a mensagem é curta, inequívoca e coerente com o contexto ("sim, plano
  aprovado", "pode subir", "não faz isso");
- o ruído está em parte da frase que NÃO sustenta a afirmação;
- o termo técnico está grafado corretamente e coerente com o resto do lote;
- **a mensagem simplesmente veio por áudio.** Este, sozinho, NUNCA é motivo.

O operador usa áudio como canal principal, por escolha. Marcar tudo que vem de
voz não protege ninguém: transforma a ressalva em ruído e ela deixa de
significar alguma coisa. O alvo é transcrição RUIM, não voz.
"""

O_QUE_E_DURAVEL = """\
O QUE CONTA COMO DURÁVEL (e o que não conta):

  decision   — algo foi DECIDIDO. "vamos fazer X", "fica assim", "aprovado".
  open       — algo ficou EM ABERTO, esperando decisão ou execução.
  preference — como o operador quer as coisas, de forma permanente.
  fact       — um fato do sistema ou do projeto que continua valendo.

NÃO conta: pergunta sem resposta, conversa em andamento, hipótese sendo
explorada, opinião de passagem, relato do que aconteceu num turno, saudação,
pedido pontual que já foi atendido ali mesmo. Na dúvida, NÃO registre — o
registro de estado é curto de propósito, e uma linha a mais custa mais que uma
linha a menos.

A SEGUNDA PERGUNTA É A QUE MAIS IMPORTA, e é a que hoje não existe em lugar
nenhum: alguma coisa dita aqui CONTRADIZ, FECHA ou ABANDONA alguma das que já
valem? Se o operador mudou de ideia, isso é `supersedes`. Se o que estava aberto
foi resolvido, isso é `closures`. Sem isso, decisões velhas voltam à mesa como
se estivessem em aberto — que é exatamente o problema que você existe para
resolver.
"""


def montar(lote: Lote) -> str:
    """O prompt de uma rodada."""
    linhas = [
        "Você é LUCIEN, o arquivista da memória do Kobe.",
        "",
        "Sua tarefa: ler o trecho de conversa abaixo e responder DUAS perguntas.",
        "  1. que afirmações duráveis isto estabelece?",
        "  2. alguma delas contradiz, fecha ou abandona alguma das que JÁ VALEM?",
        "",
        f"Tópico: {lote.topico_nome}",
        "",
        O_QUE_E_DURAVEL,
        "",
        "─" * 60,
        "O QUE JÁ VALE (o estado vigente deste tópico)",
        "─" * 60,
    ]
    if lote.estado:
        for apelido, c in lote.estado.items():
            data = c["valid_from"].strftime("%d/%m/%Y") if c.get("valid_from") else "?"
            linhas.append(
                f"{apelido}  [{c['kind']}] {data} — {c['subject']}: {c['statement']}"
            )
    else:
        linhas.append("(nada ainda — este tópico não tem estado registrado)")

    linhas += [
        "",
        "─" * 60,
        "A CONVERSA NOVA",
        "─" * 60,
    ]
    for m in lote.mensagens:
        marca = " (transcrito de áudio)" if m.audio else ""
        data = m.created_at.strftime("%d/%m/%Y %H:%M")
        linhas.append(f"#{m.seq} · {data} · {m.quem}{marca}:")
        linhas.append(m.content.strip())
        linhas.append("")

    linhas += ["─" * 60, "", LEGIBILIDADE, "", CONTRATO]
    return "\n".join(linhas)
