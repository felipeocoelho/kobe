"""A reconstrução do passado — a varredura que devolve julho.

O PROBLEMA QUE ESTE ARQUIVO EXISTE PARA RESOLVER, E QUE O PLANO NÃO PREVIU
---------------------------------------------------------------------------
LUCIEN nasce com o cursor em zero e um acervo de **3.620 mensagens** atrás dele.
Sem separar as duas leituras, a fonte incremental gastaria semanas mastigando
julho a seis rodadas por hora — e enquanto isso **não veria a conversa de hoje**,
que é justamente o que ela existe para acompanhar.

Daí os **dois cursores** da migration 008, e a divisão de trabalho entre eles:

    incremental      caminha com a conversa, do ponto de partida para a frente
    reconstruction   varre o passado, de trás para o ponto de partida

E daí o `kobe-lucien init`: ele finca o marco, pondo o cursor incremental no
`seq` mais alto de cada tópico. **É o que separa "de agora em diante" de "o que
já aconteceu"** — sem ele, os dois fariam o mesmo trabalho, e a T8 (dedupe)
seguraria a duplicata mas não a cota gasta.

A RECONSTRUÇÃO PARA SOZINHA NO MARCO
-------------------------------------
Ela lê de zero até o cursor incremental **daquele tópico**, e não além. Assim as
duas leituras se encontram exatamente uma vez, sem sobreposição e sem buraco.

RETOMÁVEL, PORQUE VAI SER INTERROMPIDA
---------------------------------------
São ~145 chamadas sobre todo o acervo. Alguma coisa vai interromper: um
`Ctrl+C`, um deploy, a cota. O cursor de reconstrução guarda o progresso por
tópico, então recomeçar continua de onde parou em vez de refazer — e refazer não
seria só lento, seria cota gasta em cima de trabalho já feito.

ORDEM CRONOLÓGICA, OBRIGATORIAMENTE
------------------------------------
Superação só faz sentido lida na ordem em que aconteceu. Ler o passado de trás
para a frente registraria a decisão de julho como se ela tivesse superado a de
agosto — o inverso exato do que a fase existe para consertar.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from bot import lucien as cfg
from bot.lucien import store, worker

logger = logging.getLogger("kobe.lucien.reconstrucao")


@dataclass
class Plano:
    """O que a varredura vai custar, ANTES de gastar cota com ela."""

    topicos: list[dict] = field(default_factory=list)

    @property
    def mensagens(self) -> int:
        return sum(int(t["mensagens"]) for t in self.topicos)

    @property
    def caracteres(self) -> int:
        return sum(int(t["caracteres"] or 0) for t in self.topicos)

    @property
    def lotes(self) -> int:
        return sum(int(t["lotes"]) for t in self.topicos)

    @property
    def tokens_aproximados(self) -> int:
        """Regra de bolso de 4 caracteres por token, mais o prefixo de 13.154
        medido em 30/08. O prefixo entra **uma vez**, não por lote: a medição
        mostrou a 2ª chamada lendo os 13k do cache e criando zero."""
        return self.caracteres // 4 + 13154


def planejar(cx, *, topic_id: Optional[str] = None) -> Plano:
    """Quanto falta reconstruir, por tópico. Não chama modelo nenhum."""
    cur = cx.cursor()
    cur.execute(
        """
        WITH marco AS (
          SELECT topic_id, last_seq FROM lucien_cursor WHERE scope = 'incremental'
        ), feito AS (
          SELECT topic_id, last_seq FROM lucien_cursor WHERE scope = 'reconstruction'
        )
        SELECT t.id AS topic_id,
               COALESCE(t.current_name, '(sem nome)') AS topico,
               COUNT(m.id)              AS mensagens,
               SUM(length(m.content))   AS caracteres,
               COALESCE(f.last_seq, 0)  AS de_seq,
               COALESCE(mc.last_seq, 0) AS ate_seq
          FROM topics t
          LEFT JOIN marco mc ON mc.topic_id = t.id
          LEFT JOIN feito f  ON f.topic_id  = t.id
          JOIN messages m
            ON m.topic_id = t.id
           AND m.role IN ('user', 'assistant')
           AND m.seq >  COALESCE(f.last_seq, 0)
           AND m.seq <= COALESCE(mc.last_seq, 0)
         WHERE (%s::uuid IS NULL OR t.id = %s::uuid)
         GROUP BY t.id, t.current_name, f.last_seq, mc.last_seq
         ORDER BY COUNT(m.id) DESC
        """,
        (topic_id, topic_id),
    )
    linhas = []
    for l in cur.fetchall():
        d = dict(l)
        # O lote é limitado por mensagens E por caracteres; o número maior manda.
        por_msg = -(-int(d["mensagens"]) // cfg.lote_maximo())
        por_chars = -(-int(d["caracteres"] or 0) // cfg.lote_maximo_chars())
        d["lotes"] = max(por_msg, por_chars, 1)
        linhas.append(d)
    return Plano(topicos=linhas)


def fincar_marco(cx, *, refincar: bool = False) -> list[dict]:
    """Põe o cursor incremental no `seq` mais alto de cada tópico. **Só ele.**

    O invariante que se quer é que cada mensagem seja lida exatamente uma vez,
    por exatamente uma das duas leituras. Chamando de `C` a posição atual do
    cursor incremental e de `M` o topo do tópico:

        já lido pela leitura corrente   (0, C]
        a reconstruir                   (C, M]
        leitura corrente daqui pra frente   (M, ∞)

    O ATO QUE ESTA FUNÇÃO NÃO FAZ MAIS, E POR QUE — 31/08/2026
    -----------------------------------------------------------
    Ela também punha o cursor de RECONSTRUÇÃO em `C`, para que a varredura não
    refizesse `(0, C]` — trecho que a leitura corrente já teria processado. A
    intenção era boa e o efeito, na segunda execução, era **apagar o passado a
    reconstruir em silêncio**: com o incremental já no topo, `C` É o topo, o
    intervalo `(C, M]` vira vazio e `planejar()` passa a responder *"nada
    pendente"* — com a mesma cara de quem terminou o trabalho. Visto ao vivo em
    30/08/2026, ligando o LUCIEN: o backlog de 3.595 mensagens do dev sumiu num
    `init` rodado como pré-condição de um roteiro.

    E a docstring de então **prometia idempotência**, o que fazia quem lesse
    rodar de novo com confiança. Era verdade para o cursor incremental (o
    `GREATEST` do `_avancar_cursor` nunca anda para trás) e falsa para o outro,
    onde andar para a frente É o dano.

    Não bastava "só gravar se ainda não existir": a produção escapou justamente
    porque lá o cursor de reconstrução **nunca chegou a ser criado** (o
    incremental estava em zero na primeira fincada, e a guarda `if ja_lido > 0`
    segurou). Uma regra baseada em existência deixaria a bomba armada para o
    `init` seguinte. O invariante que sobra é o mais forte e o mais simples:

        **o cursor de reconstrução nunca sobe por obra do `init`.**
        Ele só sobe quando a reconstrução de fato leu.

    Quem quiser o efeito antigo pede por escrito, com `refincar=True` — e aí é
    ato explícito de quem sabe que aquele `(0, C]` já foi lido. Sem ele, o preço
    é a varredura reler `(0, C]`: **cota gasta, nunca registro perdido** (a T8
    segura a duplicata). É a assimetria certa para um comando cujo modo de falha
    era silencioso e parecia sucesso.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT t.id AS topic_id, COALESCE(t.current_name,'(sem nome)') AS topico,"
        "       MAX(m.seq) AS topo, COUNT(m.id) AS mensagens,"
        "       COALESCE(c.last_seq, 0) AS ja_lido,"
        "       COALESCE(r.last_seq, 0) AS reconstruido_ate,"
        "       (r.topic_id IS NOT NULL) AS tem_cursor_reconstrucao"
        "  FROM topics t JOIN messages m ON m.topic_id = t.id"
        "  LEFT JOIN lucien_cursor c"
        "    ON c.topic_id = t.id AND c.scope = 'incremental'"
        "  LEFT JOIN lucien_cursor r"
        "    ON r.topic_id = t.id AND r.scope = 'reconstruction'"
        " WHERE m.role IN ('user','assistant')"
        " GROUP BY t.id, t.current_name, c.last_seq, r.topic_id, r.last_seq"
        " ORDER BY 4 DESC"
    )
    marcos = cur.fetchall()
    saida = []
    for m in marcos:
        alvo = str(m["topic_id"])
        d = dict(m)
        d["refincado"] = bool(refincar and int(m["ja_lido"]) > 0)
        if d["refincado"]:
            store._avancar_cursor(cx, "reconstruction", alvo, int(m["ja_lido"]))
        store._avancar_cursor(cx, "incremental", alvo, int(m["topo"]))
        saida.append(d)
    return saida


