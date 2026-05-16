"""Descoberta e indexação de plugins do Kobe.

Plugins vivem em `$KOBE_HOME/plugins/{public,private}/<nome>/`. Cada um
é um repo Git separado (instalado via `infra/install-plugin.sh`) e
contém:

- `kobe-plugin.md` — manifest (frontmatter YAML + corpo opcional)
- `claude/agents/<nome>.md` — definição do subagente (opcional)
- `scripts/`, `bot/`, etc. — código do plugin (opcional, estrutura livre)

Este módulo:
1. Descobre todos os plugins instalados a cada chamada (sem cache —
   o overhead é desprezível e elimina a complicação de invalidar
   cache após `install-plugin.sh`).
2. Parseia o frontmatter YAML do manifest.
3. Constrói uma lista de subagentes a serem expostos ao Claude Code,
   simlinkando `KOBE_HOME/.claude/agents/<nome>.md` → o `.md` real
   dentro da pasta do plugin.

O `.claude/agents/` simlinkado dá ao agente principal acesso aos
subagentes via `Agent(subagent_type=<nome>, ...)` — mesmo mecanismo
do Claude Code padrão, só que populado dinamicamente.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


logger = logging.getLogger("kobe.plugins")

PLUGINS_DIRNAME = "plugins"
VISIBILITIES = ("public", "private")
MANIFEST_NAME = "kobe-plugin.md"


@dataclass
class Plugin:
    """Representação parseada de um plugin instalado."""

    name: str
    visibility: str  # "public" | "private"
    description: str
    path: Path  # raiz do plugin (onde está o kobe-plugin.md)
    version: Optional[str] = None
    triggers: list[str] = field(default_factory=list)
    agent_definition: Optional[Path] = None  # absoluto, se houver
    dependencies: dict = field(default_factory=dict)
    # Slash commands declarados pelo plugin, no formato:
    #   [{"name": "transcrever_txt", "description": "..."}]
    # Usado pelo bot pra registrar no menu do Telegram via set_my_commands.
    # Restrições do Telegram: name 1-32 chars [a-z0-9_], description ≤ 256.
    slash_commands: list[dict] = field(default_factory=list)


def discover_plugins(kobe_home: Path) -> list[Plugin]:
    """Escaneia plugins/{public,private}/* e devolve a lista parseada.

    Plugin sem manifest válido é logado e ignorado — não derruba a
    descoberta dos demais. Se a pasta `plugins/` não existir, retorna
    lista vazia (instalação sem plugins é estado normal).
    """
    plugins_root = kobe_home / PLUGINS_DIRNAME
    if not plugins_root.is_dir():
        return []

    found: list[Plugin] = []
    for visibility in VISIBILITIES:
        vdir = plugins_root / visibility
        if not vdir.is_dir():
            continue
        for plugin_dir in sorted(vdir.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("."):
                continue
            manifest = plugin_dir / MANIFEST_NAME
            if not manifest.is_file():
                logger.warning(
                    "plugin sem manifest: %s (esperado %s)", plugin_dir, MANIFEST_NAME
                )
                continue
            try:
                plugin = _parse_manifest(manifest, plugin_dir, visibility)
            except Exception:  # noqa: BLE001 — qualquer plugin quebrado é ignorável
                logger.exception("falha parseando manifest %s", manifest)
                continue
            found.append(plugin)
    return found


def _parse_manifest(manifest_path: Path, plugin_dir: Path, visibility: str) -> Plugin:
    """Lê frontmatter YAML do `kobe-plugin.md` e devolve um `Plugin`.

    O frontmatter é o bloco delimitado por `---` no topo do arquivo
    (convenção do Claude Code e de geradores estáticos). Tudo depois
    do segundo `---` é corpo legível por humanos — ignoramos aqui.
    """
    text = manifest_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError("manifest sem frontmatter YAML (deve começar com '---')")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("manifest com frontmatter incompleto (faltou o '---' de fechamento)")
    front = yaml.safe_load(parts[1]) or {}

    name = front.get("name")
    if not name:
        raise ValueError("manifest sem campo 'name'")
    declared_visibility = front.get("visibility") or visibility
    if declared_visibility != visibility:
        logger.warning(
            "plugin %s: visibility declarada (%s) ≠ pasta (%s); usando a da pasta",
            name,
            declared_visibility,
            visibility,
        )

    description = front.get("description") or ""

    agent_def_rel = front.get("agent_definition")
    agent_def_abs: Optional[Path] = None
    if agent_def_rel:
        candidate = (plugin_dir / agent_def_rel).resolve()
        if candidate.is_file():
            agent_def_abs = candidate
        else:
            logger.warning(
                "plugin %s: agent_definition %s não existe — ignorando",
                name,
                candidate,
            )

    triggers = front.get("triggers") or []
    if isinstance(triggers, str):
        triggers = [triggers]

    # slash_commands: lista de dicts com `name` (a-z0-9_, ≤32) e
    # `description` (≤256). Plugin pode omitir; validamos cada entrada
    # e dropamos silenciosamente as inválidas (com log).
    raw_cmds = front.get("slash_commands") or []
    if not isinstance(raw_cmds, list):
        logger.warning("plugin %s: slash_commands não é lista — ignorando", name)
        raw_cmds = []
    slash_commands: list[dict] = []
    for entry in raw_cmds:
        if not isinstance(entry, dict):
            logger.warning("plugin %s: slash_command malformado: %r", name, entry)
            continue
        cmd_name = (entry.get("name") or "").strip().lower()
        cmd_desc = (entry.get("description") or "").strip()
        if not cmd_name or not cmd_desc:
            logger.warning("plugin %s: slash_command faltando name/description: %r",
                           name, entry)
            continue
        # Telegram só aceita [a-z0-9_], 1-32 chars
        if not all(c.isalnum() or c == "_" for c in cmd_name) or not (1 <= len(cmd_name) <= 32):
            logger.warning(
                "plugin %s: slash_command name inválido pro Telegram: %r (a-z0-9_, ≤32)",
                name, cmd_name,
            )
            continue
        if len(cmd_desc) > 256:
            cmd_desc = cmd_desc[:253] + "…"
        slash_commands.append({"name": cmd_name, "description": cmd_desc})

    return Plugin(
        name=name,
        visibility=visibility,
        description=description,
        path=plugin_dir,
        version=front.get("version"),
        triggers=list(triggers),
        agent_definition=agent_def_abs,
        dependencies=front.get("dependencies") or {},
        slash_commands=slash_commands,
    )


def sync_agent_symlinks(kobe_home: Path, plugins: list[Plugin]) -> int:
    """Simlinka `claude/agents/<plugin>.md` de cada plugin pra `.claude/agents/`.

    Idempotente: remove symlinks órfãos (apontando pra plugin que não
    existe mais) e recria os atuais. Não toca em `.claude/agents/*.md`
    que não sejam symlinks (deixa intactos os subagentes do projeto).

    Retorna a contagem de symlinks criados/atualizados.
    """
    agents_dir = kobe_home / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    plugin_names = {p.name for p in plugins if p.agent_definition}

    # 1) Limpa symlinks órfãos que apontam pra plugins/ mas o plugin
    # foi removido (ou não declara mais agent_definition).
    for entry in agents_dir.iterdir():
        if not entry.is_symlink():
            continue
        target = os.readlink(entry)
        if PLUGINS_DIRNAME not in target:
            continue  # symlink de outra origem, não tocamos
        stem = entry.stem
        if stem not in plugin_names:
            logger.info("removendo symlink órfão: %s → %s", entry.name, target)
            entry.unlink()

    # 2) Cria/atualiza symlinks dos plugins atuais.
    created = 0
    for plugin in plugins:
        if not plugin.agent_definition:
            continue
        link = agents_dir / f"{plugin.name}.md"
        target = plugin.agent_definition
        if link.is_symlink() and Path(os.readlink(link)) == target:
            continue  # já correto
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(target)
        logger.info("symlink subagente: %s → %s", link.name, target)
        created += 1
    return created


def render_plugins_section(plugins: list[Plugin]) -> str:
    """Constrói a seção `[Plugins disponíveis]` pra injetar no prompt.

    Devolve string vazia se não houver plugins — o caller decide se
    inclui no prompt ou pula a seção.
    """
    if not plugins:
        return ""
    lines = ["[Plugins disponíveis]"]
    for p in plugins:
        triggers_hint = ""
        if p.triggers:
            triggers_hint = " — triggers: " + "; ".join(p.triggers)
        agent_hint = ""
        if p.agent_definition:
            agent_hint = f" (subagente: {p.name})"
        lines.append(
            f"- {p.name} [{p.visibility}]{agent_hint}: {p.description}{triggers_hint}"
        )
    return "\n".join(lines)
