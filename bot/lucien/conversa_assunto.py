"""A conversa do assunto — as mensagens inteiras, com as partes de outro assunto
sinalizadas (Highlander v3, F7).

O QUE É
-------
É a "lista de chats" que o operador pediu para emular: abrir um assunto e ler a
conversa dele, do jeito que ela aconteceu. Cada mensagem marcada com o assunto
aparece INTEIRA (decisão do operador, 23/10/2026: *"pense numa forma de levar a
mensagem inteira… sinalize que tal parte da mensagem é uma referência a outro
assunto"*). A parte que é de outro assunto vem com uma linha `↪` e a marca `┆`
— que saem DAQUI, do código de apresentação. O texto é sempre o gravado.

O LIMITE, E A ORDEM DO QUE SE RECOLHE (aprovado em 31/10/2026)
--------------------------------------------------------------
Nada se corta em silêncio. Quando a conversa não cabe no teto:

1. as partes **de outro assunto** são recolhidas primeiro, com a marca visível
   `↪ parte sobre "X" recolhida (N caracteres)`;
2. se ainda não couber, ficam as mensagens **mais recentes** e as mais antigas
   vão para a página seguinte — dito no cabeçalho, com o comando que abre;
3. **a parte do próprio assunto nunca é recolhida.** Uma mensagem cuja parte do
   assunto sozinha passa do teto aparece inteira mesmo assim.

POR QUE É DETERMINÍSTICO
------------------------
Como o dossiê (`dossie_assunto.py`): é SQL e formatação de texto. Nada aqui é
gerado por modelo, então nada aqui pode inventar uma fala.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from bot.lucien import assuntos

logger = logging.getLogger("kobe.lucien.conversa_assunto")

TETO_CHARS = 12000


@dataclass
class MsgDoAssunto:
    seq: int
    role: str
    audio: bool
    created_at: object
    content: str
    principal: Optional[str]
    partes: dict[str, list[list[int]]] = field(default_factory=dict)


def familia(cx, sid: str) -> list[str]:
    """O assunto, todos os que foram fundidos nele (em qualquer número de saltos)
    e — F7 (014) — os subassuntos dele: o tema traz as conversas que moram nele.
    Um nível só, pela trava do banco; as fusões das conversas vêm junto."""
    cur = cx.cursor()
    cur.execute(
        "WITH RECURSIVE f(id) AS (SELECT %s::uuid UNION"
        "  SELECT s.id FROM lucien_subjects s JOIN f"
        "      ON s.merged_into = f.id OR s.parent_id = f.id)"
        " SELECT id FROM f", (sid,))
    return [str(r["id"]) for r in cur.fetchall()]


def subassuntos(cx, sid: str) -> list[dict]:
    """As conversas (subassuntos) dentro do assunto, as mais recentes primeiro."""
    cur = cx.cursor()
    cur.execute(
        "SELECT s.id, s.title, s.last_touched_at,"
        "       (SELECT COUNT(*) FROM message_subjects ms WHERE ms.subject_id = s.id) AS n"
        "  FROM lucien_subjects s WHERE s.parent_id = %s AND s.status = 'ativo'"
        " ORDER BY s.last_touched_at DESC", (sid,))
    return cur.fetchall()


def mensagens(cx, sid: str) -> list[MsgDoAssunto]:
    """As mensagens marcadas com o assunto (ou com quem foi fundido nele), em
    ordem, cada uma com TODAS as suas marcas — as do assunto e as dos outros."""
    ids = familia(cx, sid)
    cur = cx.cursor()
    cur.execute(
        "SELECT m.id AS message_id, m.seq, m.role, m.audio_transcribed AS audio,"
        "       m.created_at, m.content, ms.subject_id, ms.role AS papel, ms.parts"
        "  FROM messages m JOIN message_subjects ms ON ms.message_id = m.id"
        " WHERE m.id IN (SELECT message_id FROM message_subjects"
        "                 WHERE subject_id = ANY(%s::uuid[]))"
        " ORDER BY m.seq", (ids,))
    # Quem é da família (fundido nele, ou subassunto dele) conta como o próprio.
    fusao: dict[str, str] = {i: sid for i in ids}
    por_msg: dict[str, MsgDoAssunto] = {}
    for r in cur.fetchall():
        bruto = str(r["subject_id"])
        if bruto not in fusao:
            fusao[bruto] = assuntos._seguir_fusao(cx.cursor(), bruto)
        alvo = fusao[bruto]
        m = por_msg.setdefault(str(r["message_id"]), MsgDoAssunto(
            seq=int(r["seq"]), role=r["role"], audio=bool(r["audio"]),
            created_at=r["created_at"], content=r["content"] or "", principal=None))
        m.partes.setdefault(alvo, []).extend([list(x) for x in (r["parts"] or [])])
        if r["papel"] == "principal":
            m.principal = alvo
    return sorted(por_msg.values(), key=lambda m: m.seq)


def titulos(cx, ids) -> dict[str, str]:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    cur = cx.cursor()
    cur.execute("SELECT id, title FROM lucien_subjects WHERE id = ANY(%s::uuid[])", (ids,))
    return {str(r["id"]): r["title"] for r in cur.fetchall()}


def _segmentos(m: MsgDoAssunto, sid: str) -> list[tuple[int, int, frozenset]]:
    """A mensagem inteira em segmentos contíguos, cada um com os assuntos dele.
    O que nenhuma marca cobre (o espaço entre partes) vai junto do vizinho."""
    cortes = {0, len(m.content)}
    for ivs in m.partes.values():
        for a, b in ivs:
            cortes.update((max(0, a), min(len(m.content), b)))
    pts = sorted(cortes)
    segs: list[tuple[int, int, frozenset]] = []
    for a, b in zip(pts, pts[1:]):
        if b <= a:
            continue
        donos = frozenset(s for s, ivs in m.partes.items()
                          if any(x <= a and b <= y for x, y in ivs))
        if not donos and segs:                     # espaço entre partes: cola no anterior
            x, _, d = segs[-1]
            segs[-1] = (x, b, d)
            continue
        if segs and segs[-1][2] == donos:
            segs[-1] = (segs[-1][0], b, donos)
        else:
            segs.append((a, b, donos))
    return segs


def _data(d) -> str:
    from bot.lucien.dossie_assunto import _data as fmt

    return fmt(d, "%d/%m/%Y %H:%M")


def renderizar(m: MsgDoAssunto, sid: str, nomes: dict[str, str], *, recolher: bool) -> list[str]:
    """Uma mensagem, inteira, sob o assunto `sid`. Devolve as linhas."""
    quem = ("tu" if m.role == "user" else "agente") + (", áudio" if m.audio else "")
    outros = [nomes.get(s, "?") for s in m.partes if s != sid]
    cab = f"**#{m.seq} · {_data(m.created_at)} · {quem}**"
    if outros:
        cab += " — também em: " + " · ".join(f'"{t}"' for t in outros)
    linhas = [cab]
    for a, b, donos in _segmentos(m, sid):
        texto = m.content[a:b].strip()
        if not texto:
            continue
        if sid in donos or not donos:
            linhas.append(texto)
            continue
        nome = " · ".join(f'"{nomes.get(s, "?")}"' for s in sorted(donos))
        if recolher:
            linhas.append(f"↪ _parte sobre {nome} recolhida ({len(texto)} caracteres) — "
                          "peça a conversa com `--inteira` para abrir_")
        else:
            linhas.append(f"↪ _a parte abaixo é do assunto {nome} — está aqui porque foi "
                          "dita nesta mesma mensagem_")
            linhas += ["┆ " + l if l.strip() else "┆" for l in texto.splitlines()]
    return linhas


def montar(cx, sid: str, *, teto: int = TETO_CHARS, pagina: int = 1,
           inteira: bool = False, titulo_para_comando: str = "") -> str:
    """A conversa do assunto dentro do teto, pela ordem do que se recolhe."""
    msgs = mensagens(cx, sid)
    if not msgs:
        return ""
    nomes = titulos(cx, {s for m in msgs for s in m.partes} | {sid})
    total = len(msgs)

    def bloco(m, rec):
        return "\n".join(renderizar(m, sid, nomes, recolher=rec))

    plenos = [bloco(m, False) for m in msgs]
    recolhidos = plenos if inteira else [bloco(m, True) for m in msgs]
    usados = plenos if (inteira or sum(len(x) + 2 for x in plenos) <= teto) else recolhidos
    recolheu = usados is not plenos and any(a != b for a, b in zip(plenos, recolhidos))

    # Páginas: da mais recente para trás, cada uma até o teto (ao menos 1 mensagem).
    paginas: list[list[int]] = []
    i = total - 1
    while i >= 0:
        atual, tam = [], 0
        while i >= 0 and (not atual or tam + len(usados[i]) + 2 <= teto):
            atual.append(i)
            tam += len(usados[i]) + 2
            i -= 1
        paginas.append(sorted(atual))
    pagina = max(1, min(pagina, len(paginas)))
    mostrados = paginas[pagina - 1]

    cmd = f'kobe-assunto conversa "{titulo_para_comando or nomes.get(sid, sid)}"'
    cab = [f"_{total} mensagem(ns) nesta conversa; mostrando {len(mostrados)}"
           f" (página {pagina} de {len(paginas)}, da mais recente para trás)._"]
    if len(paginas) > pagina:
        cab.append(f"_as mais antigas estão na página {pagina + 1}: `{cmd} --pagina {pagina + 1}`._")
    if recolheu:
        cab.append(f"_para caber, as partes de OUTRO assunto foram recolhidas (a parte deste "
                   f"assunto nunca é): `{cmd} --inteira` mostra tudo._")
    return "\n".join(cab) + "\n\n" + "\n\n".join(usados[k] for k in mostrados)
