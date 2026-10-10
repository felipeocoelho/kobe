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
import os
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


# Um plano da sala: `.local/plano-*.md` em qualquer nível. O `.local/` é a
# convenção do rito do Coder pra rascunho fora do git — é ali que o plano mora.
_RE_PLANO = re.compile(r"(?:^|/)\.local/(?:[^/]+/)*plano-[^/]*\.md$")
# Plano escrito por shell (`cat > .local/plano-x.md <<EOF`, `tee`). O conteúdo
# não é reconstituível a partir do comando com segurança, mas o FATO de a sala
# ter escrito aquele arquivo é — e é o fato que importa pra atribuição.
#
# Duas cautelas, ambas medidas no acervo de produção em 07/10/2026: o caminho
# só aceita caracteres de caminho (um `→ (\`.local/plano-x.md\`` dentro do texto
# de um `kobe-notify` casava), e o corpo de heredoc é removido antes (uma sala
# que escreveu um TESTE contendo `cat > .local/plano-sh.md` ganhava esse plano).
# Errar pra menos aqui é o lado certo: sem atribuição o dossiê diz "nenhum
# plano"; atribuição falsa é exatamente o defeito que esta regra existe pra matar.
_RE_PLANO_SHELL = re.compile(
    r"(?:(?<![-=])>{1,2}|\btee\s+(?:-a\s+)?)\s*['\"]?"
    r"((?:[\w.~+-]+/)*\.local/(?:[\w.+-]+/)*plano-[\w.+-]*\.md)(?![\w.+/-])"
)
_RE_NOME_PLANO = re.compile(r"plano-[\w.+-]*\.md")
_RE_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$", re.S | re.M)


def _sem_heredoc(comando: str) -> str:
    """O comando sem o CORPO dos heredocs (a linha que abre fica)."""
    return _RE_HEREDOC.sub(lambda m: m.group(0).split("\n", 1)[0], comando)


@dataclass
class EscritaDePlano:
    """Uma escrita que a sala fez num plano, na ordem em que aconteceu.

    `tipo` é `write` (conteúdo inteiro), `edit` (troca de trecho) ou `shell`
    (escrita por comando — sabe-se QUE escreveu, não O QUE).
    """

    caminho: str
    tipo: str
    conteudo: Optional[str] = None
    antigo: Optional[str] = None
    novo: Optional[str] = None
    todas: bool = False


def _absoluto(caminho: str, cwd: Optional[str]) -> str:
    if not os.path.isabs(caminho) and cwd:
        caminho = os.path.join(cwd, caminho)
    return os.path.normpath(caminho)


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
    # Toda escrita que a sala fez em `.local/**/plano-*.md`, em ordem. É a
    # ÚNICA fonte de atribuição do plano: o que a sala escreveu é dela; o que só
    # está no disco ao lado pode ser de qualquer outra.
    escritas_de_plano: list[EscritaDePlano] = field(default_factory=list)
    # Nomes de plano (`plano-*.md`) citados em QUALQUER comando de shell da
    # sala, corpo de heredoc incluído. Não atribui plano — só diz que a sala
    # pode ter mexido no arquivo por fora das ferramentas de edição (um script
    # marcando caixas, um `sed`), o que explica o disco divergir do transcript.
    planos_tocados_por_shell: set[str] = field(default_factory=set)


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
                            if nome in ("Write", "Edit", "MultiEdit",
                                        "NotebookEdit") and caminho:
                                if caminho not in vistos_arquivos:
                                    vistos_arquivos.add(caminho)
                                    leitura.arquivos_escritos.append(caminho)
                            if (isinstance(caminho, str)
                                    and nome in ("Write", "Edit", "MultiEdit")
                                    and _RE_PLANO.search(caminho)):
                                _registra_escrita_de_plano(
                                    leitura, nome, entrada,
                                    _absoluto(caminho, obj.get("cwd") or leitura.cwd))
                            comando = entrada.get("command")
                            if nome == "Bash" and isinstance(comando, str):
                                leitura.planos_tocados_por_shell.update(
                                    _RE_NOME_PLANO.findall(comando))
                                for m in _RE_PLANO_SHELL.finditer(_sem_heredoc(comando)):
                                    leitura.escritas_de_plano.append(EscritaDePlano(
                                        caminho=_absoluto(
                                            m.group(1),
                                            obj.get("cwd") or leitura.cwd),
                                        tipo="shell"))
                                texto = _texto_de_notify(comando)
                                if texto:
                                    leitura.marcos.append(_corta(texto))
                                for m in _RE_COMMIT_MSG.finditer(comando):
                                    leitura.comandos_git.append(
                                        _corta(m.group(2).strip().splitlines()[0], 200))
    return leitura


