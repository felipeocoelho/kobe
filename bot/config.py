"""Carrega e valida variáveis de ambiente do Kobe."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


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
    )


def _parse_bool(raw: Optional[str]) -> bool:
    if not raw:
        return False
    return raw.strip().lower() in ("1", "true", "yes", "on", "enabled")
