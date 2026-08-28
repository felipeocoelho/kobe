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
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("kobe.memory.curated_core")


# Tetos do núcleo curado (chars), um POR ARQUIVO — Highlander v3, F0.1 (E10).
#
# Antes era um teto só, de 6.000, dividido entre os dois: o USER.md entrava
# inteiro e o MEMORY.md ficava com a SOBRA. Com o USER.md real do operador
# (3.367 chars), a sobra era 2.633 — que não cabe anos de convivência, que é
# justamente o que o MEMORY.md existe pra acumular (frente (d) do briefing).
#
# Orçamento separado conserta a dependência errada: o tamanho da identidade
# não decide mais quanto de memória durável cabe. Cada um tem env própria, e
# baixar os dois de volta pra 4000/2000 devolve o comportamento antigo.
CURATED_CORE_USER_CHAR_LIMIT = int(os.getenv("CURATED_CORE_USER_LIMIT", "4000"))
CURATED_CORE_MEMORY_CHAR_LIMIT = int(os.getenv("CURATED_CORE_MEMORY_LIMIT", "6000"))
# Teto agregado — mantido porque é reexportado por `bot.memory` e serve de
# número único pra quem só quer saber "quanto o núcleo pode ocupar".
CURATED_CORE_CHAR_LIMIT = CURATED_CORE_USER_CHAR_LIMIT + CURATED_CORE_MEMORY_CHAR_LIMIT
# Acima desta fração do teto DO MEMORY.md, anexa um empurrão pro agente
# CONSOLIDAR (esquecimento ativo) — o gatilho é ~80%, como no Hermes. Medir
# contra o teto do MEMORY.md, e não contra o agregado, é o que faz o aviso
# falar da coisa que o agente pode consertar: o USER.md não é ele quem enxuga.
CURATED_CORE_SOFT_RATIO = 0.8

_TRUNCATED_MARKER = "\n\n[… MEMORY.md truncado no teto do núcleo — consolide os fatos duráveis …]"
_USER_TRUNCATED_MARKER = "\n\n[… USER.md truncado no teto próprio …]"


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
    kobe_home: Path,
    *,
    user_limit: Optional[int] = None,
    memory_limit: Optional[int] = None,
) -> Optional[str]:
    """Monta o bloco `[Núcleo curado]` a partir de USER.md + MEMORY.md.

    Cada arquivo tem **orçamento próprio** (F0.1): o que um usa não tira do
    outro, e cada um é truncado com marcador contra o próprio teto. Acima de
    ~80% do teto DO MEMORY.md, anexa o empurrão de consolidação. None se nada
    existe (no-op).
    """
    if user_limit is None:
        user_limit = CURATED_CORE_USER_CHAR_LIMIT
    if memory_limit is None:
        memory_limit = CURATED_CORE_MEMORY_CHAR_LIMIT

    identity_dir = kobe_home / "user-data" / "identity"
    user_md = _read(identity_dir / "USER.md")
    memory_md = _read(identity_dir / "MEMORY.md")

    if not user_md and not memory_md:
        return None

    parts: list[str] = [
        "[Núcleo curado — identidade do operador + fatos duráveis do agente "
        "(auto-injetado, sempre confira contra a fonte se algo mudou)]"
    ]

    if user_md:
        # Identidade contra o teto DELA — USER.md inflado agora é problema só
        # do USER.md; antes ele comia o espaço da memória durável em silêncio.
        if len(user_md) > user_limit:
            logger.warning(
                "curated_core: USER.md (%d chars) excede o teto próprio %d — truncando",
                len(user_md), user_limit,
            )
            user_md = user_md[:user_limit] + _USER_TRUNCATED_MARKER
        parts.append("")
        parts.append("## USER.md — quem é o operador")
        parts.append(user_md)

    memory_used = 0
    if memory_md:
        if len(memory_md) > memory_limit:
            logger.warning(
                "curated_core: MEMORY.md (%d chars) excede o teto próprio %d — truncando",
                len(memory_md), memory_limit,
            )
            memory_md = memory_md[:memory_limit] + _TRUNCATED_MARKER
        memory_used = len(memory_md)
        parts.append("")
        parts.append("## MEMORY.md — fatos duráveis do agente")
        parts.append(memory_md)

    # Esquecimento ativo (sinal): perto do teto, empurra a consolidação. O
    # CÓDIGO não apaga fato sozinho (anti-alucinação) — quem consolida é o
    # agente, editando o MEMORY.md. Aqui só sinalizamos o "está apertando".
    if memory_used and memory_used >= memory_limit * CURATED_CORE_SOFT_RATIO:
        parts.append("")
        parts.append(
            f"[MEMORY.md em {memory_used}/{memory_limit} chars "
            f"(~{memory_used * 100 // memory_limit}%). "
            "Se passar do teto, consolide: funda fatos parecidos, "
            "descarte o que envelheceu. Núcleo enxuto > núcleo inchado.]"
        )

    return "\n".join(parts)
