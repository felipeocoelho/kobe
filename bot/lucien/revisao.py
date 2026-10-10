"""A revisão da memória (Highlander v3, F5) — o LUCIEN conta o que gravou e
pergunta o que morreu; o operador corrige solto.

O CICLO, NA ORDEM
-----------------
1. Uma vez por semana (padrão: segunda 9h), a fonte do Keyko monta **uma**
   mensagem, consolidando todos os tópicos, e manda no privado do operador:
   bloco A — o que o LUCIEN deduziu desde a última revisão ("confere pra mim");
   bloco B — pendências abertas há muito tempo ("alguma já morreu?").
2. A revisão fica **aberta** até a próxima. Enquanto aberta, o prompt do privado
   traz o bloco `[Revisão da memória aguardando tua resposta]` com os números.
3. O operador responde solto. O Hal traduz a frase em operações de vocabulário
   fechado e chama `kobe-revisao aplicar`. **O comando** valida e grava, pela
   porta (`porta.py`). O Hal devolve a foto de como ficou.
4. Silêncio: nada acontece. A próxima revisão substitui a anterior; o que foi
   gravado continua gravado; pendência perguntada e não respondida só volta
   depois de N semanas. Ninguém é cobrado.

QUEM É DONO DE QUÊ — o precedente dos Alertas
---------------------------------------------
O código decide quando mandar, o que entra, a numeração e o estado. O modelo
**não escreve** a mensagem de revisão (é um modelo fixo, montado aqui: custo
zero e nenhuma chance de inventar item) e, na resposta, só traduz linguagem em
operação — quem grava é o comando.

ONDE MORA O QUÊ
---------------
- O que foi **corrigido** é verdade do banco: cada correção vira evento em
  `lucien_events` com `detail.revisao` e `detail.item`, a frase do operador e a
  origem da fala (`origem_seq`, `origem_topico`).
- O que foi **perguntado** (numeração, janela, pendências já perguntadas) mora em
  `user-data/lucien/revisao.json`, gravado de forma atômica e sob trava. Perder
  este arquivo não perde correção nenhuma — só faz a próxima revisão olhar uma
  janela de 7 dias e poder repetir uma pendência.

POR QUE A MENSAGEM NÃO ENTRA NA CONVERSA GRAVADA
------------------------------------------------
Se a lista entrasse em `messages`, o LUCIEN a leria na rodada seguinte e poderia
transformar os próprios títulos em "fatos novos". O bloco no prompt é o que dá ao
Hal o contexto. Pelo mesmo motivo, a foto que o comando devolve começa com
`MARCA` — e o LUCIEN não lê mensagem do agente que a contenha (`store.montar_lote`).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from bot import lucien as cfg
from bot.lucien import porta, store

logger = logging.getLogger("kobe.lucien.revisao")

TZ = ZoneInfo("America/Sao_Paulo")

# A marca que abre a mensagem de revisão e a foto de "como ficou". O LUCIEN
# ignora mensagem do agente que a contenha — ver o cabeçalho.
MARCA = "🗂️ Revisão da memória"

# O Telegram aceita 4.096 caracteres por mensagem. A folga cobre a conversão de
# Markdown em HTML que o `kobe-notify` faz.
TEXTO_MAXIMO = 3800
TITULO_MAXIMO = 70
FOTO_TEXTO_MAXIMO = 160

ACOES = ("confere", "contestar", "corrigir", "fechar", "abandonar", "manter", "desfazer")

ARQUIVO = "revisao.json"


class RevisaoRecusou(Exception):
    """O comando recusou a operação — e a mensagem diz por quê, para o Hal
    repetir ao operador ou refazer a tradução."""


# ── Estado em disco ──────────────────────────────────────────────────────


def caminho_estado(kobe_home) -> Path:
    return Path(kobe_home) / "user-data" / "lucien" / ARQUIVO


@contextlib.contextmanager
def travado(kobe_home):
    """Trava exclusiva do arquivo de estado. O comando do Hal e a fonte do Keyko
    podem tocar o estado ao mesmo tempo (operador respondendo no minuto em que a
    revisão nova sai)."""
    p = caminho_estado(kobe_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(str(p) + ".lock", "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def ler_estado(kobe_home) -> dict:
    p = caminho_estado(kobe_home)
    try:
        dados = json.loads(p.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.exception("revisao: estado ilegível em %s — recomeçando", p)
        return {}


def gravar_estado(kobe_home, estado: dict) -> None:
    p = caminho_estado(kobe_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, p)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _de_iso(valor: Optional[str]) -> Optional[datetime]:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _dia(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d/%m")


def _titulo(texto: str) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= TITULO_MAXIMO else t[: TITULO_MAXIMO - 1].rstrip() + "…"


# ── Seleção ──────────────────────────────────────────────────────────────


@dataclass
class Selecao:
    gravadas: list[dict]       # bloco A, já cortado no teto
    gravadas_fora: int         # quantas do bloco A ficaram de fora
    do_operador: int           # quantas o operador mesmo registrou na janela
    pendencias: list[dict]     # bloco B, já cortado no teto
    desde: datetime
    ate: datetime

    @property
    def vazia(self) -> bool:
        return not self.gravadas and not self.pendencias


def selecionar(cx, *, desde: datetime, ate: datetime,
               perguntadas: Optional[dict] = None) -> Selecao:
    """Os dois blocos, em todos os tópicos. Só leitura.

    Bloco A — o que o **LUCIEN deduziu** na janela e ainda vale. O que o operador
    registrou pela porta é palavra dele: vira só uma contagem. Confiança mais
    baixa primeiro — é onde o erro mora —, depois por ordem de gravação. Teto
    global.

    Bloco B — pendências vigentes abertas há `idade` dias ou mais, as mais
    antigas primeiro, sem as perguntadas ou reconfirmadas nas últimas N semanas.
    Teto global.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT c.id, c.subject, c.statement, c.kind, c.confidence, c.created_at,"
        "       COALESCE(t.current_name, '(sem nome)') AS topico"
        "  FROM lucien_claims c JOIN topics t ON t.id = c.topic_id"
        " WHERE c.created_by = 'lucien' AND c.status = 'vigente'"
        "   AND c.created_at >= %s AND c.created_at < %s"
        " ORDER BY CASE c.confidence WHEN 'baixa' THEN 0 WHEN 'media' THEN 1"
        "          ELSE 2 END, c.created_at, c.id",
        (desde, ate),
    )
    todas = list(cur.fetchall())
    teto_a = cfg.revisao_teto_gravadas()

    # O que o operador registrou na janela — menos as correções que ele mesmo
    # fez numa revisão anterior (essas já passaram pelo olho dele).
    cur.execute(
        "SELECT count(*) AS n FROM lucien_claims c"
        " WHERE c.created_by = 'operador' AND c.created_at >= %s AND c.created_at < %s"
        "   AND NOT EXISTS (SELECT 1 FROM lucien_events e WHERE e.claim_id = c.id"
        "                    AND e.action = 'created' AND e.detail->>'porta' = 'revisao')",
        (desde, ate),
    )
    do_operador = int(cur.fetchone()["n"])

    idade = timedelta(days=cfg.revisao_idade_pendencia_dias())
    janela_repergunta = timedelta(weeks=cfg.revisao_repergunta_semanas())
    recentes = set()
    for cid, quando in (perguntadas or {}).items():
        dt = _de_iso(quando)
        if dt is not None and ate - dt < janela_repergunta:
            recentes.add(str(cid))
    ids_a = [str(r["id"]) for r in todas]
    cur.execute(
        "SELECT c.id, c.subject, c.statement, c.kind, c.valid_from,"
        "       COALESCE(t.current_name, '(sem nome)') AS topico"
        "  FROM lucien_claims c JOIN topics t ON t.id = c.topic_id"
        " WHERE c.kind = 'open' AND c.status = 'vigente' AND c.valid_from < %s"
        "   AND NOT (c.id::text = ANY(%s))"
        "   AND NOT EXISTS (SELECT 1 FROM lucien_events e WHERE e.claim_id = c.id"
        "                    AND e.action = 'reconfirmed' AND e.at >= %s)"
        " ORDER BY c.valid_from, c.id",
        (ate - idade, list(recentes | set(ids_a)), ate - janela_repergunta),
    )
    pend = list(cur.fetchall())[: cfg.revisao_teto_pendencias()]

    return Selecao(
        gravadas=todas[:teto_a],
        gravadas_fora=max(0, len(todas) - teto_a),
        do_operador=do_operador,
        pendencias=pend,
        desde=desde,
        ate=ate,
    )


