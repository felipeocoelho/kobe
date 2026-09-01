"""O único caminho pelo qual LUCIEN fala com o operador.

POR QUE EXISTE UM ARQUIVO SÓ PARA ISSO
---------------------------------------
LUCIEN é deliberadamente **mudo**: o cérebro roda com `KOBE_CHAT_ID` e
`KOBE_THREAD_ID` removidos do ambiente (ver `brain._ambiente`), justamente para
que um `kobe-notify` disparado por engano lá dentro não tenha para onde ir. Ele
escreve no registro, não no chat.

A exceção são os avisos de **saúde do sistema** — "parei, e não foi porque
terminei". Eram dois lugares diferentes precisando da mesma coisa (a degeneração
da T7, no `worker`; a varredura que morre, na `reconstrucao`), e duas cópias da
regra de destino é como uma delas acaba divergindo em silêncio.

O DESTINO É DECLARADO, E ISSO NÃO É BUROCRACIA
-----------------------------------------------
Só `LUCIEN_ALERT_CHAT_ID` — a mesma regra do coletor da F1. Escolher um tópico
por conta própria faria uma mensagem de saúde do sistema cair numa conversa
qualquer, **o que é pior que não mandar**: quem recebe não tem contexto para
entender, e quem devia receber continua sem saber.

Sem a chave, o aviso vai para o log em nível `warning` e a função retorna. Isso é
caso **esperado**, não erro: a varredura roda tanto do daemon quanto de um shell
qualquer, e um aviso que não tem para onde ir não pode virar uma segunda fonte de
crash em cima da primeira.

FALHAR EM AVISAR NUNCA DERRUBA QUEM CHAMOU
-------------------------------------------
O que foi gravado já está gravado. Um Telegram fora do ar não pode desfazer uma
rodada bem-sucedida nem transformar uma parada anormal em stack trace.
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger("kobe.lucien.aviso")

PREFIXO = "⚠️ [lucien]"


def avisar(kobe_home: str, motivo: str) -> bool:
    """Manda `motivo` ao operador. Devolve se saiu de fato. **Nunca levanta.**"""
    chat_id = (os.environ.get("LUCIEN_ALERT_CHAT_ID") or "").strip()
    if not chat_id:
        logger.warning(
            "lucien: sem LUCIEN_ALERT_CHAT_ID — o aviso ficou só no log: %s", motivo,
        )
        return False
    helper = os.path.join(kobe_home, "bot", "bin", "kobe-notify")
    if not os.path.isfile(helper):
        logger.warning("lucien: %s não existe — aviso não enviado: %s", helper, motivo)
        return False
    env = dict(os.environ)
    env["KOBE_CHAT_ID"] = chat_id
    thread = (os.environ.get("LUCIEN_ALERT_THREAD_ID") or "").strip()
    if thread:
        env["KOBE_THREAD_ID"] = thread
    else:
        env.pop("KOBE_THREAD_ID", None)
    try:
        subprocess.run(
            [helper, f"{PREFIXO} {motivo}"], env=env, timeout=30,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        return True
    except Exception:  # noqa: BLE001 — falhar em avisar não derruba quem chamou
        logger.exception("lucien: falha ao enviar o aviso")
        return False
