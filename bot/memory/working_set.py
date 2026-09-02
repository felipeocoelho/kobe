"""Janela imediata — a camada crua e barata da memória de trabalho.

Movida do pacote do Chat Manager na Frente 0 do Highlander (refactor sem mudar
comportamento). É memória PURA: consulta `messages` só por `topic_id`, então o
lugar dela é aqui — e foi por já estar aqui que ela atravessou intacta a
aposentadoria do Chat Manager (2026-08-25).

O turno é burro e rápido: lê o que já está no banco e cola no prompt. Nada de
embedding/LLM aqui (plano §6). Filtrada por tópico (predicado obrigatório —
Dev Kobe não puxa Olimpo).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from bot.db import KobeDB

logger = logging.getLogger("kobe.memory.working_set")


def _int_env(nome: str, padrao: int, *, minimo: int, maximo: int) -> int:
    """Parâmetro inteiro do ambiente, PRESO em faixa e com o desvio registrado.

    Preso porque estes números interagem: um piso maior que o teto de mensagens
    não é "configuração agressiva", é um estado incoerente que o código
    obedeceria sem reclamar. E **registrado** porque parâmetro cujo valor errado
    falha em silêncio é o defeito que este sistema já cometeu — ver a nota sobre
    a dimensão do vetor em `bot/search/embedder.py`.
    """
    bruto = (os.getenv(nome) or "").strip()
    if not bruto:
        return padrao
    try:
        valor = int(bruto)
    except ValueError:
        logger.warning("working_set: %s=%r não é inteiro — usando %s", nome, bruto, padrao)
        return padrao
    preso = max(minimo, min(maximo, valor))
    if preso != valor:
        logger.warning(
            "working_set: %s=%s está fora da faixa [%s, %s] — preso em %s",
            nome, valor, minimo, maximo, preso,
        )
    return preso


# Camada imediata — piso híbrido (doc §3): "últimos 10 min OU últimas N
# msgs, o que for maior".
#
# ⚠️ QUEM MANDA AQUI NÃO É O RELÓGIO — medido em 31/08/2026, sobre os 968 turnos
# reais do operador no tópico de maior volume:
#
#     o PISO de 8 mensagens corta em  87% dos turnos
#     a janela de tempo corta em      11%
#     o teto de 60 mensagens           0%
#     o teto de 8.000 tokens           0%
#
# Isso importa para quem for calibrar: mexer no tempo enquanto o piso está na
# frente dele não muda quase nada, e o resultado nulo seria lido como "o
# parâmetro não importa" em vez de "outro parâmetro está na frente". Para ver
# quem manda na configuração atual, rode `bot/bin/kobe-memoria diagnostico`.
#
# E os valores continuam os de sempre — medir três alternativas (1 h, 6 h, 24 h)
# mostrou que alargar por recência tem rendimento decrescente: o custo cresce
# ~5,4× até 24 h enquanto o material NO ASSUNTO cresce ~2,7×, e a razão
# sinal/ruído cai monotonicamente em todos os limiares testados. Quem responde
# ao desejo de "enxergar além dos últimos minutos" é recuperação, não recência.
#
# Os 600 s, por sinal, foram escolhidos no chute pelo operador (declarado por
# ele em 31/08/2026) — e não recebem crédito por antiguidade. Eles ficam porque
# nenhuma alternativa medida entrega mais assunto por token gasto.
IMMEDIATE_WINDOW_SECONDS = _int_env(
    "WORKING_MEMORY_WINDOW_SECONDS", 600, minimo=30, maximo=604800)
IMMEDIATE_MIN_COUNT = _int_env(
    "WORKING_MEMORY_MIN_COUNT", 8, minimo=1, maximo=200)
IMMEDIATE_HARD_CAP = _int_env(
    "WORKING_MEMORY_HARD_CAP", 60, minimo=1, maximo=1000)

# Teto de TOKEN da janela (Highlander v2 F4). Os caps de TEMPO e CONTAGEM não
# protegem do TAMANHO: uma rajada de áudios longos (transcrições de minutos)
# cabe em 60 msgs / 10 min e mesmo assim estoura o prompt — queima o teto de 5h
# e dilui o contrato. Este teto corta a janela por TAMANHO, mantendo as msgs
# mais RECENTES (descarta as mais antigas da janela). Estimativa barata de
# token (sem tokenizer): ~4 chars/token. Configurável; default generoso o
# bastante pra não cortar conversa normal, baixo o bastante pra pegar o
# patológico. 0 ou negativo = desliga o teto (volta ao comportamento pré-F4).
IMMEDIATE_TOKEN_CAP = _int_env(
    "WORKING_MEMORY_TOKEN_CAP", 8000, minimo=0, maximo=200000)

# A régua que converte char em token, e ela NÃO é política — é medida. Um valor
# alto aqui não relaxa um orçamento: faz o código **contar errado** e estourar o
# teto achando que respeitou. Por isso é presa numa faixa estreita, e o desvio
# vai para o log. (Erro seguro é supor POUCOS chars por token; 4 é o valor
# histórico e fica no meio da faixa usual do português, 3,5–4,2.)
_CHARS_PER_TOKEN = float(_int_env(
    "WORKING_MEMORY_CHARS_PER_TOKEN", 4, minimo=3, maximo=5))


def conferir() -> list[str]:
    """As incoerências da configuração atual. Lista vazia = tudo são.

    Por que isto existe separado das travas de faixa: **cada parâmetro sozinho
    pode ser válido e o conjunto ser impossível.** Um piso de 80 mensagens com
    teto de 60 é um estado que o código obedeceria sem reclamar — a consulta
    traz 60, o piso pede 80, e a janela silenciosamente entrega 60 enquanto a
    configuração anuncia outra coisa. Faixa por parâmetro não pega isso; só a
    relação entre eles pega.

    Devolve texto em vez de levantar: uma configuração torta não pode impedir o
    bot de subir — um bot no ar com aviso alto se conserta em minutos, um bot
    que se recusa a subir por causa de um número deixa o operador sem canal.
    """
    problemas: list[str] = []
    if IMMEDIATE_MIN_COUNT > IMMEDIATE_HARD_CAP:
        problemas.append(
            f"WORKING_MEMORY_MIN_COUNT ({IMMEDIATE_MIN_COUNT}) é maior que "
            f"WORKING_MEMORY_HARD_CAP ({IMMEDIATE_HARD_CAP}): o piso pede mais "
            f"mensagens do que a consulta traz, então a janela entrega no máximo "
            f"{IMMEDIATE_HARD_CAP} e o piso vira letra morta"
        )
    if 0 < IMMEDIATE_TOKEN_CAP < IMMEDIATE_MIN_COUNT * 20:
        problemas.append(
            f"WORKING_MEMORY_TOKEN_CAP ({IMMEDIATE_TOKEN_CAP}) é pequeno demais "
            f"para o piso de {IMMEDIATE_MIN_COUNT} mensagens: o teto de tamanho "
            f"vai cortar abaixo do piso em quase todo turno, e aí quem manda na "
            f"janela é ele, não o que a configuração parece dizer"
        )
    return problemas


def descrever() -> str:
    """A configuração efetiva, em uma linha, para o log de subida.

    Existe porque calibrar às cegas é o que faz um ajuste parecer inócuo: o
    operador muda o tempo, não sente diferença, e conclui que o parâmetro não
    importa — quando na verdade outro estava na frente dele. Quem quiser saber
    *quem manda* sobre os turnos reais roda `bot/bin/kobe-memoria diagnostico`.
    """
    return (
        f"janela imediata: {IMMEDIATE_WINDOW_SECONDS}s OU {IMMEDIATE_MIN_COUNT} "
        f"msgs (o que for maior), teto {IMMEDIATE_HARD_CAP} msgs / "
        f"{IMMEDIATE_TOKEN_CAP} tokens, régua {_CHARS_PER_TOKEN:g} chars/token"
    )


def _parse_ts(value: str) -> Optional[datetime]:
    """Parseia timestamp ISO 8601 (created_at do banco) com tolerância a
    sufixo 'Z'. None se vazio/inválido — chamador cai no fallback."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_immediate_messages(
    db: KobeDB, topic_id: str
) -> list[dict]:
    """Camada imediata: piso híbrido (10 min OU N msgs, o que for maior).

    Filtra por tópico (predicado que evita full scan cruzado). Ordem
    cronológica crescente, pronta pro histórico do prompt.
    """
    rows = list(
        reversed(
            db.query(
                "SELECT role, content, created_at, audio_transcribed"
                "  FROM messages"
                " WHERE topic_id = %s"
                " ORDER BY created_at DESC"
                " LIMIT %s",
                (topic_id, IMMEDIATE_HARD_CAP),
            )
        )
    )
    # Blindagem: jamais deixar um [Resumo da sessão anterior] (role='system'
    # injetado pelo compactador legado) entrar na janela crua. Com Chat
    # Manager a compactação não roda mais, mas summaries de antes deste fix
    # podem estar no fluxo do tópico — filtra pra não poluir o cru. Princípio:
    # ponteiro, nunca resumo. Contexto profundo vem do kobe-recall, não daqui.
    rows = [
        r
        for r in rows
        if not (
            r.get("role") == "system"
            and (r.get("content") or "").lstrip().startswith("[Resumo da sessão")
        )
    ]
    if not rows:
        return []
    # Âncora da janela: timestamp da ÚLTIMA mensagem da conversa, NÃO 'agora'.
    # Assim "últimos 10 min" são os 10 min finais de CONVERSA real — se o
    # operador larga o telefone por horas e volta, o imediato ainda traz o fim
    # do último papo inteiro, em vez de cair pro piso de N msgs decapitado.
    # Fallback pra now() só se o último created_at vier ilegível.
    anchor = _parse_ts(rows[-1].get("created_at") or "") or datetime.now(timezone.utc)
    cutoff = (anchor - timedelta(seconds=IMMEDIATE_WINDOW_SECONDS)).isoformat()
    within = [r for r in rows if (r.get("created_at") or "") >= cutoff]
    keep = max(len(within), IMMEDIATE_MIN_COUNT)
    window = rows[-keep:]
    return _bound_by_tokens(window)


def _bound_by_tokens(window: list[dict]) -> list[dict]:
    """Teto de TAMANHO (F4): mantém as msgs mais RECENTES cujo total estimado cabe
    em IMMEDIATE_TOKEN_CAP, descartando as mais antigas. Garante ao menos a última
    msg (o contexto imediato do turno) mesmo que ela sozinha estoure o teto — cortar
    a mensagem atual seria pior que o estouro. Cap <= 0 desliga (no-op)."""
    if IMMEDIATE_TOKEN_CAP <= 0 or not window:
        return window
    kept: list[dict] = []
    total = 0
    for r in reversed(window):  # do mais recente pro mais antigo
        cost = int(len(r.get("content") or "") / _CHARS_PER_TOKEN) + 1
        if kept and total + cost > IMMEDIATE_TOKEN_CAP:
            break
        kept.append(r)
        total += cost
    kept.reverse()  # volta à ordem cronológica crescente
    return kept