# ── A mensagem ───────────────────────────────────────────────────────────


def _itens(sel: Selecao) -> list[dict]:
    """Numeração GLOBAL, 1..N atravessando os tópicos: "o 3" nunca é ambíguo."""
    itens: list[dict] = []
    for r in sel.gravadas:
        itens.append({"n": len(itens) + 1, "bloco": "gravada", "claim_id": str(r["id"]),
                      "topico": r["topico"], "titulo": _titulo(r["subject"]),
                      "kind": r["kind"]})
    for r in sel.pendencias:
        itens.append({"n": len(itens) + 1, "bloco": "pendencia", "claim_id": str(r["id"]),
                      "topico": r["topico"], "titulo": _titulo(r["subject"]),
                      "kind": r["kind"], "desde": _dia(r["valid_from"])})
    return itens


def _linha(item: dict) -> str:
    extra = f" (aberta desde {item['desde']})" if item.get("desde") else ""
    return f"{item['n']}. [{item['topico']}] {item['titulo']}{extra}"


def montar_texto(sel: Selecao, itens: list[dict], *, fora_extra: int = 0) -> str:
    gravadas = [i for i in itens if i["bloco"] == "gravada"]
    pend = [i for i in itens if i["bloco"] == "pendencia"]
    partes = [f"{MARCA} — Lucien aqui.", ""]
    if gravadas:
        partes.append(
            f"Oi! Gravei estas coisas na memória desde {_dia(sel.desde)} — "
            "confere pra mim?")
        partes += [_linha(i) for i in gravadas]
        fora = sel.gravadas_fora + fora_extra
        if fora:
            partes.append(f"(e mais {fora} que não couberam aqui — se quiser ver, é só pedir)")
    else:
        partes.append(f"Oi! Desde {_dia(sel.desde)} não deduzi nada novo da conversa.")
    if sel.do_operador:
        partes.append(
            f"Além disso, registrei {sel.do_operador} coisa(s) que você mesmo me "
            "pediu — não repito aqui.")
    if pend:
        partes += ["", "E estas pendências estão abertas há muito tempo — alguma já morreu?"]
        partes += [_linha(i) for i in pend]
    partes += [
        "",
        "Se precisar de ajuste é só falar, do jeito que vier — pode mandar áudio "
        "(ex.: \"o 3 tá errado\", \"a 12 eu já entreguei\"). Se estiver tudo "
        "certo, não precisa responder.",
    ]
    return "\n".join(partes)


