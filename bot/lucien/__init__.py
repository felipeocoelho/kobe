"""LUCIEN — o arquivista do registro de estado (Highlander v3, F3).

QUEM ELE É
----------
Uma fonte do daemon Keyko que lê o que entrou de novo na conversa e faz **duas**
perguntas:

    1. que afirmações duráveis isto estabelece?
    2. alguma delas CONTRADIZ, FECHA ou ABANDONA alguma das que já valem?

A segunda é a que hoje não existe em lugar nenhum do Kobe, e é ela que cura a
dor original do projeto: decisões já tomadas voltando à mesa como se estivessem
em aberto.

O nome é do operador — o bibliotecário do Sandman, não "ARQUIVISTA".

A LINHA QUE SEPARA O QUE É DO CÓDIGO E O QUE É DO MODELO
---------------------------------------------------------
Esta é a única fase do Highlander v3 em que **um modelo escreve estado que o
agente depois serve como se fosse conhecido**. Toda a arquitetura deste pacote
responde a esse risco de um jeito só:

    **O modelo NÃO escreve no banco.**

Ele recebe um lote de mensagens e devolve JSON. Quem escreve é `store.py`, que
valida campo por campo — e a validação que importa mais é esta: **um `seq` de
origem que não estava no lote mostrado ao modelo é DESCARTADO**. Ele não pode
citar uma mensagem que não viu.

É o mesmo princípio que o `CLAUDE.md` já aplica aos Alertas: *"a lógica
determinística é do CÓDIGO; o modelo só é invocado para LINGUAGEM"*.

NADA AQUI É ESPECÍFICO DE UM TÓPICO
------------------------------------
Regra do operador, 30/08/2026: *"nada que a gente vai construir é específico do
Dev Kobe, exceto se eu falar o contrário"*. LUCIEN roda igual em todos os
tópicos; o registro é global, com coluna de tópico (decisão E5).

A CHAVE
-------
`LUCIEN_ENABLED=false` no `.env.example`. Desligado, a fonte **não é registrada**
no Keyko (não basta ela existir e não fazer nada — uma fonte que aparece no log
de inicialização sem trabalhar faz "quem o Keyko está observando" deixar de ser
verdade) e as cinco tabelas ficam inertes.
"""

from __future__ import annotations

import os

# ── A chave ──────────────────────────────────────────────────────────────

def _env(nome: str) -> str:
    return (os.environ.get(nome) or "").strip()


def _num(nome: str, padrao: float, *, minimo: float = 0.0) -> float:
    bruto = _env(nome)
    try:
        valor = float(bruto) if bruto else padrao
    except ValueError:
        valor = padrao
    return max(minimo, valor)


def habilitado() -> bool:
    return _env("LUCIEN_ENABLED").lower() in ("1", "true", "yes", "on", "sim")


# ── Quando um lote é devido ──────────────────────────────────────────────
# Dois gatilhos, e o segundo existe por uma razão prática: sem ele, um tópico
# com pouca conversa nunca acumularia o mínimo e ficaria para sempre sem estado.
# É também o que faz a bateria da fase funcionar — as esperas `@60`/`@120` do
# roteiro só significam alguma coisa se a idade dispara sozinha.

def lote_minimo() -> int:
    return int(_num("LUCIEN_BATCH_MIN", 12, minimo=1))


def idade_maxima_s() -> float:
    """Idade da mensagem pendente mais antiga que força um lote mesmo pequeno.
    Padrão de 6 h em produção; em dev a bateria baixa isto para segundos."""
    return _num("LUCIEN_MAX_AGE_S", 21600, minimo=5)


# ── Tetos do lote ────────────────────────────────────────────────────────
# Dois, e não um: 40 mensagens curtas cabem folgadas, 40 mensagens do agente
# (mediana bem maior) não cabem. O que estoura contexto é caractere, não linha.

def lote_maximo() -> int:
    return int(_num("LUCIEN_BATCH_MAX", 40, minimo=1))


def lote_maximo_chars() -> int:
    return int(_num("LUCIEN_BATCH_MAX_CHARS", 60000, minimo=2000))


def estado_maximo() -> int:
    """Quantas afirmações vigentes se mostram ao modelo por rodada."""
    return int(_num("LUCIEN_ESTADO_MAX", 60, minimo=5))


# ── Tetos sobre o que o modelo devolve ───────────────────────────────────

def claims_maximo() -> int:
    """Teto por lote — e ele **não descarta**, ele MANDA DIVIDIR o lote.

    O primeiro valor foi 8, chutado no plano. O piloto de 5 lotes mediu: o modelo
    quis escrever **entre 9 e 15** afirmações por lote de 40 mensagens, e o teto
    bateu em **5 de 5**. Pior que o número errado era o comportamento: as
    excedentes eram cortadas **por posição** — a trava não escolhia, e o que se
    perdia era invisível.

    Decisão do operador em 30/08/2026: *"eu não queria que nada que fosse
    relevante ficasse de fora"*. Então **20**, com folga sobre os 15 medidos, sem
    tornar a trava inerte — 20 afirmações de 40 mensagens continua sendo
    paráfrase, não extração, e continua sendo pego.

    E o excedente deixou de ser descartado: ver `lote_piso()` e
    `bot/lucien/worker.py`.
    """
    return int(_num("LUCIEN_MAX_CLAIMS_POR_LOTE", 20, minimo=1))


