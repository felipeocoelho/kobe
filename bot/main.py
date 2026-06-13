"""Entrypoint do bot Kobe.

Fase 7 (comandos especiais): texto/áudio segue o pipeline Claude da Fase 6,
e os comandos `/nova`, `/contexto`, `/salvar` e `/retomar` mexem direto na
memória persistente (sessions / saved_artifacts) sem invocar o Claude.
"""

from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

import asyncio

from bot.claude_runner import ClaudeRunner
from bot.cleanup import cleanup_loop
from bot.config import Config, ConfigError, load_config
from bot.db import build_client
from bot.plugins import (
    build_capability_index,
    discover_plugins,
    render_plugins_section,
    sync_agent_symlinks,
)
from bot.resume import resume_pending_snapshots
from bot.snapshot import (
    cleanup_expired_snapshots,
    load_pending_snapshots,
    save_pending_snapshots,
)
from bot.missoes.handlers import (
    on_command_missao,
    on_command_missao_abortar,
    on_command_missao_lista,
    on_command_missao_status,
)
from bot.alertas.handlers import (
    on_command_alerta_apagar,
    on_command_alerta_lista,
    on_command_alerta_pausar,
    on_command_alerta_retomar,
)
from bot.chat_manager_commands import (
    on_command_conversa,
    on_command_conversas_global,
    on_command_conversas_topico,
    on_command_renomear,
    on_command_retomar_short,
)
from bot.telegram_handler import (
    on_command_contexto,
    on_command_handoff,
    on_command_nova,
    on_command_retomar,
    on_command_salvar,
    on_document,
    on_forum_topic_closed,
    on_forum_topic_created,
    on_forum_topic_edited,
    on_forum_topic_reopened,
    on_error,
    on_text,
    on_voice,
    send_welcome,
)
from bot.topic_manager import list_unwelcomed_topics
from bot.transcribe import Transcriber


logger = logging.getLogger("kobe.bot")


# Slash commands do core do Kobe que aparecem no menu do Telegram.
# Plugins adicionam mais via campo `slash_commands` no manifest.
# Limite Telegram: 100 comandos no total, cada description ≤ 256 chars.
_CORE_SLASH_COMMANDS: list[BotCommand] = [
    BotCommand("nova", "Arquivar sessão atual e começar uma nova"),
    BotCommand("contexto", "Mostrar resumo da memória ativa do tópico"),
    BotCommand("salvar", "Salvar a conversa como artefato"),
    BotCommand("retomar", "Buscar um artefato salvo anteriormente"),
    BotCommand("handoff", "Destilar sessão atual em handoff doc"),
    BotCommand("conversas_topico", "Listar conversas do tópico atual"),
    BotCommand("conversas_global", "Listar conversas de todos os tópicos"),
    BotCommand("conversa", "Buscar e abrir conversa específica"),
    BotCommand("renomear", "Renomear a conversa ativa"),
    BotCommand("missao", "Abrir nova missão coordenada (multi-tarefa)"),
    BotCommand("missao_status", "Snapshot do painel da missão ativa"),
    BotCommand("missao_abortar", "Abortar a missão ativa neste tópico"),
    BotCommand("missao_lista", "Listar missões deste tópico (ativas + recentes)"),
    BotCommand("alerta_lista", "Listar alertas deste tópico"),
    BotCommand("alerta_pausar", "Pausar um alerta (sem apagar)"),
    BotCommand("alerta_retomar", "Retomar um alerta pausado"),
    BotCommand("alerta_apagar", "Apagar um alerta de vez"),
]


