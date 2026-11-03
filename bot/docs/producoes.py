"""O catálogo do que as salas produziram — e o DESFECHO de cada produção.

O DESFECHO NÃO SE ADIVINHA
--------------------------
Ele nasce `desconhecido` e só muda por uma de duas portas:

1. **O estado do Coder** (`plan_approved`), gravado pelo próprio Coder no instante
   em que o operador aprova — fonte `estado_coder`. É a única inferência, e ela não é
   inferência: é o registro da aprovação.
2. **O operador**, por comando, com o `#número` da fala dele — fonte `operador`.
   É a ÚNICA porta de `recusado` e `superado` (o banco recusa as duas sem
   `origin_seq`), e é também como se corrige um `aprovado` que veio por outro
   caminho. Caso real: o redesenho da sala 243c98fa foi aprovado (#4251) depois de
   a sala ter morrido `failed` — o estado dela nunca soube.

`sem_resposta` existe no schema mas NÃO é deduzido aqui: "a sala acabou sem
aprovação registrada" não prova que o operador não respondeu — a aprovação pode ter
vindo por outra sala, como no caso acima. Só entra pela porta do operador.

O que veio do operador nunca é sobrescrito pelo estado do Coder.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from bot.docs import raizes

logger = logging.getLogger("kobe.docs.producoes")

DESFECHOS = ("aprovado", "recusado", "sem_resposta", "superado", "desconhecido")


def aprovacoes_do_coder(kobe_home: Path) -> dict[str, bool]:
    """`short_id → plan_approved`, lido dos estados das sessões do Coder.

    Só dois campos de cada arquivo são usados. Sessão antiga sem o campo não
    entra (não se sabe). Arquivo ilegível é pulado com aviso, nunca adivinhado.
    """
    base = Path(kobe_home) / "user-data" / "coder-sessions"
    saida: dict[str, bool] = {}
    if not base.is_dir():
        return saida
    for f in sorted(base.glob("*/*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("docs: estado ilegível %s: %s", f.name, exc)
            continue
        sid = (d.get("short_id") or "").strip()
        if sid and "plan_approved" in d:
            saida[sid] = saida.get(sid, False) or bool(d.get("plan_approved"))
    return saida


def atualizar(db, arquivos: list[raizes.Arquivo], *, kobe_home: Path,
              ler_estado_coder: bool = True) -> int:
    """Garante uma produção por artefato de sala e aplica o estado do Coder.
    Devolve quantas linhas mudaram."""
    salas = [a for a in arquivos if a.sala_kind]
    if not salas:
        return 0
    ids = {(l["root"], l["rel_path"]): l["id"] for l in db.query(
        "SELECT id, root, rel_path FROM doc_sources WHERE nature = 'artefato_sala'")}
    atuais = {l["doc_id"]: l for l in db.query(
        "SELECT doc_id, outcome, outcome_source FROM doc_producoes")}
    aprov = aprovacoes_do_coder(kobe_home) if ler_estado_coder else {}
    mudou = 0
    for a in salas:
        doc_id = ids.get((a.root, a.rel_path))
        if doc_id is None:
            continue
        if doc_id not in atuais:
            db.execute(
                "INSERT INTO doc_producoes (doc_id, sala_id, sala_kind, produced_at)"
                " SELECT %s, %s, %s, file_mtime FROM doc_sources WHERE id = %s"
                " ON CONFLICT (doc_id) DO NOTHING",
                (doc_id, a.sala_id, a.sala_kind, doc_id),
            )
            atuais[doc_id] = {"outcome": "desconhecido", "outcome_source": None}
            mudou += 1
        atual = atuais[doc_id]
        if (a.sala_kind == "coder" and aprov.get(a.sala_id)
                and atual["outcome_source"] in (None, "estado_coder")
                and atual["outcome"] != "aprovado"):
            db.execute(
                "UPDATE doc_producoes SET outcome='aprovado', outcome_source='estado_coder',"
                " outcome_at=NOW(), updated_at=NOW() WHERE doc_id=%s",
                (doc_id,),
            )
            mudou += 1
    return mudou


class DesfechoRecusado(ValueError):
    """O pedido de desfecho não tem a base que o registro exige."""


def registrar_desfecho(db, doc_id: int, desfecho: str, origem_seq: int) -> dict:
    """A porta do operador. Exige que `origem_seq` seja uma fala DELE (role
    `user`) — o desfecho tem que ter a voz dele atrás, não a do agente."""
    if desfecho not in DESFECHOS:
        raise DesfechoRecusado(f"desfecho desconhecido: {desfecho}")
    prod = db.query("SELECT doc_id FROM doc_producoes WHERE doc_id = %s", (doc_id,))
    if not prod:
        raise DesfechoRecusado(f"o documento {doc_id} não é produção de sala")
    msg = db.query("SELECT role FROM messages WHERE seq = %s", (origem_seq,))
    if not msg:
        raise DesfechoRecusado(f"não existe mensagem #{origem_seq}")
    if msg[0]["role"] != "user":
        raise DesfechoRecusado(
            f"#{origem_seq} não é fala do operador (é {msg[0]['role']}) — o desfecho "
            "precisa da voz dele")
    db.execute(
        "UPDATE doc_producoes SET outcome=%s, outcome_source='operador', origin_seq=%s,"
        " outcome_at=(SELECT created_at FROM messages WHERE seq=%s), updated_at=NOW()"
        " WHERE doc_id=%s",
        (desfecho, origem_seq, origem_seq, doc_id),
    )
    return {"doc_id": doc_id, "outcome": desfecho, "origin_seq": origem_seq}


def arquivo_de_plano(abs_path: Path, kobe_home: Path) -> Optional[raizes.Arquivo]:
    """O `Arquivo` de um plano anexado agora, se ele mora numa raiz conhecida.

    É o que o `kobe-attach` usa para registrar o plano NA HORA — sem esperar o
    tick e sem depender de a worktree sobreviver."""
    p = Path(abs_path).resolve()
    if not raizes._RE_PLANO.match(p.name) or p.parent.name != ".local":
        return None
    sala = p.parent.parent
    for nome, base in raizes.bases(kobe_home).items():
        dir_salas = base / "coder-worktrees" if nome == "casa" else (
            base if nome == "worktrees" else None)
        if dir_salas is None:
            continue
        try:
            if sala.parent.resolve() != dir_salas.resolve():
                continue
        except OSError:
            continue
        return raizes._novo(nome, base.resolve(), p, "artefato_sala", None,
                            sala_id=sala.name, sala_kind="coder")
    return None


def registrar_plano_anexado(db, abs_path: Path, kobe_home: Path) -> bool:
    """Grava o plano e a produção na hora. Nunca levanta (quem chama é o anexo,
    que não pode falhar por isto)."""
    from bot.docs import indexer

    try:
        a = arquivo_de_plano(abs_path, kobe_home)
        if a is None:
            return False
        indexer.sincronizar(db, [a], raizes_varridas=set())
        atualizar(db, [a], kobe_home=kobe_home, ler_estado_coder=False)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("docs: não registrei o plano anexado: %s", exc)
        return False
