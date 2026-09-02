"""O boletim quente — o registro de estado projetado em disco, lido de graça.

Highlander v3, F4. Este módulo é a metade de LEITURA: o que o turno faz. A
metade de ESCRITA mora em `bot/lucien/boletim.py`, e a direção da dependência é
só uma — **`lucien` importa `memory`, nunca o contrário**. É isso que garante,
por construção, que o caminho quente não tenha como abrir conexão com o banco.

A PROPRIEDADE QUE DEFINE A FASE É NEGATIVA
-------------------------------------------
Nada acontece no turno. Não há chamada de modelo, não há consulta, não há rede:
é `open()` + `read()`. O conteúdo já foi escolhido atrás, pelo LUCIEN, na rodada
que ele ia fazer de qualquer jeito. Se um dia esta camada precisar perguntar
alguma coisa a alguém para montar o bloco, ela deixou de ser o que é.

O vizinho de natureza é `curated_core.py` — arquivo do disco, teto duro no
código, `None` quando não existe — e o padrão daqui é o de lá, de propósito.

POR QUE 800 TOKENS, E NÃO OS 1.200 DO BRIEFING — medido em 31/08/2026
----------------------------------------------------------------------
O briefing da fase fixou 1.200 como lei. O número era **valor inicial
declarado**, não medição, e medir mudou a resposta — para baixo.

O boletim é escolhido **sem saber a pergunta**. Então, para qualquer turno
específico, parte das linhas é sobre outro assunto por construção. Medindo sobre
os 968 turnos reais do operador no tópico de maior volume, com a régua que a F2
calibrou (`SEARCH_PISO_COS = 0,57`, sobre a própria tabela `messages`):

    tamanho do bloco   fora do assunto (mediana)
        12 linhas               11
        16 linhas               15
        20 linhas               19
        25 linhas (=1.200 t)    24

A pesquisa de 27/08 mede a precisão caindo de ~72% para ~57% com ~20 documentos
irrelevantes. **A 1.200 tokens o bloco cruza esse marco na mediana** — não na
cauda. Daí 800 (~16 linhas), que fica abaixo com folga.

A analogia é frouxa e eu digo que é: o estudo mede *documentos*, e uma linha
daqui é uma frase, que dilui menos. Por isso o marco vale como ordem de
grandeza, e na dúvida se erra para baixo. O teto duro de 1.000 é onde a analogia
para de dar conforto.

E É POR ISSO QUE O BLOCO DE RECÊNCIA É O MENOR DOS TRÊS
--------------------------------------------------------
A mesma medição diz qual bloco merece a vaga. "Pendências abertas" e "o que saiu
de cena" valem **justamente por serem independentes da pergunta**: são a cura da
dor original do projeto (decisão fechada voltando à mesa como aberta), e valem
mesmo quando o turno é sobre outra coisa. Já "o que vale hoje", escolhido por
recência pura, é o único bloco aberto — e o de pior perfil de ruído. Por isso
ele encolheu de 55% para 35%, e os outros dois cresceram.

DEGRADAR EM VOZ ALTA
---------------------
Duas coisas o bloco nunca faz em silêncio: **cortar** e **faltar**. O rodapé diz
quantas afirmações ficaram fora do recorte e por onde alcançá-las, porque um
bloco de 16 linhas *parece* o registro inteiro — e "não está no boletim" virando
"não existe" é exatamente a mentira que o Highlander existe para não contar.
Ausência do arquivo, por outro lado, é `None`: o turno segue como antes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger("kobe.memory.boletim")


def _num(nome: str, padrao: float, *, minimo: float, maximo: float) -> float:
    """Lê um parâmetro do ambiente e o PRENDE numa faixa sã.

    A trava não é burocracia. Estes números não são política, são **medida**:
    um teto de 20.000 tokens não é "configuração agressiva", é o bloco comendo
    o prompt; e uma régua de 10 chars/token não relaxa um orçamento, faz o
    código **contar errado** e estourar o teto achando que respeitou.

    Valor fora da faixa é preso e **registrado**, nunca corrigido em silêncio —
    parâmetro cujo erro é mudo é o defeito que este sistema já cometeu antes.
    """
    bruto = (os.environ.get(nome) or "").strip()
    if not bruto:
        return padrao
    try:
        valor = float(bruto)
    except ValueError:
        logger.warning("boletim: %s=%r não é número — usando %s", nome, bruto, padrao)
        return padrao
    preso = max(minimo, min(maximo, valor))
    if preso != valor:
        logger.warning(
            "boletim: %s=%s está fora da faixa [%s, %s] — preso em %s",
            nome, valor, minimo, maximo, preso,
        )
    return preso


# ── O orçamento, na unidade em que a lei foi escrita ──────────────────────
# O teto se declara em TOKENS porque é assim que o orçamento do prompt é
# discutido; o mecanismo trabalha em CHARS porque é o que o código consegue
# contar sem mentir. A razão entre os dois é conservadora de propósito: erro
# seguro é supor POUCOS chars por token, o que aperta o teto em chars e deixa o
# consumo real de token abaixo do orçamento. Português com acento tokeniza pior
# que inglês (3,5–4,2 chars/token é a faixa usual); 3,2 fica abaixo do piso
# dela, então a conta erra para o lado que não fura.
#
# Nada de tokenizer de verdade aqui, e a razão é dupla: o único exato seria uma
# chamada de rede (desqualificada pela definição da fase), e o `tiktoken` seria
# preciso sobre a coisa errada — é o BPE da OpenAI, não o tokenizer do Claude.
BOLETIM_TOKEN_BUDGET = int(_num("BOLETIM_TOKEN_BUDGET", 800, minimo=400, maximo=1000))
BOLETIM_CHARS_POR_TOKEN = _num("BOLETIM_CHARS_POR_TOKEN", 3.2, minimo=2.5, maximo=5.0)
BOLETIM_CHAR_LIMIT = int(BOLETIM_TOKEN_BUDGET * BOLETIM_CHARS_POR_TOKEN)

# Os pesos dos três blocos — ver o cabeçalho para o porquê da redistribuição.
# A sobra de um bloco magro escorre para os seguintes (`_repartir`), então peso
# é reserva mínima, não teto.
PESO_PENDENCIAS = 0.40
PESO_VIGENTES = 0.35
PESO_SAIU = 0.25

# Uma afirmação pode ter até 400 chars (o CHECK da migration 008). Uma linha de
# 400 comeria 15% do orçamento sozinha e deixaria o bloco com meia dúzia de
# vozes. Cortar o texto longo é o que mantém o bloco plural.
TEXTO_MAXIMO = 180

_ROTULO = {
    "decision": "decisão",
    "open": "aberto",
    "preference": "preferência",
    "fact": "fato",
}
_ROTULO_SAIDA = {
    "superseded": "substituída",
    "closed": "fechada",
    "abandoned": "abandonada",
    "reverted": "revertida",
}

_CABECALHO = (
    "[Boletim do tópico — o que o registro de estado tem de mais quente. "
    "Curado por um MODELO (LUCIEN), não é fala literal: cada linha traz a "
    "origem `←#seq`, confira com `kobe-remember --ver <n>` antes de afirmar "
    "ao operador que algo foi decidido.]"
)


def habilitado() -> bool:
    """A chave, lida do ambiente pelos DOIS lados (turno e worker).

    Uma só, e não duas: meia funcionalidade — escrevendo e não lendo, ou o
    contrário — é pior que desligada, porque produz um arquivo que ninguém usa
    ou um bloco que nunca atualiza.
    """
    return (os.environ.get("BOLETIM_ENABLED") or "true").strip().lower() in (
        "1", "true", "yes", "on", "sim",
    )


@dataclass
class Linha:
    """Uma afirmação vigente, pronta para virar texto."""
    kind: str
    dia: str          # 'dd/mm'
    texto: str
    seq: int


@dataclass
class Saida:
    """Uma afirmação que deixou de valer — o sinal de 'mudamos de ideia'."""
    acao: str
    dia: str          # 'dd/mm' do encerramento
    texto: str
    seq: int


def diretorio(kobe_home: Path) -> Path:
    return Path(kobe_home) / "user-data" / "lucien" / "boletins"


def caminho(kobe_home: Path, topic_id: str) -> Path:
    """O arquivo de um tópico, chaveado pelo **UUID** e não pelo slug.

    Não é gosto, é correção. `topic_manager.get_topic_slug` devolve `None` para
    tópico sem `current_name` (os pré-v0.10), e dois nomes distintos podem
    slugificar igual ("Café" e "Cafe") — o que faria dois tópicos escreverem no
    mesmo arquivo, em silêncio. O `topic_id` não tem nenhum desses buracos, é
    estável a rename, e custa zero: ele já está em escopo nos dois caminhos que
    montam prompt (`telegram_handler` e `resume`).

    A opacidade do nome se resolve onde ela dói — dentro do arquivo, cuja
    primeira linha é o nome do tópico — e não no filesystem.
    """
    return diretorio(kobe_home) / f"{topic_id}.md"


def _cortar(texto: str, teto: int = TEXTO_MAXIMO) -> str:
    texto = " ".join((texto or "").split())
    return texto if len(texto) <= teto else texto[: teto - 1].rstrip() + "…"


def _render_linha(l: Linha) -> str:
    rot = _ROTULO.get(l.kind, l.kind)
    return f"· [{rot} {l.dia}] {_cortar(l.texto)}  ←#{l.seq}"


def _render_saida(s: Saida) -> str:
    rot = _ROTULO_SAIDA.get(s.acao, s.acao)
    return f"· [{rot} {s.dia}] {_cortar(s.texto)}  ←#{s.seq}"


def _repartir(blocos: Sequence[list[str]], disponivel: int) -> list[list[str]]:
    """Reparte o orçamento entre os blocos, em duas passadas.

    **Reserva, e não pool único**, porque com um pool ordenado por recência uma
    rajada de decisões num dia só empurraria toda pendência aberta para fora —
    e pendência aberta é metade da dor que a fase existe para curar.

    **Passada 2**, porque reserva sem escorrimento desperdiça: um tópico sem
    nenhuma pendência aberta deixaria 40% do bloco em branco enquanto houvesse
    linha esperando vaga logo abaixo.
    """
    pesos = (PESO_PENDENCIAS, PESO_VIGENTES, PESO_SAIU)
    escolhidas: list[list[str]] = [[] for _ in blocos]
    gasto = 0

    for i, (linhas, peso) in enumerate(zip(blocos, pesos)):
        reserva = int(disponivel * peso)
        usado = 0
        for texto in linhas:
            custo = len(texto) + 1
            if usado + custo > reserva:
                break
            escolhidas[i].append(texto)
            usado += custo
        gasto += usado

    sobra = disponivel - gasto
    for i, linhas in enumerate(blocos):
        for texto in linhas[len(escolhidas[i]):]:
            custo = len(texto) + 1
            if custo > sobra:
                break
            escolhidas[i].append(texto)
            sobra -= custo
    return escolhidas


def montar(
    *,
    topico: str,
    apurado_ate: str,
    pendencias: Sequence[Linha],
    vigentes: Sequence[Linha],
    saiu_de_cena: Sequence[Saida],
    total_vigentes: int,
    limite: Optional[int] = None,
) -> Optional[str]:
    """O texto do boletim. Função **pura** — nenhum I/O, nenhum relógio.

    Pureza aqui não é elegância, é o que dá idempotência de graça: se cada byte
    é função só do que veio do banco, gerar duas vezes sem conversa nova produz
    bytes idênticos, e a escrita nem acontece. Repare que não há `now()` em
    lugar nenhum — o `apurado_ate` é a marca d'água do próprio registro, não o
    relógio de quem gerou. Foi essa escolha que dispensou guardar metadado de
    geração (e, com ele, uma migration).

    `None` quando não há nada a dizer: bloco vazio no prompt é ruído com cara
    de informação.
    """
    if limite is None:
        limite = BOLETIM_CHAR_LIMIT
    if not pendencias and not vigentes and not saiu_de_cena:
        return None

    titulos = (
        "PENDÊNCIAS ABERTAS",
        "O QUE VALE HOJE",
        "O QUE SAIU DE CENA",
    )
    blocos = [
        [_render_linha(l) for l in pendencias],
        [_render_linha(l) for l in vigentes],
        [_render_saida(s) for s in saiu_de_cena],
    ]

    # A moldura (cabeçalho, títulos, rodapé) sai do orçamento ANTES das linhas.
    # Se ela saísse depois, o teto declarado seria mentira — o bloco entregaria
    # sempre um pouco mais do que promete, que é o erro para o lado que fura.
    moldura = (
        len(f"# {topico}") + len(_CABECALHO)
        + len(f"(apurado até {apurado_ate})")
        + sum(len(t) + 4 for t in titulos)
        + 220  # a linha de recorte, com folga
    )
    disponivel = max(0, limite - moldura)
    escolhidas = _repartir(blocos, disponivel)

    partes = [f"# {topico}", _CABECALHO, f"(apurado até {apurado_ate})"]
    for titulo, linhas in zip(titulos, escolhidas):
        if not linhas:
            continue
        partes.append("")
        partes.append(titulo)
        partes.extend(linhas)

    # O que se compara com `total_vigentes` são as linhas VIGENTES (blocos 1 e
    # 2) — nunca o total de linhas. O terceiro bloco é feito de afirmações
    # ENCERRADAS, que por definição não estão no acervo vigente: contá-las aqui
    # produzia o absurdo "3 linhas de 2 afirmações vigentes", visto no primeiro
    # smoke contra banco de verdade em 01/09/2026. Um rodapé que se contradiz
    # destrói a confiança justamente na linha que existe para ser confiável.
    mostradas_vigentes = len(escolhidas[0]) + len(escolhidas[1])
    mostradas_saida = len(escolhidas[2])
    if not mostradas_vigentes and not mostradas_saida:
        return None

    # O recorte, dito em voz alta. Sem esta linha, 16 linhas PARECEM o registro
    # inteiro e "não está no boletim" vira "não existe" — a mesma doutrina do
    # `SEM REGISTRO PARCIAL` do kobe-remember, aplicada do outro lado.
    extra = f", mais {mostradas_saida} mudança(s) recente(s)" if mostradas_saida else ""
    partes.append("")
    partes.append(
        f"[recorte por recência: {mostradas_vigentes} de {total_vigentes} "
        f"afirmação(ões) vigente(s) deste tópico{extra}. O resto NÃO sumiu — "
        f"`kobe-remember \"<assunto>\" --estado` alcança tudo. "
        f"Não conclua ausência a partir daqui.]"
    )
    return "\n".join(partes)


def carregar(kobe_home: Path, topic_id: Optional[str]) -> Optional[str]:
    """O boletim do disco, ou `None`. **Nunca levanta.**

    Todo caminho de falha vira `None` — arquivo ausente (o caso normal num
    tópico que o LUCIEN ainda não leu), vazio, sem permissão, bytes inválidos.
    Um bloco a menos é um prompt igual ao de ontem; uma exceção aqui seria um
    turno perdido por causa de um arquivo de conveniência.

    O teto é aplicado **de novo** na leitura, e não é redundância: o arquivo
    pode ter sido editado à mão, ter vindo de uma versão anterior com outro
    orçamento, ou ter sido corrompido. O orçamento do prompt não pode depender
    da boa conduta de um arquivo em disco.
    """
    if not topic_id or not habilitado():
        return None
    try:
        alvo = caminho(kobe_home, topic_id)
        if not alvo.is_file():
            return None
        conteudo = alvo.read_text(encoding="utf-8").strip()
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("boletim: falhou lendo o boletim de %s: %s", topic_id, exc)
        return None
    if not conteudo:
        return None
    if len(conteudo) > BOLETIM_CHAR_LIMIT:
        logger.warning(
            "boletim: arquivo de %s tem %d chars e o teto é %d — truncando na "
            "leitura (arquivo editado à mão ou de outro orçamento?)",
            topic_id, len(conteudo), BOLETIM_CHAR_LIMIT,
        )
        conteudo = (
            conteudo[: BOLETIM_CHAR_LIMIT].rstrip()
            + "\n[… boletim truncado no teto do prompt …]"
        )
    return conteudo
