"""O indexador de documentos — sempre em background, nunca no turno.

O QUE UM TICK FAZ, NESTA ORDEM
------------------------------
1. **Sincroniza** o disco com `doc_sources`: arquivo novo entra; arquivo cujo
   tamanho ou data mudou é relido, e só é tratado como mudado se o HASH mudou;
   arquivo que sumiu vira `ausente_do_disco` — a linha, o texto e os trechos FICAM
   (a citação continua viva). Nada é apagado.
2. **Quebra** em trechos o documento novo ou mudado (os trechos velhos dele são
   refeitos — é o único DELETE, e é de dado derivado, nunca do texto preservado).
3. **Atualiza** o catálogo das salas (`producoes.py`).
4. **Embedda** os trechos sem vetor, em lote.

A mesma regra da conversa: quebrar antes de embeddar deixa trabalho PENDENTE, nunca
PERDIDO, se o tick parar no meio. A fila é `embedding IS NULL`, no banco.

O ARQUIVO SÓ É LIDO
-------------------
`open(..., "rb")` e nada mais. O teste `test_docs_indexer` confere o hash de toda a
árvore antes e depois de uma carga.

FALHA NÃO VIRA SILÊNCIO
-----------------------
Embedding fora → `EmbeddingIndisponivel` sobe até o tick, que devolve o erro no
resultado e no log: o índice para de crescer, dito em voz alta. A busca por palavra
continua — ela não depende de vetor.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.docs import producoes, raizes
from bot.search import embedder
from bot.search.chunker import chunk

logger = logging.getLogger("kobe.docs.indexer")

LOTE_POR_TICK = 1000


@dataclass
class Resultado:
    vistos: int = 0
    novos: int = 0
    mudados: int = 0
    ausentes: int = 0
    trechos_criados: int = 0
    trechos_embeddados: int = 0
    producoes: int = 0
    erro: Optional[str] = None
    avisos: list[str] = field(default_factory=list)

    @property
    def fez_algo(self) -> bool:
        return bool(self.novos or self.mudados or self.ausentes
                    or self.trechos_embeddados or self.producoes)


def _env(nome: str) -> str:
    return (os.environ.get(nome) or "").strip()


def indexer_enabled() -> bool:
    """Ligada por padrão no `.env.example` (ajuste de 03/11: o que vai ligado na
    produção é requisito do Kobe). Ausente = desligada — é o rollback."""
    return _env("DOC_INDEX_ENABLED").lower() in ("1", "true", "on", "yes")


def _ler(p: Path) -> tuple[str, str]:
    """(sha256, texto). Só leitura. Texto inválido em UTF-8 é decodificado com
    substituição — documento com um byte torto continua achável."""
    with open(p, "rb") as f:
        dados = f.read()
    return hashlib.sha256(dados).hexdigest(), dados.decode("utf-8", errors="replace")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 16), b""):
            h.update(bloco)
    return h.hexdigest()


_RE_TITULO = re.compile(r"^\s*#\s+(.+?)\s*$", re.M)
_RE_CAB = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def titulo(texto: Optional[str], nome: str) -> str:
    if texto:
        m = _RE_TITULO.search(texto[:4000])
        if m:
            return m.group(1)[:200]
    return nome


def trechos_com_ancora(texto: str) -> list[tuple[str, str]]:
    """[(âncora, corpo)]. A âncora é o caminho de títulos acima do trecho
    (`Plano › 3. Mudanças`) — é o que a citação mostra para o operador achar o
    lugar no arquivo."""
    pedacos = chunk(texto)
    cabecalhos = [(m.start(), len(m.group(1)), m.group(2)) for m in _RE_CAB.finditer(texto)]
    saida, cursor = [], 0
    for corpo in pedacos:
        pos = texto.find(corpo[:80], cursor)
        if pos < 0:
            pos = cursor
        cursor = max(cursor, pos)
        pilha: list[tuple[int, str]] = []
        for ini, nivel, nome in cabecalhos:
            if ini > pos:
                break
            while pilha and pilha[-1][0] >= nivel:
                pilha.pop()
            pilha.append((nivel, nome.strip()))
        saida.append((" › ".join(n for _, n in pilha)[:300], corpo))
    return saida


def _mtime(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)


def _mesmo(guardado, mtime: datetime) -> bool:
    """O banco devolve datetime (conexão crua) ou ISO (a ponte do bot)."""
    if guardado is None:
        return False
    if isinstance(guardado, str):
        try:
            guardado = datetime.fromisoformat(guardado)
        except ValueError:
            return False
    return guardado == mtime


def _refazer_trechos(db, doc_id: int, texto: str) -> int:
    db.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
    n = 0
    for i, (ancora, corpo) in enumerate(trechos_com_ancora(texto)):
        db.execute(
            "INSERT INTO doc_chunks (doc_id, idx, anchor, body) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (doc_id, idx) DO NOTHING",
            (doc_id, i, ancora or None, corpo),
        )
        n += 1
    return n


def sincronizar(db, arquivos: list[raizes.Arquivo], *, raizes_varridas: set[str]) -> Resultado:
    """Disco → `doc_sources`. `raizes_varridas` diz de quais raízes a varredura
    foi COMPLETA: só nessas um arquivo que não apareceu vira ausente. Uma raiz
    não configurada nesta instalação não faz ninguém "sumir"."""
    r = Resultado()
    linhas = db.query(
        "SELECT id, root, rel_path, size_bytes, file_mtime, sha256, status FROM doc_sources"
    )
    existentes = {(l["root"], l["rel_path"]): l for l in linhas}
    vistos: set[tuple[str, str]] = set()
    for a in arquivos:
        chave = (a.root, a.rel_path)
        vistos.add(chave)
        r.vistos += 1
        try:
            st = a.abs_path.stat()
            mtime = _mtime(a.abs_path)
        except OSError:
            continue
        atual = existentes.get(chave)
        if atual and atual["size_bytes"] == st.st_size and _mesmo(atual["file_mtime"], mtime) \
                and atual["status"] == "presente":
            continue
        try:
            if a.indexable:
                sha, texto = _ler(a.abs_path)
            else:
                sha, texto = _sha(a.abs_path), None
        except OSError as exc:
            r.avisos.append(f"{a.root}:{a.rel_path}: {exc}")
            continue
        tit = titulo(texto, a.abs_path.name)
        if atual and atual["sha256"] == sha:
            db.execute(
                "UPDATE doc_sources SET size_bytes=%s, file_mtime=%s, status='presente',"
                " last_seen_at=NOW() WHERE id=%s",
                (st.st_size, mtime, atual["id"]),
            )
            continue
        if atual:
            db.execute(
                "UPDATE doc_sources SET nature=%s, topic_slug=%s, title=%s, ext=%s,"
                " size_bytes=%s, file_mtime=%s, sha256=%s, indexable=%s, content=%s,"
                " status='presente', last_seen_at=NOW(), updated_at=NOW() WHERE id=%s",
                (a.nature, a.topic_slug, tit, a.ext, st.st_size, mtime, sha,
                 a.indexable, texto, atual["id"]),
            )
            doc_id = atual["id"]
            r.mudados += 1
        else:
            linha = db.execute(
                "INSERT INTO doc_sources (root, rel_path, nature, topic_slug, title, ext,"
                " size_bytes, file_mtime, sha256, indexable, content)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (root, rel_path) DO UPDATE SET last_seen_at = NOW()"
                " RETURNING id",
                (a.root, a.rel_path, a.nature, a.topic_slug, tit, a.ext, st.st_size,
                 mtime, sha, a.indexable, texto),
            )
            doc_id = linha[0]["id"]
            r.novos += 1
        if a.indexable and texto:
            r.trechos_criados += _refazer_trechos(db, doc_id, texto)
        elif not a.indexable:
            db.execute("DELETE FROM doc_chunks WHERE doc_id = %s", (doc_id,))
    # Os que sumiram — só nas raízes varridas por inteiro.
    for chave, l in existentes.items():
        if chave in vistos or l["status"] != "presente" or chave[0] not in raizes_varridas:
            continue
        db.execute(
            "UPDATE doc_sources SET status='ausente_do_disco', updated_at=NOW() WHERE id=%s",
            (l["id"],),
        )
        r.ausentes += 1
    return r


def _entrada_do_vetor(l: dict) -> str:
    """O que vai para o embedding: o título e a âncora junto do corpo. Um trecho
    do meio de um plano não diz, sozinho, de que plano é — o título diz."""
    cab = l["title"] or ""
    if l.get("anchor"):
        cab += " › " + l["anchor"]
    return f"{cab}\n\n{l['body']}" if cab else l["body"]


def embeddar_pendentes(db, *, limite: int = LOTE_POR_TICK) -> int:
    pendentes = db.query(
        "SELECT c.id, c.body, c.anchor, s.title FROM doc_chunks c"
        " JOIN doc_sources s ON s.id = c.doc_id"
        " WHERE c.embedding IS NULL ORDER BY c.id LIMIT %s",
        (limite,),
    )
    if not pendentes:
        return 0
    vetores = embedder.embed([_entrada_do_vetor(p) for p in pendentes])
    modelo = embedder.modelo()
    for p, v in zip(pendentes, vetores):
        db.execute(
            "UPDATE doc_chunks SET embedding = %s, model = %s, embedded_at = NOW() WHERE id = %s",
            (embedder.para_sql(v), modelo, p["id"]),
        )
    return len(pendentes)


def tick(db, kobe_home: Path, *, lote: int = LOTE_POR_TICK, estado_coder: bool = True,
         extras: Optional[dict[str, Path]] = None) -> Resultado:
    """Um ciclo completo. Nunca levanta: o erro vai no resultado e no log."""
    r = Resultado()
    if not indexer_enabled():
        return r
    try:
        bs = raizes.bases(kobe_home, extras)
        varridas = {nome for nome, d in bs.items() if d.is_dir()}
        arquivos = list(raizes.descobrir(kobe_home, extras))
        r = sincronizar(db, arquivos, raizes_varridas=varridas)
        r.producoes = producoes.atualizar(db, arquivos, kobe_home=kobe_home,
                                          ler_estado_coder=estado_coder)
        r.trechos_embeddados = embeddar_pendentes(db, limite=lote)
    except embedder.EmbeddingIndisponivel as exc:
        r.erro = str(exc)
        logger.warning("docs: serviço de embedding fora — %s", exc)
    except Exception as exc:  # noqa: BLE001 — o daemon não pode cair por isto
        r.erro = f"{type(exc).__name__}: {exc}"
        logger.exception("docs: falha inesperada")
    return r


def pendencia(db) -> dict:
    linha = db.query(
        "SELECT COUNT(*) AS n, EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) AS idade"
        "  FROM doc_chunks WHERE embedding IS NULL"
    )
    l = linha[0] if linha else {}
    return {"trechos_sem_vetor": int(l.get("n") or 0),
            "idade_segundos": float(l.get("idade") or 0.0)}


def carga(db, kobe_home: Path, *, log=print, **kw) -> Resultado:
    """Roda o tick até a fila de vetores zerar. Mesmo trabalho do regime
    permanente, repetido — um caminho só para o mesmo bug morar."""
    total: Optional[Resultado] = None
    while True:
        t0 = time.time()
        r = tick(db, kobe_home, **kw)
        if total is None:
            total = r
        else:
            total.trechos_embeddados += r.trechos_embeddados
            total.novos += r.novos
            total.mudados += r.mudados
            total.trechos_criados += r.trechos_criados
        if r.erro:
            total.erro = r.erro
            return total
        log(f"  {r.vistos} vistos · +{r.novos} novos · {r.mudados} mudados · "
            f"{r.ausentes} ausentes · {r.trechos_criados} trechos · "
            f"{r.trechos_embeddados} embeddados · {time.time() - t0:.1f}s")
        if r.trechos_embeddados == 0:
            return total
