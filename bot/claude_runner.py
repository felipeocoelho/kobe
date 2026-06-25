"""Wrapper async do Claude Code CLI (`claude -p`).

O bot Python é uma camada de transporte: monta um prompt com contexto
(histórico da sessão + mensagem nova) e dispara `claude -p` no diretório
do Kobe. O Claude Code, lá dentro, faz auto-discovery do `CLAUDE.md` da
raiz — que carrega SOUL/USER/PREFERENCES — e responde com a personalidade
do agente.

O prompt é enviado via stdin (não via argumento) pra evitar limites de
tamanho de linha de comando. `--permission-mode bypassPermissions` é
necessário porque em modo `-p` (não-interativo) qualquer prompt de
permissão trava o processo até timeout.

Saída em `stream-json` (linha por linha) — permite ao bot capturar
eventos de uso de ferramenta enquanto o Claude trabalha e devolver
"sinais de vida" pro operador no Telegram (typing + progresso textual
por etapa via `ProgressReporter`) em vez de só silêncio até a resposta
final. A resposta em si NÃO é streamada token-a-token pro Telegram
(decisão 2026-06-01: streaming editado a cada token é UX ruim em
mensageiro — "pior a emenda que o soneto"); ela sai inteira no fim,
montada da concatenação de TODOS os blocos de texto do turno.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Iterable, Optional
from zoneinfo import ZoneInfo


# Fuso do operador. Forçado explicitamente porque a VPS roda em UTC e
# "hoje/amanhã" do Felipe ancora no Brasil — caso contrário datas viram
# passado/futuro espelhado (bug observado: jogo "amanhã 12/05" lido como
# ontem porque o servidor já estava em 13/05 UTC).
OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")

# Tag que marca, no contexto do prompt, que aquele texto veio de uma
# mensagem de voz transcrita (Whisper/Groq ou AssemblyAI), não digitada.
# Fica só no prompt do agente — não é ecoada de volta no chat (o operador
# já sabe que mandou áudio). Serve pro Hal saber que pode haver ruído de
# transcrição e que o tom é de fala, não de texto escrito.
AUDIO_TRANSCRIBED_TAG = "🎤 [áudio transcrito]"

# Limite do buffer de leitura do stdout do `claude` (StreamReader do asyncio).
# O default do asyncio é 64KB; o stream-json do claude emite UMA linha por
# evento, e um resultado gordo de ferramenta (ler arquivo grande / fetch /
# tool_result extenso) estoura 64KB numa linha só → `readline()` levanta
# `ValueError` (LimitOverrunError) e, como o erro não é `ClaudeError`, derrubava
# o turno inteiro — prendendo o "digitando…" no foreground. 10MB cobre qualquer
# evento realista. (bug do "digitando…" fantasma, 2026-06-25)
STDOUT_BUFFER_LIMIT_BYTES = 10 * 1024 * 1024


logger = logging.getLogger("kobe.claude")


class ClaudeError(Exception):
    """Falha ao invocar ou obter resposta do Claude Code."""


class ClaudeTimeoutError(ClaudeError):
    """Claude não respondeu dentro de `timeout_seconds`.

    `partial_text` carrega os blocos de texto do agente já COMPLETADOS
    até o corte (pode ser vazio se estourou no meio do 1º bloco). O
    handler usa isso pra entregar a resposta parcial em vez de descartar
    todo o trabalho quando o turno estoura o tempo limite.
    """

    def __init__(self, message: str, *, partial_text: str = "") -> None:
        self.partial_text = partial_text
        super().__init__(message)


class ClaudeNotFoundError(ClaudeError):
    """O binário do Claude Code não está no PATH do serviço."""


class ClaudeExitError(ClaudeError):
    """Claude terminou com exit code != 0 (problema no próprio CLI)."""

    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(stderr or f"claude exit code {returncode}")


# Callback recebe um dict com o evento parseado do stream-json. Pode ser
# síncrono ou assíncrono — o runner aguarda se for awaitable.
EventCallback = Callable[[dict], "Awaitable[None] | None"]


@dataclass(frozen=True)
class ClaudeResult:
    """Resposta + métricas de uma chamada ao Claude. Tokens e custo vêm
    do evento `result` do stream-json (campo `usage` + `total_cost_usd`).
    Se o evento não chegar (caminho de fallback), os campos numéricos
    ficam em zero — log estruturado expõe isso naturalmente.
    """
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class ClaudeRunner:
    cwd: Path
    timeout_seconds: int
    binary: str = "claude"

    async def run(
        self,
        prompt: str,
        *,
        on_event: Optional[EventCallback] = None,
        chat_id: Optional[int] = None,
        thread_id: Optional[int] = None,
        bot_token: Optional[str] = None,
        timeout_override: Optional[int] = None,
    ) -> ClaudeResult:
        """Manda `prompt` via stdin pro Claude Code e retorna a resposta.

        Se `on_event` for fornecido, é chamado pra cada evento JSON do
        stream (system/assistant/user/result/etc.) — útil pra mostrar
        progresso ao usuário enquanto o Claude trabalha.

        `chat_id`, `thread_id` e `bot_token` (se fornecidos) são injetados
        no env do subprocess como `KOBE_CHAT_ID`, `KOBE_THREAD_ID` e
        `KOBE_TELEGRAM_BOT_TOKEN` — usados pelos helpers `bot/bin/kobe-notify`
        e `bot/bin/kobe-attach` pra plugins emitirem progresso/anexos em
        tempo real, sem precisar passar pela resposta final do agente.

        O texto final retornado é a concatenação de TODOS os blocos de
        texto do AGENTE PRINCIPAL no turno (eventos `assistant` com
        `parent_tool_use_id` nulo). Isso é deliberado: o campo `result`
        do evento final carrega só a ÚLTIMA mensagem do assistant (o
        bloco emitido depois da última tool call) — usá-lo engolia toda
        a prosa que o agente escreveu ANTES de uma ferramenta (bug
        2026-06-01: resposta longa virava só o "Anotado em…" pós-tool).
        O `result` segue sendo lido pra métricas (usage/custo) e como
        fallback se, por algum path raro, nenhum bloco `assistant` vier.
        """
        cmd = [
            self.binary,
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--verbose",
        ]

        env = dict(os.environ)
        if bot_token:
            env["KOBE_TELEGRAM_BOT_TOKEN"] = bot_token
        if chat_id is not None:
            env["KOBE_CHAT_ID"] = str(chat_id)
        if thread_id is not None:
            env["KOBE_THREAD_ID"] = str(thread_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Buffer maior que o default de 64KB: o stream-json do claude
                # emite uma linha por evento e um tool_result gordo estoura 64KB
                # numa linha só (ver STDOUT_BUFFER_LIMIT_BYTES).
                limit=STDOUT_BUFFER_LIMIT_BYTES,
            )
        except FileNotFoundError as exc:
            raise ClaudeNotFoundError(
                f"CLI {self.binary!r} não encontrado no PATH."
            ) from exc

        # Envia o prompt e fecha stdin pra Claude saber que terminou.
        assert proc.stdin is not None
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

        result_text: Optional[str] = None
        result_usage: dict = {}
        result_cost: float = 0.0
        # Blocos de texto do AGENTE PRINCIPAL, na ordem em que o turno os
        # emitiu (prosa antes de uma tool + texto depois dela). É a fonte
        # de verdade da resposta final — e, no timeout, do parcial já
        # pronto. Texto de subagente (parent_tool_use_id != None) é
        # ignorado: não é a resposta ao operador.
        assistant_texts: list[str] = []
        # Buffer dos eventos parseados — usado pra dump diagnóstico quando
        # a resposta final vier vazia (acontece raro mas precisamos de
        # evidência pra entender em qual cenário do Claude isso dispara).
        raw_events: list[dict] = []
        non_json_lines: int = 0

        async def _consume_stdout() -> None:
            nonlocal result_text, non_json_lines, result_usage, result_cost
            assert proc.stdout is not None
            while True:
                try:
                    line = await proc.stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    # Degradação amigável: mesmo com o buffer de 10MB, uma linha
                    # pathológica > limite ainda estouraria. Converte em
                    # ClaudeError pro `_resolve_claude` tratar (mensagem amigável
                    # ao operador) em vez de derrubar o turno e prender o typing.
                    raise ClaudeError(
                        f"linha do stream do claude excedeu o buffer de "
                        f"{STDOUT_BUFFER_LIMIT_BYTES} bytes"
                    ) from exc
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    non_json_lines += 1
                    logger.debug("linha não-JSON ignorada: %s", line[:200])
                    continue

                raw_events.append(event)

                # Capta resultado final / fallbacks de texto.
                etype = event.get("type")
                if etype == "result":
                    result_text = (event.get("result") or "").strip() or None
                    # Métricas (v0.12): usage e custo só vêm aqui. Pode ser
                    # None em paths de erro — guardamos dict vazio então.
                    result_usage = event.get("usage") or {}
                    try:
                        result_cost = float(event.get("total_cost_usd") or 0.0)
                    except (TypeError, ValueError):
                        result_cost = 0.0
                elif etype == "assistant" and not event.get("parent_tool_use_id"):
                    # Só o agente principal (parent_tool_use_id nulo). Cada
                    # evento `assistant` é uma mensagem COMPLETA — junta os
                    # blocos de texto dela na ordem. Acumular aqui (em vez
                    # de pegar só o `result` final) é o que preserva a
                    # prosa escrita ANTES de uma tool call.
                    msg = event.get("message") or {}
                    for block in msg.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "text":
                            txt = block.get("text") or ""
                            if txt:
                                assistant_texts.append(txt)

                if on_event is not None:
                    try:
                        maybe = on_event(event)
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    except Exception:  # noqa: BLE001 — callback não deve derrubar
                        logger.exception("on_event raised; seguindo")

        async def _consume_stderr() -> bytes:
            assert proc.stderr is not None
            return await proc.stderr.read()

        # Teto efetivo do turno: o override (caminho de despacho pesado, que
        # passa um teto maior dimensionado pro turno PESADO) vence o default
        # do runner. Sem override → comportamento clássico (self.timeout_seconds).
        effective_timeout = timeout_override or self.timeout_seconds
        try:
            stderr_task = asyncio.create_task(_consume_stderr())
            await asyncio.wait_for(_consume_stdout(), timeout=effective_timeout)
            stderr_bytes = await stderr_task
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ClaudeTimeoutError(
                f"Claude não respondeu em {effective_timeout}s.",
                partial_text=_join_texts(assistant_texts),
            ) from exc
        except ClaudeError:
            # Overrun do buffer (única origem de ClaudeError neste bloco): o
            # claude segue em voo, bloqueado escrevendo no pipe cheio. Mata o
            # subprocess e drena o stderr_task pra NÃO vazar processo/task, e
            # re-levanta pro `_resolve_claude` virar mensagem amigável.
            proc.kill()
            await proc.wait()
            stderr_task.cancel()
            try:
                await stderr_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            raise

        if proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            # Loga o stderr inteiro (sem truncar) — em diagnóstico de
            # erro do CLI a parte útil costuma vir no fim do output.
            logger.warning(
                "claude exit=%s stderr=%s",
                proc.returncode,
                stderr or "(vazio)",
            )
            raise ClaudeExitError(proc.returncode, stderr)

        def _result(text: str) -> ClaudeResult:
            return ClaudeResult(
                text=text,
                input_tokens=int(result_usage.get("input_tokens") or 0),
                output_tokens=int(result_usage.get("output_tokens") or 0),
                cache_read_tokens=int(result_usage.get("cache_read_input_tokens") or 0),
                cache_creation_tokens=int(
                    result_usage.get("cache_creation_input_tokens") or 0
                ),
                cost_usd=result_cost,
            )

        # Resposta = concatenação de TODOS os blocos de texto do agente
        # principal (prosa antes de tools + texto depois). É o que corrige
        # o bug de engolir a prosa pré-tool.
        joined = _join_texts(assistant_texts)
        if joined:
            return _result(joined)
        # Fallback raro: nenhum bloco `assistant` foi capturado, mas o
        # evento `result` trouxe texto. Melhor que devolver vazio.
        if result_text:
            return _result(result_text)

        # Resposta totalmente vazia. Não levantamos exceção (o caller
        # devolve uma mensagem amigável no Telegram), mas dumpamos o
        # stream completo + stderr pra `/tmp/` e logamos uma assinatura
        # do que apareceu — assim qualquer reincidência tem evidência
        # imediata pra root cause.
        dump_path = _dump_empty_stream(raw_events, stderr_bytes, non_json_lines)
        types_seen = Counter(e.get("type", "?") for e in raw_events)
        logger.warning(
            "claude_empty events=%d types=%s non_json_lines=%d "
            "stderr_bytes=%d dump=%s",
            len(raw_events),
            dict(types_seen),
            non_json_lines,
            len(stderr_bytes),
            dump_path,
        )
        return _result("")


def _join_texts(texts: list[str]) -> str:
    """Concatena blocos de texto do agente, separados por linha em branco.

    Cada bloco é strip-ado nas pontas; blocos vazios são descartados. A
    linha em branco entre blocos separa a prosa de antes de uma tool do
    texto emitido depois — sem isso correriam juntos no Telegram
    ("…fim da fraseAnotado em X").
    """
    return "\n\n".join(t.strip() for t in texts if t.strip()).strip()


def _dump_empty_stream(
    events: list[dict], stderr_bytes: bytes, non_json_lines: int
) -> str:
    """Persiste o stream cru num arquivo de diagnóstico em `/tmp/`.

    Salvamos como JSONL pra inspeção rápida com `jq` / leitura linear,
    e anexamos o stderr e contadores no fim como comentário. Retorna o
    path absoluto pro caller logar.
    """
    fd, path = tempfile.mkstemp(
        prefix="kobe-claude-empty-", suffix=".jsonl", dir="/tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event, ensure_ascii=False, default=str))
                fh.write("\n")
            fh.write(f"# events_count={len(events)}\n")
            fh.write(f"# non_json_lines={non_json_lines}\n")
            if stderr_bytes:
                fh.write("# --- stderr ---\n")
                for ln in stderr_bytes.decode("utf-8", errors="replace").splitlines():
                    fh.write(f"# {ln}\n")
            else:
                fh.write("# stderr=(vazio)\n")
    except OSError:
        logger.exception("falha gravando dump de stream vazio em %s", path)
    return path


def build_prompt(
    *,
    thread_id: Optional[int],
    history: Iterable[dict],
    new_message: str,
    plugins_section: str = "",
    topic_context: Optional[str] = None,
    missao_ativa_info: Optional[str] = None,
    alertas_abertos_info: Optional[str] = None,
    conversation_active: Optional[dict] = None,
    conversation_summaries: Optional[list[dict]] = None,
    chat_manager_section: Optional[str] = None,
    curated_core: Optional[str] = None,
    grounding_signals: Optional[str] = None,
    background_state: Optional[str] = None,
    durable_memory: Optional[str] = None,
    audio_transcribed: bool = False,
    background_handoff: Optional[str] = None,
    quoted_message: Optional[str] = None,
) -> str:
    """Monta o prompt que vai pro `claude -p`.

    Mantemos minimal: identidade e regras vivem no `CLAUDE.md` (que o
    Claude Code lê via auto-discovery no `cwd`). Aqui só damos o contexto
    dinâmico — qual tópico, o histórico recente, plugins instalados (se
    houver), conhecimento curado do tópico (se houver) e a mensagem nova.

    `topic_context` é o output de `topic_manager.load_topic_context`
    (concatenação de `prompt.md` + `knowledge/*` do tópico). Vem antes do
    histórico pra funcionar como instrução de base — o histórico é
    consequência dela.

    `missao_ativa_info` (v0.13): quando há missão ativa no tópico E o
    orquestrador da missão triou a mensagem como "não é sobre a missão",
    o bot routea pra cá com essa linha extra de ciência (formato
    `[Missão ativa: <id> — "<objetivo>"]`). Só uma linha — sem inflar
    contexto. Hal sabe que existe missão rolando sem precisar gerenciá-la.
    """
    topic_label = (
        f"telegram_thread_id={thread_id}" if thread_id is not None else "geral"
    )
    now_br = datetime.now(OPERATOR_TZ)
    parts: list[str] = []
    # Nota de handoff de background (Fase C): quando o turno foi roteado pra
    # rodar em segundo plano na ENTRADA (previsão do classificador), a run é
    # um `claude -p` fresco e sem memória da decisão. Esta nota é a única
    # forma de ela saber que está em bg, por quê, e como se portar (avisar na
    # própria voz, reler a janela fresca antes de agir). Vai PRIMEIRO, antes
    # de qualquer contexto, pra ser o que a run lê de cara.
    if background_handoff:
        parts.append(background_handoff)
        parts.append("")
    parts.extend(
        [
            f"[Telegram] tópico: {topic_label}",
            f"[Agora (America/Sao_Paulo)] {now_br.isoformat(timespec='minutes')}",
        ]
    )

    # Sinal de grounding temporal (Highlander Frente 1.1): há quanto tempo foi a
    # última troca. Cola junto ao [Agora] porque é da mesma natureza (tempo).
    if grounding_signals:
        parts.append(grounding_signals)

    # Estado de background vivo (Highlander v2, P1): o código leu AGORA os arquivos
    # de estado dos trabalhos de background deste tópico (Coder/Atrus) e cola o fato
    # vivo + a regra dura "use isto, não memória". Vai junto do grounding porque é da
    # mesma natureza (estado que MUDA e o agente senão narraria de memória). None
    # quando não há trabalho recente — aí o agente não tem status a afirmar.
    if background_state:
        parts.append("")
        parts.append(background_state)

    # Núcleo curado (Highlander Frente 1.2): identidade do operador (USER.md) +
    # fatos duráveis do agente (MEMORY.md), auto-injetados. Vai logo após o
    # cabeçalho [Agora] e antes de tudo, como base de identidade — o resto do
    # contexto é consequência de quem é o operador e do que o agente já sabe.
    if curated_core:
        parts.append("")
        parts.append(curated_core)

    if missao_ativa_info:
        parts.append(missao_ativa_info)

    if alertas_abertos_info:
        parts.append("")
        parts.append(alertas_abertos_info)

    if plugins_section:
        parts.append("")
        parts.append(plugins_section)

    if topic_context:
        parts.append("")
        parts.append("[Contexto do tópico]")
        parts.append(topic_context)

    # New Chat Manager (2026-06-01): bloco residente já mastigado pelo
    # daemon — ponteiro do quente + catálogo frio + relações + instruções
    # de pull sob demanda. Substitui o render legado de conversation_active
    # quando presente (caminho novo do CHAT_MANAGER_ENABLED).
    if chat_manager_section:
        parts.append("")
        parts.append(chat_manager_section)

    # Memória durável recuperada (Highlander Frente 2.3): fatos do Hindsight
    # relevantes pra mensagem atual — o "trazer assunto velho de volta". Vem
    # como PISTA cética (a própria seção avisa pra confirmar contra a fonte).
    if durable_memory:
        parts.append("")
        parts.append(durable_memory)

    # Chat Manager (Fase 4 — legado): header da conversation ativa +
    # cronologia comprimida das sessions arquivadas. Só no caminho antigo
    # (mantido pra compat; o caminho novo passa chat_manager_section).
    if conversation_active:
        parts.append("")
        title = conversation_active.get("title") or "(sem título)"
        started = conversation_active.get("started_at") or ""
        n_summaries = len(conversation_summaries or [])
        parts.append(
            f"[Conversation ativa: '{title}' — iniciada em {started[:10]}, "
            f"{n_summaries} session(s) arquivada(s)]"
        )

    if conversation_summaries:
        parts.append("")
        parts.append("[Cronologia comprimida (sessions arquivadas desta conversation)]")
        for i, s in enumerate(conversation_summaries, 1):
            started = (s.get("started_at") or "")[:10]
            summary = (s.get("summary") or "").strip()
            parts.append(f"— Session {i} ({started}): {summary}")

    history_lines: list[str] = []
    for msg in history:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        # Mesma tag de áudio no histórico: mensagens antigas que vieram de
        # voz aparecem marcadas, mantendo a leitura consistente turno a turno
        # (o flag `audio_transcribed` é carregado junto do histórico).
        if msg.get("audio_transcribed"):
            content = f"{AUDIO_TRANSCRIBED_TAG} {content}"
        history_lines.append(f"{role}: {content}")
    if history_lines:
        parts.append("")
        parts.append("[Histórico recente da sessão ativa]")
        parts.extend(history_lines)

    # Mensagem citada (reply do Telegram): o operador respondeu CITANDO uma
    # mensagem anterior e emendou texto novo. A citada é o TEMA principal do
    # turno (não pano de fundo) — sem ela o agente fica cego de metade do
    # contexto. Vai colada à mensagem nova, logo antes dela, pra a relação
    # "isto é sobre aquilo" ficar explícita.
    if quoted_message:
        parts.append("")
        parts.append(
            "[O operador respondeu CITANDO esta mensagem — é o contexto "
            "principal do que ele diz a seguir]"
        )
        parts.append(quoted_message)

    parts.append("")
    parts.append("[Mensagem nova do operador]")
    parts.append(
        f"{AUDIO_TRANSCRIBED_TAG} {new_message}" if audio_transcribed else new_message
    )
    parts.append("")
    parts.append("Responda agora, em português, no estilo do agente.")
    return "\n".join(parts)
