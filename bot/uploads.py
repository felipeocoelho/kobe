"""Normalizer multimodal + registro central de uploads (Peça D da borda nova).

A borda antiga só aceitava `.txt/.md/.pdf/.docx`, forçava tudo pra `.md`,
jogava na `knowledge/` do tópico e ignorava imagem (foto sumia sem feedback).
Este módulo é a peça que conserta isso:

- **Aceita QUALQUER anexo** do operador. O Kobe é single-tenant / assistente
  pessoal — paridade com o Claude Desktop pro dono. Sem peneira de tipo. (A
  segurança de execução/credencial NÃO é afrouxada: nunca executamos conteúdo
  de anexo cegamente e não logamos segredo — isso é outro assunto.)
- **Salva o ORIGINAL** em `user-data/topics/<slug>/uploads/` (formato
  preservado), separado da KB curada em `knowledge/`.
- **Normaliza** pro turno: imagem → path injetado (o Claude Code lê imagem por
  path com a tool Read); documento texto-extraível → texto extraído entra no
  turno; qualquer outro tipo → path injetado, o agente decide.
- **Cataloga** toda ingestão num arquivo markdown ÚNICO e legível
  (`user-data/uploads-catalogo.md`) pro operador ver/gerenciar/apagar depois.

Atrás da flag `EDGE_UPLOADS_ENABLED` — ver bot/config.py e os handlers em
bot/telegram_handler.py.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from bot.topic_manager import topic_uploads_dir, unique_upload_path


logger = logging.getLogger("kobe.uploads")

# Fuso do operador — o catálogo é lido por humano, então os horários ancoram
# no Brasil (a VPS roda em UTC). Mesmo princípio de claude_runner.OPERATOR_TZ.
OPERATOR_TZ = ZoneInfo("America/Sao_Paulo")

# Catálogo central ÚNICO (agnóstico de tópico): um markdown legível que lista
# todos os uploads de todos os tópicos, pro operador gerenciar/apagar. Mora na
# raiz do user-data pra ser fácil de achar no Explorer.
CATALOG_RELATIVE = Path("user-data") / "uploads-catalogo.md"

_CATALOG_HEADER = (
    "# Catálogo de uploads\n"
    "\n"
    "> Todos os anexos que você me enviou, de todos os tópicos. O arquivo\n"
    "> ORIGINAL fica em `topics/<slug>/uploads/` (formato preservado); esta é\n"
    "> só uma lista legível pra você consultar e, quando quiser liberar espaço,\n"
    "> apagar os que não precisa mais. Apagar linhas daqui ou arquivos de lá é\n"
    "> seguro — é material de trabalho, não a base de conhecimento curada.\n"
    "\n"
    "| Quando | Tópico | Tipo | Arquivo | Tamanho | Caminho |\n"
    "|---|---|---|---|---|---|\n"
)

# Classificação de tipo (só pra frasear a injeção no turno de forma útil). Por
# extensão — barato e suficiente; o MIME do Telegram é acessório.
_IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    ".heic", ".heif", ".svg",
}
# Documentos dos quais extraímos texto pro turno (reusa a mesma extração da
# borda antiga). Outros tipos são aceitos igual — só não têm texto pra inline.
_TEXT_EXTRACTABLE_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}

KIND_IMAGE = "image"
KIND_DOCUMENT = "document"
KIND_OTHER = "other"


@dataclass(frozen=True)
class UploadDescriptor:
    """Resultado de normalizar um anexo. O handler injeta isto no turno."""

    kind: str  # KIND_IMAGE | KIND_DOCUMENT | KIND_OTHER
    filename: str
    path: Path  # caminho absoluto do original salvo
    rel_path: str  # caminho relativo ao kobe_home (pra exibir / catalogar)
    size: int
    extracted_text: Optional[str] = None  # só docs texto-extraíveis


def classify_kind(filename: str, mime: Optional[str] = None) -> str:
    """Classifica o anexo em image | document | other (por extensão/MIME)."""
    suffix = Path(filename).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return KIND_IMAGE
    if mime and mime.startswith("image/"):
        return KIND_IMAGE
    if suffix in _TEXT_EXTRACTABLE_SUFFIXES:
        return KIND_DOCUMENT
    return KIND_OTHER


def extract_text_from_bytes(suffix: str, raw: bytes) -> str:
    """Extrai texto plano de bytes de arquivo, conforme extensão.

    - `.txt`/`.md`: decode UTF-8 (errors='replace')
    - `.pdf`: pypdf concatena page.extract_text() de todas as páginas
    - `.docx`: python-docx concatena texto de parágrafos
    Outras extensões: raise ValueError — o caller pré-filtra o que chama aqui.

    (Movido da borda antiga `telegram_handler._extract_text` pra ser
    compartilhado sem import circular — o handler legado passa a importar daqui.)
    """
    suffix = suffix.lower()
    if suffix in {".txt", ".md"}:
        return raw.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — alguma página pode ter glyph quebrado
                logger.warning("pypdf: falha extraindo página, pulando", exc_info=True)
        return "\n\n".join(p.strip() for p in parts if p.strip())
    if suffix == ".docx":
        import docx

        doc = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs if p.text and p.text.strip())
    raise ValueError(f"extensão não suportada: {suffix}")


def _human_size(n: int) -> str:
    """Tamanho legível pro catálogo (KB/MB), pro operador estimar espaço."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def ingest_upload(
    kobe_home: Path,
    slug: str,
    filename: str,
    raw: bytes,
    *,
    mime: Optional[str] = None,
    extract_text: bool = True,
    now: Optional[datetime] = None,
) -> UploadDescriptor:
    """Salva o original em `uploads/`, extrai texto se aplicável, cataloga e
    devolve o descriptor pro handler injetar no turno.

    NÃO valida tipo (aceita tudo) nem tamanho — os guards de RECURSO (teto de
    download / de texto extraído) ficam no handler, que conhece o contexto do
    Telegram. Aqui é só normalização + persistência + catálogo.
    """
    filename = (filename or "anexo").strip() or "anexo"
    kind = classify_kind(filename, mime)

    target = unique_upload_path(kobe_home, slug, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    extracted: Optional[str] = None
    if extract_text and kind == KIND_DOCUMENT:
        try:
            extracted = extract_text_from_bytes(target.suffix, raw).strip() or None
        except Exception:  # noqa: BLE001 — PDF corrompido/DOCX inválido: segue com o path
            logger.warning(
                "uploads: falha extraindo texto de %r; injeto só o path", filename,
                exc_info=True,
            )
            extracted = None

    rel_path = str(target.relative_to(kobe_home))
    descriptor = UploadDescriptor(
        kind=kind,
        filename=target.name,
        path=target,
        rel_path=rel_path,
        size=len(raw),
        extracted_text=extracted,
    )

    try:
        _register_in_catalog(kobe_home, slug, descriptor, now=now)
    except OSError:  # noqa: BLE001 — catálogo é best-effort, nunca derruba o upload
        logger.warning("uploads: falha catalogando %r", filename, exc_info=True)

    return descriptor


def _register_in_catalog(
    kobe_home: Path,
    slug: str,
    descriptor: UploadDescriptor,
    *,
    now: Optional[datetime] = None,
) -> None:
    """Append de uma linha no catálogo markdown único (cria com header se novo)."""
    catalog = kobe_home / CATALOG_RELATIVE
    catalog.parent.mkdir(parents=True, exist_ok=True)
    if not catalog.exists():
        catalog.write_text(_CATALOG_HEADER, encoding="utf-8")

    stamp = (now or datetime.now(OPERATOR_TZ)).strftime("%Y-%m-%d %H:%M")
    kind_label = {
        KIND_IMAGE: "imagem",
        KIND_DOCUMENT: "documento",
        KIND_OTHER: "arquivo",
    }.get(descriptor.kind, "arquivo")
    row = (
        f"| {stamp} | {slug} | {kind_label} | `{descriptor.filename}` | "
        f"{_human_size(descriptor.size)} | `{descriptor.rel_path}` |\n"
    )
    with catalog.open("a", encoding="utf-8") as fh:
        fh.write(row)


def render_attachments_section(descriptors: list[UploadDescriptor]) -> Optional[str]:
    """Monta a seção `[Anexos deste turno]` pro prompt (None se lista vazia).

    Imagem → path + instrução de abrir com Read (multimodalidade nativa).
    Documento → path + texto extraído inline (correlacionado à instrução).
    Outro → só o path; o agente decide o que fazer.
    """
    if not descriptors:
        return None

    lines: list[str] = [
        "[Anexos deste turno — o operador enviou estes arquivos junto do pedido]",
    ]
    for d in descriptors:
        if d.kind == KIND_IMAGE:
            lines.append(
                f"- Imagem `{d.filename}` em `{d.path}` — use a tool Read nesse "
                f"caminho pra VER a imagem (você é multimodal)."
            )
        elif d.kind == KIND_DOCUMENT:
            if d.extracted_text:
                lines.append(
                    f"- Documento `{d.filename}` em `{d.path}` — texto extraído "
                    f"abaixo (o original está preservado nesse caminho):"
                )
                lines.append("")
                lines.append(d.extracted_text)
                lines.append("")
            else:
                lines.append(
                    f"- Documento `{d.filename}` em `{d.path}` — não consegui "
                    f"extrair texto; abra o arquivo por esse caminho se precisar."
                )
        else:
            lines.append(
                f"- Arquivo `{d.filename}` em `{d.path}` — abra por esse caminho "
                f"se o pedido exigir (tipo não texto-extraível)."
            )
    return "\n".join(lines)
