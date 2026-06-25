"""Núcleo curado global — identidade + fatos duráveis auto-injetados.

Highlander Frente 1.2. Estilo Hermes: um núcleo PEQUENO e estável no topo do
prompt, com TETO fixo e esquecimento ativo. Duas fontes, em
`user-data/identity/`:

- `USER.md` — quem é o operador. Hoje **não** é injetado (depende da instrução
  "leia o USER.md" no CLAUDE.md); aqui passa a entrar por construção, todo turno.
- `MEMORY.md` — fatos duráveis do agente (preferências, decisões, pendências).
  Curado pelo próprio agente via edição de arquivo (add/replace/remove). É o
  núcleo que cresce — por isso o teto e o sinal de consolidação.

Por que núcleo pequeno e fixo: "% de quê?" → de um núcleo minúsculo, não de uma
janela grande. Consolidar é baratíssimo (Hermes). O contexto profundo continua
vindo da janela imediata (`working_set`) e, depois, do recall do Hindsight.

Read-only e tolerante a ausência: se os arquivos não existem (instalação nova),
devolve None — vira no-op, zero efeito. Atrás da flag `CURATED_CORE_ENABLED`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kobe.memory.curated_core")


# Teto do núcleo curado (chars). PEQUENO e fixo por design — é núcleo, não
# janela. USER.md tem prioridade (identidade sempre entra inteira); o que
# espreme é o MEMORY.md, que é o que cresce.
CURATED_CORE_CHAR_LIMIT = 6000
# Acima desta fração do teto, anexa um empurrão pro agente CONSOLIDAR o
# MEMORY.md (esquecimento ativo) — o gatilho é ~80%, como no Hermes.
CURATED_CORE_SOFT_RATIO = 0.8

_TRUNCATED_MARKER = "\n\n[… MEMORY.md truncado no teto do núcleo — consolide os fatos duráveis …]"


def _read(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("curated_core: falhou lendo %s: %s", path, exc)
        return None
    return content or None


def load_curated_core(
    kobe_home: Path, *, char_limit: int = CURATED_CORE_CHAR_LIMIT
) -> Optional[str]:
    """Monta o bloco `[Núcleo curado]` a partir de USER.md + MEMORY.md.

    USER.md entra inteiro (identidade); MEMORY.md entra até o orçamento que
    sobra, truncado com marcador se estourar. Acima de ~80% do teto, anexa o
    empurrão de consolidação. None se nada existe (no-op).
    """
    identity_dir = kobe_home / "user-data" / "identity"
    user_md = _read(identity_dir / "USER.md")
    memory_md = _read(identity_dir / "MEMORY.md")

    if not user_md and not memory_md:
        return None

    parts: list[str] = [
        "[Núcleo curado — identidade do operador + fatos duráveis do agente "
        "(auto-injetado, sempre confira contra a fonte se algo mudou)]"
    ]

    used = 0
    if user_md:
        # Identidade tem prioridade: entra inteira (se ela sozinha já estourar
        # o teto, é sinal de USER.md inflado — trunca com marcador e loga).
        if len(user_md) > char_limit:
            logger.warning(
                "curated_core: USER.md (%d chars) excede o teto %d — truncando",
                len(user_md), char_limit,
            )
            user_md = user_md[:char_limit] + _TRUNCATED_MARKER
        parts.append("")
        parts.append("## USER.md — quem é o operador")
        parts.append(user_md)
        used += len(user_md)

    if memory_md:
        remaining = char_limit - used
        if remaining <= 0:
            logger.warning("curated_core: sem orçamento pro MEMORY.md (USER.md encheu o teto)")
        else:
            if len(memory_md) > remaining:
                memory_md = memory_md[:remaining] + _TRUNCATED_MARKER
            parts.append("")
            parts.append("## MEMORY.md — fatos duráveis do agente")
            parts.append(memory_md)
            used += len(memory_md)

    # Esquecimento ativo (sinal): perto do teto, empurra a consolidação. O
    # CÓDIGO não apaga fato sozinho (anti-alucinação) — quem consolida é o
    # agente, editando o MEMORY.md. Aqui só sinalizamos o "está apertando".
    if used >= char_limit * CURATED_CORE_SOFT_RATIO:
        parts.append("")
        parts.append(
            f"[Núcleo em {used}/{char_limit} chars (~{used * 100 // char_limit}%). "
            "Se passar do teto, consolide o MEMORY.md: funda fatos parecidos, "
            "descarte o que envelheceu. Núcleo enxuto > núcleo inchado.]"
        )

    return "\n".join(parts)