def preparar(sel: Selecao) -> tuple[list[dict], str]:
    """Itens numerados e o texto que cabe numa mensagem. Se não couber, corta do
    fim do bloco A (o mais confiável) e diz quantos ficaram de fora; em último
    caso, do fim do bloco B."""
    itens = _itens(sel)
    fora_extra = 0
    texto = montar_texto(sel, itens)
    while len(texto) > TEXTO_MAXIMO and itens:
        gravadas = [i for i in itens if i["bloco"] == "gravada"]
        alvo = gravadas[-1] if gravadas else itens[-1]
        itens.remove(alvo)
        if alvo["bloco"] == "gravada":
            fora_extra += 1
        for k, i in enumerate(itens, start=1):
            i["n"] = k
        texto = montar_texto(sel, itens, fora_extra=fora_extra)
    return itens, texto


# ── A revisão aberta ─────────────────────────────────────────────────────


def aberta(kobe_home) -> Optional[dict]:
    return ler_estado(kobe_home).get("aberta") or None


def _item(rev: dict, n: Any) -> dict:
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise RevisaoRecusou(f"item {n!r} não é um número.") from None
    for i in rev.get("itens", []):
        if i["n"] == n:
            return i
    raise RevisaoRecusou(
        f"não existe item {n} na revisão aberta (vai de 1 a {len(rev.get('itens', []))})."
    )


