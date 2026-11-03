"""O que entra no índice de documentos — por REGRA GERAL, nunca por nome de pasta.

A LISTA DO QUE ENTRA (e não a do que sai)
-----------------------------------------
Medido no disco da produção em 02/11/2026: dentro de um tópico há um repositório
git, dois arquivos `.env` e um ambiente Python inteiro (`.local/`). Um indexador por
"lista do que sai" teria de conhecer cada armadilha de antemão; uma armadilha nova
iria para o índice — e o texto do índice vai para a API de embedding. Por isso:

    entra   = (raiz conhecida) × (regra da raiz) × (extensão de documento)
    nunca   = qualquer componente do caminho começando com ponto (`.git`, `.local`,
              `.env`, `.venv`…), link simbólico, extensão fora das listas.

A única exceção ao "ponto" é nomeada e estreita: o plano de uma sala do Coder mora
em `<sala>/.local/plano-*.md` (é ali que o rito manda escrever), e só esse padrão é
aceito dentro de `.local`.

AS REGRAS (casa = `$KOBE_HOME/user-data`)
-----------------------------------------
    casa:topics/<slug>/knowledge/**   base_topico        (tópico <slug>)
    casa:topics/<slug>/**             material_operador  (tópico <slug>)
    casa:knowledge/**                 anotacao_agente
    casa:missoes/<id>/**/*.md         artefato_sala      (missão <id>)
    casa:coder-worktrees/<id>/.local/plano-*.md    artefato_sala (sala do Coder)

E raízes da instalação, por `DOC_INDEX_SALA_ROOTS` (`tipo:caminho`, separadas por
vírgula), para quando as salas moram fora da casa — o caso de quem roda a produção
numa árvore e as salas na de desenvolvimento:

    worktrees:<dir>   <dir>/<id>/.local/plano-*.md   artefato_sala (sala do Coder)
    briefs:<dir>      <dir>/*.md                     anotacao_agente (brief que o
                                                     agente escreveu para abrir a sala)

O caminho concreto vem do `.env`; o código e o banco só conhecem o NOME da raiz.

TEXTO × SÓ LISTADO (decisão do operador, 02/11/2026)
----------------------------------------------------
`.md` e `.txt` são indexados. PDF, Word, planilha, imagem e HTML são só LISTADOS —
achados pelo nome, rotulados "conteúdo não lido". Extrair texto deles é fase própria.
Código, JSON de estado, logs e compactados não entram nem na lista.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

EXT_TEXTO = frozenset({".md", ".txt"})
EXT_LISTADO = frozenset({
    ".pdf", ".docx", ".doc", ".odt", ".rtf",
    ".xlsx", ".xls", ".ods", ".csv",
    ".pptx", ".ppt", ".odp",
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic",
    ".html", ".htm",
})

# Um `.txt` de vários MB é log ou dump, não documento — e vetorizá-lo custaria por
# nada. Acima do teto o arquivo é só listado.
TETO_TEXTO_BYTES = 2_000_000

_RE_PLANO = re.compile(r"^plano-.+\.md$")


@dataclass(frozen=True)
class Arquivo:
    root: str
    rel_path: str
    abs_path: Path
    nature: str
    topic_slug: Optional[str]
    ext: str
    indexable: bool
    sala_id: Optional[str] = None
    sala_kind: Optional[str] = None


def _env(nome: str) -> str:
    return (os.environ.get(nome) or "").strip()


def raizes_extras(valor: Optional[str] = None) -> dict[str, Path]:
    """`DOC_INDEX_SALA_ROOTS` → `{tipo: caminho}`. Tipo desconhecido é ignorado
    (e dito no log pelo chamador), nunca adivinhado."""
    bruto = _env("DOC_INDEX_SALA_ROOTS") if valor is None else valor
    saida: dict[str, Path] = {}
    for item in bruto.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        tipo, caminho = item.split(":", 1)
        tipo = tipo.strip()
        if tipo in ("worktrees", "briefs") and caminho.strip():
            saida[tipo] = Path(caminho.strip()).expanduser()
    return saida


def bases(kobe_home: Path, extras: Optional[dict[str, Path]] = None) -> dict[str, Path]:
    """Nome da raiz → diretório absoluto, NESTA instalação. É o que resolve o
    caminho guardado no banco em tempo de execução."""
    b = {"casa": Path(kobe_home) / "user-data"}
    b.update(raizes_extras() if extras is None else extras)
    return b


def _oculto(partes) -> bool:
    return any(p.startswith(".") for p in partes)


def _classificar(ext: str, tamanho: int) -> Optional[bool]:
    """True = indexável · False = só listado · None = fora."""
    if ext in EXT_TEXTO:
        return tamanho <= TETO_TEXTO_BYTES
    if ext in EXT_LISTADO:
        return False
    return None


def _andar(base: Path) -> Iterator[Path]:
    """Arquivos regulares sob `base`, sem entrar em pasta oculta nem seguir link."""
    if not base.is_dir():
        return
    for dp, dn, fn in os.walk(base, followlinks=False):
        dn[:] = sorted(d for d in dn if not d.startswith(".")
                       and not os.path.islink(os.path.join(dp, d)))
        for f in sorted(fn):
            if f.startswith("."):
                continue
            p = Path(dp) / f
            if p.is_symlink() or not p.is_file():
                continue
            yield p


def _novo(root, base, p, nature, slug, **extra) -> Optional[Arquivo]:
    ext = p.suffix.lower()
    try:
        tamanho = p.stat().st_size
    except OSError:
        return None
    ind = _classificar(ext, tamanho)
    if ind is None:
        return None
    return Arquivo(root=root, rel_path=p.relative_to(base).as_posix(), abs_path=p,
                   nature=nature, topic_slug=slug, ext=ext, indexable=ind, **extra)


def _planos_de_salas(root: str, base: Path, dir_salas: Path) -> Iterator[Arquivo]:
    """`<dir_salas>/<id>/.local/plano-*.md` — e só isso. Não anda pela worktree
    (que é uma cópia do repo inteiro): olha uma pasta, com um padrão."""
    if not dir_salas.is_dir():
        return
    for sala in sorted(dir_salas.iterdir()):
        if sala.name.startswith(".") or sala.is_symlink() or not sala.is_dir():
            continue
        local = sala / ".local"
        if not local.is_dir() or local.is_symlink():
            continue
        for p in sorted(local.iterdir()):
            if _RE_PLANO.match(p.name) and p.is_file() and not p.is_symlink():
                a = _novo(root, base, p, "artefato_sala", None,
                          sala_id=sala.name, sala_kind="coder")
                if a:
                    yield a


def descobrir(kobe_home: Path, extras: Optional[dict[str, Path]] = None) -> Iterator[Arquivo]:
    """Todos os documentos das raízes, pela regra geral. Só lê metadados (stat)."""
    raizes = bases(kobe_home, extras)
    casa = raizes["casa"]
    vistos: set[str] = set()

    def _uma_vez(a: Optional[Arquivo]) -> Optional[Arquivo]:
        if a is None:
            return None
        real = os.path.realpath(a.abs_path)
        if real in vistos:
            return None
        vistos.add(real)
        return a

    topicos = casa / "topics"
    if topicos.is_dir():
        for t in sorted(topicos.iterdir()):
            if t.name.startswith(".") or t.is_symlink() or not t.is_dir():
                continue
            for p in _andar(t):
                rel = p.relative_to(t).parts
                nature = "base_topico" if rel and rel[0] == "knowledge" else "material_operador"
                a = _uma_vez(_novo("casa", casa, p, nature, t.name))
                if a:
                    yield a

    for p in _andar(casa / "knowledge"):
        a = _uma_vez(_novo("casa", casa, p, "anotacao_agente", None))
        if a:
            yield a

    missoes = casa / "missoes"
    if missoes.is_dir():
        for m in sorted(missoes.iterdir()):
            if m.name.startswith(".") or m.is_symlink() or not m.is_dir():
                continue
            for p in _andar(m):
                if p.suffix.lower() != ".md":
                    continue
                a = _uma_vez(_novo("casa", casa, p, "artefato_sala", None,
                                   sala_id=m.name, sala_kind="missao"))
                if a:
                    yield a

    for a in _planos_de_salas("casa", casa, casa / "coder-worktrees"):
        if _uma_vez(a):
            yield a

    if "worktrees" in raizes:
        for a in _planos_de_salas("worktrees", raizes["worktrees"], raizes["worktrees"]):
            if _uma_vez(a):
                yield a

    if "briefs" in raizes and raizes["briefs"].is_dir():
        b = raizes["briefs"]
        for p in sorted(b.iterdir()):
            if (p.suffix.lower() == ".md" and not p.name.startswith(".")
                    and p.is_file() and not p.is_symlink()):
                a = _uma_vez(_novo("briefs", b, p, "anotacao_agente", None))
                if a:
                    yield a


def caminho_absoluto(root: str, rel_path: str, kobe_home: Path) -> Optional[Path]:
    """O caminho para `Read`, nesta instalação — ou None se a raiz não está
    configurada aqui (o documento continua citável pelo texto preservado)."""
    base = bases(kobe_home).get(root)
    return (base / rel_path) if base else None
