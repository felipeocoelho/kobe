"""Remede os pisos do "não tenho registro" sobre o acervo do dia.

POR QUE ISTO É ENTREGA, E NÃO SCRIPT DE BANCADA
------------------------------------------------
O piso da busca por sentido separa "achei" de "não achei" por uma folga de
**0,061**. É apertado, e é honesto dizer que ele **envelhece**: conforme o acervo
cresce, tanto a chance de um assunto inexistente encontrar um vizinho parecido
quanto a distribuição de similaridade mudam.

Reconferir isso não pode depender de alguém lembrar de como a bancada foi feita
seis meses atrás. Vira um comando:

    python -m bot.search.calibrar

Ele roda duas listas de perguntas contra o acervo **real** — um grupo que TEM
resposta e um grupo sobre assuntos que nunca existiram — e imprime a folga.
Folga positiva significa que existe piso; folga negativa significa que o sistema
**não consegue mais** distinguir, e aí é hora de mudar de modelo ou de desenho,
não de espremer o número.

⚠️ A CALIBRAGEM CONTAMINA O PRÓPRIO ACERVO — e isto custou um vermelho
-----------------------------------------------------------------------
Rodar este comando (ou a bateria) **escreve as perguntas em `messages`**: elas
passam pelo bot, viram histórico, e o indexador as embedda. Medir logo depois é
medir o **eco**: na primeira vez que isso aconteceu, três perguntas "com
resposta" pontuaram **0,992 / 1,000 / 1,000** — similaridade de 1,0 é a pergunta
encontrando a si mesma, palavra por palavra — e a folga virou **-0,386**, um
falso alarme de "o modelo parou de separar".

Por isso a medição ignora, por padrão, tudo que foi dito na **última hora**
(`JANELA_S`). É larga o bastante para excluir a própria sessão de medição e
curta o bastante para não descartar acervo de verdade. É a mesma lição do
cenário anti-invenção da bateria, que queima o termo que usa: **o acervo é a
conversa, e toda sonda que se roda entra nele.**

AS PERGUNTAS DE CONTROLE
------------------------
As oito do grupo "sem resposta" são deliberadamente **plausíveis para este
operador** — dieta, viagem, aluguel, maratona, financiamento — e não absurdos.
Um controle feito de perguntas exóticas ("o que decidimos sobre física de
partículas?") daria uma folga bonita e falsa: o que precisa ser rejeitado é o
assunto que *poderia* ter sido conversado e não foi.

As do grupo "com resposta" ficam propositalmente sem citar o termo exato do
registro sempre que possível — é a busca por sentido que está sendo medida, não
a por palavra.
"""

from __future__ import annotations

import statistics as st
import sys
from typing import Optional

from bot.search import embedder, query

# Uma hora: exclui a sessão de medição inteira (bateria + calibragem) sem
# descartar acervo real. Vide o aviso acima.
JANELA_S = 3600.0

COM_RESPOSTA = [
    "o que a gente decidiu sobre a arquitetura de borda?",
    "me lembra o que foi conversado sobre rsync",
    "aquela vez que a gente discutiu como impedir que a producao rodasse uma "
    "versao diferente da que o git dizia, o que ficou decidido?",
    "o que ficou decidido sobre aposentar o Chat Manager?",
    "o que a gente combinou sobre o coletor de transcricao das salas?",
    "qual foi a decisao sobre usar o Postgres direto em vez do PostgREST?",
    "o que a gente falou sobre o plugin de WhatsApp?",
    "como ficou a regra de aprovacao de plano do Coder?",
]

SEM_RESPOSTA = [
    "o que a gente decidiu sobre integracao com o Salesforce?",
    "o que a gente combinou sobre a dieta e o plano de nutricao da minha filha?",
    "qual foi a decisao sobre a viagem para o Japao em dezembro?",
    "o que ficou definido sobre o contrato de aluguel do escritorio?",
    "me lembra o que a gente falou sobre treinar para a maratona",
    "o que a gente decidiu sobre migrar tudo para Kubernetes na AWS?",
    "qual foi a conversa sobre o financiamento do carro novo?",
    "o que a gente combinou sobre as aulas de piano?",
]


def _topo(db, pergunta: str, *, janela: float = JANELA_S) -> float:
    vetor = embedder.embed_um(pergunta)
    linhas = query.buscar_sentido(db, vetor, k=1, janela_eco=janela)
    return float(linhas[0]["cos"]) if linhas else 0.0


def medir(db, *, log=print, janela: float = JANELA_S) -> dict:
    """A tabela da decisão, sobre o acervo de agora (menos a última hora)."""
    a = sorted(_topo(db, q, janela=janela) for q in COM_RESPOSTA)
    b = sorted(_topo(db, q, janela=janela) for q in SEM_RESPOSTA)
    folga = min(a) - max(b)
    piso_atual = query.piso_cos()

    log(f"modelo: {embedder.modelo()}  ·  piso em uso: {piso_atual:.3f}"
        f"  ·  ignorando a última {janela / 3600:.0f}h (eco da própria medição)")
    log(f"  COM resposta  n={len(a)}  min={min(a):.3f}  mediana={st.median(a):.3f}  max={max(a):.3f}")
    log(f"  SEM resposta  n={len(b)}  min={min(b):.3f}  mediana={st.median(b):.3f}  max={max(b):.3f}")
    log(f"  FOLGA = {folga:+.3f}")
    log(f"  COM: {[round(x, 3) for x in a]}")
    log(f"  SEM: {[round(x, 3) for x in b]}")

    if folga <= 0:
        log(
            "\n  ⚠️  AS FAIXAS SE SOBREPOEM. Nao existe piso que acerte as 16 —\n"
            "      qualquer valor escolhido erra para um lado. Isto NAO se resolve\n"
            "      ajustando o numero: e sinal de que o modelo (ou o desenho da\n"
            "      busca) parou de separar neste acervo."
        )
    else:
        sugerido = (min(a) + max(b)) / 2
        log(f"\n  piso sugerido (meio da folga): {sugerido:.3f}")
        if not (max(b) < piso_atual < min(a)):
            log(
                f"  ⚠️  o piso EM USO ({piso_atual:.3f}) esta fora da folga\n"
                f"      ({max(b):.3f} a {min(a):.3f}) — ajuste SEARCH_PISO_COS."
            )

    return {
        "com_resposta": a,
        "sem_resposta": b,
        "folga": folga,
        "piso_atual": piso_atual,
        "falsos_positivos": sum(1 for c in b if c >= piso_atual),
    }


def _main(argv: Optional[list] = None) -> int:  # pragma: no cover — CLI
    from bot.config import load_config
    from bot.db import build_client

    db = build_client(load_config())
    r = medir(db)
    return 0 if r["folga"] > 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
