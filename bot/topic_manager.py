"""Ciclo de vida de topics, sessions e messages no Postgres.

Camada fina sobre o cliente: encontra-ou-cria o topic correspondente ao
`message_thread_id` do Telegram, garante uma session ativa, e grava
mensagens individuais.

A constante `GENERAL_THREAD_ID = 0` é a chave do "general" do supergrupo:
o Telegram não emite `message_thread_id` quando a mensagem cai no chat raiz,
mas a coluna `topics.telegram_thread_id` é UNIQUE, e PostgreSQL permite
múltiplos NULLs em UNIQUE — o que abriria duplicação. Como o Telegram nunca
usa thread_id=0 pra tópicos reais, usamos 0 como sentinela do general.
Isso permite um único caminho de upsert atômico via ON CONFLICT.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.db import KobeDB


logger = logging.getLogger("kobe.topics")

GENERAL_THREAD_ID: int = 0

# Limite de chars do contexto carregado em user-data/topics/<slug>/.
# Acima disso truncamos e avisamos o operador via Telegram pra ele
# reorganizar (mover algo pra saved_artifacts, dividir o KB, etc.).
TOPIC_CONTEXT_CHAR_LIMIT = 20_000

# SPR P1 #3: até este tamanho (chars) a pasta knowledge/ do tópico vai
# INLINE no prompt (comportamento histórico). Acima disso, injetamos só um
# ÍNDICE (caminhos + prévia) e o agente lê os arquivos sob demanda com Read
# — KB grande inflava o prompt a cada turno e atrasava o primeiro token.
# `prompt.md` (instruções do tópico) vai SEMPRE inline. Rollback: setar a
# env bem alta e reiniciar o bot.
TOPIC_KNOWLEDGE_INLINE_LIMIT = int(
    os.getenv("TOPIC_KNOWLEDGE_INLINE_LIMIT", "8000")
)

# Sufixo interno que `load_topic_context` adiciona quando a saída foi
# truncada. O caller no handler remove antes de injetar no prompt e
# usa pra disparar 1 aviso ao operador. Caracteres NUL não aparecem
# em conteúdo real de markdown, então não há colisão.
_TRUNCATED_MARKER = "\x00TRUNCATED\x00"


def _normalize_thread_id(thread_id: Optional[int]) -> int:
    return thread_id if thread_id is not None else GENERAL_THREAD_ID


def slugify(name: str) -> str:
    """Converte nome de tópico → slug compatível com filesystem.

    Minúsculo, sem acentos, qualquer run não-alfanumérica vira `-` único,
    sem `-` nas pontas. Casamento com o que `CLAUDE.md` promete pra
    `user-data/topics/<slug>/`.
    """
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def get_topic_slug(
    db: KobeDB, chat_id: int, thread_id: Optional[int]
) -> Optional[str]:
    """Slug do tópico (kebab-case de `topics.current_name`) ou None se
    ainda não está no banco / o nome não foi capturado.

    Sem thread_id real (None ou GENERAL_THREAD_ID=0), distingue:
    - `"private"` quando chat_id > 0 (DM 1-on-1 com o operador)
    - `"general"` quando chat_id < 0 (topic raiz do supergrupo)

    Forum topics retornam `slugify(current_name)`. Tópicos pré-v0.10 ficam
    com `current_name=NULL` até o operador renomear ou rodar UPDATE manual.
    """
    if thread_id is None or thread_id == GENERAL_THREAD_ID:
        return "private" if chat_id > 0 else "general"
    row = db.one(
        "SELECT current_name FROM topics "
        " WHERE telegram_chat_id = %s AND telegram_thread_id = %s"
        " LIMIT 1",
        (chat_id, thread_id),
    )
    if not row:
        return None
    raw_name = (row.get("current_name") or "").strip()
    if not raw_name:
        logger.info(
            "topic_context: tópico thread_id=%s sem current_name; "
            "renomeie no Telegram ou rode UPDATE topics SET current_name=... "
            "pra ativar o knowledge dele.",
            thread_id,
        )
        return None
    slug = slugify(raw_name)
    return slug or None


def set_topic_name(
    db: KobeDB,
    *,
    chat_id: int,
    thread_id: int,
    name: str,
) -> Optional[str]:
    """Persiste o nome do tópico em `topics.current_name` e arquiva o
    valor anterior em `topic_name_history` (auditoria).

    Chamado pelos handlers `forum_topic_created` / `forum_topic_edited`.
    Idempotente: se o nome já está atualizado, é no-op silencioso.

    Retorna o `current_name` **anterior** (ou `None` se era tópico novo
    ou já estava com esse nome). O caller usa isso pra detectar rename
    real e disparar `rename_topic_dir` no filesystem.
    """
    row = db.one(
        "SELECT id, current_name FROM topics "
        " WHERE telegram_chat_id = %s AND telegram_thread_id = %s"
        " LIMIT 1",
        (chat_id, thread_id),
    )
    if not row:
        # Tópico ainda não foi visto: cria a linha com o nome já preenchido.
        # `ensure_topic` faria upsert sem o nome — preferimos inserir aqui
        # com tudo de uma vez (evita uma rodada extra).
        # ON CONFLICT tem que nomear a UNIQUE que EXISTE: a composta
        # (telegram_chat_id, telegram_thread_id). A versao anterior deste
        # codigo apontava para `telegram_thread_id` sozinha — uma UNIQUE que
        # `infra/schema.sql` REMOVE explicitamente. Vide o CHANGELOG.
        db.execute(
            "INSERT INTO topics"
            " (telegram_chat_id, telegram_thread_id, current_name, last_activity_at)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (telegram_chat_id, telegram_thread_id) DO UPDATE"
            "    SET current_name     = EXCLUDED.current_name,"
            "        last_activity_at = EXCLUDED.last_activity_at",
            (chat_id, thread_id, name, _now_iso()),
        )
        return None

    previous = (row.get("current_name") or "") or None
    if previous == name:
        return None

    db.execute("UPDATE topics SET current_name = %s WHERE id = %s", (name, row["id"]))
    db.execute(
        "INSERT INTO topic_name_history (topic_id, name) VALUES (%s, %s)",
        (row["id"], name),
    )
    return previous


def set_topic_status(
    db: KobeDB, *, chat_id: int, thread_id: int, status: str
) -> Optional[str]:
    """Atualiza `topics.status` em resposta a forum_topic_closed/reopened.

    Aceita os valores válidos do CHECK do schema: 'active', 'archived',
    'deleted'. Retorna o `topics.id` modificado ou None se a linha não
    existir (evento sem topic prévio — improvável, mas defensivo).
    """
    rows = db.execute(
        "UPDATE topics SET status = %s"
        " WHERE telegram_chat_id = %s AND telegram_thread_id = %s"
        " RETURNING id",
        (status, chat_id, thread_id),
    )
    if not rows:
        return None
    return rows[0]["id"]


def rename_topic_dir(
    kobe_home: Path, old_slug: str, new_slug: str
) -> str:
    """Move `user-data/topics/<old_slug>/` → `<new_slug>/` quando o
    operador renomeia o tópico no Telegram. Retorna status:

    - `"renamed"` — pasta movida com sucesso
    - `"no_source"` — pasta antiga não existia (operador nunca criou KB)
    - `"same"` — slugs iguais (rename foi cosmético, ex: "Olimpo" → "OLIMPO")
    - `"conflict"` — destino já existe com conteúdo, abortado pra evitar perda
    - `"error"` — falha de IO (logada, caller decide o que fazer)
    """
    if old_slug == new_slug or not old_slug or not new_slug:
        return "same"
    base = kobe_home / "user-data" / "topics"
    src = base / old_slug
    dst = base / new_slug
    if not src.is_dir():
        return "no_source"
    if dst.exists():
        logger.warning(
            "rename_topic_dir: destino já existe (%s) — abortado, conteúdo de %s preservado",
            dst,
            src,
        )
        return "conflict"
    try:
        src.rename(dst)
    except OSError as exc:
        logger.exception("rename_topic_dir: falha movendo %s → %s: %s", src, dst, exc)
        return "error"
    logger.info("rename_topic_dir: %s → %s", src, dst)
    return "renamed"


def _first_line_preview(content: str, limit: int = 120) -> str:
    """Primeira linha não-vazia do conteúdo, capada — pra índice da KB."""
    for line in content.splitlines():
        line = line.lstrip("# ").strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return "(sem prévia)"


def load_topic_context(
    kobe_home: Path, slug: str, *, knowledge_inline_limit: Optional[int] = None
) -> Optional[str]:
    """Lê `user-data/topics/<slug>/prompt.md` + `knowledge/*` (ordem
    alfabética) e devolve string única pra injetar no prompt do Claude.

    `prompt.md` (instruções permanentes do tópico) vai sempre inline. A
    pasta `knowledge/` vai inline enquanto o total couber em
    `knowledge_inline_limit` (default `TOPIC_KNOWLEDGE_INLINE_LIMIT`);
    acima disso, injeta só um índice (caminho absoluto + prévia de cada
    arquivo) e instrui o agente a ler sob demanda com `Read` — evita
    inflar o prompt a cada turno (SPR P1 #3).

    Retorna `None` se o diretório do tópico não existir (caso normal —
    nem todo tópico tem KB). Trunca em `TOPIC_CONTEXT_CHAR_LIMIT` chars
    adicionando `_TRUNCATED_MARKER` ao final pra sinalizar ao caller que
    deve avisar o operador. Arquivos individuais ilegíveis são pulados
    com WARN — falha de um arquivo não derruba o tópico inteiro.
    """
    topic_dir = kobe_home / "user-data" / "topics" / slug
    if not topic_dir.is_dir():
        return None
    if knowledge_inline_limit is None:
        knowledge_inline_limit = TOPIC_KNOWLEDGE_INLINE_LIMIT

    chunks: list[str] = []

    prompt_md = topic_dir / "prompt.md"
    if prompt_md.is_file():
        try:
            content = prompt_md.read_text(encoding="utf-8").strip()
            if content:
                chunks.append(f"## {slug}/prompt.md\n\n{content}")
        except OSError as exc:
            logger.warning("topic_context: falhou lendo %s: %s", prompt_md, exc)

    knowledge_dir = topic_dir / "knowledge"
    kfiles: list[tuple[Path, str]] = []
    if knowledge_dir.is_dir():
        for f in sorted(knowledge_dir.iterdir()):
            if not f.is_file():
                continue
            try:
                content = f.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("topic_context: falhou lendo %s: %s", f, exc)
                continue
            if content:
                kfiles.append((f, content))

    total_knowledge = sum(len(c) for _, c in kfiles)
    if total_knowledge <= knowledge_inline_limit:
        for f, content in kfiles:
            chunks.append(f"## {slug}/knowledge/{f.name}\n\n{content}")
    elif kfiles:
        idx = [
            f"## Base de conhecimento de '{slug}' — extensa "
            f"({total_knowledge} chars), carregada sob demanda",
            "",
            "Os arquivos abaixo NÃO estão inline pra não inflar o prompt a "
            "cada turno. Use a ferramenta `Read` no caminho absoluto SÓ quando "
            "a mensagem do operador exigir aquele conteúdo:",
            "",
        ]
        for f, content in kfiles:
            idx.append(f"- `{f}` — {_first_line_preview(content)}")
        chunks.append("\n".join(idx))
        logger.info(
            "topic_context: '%s' knowledge=%d chars > limite %d — modo índice "
            "(%d arquivo(s) sob demanda)",
            slug, total_knowledge, knowledge_inline_limit, len(kfiles),
        )

    if not chunks:
        return None

    full = "\n\n---\n\n".join(chunks)
    if len(full) <= TOPIC_CONTEXT_CHAR_LIMIT:
        return full

    original_len = len(full)
    truncated = full[:TOPIC_CONTEXT_CHAR_LIMIT]
    cut = truncated.rfind("\n")
    if cut > 0:
        truncated = truncated[:cut]
    truncated += "\n\n[...truncado em TOPIC_CONTEXT_CHAR_LIMIT chars...]"
    logger.warning(
        "topic_context: tópico '%s' estourou limite (%d > %d chars) — truncado",
        slug,
        original_len,
        TOPIC_CONTEXT_CHAR_LIMIT,
    )
    return truncated + _TRUNCATED_MARKER


def list_unwelcomed_topics(db: KobeDB) -> list[dict]:
    """Tópicos que ainda não receberam a msg de boas-vindas da v0.11.

    Critério: `welcomed_at IS NULL` AND `telegram_chat_id IS NOT NULL`
    (sem chat_id não conseguimos enviar mensagem proativa). Retorna lista
    de `{topic_id, telegram_chat_id, telegram_thread_id, current_name}`
    pra o caller iterar no startup.

    NOTA: tópicos com `current_name=NULL` também são incluídos — General
    (thread_id=0) tem nome implícito mas current_name vazio, e queremos
    enviá-lo lá também. O caller decide o slug via `get_topic_slug`.
    """
    return db.query(
        "SELECT id, telegram_chat_id, telegram_thread_id, current_name"
        "  FROM topics"
        " WHERE welcomed_at IS NULL"
        "   AND telegram_chat_id IS NOT NULL"
        "   AND status = 'active'"
    )


def mark_welcomed(db: KobeDB, topic_id: str) -> None:
    """Marca o tópico como onboardado (msg de boas-vindas enviada).

    Idempotente — se já está marcado, é no-op silencioso (update de
    coluna pelo mesmo valor é OK no Postgres).
    """
    db.execute(
        "UPDATE topics SET welcomed_at = %s WHERE id = %s", (_now_iso(), topic_id)
    )


def topic_knowledge_dir(kobe_home: Path, slug: str) -> Path:
    """Pasta onde anexos do operador são salvos como KB do tópico."""
    return kobe_home / "user-data" / "topics" / slug / "knowledge"


def unique_knowledge_path(kobe_home: Path, slug: str, basename: str) -> Path:
    """Devolve um path único em `knowledge/` derivado de `basename`.

    Sanitiza separadores de caminho (Telegram aceita filenames com `/`),
    força extensão `.md` (todo upload vira markdown — extraímos texto e
    perdemos formatação original), e adiciona sufixo `-2`, `-3`… se já
    existir arquivo com o mesmo nome (não sobrescreve).
    """
    safe = basename.replace("/", "_").replace("\\", "_").strip() or "anexo"
    stem = Path(safe).stem or "anexo"
    target_dir = topic_knowledge_dir(kobe_home, slug)
    target = target_dir / f"{stem}.md"
    if not target.exists():
        return target
    i = 2
    while True:
        candidate = target_dir / f"{stem}-{i}.md"
        if not candidate.exists():
            return candidate
        i += 1


def topic_uploads_dir(kobe_home: Path, slug: str) -> Path:
    """Pasta onde anexos de turno do operador são salvos no formato ORIGINAL.

    Separada do `knowledge/` (KB curada, que entra em todo turno): uploads são
    material de trabalho efêmero, correlacionados à instrução do turno, e só
    viram KB permanente sob comando explícito ("guarda na base"). Parte da nova
    arquitetura de borda (Peça D) — ver bot/uploads.py.
    """
    return kobe_home / "user-data" / "topics" / slug / "uploads"


def unique_upload_path(kobe_home: Path, slug: str, basename: str) -> Path:
    """Path único em `uploads/` preservando a EXTENSÃO ORIGINAL do arquivo.

    Diferente de `unique_knowledge_path` (que força `.md` porque a KB é
    markdown), aqui o formato original é preservado — a imagem continua `.png`,
    o PDF continua `.pdf` — porque o agente lê o arquivo por path (imagem via a
    tool Read multimodal, documento via texto extraído). Sanitiza separadores de
    caminho (Telegram aceita filenames com `/`) e adiciona sufixo `-2/-3`… se já
    existir arquivo com o mesmo nome (não sobrescreve).
    """
    safe = basename.replace("/", "_").replace("\\", "_").strip() or "anexo"
    p = Path(safe)
    stem = p.stem or "anexo"
    suffix = p.suffix  # preserva a extensão original (ex.: .png, .pdf)
    target_dir = topic_uploads_dir(kobe_home, slug)
    target = target_dir / f"{stem}{suffix}"
    if not target.exists():
        return target
    i = 2
    while True:
        candidate = target_dir / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def consume_truncated_marker(context: Optional[str]) -> tuple[Optional[str], bool]:
    """Retorna `(contexto_limpo, foi_truncado)`. Tira o marcador interno
    antes de injetar no prompt.
    """
    if context is None:
        return None, False
    if context.endswith(_TRUNCATED_MARKER):
        return context[: -len(_TRUNCATED_MARKER)].rstrip(), True
    return context, False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_topic(
    db: KobeDB,
    thread_id: Optional[int],
    *,
    chat_id: int,
) -> str:
    """Get-or-create do topic. Retorna o `topics.id` (UUID em str).

    `chat_id` é obrigatório — a UNIQUE
    composta `(telegram_chat_id, telegram_thread_id)` permite topics
    diferentes com `thread_id=0` separados por chat: o chat privado do
    operador (`chat_id > 0`) e o "Geral" do supergrupo (`chat_id < 0`).

    Topics sem thread_id real recebem `current_name` automático no
    primeiro insert: "Private" pra DM, "General" pra Geral do supergrupo.
    Forum topics têm current_name preenchido depois por `set_topic_name`
    (handler de `forum_topic_created` / `forum_topic_edited`).
    """
    key = _normalize_thread_id(thread_id)

    row = db.one(
        "SELECT id, current_name FROM topics "
        " WHERE telegram_chat_id = %s AND telegram_thread_id = %s"
        " LIMIT 1",
        (chat_id, key),
    )
    if row:
        db.execute(
            "UPDATE topics SET last_activity_at = %s WHERE id = %s",
            (_now_iso(), row["id"]),
        )
        return row["id"]

    # Topic novo. `current_name` so e preenchido aqui para os sem thread real;
    # forum topic recebe o nome depois, por `set_topic_name`.
    nome = None
    if key == GENERAL_THREAD_ID:
        nome = "Private" if chat_id > 0 else "General"
    criado = db.execute(
        "INSERT INTO topics"
        " (telegram_thread_id, telegram_chat_id, last_activity_at, current_name)"
        " VALUES (%s, %s, %s, %s)"
        " RETURNING id",
        (key, chat_id, _now_iso(), nome),
    )
    if not criado:
        raise RuntimeError(
            f"insert de topic não retornou linha (chat_id={chat_id}, thread_id={key})"
        )
    return criado[0]["id"]


def ensure_active_session(db: KobeDB, topic_id: str) -> str:
    """Get-or-create da session ativa do topic. Retorna `sessions.id`.

    Nota: há uma janela de corrida teórica (duas mensagens chegando no
    primeiro instante de um topic novo, ambas vendo "sem session ativa" e
    ambas inserindo). O schema atual não tem unique parcial em
    (topic_id WHERE status='active'), então aceitamos o risco — em prática
    o handler é serial por mensagem e o caso só dispara no primeiro evento
    de um topic novíssimo.
    """
    row = db.one(
        "SELECT id FROM sessions"
        " WHERE topic_id = %s AND status = 'active'"
        " LIMIT 1",
        (topic_id,),
    )
    if row:
        return row["id"]

    criado = db.execute(
        "INSERT INTO sessions (topic_id, status) VALUES (%s, 'active') RETURNING id",
        (topic_id,),
    )
    if not criado:
        raise RuntimeError(f"insert de session não retornou linha (topic_id={topic_id})")
    return criado[0]["id"]


def get_recent_messages(
    db: KobeDB, session_id: str, limit: int = 20
) -> list[dict]:
    """Últimas N mensagens da session em ordem cronológica (mais antiga primeiro).

    Usado pra montar o histórico que vai no prompt do Claude. Buscamos em
    ordem decrescente pra pegar as mais recentes (caso a sessão seja longa)
    e revertemos pra apresentar como conversa natural.
    """
    rows = db.query(
        "SELECT role, content, created_at, audio_transcribed"
        "  FROM messages"
        " WHERE session_id = %s"
        " ORDER BY created_at DESC"
        " LIMIT %s",
        (session_id, limit),
    )
    return list(reversed(rows))


def get_messages_since(
    db: KobeDB, topic_id: str, since_iso: str, *, limit: int = 50
) -> list[dict]:
    """Mensagens do topic com `created_at > since_iso`, ordem cronológica.

    Janela de FRESCOR pra run de background reler o que entrou DEPOIS que ela
    foi despachada — follow-up do operador, cancelamento, correção (decisão
    Fase C, 2026-06-05). É um index seek por (topic_id, created_at): barato.
    Lê por topic (não por session) de propósito: a session pode ter
    rotacionado entre o despacho e o momento de agir.

    Retorna [] quando nada novo chegou — o caller (helper kobe-recall-since)
    traduz isso em "nenhuma mensagem nova" pro agente.
    """
    return db.query(
        "SELECT role, content, created_at, audio_transcribed"
        "  FROM messages"
        " WHERE topic_id = %s AND created_at > %s"
        " ORDER BY created_at ASC"
        " LIMIT %s",
        (topic_id, since_iso, limit),
    )


def archive_active_session(
    db: KobeDB,
    topic_id: str,
    *,
    summary: Optional[str] = None,
    status: str = "archived",
) -> Optional[str]:
    """Marca a session ativa do topic como `archived` (ou `compacted` na
    compactação automática da v0.12). Retorna o id da sessão modificada
    ou `None` se não havia sessão ativa (caso /nova num topic recém-criado).

    A próxima mensagem do topic dispara `ensure_active_session`, que cria
    uma sessão nova automaticamente — não criamos aqui pra não deixar
    sessão vazia no banco se o operador rodar /nova e mudar de ideia.

    `status` pode ser 'archived' (operador rodou /nova) ou 'compacted'
    (limiar de mensagens atingido e bot rotacionou pra preservar contexto).
    """
    if status not in ("archived", "compacted"):
        raise ValueError(f"status inválido: {status!r}")
    # `summary` so entra no SET quando foi passado: `None` explicito apagaria
    # um resumo ja gravado, e o contrato aqui e "nao mexe se nao veio".
    sets = ["status = %s", "ended_at = %s"]
    params: list = [status, _now_iso()]
    if summary is not None:
        sets.append("summary = %s")
        params.append(summary)
    params.append(topic_id)

    rows = db.execute(
        f"UPDATE sessions SET {', '.join(sets)}"
        " WHERE topic_id = %s AND status = 'active'"
        " RETURNING id",
        params,
    )
    if not rows:
        return None
    return rows[0]["id"]


def count_messages(db: KobeDB, session_id: str) -> int:
    return db.scalar(
        "SELECT count(*) FROM messages WHERE session_id = %s", (session_id,)
    ) or 0


def get_active_session(db: KobeDB, topic_id: str) -> Optional[dict]:
    """Retorna a session ativa do topic (ou None) — sem criar."""
    return db.one(
        "SELECT id, started_at FROM sessions"
        " WHERE topic_id = %s AND status = 'active'"
        " LIMIT 1",
        (topic_id,),
    )


def insert_message(
    db: KobeDB,
    *,
    session_id: str,
    topic_id: str,
    role: str,
    content: str,
    telegram_message_id: Optional[int] = None,
    audio_transcribed: bool = False,
) -> str:
    """Grava uma mensagem (user/assistant/system). Retorna `messages.id`."""
    criado = db.execute(
        "INSERT INTO messages"
        " (session_id, topic_id, telegram_message_id, role, content, audio_transcribed)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (session_id, topic_id, telegram_message_id, role, content, audio_transcribed),
    )
    if not criado:
        raise RuntimeError("insert de message não retornou linha")
    return criado[0]["id"]


def get_last_assistant_message_of_session(
    db: KobeDB, session_id: str
) -> Optional[str]:
    """Última mensagem com role='assistant' da session, ou None.

    Helper genérico de sessão. Nasceu para o detector de conversa (Chat
    Manager, aposentado em 2026-08-25) e ficou sem consumidor; não foi
    removido junto porque não é acoplado a conversation nenhuma — lê
    `messages` direto e serve qualquer caso de "o que o agente disse por
    último nesta sessão".
    """
    meta = get_last_assistant_message_meta_of_session(db, session_id)
    return meta["content"] if meta else None


def pop_awaiting_slash_response(
    db: KobeDB, session_id: str
) -> Optional[dict]:
    """Lê `sessions.awaiting_slash_response`, limpa, e devolve o conteúdo.

    Retorna `None` quando:
    - session sem o estado preenchido;
    - estado expirado (`asked_at + expires_in_seconds` no passado).

    Em qualquer caso (presente válido OU expirado), o campo é zerado
    no banco após a leitura — bypass é one-shot. Atomicidade aceitável
    sem transação: o handler do bot serializa msgs por topic via lock,
    então não há corrida com outra msg do mesmo operador.
    """
    row = db.one(
        "SELECT awaiting_slash_response FROM sessions WHERE id = %s LIMIT 1",
        (session_id,),
    )
    if not row:
        return None
    state = row.get("awaiting_slash_response")
    if state is None:
        return None
    # Limpa sempre — quem fez pergunta consome o estado nessa msg.
    db.execute(
        "UPDATE sessions SET awaiting_slash_response = NULL WHERE id = %s",
        (session_id,),
    )
    asked_at_raw = state.get("asked_at")
    ttl_seconds = state.get("expires_in_seconds", 600)
    if not asked_at_raw or not isinstance(ttl_seconds, (int, float)):
        return None
    try:
        asked_at = datetime.fromisoformat(asked_at_raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if asked_at.tzinfo is None:
        asked_at = asked_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - asked_at).total_seconds()
    if age < 0 or age > ttl_seconds:
        return None
    return state


def get_last_assistant_message_meta_of_session(
    db: KobeDB, session_id: str
) -> Optional[dict]:
    """Última msg `assistant` da session com `{content, created_at}`.

    Variante do helper acima, com o timestamp — serve pra avaliar janela
    de relevância (msg recente = mais provável de ser resposta à última
    pergunta do agente). Mesma nota de consumidor do helper acima.
    """
    row = db.one(
        "SELECT content, created_at FROM messages"
        " WHERE session_id = %s AND role = 'assistant'"
        " ORDER BY created_at DESC"
        " LIMIT 1",
        (session_id,),
    )
    if not row:
        return None
    return {
        "content": row.get("content"),
        "created_at": row.get("created_at"),
    }