async def _on_startup(app: Application) -> None:
    """Pós-init, pré-polling: descoberta de plugins + consumo de snapshots.

    Sequência:
    1. Descobre plugins instalados e sincroniza os symlinks de subagentes
       — feito no startup pra refletir qualquer `install-plugin.sh` que
       tenha rodado desde o último boot.
    2. Registra slash commands no menu do Telegram (core + plugins).
    3. Limpa snapshots expirados (TTL excedido).
    4. Carrega os ainda válidos e manda uma mensagem proativa em cada
       tópico, sinalizando o retorno e citando a última fala do operador
       como gancho.
    5. Apaga cada snapshot após enviar — único uso, sem replay no
       próximo boot.
    """
    config: Config = app.bot_data["config"]
    plugins = discover_plugins(config.kobe_home)
    app.bot_data["plugins"] = plugins
    if plugins:
        linked = sync_agent_symlinks(config.kobe_home, plugins)
        logger.info(
            "startup: %d plugin(s) descoberto(s), %d symlink(s) de subagente",
            len(plugins),
            linked,
        )
    else:
        logger.info("startup: nenhum plugin instalado")

    # Kobe Integrations: monta o índice `capacidade → provedor` a partir dos
    # manifests. Conflito (duas capacidades iguais) não escolhe vencedor — fica
    # travado e é logado como ERROR (build_capability_index avisa). Guardamos o
    # índice em bot_data pra inspeção; a switchboard (bin/kobe-integrations) o
    # reconstrói por conta própria, já que roda detached.
    cap_index, cap_conflicts = build_capability_index(plugins)
    app.bot_data["capability_index"] = cap_index
    app.bot_data["capability_conflicts"] = cap_conflicts
    if cap_index or cap_conflicts:
        logger.info(
            "startup: %d capacidade(s) no índice de integrações, %d em conflito",
            len(cap_index),
            len(cap_conflicts),
        )

    # Menu do Telegram (auto-complete do "/"): core + plugins. Telegram
    # rejeita comandos duplicados, com hífen, ou >256 chars de descrição;
    # plugins.py já valida o name no parse, então aqui apenas concatenamos.
    plugin_cmds: list[BotCommand] = []
    seen_names: set[str] = {c.command for c in _CORE_SLASH_COMMANDS}
    for plugin in plugins:
        for entry in plugin.slash_commands:
            cname = entry["name"]
            if cname in seen_names:
                logger.warning(
                    "plugin %s: slash_command %r colide com nome já registrado — pulando",
                    plugin.name, cname,
                )
                continue
            seen_names.add(cname)
            plugin_cmds.append(BotCommand(cname, entry["description"]))
    all_cmds = _CORE_SLASH_COMMANDS + plugin_cmds
    try:
        await app.bot.set_my_commands(all_cmds)
        logger.info(
            "startup: menu Telegram atualizado com %d comando(s) (%d core + %d plugins)",
            len(all_cmds), len(_CORE_SLASH_COMMANDS), len(plugin_cmds),
        )
    except Exception:  # noqa: BLE001 — não derruba boot
        logger.exception("falha registrando menu de comandos no Telegram")

    # Cleanup background loop: roda imediato + repete a cada 6h enquanto
    # o bot estiver vivo. Guardamos a task em app.bot_data pra possível
    # cancelamento no shutdown (asyncio cancela automático ao final, mas
    # explícito é mais limpo).
    app.bot_data["cleanup_task"] = asyncio.create_task(
        cleanup_loop(config.kobe_home),
        name="kobe-cleanup-loop",
    )

    db = app.bot_data["db"]
    expired = cleanup_expired_snapshots(db)
    if expired:
        logger.info("startup: %d snapshot(s) expirado(s) limpo(s)", expired)

    pending = load_pending_snapshots(db)
    if pending:
        logger.info("startup: %d snapshot(s) pendente(s) — re-situando", len(pending))
        # Boot-resume (bug-retomada-contexto, 2026-06-04): em vez de só
        # pingar o operador com um template fixo, INVOCAMOS o agente com o
        # contexto imediato de cada tópico pra ele sintetizar onde a
        # conversa estava. Roda em background (não bloqueia o polling),
        # serializado por tópico via lock do telegram_handler. Cai no
        # template antigo (render_resume_message) se o agente falhar.
        await resume_pending_snapshots(app, pending)

    # Welcome retroativo (v0.11): tópicos pré-existentes nunca dispararam
    # `forum_topic_created` (ou dispararam antes da feature) e ainda não
    # receberam a msg de instruções. Mandamos uma vez por boot até estar
    # tudo onboardado. Idempotente — `welcomed_at` controla.
    try:
        unwelcomed = list_unwelcomed_topics(db)
    except Exception:  # noqa: BLE001
        logger.exception("startup: falha listando unwelcomed_topics")
        return
    if not unwelcomed:
        return
    logger.info(
        "startup: %d tópico(s) pendente(s) de boas-vindas — enviando",
        len(unwelcomed),
    )
    for t in unwelcomed:
        chat_id = t.get("telegram_chat_id")
        thread_id = t.get("telegram_thread_id")
        topic_id = t["id"]
        if chat_id is None:
            continue
        await send_welcome(
            app.bot,
            db,
            chat_id=chat_id,
            thread_id=thread_id,
            topic_id=topic_id,
        )


