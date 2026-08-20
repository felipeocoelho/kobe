"""Garantia de turno — o turno NUNCA morre calado, e a mensagem não se perde.

──────────────────────────────────────────────────────────────────────────
A dor (com prova de produção)
──────────────────────────────────────────────────────────────────────────

3 vezes em 30 dias, uma mensagem do operador sumiu em silêncio absoluto. O
caminho era sempre o mesmo: a mensagem chega, o montador da borda dispara o
turno, o turno vai buscar o histórico, o banco derruba a conexão ociosa
(`Server disconnected`) — e o `except Exception` do `assembler.py` **engolia**.
Do lado do operador é indistinguível de "o agente me ignorou".

Existia uma rede de segurança (`on_error` no handler, que avisa "travei,
reenvia" — disparou 20× em 30 dias), mas ela **nunca via essas três**: o
montador roda o turno numa task própria e captura a exceção antes que ela
chegue ao PTB. A rede existia e o buraco estava do lado de fora dela.

──────────────────────────────────────────────────────────────────────────
O que este módulo garante
──────────────────────────────────────────────────────────────────────────

Quando um turno levanta exceção, três coisas acontecem NESTA ordem:

1. **A mensagem é gravada em disco**, em `user-data/pending-turns/`. Disco
   local, sem depender de banco nenhum — é justamente o banco que pode estar
   fora. Ela para de depender de o turno sobreviver pra existir.
2. **Uma tentativa automática, e SÓ UMA**, alguns segundos depois — e apenas
   quando (a) o erro é de transporte do banco e (b) o turno morreu ANTES do
   ponto de não-retorno (ver `PONTO_SEGURO` abaixo). Fora dessas duas
   condições, não re-executa: melhor avisar do que arriscar responder duas
   vezes ou duplicar a mensagem no histórico.
3. **O operador é avisado**, com texto que não mente: a mensagem chegou, está
   guardada, e não foi respondida.

Além disso, no arranque do bot o que sobrou na fila é REPORTADO (não
reprocessado — uma mensagem de horas atrás não deve virar resposta do nada).

──────────────────────────────────────────────────────────────────────────
PONTO SEGURO (por que o retry é preciso, e não otimista)
──────────────────────────────────────────────────────────────────────────

O turno tem um ponto de não-retorno: gravar a mensagem do operador no banco.
Antes dele, re-executar é 100% inócuo (nada foi gravado, nada foi respondido).
Depois dele, re-executar duplicaria a mensagem e poderia gerar resposta dupla.
Por isso `_handle_user_text` marca `progress["committed"] = True` ao cruzar
esse ponto, e aqui só re-tentamos quando a marca NÃO foi posta. As 3 falhas
observadas morreram todas na leitura do histórico — antes da marca.

──────────────────────────────────────────────────────────────────────────
Agnóstico de banco — DE PROPÓSITO
──────────────────────────────────────────────────────────────────────────

Este módulo não sabe se o banco é Supabase, Postgres local ou outra coisa. Ele
só sabe que o turno levantou e que a mensagem não pode se perder. A única
concessão é `is_transport_error()`, que decide se vale re-tentar — e ela
pergunta ao `bot/db.py` (o ponto único que conhece o driver) em vez de decidir
sozinha. Na migração pro Postgres local, este arquivo não muda.

Atrás da flag `TURN_GUARANTEE_ENABLED` (default ON — é o conserto de um bug).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


logger = logging.getLogger("kobe.turn_guarantee")

# Espera antes da tentativa automática. Curta o bastante pra o operador nem
# perceber; longa o bastante pra uma conexão nova ser aceita do outro lado.
RETRY_DELAY_SECONDS = 3.0

# Teto de arquivos na fila. Passou disso, os mais velhos são podados no
# arranque — fila de pendências não pode virar vazamento de disco.
MAX_PENDING_FILES = 200

# Quantos caracteres da mensagem entram no aviso ao operador (pra ele
# reconhecer O QUE ficou pendente sem receber a mensagem inteira de volta).
PREVIEW_CHARS = 160


def enabled() -> bool:
    raw = (os.getenv("TURN_GUARANTEE_ENABLED") or "").strip().lower()
    if not raw:
        return True  # default ON: é o conserto de um bug que atrapalha hoje
    return raw in ("1", "true", "on", "yes")


def pending_dir(kobe_home: Path) -> Path:
    return Path(kobe_home) / "user-data" / "pending-turns"


def is_transport_error(exc: BaseException) -> bool:
    """O erro é 'a conexão morreu' (vale re-tentar) ou 'o pedido estava errado'
    (não vale)? A pergunta é feita ao bot/db.py, que é quem conhece o driver."""
    try:
        from bot.db import TRANSPORT_ERRORS

        return isinstance(exc, TRANSPORT_ERRORS)
    except Exception:  # noqa: BLE001 — se nem isso dá, tratamos como não-retentável
        return False


# ── Fila em disco ─────────────────────────────────────────────────────────


def queue_pending(
    kobe_home: Path,
    *,
    chat_id: int,
    thread_id: Optional[int],
    message_id: Optional[int],
    text: str,
    audio: bool,
    erro: str,
) -> Optional[Path]:
    """Grava a mensagem pendente em disco. Devolve o path, ou None se nem isso
    deu (aí só resta o aviso — mas o aviso ainda sai)."""
    try:
        d = pending_dir(kobe_home)
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = d / f"{stamp}-{chat_id}-{thread_id or 'geral'}.json"
        payload = {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "message_id": message_id,
            "text": text,
            "audio_transcribed": audio,
            "erro": erro,
            "criado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)  # atômico: um leitor nunca vê JSON pela metade
        logger.info("turn_guarantee: mensagem pendente gravada em %s", path)
        return path
    except OSError:
        logger.exception("turn_guarantee: falha gravando pendência (disco?)")
        return None


def resolve_pending(path: Optional[Path]) -> None:
    """Some com a pendência — a retentativa deu certo, não há o que reportar."""
    if path is None:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:  # noqa: BLE001 — resíduo na fila é ruído, não perda
        logger.warning("turn_guarantee: falha removendo %s", path, exc_info=True)


def list_pending(kobe_home: Path) -> list[dict]:
    """Pendências em disco, mais antigas primeiro. Cada item ganha `_path`."""
    d = pending_dir(kobe_home)
    if not d.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — arquivo parcial/corrompido: ignora
            continue
        if isinstance(data, dict):
            data["_path"] = f
            out.append(data)
    return out


def prune_pending(kobe_home: Path, keep: int = MAX_PENDING_FILES) -> int:
    """Poda os mais velhos acima do teto. Devolve quantos foram removidos."""
    d = pending_dir(kobe_home)
    if not d.is_dir():
        return 0
    files = sorted(d.glob("*.json"))
    excedente = files[: max(0, len(files) - keep)]
    for f in excedente:
        try:
            f.unlink(missing_ok=True)
        except OSError:  # noqa: BLE001
            pass
    return len(excedente)


# ── Execução guardada ─────────────────────────────────────────────────────


def _preview(text: str) -> str:
    t = " ".join((text or "").split())
    return t[:PREVIEW_CHARS] + ("…" if len(t) > PREVIEW_CHARS else "")


def _escape(text: str) -> str:
    """Escapa o mínimo do HTML do Telegram. A mensagem do operador entra no
    aviso: um `<` solto nela derrubaria o próprio aviso — e aí a garantia
    falharia justamente no ato de garantir."""
    return (
        (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def failure_notice(text: str, *, tentou_de_novo: bool) -> str:
    """O aviso ao operador (HTML do Telegram). Não promete o que não vai
    acontecer, e diz ONDE a mensagem está — a diferença entre 'travei' e
    'travei e perdi'."""
    de_novo = " Tentei de novo automaticamente e falhou também." if tentou_de_novo else ""
    return (
        "🔴 Tua mensagem chegou, mas eu travei antes de conseguir responder."
        f"{de_novo} Ela <b>não se perdeu</b> — está guardada aqui:\n\n"
        f"<i>{_escape(_preview(text))}</i>\n\n"
        "Me manda um alô que eu retomo daqui."
    )


async def run_guarded(
    *,
    run: Callable[[dict], Awaitable[Any]],
    kobe_home: Path,
    chat_id: int,
    thread_id: Optional[int],
    message_id: Optional[int],
    text: str,
    audio: bool,
    notify: Callable[[str], Awaitable[None]],
) -> None:
    """Roda o turno com a garantia. NUNCA propaga exceção de turno.

    `run` recebe um dict de progresso e deve marcar `progress["committed"]=True`
    ao cruzar o ponto de não-retorno (gravar a mensagem do operador). `notify`
    entrega o aviso ao operador (o chamador decide como — reply, HTML, etc.).
    """
    if not enabled():
        await run({})  # flag off: comportamento legado (exceção sobe como antes)
        return

    progress: dict = {}
    try:
        await run(progress)
        return
    except asyncio.CancelledError:
        # Cancelamento é pedido de encerramento, não falha de turno — propaga
        # (engolir travaria o shutdown do bot; lição do bug do auto-cancelamento).
        raise
    except Exception as exc:  # noqa: BLE001 — é exatamente o que estava sumindo
        logger.exception("turn_guarantee: turno levantou — acionando a garantia")
        erro = f"{type(exc).__name__}: {exc}"
        pendente = queue_pending(
            kobe_home,
            chat_id=chat_id,
            thread_id=thread_id,
            message_id=message_id,
            text=text,
            audio=audio,
            erro=erro,
        )
        pode_retentar = is_transport_error(exc) and not progress.get("committed")
        if not pode_retentar:
            motivo = (
                "turno já passou do ponto seguro"
                if progress.get("committed")
                else "erro não é de transporte"
            )
            logger.info("turn_guarantee: SEM retentativa (%s)", motivo)
            await _safe_notify(notify, failure_notice(text, tentou_de_novo=False))
            return

    # Uma tentativa, e só uma. Chegar aqui significa: erro de transporte, antes
    # do ponto de não-retorno — nada foi gravado nem respondido.
    logger.info("turn_guarantee: retentativa única em %.1fs", RETRY_DELAY_SECONDS)
    await asyncio.sleep(RETRY_DELAY_SECONDS)
    try:
        await run({})
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("turn_guarantee: retentativa também falhou")
        await _safe_notify(notify, failure_notice(text, tentou_de_novo=True))
        return

    logger.info("turn_guarantee: retentativa OK — o operador nem percebeu")
    resolve_pending(pendente)


async def _safe_notify(notify: Callable[[str], Awaitable[None]], texto: str) -> None:
    """O aviso é a última linha de defesa: se ele falhar, só resta o log."""
    try:
        await notify(texto)
    except Exception:  # noqa: BLE001
        logger.exception("turn_guarantee: falha entregando o aviso ao operador")


# ── Relatório de arranque ─────────────────────────────────────────────────


def render_startup_report(kobe_home: Path) -> list[tuple[int, Optional[int], str]]:
    """Pendências que sobreviveram a um restart, agrupadas por tópico.

    Devolve `[(chat_id, thread_id, texto)]` pronto pra enviar. NÃO reprocessa:
    uma mensagem de horas atrás não deve virar resposta do nada — o certo é
    mostrar ao operador e ele decidir.
    """
    podados = prune_pending(kobe_home)
    if podados:
        logger.info("turn_guarantee: %d pendência(s) antiga(s) podada(s)", podados)

    por_topico: dict[tuple[int, Optional[int]], list[dict]] = {}
    for item in list_pending(kobe_home):
        chat_id = item.get("chat_id")
        if chat_id is None:
            continue
        por_topico.setdefault((chat_id, item.get("thread_id")), []).append(item)

    saida: list[tuple[int, Optional[int], str]] = []
    for (chat_id, thread_id), itens in por_topico.items():
        linhas = [
            f"⚠️ Antes do último restart, {len(itens)} mensagem(ns) tua(s) "
            "chegaram e não consegui responder. Ficaram guardadas:"
        ]
        for it in itens[-5:]:
            quando = (it.get("criado_em") or "")[:16].replace("T", " ")
            linhas.append(
                f"• <i>{_escape(_preview(it.get('text') or ''))}</i> ({quando})"
            )
        if len(itens) > 5:
            linhas.append(f"…e mais {len(itens) - 5}.")
        linhas.append("Se ainda valem, é só remandar.")
        saida.append((chat_id, thread_id, "\n".join(linhas)))
    return saida


def clear_pending(kobe_home: Path) -> int:
    """Limpa a fila (o operador já foi avisado no arranque; guardar de novo só
    faria o mesmo aviso repetir a cada boot). Devolve quantos foram limpos."""
    d = pending_dir(kobe_home)
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob("*.json"):
        try:
            f.unlink(missing_ok=True)
            n += 1
        except OSError:  # noqa: BLE001
            pass
    return n
