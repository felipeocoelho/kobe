"""Uma rodada do LUCIEN, de ponta a ponta.

QUEM CHAMA
----------
- A fonte do Keyko, como **subprocesso detached** (`source.py`).
- O `kobe-lucien rodada`, à mão.
- A reconstrução do passado, em laço (`reconstrucao.py`).

POR QUE SUBPROCESSO, E NÃO TRABALHO DENTRO DO `tick()`
--------------------------------------------------------
As outras fontes que fazem trabalho colateral (o coletor de transcripts, o
indexador de busca) o fazem dentro do próprio `tick()`, e está certo: copiar
bytes e pedir vetor levam milissegundos. Uma chamada de modelo leva **dezenas de
segundos**, e o Keyko é single-threaded — LUCIEN travando o laço travaria os
**Alertas**, que são a peça do sistema em que atraso é falha visível para o
operador.

AS FRONTEIRAS DE TRANSAÇÃO, E POR QUE SÃO TRÊS
-----------------------------------------------
1. A linha da rodada é aberta e **comitada na hora**. Se o processo morrer no
   meio da chamada ao modelo, a rodada fica registrada como não-terminada em vez
   de sumir — e `kobe-lucien status` consegue mostrar isso.
2. A chamada ao modelo acontece **fora de transação**. Segurar uma transação
   aberta por 60 s prenderia recursos do banco à espera de uma coisa que nem é
   do banco.
3. A escrita é uma transação só: afirmações, superações, eventos e cursor — ou
   tudo, ou nada.

A T9, QUE MORA AQUI
-------------------
Qualquer falha entre a montagem do lote e a escrita — modelo fora, resposta
torta, timeout — descarta a rodada inteira e **NÃO avança o cursor**. O mesmo
lote é relido na próxima passada. Meia rodada gravada seria um buraco permanente
no registro, e ninguém saberia onde.

A DIVISÃO DO LOTE, E POR QUE ELA SUBSTITUIU O DESCARTE
--------------------------------------------------------
Batendo no teto de afirmações, a versão anterior cortava **as últimas da lista**.
Medido no piloto: o teto bateu em **5 de 5 lotes** e levou 20 afirmações embora,
escolhidas por posição. É o pior comportamento possível numa trava — ela não
escolhe, e o que se perde é invisível.

Agora: nada é gravado, o cursor não anda, e o lote é **partido ao meio**. Cada
metade vira uma rodada própria, com chamada e linha de registro próprias.
Reprocessar é natural aqui porque o cursor não avançou — o desenho já previa
releitura.

A divisão para no **piso de 5 mensagens** (`LUCIEN_LOTE_PISO`). Se nem com 5 o
modelo couber no teto, não é lote grande: é degeneração, e aí a recusa T7 é
**ruidosa** — vai para o operador, não só para o log. É o único caminho em que
algo se perde, e ele grita.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

from bot import lucien as cfg
from bot.lucien import aviso, brain, store
from bot.lucien.models import Lote, Proposta, ResultadoDaRodada

logger = logging.getLogger("kobe.lucien.worker")


def _kobe_home() -> str:
    return os.environ.get("KOBE_HOME") or str(
        __import__("pathlib").Path(__file__).resolve().parents[2]
    )


def _conninfo() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL ausente. LUCIEN não tem banco default de propósito — "
            "apontar para o banco errado tem que custar um ato explícito."
        )
    return url


def cota_disponivel(cx) -> bool:
    """Teto de rodadas por hora. LUCIEN não tem pressa; um pico de conversa não
    pode virar um pico de chamadas de modelo."""
    cur = cx.cursor()
    cur.execute(
        "SELECT COUNT(*) AS n FROM lucien_runs"
        " WHERE mode = 'incremental' AND started_at > NOW() - INTERVAL '1 hour'"
    )
    return int(cur.fetchone()["n"]) < cfg.maximo_rodadas_hora()


def escolher_topico(cx, *, scope: str = "incremental") -> Optional[dict]:
    """O tópico com lote devido há mais tempo.

    **Um por rodada, e o mais antigo primeiro.** Assim um tópico movimentado não
    faz os outros esperarem para sempre — e nada aqui é específico de um tópico,
    que é regra do operador.
    """
    for linha in store.topicos_pendentes(cx, scope=scope):
        if store.lote_devido(linha):
            return linha
    return None


def _processar(cx, lote: Lote, *, kobe_home: str, modo: str, scope: str,
               reconstrucao: bool, profundidade: int = 0) -> ResultadoDaRodada:
    """Um lote: chama o modelo, e grava OU divide.

    Recursiva, com dois freios: o piso de mensagens (`LUCIEN_LOTE_PISO`) e a
    profundidade (`LUCIEN_DIVISOES_MAX`). Sem o segundo, um modelo em laço
    viraria uma árvore de chamadas — e cada nó dela custa cota.
    """
    run_id = store.abrir_rodada(
        cx, mode=modo, topic_id=lote.topic_id, lote=lote,
        model=cfg.modelo(reconstrucao=reconstrucao) or "(padrão)",
    )
    cx.commit()  # fronteira 1 — a rodada existe mesmo se o processo morrer

    # Fronteira 2: a chamada acontece FORA de transação.
    try:
        proposta = brain.pensar(lote, kobe_home=kobe_home, reconstrucao=reconstrucao)
    except brain.CerebroIndisponivel as exc:
        # T9 — a rodada morre, o cursor NÃO anda, o lote é relido.
        r = ResultadoDaRodada(run_id=run_id, erro=str(exc),
                              mensagens_vistas=len(lote.mensagens))
        store.fechar_rodada(cx, run_id, r)
        cx.commit()
        logger.warning("lucien: rodada descartada — %s", exc)
        return r

    metades = _partir(lote)
    pode_dividir = (
        metades is not None and profundidade < cfg.profundidade_maxima()
    )

    # Fronteira 3: ou tudo, ou nada.
    r = store.aplicar(cx, lote, proposta, run_id=run_id, scope=scope,
                      truncar=not pode_dividir)

    if not r.excedeu:
        store.fechar_rodada(cx, run_id, r)
        cx.commit()
        # A recusa T7 no piso é a ÚNICA em que alguma coisa se perde. Por isso
        # ela é barulhenta: vai ao operador, não só ao log. Recusa silenciosa é
        # o mesmo defeito de origem inventada, visto do outro lado.
        for rec in r.recusas:
            if rec.trava == "T7":
                _gritar(kobe_home, rec.motivo)
        return r

    # Passou do teto e dá para dividir: NADA foi gravado e o cursor não andou.
    store.fechar_rodada(cx, run_id, r)
    cx.commit()
    logger.info(
        "lucien: %d afirmações num lote de %d mensagens (teto %d) — dividindo",
        len(proposta.claims), len(lote.mensagens), cfg.claims_maximo(),
    )
    total = ResultadoDaRodada(run_id=run_id, mensagens_vistas=len(lote.mensagens),
                              divisoes=1)
    for metade in metades:
        parcial = _processar(cx, metade, kobe_home=kobe_home, modo=modo,
                             scope=scope, reconstrucao=reconstrucao,
                             profundidade=profundidade + 1)
        if parcial.erro:
            # Uma metade que falha não desfaz a outra: o cursor da que deu certo
            # já avançou, e a que falhou é relida na próxima passada.
            total.erro = parcial.erro
            return total
        total.criadas += parcial.criadas
        total.superadas += parcial.superadas
        total.encerradas += parcial.encerradas
        total.recusas.extend(parcial.recusas)
        total.divisoes += parcial.divisoes
        total.cursor_avancou_para = parcial.cursor_avancou_para or total.cursor_avancou_para
    return total


def _gritar(kobe_home: str, motivo: str) -> None:
    """O aviso de degeneração da T7, pelo caminho de aviso do LUCIEN.

    O corpo mora em `bot/lucien/aviso.py` desde 31/08/2026: a varredura do
    passado passou a precisar do mesmo canal para não morrer calada, e duas
    cópias da regra de destino é como uma delas acaba divergindo em silêncio.
    """
    aviso.avisar(kobe_home, motivo)


def _partir(lote: Lote) -> Optional[list[Lote]]:
    """As duas metades, ou `None` se o lote já está no piso.

    A divisão é **cronológica e contígua**: a primeira metade vai até o meio, a
    segunda do meio ao fim. Assim o cursor de cada uma avança em ordem e as duas
    se encaixam sem buraco nem sobreposição.
    """
    n = len(lote.mensagens)
    piso = cfg.lote_piso()
    if n < 2 * piso:
        return None
    meio = n // 2
    partes = []
    for pedaco in (lote.mensagens[:meio], lote.mensagens[meio:]):
        novo = Lote(topic_id=lote.topic_id, topico_nome=lote.topico_nome,
                    mensagens=pedaco)
        # O estado vigente mostrado é o mesmo — ele não depende do recorte, e
        # remontá-lo custaria uma consulta por metade sem mudar nada.
        novo.estado = lote.estado
        partes.append(novo)
    return partes


def uma_rodada(
    *,
    conninfo: Optional[str] = None,
    kobe_home: Optional[str] = None,
    topic_id: Optional[str] = None,
    scope: str = "incremental",
    reconstrucao: bool = False,
    limite_mensagens: Optional[int] = None,
    respeitar_cota: bool = True,
    dry_run: bool = False,
) -> ResultadoDaRodada:
    """Lê um lote, pensa e grava. Nunca levanta — devolve o resultado com o erro."""
    conninfo = conninfo or _conninfo()
    kobe_home = kobe_home or _kobe_home()
    modo = "reconstruction" if reconstrucao else "incremental"

    cx = store.conectar(conninfo)
    try:
        if not store.travar(cx):
            return ResultadoDaRodada(erro="outra rodada já está acontecendo")
        try:
            if respeitar_cota and not reconstrucao and not cota_disponivel(cx):
                return ResultadoDaRodada(
                    erro=f"teto de {cfg.maximo_rodadas_hora()} rodadas por hora atingido"
                )

            if topic_id is None:
                escolhido = escolher_topico(cx, scope=scope)
                if escolhido is None:
                    return ResultadoDaRodada(erro=None, mensagens_vistas=0)
                topic_id = str(escolhido["topic_id"])

            # A reconstrução para no marco fincado pelo `kobe-lucien init`.
            # Sem isso ela passaria por cima do que a leitura corrente já faz, e
            # as duas gastariam cota no mesmo trecho. O marco é GRAVADO (escopo
            # `marco`) desde 31/08/2026 — antes ele era o cursor incremental lido
            # na hora, e o teto fugia para a frente junto com a conversa.
            teto = store.marco_reconstrucao(cx, topic_id) if scope == "reconstruction" else None
            lote = store.montar_lote(cx, topic_id, scope=scope, teto_seq=teto)
            if limite_mensagens:
                lote.mensagens = lote.mensagens[:limite_mensagens]
            if lote.vazio:
                return ResultadoDaRodada(erro=None, mensagens_vistas=0)

            if dry_run:
                r = ResultadoDaRodada(mensagens_vistas=len(lote.mensagens))
                logger.info(
                    "lucien (ensaio): tópico %s, #%s..#%s, %d mensagens, %d chars",
                    lote.topico_nome, lote.de_seq, lote.ate_seq,
                    len(lote.mensagens), lote.caracteres,
                )
                return r

            r = _processar(cx, lote, kobe_home=kobe_home, modo=modo, scope=scope,
                           reconstrucao=reconstrucao)
            if r.erro:
                return r
            logger.info("lucien [%s]: %s", lote.topico_nome, r.resumo())

            # O vetor vem DEPOIS do commit, e de propósito: uma afirmação não
            # pode deixar de ser gravada porque um serviço de embedding estava
            # fora. Sem vetor ela ainda é achável por palavra e por
            # identificador — perde só a perna da paráfrase, e só até a próxima
            # passada. Falhar aqui não desfaz nada do que já vale.
            try:
                from bot.lucien import consulta

                n = consulta.embeddar_pendentes(cx)
                cx.commit()
                if n:
                    logger.info("lucien: %d afirmação(ões) ganharam vetor", n)
            except Exception as exc:  # noqa: BLE001
                cx.rollback()
                logger.warning(
                    "lucien: as afirmações ficaram SEM vetor (%s) — elas já estão "
                    "gravadas e continuam achaveis por palavra; a busca por "
                    "paráfrase as alcança na próxima passada", exc,
                )

            # O boletim quente (F4), pelo mesmo motivo e com a mesma proteção do
            # bloco acima: vem DEPOIS do commit e não pode desfazer nada. É a
            # projeção em disco do que esta rodada acabou de apurar — SQL e
            # formatação de string, sem nenhuma chamada de modelo —, e ela pega
            # carona numa rodada que já ia acontecer. Custo marginal de
            # assinatura: zero.
            try:
                from bot.lucien import boletim

                if boletim.habilitado() and boletim.gerar(
                    cx, topic_id, kobe_home=kobe_home
                ):
                    logger.info("lucien: boletim de %s regerado", lote.topico_nome)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "lucien: o boletim não foi regerado (%s) — o registro está "
                    "gravado e a próxima rodada tenta de novo; o que o turno lê "
                    "continua sendo o boletim anterior, com a data que ele "
                    "declara", exc,
                )
            return r
        finally:
            store.destravar(cx)
    except Exception as exc:  # noqa: BLE001 — o worker nunca derruba quem o chamou
        try:
            cx.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.exception("lucien: rodada falhou")
        return ResultadoDaRodada(erro=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            cx.close()
        except Exception:  # noqa: BLE001
            pass


def _main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover — CLI
    ap = argparse.ArgumentParser(
        prog="python -m bot.lucien.worker",
        description="Uma rodada do LUCIEN (o arquivista do registro de estado).",
    )
    ap.add_argument("--uma-rodada", action="store_true", help="roda e sai (o default)")
    ap.add_argument("--topico", default=None, help="UUID do tópico (default: o mais antigo devido)")
    ap.add_argument("--limite", type=int, default=None, help="teto de mensagens no lote")
    ap.add_argument("--ensaio", action="store_true", help="mostra o lote e NÃO chama o modelo")
    ap.add_argument("--sem-cota", action="store_true", help="ignora o teto por hora")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    r = uma_rodada(
        topic_id=args.topico,
        limite_mensagens=args.limite,
        dry_run=args.ensaio,
        respeitar_cota=not args.sem_cota,
    )
    print(r.resumo())
    for rec in r.recusas:
        print(f"  recusa {rec.trava}: {rec.motivo}")
    return 0 if r.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