def _ja_revertido(cx, event_id: str) -> bool:
    cur = cx.cursor()
    cur.execute(
        "SELECT 1 FROM lucien_events WHERE action = 'reverted'"
        "   AND detail->>'evento_revertido' = %s LIMIT 1",
        (event_id,),
    )
    return cur.fetchone() is not None


def _desfazer(cx, rev: dict, item: dict, *, origem: dict, frase: str) -> list[str]:
    """Desfaz o que ESTA revisão aplicou no item, do mais novo para o mais velho.

    Contestar/fechar/abandonar/substituir → `store.reverter` devolve a imagem de
    antes. A afirmação nova que uma correção criou → sai de vigência como
    abandonada, com o motivo. Reconfirmar não mudou a linha: não há o que
    desfazer.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT id, claim_id, action FROM lucien_events"
        " WHERE detail->>'revisao' = %s AND detail->>'item' = %s"
        "   AND action IN ('contested','closed','abandoned','superseded','created')"
        "   AND NOT (detail ? 'desfazimento')"
        " ORDER BY at DESC, id DESC",
        (rev["id"], str(item["n"])),
    )
    eventos = [e for e in cur.fetchall() if not _ja_revertido(cx, str(e["id"]))]
    if not eventos:
        raise RevisaoRecusou(f"não há nada aplicado no item {item['n']} para desfazer.")
    detalhe = porta._detalhe(origem, frase, {"revisao": rev["id"], "item": item["n"]})
    feitos = []
    # A afirmação nova primeiro (sai de vigência), depois a antiga volta.
    for e in sorted(eventos, key=lambda e: e["action"] != "created"):
        if e["action"] == "created":
            img = store._imagem(cx, str(e["claim_id"]))
            if img and img["status"] == "vigente":
                cx.cursor().execute(
                    "UPDATE lucien_claims SET status = 'abandonada', valid_to = %s,"
                    " updated_at = NOW() WHERE id = %s",
                    (datetime.now(timezone.utc), e["claim_id"]),
                )
                store._evento(cx, str(e["claim_id"]), "abandoned", img,
                              {**detalhe, "motivo": "correção desfeita na revisão",
                               "desfazimento": True},
                              None, actor="operador")
                # marca o 'created' como desfeito, para um 2º "desfaz" não repetir
                store._evento(cx, str(e["claim_id"]), "reverted", img,
                              {**detalhe, "evento_revertido": str(e["id"])}, None,
                              actor="operador")
            feitos.append("correção retirada")
        else:
            store.reverter(cx, str(e["id"]), detalhe=detalhe)
            feitos.append(f"{e['action']} desfeito")
    return feitos


def aplicar(cx, rev: dict, operacoes: list[dict], *, frase: str,
            origem: dict) -> list[dict]:
    """Aplica as operações, em ordem, **tudo ou nada** — quem commita é quem
    chama, e só se nada foi recusado. Devolve o que foi feito, item a item."""
    if not isinstance(operacoes, list) or not operacoes:
        raise RevisaoRecusou("nenhuma operação — mande ao menos uma.")
    feito: list[dict] = []
    for op in operacoes:
        if not isinstance(op, dict):
            raise RevisaoRecusou(f"operação mal formada: {op!r}")
        acao = str(op.get("acao", "")).strip()
        if acao not in ACOES:
            raise RevisaoRecusou(f"ação {acao!r} não existe. As que existem: {', '.join(ACOES)}.")
        item = _item(rev, op.get("item"))
        extra = {"revisao": rev["id"], "item": item["n"]}
        cid = item["claim_id"]
        try:
            if acao == "confere":
                r = {"nada": True}
            elif acao == "contestar":
                r = porta.contestar(cx, cid, origem=origem, frase=frase, extra=extra)
            elif acao == "corrigir":
                texto = str(op.get("texto") or "").strip()
                if not texto:
                    raise RevisaoRecusou(
                        f"item {item['n']}: corrigir precisa do texto novo, numa frase "
                        "que se sustente sozinha (o que passa a valer).")
                r = porta.corrigir(cx, cid, novo_texto=texto, origem=origem,
                                   frase=frase, extra=extra)
            elif acao in ("fechar", "abandonar"):
                if item["kind"] != "open":
                    raise RevisaoRecusou(
                        f"item {item['n']} não é pendência — não se fecha. Se está "
                        "errado, é contestar; se mudou, é corrigir.")
                r = porta.encerrar(cx, cid, acao="closed" if acao == "fechar" else "abandoned",
                                   origem=origem, frase=frase, extra=extra)
            elif acao == "manter":
                r = porta.reconfirmar(cx, cid, origem=origem, frase=frase, extra=extra)
            else:  # desfazer
                r = {"desfeito": _desfazer(cx, rev, item, origem=origem, frase=frase)}
        except porta.PortaRecusou as exc:
            raise RevisaoRecusou(f"item {item['n']}: {exc}") from None
        feito.append({"item": item["n"], "acao": acao, **r})
    return feito


# ── A foto: como ficou ───────────────────────────────────────────────────


_ROTULO = {
    "vigente": "segue valendo",
    "contestada": "❌ contestada — saiu da memória",
    "fechada": "✔️ fechada",
    "abandonada": "🗑️ abandonada",
    "superada": "✏️ corrigida",
}


def foto(cx, rev: dict) -> str:
    """O ESTADO dos itens da revisão agora — não a lista de operações. Itens que
    esta revisão tocou aparecem um a um; os intocados, numa linha só."""
    ids = [i["claim_id"] for i in rev.get("itens", [])]
    cur = cx.cursor()
    cur.execute(
        "SELECT c.id, c.status, c.superseded_by, n.statement AS novo"
        "  FROM lucien_claims c LEFT JOIN lucien_claims n ON n.id = c.superseded_by"
        " WHERE c.id::text = ANY(%s)",
        (ids,),
    )
    estado = {str(r["id"]): r for r in cur.fetchall()}
    cur.execute(
        "SELECT DISTINCT (detail->>'item')::int AS n FROM lucien_events"
        " WHERE detail->>'revisao' = %s",
        (rev["id"],),
    )
    tocados = {r["n"] for r in cur.fetchall()}
    cur.execute(
        "SELECT (detail->>'item')::int AS n FROM lucien_events"
        " WHERE detail->>'revisao' = %s AND action = 'reconfirmed'",
        (rev["id"],),
    )
    reconfirmados = {r["n"] for r in cur.fetchall()}

    linhas = [f"{MARCA} — como ficou", ""]
    intocados = []
    for i in rev.get("itens", []):
        if i["n"] not in tocados:
            intocados.append(i["n"])
            continue
        e = estado.get(i["claim_id"])
        status = e["status"] if e else "?"
        rotulo = _ROTULO.get(status, status)
        if status == "vigente" and i["n"] in reconfirmados:
            rotulo = "✅ segue valendo (você reconfirmou)"
        linha = f"{i['n']}. [{i['topico']}] {i['titulo']} — {rotulo}"
        if status == "superada" and e and e.get("novo"):
            novo = " ".join(e["novo"].split())
            if len(novo) > FOTO_TEXTO_MAXIMO:
                novo = novo[: FOTO_TEXTO_MAXIMO - 1].rstrip() + "…"
            linha += f": \"{novo}\""
        linhas.append(linha)
    if intocados:
        linhas += ["", "Os demais seguem como estavam: " + _faixas(intocados) + "."]
    return "\n".join(linhas)


def _faixas(ns: list[int]) -> str:
    ns = sorted(ns)
    partes, ini, ant = [], ns[0], ns[0]
    for n in ns[1:] + [None]:
        if n is not None and n == ant + 1:
            ant = n
            continue
        partes.append(str(ini) if ini == ant else f"{ini}–{ant}")
        if n is not None:
            ini = ant = n
    return ", ".join(partes)


# ── O bloco do prompt ────────────────────────────────────────────────────


def render_bloco(kobe_home, chat_id: Optional[int], thread_id: Optional[int]) -> Optional[str]:
    """O bloco `[Revisão da memória aguardando tua resposta]`, ou `None`.

    Só no privado configurado, só com a chave ligada, só com revisão aberta.
    **Nunca levanta** — é contexto, e contexto não derruba turno.
    """
    try:
        if not cfg.revisao_habilitada():
            return None
        alvo = cfg.revisao_chat_id()
        if alvo is None or chat_id is None or int(chat_id) != alvo:
            return None
        if thread_id not in (None, 0):
            return None
        rev = aberta(kobe_home)
        if not rev or not rev.get("itens"):
            return None
        enviada = _de_iso(rev.get("enviada_em"))
        quando = _dia(enviada) if enviada else "?"
        linhas = [
            "[Revisão da memória aguardando tua resposta]",
            f"O LUCIEN mandou neste chat, em {quando}, a revisão da memória (o que ele "
            "gravou + pendências velhas). SE a mensagem do operador se referir a ela "
            "(\"o 3 tá errado\", \"a 12 já entreguei\", \"o 5 é só pra dev\", \"tá tudo "
            "certo\"), carregue `docs/agente/revisao-da-memoria.md` e aplique com "
            "`bot/bin/kobe-revisao aplicar`. Se a mensagem for sobre outra coisa, "
            "ignore este bloco. Silêncio não é resposta: não cobre, não lembre.",
            "Itens (número → [tópico] título):",
        ]
        for i in rev["itens"]:
            tipo = "pendência" if i["bloco"] == "pendencia" else "gravado"
            linhas.append(f"  {i['n']}. [{i['topico']}] {i['titulo']} ({tipo})")
        return "\n".join(linhas)
    except Exception:  # noqa: BLE001
        logger.warning("revisao: falha montando o bloco do prompt", exc_info=True)
        return None


# ── O ciclo (chamado pela fonte e por `kobe-revisao enviar`) ─────────────


def enviar(kobe_home, chat_id: int, texto: str, *, token: str = "") -> bool:
    """Manda pelo `kobe-notify`, no privado. Devolve se o Telegram aceitou.
    **Nunca levanta.**

    O token vai EXPLÍCITO: o `kobe-notify` só o lê de `KOBE_TELEGRAM_BOT_TOKEN`,
    que o Keyko injeta nos processos que ele ACORDA — e esta fonte não acorda
    ninguém. Sem isto a revisão falhava no Keyko com "token ausente" (achado no
    teste de ponta a ponta no ambiente de desenvolvimento, 10/10/2026).
    """
    import subprocess

    helper = os.path.join(str(kobe_home), "bot", "bin", "kobe-notify")
    env = dict(os.environ)
    token = (token or env.get("KOBE_TELEGRAM_BOT_TOKEN")
             or env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        env["KOBE_TELEGRAM_BOT_TOKEN"] = token
    env["KOBE_CHAT_ID"] = str(chat_id)
    env.pop("KOBE_THREAD_ID", None)
    try:
        proc = subprocess.run([helper, texto], env=env, timeout=30,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              check=False)
    except Exception:  # noqa: BLE001
        logger.exception("revisao: falha ao chamar o kobe-notify")
        return False
    if proc.returncode != 0:
        logger.warning("revisao: kobe-notify saiu %s: %s", proc.returncode,
                       (proc.stderr or b"").decode("utf-8", "replace").strip()[:300])
        return False
    return True


def rodar_ciclo(cx, kobe_home, *, agora: Optional[datetime] = None,
                chat_id: Optional[int] = None, enviar_fn=None,
                marco: Optional[datetime] = None, token: str = "") -> dict:
    """Uma revisão: seleciona, monta, envia e, **só se o envio deu certo**, troca a
    revisão aberta pela nova. Lê o banco, não escreve nele.

    `marco` é o horário agendado que esta rodada cumpre (a fonte passa); fica
    gravado para a mesma segunda não disparar duas vezes.
    """
    agora = agora or datetime.now(timezone.utc)
    chat_id = chat_id if chat_id is not None else cfg.revisao_chat_id()
    if chat_id is None:
        return {"ok": False, "motivo": "sem chat de destino (LUCIEN_REVIEW_CHAT_ID)"}
    if enviar_fn is None:
        def enviar_fn(home, chat, texto):
            return enviar(home, chat, texto, token=token)
    with travado(kobe_home):
        estado = ler_estado(kobe_home)
        desde = _de_iso(estado.get("janela_ate")) or (agora - timedelta(days=7))
        perguntadas = dict(estado.get("perguntadas") or {})
        sel = selecionar(cx, desde=desde, ate=agora, perguntadas=perguntadas)
        if sel.vazia:
            # Nada a perguntar: a semana virou mesmo assim. A revisão anterior
            # fecha (a numeração dela não vale mais) e a janela anda.
            estado.update({"aberta": None, "janela_ate": _iso(agora),
                           "ultimo_marco": _iso(marco or agora), "falhas": 0})
            gravar_estado(kobe_home, estado)
            return {"ok": True, "enviada": False, "motivo": "nada a revisar"}
        itens, texto = preparar(sel)
        if not enviar_fn(kobe_home, chat_id, texto):
            estado["falhas"] = int(estado.get("falhas") or 0) + 1
            gravar_estado(kobe_home, estado)
            return {"ok": False, "enviada": False, "motivo": "envio falhou",
                    "falhas": estado["falhas"]}
        rev_id = "rev-" + agora.astimezone(TZ).strftime("%Y%m%d-%H%M%S")
        # Esquece o que já saiu da janela de repergunta — sem isto o arquivo
        # cresce para sempre.
        limite = agora - timedelta(weeks=cfg.revisao_repergunta_semanas())
        perguntadas = {k: v for k, v in perguntadas.items()
                       if (_de_iso(v) or agora) >= limite}
        for i in itens:
            if i["bloco"] == "pendencia":
                perguntadas[i["claim_id"]] = _iso(agora)
        estado.update({
            "aberta": {"id": rev_id, "enviada_em": _iso(agora), "chat_id": chat_id,
                       "desde": _iso(desde), "ate": _iso(agora), "itens": itens,
                       # o texto exato que saiu — auditoria do que ele recebeu
                       "texto": texto},
            "anterior": (estado.get("aberta") or {}).get("id"),
            "janela_ate": _iso(agora),
            "ultima_enviada": _iso(agora),
            "ultimo_marco": _iso(marco or agora),
            "perguntadas": perguntadas,
            "falhas": 0,
        })
        gravar_estado(kobe_home, estado)
        return {"ok": True, "enviada": True, "id": rev_id, "itens": len(itens),
                "texto": texto}