def _registra_escrita_de_plano(leitura: Leitura, ferramenta: str,
                               entrada: dict, caminho: str) -> None:
    if ferramenta == "Write":
        conteudo = entrada.get("content")
        leitura.escritas_de_plano.append(EscritaDePlano(
            caminho=caminho, tipo="write",
            conteudo=conteudo if isinstance(conteudo, str) else None))
        return
    trocas = entrada.get("edits") if ferramenta == "MultiEdit" else [entrada]
    for t in trocas if isinstance(trocas, list) else []:
        if not isinstance(t, dict):
            continue
        antigo, novo = t.get("old_string"), t.get("new_string")
        leitura.escritas_de_plano.append(EscritaDePlano(
            caminho=caminho, tipo="edit",
            antigo=antigo if isinstance(antigo, str) else None,
            novo=novo if isinstance(novo, str) else None,
            todas=bool(t.get("replace_all"))))


# --- o que ficou aberto -------------------------------------------------
#
# ATÉ 07/10/2026 o plano vinha do disco: "o `.local/plano-*.md` mais recente da
# pasta de trabalho". Toda sala com pasta na raiz do repositório divide um único
# `.local/`, e herdava o plano da última sala que escreveu ali. Medido em
# produção naquele dia: 742 de 797 dossiês citavam um plano que a sala NUNCA
# escreveu. Dossiê que mente é pior que dossiê que falta.
#
# Agora a atribuição vem do transcript — o que a sala escreveu é dela. O disco
# só entra pra trazer o estado mais atual de um plano que JÁ é dela.

_RE_CAIXA = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")


@dataclass
class PlanoDaSala:
    """O plano atribuído à sala, e de onde o checklist foi lido.

    `origem`:
      - `disco`       — o arquivo existe e é o que a sala escreveu;
      - `transcript`  — reconstruído reaplicando as escritas do transcript
                        (o arquivo sumiu, ou o do disco já não é o dela);
      - `indisponivel`— a sala escreveu, mas não há como mostrar o checklist.
    """

    caminho: str
    origem: str
    abertas: list[str] = field(default_factory=list)
    total: int = 0
    nota: Optional[str] = None
    outros: list[str] = field(default_factory=list)


def _caixas(texto: str) -> list[tuple[bool, str]]:
    caixas = []
    for linha in texto.splitlines():
        m = _RE_CAIXA.match(linha)
        if m:
            caixas.append((m.group(1) != " ", m.group(2)))
    return caixas


def _reconstroi(escritas: list[EscritaDePlano]) -> tuple[Optional[str], Optional[str]]:
    """Reaplica as escritas em ordem. Devolve `(texto, None)` ou `(None, motivo)`.

    Nunca devolve meio plano: uma troca que não casa (o trecho antigo não está
    no texto) quer dizer que o arquivo foi mexido por fora do que o transcript
    registra, e a partir daí qualquer texto seria palpite.
    """
    sem_partida = "o transcript não tem o texto de partida do plano"
    por_shell = ("o plano foi escrito por comando de shell, cujo conteúdo o "
                 "transcript não guarda")
    texto: Optional[str] = None
    lacuna = sem_partida  # por que `texto` é None, quando é
    for e in escritas:
        if e.tipo == "write":
            # Uma escrita inteira recompõe o texto, mesmo depois de uma lacuna.
            texto = e.conteudo if isinstance(e.conteudo, str) else None
            lacuna = "uma escrita do plano não tem conteúdo legível no transcript"
        elif e.tipo == "shell":
            texto, lacuna = None, por_shell
        else:  # edit
            if texto is None:
                continue  # a lacuna já está registrada; uma escrita pode salvar
            if e.antigo is None or e.novo is None or e.antigo not in texto:
                return None, ("uma edição do plano não casa com o texto "
                              "reconstruído — o arquivo foi mexido por fora")
            texto = texto.replace(e.antigo, e.novo) if e.todas \
                else texto.replace(e.antigo, e.novo, 1)
    if texto is None:
        return None, lacuna
    return texto, None