def rodar(
    *,
    conninfo: str,
    kobe_home: str,
    topic_id: Optional[str] = None,
    max_lotes: int = 5,
    pausa_s: float = 2.0,
    ao_terminar_lote: Optional[Callable] = None,
) -> list:
    """A varredura, lote a lote, em ordem cronológica.

    `max_lotes` é teto duro e obrigatório — **não existe "rode até acabar"**. É o
    que torna o piloto de 5 lotes possível sem inventar um modo especial: o
    piloto é a mesma função com um número menor.
    """
    resultados = []
    seguidas = 0
    limite_seguidas = cfg.falhas_seguidas_max()
    for i in range(max_lotes):
        cx = store.conectar(conninfo)
        try:
            alvo = topic_id or _proximo_topico(cx)
            if alvo is None:
                logger.info("reconstrução: nada mais a fazer")
                break
            if not _falta(cx, alvo):
                if topic_id:
                    logger.info("reconstrução: este tópico chegou ao marco")
                    break
                alvo = _proximo_topico(cx)
                if alvo is None:
                    break
        finally:
            cx.close()

        r = worker.uma_rodada(
            conninfo=conninfo,
            kobe_home=kobe_home,
            topic_id=alvo,
            scope="reconstruction",
            reconstrucao=True,
            respeitar_cota=False,
        )
        resultados.append(r)
        logger.info("reconstrução [%d/%d]: %s", i + 1, max_lotes, r.resumo())
        if ao_terminar_lote:
            ao_terminar_lote(i + 1, r)
        if r.erro and "outra rodada" in (r.erro or ""):
            break

        # ── O freio de falha repetida ────────────────────────────────────
        # Medido em 30/08/2026, e foi o próprio uso que ensinou: um limite
        # transitório do modelo derrubou **70 lotes seguidos em 4 minutos**,
        # cada um falhando em ~3,5 s. O desenho segurou — nada foi gravado e o
        # cursor não andou —, mas o laço queimou o orçamento inteiro de lotes
        # numa falha que não ia se curar na iteração seguinte, três segundos
        # depois.
        #
        # Falha isolada é ruído e não interrompe nada. Falhas SEGUIDAS são outra
        # coisa: são um sintoma, e insistir contra elas é gastar as vagas que a
        # retomada vai precisar.
        if r.erro:
            seguidas += 1
            if seguidas >= limite_seguidas:
                logger.warning(
                    "reconstrução: %d falhas seguidas — parando. A varredura é "
                    "RETOMÁVEL: o cursor não andou, e rodar de novo continua "
                    "exatamente daqui. Último erro: %s",
                    seguidas, r.erro,
                )
                break
            # Espera crescente entre falhas: se for limite de taxa, insistir no
            # mesmo ritmo é o que mantém o limite fechado.
            time.sleep(pausa_s * (2 ** seguidas))
            continue

        seguidas = 0
        if i + 1 < max_lotes:
            # Uma pausa curta entre lotes. Não é educação com o servidor: é para
            # que um `Ctrl+C` tenha onde cair sem interromper uma escrita.
            time.sleep(pausa_s)
    return resultados


def _proximo_topico(cx) -> Optional[str]:
    """O tópico com mais passado por reconstruir. **Maior primeiro**, ao
    contrário do incremental (que vai pelo mais antigo): aqui não há justiça a
    fazer entre tópicos — há dívida a pagar, e a maior dívida é onde está o
    valor."""
    plano = planejar(cx)
    for t in plano.topicos:
        if int(t["mensagens"]) > 0:
            return str(t["topic_id"])
    return None


def _falta(cx, topic_id: str) -> bool:
    plano = planejar(cx, topic_id=topic_id)
    return plano.mensagens > 0
