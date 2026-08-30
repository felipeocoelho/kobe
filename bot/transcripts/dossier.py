"""O dossiê por sala — o que decidiu, o que ficou aberto, o que produziu.

Regenerado **por acúmulo**, nunca por "fechar a sala" (decisão E3 do briefing):
fechar sala é rótulo de estado, não evento de sistema. O critério de pronto da
fase é explícito quanto a isso — o dossiê tem que ser legível **antes** de a sala
fechar, e é por isso que ele carrega `status: em andamento | concluída`.

──────────────────────────────────────────────────────────────────────────
POR QUE ELE É DETERMINÍSTICO, E POR QUE ISSO NÃO É PREGUIÇA
──────────────────────────────────────────────────────────────────────────

A tentação óbvia é pedir a um modelo que leia o transcript e resuma. Aqui isso
seria errado por três motivos, e o terceiro é o que decide:

1. **Custo.** O §9.6 do briefing já marca a F1 como a fase mais cara do projeto,
   e o recurso escasso desta campanha é cota de assinatura. Um resumo por sala,
   regenerado a cada acúmulo, é uma conta que cresce sozinha.
2. **Escopo.** Destilação com julgamento é a **F3** (LUCIEN). Antecipá-la aqui
   faria a F1 depender de um LLM pra entregar um artefato que o critério (d) só
   exige que seja **legível**.
3. **Procedência — e este é o que importa.** O que um modelo escreveria sobre a
   sala é *texto gerado*; o que está aqui é *o que a sala de fato disse e fez*.
   A dor que esta missão inteira existe pra curar é justamente a de tratar texto
   plausível como fato. Um dossiê que cita as próprias mensagens da sala não tem
   como inventar uma decisão que não houve.

E as fontes determinísticas são melhores do que parecem. As mensagens que a sala
manda pelo `kobe-notify` **são**, por construção do rito do Coder, exatamente os
marcos, bloqueios e conclusões — escritas pela própria sala, no momento em que
aconteceram. As caixas não marcadas do plano **são** literalmente o que ficou
aberto. Os arquivos escritos e os commits **são** o que ela produziu. Não é um
resumo pior: é uma fonte diferente, e mais confiável.

O gancho pra F3 fica pronto (`TRANSCRIPT_DOSSIER_LLM`), desligado.

──────────────────────────────────────────────────────────────────────────
UMA HONESTIDADE SOBRE ROTULAGEM
──────────────────────────────────────────────────────────────────────────

Numa sala do Coder, tanto as mensagens do operador quanto os prompts injetados
pelo sistema chegam ao transcript como linhas `type=user`. **Não dá pra separar
os dois com confiança**, então a seção correspondente se chama "o que entrou na
sala" e não "o que o operador disse". Rotular texto de máquina como fala do
operador seria criar exatamente o tipo de falso que a F3 vai ter de desfazer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

# Quanto de cada texto entra no dossiê. Ele é pra ser lido por gente (e, na F2,
# indexado), não pra ser uma segunda cópia do transcript — a cópia integral já
# está ao lado, no `.jsonl`.
_MAX_TEXTO = 1500
_MAX_PEDIDO = 2500


def dossier_path_for(transcript: Path) -> Path:
    """`<...>/<session_id>.jsonl` → `<...>/<session_id>.dossier.md`."""
    return transcript.with_suffix("").with_suffix(".dossier.md") \
        if transcript.suffix == ".jsonl" \
        else transcript.with_name(transcript.name + ".dossier.md")


def _corta(texto: str, limite: int = _MAX_TEXTO) -> str:
    texto = (texto or "").strip()
    if len(texto) <= limite:
        return texto
    return texto[:limite].rstrip() + f" […mais {len(texto) - limite} caracteres]"


# --- extração do transcript ---------------------------------------------

_RE_NOTIFY = re.compile(r"kobe-notify\s+(.*)", re.S)
_RE_COMMIT_MSG = re.compile(r"git\s+commit\b[^\n]*?-m\s+(['\"])(.*?)\1", re.S)


def _texto_de_notify(comando: str) -> Optional[str]:
    """O texto que a sala mandou pro operador, extraído da chamada de shell.

    Best-effort por natureza: o comando é uma linha de shell, e o texto pode vir
    entre aspas simples, duplas ou num heredoc. Quando o desembrulho não é
    seguro, devolve o comando cortado em vez de arriscar um texto errado —
    mostrar o comando cru é feio, inventar a mensagem é pior.
    """
    m = _RE_NOTIFY.search(comando or "")
    if not m:
        return None
    resto = m.group(1).strip()
    if not resto:
        return None
    if resto[0] in "\"'":
        aspa = resto[0]
        fim = resto.rfind(aspa)
        if fim > 0:
            return resto[1:fim]
    # sem aspas reconhecíveis: corta no primeiro pipe/redirecionamento
    return re.split(r"\s+[|>2]", resto)[0].strip()


@dataclass
class Leitura:
    """O que se extraiu de um transcript. Só fato, nada interpretado."""

    linhas: int = 0
    linhas_ilegiveis: int = 0
    bytes: int = 0
    primeiro_ts: Optional[str] = None
    ultimo_ts: Optional[str] = None
    cwd: Optional[str] = None
    versao: Optional[str] = None
    pedidos: list[str] = field(default_factory=list)
    marcos: list[str] = field(default_factory=list)
    arquivos_escritos: list[str] = field(default_factory=list)
    comandos_git: list[str] = field(default_factory=list)
    ferramentas: dict[str, int] = field(default_factory=dict)
    turnos_assistente: int = 0
    blocos_thinking: int = 0


def ler_transcript(path: Path) -> Leitura:
    """Percorre o `.jsonl` uma vez, tolerando linha ruim.

    **Nunca levanta por conteúdo.** Um transcript é escrito por outro processo e
    pode estar sendo escrito agora; uma linha ilegível é contada e seguida, e o
    dossiê sai assim mesmo. Deixar de gerar o dossiê por causa de uma linha
    torta seria trocar um artefato quase completo por nenhum.
    """
    leitura = Leitura()
    if not path.is_file():
        return leitura
    leitura.bytes = path.stat().st_size

    vistos_arquivos: set[str] = set()
    with path.open("rb") as fh:
        for bruto in fh:
            leitura.linhas += 1
            try:
                obj = json.loads(bruto)
            except Exception:  # noqa: BLE001
                leitura.linhas_ilegiveis += 1
                continue
            if not isinstance(obj, dict):
                leitura.linhas_ilegiveis += 1
                continue

            ts = obj.get("timestamp")
            if isinstance(ts, str):
                leitura.primeiro_ts = leitura.primeiro_ts or ts
                leitura.ultimo_ts = ts
            leitura.cwd = leitura.cwd or obj.get("cwd")
            leitura.versao = leitura.versao or obj.get("version")

            tipo = obj.get("type")
            msg = obj.get("message") or {}
            conteudo = msg.get("content")

            if tipo == "user":
                textos = []
                if isinstance(conteudo, str):
                    textos.append(conteudo)
                elif isinstance(conteudo, list):
                    for b in conteudo:
                        if isinstance(b, dict) and b.get("type") == "text":
                            textos.append(b.get("text") or "")
                for t in textos:
                    limpo = re.sub(r"<system-reminder>.*?</system-reminder>", "",
                                   t, flags=re.S).strip()
                    # `<local-command-*>` e blocos de lembrete não são pedido.
                    if limpo and not limpo.startswith("<"):
                        leitura.pedidos.append(_corta(limpo, _MAX_PEDIDO))

            elif tipo == "assistant":
                leitura.turnos_assistente += 1
                if isinstance(conteudo, list):
                    for b in conteudo:
                        if not isinstance(b, dict):
                            continue
                        bt = b.get("type")
                        if bt == "thinking":
                            leitura.blocos_thinking += 1
                        elif bt == "tool_use":
                            nome = b.get("name") or "?"
                            leitura.ferramentas[nome] = \
                                leitura.ferramentas.get(nome, 0) + 1
                            entrada = b.get("input") or {}
                            if not isinstance(entrada, dict):
                                continue
                            caminho = entrada.get("file_path")
                            if nome in ("Write", "Edit", "NotebookEdit") and caminho:
                                if caminho not in vistos_arquivos:
                                    vistos_arquivos.add(caminho)
                                    leitura.arquivos_escritos.append(caminho)
                            comando = entrada.get("command")
                            if nome == "Bash" and isinstance(comando, str):
                                texto = _texto_de_notify(comando)
                                if texto:
                                    leitura.marcos.append(_corta(texto))
                                for m in _RE_COMMIT_MSG.finditer(comando):
                                    leitura.comandos_git.append(
                                        _corta(m.group(2).strip().splitlines()[0], 200))
    return leitura


# --- o que ficou aberto -------------------------------------------------

_RE_CAIXA = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")


def pendencias_do_plano(cwd: Optional[str]) -> tuple[Optional[str], list[str], int]:
    """As caixas NÃO marcadas do `.local/plano-*.md` da sala.

    É a fonte mais literal que existe pra "o que ficou aberto": não é inferência
    sobre o que a sala fez, é a lista que ela própria escreveu e foi marcando.
    Se houver mais de um plano, vale o mais recente.
    """
    if not cwd:
        return None, [], 0
    base = Path(cwd) / ".local"
    if not base.is_dir():
        return None, [], 0
    planos = sorted(base.glob("plano-*.md"), key=lambda p: p.stat().st_mtime)
    if not planos:
        return None, [], 0
    plano = planos[-1]
    abertas, total = [], 0
    try:
        for linha in plano.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _RE_CAIXA.match(linha)
            if not m:
                continue
            total += 1
            if m.group(1) == " ":
                abertas.append(m.group(2))
    except OSError:
        return str(plano), [], 0
    return str(plano), abertas, total


# --- a renderização -----------------------------------------------------

def _bloco(titulo: str, itens: Iterable[str], vazio: str) -> list[str]:
    itens = list(itens)
    linhas = [f"## {titulo}", ""]
    if not itens:
        linhas += [f"_{vazio}_", ""]
        return linhas
    for i in itens:
        primeira, *resto = i.splitlines()
        linhas.append(f"- {primeira}")
        for r in resto:
            linhas.append(f"  {r}")
    linhas.append("")
    return linhas


def render(
    *,
    session_id: str,
    transcript: Path,
    leitura: Leitura,
    catalogo: Optional[dict] = None,
    artefatos: Optional[list[dict]] = None,
    gerado_em: Optional[str] = None,
) -> str:
    """O dossiê em Markdown. Legível a qualquer momento da vida da sala."""
    cat = catalogo or {}
    status_bruto = (cat.get("status") or "").strip()
    status_legivel = {
        "running": "em andamento",
        "idle": "em andamento",
        "closed": "concluída",
        "dead": "concluída",
    }.get(status_bruto, "em andamento")

    cwd = cat.get("cwd") or leitura.cwd
    plano, abertas, total_caixas = pendencias_do_plano(cwd)

    sistema = cat.get("system_name") or "(não catalogada)"
    subsistema = cat.get("subsystem_name") or "(nenhum)"

    L: list[str] = []
    L.append("---")
    L.append(f"session_id: {session_id}")
    L.append(f"status: {status_legivel}")
    L.append(f"sistema: {sistema}")
    L.append(f"subsistema: {subsistema}")
    if cat.get("kind"):
        L.append(f"tipo: {cat['kind']}")
    L.append(f"gerado_em: {gerado_em or datetime.now().astimezone().isoformat(timespec='seconds')}")
    L.append("---")
    L.append("")
    titulo = cat.get("title") or f"Sala {session_id[:8]}"
    L.append(f"# Dossiê — {titulo}")
    L.append("")
    L.append(
        "> Regenerado por acúmulo, a cada coleta. **Fechar a sala não é gatilho de "
        "nada** — este arquivo é legível enquanto ela trabalha, e a única coisa que "
        "muda quando ela fecha é o rótulo de status acima."
    )
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| transcript | `{transcript}` |")
    if cwd:
        L.append(f"| pasta de trabalho | `{cwd}` |")
    if cat.get("started_at"):
        L.append(f"| aberta em | {cat['started_at']} |")
    if leitura.primeiro_ts:
        L.append(f"| 1ª linha do transcript | {leitura.primeiro_ts} |")
    if leitura.ultimo_ts:
        L.append(f"| última linha | {leitura.ultimo_ts} |")
    # `f"{n:,}"` usa vírgula como separador de milhar; trocar por ponto exige
    # trocar SÓ os separadores, e não toda vírgula da linha — a primeira versão
    # fazia `.replace(",", ".")` na linha inteira e comia a vírgula antes de
    # "726 linhas", virando "1.678.468 bytes. 726 linhas".
    _bytes = f"{leitura.bytes:,}".replace(",", ".")
    L.append(f"| tamanho colhido | {_bytes} bytes, {leitura.linhas} linhas |")
    L.append(f"| turnos do assistente | {leitura.turnos_assistente} |")
    L.append(f"| blocos de raciocínio | {leitura.blocos_thinking} |")
    if leitura.linhas_ilegiveis:
        L.append(f"| linhas ilegíveis | {leitura.linhas_ilegiveis} |")
    L.append("")

    if cat.get("motivation"):
        L.append("## Por que a sala foi aberta")
        L.append("")
        L.append(_corta(cat["motivation"], _MAX_PEDIDO))
        L.append("")

    L += _bloco(
        "O que entrou na sala",
        leitura.pedidos,
        "nenhum pedido legível no transcript ainda",
    )
    if leitura.pedidos:
        L.append(
            "> Numa sala do Coder, tanto a fala do operador quanto os prompts "
            "injetados pelo sistema chegam ao transcript como linhas `user`, e não "
            "há como separá-los com confiança. Por isso a seção diz *o que entrou "
            "na sala*, e não *o que o operador disse*."
        )
        L.append("")

    L += _bloco(
        "Marcos e decisões, na voz da própria sala",
        leitura.marcos,
        "a sala ainda não emitiu nenhuma mensagem de progresso",
    )
    if leitura.marcos:
        L.append(
            "> São as mensagens que a sala mandou pelo `kobe-notify` — por "
            "construção do rito, exatamente os marcos, bloqueios e conclusões, "
            "escritas no momento em que aconteceram."
        )
        L.append("")

    L.append("## O que ficou aberto")
    L.append("")
    if plano:
        marcadas = total_caixas - len(abertas)
        L.append(f"Plano: `{plano}` — **{marcadas} de {total_caixas}** concluídos.")
        L.append("")
        if abertas:
            for item in abertas:
                L.append(f"- [ ] {item}")
        else:
            L.append("_Nenhum item em aberto no checklist._")
    else:
        L.append("_Nenhum `.local/plano-*.md` encontrado na pasta de trabalho._")
    L.append("")

    L.append("## O que produziu")
    L.append("")
    if leitura.comandos_git:
        L.append("**Commits (pela mensagem, como a sala os escreveu):**")
        L.append("")
        for c in leitura.comandos_git:
            L.append(f"- {c}")
        L.append("")
    if leitura.arquivos_escritos:
        L.append(f"**Arquivos escritos ou editados ({len(leitura.arquivos_escritos)}):**")
        L.append("")
        for a in leitura.arquivos_escritos:
            L.append(f"- `{a}`")
        L.append("")
    if artefatos:
        L.append("**Artefatos catalogados:**")
        L.append("")
        for art in artefatos:
            desc = f" — {art['description']}" if art.get("description") else ""
            L.append(f"- `{art['kind']}` `{art['path']}`{desc}")
        L.append("")
    if not (leitura.comandos_git or leitura.arquivos_escritos or artefatos):
        L.append("_Nada registrado ainda._")
        L.append("")

    if leitura.ferramentas:
        L.append("## Ferramentas usadas")
        L.append("")
        for nome, n in sorted(leitura.ferramentas.items(), key=lambda x: -x[1]):
            L.append(f"- {nome}: {n}")
        L.append("")

    if cat.get("outcome_summary"):
        L.append("## O que a sessão entregou")
        L.append("")
        L.append(cat["outcome_summary"])
        L.append("")

    return "\n".join(L).rstrip() + "\n"


def gerar(
    *,
    session_id: str,
    transcript: Path,
    catalogo: Optional[dict] = None,
    artefatos: Optional[list[dict]] = None,
    destino: Optional[Path] = None,
) -> Path:
    """Lê, renderiza e grava. Devolve o caminho do dossiê.

    Reescreve o arquivo inteiro a cada geração — e isso é seguro justamente
    porque ele é **derivado**: a fonte de verdade é o `.jsonl` ao lado, que é
    append-only e nunca é tocado aqui. Perder um dossiê custa uma regeração.
    """
    destino = destino or dossier_path_for(transcript)
    leitura = ler_transcript(transcript)
    texto = render(session_id=session_id, transcript=transcript, leitura=leitura,
                   catalogo=catalogo, artefatos=artefatos)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    return destino