def lote_piso() -> int:
    """O menor lote que ainda se divide.

    Batendo no teto, a rodada **não grava nada** e o lote é partido ao meio — o
    cursor não andou, então reprocessar é natural no desenho. A divisão para
    aqui: se nem com 5 mensagens o modelo couber no teto, não é lote grande, é
    **degeneração**, e aí a recusa vira ruidosa em vez de silenciosa.
    """
    return int(_num("LUCIEN_LOTE_PISO", 5, minimo=1))


def profundidade_maxima() -> int:
    """Quantas vezes um lote pode ser partido. 40 → 20 → 10 → 5 são três
    divisões; o teto existe para que um modelo em laço não vire uma árvore de
    chamadas."""
    return int(_num("LUCIEN_DIVISOES_MAX", 3, minimo=0))


STATEMENT_MIN = 20
STATEMENT_MAX = 400
SUBJECT_MIN = 3
SUBJECT_MAX = 80

KINDS = ("decision", "open", "preference", "fact")
STATUS = ("vigente", "superada", "fechada", "abandonada")
ENCERRAMENTOS = ("closed", "abandoned")

# ── A escala de confiança, e o que ela mede ──────────────────────────────
# A primeira versão media o CANAL: `"baixa" if origem.audio else "media"`. Três
# defeitos de uma vez, todos medidos no piloto:
#
#   1. `alta` nunca era escrita por ninguém — nível morto num CHECK de três;
#   2. o operador usa áudio como canal PRINCIPAL, então "baixa" saiu em 27 de 40
#      linhas. Um sinal que aparece em 2 de cada 3 deixa de distinguir;
#   3. e o efeito prático é o pior possível: ou o agente hedgeia tudo — e aí a
#      F3 não cura a doença que existe pra curar —, ou ignora a flag, e a
#      mitigação vira teatro.
#
# A régua nova mede CORROBORAÇÃO, que é do que a confiança de uma afirmação de
# fato depende:
#
#   alta   há evidência ALÉM da mensagem de origem (no piloto: 35 de 40)
#   media  origem única, sem corroboração
#   baixa  o trecho que SUSTENTA a afirmação está ilegível ou ambíguo
#
# O fato de a origem ter vindo de áudio **continua registrado** — ele só deixou
# de SER a confiança. Sai como metadado ("origem transcrita de áudio"), derivado
# de `messages.audio_transcribed` na leitura.
CONFIANCAS = ("alta", "media", "baixa")


# ── O piso do "não tenho estado sobre isso" ──────────────────────────────

def piso_cos() -> float:
    """Similaridade mínima para a busca de ESTADO afirmar que achou.

    **NÃO é o piso da F2, e a diferença foi medida.** O da F2 (0,57) foi
    calibrado sobre `messages` — texto conversacional, longo e ruidoso. As
    afirmações são o oposto: curtas, densas e distintas entre si, e por isso
    separam muito melhor.

    Medido em 30/08/2026 sobre as 40 afirmações do piloto, 8 perguntas com
    resposta no registro e 8 sobre assuntos que nunca existiram:

        com resposta   0,570 – 0,773
        sem resposta   0,253 – 0,289
        FOLGA          +0,281      (contra +0,061 da busca de evidência)

    O default de **0,43** fica no meio dos dois lados medidos — 0,14 de margem
    para cada. Escolher pelo lado de cima (0,57, que é o da F2) põe o limiar
    EXATAMENTE em cima do verdadeiro-positivo mais fraco: foi o que aconteceu na
    primeira tentativa, e a pergunta *"o que a gente decidiu sobre o nome dos
    ambientes"* voltou vazia com a resposta certa a 0,570 no banco.

    A lição é da F2, e é a mesma: **limiar com um lado só medido é chute com
    aparência de critério.**
    """
    return _num("LUCIEN_PISO_COS", 0.43, minimo=0.0)


# ── O modelo e o relógio ─────────────────────────────────────────────────

def modelo(*, reconstrucao: bool = False) -> str:
    """Vazio = o modelo padrão da CLI. Decisão do operador em 30/08/2026:
    `Sonnet` na reconstrução (onde está o volume) e o padrão no incremental
    (onde o volume é baixo e a qualidade é o que importa).

    Sai de `.env` e não do código de propósito — o Kobe não fixa modelo.
    """
    nome = _env("LUCIEN_MODEL_RECONSTRUCAO") if reconstrucao else _env("LUCIEN_MODEL")
    return nome


def timeout_s() -> float:
    return _num("LUCIEN_TIMEOUT_S", 180, minimo=30)


def intervalo_s() -> float:
    """Cadência do tick da fonte no Keyko. Não é a cadência das rodadas — é de
    quanto em quanto tempo se PERGUNTA se há lote devido."""
    return _num("LUCIEN_INTERVAL_S", 300, minimo=30)


def falhas_seguidas_max() -> int:
    """Quantas falhas SEGUIDAS a varredura aguenta antes de parar.

    Falha isolada é ruído. Falhas seguidas são sintoma — e insistir contra elas
    gasta as vagas de lote que a retomada vai precisar. Medido: um limite
    transitório do modelo derrubou 70 lotes em 4 minutos, cada um em ~3,5 s.
    """
    return int(_num("LUCIEN_FALHAS_SEGUIDAS", 3, minimo=1))


def maximo_rodadas_hora() -> int:
    """Teto de cota. LUCIEN não tem pressa; um pico de conversa não pode virar
    um pico de chamadas de modelo."""
    return int(_num("LUCIEN_MAX_RUNS_HORA", 6, minimo=1))
