"""O trabalhador que preenche o índice de busca — sempre ATRÁS, nunca no turno.

A REGRA QUE GOVERNA ESTE ARQUIVO
---------------------------------
**A gravação de uma mensagem nunca espera por embedding.** É a preocupação nº 1
do operador (performance) e é a decisão E3 do briefing (*"nada tem 'fechar a
sala' como gatilho; tudo é contínuo, dirigido por relógio ou acúmulo"*).

Por isso o trabalho se divide em duas naturezas:

- **As pernas de palavra e literal ficam prontas no próprio INSERT**, sem código
  nenhum: `messages.search_tsv` é coluna gerada e os índices são do Postgres.
  Uma mensagem é buscável por palavra no instante em que é gravada.
- **A perna de sentido roda aqui**, num tick do Keyko. Uma mensagem fica
  buscável por sentido em até ~1 minuto. É o atraso que o `kobe-remember`
  **avisa** quando existe, em vez de omitir calado.

O QUE UM TICK FAZ, NESTA ORDEM
-------------------------------
1. **Quebra em trechos** as mensagens que ainda não têm nenhum. Não custa rede.
2. **Embedda** os trechos sem vetor, em lote.
3. **Recalcula `search_lexeme_df`** — a estatística que separa termo banal de
   termo raro. Só quando algo mudou, e no máximo a cada `DF_INTERVALO`.

A ORDEM IMPORTA: quebrar antes de embeddar garante que um tick interrompido no
meio deixe trabalho *pendente*, nunca trabalho *perdido*. Todo estado vive no
banco (`embedding IS NULL` é a fila), então reiniciar o daemon não perde nada e
não duplica nada.

CUSTO DE COTA: ZERO
--------------------
Como o coletor da F1, este trabalho acontece **dentro do tick**, em processo.
Nenhum `claude -p` é acordado — quebrar texto e pedir vetor não precisa de um
agente, e acordar um pra isso gastaria o recurso escasso na tarefa mais burra.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from bot.search import embedder
from bot.search.chunker import chunk

logger = logging.getLogger("kobe.search.indexer")

# Quantos trechos embeddar por tick. Teto de cortesia, não de capacidade: a
# carga inicial do acervo inteiro levou 42 s, mas um tick que segura o daemon
# por 42 s atrasa as outras fontes. Com 1.000 por tick, o acervo entra em ~8
# ticks e a fila do dia a dia (dezenas de mensagens) some no primeiro.
LOTE_POR_TICK = 1000

# `ts_stat` sobre o acervo custou 80 ms. Não é caro, mas também não muda a cada
# minuto: a seletividade de um radical é uma propriedade lenta do acervo.
DF_INTERVALO_SEGUNDOS = 3600.0


@dataclass
class Resultado:
    """O que um tick fez. Vai pro log e pro diagnóstico do comando."""

    mensagens_quebradas: int = 0
    trechos_criados: int = 0
    trechos_embeddados: int = 0
    df_atualizado: bool = False
    erro: Optional[str] = None

    @property
    def fez_algo(self) -> bool:
        return bool(
            self.mensagens_quebradas or self.trechos_embeddados or self.df_atualizado
        )


def _env(nome: str) -> str:
    return (os.environ.get(nome) or "").strip()


def indexer_enabled() -> bool:
    """Nasce desligada — é o rollback nomeado no briefing.

    Desligada, `message_chunks` fica **inerte**: nada é escrito, nada é apagado,
    e o `kobe-remember` continua respondendo pelas duas pernas de palavra,
    dizendo em toda saída que a de sentido está fora. O que não se pode é ficar
    calado e deixar um "não achei" parcial passar por ausência confirmada.
    """
    return _env("SEARCH_INDEX_ENABLED").lower() in ("1", "true", "on", "yes")


# ── Passo 1: quebrar em trechos ───────────────────────────────────────────


def quebrar_pendentes(db, *, limite: int = 5000) -> tuple[int, int]:
    """Cria os trechos das mensagens que ainda não têm nenhum.

    O `NOT EXISTS` é o que torna a operação repetível: rodar duas vezes não
    duplica trecho, e o UNIQUE `(message_id, idx)` é a rede embaixo disso.
    """
    pendentes = db.query(
        "SELECT m.id, m.content FROM messages m"
        " WHERE NOT EXISTS (SELECT 1 FROM message_chunks c WHERE c.message_id = m.id)"
        " ORDER BY m.seq"
        " LIMIT %s",
        (limite,),
    )
    mensagens = 0
    trechos = 0
    for linha in pendentes:
        pedacos = chunk(linha["content"] or "")
        if not pedacos:
            # Mensagem vazia não gera trecho — e por isso ela seria relida a
            # cada tick para sempre. Um trecho vazio também não pode entrar
            # (`body` é NOT NULL, e um vetor de nada seria ruído no índice).
            # A saída é um trecho com um caractere de marca: ele fecha a
            # pendência e nunca vai ganhar similaridade com nada.
            pedacos = ["​"]
        for i, corpo in enumerate(pedacos):
            db.execute(
                "INSERT INTO message_chunks (message_id, idx, body)"
                " VALUES (%s, %s, %s)"
                " ON CONFLICT (message_id, idx) DO NOTHING",
                (linha["id"], i, corpo),
            )
            trechos += 1
        mensagens += 1
    return mensagens, trechos


# ── Passo 2: embeddar ─────────────────────────────────────────────────────


def embeddar_pendentes(db, *, limite: int = LOTE_POR_TICK) -> int:
    """Preenche o vetor dos trechos que ainda não têm. Levanta se o serviço cair.

    Deixa `EmbeddingIndisponivel` subir de propósito: quem chama tem que
    distinguir "não havia o que fazer" de "não deu pra fazer". Engolir aqui
    faria o índice parar de crescer em silêncio, que é a forma de falhar mais
    cara possível pra uma peça cujo produto é "não tenho registro disso".
    """
    pendentes = db.query(
        "SELECT id, body FROM message_chunks WHERE embedding IS NULL"
        " ORDER BY id LIMIT %s",
        (limite,),
    )
    if not pendentes:
        return 0
    vetores = embedder.embed([p["body"] for p in pendentes])
    modelo = embedder.modelo()
    for p, v in zip(pendentes, vetores):
        db.execute(
            "UPDATE message_chunks"
            "   SET embedding = %s, model = %s, embedded_at = NOW()"
            " WHERE id = %s",
            (embedder.para_sql(v), modelo, p["id"]),
        )
    return len(pendentes)


# ── Passo 3: a estatística de seletividade ────────────────────────────────


def atualizar_df(db) -> bool:
    """Recalcula `search_lexeme_df` a partir de `messages.search_tsv`.

    O `INSERT ... ON CONFLICT DO UPDATE` substitui uma linha de cada vez, sem
    nunca esvaziar a tabela: um `DELETE`+`INSERT` deixaria a estatística vazia
    por um instante, e uma busca que caísse nessa janela consideraria **todo**
    radical raro — o oposto exato do que a tabela existe pra impedir.

    O que sobrou de uma versão anterior do acervo e não aparece mais fica com o
    `ndoc` velho, o que é inofensivo: radical que sumiu do acervo não vai casar
    com nada de qualquer forma.
    """
    linhas = db.query(
        "SELECT word, ndoc FROM ts_stat('SELECT search_tsv FROM messages')"
    )
    for linha in linhas:
        db.execute(
            "INSERT INTO search_lexeme_df (word, ndoc, refreshed_at)"
            " VALUES (%s, %s, NOW())"
            " ON CONFLICT (word) DO UPDATE"
            "   SET ndoc = EXCLUDED.ndoc, refreshed_at = EXCLUDED.refreshed_at",
            (linha["word"], linha["ndoc"]),
        )
    return bool(linhas)


def df_esta_velha(db, *, intervalo: float = DF_INTERVALO_SEGUNDOS) -> bool:
    linha = db.one(
        "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(refreshed_at))) AS idade"
        "  FROM search_lexeme_df"
    )
    if not linha or linha.get("idade") is None:
        return True
    return float(linha["idade"]) >= intervalo


# ── O tick ────────────────────────────────────────────────────────────────


def tick(db, *, lote: int = LOTE_POR_TICK) -> Resultado:
    """Um ciclo completo. Nunca levanta: devolve o erro no resultado.

    Não levantar é a regra do daemon — uma fonte que estoura derruba o tick das
    outras. Mas o erro **não some**: ele vai no `Resultado`, no log, e o índice
    simplesmente para de crescer até a causa ser resolvida. Nada é gravado
    torto no meio do caminho.
    """
    r = Resultado()
    if not indexer_enabled():
        return r
    try:
        r.mensagens_quebradas, r.trechos_criados = quebrar_pendentes(db)
        r.trechos_embeddados = embeddar_pendentes(db, limite=lote)
        if (r.mensagens_quebradas or r.trechos_embeddados) and df_esta_velha(db):
            r.df_atualizado = atualizar_df(db)
    except embedder.EmbeddingIndisponivel as exc:
        r.erro = str(exc)
        logger.warning("indexador: serviço de embedding fora — %s", exc)
    except Exception as exc:  # noqa: BLE001 — o daemon não pode cair por isto
        r.erro = f"{type(exc).__name__}: {exc}"
        logger.exception("indexador: falha inesperada")
    return r


def pendencia(db) -> dict:
    """Quanto falta indexar, e desde quando.

    É o que o `kobe-remember` usa para **avisar** que a perna de sentido está
    atrasada, em vez de devolver menos resultado e deixar parecer ausência.
    """
    linha = db.one(
        "SELECT COUNT(*) AS trechos,"
        "       EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) AS idade_segundos"
        "  FROM message_chunks WHERE embedding IS NULL"
    )
    sem_trecho = db.scalar(
        "SELECT COUNT(*) FROM messages m"
        " WHERE NOT EXISTS (SELECT 1 FROM message_chunks c WHERE c.message_id = m.id)"
    )
    return {
        "trechos_sem_vetor": int((linha or {}).get("trechos") or 0),
        "mensagens_sem_trecho": int(sem_trecho or 0),
        "idade_segundos": float((linha or {}).get("idade_segundos") or 0.0),
    }


def carga_inicial(db, *, log=print) -> Resultado:
    """A carga do histórico: roda o tick até a fila zerar.

    Existe como função (e não como script solto) porque a carga inicial e o
    regime permanente são exatamente o mesmo trabalho — o que muda é só quantas
    vezes ele repete. Um caminho separado pra carga seria um segundo lugar onde
    o mesmo bug poderia morar.
    """
    total = Resultado()
    while True:
        t0 = time.time()
        r = tick(db)
        total.mensagens_quebradas += r.mensagens_quebradas
        total.trechos_criados += r.trechos_criados
        total.trechos_embeddados += r.trechos_embeddados
        total.df_atualizado = total.df_atualizado or r.df_atualizado
        if r.erro:
            total.erro = r.erro
            return total
        if not r.fez_algo:
            return total
        log(
            f"  +{r.trechos_embeddados} trechos embeddados "
            f"({r.mensagens_quebradas} mensagens quebradas) em {time.time() - t0:.1f}s"
        )


# ── Linha de comando ──────────────────────────────────────────────────────
#
# Existe pela carga inicial e pelo diagnóstico. O regime permanente é o tick do
# Keyko; aqui é onde o operador (ou uma sessão de código) empurra o histórico
# inteiro de uma vez e confere quanto falta.
#
#     python -m bot.search.indexer status
#     python -m bot.search.indexer carga


def _main(argv: Optional[list] = None) -> int:  # pragma: no cover — CLI
    import argparse
    import sys

    from bot.config import load_config
    from bot.db import build_client

    p = argparse.ArgumentParser(prog="bot.search.indexer")
    p.add_argument("acao", choices=["status", "carga", "tick", "df"])
    args = p.parse_args(argv)

    db = build_client(load_config())

    if args.acao == "status":
        p_ = pendencia(db)
        total = db.scalar("SELECT COUNT(*) FROM message_chunks")
        comvet = db.scalar("SELECT COUNT(*) FROM message_chunks WHERE embedding IS NOT NULL")
        msgs = db.scalar("SELECT COUNT(*) FROM messages")
        radicais = db.scalar("SELECT COUNT(*) FROM search_lexeme_df")
        print(f"chave SEARCH_INDEX_ENABLED: {'ligada' if indexer_enabled() else 'DESLIGADA'}")
        print(f"mensagens                 : {msgs}")
        print(f"trechos                   : {total}  (com vetor: {comvet})")
        print(f"mensagens sem trecho      : {p_['mensagens_sem_trecho']}")
        print(f"trechos sem vetor         : {p_['trechos_sem_vetor']}")
        print(f"radicais na estatistica   : {radicais}")
        return 0

    if not indexer_enabled():
        print(
            "SEARCH_INDEX_ENABLED está desligada — nada a fazer.\n"
            "Ligue no .env (ou no ambiente) antes de rodar a carga.",
            file=sys.stderr,
        )
        return 2

    if args.acao == "df":
        atualizar_df(db)
        print("estatística de radicais atualizada")
        return 0

    t0 = time.time()
    r = carga_inicial(db) if args.acao == "carga" else tick(db)
    print(
        f"mensagens quebradas: {r.mensagens_quebradas} · "
        f"trechos criados: {r.trechos_criados} · "
        f"trechos embeddados: {r.trechos_embeddados} · "
        f"{time.time() - t0:.1f}s"
    )
    if r.erro:
        print(f"ERRO: {r.erro}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
