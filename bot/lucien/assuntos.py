"""O assunto como unidade (Highlander v3, F7.1) — a lista e a trava T11.

O DEFEITO, MEDIDO
-----------------
Em 13/10/2026 a tabela de fatos tinha 1.166 afirmações e **1.163 assuntos
distintos**. O curador criava um assunto por linha porque ninguém lhe mostrava
os que já existiam: cada rodada via a conversa e o estado vigente, nunca a lista
de assuntos. Este módulo é essa lista — e a trava que impede o curador de
ignorá-la.

O QUE É UM ASSUNTO — A RÉGUA É DO OPERADOR
------------------------------------------
07/10/2026 (`#4029`): assunto é o que, num aplicativo de chat, seria uma
**thread**. *"desenvolvimento da V3 do Highlander"* é bom; *"Manutenção do
Kobe"* é lixo; a referência de tamanho são os arquivos `.md` com data da base de
conhecimento — **tamanho de documento, não tamanho de tópico**. E: *"não é tudo
que vai precisar ter um assunto"* — afirmação pontual fica avulsa.

A lista é **única e plana** (decisão dele, 13/10/2026): vale para todos os
tópicos, sem hierarquia. A parede entre tópicos continua na coluna `topic_id` de
cada afirmação.

A T11, E POR QUE ELA É CÓDIGO E NÃO SÓ PROMPT
---------------------------------------------
O prompt pede ao modelo que REUSE (`"assunto": "A3"`) e só crie quando nada
serve. Pedir não basta — foi pedindo ("seja consistente") que se chegou a 1.163
assuntos. Então, quando o modelo propõe um assunto novo, o código confere antes
de criar, nesta ordem:

    1. o mesmo slug já existe          → reusa (seguindo a fusão, se houver)
    2. um título ativo parecido        → reusa  (trigrama ≥ TRGM_REUSO)
    3. um título ativo de mesmo sentido → reusa  (cosseno ≥ COS_REUSO)

Cada desvio é contado (`subjects_avoided` em `lucien_runs`). Mesmo princípio das
T1–T10: o prompt faz o modelo acertar; a trava garante quando ele não acerta; e
a trava que dispara é visível, nunca silenciosa.

Os limiares foram calibrados no laboratório com o acervo real (ver o relatório
da F7.1). São ajustáveis por ambiente porque calibração é medida, não opinião.

NENHUMA FUNÇÃO DAQUI DÁ COMMIT
------------------------------
Mesma regra da porta (`porta.py`): quem chama é dono da transação. Uma
afirmação e o assunto que ela criou entram juntos ou não entram.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Iterable, Optional

from bot import lucien as cfg

logger = logging.getLogger("kobe.lucien.assuntos")

TITULO_MIN = 3
TITULO_MAX = 120


def _env(nome: str) -> str:
    return (os.getenv(nome) or "").strip()


def habilitado() -> bool:
    """`LUCIEN_ASSUNTOS_ENABLED` — **ligada quando ausente**.

    Diferente das outras chaves do LUCIEN, esta liga por omissão: ela só muda o
    prompt do curador (sem custo em dinheiro, sem dado saindo da VPS), e uma
    instalação que já roda não deve precisar editar o `.env` para ganhar o
    assunto. Desligar é explícito: `false`. Desligada, o prompt volta a ser, byte
    a byte, o de antes da F7.
    """
    v = _env("LUCIEN_ASSUNTOS_ENABLED").lower()
    if not v:
        return True
    return v in ("1", "true", "yes", "on", "sim")


_DISPONIVEL: dict[str, bool] = {}


def ativo(cx) -> bool:
    """Ligado **e** com a 012 aplicada neste banco.

    A segunda metade existe para que código novo sobre banco velho (a ordem
    errada de um deploy: `git pull` sem `migrate.py up`) degrade para o
    comportamento de antes, em vez de derrubar toda rodada do curador por uma
    coluna que não existe. A resposta fica guardada por conexão de banco."""
    if not habilitado():
        return False
    chave = str(getattr(cx, "info", None) and cx.info.dsn)
    if chave not in _DISPONIVEL:
        try:
            cur = cx.cursor()
            cur.execute("SELECT to_regclass('lucien_subjects') IS NOT NULL AS ok")
            _DISPONIVEL[chave] = bool(cur.fetchone()["ok"])
        except Exception:  # noqa: BLE001
            return False
        if not _DISPONIVEL[chave]:
            logger.warning("lucien: assunto ligado, mas a migration 012 não está "
                           "aplicada neste banco — rodando sem assunto")
    return _DISPONIVEL[chave]


def maximo_vivos() -> int:
    """Quantos assuntos o curador vê por rodada. ~40 cabem em ~3 mil
    caracteres; mais que isso dilui a escolha."""
    try:
        return max(5, int(_env("LUCIEN_ASSUNTOS_MAX") or 40))
    except ValueError:
        return 40


def trgm_reuso() -> float:
    try:
        return float(_env("LUCIEN_ASSUNTOS_TRGM") or 0.60)
    except ValueError:
        return 0.60


def cos_reuso() -> float:
    try:
        return float(_env("LUCIEN_ASSUNTOS_COS") or 0.82)
    except ValueError:
        return 0.82


def slug(texto: str) -> str:
    from bot.lucien.store import slug as _slug

    return _slug(texto)


# ── A lista que o curador vê ──────────────────────────────────────────────

_CAMPOS = ("s.id, s.title, s.description, s.last_touched_at,"
           " (SELECT COUNT(*) FROM lucien_claims c WHERE c.subject_id = s.id) AS n")


def vivos(cx, topic_id: str, *, estado: Optional[dict] = None,
          vetor_sql: Optional[str] = None, radicais: str = "",
          kind: Optional[str] = None, prefixo: str = "A") -> dict[str, dict]:
    """Os assuntos que o curador pode reusar nesta rodada, por apelido (`A1`…).

    A seleção é a mesma ideia de `store.estado_vigente` — **recência ∪
    relevância** —, pelo mesmo motivo medido lá: só recência erra exatamente
    quando um assunto antigo volta à conversa, que é o caso do "retoma".

    - os assuntos das afirmações que o curador já está vendo (o estado);
    - os tocados mais recentemente neste tópico;
    - os de sentido próximo ao lote (vetor), **de qualquer tópico** — a lista é
      global, e é isto que impede o mesmo bug de virar dois assuntos;
    - os que casam com os radicais raros do lote.
    """
    teto = maximo_vivos()
    cur = cx.cursor()
    achados: dict[str, dict] = {}
    # F7 (014): `kind` filtra a natureza — 'tema' (o assunto largo) ou 'conversa'
    # (o subassunto). Sem ele, a lista é a de antes (todas as naturezas).
    so = None if kind is None or not tem_natureza(cx) else kind
    if so not in (None, "tema", "conversa"):
        raise ValueError(f"natureza de assunto desconhecida: {so!r}")
    campos = _CAMPOS + (", s.kind" if tem_natureza(cx) else "")
    fk = f" AND s.kind = '{so}'" if so else ""      # literal fixo, conferido acima

    def _somar(linhas: Iterable[dict]) -> None:
        for r in linhas:
            if len(achados) >= teto:
                return
            achados.setdefault(str(r["id"]), r)

    ids_estado = [str(c["subject_id"]) for c in (estado or {}).values()
                  if c.get("subject_id")]
    if ids_estado:
        cur.execute(f"SELECT {campos} FROM lucien_subjects s"
                    " WHERE s.id = ANY(%s::uuid[]) AND s.status = 'ativo'" + fk,
                    (ids_estado,))
        _somar(cur.fetchall())

    if vetor_sql:
        try:
            cur.execute(f"SELECT {campos} FROM lucien_subjects s"
                        " WHERE s.status = 'ativo' AND s.embedding IS NOT NULL" + fk +
                        " ORDER BY s.embedding <=> %s::vector LIMIT %s",
                        (vetor_sql, max(5, teto // 3)))
            _somar(cur.fetchall())
        except Exception:  # noqa: BLE001 — relevância a menos não derruba a rodada
            logger.info("lucien: sem a perna de sentido na lista de assuntos")

    if radicais:
        try:
            cur.execute(f"SELECT {campos} FROM lucien_subjects s"
                        " WHERE s.status = 'ativo'" + fk +
                        "   AND s.search_tsv @@ to_tsquery('portuguese', %s)"
                        " ORDER BY s.last_touched_at DESC LIMIT %s",
                        (radicais, max(5, teto // 4)))
            _somar(cur.fetchall())
        except Exception:  # noqa: BLE001
            logger.info("lucien: sem a perna de palavra na lista de assuntos")

    cur.execute(f"SELECT {campos} FROM lucien_subjects s"
                " WHERE s.status = 'ativo'" + fk + " AND EXISTS ("
                "   SELECT 1 FROM lucien_claims c"
                "    WHERE c.subject_id = s.id AND c.topic_id = %s)"
                " ORDER BY s.last_touched_at DESC LIMIT %s",
                (topic_id, teto))
    _somar(cur.fetchall())

    ordenados = sorted(achados.values(), key=lambda r: r["last_touched_at"], reverse=True)
    return {f"{prefixo}{i}": r for i, r in enumerate(ordenados, start=1)}


_NATUREZA: dict[str, bool] = {}


def tem_natureza(cx) -> bool:
    """A 014 (tema × conversa) está aplicada neste banco?"""
    chave = str(getattr(cx, "info", None) and cx.info.dsn)
    if chave not in _NATUREZA:
        cur = cx.cursor()
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.columns"
                    " WHERE table_name = 'lucien_subjects' AND column_name = 'kind') AS ok")
        _NATUREZA[chave] = bool(cur.fetchone()["ok"])
    return _NATUREZA[chave]


# ── A T11 ─────────────────────────────────────────────────────────────────


def _seguir_fusao(cur, subject_id: str) -> str:
    """Um assunto fundido aponta para quem o absorveu; a afirmação nova vai para
    o destino final. O teto de saltos só existe contra ciclo — o banco proíbe o
    de um elemento, não o de dois."""
    atual = subject_id
    for _ in range(10):
        cur.execute("SELECT status, merged_into FROM lucien_subjects WHERE id = %s",
                    (atual,))
        r = cur.fetchone()
        if r is None or r["status"] != "fundido" or not r["merged_into"]:
            return atual
        atual = str(r["merged_into"])
    return atual


def ja_existe(cx, titulo: str, *, vetor_sql: Optional[str] = None,
              kind: Optional[str] = None) -> Optional[tuple[str, str]]:
    """O assunto existente que este título duplicaria, e por quê — ou `None`.

    Devolve `(subject_id, motivo)`; `motivo` é `slug`, `trigrama` ou `sentido`.
    """
    cur = cx.cursor()
    s = slug(titulo)
    cur.execute("SELECT id FROM lucien_subjects WHERE slug = %s", (s,))
    r = cur.fetchone()
    if r is not None:
        return _seguir_fusao(cur, str(r["id"])), "slug"
    # F7 (014): conversa só se confunde com conversa, tema com tema — um subassunto
    # de título parecido com o tema dele NÃO é duplicata do tema.
    fk = ""
    if kind in ("tema", "conversa") and tem_natureza(cx):
        fk = f" AND kind = '{kind}'"

    cur.execute(
        "SELECT id, similarity(title, %s) AS sim FROM lucien_subjects"
        " WHERE status = 'ativo' AND title %% %s" + fk +
        " ORDER BY sim DESC LIMIT 1",
        (titulo, titulo),
    )
    r = cur.fetchone()
    if r is not None and float(r["sim"]) >= trgm_reuso():
        return str(r["id"]), "trigrama"

    if vetor_sql:
        cur.execute(
            "SELECT id, 1 - (embedding <=> %s::vector) AS cos FROM lucien_subjects"
            " WHERE status = 'ativo' AND embedding IS NOT NULL" + fk +
            " ORDER BY embedding <=> %s::vector LIMIT 1",
            (vetor_sql, vetor_sql),
        )
        r = cur.fetchone()
        if r is not None and float(r["cos"]) >= cos_reuso():
            return str(r["id"]), "sentido"
    return None


def _vetor_do_titulo(titulo: str, descricao: str = "") -> Optional[tuple[str, str]]:
    """`(vetor_sql, modelo)` do título, ou `None` se o embedder estiver fora.

    Falhar aqui só tira a terceira perna da T11 — slug e trigrama continuam."""
    try:
        from bot.search import embedder

        texto = titulo if not descricao else f"{titulo}: {descricao}"
        return embedder.para_sql(embedder.embed_um(texto)), embedder.modelo()
    except Exception:  # noqa: BLE001
        logger.info("lucien: título de assunto sem vetor agora; a T11 roda sem sentido")
        return None


def criar(cx, titulo: str, *, quando: datetime, descricao: str = "",
          home_topic_id: Optional[str] = None, criado_por: str = "lucien",
          run_id: Optional[str] = None, vetor: Optional[tuple[str, str]] = None,
          motivo: str = "", kind: str = "tema") -> str:
    """Grava um assunto novo e o evento `criado`. Não confere duplicata — isso é
    `resolver_novo`/`ja_existe`; aqui é só a escrita."""
    titulo = " ".join((titulo or "").split())
    if not (TITULO_MIN <= len(titulo) <= TITULO_MAX):
        raise ValueError(f"título de assunto com {len(titulo)} caracteres "
                         f"(limite {TITULO_MIN}–{TITULO_MAX})")
    cur = cx.cursor()
    cur.execute(
        "INSERT INTO lucien_subjects (title, slug, description, home_topic_id,"
        " opened_at, last_touched_at, created_by, run_id, embedding, model, embedded_at)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,"
        "         CASE WHEN %s::text IS NULL THEN NULL ELSE NOW() END)"
        " RETURNING id",
        (titulo, slug(titulo), (descricao or "").strip() or None, home_topic_id,
         quando, quando, criado_por, run_id,
         vetor[0] if vetor else None, vetor[1] if vetor else None,
         vetor[0] if vetor else None),
    )
    sid = str(cur.fetchone()["id"])
    if kind != "tema" and tem_natureza(cx):
        cur.execute("UPDATE lucien_subjects SET kind = %s WHERE id = %s", (kind, sid))
    evento(cx, sid, "criado", None, {"motivo": (motivo or "").strip()[:400], "natureza": kind},
           run_id=run_id, actor=criado_por)
    return sid


def resolver_novo(cx, titulo: str, *, quando: datetime, descricao: str = "",
                  home_topic_id: Optional[str] = None, run_id: Optional[str] = None,
                  motivo: str = "", criado_por: str = "lucien",
                  kind: str = "tema") -> tuple[str, str]:
    """A T11 inteira: reusa se duplicaria, cria se não. Devolve
    `(subject_id, desfecho)` — `criado` ou `evitado:<motivo>`."""
    vetor = _vetor_do_titulo(titulo, descricao)
    dup = ja_existe(cx, titulo, vetor_sql=vetor[0] if vetor else None, kind=kind)
    if dup is not None:
        return dup[0], f"evitado:{dup[1]}"
    return criar(cx, titulo, quando=quando, descricao=descricao,
                 home_topic_id=home_topic_id, criado_por=criado_por,
                 run_id=run_id, vetor=vetor, motivo=motivo, kind=kind), "criado"


def tocar(cx, subject_id: str, quando: datetime) -> None:
    """Abertura e último toque saem das DATAS DOS FATOS, nunca do relógio."""
    cx.cursor().execute(
        "UPDATE lucien_subjects SET opened_at = LEAST(opened_at, %s),"
        " last_touched_at = GREATEST(last_touched_at, %s), updated_at = NOW()"
        " WHERE id = %s",
        (quando, quando, subject_id),
    )


def evento(cx, subject_id: str, action: str, before: Optional[dict],
           detail: Optional[dict], *, run_id: Optional[str] = None,
           actor: str = "lucien") -> str:
    import psycopg

    cur = cx.cursor()
    cur.execute(
        "INSERT INTO lucien_subject_events (subject_id, action, before, detail, actor, run_id)"
        " VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (subject_id, action,
         psycopg.types.json.Jsonb(before) if before else None,
         psycopg.types.json.Jsonb(detail) if detail else None,
         actor, run_id),
    )
    return str(cur.fetchone()["id"])


# ── Fusão e o caminho de volta ────────────────────────────────────────────


def imagem(cx, subject_id: str) -> Optional[dict]:
    import json

    cur = cx.cursor()
    cur.execute("SELECT id, title, slug, description, status, merged_into,"
                " opened_at, last_touched_at FROM lucien_subjects WHERE id = %s",
                (subject_id,))
    r = cur.fetchone()
    return json.loads(json.dumps(r, default=str)) if r else None


def fundir(cx, origem_id: str, destino_id: str, *, actor: str = "operador",
           motivo: str = "", run_id: Optional[str] = None,
           extra: Optional[dict] = None) -> int:
    """Funde `origem` em `destino`: as afirmações mudam de assunto, a origem fica
    `fundido` apontando para o destino. Devolve quantas afirmações mudaram.

    O evento guarda a LISTA das afirmações movidas — é ela que faz `desfazer`
    devolver exatamente o que era, sem adivinhar."""
    destino_id = _seguir_fusao(cx.cursor(), destino_id)
    if str(origem_id) == str(destino_id):
        raise ValueError("fundir um assunto nele mesmo não faz nada")
    antes = imagem(cx, origem_id)
    if antes is None or imagem(cx, destino_id) is None:
        raise LookupError("assunto inexistente")
    if antes["status"] != "ativo":
        raise ValueError(f"o assunto de origem está '{antes['status']}', não ativo")
    cur = cx.cursor()
    cur.execute("UPDATE lucien_claims SET subject_id = %s WHERE subject_id = %s"
                " RETURNING id", (destino_id, origem_id))
    movidas = [str(r["id"]) for r in cur.fetchall()]
    cur.execute("UPDATE lucien_subjects SET status = 'fundido', merged_into = %s,"
                " updated_at = NOW() WHERE id = %s", (destino_id, origem_id))
    cur.execute(
        "UPDATE lucien_subjects d SET"
        "   opened_at = LEAST(d.opened_at, o.opened_at),"
        "   last_touched_at = GREATEST(d.last_touched_at, o.last_touched_at),"
        "   updated_at = NOW()"
        "  FROM lucien_subjects o WHERE d.id = %s AND o.id = %s",
        (destino_id, origem_id))
    evento(cx, origem_id, "fundido", antes,
           {**(extra or {}), "destino": str(destino_id), "afirmacoes": movidas,
            "motivo": (motivo or "").strip()[:400]},
           run_id=run_id, actor=actor)
    return len(movidas)


def desfazer_fusao(cx, event_id: str, *, actor: str = "operador") -> int:
    """Devolve as afirmações da fusão ao assunto de origem e o reativa."""
    cur = cx.cursor()
    cur.execute("SELECT * FROM lucien_subject_events WHERE id = %s", (event_id,))
    ev = cur.fetchone()
    if ev is None or ev["action"] != "fundido":
        raise LookupError(f"o evento {event_id} não é uma fusão")
    detalhe = ev["detail"] or {}
    ids = detalhe.get("afirmacoes") or []
    cur.execute("UPDATE lucien_claims SET subject_id = %s"
                " WHERE id = ANY(%s::uuid[]) AND subject_id = %s",
                (ev["subject_id"], ids, detalhe.get("destino")))
    n = cur.rowcount
    cur.execute("UPDATE lucien_subjects SET status = 'ativo', merged_into = NULL,"
                " updated_at = NOW() WHERE id = %s", (ev["subject_id"],))
    evento(cx, str(ev["subject_id"]), "desfeito", None,
           {"evento_desfeito": str(event_id), "afirmacoes_devolvidas": n},
           actor=actor)
    return n


def desfazer_rodada(cx, run_id: str, *, actor: str = "operador") -> dict:
    """O caminho de volta do agrupamento retroativo inteiro.

    Tira o assunto das afirmações que aquela rodada atribuiu e arquiva os
    assuntos que ela criou e que ficaram sem afirmação nenhuma. Nada é apagado:
    arquivar é status, e o histórico guarda o que houve.
    """
    cur = cx.cursor()
    cur.execute("SELECT subject_id, detail FROM lucien_subject_events"
                " WHERE run_id = %s AND action = 'atribuido'", (run_id,))
    soltas = 0
    origens: set[str] = set()
    for ev in cur.fetchall():
        ids = (ev["detail"] or {}).get("afirmacoes") or []
        # Numa DIVISÃO, a afirmação volta para o assunto de onde saiu; no
        # agrupamento, volta a não ter assunto — que é de onde ela veio.
        de = (ev["detail"] or {}).get("dividido_de")
        c2 = cx.cursor()
        c2.execute("UPDATE lucien_claims SET subject_id = %s"
                   " WHERE id = ANY(%s::uuid[]) AND subject_id = %s",
                   (de, ids, ev["subject_id"]))
        soltas += c2.rowcount
        if de:
            origens.add(str(de))
    for de in origens:
        cur.execute("UPDATE lucien_subjects SET status = 'ativo', updated_at = NOW()"
                    " WHERE id = %s AND status = 'arquivado' RETURNING id", (de,))
        if cur.fetchone():
            evento(cx, de, "reativado", None, {"desfeita_a_rodada": str(run_id)}, actor=actor)
    cur.execute(
        "UPDATE lucien_subjects s SET status = 'arquivado', updated_at = NOW()"
        " WHERE s.run_id = %s AND s.status = 'ativo'"
        "   AND NOT EXISTS (SELECT 1 FROM lucien_claims c WHERE c.subject_id = s.id)"
        " RETURNING id", (run_id,))
    arquivados = [str(r["id"]) for r in cur.fetchall()]
    for sid in arquivados:
        evento(cx, sid, "arquivado", None, {"desfeita_a_rodada": str(run_id)}, actor=actor)
    return {"afirmacoes_soltas": soltas, "assuntos_arquivados": len(arquivados)}



# ── O vetor dos assuntos ──────────────────────────────────────────────────


def embeddar_pendentes(cx, *, limite: int = 200, cliente=None) -> int:
    """Preenche o vetor dos assuntos que ainda não têm. Depois da escrita, como
    o das afirmações (`consulta.embeddar_pendentes`) — o assunto nunca deixa de
    ser criado porque o embedder estava fora."""
    from bot.search import embedder

    cur = cx.cursor()
    cur.execute("SELECT id, title, description FROM lucien_subjects"
                " WHERE embedding IS NULL ORDER BY created_at LIMIT %s", (limite,))
    linhas = cur.fetchall()
    if not linhas:
        return 0
    textos = [r["title"] if not r["description"] else f"{r['title']}: {r['description']}"
              for r in linhas]
    vetores = embedder.embed(textos, cliente=cliente)
    modelo = embedder.modelo()
    for r, v in zip(linhas, vetores):
        cur.execute("UPDATE lucien_subjects SET embedding = %s::vector, model = %s,"
                    " embedded_at = NOW() WHERE id = %s",
                    (embedder.para_sql(v), modelo, r["id"]))
    return len(linhas)


def contar(cx) -> dict[str, Any]:
    """Os números que dizem se o vocabulário está saudável."""
    cur = cx.cursor()
    cur.execute(
        "WITH n AS (SELECT s.id, COUNT(c.id) AS k FROM lucien_subjects s"
        "             LEFT JOIN lucien_claims c ON c.subject_id = s.id"
        "            WHERE s.status = 'ativo' GROUP BY s.id)"
        " SELECT (SELECT COUNT(*) FROM n) AS assuntos,"
        "        (SELECT COUNT(*) FROM n WHERE k = 1) AS de_um_so,"
        "        (SELECT COUNT(*) FROM n WHERE k > 40) AS acima_de_40,"
        "        (SELECT COALESCE(MAX(k), 0) FROM n) AS maior,"
        "        (SELECT COUNT(*) FROM lucien_claims) AS afirmacoes,"
        "        (SELECT COUNT(*) FROM lucien_claims WHERE subject_id IS NOT NULL) AS com_assunto")
    return dict(cur.fetchone())
