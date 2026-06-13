"""Sinal de vida no Telegram enquanto o Claude trabalha.

Pricípio: assim que o operador manda mensagem, ele precisa sentir que a
gente tá viva. O `typing…` nativo já cobre o caso simples (resposta em
poucos segundos). Mas em tarefas longas — Claude lendo arquivos, rodando
comandos, consultando MCPs — o operador fica no escuro. Este módulo
fecha o gap: lê eventos do stream-json do Claude Code e atualiza uma
única "mensagem de status" no Telegram traduzindo a ação atual pra algo
humano ("lendo `bot/main.py`…", "rodando comando…").

Decisões:
- **Uma mensagem só, editada in-place.** Evita ping repetido no chat e
  mantém o feed limpo. A primeira edição é a única que vibra o celular.
- **Lazy: só aparece se demorar.** Tarefas rápidas (brainstorm puro)
  resolvem antes do threshold; nesses casos a status nem é criada.
- **Throttle.** Telegram limita edits; aplicamos um piso mínimo entre
  updates pra evitar 429.
- **Filtra subagentes.** Eventos com `parent_tool_use_id` são ações
  internas de subagentes — não traduzimos linha a linha, agrupamos sob
  "delegando subtarefa".
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import PurePosixPath
from typing import Optional


logger = logging.getLogger("kobe.progress")

# Quanto tempo esperar (sem nenhum tool_use) antes de mostrar status
# textual. Se a resposta chegar antes disso, o typing nativo bastou.
DEFAULT_DELAY_SECONDS = 6.0

# Intervalo mínimo entre edições da status message — protege contra rate
# limit do Telegram (40 msg/s global, mais restrito por chat) e evita
# poluição visual quando o Claude encadeia muitos tool calls em rajada.
MIN_EDIT_INTERVAL_SECONDS = 1.5

# Prefixo curto na status pra deixar claro que é meta-mensagem do bot, e
# não conteúdo do agente. O operador aprende a distinguir num olhar.
STATUS_PREFIX = "⏳ "

# Mapeamento tool → frase humana. Mantemos curto e em PT-BR. Tools fora
# da lista caem num genérico "trabalhando…" — preferível a vazar nome
# técnico ("invocando Glob…") pro operador.
_TOOL_LABELS: dict[str, str] = {
    "Read": "lendo arquivo",
    "Edit": "editando arquivo",
    "Write": "escrevendo arquivo",
    "NotebookEdit": "editando notebook",
    "Bash": "rodando comando",
    "Grep": "buscando no código",
    "Glob": "listando arquivos",
    "WebFetch": "consultando a web",
    "WebSearch": "buscando na web",
    "Task": "delegando subtarefa",
    "Agent": "delegando subtarefa",
    "AskUserQuestion": "preparando pergunta",
}


def describe_tool_use(name: str, input_: dict) -> str:
    """Traduz um tool_use em frase amigável pro operador."""
    base = _TOOL_LABELS.get(name, "trabalhando")
    if name == "Read":
        path = input_.get("file_path") or ""
        return f"{base} `{_short_path(path)}`" if path else base
    if name in ("Edit", "Write", "NotebookEdit"):
        path = input_.get("file_path") or ""
        return f"{base} `{_short_path(path)}`" if path else base
    if name == "Bash":
        desc = input_.get("description") or ""
        return f"{base}: {desc.strip()}" if desc else base
    if name == "Grep":
        pat = (input_.get("pattern") or "").strip()
        if pat:
            preview = pat if len(pat) <= 40 else pat[:40] + "…"
            return f'{base} ("{preview}")'
        return base
    if name == "Glob":
        pat = (input_.get("pattern") or "").strip()
        return f"{base} ({pat})" if pat else base
    if name in ("WebFetch", "WebSearch"):
        url = input_.get("url") or input_.get("query") or ""
        if url:
            host = _host_of(url)
            return f"{base} ({host})" if host else base
        return base
    if name in ("Task", "Agent"):
        desc = input_.get("description") or input_.get("subagent_type") or ""
        return f"{base}: {desc.strip()}" if desc else base
    return base


def _short_path(path: str) -> str:
    """Compacta path absoluto pra ler bem na status: `bot/main.py`."""
    if not path:
        return ""
    try:
        p = PurePosixPath(path)
        parts = p.parts
        if len(parts) <= 2:
            return p.name
        # Mostra as duas últimas pastas + nome do arquivo.
        return "/".join(parts[-2:])
    except Exception:  # noqa: BLE001 — qualquer formato esquisito
        return path


def _host_of(url: str) -> str:
    if "://" not in url:
        return url[:40]
    rest = url.split("://", 1)[1]
    return rest.split("/", 1)[0]


class ProgressReporter:
    """Mantém uma única "status message" do Telegram durante a execução.

    Uso típico (handler):
        reporter = ProgressReporter(chat_id, thread_id, bot)
        await reporter.start()
        try:
            reply = await claude.run(prompt, on_event=reporter.on_event)
        finally:
            await reporter.finish()
    """

    def __init__(
        self,
        chat_id: int,
        thread_id: Optional[int],
        bot,
        *,
        reply_to_message_id: Optional[int] = None,
        delay_seconds: float = DEFAULT_DELAY_SECONDS,
    ) -> None:
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._bot = bot
        self._reply_to = reply_to_message_id
        self._delay = delay_seconds

        self._message_id: Optional[int] = None
        self._last_label: Optional[str] = None
        self._last_edit_at: float = 0.0
        self._started_at: float = 0.0
        self._lock = asyncio.Lock()
        self._delay_task: Optional[asyncio.Task] = None
        self._closed = False
        # Contador de tool_use vistos no stream — usado pelo handler pra
        # logar "quanto trabalho o Claude fez nessa mensagem". Inclui
        # ações de subagentes, pra refletir o esforço real.
        self.tool_call_count: int = 0
        # True assim que o agente emite um `kobe-notify` no turno (o ack que
        # nomeia a ação — vide CLAUDE.md "Avisa antes de agir"). O handler usa
        # isso na retaguarda do despacho: se o Hal JÁ avisou o operador, não
        # mandamos o aviso enlatado de background por cima (evita aviso duplo).
        self.acked: bool = False

    async def start(self) -> None:
        """Inicia o timer pra status default ("trabalhando…")."""
        self._started_at = time.monotonic()
        self._delay_task = asyncio.create_task(self._delayed_default_status())

    async def _delayed_default_status(self) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return
        # Só dispara se ainda nada foi mostrado.
        await self._set_status("trabalhando…")

    async def on_event(self, event: dict) -> None:
        """Recebe um evento JSON do stream-json do Claude e atualiza UI."""
        if self._closed:
            return
        etype = event.get("type")
        if etype != "assistant":
            return
        msg = event.get("message") or {}
        is_subagent = bool(event.get("parent_tool_use_id"))
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            name = block.get("name") or ""
            if not name:
                continue
            # Conta TODA tool_use (inclusive de subagente) — reflete o
            # esforço real da resposta. O filtro de UI vem depois.
            self.tool_call_count += 1
            # Ack-detection: um Bash chamando `kobe-notify` é o agente avisando
            # o operador na própria voz. Substring cobre qualquer forma de
            # invocação (`bot/bin/kobe-notify`, path absoluto, `python …`).
            if name == "Bash":
                cmd = (block.get("input") or {}).get("command") or ""
                if "kobe-notify" in cmd:
                    self.acked = True
            # Subagentes: ações internas já cobertas por "delegando
            # subtarefa" no evento pai; não detalhamos aqui.
            if is_subagent:
                continue
            # TodoWrite e ScheduleWakeup são internos — não fazem sentido
            # pro operador. Idem nossos próprios reminders.
            if name in {"TodoWrite", "ScheduleWakeup"}:
                continue
            label = describe_tool_use(name, block.get("input") or {})
            await self._set_status(label)

    async def _set_status(self, label: str) -> None:
        """Envia ou edita a status message com `label`, respeitando throttle."""
        async with self._lock:
            if self._closed:
                return
            if label == self._last_label:
                return
            now = time.monotonic()
            if (
                self._message_id is not None
                and (now - self._last_edit_at) < MIN_EDIT_INTERVAL_SECONDS
            ):
                # Ignora updates muito rápidos — o próximo significativo
                # pega a vez. Não enfileiramos pra evitar lag perceptível.
                self._last_label = label  # registra pra de-dup
                return

            text = f"{STATUS_PREFIX}{label}"
            try:
                if self._message_id is None:
                    sent = await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=text,
                        message_thread_id=self._thread_id,
                        reply_to_message_id=self._reply_to,
                        disable_notification=True,
                    )
                    self._message_id = sent.message_id if sent is not None else None
                else:
                    await self._bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=self._message_id,
                        text=text,
                    )
            except Exception:  # noqa: BLE001 — rede/Telegram, não derrubar fluxo
                logger.debug("falha mostrando status; ignorando", exc_info=True)
                return

            self._last_label = label
            self._last_edit_at = now

    async def finish(self, *, delete: bool = True) -> Optional[int]:
        """Encerra o reporter. Por padrão apaga a status (resposta final
        substitui). Retorna o `message_id` se a status existiu.
        """
        self._closed = True
        if self._delay_task is not None:
            self._delay_task.cancel()
            try:
                await self._delay_task
            except asyncio.CancelledError:
                pass
        if self._message_id is None:
            return None
        if delete:
            try:
                await self._bot.delete_message(
                    chat_id=self._chat_id,
                    message_id=self._message_id,
                )
            except Exception:  # noqa: BLE001
                logger.debug("falha apagando status; ignorando", exc_info=True)
        return self._message_id