async def _on_shutdown(app: Application) -> None:
    """Pré-shutdown: salva snapshots das sessões ativas recentes.

    PTB invoca este hook ao receber SIGTERM/SIGINT (deploy, restart) —
    rodamos antes do polling fechar, com a conexão ao Supabase ainda
    viva. Falhas individuais são logadas dentro do snapshot e não
    abortam o shutdown.
    """
    # Cancela cleanup loop pra não deixar warning de task pendente.
    cleanup_task = app.bot_data.get("cleanup_task")
    if cleanup_task is not None:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    db = app.bot_data["db"]
    saved = save_pending_snapshots(db)
    logger.info("shutdown: %d snapshot(s) gravado(s) pra próximo boot", saved)


def build_application(config: Config) -> Application:
    # Timeouts do PTB são 5s por padrão — curto demais pra get_file/download
    # de áudio: voice messages mais longas (3+ min) chegaram a estourar só
    # no metadata fetch. Subimos pra valores generosos, ainda dentro da boa
    # prática do PTB pra long-polling clients.
    # concurrent_updates(True) deixa o PTB processar updates em paralelo
    # — combinado com o sequenciador FIFO por (chat_id, thread_id) em
    # telegram_handler (_TopicGate), garante que mensagens em tópicos
    # diferentes andam em paralelo, mas dentro de um mesmo tópico a seção
    # crítica roda uma por vez E na ORDEM DE CHEGADA (não na ordem em que
    # cada preparo — download/transcrição — termina). Ver telegram_handler.
    app = (
        ApplicationBuilder()
        .token(config.telegram_bot_token)
        .concurrent_updates(True)
        .connect_timeout(15)
        .read_timeout(30)
        .write_timeout(60)
        .pool_timeout(5)
        .media_write_timeout(120)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    app.bot_data["config"] = config
    app.bot_data["db"] = build_client(config)
    app.bot_data["transcriber"] = Transcriber(
        api_key=config.groq_api_key,
        hints_path=config.kobe_home / "user-data" / "transcription-hints.md",
        assemblyai_api_key=config.assemblyai_api_key,
    )
    app.bot_data["claude"] = ClaudeRunner(
        cwd=config.kobe_claude_cwd,
        timeout_seconds=config.claude_timeout_seconds,
    )
    app.add_handler(CommandHandler("nova", on_command_nova))
    app.add_handler(CommandHandler("contexto", on_command_contexto))
    app.add_handler(CommandHandler("salvar", on_command_salvar))
    app.add_handler(CommandHandler("retomar", on_command_retomar))
    app.add_handler(CommandHandler("handoff", on_command_handoff))
    # Sistema de Missões (v0.13)
    app.add_handler(CommandHandler("missao", on_command_missao))
    app.add_handler(CommandHandler("missao_status", on_command_missao_status))
    app.add_handler(CommandHandler("missao_abortar", on_command_missao_abortar))
    app.add_handler(CommandHandler("missao_lista", on_command_missao_lista))

    app.add_handler(CommandHandler("alerta_lista", on_command_alerta_lista))
    app.add_handler(CommandHandler("alerta_pausar", on_command_alerta_pausar))
    app.add_handler(CommandHandler("alerta_retomar", on_command_alerta_retomar))
    app.add_handler(CommandHandler("alerta_apagar", on_command_alerta_apagar))
    # Chat Manager (Fase 6) — handlers respondem mensagem explicativa se
    # CHAT_MANAGER_ENABLED=false, então é safe registrar mesmo com flag off.
    app.add_handler(CommandHandler("conversas_topico", on_command_conversas_topico))
    app.add_handler(CommandHandler("conversas_global", on_command_conversas_global))
    app.add_handler(CommandHandler("conversa", on_command_conversa))
    app.add_handler(CommandHandler("renomear", on_command_renomear))
    # Apolo (plugin WhatsApp) — handlers de gestão de contatos + grupos + inbox.
    # Envio em si é via subagente (Agent(subagent_type="apolo", ...)) — não tem
    # /apolo_enviar como comando direto.
    from bot.apolo_handlers import (  # noqa: E402
        on_command_contatos_buscar,
        on_command_contatos_listar,
        on_command_contatos_promover,
        on_command_whatsapp_grupos,
        on_command_whatsapp_inbox,
    )
    app.add_handler(CommandHandler("contatos_buscar", on_command_contatos_buscar))
    app.add_handler(CommandHandler("contatos_listar", on_command_contatos_listar))
    app.add_handler(CommandHandler("contatos_promover", on_command_contatos_promover))
    app.add_handler(CommandHandler("whatsapp_grupos", on_command_whatsapp_grupos))
    app.add_handler(CommandHandler("whatsapp_inbox", on_command_whatsapp_inbox))
    # /retomar_<id_curto> é gerado dinamicamente nas listagens; intercepta
    # via MessageHandler com regex (CommandHandler não suporta sufixo).
    # IMPORTANTE: tem que vir ANTES do MessageHandler(filters.TEXT, on_text)
    # genérico, senão on_text engole.
    app.add_handler(
        MessageHandler(
            filters.Regex(r"^/retomar_[0-9a-f]{6,16}(?:@\w+)?\s*$"),
            on_command_retomar_short,
        )
    )
    # Texto E commands desconhecidos vão pro mesmo handler: os
    # CommandHandler acima já consumem /nova /contexto /salvar /retomar;
    # qualquer outro `/comando` cai aqui e é repassado ao agente Claude,
    # que decide se delega pra plugin (ex: /transcrever pro Atrus) ou
    # trata como texto livre.
    app.add_handler(MessageHandler(filters.TEXT, on_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    # Upload de anexo na KB do tópico (v0.11): operador manda .txt/.md/.pdf/.docx
    # e o bot extrai texto e salva em user-data/topics/<slug>/knowledge/.
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    # Eventos administrativos de forum topics (v0.10): captura nome do
    # tópico pra popular topics.current_name — base do slug usado pela
    # knowledge base por tópico.
    app.add_handler(
        MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CREATED, on_forum_topic_created)
    )
    app.add_handler(
        MessageHandler(filters.StatusUpdate.FORUM_TOPIC_EDITED, on_forum_topic_edited)
    )
    # v0.12: detecção passiva via close/reopen (Telegram não emite "deleted").
    app.add_handler(
        MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CLOSED, on_forum_topic_closed)
    )
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.FORUM_TOPIC_REOPENED, on_forum_topic_reopened
        )
    )
    # Rede de segurança global: qualquer exceção não-tratada num handler avisa
    # o operador ("travei, reenvia") em vez de morrer calada. Ver on_error.
    app.add_error_handler(on_error)
    return app


def main() -> None:
    try:
        config = load_config()
    except ConfigError as exc:
        raise SystemExit(f"Configuração inválida: {exc}") from exc

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger.info(
        "kobe iniciando — usuários autorizados=%d home=%s",
        len(config.allowed_user_ids),
        config.kobe_home,
    )

    app = build_application(config)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
