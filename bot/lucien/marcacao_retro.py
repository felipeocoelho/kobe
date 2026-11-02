"""A marcação retroativa — dar assunto às mensagens que já estão gravadas (F7).

POR QUE EXISTE
--------------
O curador marca as mensagens daqui para a frente (`marcacao.py`). A dor do
operador é RETOMAR O PASSADO, e o passado tem ~4 mil mensagens sem marcação.
Aprovado em 31/10/2026: *"marcar o histórico inteiro fica para a sala que
codar, junto com o agrupamento retroativo"*.

COMO
----
- As mensagens sem marcação, por tópico, em TRECHOS DE CONVERSA (6 h sem
  mensagem fecham um trecho). O trecho inteiro vai para o mesmo lote — é a
  unidade que os leitores da rodada 2 marcaram (e a que tem 98–99% de
  concordância com lista fixa).
- Vários trechos por chamada, até `TETO_CHARS_LOTE`. Medido na rodada 2: cada
  chamada da CLI carrega ~32,5 mil tokens fixos de cabeçalho, então juntar
  conversas por chamada é o que mantém a cota da carga inteira razoável.
- Lotes SEQUENCIAIS, com o vocabulário acumulado: é o vocabulário que impede o
  lote 30 de recriar o que o lote 2 criou. Assunto novo passa pela T11.
- O que o modelo devolve é interpretado e gravado pelo MESMO código do curador
  (`marcacao.interpretar` e `marcacao.gravar`): uma regra só para o que vale
  como marcação.

O CAMINHO DE VOLTA
------------------
Tudo é gravado com o `run_id` de uma rodada `mode='marcacao'`. `desfazer(run_id)`
tira as marcações daquela rodada e arquiva os assuntos que ela criou e que
ficaram sem afirmação e sem mensagem. O texto das mensagens nunca é tocado.

RETOMÁVEL
---------
Cada lote é uma transação. Parou no meio (cota, rede): `--retomar <run_id>`
continua — mensagem já marcada não é marcada de novo, e as que o modelo julgou
"aparte" ficam lembradas num arquivo de estado da rodada.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

from bot.lucien import assuntos, brain, marcacao, store
from bot.lucien.models import Lote, Mensagem

logger = logging.getLogger("kobe.lucien.marcacao_retro")

# Medido no laboratório em 31/10/2026, sobre as 4.042 mensagens sem marcação:
# com 80 mil caracteres por lote e 3 mil por resposta do agente seriam 83 lotes, e
# cada um carrega ~32,5 mil tokens fixos de cabeçalho da CLI (~79,5 mil tokens no
# primeiro lote real). Com 150 mil e 1.500 (o corte com que a rodada 2 mediu 98–99%
# de concordância) são 30 lotes — metade da cota, o mesmo trabalho.
TETO_CHARS_LOTE = 150000      # conteúdo de mensagens por chamada (~8 trechos de conversa)
CORTE_TRECHO = timedelta(hours=6)
MOSTRAR_AGENTE = 1500         # quanto de cada resposta do agente se mostra (o resto herda o principal)

SYSTEM = (
    "Você é LUCIEN, o arquivista da memória do Kobe. Agora você MARCA mensagens já "
    "gravadas: diz a que assunto (conversa) cada uma pertence, e que partes dela são "
    "de que assunto. Você NÃO conversa, NÃO usa ferramentas e NÃO escreve nada além "
    "do JSON pedido."
)

REGUA = """\
O QUE É UM ASSUNTO — a régua é do operador:

