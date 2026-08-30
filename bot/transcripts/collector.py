"""O coletor incremental — copia por deslocamento de byte, sem nunca corromper.

A PREMISSA, MEDIDA E NÃO SUPOSTA
---------------------------------
O transcript do Claude Code é **append-only**. Verificado ao vivo em 29/08/2026,
no arquivo de uma sala que estava trabalhando naquele instante: em 15 segundos o
tamanho foi de 550.264 para 552.071 bytes, o **inode não mudou**, o SHA-256 dos
primeiros 64 KB ficou **idêntico**, e o arquivo terminava em `\\n`.

É isso que torna a cópia incremental segura: dá pra ler do último byte já copiado
até o fim, sem sincronizar com quem escreve, sem travar nada, e sem risco de
misturar conteúdo.

AS TRÊS GARANTIAS
-----------------
1. **Nunca corrompe.** A cópia para no **último fim-de-linha completo** do bloco
   lido. Uma linha em escrita — que o Claude Code ainda não terminou de gravar —
   fica pra próxima passada, inteira. Sem isso, uma passada no instante errado
   partiria um objeto JSON ao meio e o arquivo colhido deixaria de ser legível
   linha a linha, que é a única forma de lê-lo.

2. **Nunca duplica.** O deslocamento vem do estado, não de uma releitura do
   destino. Duas passadas seguidas sem atividade nova copiam **zero bytes**.

3. **Nunca sobrescreve.** A escrita é `'ab'`, sempre. Existe exatamente **um**
   caminho que substitui o destino — o da recópia integral, abaixo — e ele
   **renomeia o antigo antes**, preservando-o. Não há `unlink` neste arquivo.

QUANDO A ORIGEM DEIXA DE SER A MESMA
-------------------------------------
Se o começo do arquivo mudar, ou ele encolher, ou o inode trocar, então aquele já
não é o arquivo de onde viemos copiando — e anexar a partir do deslocamento
antigo produziria um Frankenstein: metade do arquivo velho, metade do novo,
emendados num ponto arbitrário. Nesse caso o coletor **preserva** o que tinha
(renomeando pra `.superseded-<carimbo>`) e recopia do byte 0.

Preservar em vez de apagar é o princípio da reversibilidade aplicado a dado: se a
detecção estiver errada, nada se perdeu; se estiver certa, as duas versões ficam
lá pra comparação. O custo é disco, que é o recurso abundante aqui (35 GB livres
contra ~1,1 GB/ano de coleta).

A CÓPIA É CRUA E ÍNTEGRA
-------------------------
Nenhum bloco é peneirado — nem `thinking`. Requisito do operador, decidido por
escrito em 27/08/2026 e reafirmado em 29/08: *"Sim, quero guardar o raciocínio
sim"*. Medido: `thinking` é **7,3%** do bruto, e **63%** do peso é
envelope/metadado — descartar raciocínio não moveria o ponteiro do disco, e
custaria justamente o que a F1 existe pra salvar (o *porquê* de cada decisão de
engenharia). Sem compressão, também por decisão do briefing (E8): comprimir
economizaria pouco e custaria a capacidade de abrir e greppar o arquivo direto.

Consequência boa e não acidental: como a cópia é byte a byte, o prefixo do
destino é **byte a byte idêntico** ao da origem — o que torna as três garantias
acima verificáveis com `sha256sum`, sem interpretar nada.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from bot.transcripts import state as st

# Quanto do começo do arquivo entra na impressão digital. 64 KB é folgado o
# bastante pra que dois transcripts distintos não colidam (o cabeçalho de sessão,
# o cwd, o primeiro pedido) e barato o bastante pra ler a cada passada.
HEAD_SAMPLE = 64 * 1024

# Teto de leitura por passada e por arquivo. Existe pra que uma primeira coleta
# de um acervo grande não carregue 90 MB na memória de uma vez; o que sobrar vem
# na passada seguinte, e o estado garante que ela continue de onde parou.
READ_CHUNK = 8 * 1024 * 1024

# `~/.claude/projects/<projeto>/<uuid>.jsonl`. O nome do arquivo é o
# `session_id` que o dispatch passou em `--session-id` — é o que amarra o
# transcript à linha do catálogo, sem tabela de-para.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def default_source_root() -> Path:
    from bot.work_catalog import _env

    raw = _env("TRANSCRIPT_SOURCE_ROOT")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "projects"


def default_dest_root() -> Path:
    from bot.work_catalog import _env

    raw = _env("TRANSCRIPT_DEST")
    if raw:
        return Path(raw).expanduser()
    kobe_home = _env("KOBE_HOME")
    base = Path(kobe_home).expanduser() if kobe_home else Path.cwd()
    return base / "user-data" / "transcripts"


def collector_enabled() -> bool:
    """Nasce desligado — é o rollback nomeado no briefing.

    Vale notar por que a chave importa mesmo sendo o coletor inofensivo: origem
    e destino são do HOST, e `~/.claude/projects` é **um só**. Se o coletor de
    dev e o de produção rodassem juntos, os dois colheriam os mesmos arquivos
    pra destinos diferentes — duas verdades e o dobro do disco. Quem colhe de
    verdade é a produção.
    """
    from bot.work_catalog import _env

    return _env("TRANSCRIPT_COLLECTOR_ENABLED").lower() in ("1", "true", "on", "yes")


def stale_hours() -> float:
    from bot.work_catalog import _env

    raw = _env("TRANSCRIPT_STALE_HOURS")
    try:
        return float(raw) if raw else 48.0
    except ValueError:
        return 48.0


# --- descoberta ---------------------------------------------------------

@dataclass(frozen=True)
class Transcript:
    """Um transcript na origem. `key` é `<projeto>/<session_id>`."""

    project: str
    session_id: str
    src: Path

    @property
    def key(self) -> str:
        return f"{self.project}/{self.session_id}"


def discover(source_root: Path) -> list[Transcript]:
    """Todos os transcripts da origem — **de toda sala, viva ou morta**.

    Deliberadamente NÃO consulta o catálogo pra decidir o que colher. As ~24
    salas que já existiam antes da F1 não têm linha nenhuma, e são justamente as
    que estão prestes a expirar. O catálogo é enriquecimento; o que é perecível
    se salva primeiro, e a linha pode vir depois.
    """
    if not source_root.is_dir():
        return []
    achados: list[Transcript] = []
    for projeto in sorted(source_root.iterdir()):
        if not projeto.is_dir():
            continue
        for arquivo in sorted(projeto.glob("*.jsonl")):
            nome = arquivo.stem
            if not _UUID_RE.match(nome):
                # Só arquivos nomeados por session_id. Um `.jsonl` avulso na
                # pasta não é transcript de sessão e não tem chave no catálogo.
                continue
            achados.append(Transcript(projeto.name, nome, arquivo))
    return achados


# --- a cópia ------------------------------------------------------------

def head_digest(path: Path, size: int) -> tuple[str, int]:
    """SHA-256 dos primeiros `HEAD_SAMPLE` bytes (ou do arquivo, se menor)."""
    n = min(HEAD_SAMPLE, max(0, size))
    if n == 0:
        return "", 0
    with path.open("rb") as fh:
        dados = fh.read(n)
    return hashlib.sha256(dados).hexdigest(), len(dados)


@dataclass
class SessionResult:
    key: str
    session_id: str
    project: str
    dest: Optional[Path] = None
    copied: int = 0
    action: str = "unchanged"   # unchanged | appended | recopied | first | error
    superseded_to: Optional[str] = None
    dossier: Optional[Path] = None
    error: Optional[str] = None


@dataclass
class RunResult:
    ok: bool = True
    sessions: list[SessionResult] = field(default_factory=list)
    dest_root: Optional[Path] = None
    skipped_reason: Optional[str] = None
    # Por que o enriquecimento (catálogo/dossiê) não aconteceu, quando não
    # aconteceu. NUNCA fica em silêncio: um `except` mudo aqui já escondeu, por
    # três execuções, um dossiê saindo com `sistema: (não catalogada)` enquanto
    # a linha existia no banco. Degradar é aceitável; degradar calado não é.
    catalog_note: Optional[str] = None

    @property
    def total_copied(self) -> int:
        return sum(s.copied for s in self.sessions)

    @property
    def touched(self) -> list[SessionResult]:
        return [s for s in self.sessions if s.copied > 0 or s.action == "recopied"]

    @property
    def errors(self) -> list[SessionResult]:
        return [s for s in self.sessions if s.action == "error"]


def _dest_e_prefixo_da_origem(src: Path, dest: Path, n: int) -> bool:
    """O destino é, byte a byte, os primeiros `n` bytes da origem?

    Só é chamado quando o estado se perdeu — caro (lê os dois arquivos), raro
    (não acontece em operação normal) e conclusivo. Compara em blocos pra que um
    transcript de dezenas de MB não vá inteiro pra memória, e desiste no
    primeiro byte diferente.
    """
    if n <= 0:
        return True
    try:
        if src.stat().st_size < n:
            return False
        with src.open("rb") as a, dest.open("rb") as b:
            restante = n
            while restante > 0:
                pedaco = min(1024 * 1024, restante)
                if a.read(pedaco) != b.read(pedaco):
                    return False
                restante -= pedaco
    except OSError:
        return False
    return True


def _copy_incremental(
    tr: Transcript, dest: Path, entry: dict[str, Any], *, dry_run: bool
) -> SessionResult:
    """Uma sessão. Toda a lógica das três garantias mora aqui."""
    res = SessionResult(key=tr.key, session_id=tr.session_id,
                        project=tr.project, dest=dest)
    try:
        stat = tr.src.stat()
    except OSError as exc:
        res.action, res.error = "error", f"origem ilegível: {exc}"
        return res

    src_size = stat.st_size
    sha, _ = head_digest(tr.src, src_size)

    ja_copiado = int(entry.get("bytes_copied") or 0)
    sha_antigo = entry.get("head_sha256") or ""
    inode_antigo = entry.get("src_inode")

    # O destino é a verdade sobre quanto já foi escrito. Se ele sumiu (apagado à
    # mão, disco novo) ou está menor que o estado diz, o estado está mentindo —
    # e continuar dali deixaria um buraco no meio do arquivo colhido.
    dest_size = dest.stat().st_size if dest.is_file() else 0
    if dest_size < ja_copiado:
        ja_copiado = dest_size

    # ── ESTADO PERDIDO, DESTINO EXISTENTE ──────────────────────────────────
    # O estado diz "nunca copiei nada" e no entanto há um destino com conteúdo.
    # Acontece quando o arquivo de estado se corrompe, é apagado, ou o coletor
    # muda de destino e volta.
    #
    # A tentação é ignorar e seguir: `ja_copiado = 0` e append. **Isso duplica o
    # arquivo inteiro** — porque a escrita é append, "recopiar" aqui não
    # substitui, ANEXA. A garantia nº 2 iria embora exatamente no cenário em que
    # ninguém está olhando. (Pego pelo teste `test_estado_corrompido_...`, que
    # falhou com o destino do dobro do tamanho.)
    #
    # A saída certa é reconstruir o deslocamento a partir do próprio destino: se
    # o que já foi colhido é um **prefixo byte a byte** da origem, então o
    # destino sabe onde paramos, e continuamos dali. Se não for, aquele destino
    # veio de outro arquivo, e cai no caminho de preservar-e-recopiar abaixo.
    prefixo_confere = True
    if ja_copiado == 0 and dest_size > 0:
        prefixo_confere = _dest_e_prefixo_da_origem(tr.src, dest, dest_size)
        if prefixo_confere:
            ja_copiado = dest_size

    # A origem ainda é a mesma? Quatro sinais, e basta um discordar.
    trocou = bool(
        (sha_antigo and sha and sha != sha_antigo)
        or (src_size < ja_copiado)
        or (inode_antigo is not None and inode_antigo != stat.st_ino)
        or (not prefixo_confere)
    )

    if trocou and dest.is_file() and dest_size > 0:
        if dry_run:
            res.action = "recopied"
            return res
        carimbo = st.now_iso().replace(":", "").replace("-", "")
        preservado = dest.with_name(f"{dest.name}.superseded-{carimbo}")
        # `move` e não `copy`: o que se quer é que o antigo saia do caminho
        # INTEIRO e de uma vez. E nunca `unlink` — o princípio é preservar.
        shutil.move(str(dest), str(preservado))
        res.superseded_to = preservado.name
        ja_copiado = 0
        res.action = "recopied"
    elif ja_copiado == 0:
        res.action = "first"

    if src_size <= ja_copiado:
        if res.action in ("unchanged", "first"):
            res.action = "unchanged" if dest.is_file() else res.action
        # Nada novo — mas o estado é atualizado abaixo, pra que a impressão
        # digital acompanhe um arquivo que cresceu e voltou a estabilizar.
        _grava_entry(entry, tr, stat, sha, ja_copiado)
        return res

    try:
        with tr.src.open("rb") as fh:
            fh.seek(ja_copiado)
            bloco = fh.read(READ_CHUNK)
    except OSError as exc:
        res.action, res.error = "error", f"leitura falhou: {exc}"
        return res

    # ── A garantia nº 1, em uma linha ──────────────────────────────────────
    # Só até o último `\n`. O resto é uma linha que ainda está sendo escrita.
    corte = bloco.rfind(b"\n")
    if corte == -1:
        # Nem uma linha completa no bloco. Não copia nada — e isso é o certo:
        # meia linha não é dado, é lixo que quebraria a leitura do arquivo.
        _grava_entry(entry, tr, stat, sha, ja_copiado)
        if res.action == "first":
            res.action = "unchanged"
        return res
    util = bloco[: corte + 1]

    if dry_run:
        res.copied = len(util)
        if res.action == "unchanged":
            res.action = "appended"
        return res

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 'ab' — a garantia nº 3. Nunca 'wb' sobre destino existente.
        with dest.open("ab") as out:
            out.write(util)
            out.flush()
            os.fsync(out.fileno())
    except OSError as exc:
        res.action, res.error = "error", f"escrita falhou: {exc}"
        return res

    res.copied = len(util)
    if res.action == "unchanged":
        res.action = "appended"
    _grava_entry(entry, tr, stat, sha, ja_copiado + len(util))
    return res


def _grava_entry(entry, tr: Transcript, stat, sha: str, bytes_copied: int) -> None:
    entry.update({
        "src": str(tr.src),
        "project": tr.project,
        "session_id": tr.session_id,
        "bytes_copied": bytes_copied,
        "head_sha256": sha,
        "src_size": stat.st_size,
        "src_inode": stat.st_ino,
        "src_mtime": stat.st_mtime,
        "last_copied_at": st.now_iso(),
    })


def collect_once(
    *,
    source_root: Optional[Path] = None,
    dest_root: Optional[Path] = None,
    only: Optional[Iterable[str]] = None,
    dry_run: bool = False,
    update_catalog: bool = True,
    write_dossier: bool = True,
) -> RunResult:
    """Uma passada. Idempotente: sem atividade nova, copia zero bytes.

    `only` filtra por `session_id` (ou pelos 8 primeiros caracteres, que é como
    as salas são chamadas no dia a dia).
    """
    source_root = source_root or default_source_root()
    dest_root = dest_root or default_dest_root()
    resultado = RunResult(dest_root=dest_root)

    filtro = {f.lower() for f in only} if only else None

    with st.exclusive_lock(dest_root):
        estado = st.load(dest_root)
        sessoes = estado.setdefault("sessions", {})

        for tr in discover(source_root):
            if filtro and not any(
                tr.session_id.lower() == f or tr.session_id.lower().startswith(f)
                for f in filtro
            ):
                continue
            dest = dest_root / tr.project / f"{tr.session_id}.jsonl"
            entry = sessoes.setdefault(tr.key, {})
            res = _copy_incremental(tr, dest, entry, dry_run=dry_run)
            entry["dest"] = str(dest)
            if res.superseded_to:
                entry.setdefault("superseded", []).append(res.superseded_to)
            resultado.sessions.append(res)

        if not dry_run:
            st.mark_run(estado, success=not resultado.errors)
            st.save(dest_root, estado)

    resultado.ok = not resultado.errors

    # Os dois passos abaixo rodam DEPOIS da trava ser solta e DEPOIS de a cópia
    # já estar em disco. A ordem é deliberada: a coleta é a parte que não pode
    # falhar (é ela que salva dado perecível), e nem o catálogo nem o dossiê
    # podem tomá-la de refém.
    if not dry_run:
        if write_dossier:
            _regenera_dossies(resultado)
        if update_catalog:
            _atualiza_catalogo(resultado)

    return resultado


def _regenera_dossies(resultado: RunResult) -> None:
    """Regenera o dossiê de cada sala que teve novidade — o "por acúmulo".

    Best-effort e por sala: um transcript que quebre a geração não pode impedir
    o dossiê dos outros. O dossiê é derivado; a fonte de verdade é o `.jsonl` ao
    lado, e ele não é tocado aqui.
    """
    from bot.transcripts import dossier

    catalogo_por_sessao: dict[str, Any] = {}
    artefatos_por_sessao: dict[str, Any] = {}
    db = None
    try:
        from bot import work_catalog as wc

        if not wc.catalog_enabled():
            resultado.catalog_note = "catálogo desligado — dossiê sem sistema/subsistema"
        else:
            db = wc.connect()
            for s in resultado.touched:
                try:
                    linha = wc.get_session(db, s.session_id)
                except Exception as exc:  # noqa: BLE001
                    resultado.catalog_note = f"leitura do catálogo falhou: {exc}"
                    continue
                if linha:
                    catalogo_por_sessao[s.session_id] = linha
                    artefatos_por_sessao[s.session_id] = \
                        wc.list_artifacts(db, s.session_id)
    except Exception as exc:  # noqa: BLE001 — sem catálogo o dossiê sai magro, e sai
        resultado.catalog_note = (
            f"catálogo indisponível ({type(exc).__name__}: {exc}) — "
            f"o dossiê saiu sem sistema/subsistema"
        )
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass

    for s in resultado.touched:
        if s.dest is None or not s.dest.is_file():
            continue
        try:
            caminho = dossier.gerar(
                session_id=s.session_id,
                transcript=s.dest,
                catalogo=catalogo_por_sessao.get(s.session_id),
                artefatos=artefatos_por_sessao.get(s.session_id),
            )
            s.dossier = caminho
        except Exception:  # noqa: BLE001 — um dossiê ruim não para os outros
            continue


def _atualiza_catalogo(resultado: RunResult) -> None:
    """Espelha `transcript_path` e `transcript_bytes_copied` no catálogo.

    **Best-effort, e fora da trava, de propósito.** A coleta é a parte que não
    pode falhar — ela é o que salva dado perecível. O catálogo é enriquecimento:
    um Postgres fora do ar não pode ser motivo pra o transcript de uma sala
    deixar de ser copiado. Por isso isto roda depois da cópia já estar em disco,
    e engole a própria falha.
    """
    from bot import work_catalog as wc

    if not wc.catalog_enabled():
        return
    tocadas = resultado.touched
    if not tocadas:
        return
    db = None
    try:
        db = wc.connect()
        for s in tocadas:
            dest = s.dest
            if dest is None or not dest.is_file():
                continue
            try:
                wc.touch_session(
                    db, session_id=s.session_id,
                    transcript_path=str(dest),
                    transcript_bytes_copied=dest.stat().st_size,
                    dossier_path=str(s.dossier) if s.dossier else None,
                )
            except Exception:  # noqa: BLE001 — uma linha ruim não para as outras
                continue
    except Exception as exc:  # noqa: BLE001 — indisponível não invalida a coleta
        resultado.catalog_note = (
            f"ponteiros não espelhados no catálogo ({type(exc).__name__}: {exc})"
        )
        return
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass


# --- a marca do relógio (mitigação da lacuna L4) ------------------------

def staleness(dest_root: Optional[Path] = None) -> dict[str, Any]:
    """A idade da última coleta bem-sucedida, e se ela passou do limite.

    É o que transforma "o coletor parou de rodar" — que não produz erro nenhum,
    só silêncio — em algo que se pode ver e sobre o que se pode avisar.
    """
    dest_root = dest_root or default_dest_root()
    estado = st.load(dest_root)
    limite = stale_hours()
    idade = st.age_hours(estado.get("last_success_at"))
    return {
        "dest_root": str(dest_root),
        "last_run_at": estado.get("last_run_at"),
        "last_success_at": estado.get("last_success_at"),
        "age_hours": idade,
        "stale_hours": limite,
        # Nunca ter rodado conta como envelhecido: um coletor que foi ligado e
        # nunca coletou é exatamente o caso que esta marca existe pra pegar.
        "stale": (idade is None) or (idade > limite),
        "sessions_tracked": len(estado.get("sessions") or {}),
    }


def staleness_warning(dest_root: Optional[Path] = None) -> Optional[str]:
    """A frase pronta, ou `None` se está tudo em dia."""
    info = staleness(dest_root)
    if not info["stale"]:
        return None
    if info["last_success_at"] is None:
        return (
            "o coletor de transcripts NUNCA registrou uma coleta bem-sucedida — "
            "transcripts expiram em 30 dias."
        )
    return (
        f"o coletor de transcripts não conclui uma coleta há "
        f"{info['age_hours']:.0f}h (limite: {info['stale_hours']:.0f}h). "
        f"Transcripts expiram em 30 dias — verifique o agendamento."
    )
