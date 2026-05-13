"""Conversão markdown → subset HTML aceito pelo Telegram.

Telegram aceita um subset HTML pequeno com `parse_mode="HTML"`:
  <b>, <strong>          → negrito
  <i>, <em>              → itálico
  <u>, <ins>             → sublinhado
  <s>, <strike>, <del>   → riscado
  <a href="...">         → link
  <code>                 → inline code
  <pre>                  → bloco de código
  <blockquote>           → citação

NÃO aceita: <p>, <h1-h6>, <ul>/<ol>/<li>, <br>, classes/IDs.
Newlines são respeitadas (texto plain com \\n quebra linha).

Esta função pega markdown padrão (estilo GitHub Flavored) — que é o
que o Claude tipicamente emite — e produz HTML válido pro Telegram.

Premissa de segurança: o texto vem do Claude (confiável-ish), mas
pode conter HTML acidental ou caracteres especiais. Escapamos `&`,
`<`, `>` antes de fazer qualquer substituição.
"""

from __future__ import annotations

import html
import re
from typing import Pattern


# ───────────────────────── padrões compilados ──────────────────────────

# Code block triple-backtick, com ou sem linguagem na primeira linha:
#   ```python
#   ...
#   ```
# Captura o conteúdo (group 1). Não-greedy.
_FENCED_CODE: Pattern[str] = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)

# Inline code: `texto` — não engole quebra de linha.
_INLINE_CODE: Pattern[str] = re.compile(r"`([^`\n]+)`")

# Links: [texto](url)
_LINK: Pattern[str] = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# Bold: **texto**. Aplica ANTES de italic pra `**` não ser confundido com `*`.
_BOLD: Pattern[str] = re.compile(r"\*\*([^*\n]+?)\*\*")
_BOLD_ALT: Pattern[str] = re.compile(r"__([^_\n]+?)__")

# Italic: *texto* ou _texto_. Lookarounds evitam pegar `**` (já tratado)
# e `_` no meio de identificadores (snake_case_etc).
_ITALIC_STAR: Pattern[str] = re.compile(
    r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)"
)
_ITALIC_UNDER: Pattern[str] = re.compile(
    r"(?<![_\w])_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)"
)

# Riscado: ~~texto~~
_STRIKE: Pattern[str] = re.compile(r"~~([^~\n]+?)~~")

# Headers: linhas começando com 1-6 `#`. Vira <b>...</b> porque o
# Telegram não tem heading nativo.
_HEADER: Pattern[str] = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


# ───────────────────────── conversão ──────────────────────────────────

def to_telegram_html(text: str) -> str:
    """Converte markdown padrão → HTML supported pelo Telegram.

    Texto sem markdown passa intacto (só com escape de `&<>`). Texto
    com markdown vira HTML formatado. Aplicar com `parse_mode="HTML"`
    no `send_message`/`reply_text`.

    Estratégia (ordem importa):
      1. Extrai code blocks/inline code pra placeholders (assim a
         pontuação markdown dentro deles não é interpretada).
      2. Escapa HTML restante.
      3. Aplica substituições markdown → HTML.
      4. Reinjeta os placeholders já com tags <pre>/<code> + escape
         do conteúdo.
    """
    if not text:
        return ""

    # Passo 1: extrai code blocks e inline code pra placeholders.
    placeholders: list[str] = []

    def _stash(html_replacement: str) -> str:
        idx = len(placeholders)
        placeholders.append(html_replacement)
        # Sentinel que não aparece em texto normal e sobrevive ao escape:
        return f"\x00CODE{idx}\x00"

    def _stash_fenced(match: re.Match[str]) -> str:
        content = match.group(1)
        # remove a última quebra de linha antes do ``` de fechamento
        if content.endswith("\n"):
            content = content[:-1]
        return _stash(f"<pre>{html.escape(content)}</pre>")

    def _stash_inline(match: re.Match[str]) -> str:
        return _stash(f"<code>{html.escape(match.group(1))}</code>")

    text = _FENCED_CODE.sub(_stash_fenced, text)
    text = _INLINE_CODE.sub(_stash_inline, text)

    # Passo 2: escapa o resto (HTML + sentinels não são afetados por
    # html.escape porque usam \x00, fora do range ASCII printable).
    text = html.escape(text)

    # Passo 3: substituições markdown.
    # Links: [texto](url) — escapamos a URL extra pra blindar atributos.
    text = _LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        text,
    )

    # Headers viram bold (Telegram não tem h1-h6).
    text = _HEADER.sub(lambda m: f"<b>{m.group(2)}</b>", text)

    # Bold antes de italic.
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _BOLD_ALT.sub(r"<b>\1</b>", text)

    # Italic.
    text = _ITALIC_STAR.sub(r"<i>\1</i>", text)
    text = _ITALIC_UNDER.sub(r"<i>\1</i>", text)

    # Strikethrough.
    text = _STRIKE.sub(r"<s>\1</s>", text)

    # Passo 4: reinjeta placeholders.
    for idx, replacement in enumerate(placeholders):
        text = text.replace(f"\x00CODE{idx}\x00", replacement)

    return text