Assunto é o que, num aplicativo de chat, seria uma conversa da lista de chats:
uma conversa sobre UMA coisa, que ele vai querer retomar depois ("retoma o
assunto X"). **Tamanho de DOCUMENTO, não tamanho de TÓPICO.**
  bom:  "Highlander v3 — F5, revisão semanal da memória" · "Coder — isolamento de sessões por worktree"
  ruim, grande demais: "Manutenção do Kobe" · "Melhorias do Coder" · "Memória" · "Olimpo"
  ruim, pequeno demais: "Timeout do kobe-reflect subido para 90s"
REUSE a lista abaixo; crie assunto novo só quando nenhum serve. Quando o assunto
é parte de um projeto com nome, ponha o nome no começo do título.

Duas listas: SUBASSUNTOS (conversas, C…) — é com elas que se marca; e ASSUNTOS
(temas largos, T…) — a pasta, só para o "dentro_de" de uma conversa nova.
"""

CONTRATO = """\
Responda SOMENTE com um objeto JSON, sem texto antes nem depois:
{
  "assuntos_novos": [{"id": "N1", "titulo": "…", "descricao": "uma frase",
                      "dentro_de": "T2 ou null", "por_que": "…"}],
  "mensagens": {
    "1234": {"principal": "C3"},
    "1235": {"principal": "C3", "partes": {"1": ["C3"], "2": ["N1"], "3": ["C3", "N1"]}},
    "1236": {"principal": null}
  }
}
- TODA mensagem mostrada aparece em "mensagens".
- Apelido que não é conversa (C…) nem está em "assuntos_novos" é descartado.
"""


@dataclass
class Relatorio:
    run_id: str = ""
    lotes: int = 0
    chamadas: int = 0
    mensagens_vistas: int = 0
    marcadas: int = 0
    apartes: int = 0
    invalidos: int = 0
    principal_suprido: int = 0
    criados: int = 0
    evitados: int = 0
    uso: dict = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0,
                                                "cache_creation": 0})
    erro: Optional[str] = None

    def resumo(self) -> str:
        return (f"marcação {self.run_id} · {self.lotes} lote(s), {self.chamadas} chamada(s) · "
                f"{self.mensagens_vistas} mensagem(ns) vistas, {self.marcadas} marcada(s), "
                f"{self.apartes} aparte(s) · {self.criados} assunto(s) criado(s), "
                f"{self.evitados} duplicata(s) evitada(s) · {self.invalidos} apelido(s)/parte(s) "
                f"inválido(s), {self.principal_suprido} principal(is) suprido(s) · "
                f"tokens: {self.uso}" + (f" · PAROU: {self.erro}" if self.erro else ""))


# ── Estado da rodada (só o que não está no banco: os apartes) ─────────────


def _estado_path(kobe_home: str, run_id: str) -> Path:
    d = Path(kobe_home) / "user-data" / "lucien"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"marcacao-{run_id}.json"


def _ler_estado(kobe_home: str, run_id: str) -> dict:
    p = _estado_path(kobe_home, run_id)
    return json.loads(p.read_text()) if p.exists() else {"vistas": []}


def _gravar_estado(kobe_home: str, run_id: str, estado: dict) -> None:
    p = _estado_path(kobe_home, run_id)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado))
    os.replace(tmp, p)


# ── As pendentes, em trechos de conversa ──────────────────────────────────


def trechos_pendentes(cx, *, ignorar: set[int], topic_id: Optional[str] = None,
                      de_seq: Optional[int] = None, ate_seq: Optional[int] = None
                      ) -> list[tuple[str, str, list[Mensagem]]]:
    """`[(topic_id, nome, mensagens)]` — cada item é um trecho de conversa (6 h
    sem mensagem fecham o trecho) com as mensagens AINDA SEM marcação."""
    cur = cx.cursor()
    cur.execute(
        "SELECT m.id, m.seq, m.topic_id, COALESCE(t.current_name, '?') AS topico, m.role,"
        "       m.content, m.created_at, m.audio_transcribed"
        "  FROM messages m JOIN topics t ON t.id = m.topic_id"
        " WHERE m.role IN ('user', 'assistant')"
        "   AND NOT EXISTS (SELECT 1 FROM message_subjects ms WHERE ms.message_id = m.id)"
        "   AND (%s::uuid IS NULL OR m.topic_id = %s::uuid)"
        "   AND (%s::bigint IS NULL OR m.seq >= %s::bigint)"
        "   AND (%s::bigint IS NULL OR m.seq <= %s::bigint)"
        " ORDER BY m.topic_id, m.created_at, m.seq",
        (topic_id, topic_id, de_seq, de_seq, ate_seq, ate_seq))
    trechos: list[tuple[str, str, list[Mensagem]]] = []
    ultimo = None
    for r in cur.fetchall():
        if int(r["seq"]) in ignorar:
            continue
        corpo = r["content"] or ""
        marcavel = not (r["role"] == "assistant" and store._MARCA_REVISAO in corpo)
        if not marcavel or not corpo.strip():
            continue
        msg = Mensagem(seq=int(r["seq"]), id=str(r["id"]), role=r["role"],
                       created_at=r["created_at"], content=corpo,
                       audio=bool(r["audio_transcribed"]))
        tid = str(r["topic_id"])
        novo = (not trechos or trechos[-1][0] != tid or ultimo is None
                or r["created_at"] - ultimo > CORTE_TRECHO)
        if novo:
            trechos.append((tid, r["topico"], []))
        trechos[-1][2].append(msg)
        ultimo = r["created_at"]
    return trechos


def _tam(m: Mensagem) -> int:
    return len(m.content) if m.role == "user" else min(len(m.content), MOSTRAR_AGENTE)


def lotes(trechos) -> list[list[tuple[str, str, list[Mensagem]]]]:
    """Trechos inteiros juntos até o teto; um trecho maior que o teto vira lotes
    próprios, partido entre mensagens (nunca no meio de uma)."""
    saida, atual, chars = [], [], 0
    for tid, nome, msgs in trechos:
        tam = sum(_tam(m) for m in msgs)
        if tam > TETO_CHARS_LOTE:
            if atual:
                saida.append(atual)
                atual, chars = [], 0
            pedaco, c = [], 0
            for m in msgs:
                if pedaco and c + _tam(m) > TETO_CHARS_LOTE:
                    saida.append([(tid, nome, pedaco)])
                    pedaco, c = [], 0
                pedaco.append(m)
                c += _tam(m)
            if pedaco:
                saida.append([(tid, nome, pedaco)])
            continue
        if atual and chars + tam > TETO_CHARS_LOTE:
            saida.append(atual)
            atual, chars = [], 0
        atual.append((tid, nome, msgs))
        chars += tam
    if atual:
        saida.append(atual)
    return saida


# ── O prompt ──────────────────────────────────────────────────────────────


def _bloco(m: Mensagem) -> list[str]:
    linhas = marcacao.bloco_da_mensagem(m.seq, m.content)
    if m.role == "assistant":
        mostrados, tam = [], 0
        for i, l in enumerate(linhas):
            if mostrados and tam + len(l) > MOSTRAR_AGENTE:
                linhas = mostrados + [f"[{m.seq}.{i + 1}–{m.seq}.{len(linhas)}] (resto da resposta "
                                      "não mostrado: herda o assunto principal)"]
                break
            mostrados.append(l)
            tam += len(l)
    return linhas


def montar_prompt(voc: dict[str, dict], lote) -> str:
    linhas = ["Marque as mensagens abaixo por CONVERSA (subassunto).", "", REGUA]
    if "C" in {ap[:1] for ap in voc} or "T" in {ap[:1] for ap in voc}:
        linhas += marcacao.bloco_vocabulario(
            {ap: a for ap, a in voc.items() if ap.startswith("C")},
            {ap: a for ap, a in voc.items() if ap.startswith("T")})
    else:
        linhas += ["", "─" * 60, "ASSUNTOS QUE JÁ EXISTEM (reuse)", "─" * 60]
        linhas += [f"{ap}  {a['title']}" + (f" — {a['description']}" if a.get("description") else "")
                   for ap, a in voc.items()] or ["(nenhum ainda)"]
    for tid, nome, msgs in lote:
        linhas += ["", "─" * 60,
                   f"TRECHO DE CONVERSA — tópico {nome}, {msgs[0].created_at:%d/%m/%Y}", "─" * 60]
        for m in msgs:
            marca = " (transcrito de áudio)" if m.audio else ""
            linhas.append(f"#{m.seq} · {m.created_at:%d/%m/%Y %H:%M} · {m.quem}{marca}:")
            linhas += _bloco(m)
            linhas.append("")
    instr = marcacao.INSTRUCAO.split("E, em cada afirmação")[0]   # aqui não há afirmações
    linhas += ["", instr, "", CONTRATO]
    return "\n".join(linhas)


def vocabulario(cx) -> dict[str, dict]:
    """Os assuntos ativos. Com a 014: conversas como C…, temas como T…; antes
    dela, tudo como A…."""
    from bot.lucien import assuntos

    cur = cx.cursor()
    if not assuntos.tem_natureza(cx):
        cur.execute("SELECT id, title, description, last_touched_at FROM lucien_subjects"
                    " WHERE status = 'ativo' ORDER BY title")
        return {f"A{i}": r for i, r in enumerate(cur.fetchall(), start=1)}
    voc = {}
    for kind, pre in (("conversa", "C"), ("tema", "T")):
        cur.execute("SELECT id, title, description, last_touched_at, kind FROM lucien_subjects"
                    " WHERE status = 'ativo' AND kind = %s ORDER BY title", (kind,))
        voc.update({f"{pre}{i}": r for i, r in enumerate(cur.fetchall(), start=1)})
    return voc


# ── A rodada ──────────────────────────────────────────────────────────────

Pensador = Callable[[str, str], str]


def _pensador_padrao(kobe_home: str) -> Pensador:
    def pensar(prompt: str, system: str) -> str:
        # O modelo do curador, de `LUCIEN_MODEL` — o Kobe não fixa modelo.
        return brain.chamar(prompt, kobe_home=kobe_home, reconstrucao=False,
                            system=system, timeout=900)
    return pensar


def _uso(bruto: str, rel: Relatorio) -> None:
    try:
        u = json.loads(bruto).get("usage") or {}
    except (ValueError, AttributeError):
        return
    rel.uso["input"] += int(u.get("input_tokens") or 0)
    rel.uso["output"] += int(u.get("output_tokens") or 0)
    rel.uso["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
    rel.uso["cache_creation"] += int(u.get("cache_creation_input_tokens") or 0)


def rodar(cx, *, kobe_home: str, retomar: Optional[str] = None,
          limite_lotes: Optional[int] = None, topic_id: Optional[str] = None,
          de_seq: Optional[int] = None, ate_seq: Optional[int] = None,
          pensador: Optional[Pensador] = None, log: Callable[[str], None] = print) -> Relatorio:
    """A marcação retroativa. Commita lote a lote; para na primeira falha."""
    pensar = pensador or _pensador_padrao(kobe_home)
    rel = Relatorio()
    if retomar:
        rel.run_id = retomar
    else:
        rel.run_id = store.abrir_rodada(cx, mode="marcacao", topic_id=topic_id, lote=None,
                                        model="(padrão)")
        cx.commit()
    estado = _ler_estado(kobe_home, rel.run_id)
    vistas = set(estado["vistas"])
    trs = trechos_pendentes(cx, ignorar=vistas, topic_id=topic_id, de_seq=de_seq, ate_seq=ate_seq)
    ls = lotes(trs)
    total = sum(len(m) for _, _, m in trs)
    log(f"marcação {rel.run_id}: {total} mensagem(ns) sem marcação, {len(trs)} trecho(s), "
        f"{len(ls)} lote(s)")
    for i, lote in enumerate(ls[:limite_lotes] if limite_lotes else ls, start=1):
        voc = vocabulario(cx)
        try:
            bruto = pensar(montar_prompt(voc, lote), SYSTEM)
            rel.chamadas += 1
            _uso(bruto, rel)
            resposta = brain.extrair_json(bruto, reconhece=lambda d: "mensagens" in d)
        except Exception as exc:  # noqa: BLE001 — para, diz onde, e é retomável
            cx.rollback()
            rel.erro = f"lote {i}/{len(ls)}: {exc}"
            break
        try:
            n_vistas = _aplicar(cx, lote, voc, resposta, rel)
            cx.commit()
        except Exception as exc:  # noqa: BLE001
            cx.rollback()
            rel.erro = f"lote {i}/{len(ls)} não gravou: {exc}"
            break
        rel.lotes += 1
        estado["vistas"] = sorted(set(estado["vistas"]) | n_vistas)
        _gravar_estado(kobe_home, rel.run_id, estado)
        log(f"  lote {i}/{len(ls)}: {sum(len(m) for _, _, m in lote)} mensagem(ns) · "
            f"{rel.marcadas} marcadas até aqui · vocabulário com {len(vocabulario(cx))}")

    cx.cursor().execute(
        "UPDATE lucien_runs SET finished_at = NOW(), ok = %s, error = %s,"
        " messages_seen = %s, messages_marked = %s,"
        " subjects_created = subjects_created + %s, subjects_avoided = subjects_avoided + %s"
        " WHERE id = %s",
        (rel.erro is None, rel.erro, rel.mensagens_vistas, rel.marcadas,
         rel.criados, rel.evitados, rel.run_id))
    cx.commit()
    return rel


def _aplicar(cx, lote, voc: dict, resposta: dict, rel: Relatorio) -> set[int]:
    """Um lote: os apelidos (A… do vocabulário, N… novos), a interpretação e a
    gravação — pelo mesmo código do curador."""
    todas = [m for _, _, msgs in lote for m in msgs]
    # Um Lote só para reusar `interpretar`: o vocabulário inteiro é a lista.
    fake = Lote(topic_id=lote[0][0], topico_nome=lote[0][1], mensagens=todas, assuntos=voc)
    from bot.lucien import assuntos
    natureza = assuntos.tem_natureza(cx)
    res = marcacao.ResultadoMarcacao()
    mapa = marcacao.resolver_apelidos(cx, fake, resposta.get("assuntos_novos") or [],
                                      run_id=rel.run_id, quando=todas[0].created_at, res=res,
                                      criado_por="agrupamento")
    marcacao.interpretar(resposta.get("mensagens") or {}, fake, mapa, res,
                         so_conversas=natureza)
    # Em lote de vários tópicos, o tópico de casa do assunto novo é o da primeira
    # mensagem dele; `home_topic_id` só registra onde nasceu, não restringe nada.
    marcacao.gravar(cx, fake, res, run_id=rel.run_id, criado_por="agrupamento")
    rel.mensagens_vistas += len(todas)
    rel.marcadas += res.marcadas
    rel.apartes += sum(1 for m in todas if m.seq not in res.marcas)
    rel.invalidos += res.invalidos
    rel.principal_suprido += res.principal_suprido
    rel.criados += res.criados
    rel.evitados += res.evitados
    return {m.seq for m in todas}


def desfazer(cx, run_id: str, *, actor: str = "operador") -> dict:
    """O caminho de volta de uma marcação retroativa inteira. Não commita.

    Tira as marcações daquela rodada (o texto das mensagens nunca foi tocado) e
    arquiva os assuntos que ela criou e que ficaram sem afirmação e sem mensagem.
    Arquivar é status: o histórico guarda o que houve."""
    cur = cx.cursor()
    cur.execute("DELETE FROM message_subjects WHERE run_id = %s", (run_id,))
    soltas = cur.rowcount
    cur.execute(
        "UPDATE lucien_subjects s SET status = 'arquivado', updated_at = NOW()"
        " WHERE s.run_id = %s AND s.status = 'ativo'"
        "   AND NOT EXISTS (SELECT 1 FROM lucien_claims c WHERE c.subject_id = s.id)"
        "   AND NOT EXISTS (SELECT 1 FROM message_subjects ms WHERE ms.subject_id = s.id)"
        " RETURNING id", (run_id,))
    arquivados = [str(r["id"]) for r in cur.fetchall()]
    for sid in arquivados:
        assuntos.evento(cx, sid, "arquivado", None, {"desfeita_a_marcacao": str(run_id)},
                        actor=actor)
    return {"marcacoes_desfeitas": soltas, "assuntos_arquivados": len(arquivados)}
