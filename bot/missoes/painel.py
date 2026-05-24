"""Painel da Missão — renderização + edit no Telegram.

A mensagem-painel é UMA mensagem por missão, editada in-place enquanto
a missão evolui. Quando a missão termina (concluída / falhou / abortada),
o painel fica **read-only com o status final** — não deletamos nem
sobrescrevemos com outra coisa, pra preservar histórico de execução
no chat (aresta acordada com o Felipe em 2026-05-23).

Renderização:
- Template fixo, sem invenção. Tudo derivado de `Missao`.
- Layout em texto puro, sem HTML (o Telegram aceita ambos; texto puro
  evita risco de quebrar parse com caractere ilegal vindo do título de
  uma tarefa).
- Trunca em 4000 chars (margem do limite 4096). Acima disso, lista é
  cortada com `… (+N tarefas)`.

Edit no Telegram:
- HTTP direto via urllib (mesmo padrão do `kobe-notify`). Sem dependência
  do `python-telegram-bot` aqui — esse módulo precisa ser importável
  tanto pelo bot async quanto pelo daemon Keyko (que é sync).
- Throttle: máximo 1 edit/segundo por painel, controlado em memória.
  Telegram permite ~30/min, sobra. Eventos rápidos demais são absorvidos
  (o painel é sempre re-renderizado do estado atual, então uma única
  edit no fim do burst tem o conteúdo certo).
- "message not modified" do Telegram (400) é benigno — log debug e segue.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

from bot.missoes.models import Missao, StatusMissao, StatusTarefa, Tarefa


logger = logging.getLogger("kobe.missoes.painel")

TELEGRAM_TEXT_LIMIT = 4000
MIN_EDIT_INTERVAL_SECONDS = 1.0

# Glyphs do painel — agrupados pra fácil tweak. Emojis precisam de
# largura visual estável; estes têm renderização consistente no Telegram
# mobile/desktop.
_GLYPH_TAREFA = {
    StatusTarefa.PENDENTE.value: "⏳",
    StatusTarefa.RODANDO.value: "▶️",
    StatusTarefa.CONCLUIDA.value: "✅",
    StatusTarefa.FALHOU.value: "❌",
}

_GLYPH_MISSAO = {
    StatusMissao.PLANEJADA.value: "🟡",
    StatusMissao.EM_ANDAMENTO.value: "▶️",
    StatusMissao.CONCLUIDA.value: "🟢",
    StatusMissao.FALHOU.value: "🔴",
    StatusMissao.ABORTADA.value: "⏸️",
}

_TITULO_MISSAO = {
    StatusMissao.PLANEJADA.value: "Planejando",
    StatusMissao.EM_ANDAMENTO.value: "Em andamento",
    StatusMissao.CONCLUIDA.value: "Concluída",
    StatusMissao.FALHOU.value: "Falhou",
    StatusMissao.ABORTADA.value: "Abortada",
}


# --- render ------------------------------------------------------------

def render(missao: Missao, *, agora: Optional[str] = None) -> str:
    """String pronta pra enviar/editar no Telegram. Sem parse mode."""
    total = len(missao.tarefas)
    concluidas = sum(
        1 for t in missao.tarefas if t.status == StatusTarefa.CONCLUIDA.value
    )
    glyph = _GLYPH_MISSAO.get(missao.status, "•")
    titulo_status = _TITULO_MISSAO.get(missao.status, missao.status)

    objetivo = _truncate(missao.objetivo, 200)
    contador = f"{concluidas}/{total} tarefa(s)" if total else "sem tarefas ainda"

    linhas: list[str] = [
        f"🎯 Missão: {objetivo}",
        f"{glyph} {titulo_status} — {contador}",
        "",
    ]

    if missao.tarefas:
        linhas.extend(_render_tarefas(missao.tarefas, missao))
        linhas.append("")

    if missao.narrativa:
        linhas.append(f"💬 {_truncate(missao.narrativa, 600)}")
        linhas.append("")

    if agora is None:
        from bot.missoes.storage import now_iso
        agora = now_iso()
    # Pega só HH:MM:SS — economiza char e operador entende.
    hora_curta = agora.split("T")[-1].split("-")[0].split("+")[0][:8]
    linhas.append(f"🕐 Atualizado: {hora_curta}")

    texto = "\n".join(linhas)
    if len(texto) <= TELEGRAM_TEXT_LIMIT:
        return texto

    # Estourou — corta tarefas até caber. Lista de tarefas é o que cresce.
    return _trim_to_limit(missao, linhas)


def _render_tarefas(tarefas: list[Tarefa], missao: Missao) -> list[str]:
    out: list[str] = []
    concluidas_ids = {
        t.id for t in tarefas if t.status == StatusTarefa.CONCLUIDA.value
    }
    for t in tarefas:
        glyph = _GLYPH_TAREFA.get(t.status, "•")
        suffix = ""
        if t.status == StatusTarefa.RODANDO.value and t.progresso:
            suffix = f" ({t.progresso}%)"
        elif t.status == StatusTarefa.PENDENTE.value and t.depende_de:
            faltam = [d for d in t.depende_de if d not in concluidas_ids]
            if faltam:
                suffix = f" (aguarda {', '.join(faltam)})"
        elif t.status == StatusTarefa.FALHOU.value and t.erro:
            suffix = f" — {_truncate(t.erro, 80)}"
        out.append(f"{glyph} {t.id} — {_truncate(t.titulo, 100)}{suffix}")
    return out


def _trim_to_limit(missao: Missao, full_lines: list[str]) -> str:
    """Tira tarefas do meio até caber em TELEGRAM_TEXT_LIMIT, adicionando
    um marcador `… (+N tarefas)`. Mantém cabeçalho, narrativa e rodapé.
    """
    # Cabeçalho = 3 primeiras linhas (titulo, status, blank).
    head = full_lines[:3]
    # Rodapé = última linha não-vazia + uma blank antes.
    rodape: list[str] = []
    for ln in reversed(full_lines):
        rodape.insert(0, ln)
        if ln.startswith("🕐 "):
            break
    # Narrativa (se houver) vem logo antes do rodapé.
    rodape_full: list[str] = []
    found_narr = False
    for ln in reversed(full_lines):
        rodape_full.insert(0, ln)
        if ln.startswith("💬 "):
            found_narr = True
        if ln.startswith("🕐 "):
            continue
        if ln == "" and found_narr:
            break
        if not found_narr and ln.startswith("🕐 "):
            # achou rodapé sem narrativa
            break

    # Lista de tarefas = tudo entre head e rodape_full.
    tarefas_render = _render_tarefas(missao.tarefas, missao)

    # Tenta cortar pela metade até caber.
    n = len(tarefas_render)
    while n > 1:
        head_block = head
        listadas = tarefas_render[:n]
        marker = [f"… (+{len(tarefas_render) - n} tarefas)", ""]
        bloco = head_block + listadas + marker + rodape_full
        texto = "\n".join(bloco)
        if len(texto) <= TELEGRAM_TEXT_LIMIT:
            return texto
        n -= 1
    # Fallback ultra-conservador.
    return "\n".join(head + ["(painel não cabe — use /missao_status)"] + rodape_full)


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


# --- Telegram HTTP -----------------------------------------------------

class TelegramError(Exception):
    """Falha de comunicação com a API do Telegram."""


_ultimo_edit_por_msg: dict[tuple[int, int], float] = {}


def enviar_painel(
    *,
    bot_token: str,
    chat_id: int,
    thread_id: Optional[int],
    texto: str,
) -> int:
    """Envia mensagem nova (usado na criação da missão) e devolve `message_id`."""
    payload: dict = {
        "chat_id": chat_id,
        "text": texto,
        "disable_notification": False,
    }
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    data = _api_call(bot_token, "sendMessage", payload)
    msg_id = data.get("result", {}).get("message_id")
    if not isinstance(msg_id, int):
        raise TelegramError(f"sendMessage não retornou message_id válido: {data}")
    return msg_id


def editar_painel(
    *,
    bot_token: str,
    chat_id: int,
    message_id: int,
    texto: str,
) -> bool:
    """Edita mensagem existente. Devolve True se editou, False se throttle.

    Throttle: se faz menos de MIN_EDIT_INTERVAL_SECONDS desde o último
    edit dessa mesma msg, ignora silenciosamente. Caller (Keyko) é OK
    com isso porque o painel é sempre re-renderizado a partir do estado
    atual — o próximo tick já pega o conteúdo certo.

    Erro 400 "message is not modified" é benigno (mandamos o mesmo texto)
    e é tratado como sucesso. Outros 4xx/5xx logam e devolvem False.
    """
    chave = (chat_id, message_id)
    agora = time.monotonic()
    ultimo = _ultimo_edit_por_msg.get(chave, 0.0)
    if agora - ultimo < MIN_EDIT_INTERVAL_SECONDS:
        return False
    _ultimo_edit_por_msg[chave] = agora

    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": texto,
    }
    try:
        _api_call(bot_token, "editMessageText", payload)
        return True
    except TelegramError as exc:
        msg = str(exc)
        if "message is not modified" in msg:
            logger.debug("painel sem mudanças (Telegram); ok")
            return True
        logger.warning("falha editando painel chat=%s msg=%s: %s",
                       chat_id, message_id, msg)
        return False


def _api_call(bot_token: str, method: str, payload: dict) -> dict:
    """POST JSON pra https://api.telegram.org/bot<token>/<method>."""
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise TelegramError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TelegramError(f"rede: {exc.reason}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise TelegramError(f"resposta inválida do Telegram: {raw[:200]!r}") from exc
