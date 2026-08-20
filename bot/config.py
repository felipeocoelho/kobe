"""Carrega e valida variáveis de ambiente do Kobe."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from bot import reactions


logger = logging.getLogger("kobe.config")


class ConfigError(Exception):
    """Configuração ausente ou inválida."""


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    allowed_user_ids: frozenset[int]
    supabase_url: str
    supabase_key: str
    groq_api_key: str
    kobe_home: Path
    kobe_claude_cwd: Path
    log_level: str
    claude_timeout_seconds: int
    recent_messages_limit: int
    compact_threshold_messages: int
    anthropic_api_key: Optional[str]
    assemblyai_api_key: Optional[str]
    openai_api_key: Optional[str]
    chat_manager_enabled: bool
    # Memória de trabalho (Highlander Frente 0 — decouple): governa a JANELA de
    # memória (imediata vs legado de sessão) e a compactação — decisões de
    # MEMÓRIA que ANTES pegavam carona na flag de CONVERSAS (chat_manager).
    # Agora separadas: chat_manager = só conversas; working_memory = só memória.
    # On (default): janela imediata + sem compactação (design Highlander).
    # Off: histórico de sessão legado + compactação aos 40 msgs (pré-Highlander).
    working_memory_enabled: bool
    # Núcleo curado global (Highlander Frente 1.2): auto-injeta USER.md +
    # MEMORY.md (identidade + fatos duráveis do agente) no topo do prompt, com
    # teto e sinal de consolidação. Off = comportamento de hoje (USER.md só
    # entra se o agente o ler). Ver bot/memory/curated_core.py.
    curated_core_enabled: bool
    # Sinais de grounding baratos (Highlander Frente 1.1): injeta no prompt o
    # que muda com o tempo e o agente senão narraria de memória — hoje, há
    # quanto tempo foi a última troca neste tópico (anti-confabulação temporal
    # + lembrete de retomada). Off = comportamento de hoje. Ver bot/memory/grounding.py.
    grounding_signals_enabled: bool
    # Gate de estado de background vivo (Highlander v2, P1): o código lê os arquivos
    # de estado dos trabalhos de background do tópico (Coder/Atrus) NESTE turno e
    # injeta o fato vivo + a regra dura "use isto, não memória" — pra o agente não
    # narrar status de sala/job de memória (a dor da "sala esperando" que já acabou).
    # Read-only, best-effort. Off = comportamento de hoje. Ver bot/memory/background_state.py.
    background_state_gate_enabled: bool
    # Memória durável via Hindsight (Highlander Frente 2.3): recall na entrada
    # (traz fato durável relevante pro prompt) + retain no fim do turno (destila
    # fato da msg do operador). Serviço REST no host (infra/hindsight/). Off =
    # Kobe como hoje. Ver bot/hindsight_client.py.
    #
    # `hindsight_enabled` é o MASTER kill-switch (off = nem retain nem recall).
    # Highlander v2 (F1) separou retain e recall em flags próprias, porque eles
    # têm perfis de risco opostos:
    #  - RETAIN (escrita) é silencioso e barato → fica LIGADO (constrói a memória).
    #  - RECALL (leitura injetada todo turno) é o vetor de confabulação que a
    #    Auditoria nomeou (destilado por LLM entra no prompt como "fato") →
    #    fica DESLIGADO até a régua medir e a F3 re-fiar pro best-practice.
    # Efetivo: retain = master AND retain_enabled; recall = master AND recall_enabled.
    hindsight_enabled: bool
    hindsight_retain_enabled: bool
    hindsight_recall_enabled: bool
    hindsight_base_url: str
    hindsight_timeout_seconds: float
    hindsight_recall_limit: int
    # Despacho de turno pesado em background (cascata de filtros). Quando
    # ligado, a ENTRADA do turno classifica se o pedido vai gerar trabalho
    # pesado e, se for, despacha o `claude -p` em background fora do lock do
    # tópico — mantendo a linha livre pro próximo pedido. Ver
    # bot/turn_classifier.py e docs/runbooks/despacho-turno-pesado.md.
    heavy_dispatch_enabled: bool
    # Retaguarda (teto de tempo): turno que entrou foreground mas estoura
    # estes segundos segurando o lock se promove sozinho pra background.
    heavy_promote_after_seconds: float
    # Cortes do placar da cascata: score >= HIGH → background na entrada;
    # score <= LOW → foreground; faixa do meio (LOW < score < HIGH) →
    # acorda o GPT-4o-mini pra desempatar (zona cinza).
    heavy_score_high: int
    heavy_score_low: int
    # Teto de tempo do `claude -p` quando o turno corre no caminho de despacho
    # pesado (background, na previsão OU na promoção). Por definição é o turno
    # PESADO — varredura, pesquisa, vários passos — então 300s (o default do
    # foreground) corta no meio e a resposta chega truncada. Dimensionado pro
    # trabalho pesado real, não infinito. Só vale com heavy_dispatch_enabled.
    heavy_timeout_seconds: int
    # Janela do watchdog de ACK no background: se a run de bg não emitir um
    # `kobe-notify` (ack na voz do Hal) dentro destes segundos e ainda estiver
    # rodando, o código manda o aviso enlatado como piso garantido — o operador
    # nunca fica no escuro mesmo que o Hal não acke. Ver telegram_handler.
    heavy_ack_fallback_seconds: float
    # ── Nova arquitetura de borda ─────────────────────────────────────────
    # Peça D (anexos multimodais): quando ligado, a borda aceita QUALQUER anexo
    # (paridade single-tenant com o Claude Desktop), salva o original em
    # uploads/ do tópico (separado do knowledge/ curado), cataloga num markdown
    # único, e correlaciona anexo+instrução no mesmo turno (foto passa a
    # funcionar; caption entra no turno). Off = comportamento legado
    # (.txt/.md/.pdf/.docx → knowledge/, foto ignorada). Ver bot/uploads.py.
    edge_uploads_enabled: bool
    # Peça A (Message Assembler): quando ligado, mensagens picadas de um tópico
    # são agregadas por debounce de silêncio num intent único (1 turno) antes do
    # FIFO. Off = disparo imediato de hoje (1 msg = 1 turno). Ver bot/assembler.py.
    edge_assembler_enabled: bool
    # Janela de silêncio base (ms) antes de flushar o buffer. Adaptativa: uma
    # frase terminada em pontuação usa a janela curta (_terminated_ms).
    edge_assembler_quiet_ms: float
    edge_assembler_quiet_terminated_ms: float
    # Teto de espera (ms) desde o 1º fragmento — flusha mesmo se ainda chegam
    # mensagens, pra não represar indefinidamente.
    edge_assembler_max_wait_ms: float
    # Peça C (separação rascunho/resposta): quando ligado, a resposta final vem
    # LIMPA (só o texto após a última ferramenta); a prosa pré-ferramenta
    # (rascunho) aparece efêmera no canal de progresso e some. Off = concatena
    # tudo (comportamento de hoje, fix de 2026-06-01). Ver bot/claude_runner.py.
    edge_clean_response_enabled: bool
    # Peça B (Liveness Protocol): quando ligado, o ACK das tarefas pesadas vira
    # GARANTIA da borda (LIV-ack semântico escrito por modelo barato) e o aviso
    # enlatado de background é aposentado. Off = comportamento de hoje (ack
    # model-driven + enlatado + watchdog). Ver bot/liveness.py.
    edge_liveness_enabled: bool
    # ── Reações de recebimento (2026-08-20) ───────────────────────────────
    # Sinal de "chegou" imediato e impossível de alucinar (não passa por modelo):
    # 👀 quando a mensagem entra, ✍️ quando a transcrição do áudio fica pronta.
    # Off = comportamento de hoje (nenhuma reação). Ver bot/reactions.py.
    telegram_reactions_enabled: bool
    telegram_reaction_received: str
    telegram_reaction_transcribed: str
    # Nota: as chaves do ack do Liveness (LIVENESS_ACK_GUARD_ENABLED,
    # LIVENESS_ACK_PROVIDER, LIVENESS_ACK_MODEL) são lidas direto do ambiente
    # em bot/liveness.py — mesmo padrão do Mission Control. Ver .env.example.


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Variável obrigatória ausente: {name}")
    return value


def _parse_user_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_ALLOWED_USER_IDS contém valor não-numérico: {chunk!r}"
            ) from exc
    if not ids:
        raise ConfigError("TELEGRAM_ALLOWED_USER_IDS está vazio.")
    return frozenset(ids)


def load_config(env_path: Optional[Path] = None) -> Config:
    """Carrega .env (se existir) e valida variáveis obrigatórias."""
    if env_path is not None:
        load_dotenv(env_path)
    else:
        load_dotenv()

    kobe_home = Path(_require("KOBE_HOME")).expanduser().resolve()
    claude_cwd_raw = os.getenv("KOBE_CLAUDE_CWD") or str(kobe_home)
    kobe_claude_cwd = Path(claude_cwd_raw).expanduser().resolve()

    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        allowed_user_ids=_parse_user_ids(_require("TELEGRAM_ALLOWED_USER_IDS")),
        supabase_url=_require("SUPABASE_URL"),
        supabase_key=_require("SUPABASE_KEY"),
        groq_api_key=_require("GROQ_API_KEY"),
        kobe_home=kobe_home,
        kobe_claude_cwd=kobe_claude_cwd,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        claude_timeout_seconds=int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "300")),
        recent_messages_limit=int(os.getenv("RECENT_MESSAGES_LIMIT", "20")),
        compact_threshold_messages=int(os.getenv("COMPACT_THRESHOLD_MESSAGES", "40")),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        assemblyai_api_key=os.getenv("ASSEMBLYAI_API_KEY") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        chat_manager_enabled=_parse_bool(os.getenv("CHAT_MANAGER_ENABLED")),
        # Memória de trabalho desacoplada da flag de conversas (Frente 0).
        # Default-ON: a janela imediata é o design Highlander. Prod hoje roda
        # com Chat Manager on (= janela imediata), então default-on preserva o
        # comportamento. Desligar (=false) volta ao histórico de sessão + compactação.
        working_memory_enabled=_parse_bool(os.getenv("WORKING_MEMORY_ENABLED", "true")),
        # Highlander: default-ON (decisão do operador 2026-06-24 — "não deixe
        # atrás de flag-off"). Pra desligar, setar a env como false. curated_core
        # e grounding são puro-cômputo (no-op gracioso se faltar arquivo/histórico);
        # hindsight é best-effort (se o serviço estiver fora, falha rápido e segue).
        curated_core_enabled=_parse_bool(os.getenv("CURATED_CORE_ENABLED", "true")),
        grounding_signals_enabled=_parse_bool(os.getenv("GROUNDING_SIGNALS_ENABLED", "true")),
        background_state_gate_enabled=_parse_bool(
            os.getenv("BACKGROUND_STATE_GATE_ENABLED", "true")
        ),
        hindsight_enabled=_parse_bool(os.getenv("HINDSIGHT_ENABLED", "true")),
        # F1 (Highlander v2): retain ON (segue construindo a memória em silêncio),
        # recall OFF por padrão (para de injetar o destilado todo turno — de-risca
        # a confabulação). Re-ligado na F3, medido pela régua. Reverter recall:
        # HINDSIGHT_RECALL=true. Master HINDSIGHT_ENABLED=false desliga os dois.
        hindsight_retain_enabled=_parse_bool(os.getenv("HINDSIGHT_RETAIN", "true")),
        hindsight_recall_enabled=_parse_bool(os.getenv("HINDSIGHT_RECALL", "false")),
        hindsight_base_url=os.getenv("HINDSIGHT_BASE_URL", "http://127.0.0.1:8888"),
        hindsight_timeout_seconds=float(os.getenv("HINDSIGHT_TIMEOUT_SECONDS", "10")),
        hindsight_recall_limit=int(os.getenv("HINDSIGHT_RECALL_LIMIT", "5")),
        heavy_dispatch_enabled=_parse_bool(os.getenv("HEAVY_DISPATCH_ENABLED")),
        heavy_promote_after_seconds=float(
            os.getenv("HEAVY_DISPATCH_PROMOTE_AFTER_SECONDS", "12")
        ),
        heavy_score_high=int(os.getenv("HEAVY_DISPATCH_SCORE_HIGH", "6")),
        heavy_score_low=int(os.getenv("HEAVY_DISPATCH_SCORE_LOW", "2")),
        heavy_timeout_seconds=int(
            os.getenv("HEAVY_DISPATCH_TIMEOUT_SECONDS", "1200")
        ),
        heavy_ack_fallback_seconds=float(
            os.getenv("HEAVY_DISPATCH_ACK_FALLBACK_SECONDS", "20")
        ),
        # Nova arquitetura de borda — Peça D (anexos). Default OFF: rollback
        # trivial (flag off + restart) volta ao on_document legado e à foto
        # ignorada. Ligar quando validado.
        edge_uploads_enabled=_parse_bool(os.getenv("EDGE_UPLOADS_ENABLED")),
        # Peça A (Message Assembler). Default OFF: rollback trivial volta ao
        # disparo imediato (1 msg = 1 turno). Janelas tunáveis (calibração no uso).
        edge_assembler_enabled=_parse_bool(os.getenv("EDGE_ASSEMBLER_ENABLED")),
        edge_assembler_quiet_ms=float(os.getenv("EDGE_ASSEMBLER_QUIET_MS", "2500")),
        edge_assembler_quiet_terminated_ms=float(
            os.getenv("EDGE_ASSEMBLER_QUIET_TERMINATED_MS", "700")
        ),
        edge_assembler_max_wait_ms=float(
            os.getenv("EDGE_ASSEMBLER_MAX_WAIT_MS", "9000")
        ),
        # Peça C (resposta limpa). Default OFF: rollback trivial volta à
        # concatenação de todos os blocos de texto (fix de 2026-06-01).
        edge_clean_response_enabled=_parse_bool(
            os.getenv("EDGE_CLEAN_RESPONSE_ENABLED")
        ),
        # Peça B (Liveness). Default OFF: rollback trivial volta ao ack
        # model-driven + enlatado + watchdog de hoje.
        edge_liveness_enabled=_parse_bool(os.getenv("EDGE_LIVENESS_ENABLED")),
        # Reações de recebimento. Default OFF: muda comportamento VISÍVEL no chat,
        # então vale validação do operador antes de ligar. Rollback: flag off +
        # restart. Emojis configuráveis porque a lista permitida é do Telegram e
        # pode mudar sem aviso (🎧/👂, por exemplo, NÃO são aceitos).
        telegram_reactions_enabled=_parse_bool(
            os.getenv("TELEGRAM_REACTIONS_ENABLED")
        ),
        telegram_reaction_received=_reaction_from_env(
            "TELEGRAM_REACTION_RECEIVED", reactions.DEFAULT_RECEIVED
        ),
        telegram_reaction_transcribed=_reaction_from_env(
            "TELEGRAM_REACTION_TRANSCRIBED", reactions.DEFAULT_TRANSCRIBED
        ),
    )


def _parse_bool(raw: Optional[str]) -> bool:
    if not raw:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on", "enabled")


def _reaction_from_env(key: str, default: str) -> str:
    """Lê um emoji de reação do .env conferindo contra a lista do Bot API.

    Três casos, e a diferença entre eles é intencional:
    - variável ausente → o default do módulo (que já é uma forma válida);
    - variável definida e VAZIA → "" = estágio desligado de propósito, sem aviso
      (é o jeito documentado de desligar um dos estágios sem tocar em código);
    - emoji que o Telegram não aceita → AVISO nomeando chave, valor e o que foi
      usado no lugar, e cai no default.

    Por que cair no default em vez de simplesmente não reagir: o que vale é o
    SINAL ("chegou" / "transcrito"), não o desenho. Se o emoji estiver errado, é
    melhor o sinal continuar aparecendo com o padrão — e o log explicando — do
    que ele sumir calado, que é justamente a dor que a reação veio resolver.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    if not raw.strip():
        return ""
    normalized = reactions.normalize_reaction(raw)
    if normalized is None:
        logger.warning(
            "config: %s=%r não está na lista de reações aceitas pelo Bot API — "
            "usando %r no lugar. Emojis válidos: telegram.constants.ReactionEmoji "
            "(se o Telegram passou a aceitar esse, atualize a python-telegram-bot).",
            key, raw, default,
        )
        return default
    return normalized