def plano_da_sala(leitura: Leitura) -> Optional[PlanoDaSala]:
    """O plano que ESTA sala escreveu, ou `None` se ela não escreveu nenhum.

    Se escreveu mais de um, vale o último que ela tocou; os outros são citados.
    """
    if not leitura.escritas_de_plano:
        return None
    ordem: list[str] = []
    for e in leitura.escritas_de_plano:
        if e.caminho in ordem:
            ordem.remove(e.caminho)
        ordem.append(e.caminho)
    caminho = ordem[-1]
    outros = ordem[:-1]
    escritas = [e for e in leitura.escritas_de_plano if e.caminho == caminho]

    reconstruido, motivo = _reconstroi(escritas)

    em_disco: Optional[str] = None
    try:
        p = Path(caminho)
        if p.is_file():
            em_disco = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        em_disco = None

    texto, origem, nota = None, "indisponivel", None
    if em_disco is not None and reconstruido is not None:
        # O disco só vale se ainda for o MESMO plano: mesmos itens, na mesma
        # ordem (as marcações podem ter mudado — é o estado mais atual). Itens
        # diferentes = outra sala reaproveitou o nome, e o disco não é dela.
        if [t for _, t in _caixas(em_disco)] == [t for _, t in _caixas(reconstruido)]:
            texto, origem = em_disco, "disco"
        elif Path(caminho).name in leitura.planos_tocados_por_shell:
            # Medido no acervo (07/10/2026): 7 de 7 divergências eram a PRÓPRIA
            # sala reescrevendo o plano por script. O disco é dela, e mais novo.
            texto, origem = em_disco, "disco"
        else:
            texto, origem = reconstruido, "transcript"
            nota = ("o arquivo em disco já não é o que esta sala escreveu; abaixo, "
                    "o plano como ela o deixou, reconstruído do transcript")
    elif em_disco is not None:
        # Não dá pra conferir o disco contra o transcript (escrita por shell,
        # edição que não casa), mas a sala comprovadamente escreveu ESTE
        # arquivo — atribuição feita; o conteúdo é o do disco.
        texto, origem = em_disco, "disco"
    elif reconstruido is not None:
        texto, origem = reconstruido, "transcript"
        nota = "o arquivo não existe mais em disco; reconstruído do transcript"
        if Path(caminho).name in leitura.planos_tocados_por_shell:
            # O transcript guarda as edições feitas por ferramenta, não as feitas
            # por script. Se a sala também mexeu no plano por comando, a contagem
            # pode estar atrasada — e um "0 de 17" errado seria uma mentira.
            nota += (" — a sala também mexeu nele por comando de shell, que o "
                     "transcript não reproduz; as marcações podem estar "
                     "desatualizadas")
    else:
        nota = f"o arquivo não existe mais em disco e não dá pra reconstruí-lo: {motivo}"

    plano = PlanoDaSala(caminho=caminho, origem=origem, nota=nota, outros=outros)
    if texto is not None:
        caixas = _caixas(texto)
        plano.total = len(caixas)
        plano.abertas = [t for feita, t in caixas if not feita]
    return plano


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
    plano = plano_da_sala(leitura)

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
    if plano is None:
        L.append(
            "_Nenhum plano identificado para esta sala_ — ela não escreveu "
            "nenhum `.local/plano-*.md` (a atribuição vem do transcript, não do "
            "que estiver na pasta)."
        )
    elif plano.origem == "indisponivel":
        L.append(f"Plano: `{plano.caminho}` — _{plano.nota}._")
    else:
        marcadas = plano.total - len(plano.abertas)
        L.append(f"Plano: `{plano.caminho}` — **{marcadas} de {plano.total}** concluídos.")
        if plano.nota:
            L.append("")
            L.append(f"_({plano.nota}.)_")
        L.append("")
        if plano.abertas:
            for item in plano.abertas:
                L.append(f"- [ ] {item}")
        else:
            L.append("_Nenhum item em aberto no checklist._")
    if plano is not None and plano.outros:
        L.append("")
        L.append("Outros planos escritos por esta sala: "
                 + ", ".join(f"`{o}`" for o in plano.outros) + ".")
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
