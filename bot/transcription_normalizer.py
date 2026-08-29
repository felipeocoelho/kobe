"""Normalizador determinístico de transcrição — glossário próprio, sem LLM.

Highlander v3, F0.6 (decisão E11). **Grafia de nome próprio é problema de
dicionário, não de modelo.**

O QUE ESTAVA QUEBRADO
---------------------
A única proteção contra erro de transcrição era o `prompt` do Whisper, lido de
`user-data/transcription-hints.md` e limitado a **850 bytes e ~224 tokens** — o que
vier primeiro (`bot/transcribe.py`),
já com 616 usados. Esse arquivo **já lista** "Kobi/Colby/Cobi → Kobe" — e mesmo
assim a memória durável do operador tem, hoje, fatos como *"os plugins do Koby
ficarão em home, Filipe e Kobi"*. Dica é probabilística; ela empurra o modelo,
não garante nada. E erro de transcrição, uma vez gravado, vira **fato permanente**
na memória durável: a mesma frase errada volta meses depois como se fosse verdade.

O DESENHO, E POR QUE ELE É CONSERVADOR
--------------------------------------
- **Só toca texto vindo de ÁUDIO.** Texto digitado é intenção: se o operador
  escreveu "Kobi", ele quis escrever aquilo, e reescrever seria mentir sobre o
  que ele disse. Quem decide isso é o chamador (ver `_download_and_transcribe`).
- **Casamento por limite de palavra**, insensível a caixa e a acento. "Kobierski"
  não vira "Kobeerski"; "cóbi", "KOBI" e "Kobi" viram todos `Kobe`, na grafia que
  o glossário declara.
- **Nada se perde.** Toda substituição é registrada numa trilha de auditoria com
  o texto original — o histórico continua auditável (§4.7 do briefing).
- **Idempotente**: normalizar duas vezes dá o mesmo resultado, desde que o
  glossário não mapeie um destino de volta pra uma origem (e isso é checado ao
  carregar, com aviso).
- **Atrás de flag, default OFF.** O critério de pronto da F0 exige que o operador
  aprove a lista do relatório antes de o normalizador entrar em produção.

FORMATO DO GLOSSÁRIO (`user-data/transcription-glossary.md`)
------------------------------------------------------------
Uma regra por linha, `errado -> certo`. Linha vazia e `#` são ignoradas; o resto
do markdown (títulos, prosa) também, o que deixa o arquivo legível como documento.
**Sem limite de tamanho** — é a diferença pro arquivo de dicas do Whisper.

    Kobi -> Kobe
    Cloud Code -> Claude Code
    # comentário
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

logger = logging.getLogger("kobe.transcription_normalizer")

GLOSSARY_RELPATH = ("user-data", "transcription-glossary.md")
AUDIT_RELDIR = ("user-data", "transcription-normalizer")

# `a -> b`, com seta ASCII ou unicode. O lado esquerdo pode ter espaço ("Cloud
# Code"), então a separação é pela seta e não por espaço em branco.
_REGRA = re.compile(r"^(?P<de>.+?)\s*(?:->|→)\s*(?P<para>.+?)\s*$")


class Rule(NamedTuple):
    de: str
    para: str
    padrao: re.Pattern


class Change(NamedTuple):
    de: str
    para: str
    n: int


def _sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _padrao(de: str) -> re.Pattern:
    """Regex que casa `de` ignorando caixa e acento, com limite de palavra.

    O truque do acento: em vez de tentar casar o texto acentuado com o termo sem
    acento, a gente casa **classe por classe** — cada letra vira `[aáàâã]` e afins.
    Assim "cóbi" casa com a regra "Kobi" sem precisar destruir o texto original
    (o que quebraria as posições e perderia os acentos do resto da frase).
    """
    equivalentes = {
        "a": "aáàâãä", "e": "eéèêë", "i": "iíìîï",
        "o": "oóòôõö", "u": "uúùûü", "c": "cç", "n": "nñ",
    }
    partes: list[str] = []
    for ch in _sem_acento(de).lower():
        if ch in equivalentes:
            classe = equivalentes[ch]
            partes.append(f"[{classe}{classe.upper()}]")
        elif ch.isalnum():
            partes.append(re.escape(ch))
        elif ch.isspace():
            partes.append(r"\s+")
        else:
            partes.append(re.escape(ch))
    corpo = "".join(partes)
    # `\b` não serve nas pontas quando o termo começa/termina com não-alfanumérico;
    # aqui todos os termos de glossário são palavras, então `\b` é o certo — e é
    # o que impede "Kobierski" de virar "Kobeerski".
    return re.compile(rf"\b{corpo}\b", re.IGNORECASE)


def load_glossary(kobe_home: Path) -> list[Rule]:
    """Lê o glossário. Lista vazia se não existir (no-op gracioso)."""
    caminho = kobe_home.joinpath(*GLOSSARY_RELPATH)
    if not caminho.is_file():
        return []
    try:
        bruto = caminho.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("glossário ilegível (%s): %s", caminho, exc)
        return []

    regras: list[Rule] = []
    destinos: set[str] = set()
    # Só vale o que está DEPOIS do título "## Regras". Descoberto rodando o
    # relatório contra as 3.521 mensagens de dev: a própria prosa do template
    # explica o formato usando uma seta ("a regra `Kobi -> Kobe` pega…"), e o
    # parser ingênuo transformou a frase inteira em regra. Documento legível e
    # dicionário no mesmo arquivo exigem uma fronteira explícita — não heurística.
    #
    # Só `##` (nível 2+) troca de seção; um `#` sozinho é COMENTÁRIO e não
    # fecha a seção — senão a primeira linha `# o nome do framework` dentro das
    # regras desligaria o parser e o glossário carregaria vazio (foi o que
    # aconteceu na primeira execução deste conserto).
    dentro_das_regras = False
    for numero, linha in enumerate(bruto.splitlines(), start=1):
        linha = linha.strip()
        if linha.startswith("##"):
            dentro_das_regras = _sem_acento(linha).lower().lstrip("# ").startswith("regras")
            continue
        if not dentro_das_regras or not linha or linha.startswith(("#", ">")):
            continue
        m = _REGRA.match(linha)
        if not m:
            continue  # prosa dentro da seção também não vira regra
        de, para = m.group("de").strip("` "), m.group("para").strip("` ")
        if not de or not para:
            logger.warning("glossário linha %d: regra vazia, ignorada", numero)
            continue
        regras.append(Rule(de, para, _padrao(de)))
        destinos.add(_sem_acento(para).lower())

    # Ciclo (`a -> b` e `b -> a`) quebraria a idempotência: cada passada
    # inverteria o texto. Avisar é melhor que "consertar" adivinhando.
    for r in regras:
        if _sem_acento(r.de).lower() in destinos and r.de.lower() != r.para.lower():
            logger.warning(
                "glossário: '%s' é origem E destino de regras — a normalização "
                "pode não ser idempotente. Revise o arquivo.", r.de,
            )
    if not regras and _REGRA.search(bruto):
        # Falha silenciosa clássica: o arquivo TEM regras, mas nenhuma sob o
        # título certo. Melhor gritar do que normalizar nada e parecer ligado.
        logger.warning(
            "glossário %s tem linhas com seta, mas nenhuma sob um título "
            "'## Regras' — nada foi carregado.", caminho,
        )
    return regras


def normalize(texto: str, regras: list[Rule]) -> tuple[str, list[Change]]:
    """Aplica o glossário. Devolve `(texto_normalizado, mudanças)`."""
    if not texto or not regras:
        return texto, []
    mudancas: list[Change] = []
    for r in regras:
        novo, n = r.padrao.subn(r.para, texto)
        if n:
            mudancas.append(Change(r.de, r.para, n))
            texto = novo
    return texto, mudancas


def registrar(
    kobe_home: Path,
    *,
    original: str,
    normalizado: str,
    mudancas: list[Change],
    origem: Optional[str] = None,
) -> None:
    """Trilha de auditoria — o original NUNCA se perde (§4.7 do briefing).

    Best-effort: falhar em auditar não pode derrubar um turno. Sem mudança,
    nada é escrito (o arquivo é o registro do que foi ALTERADO).
    """
    if not mudancas:
        return
    agora = datetime.now(timezone.utc)
    destino = kobe_home.joinpath(*AUDIT_RELDIR)
    try:
        destino.mkdir(parents=True, exist_ok=True)
        linha = json.dumps(
            {
                "quando": agora.isoformat(),
                "origem": origem,
                "original": original,
                "normalizado": normalizado,
                "regras": [{"de": c.de, "para": c.para, "n": c.n} for c in mudancas],
            },
            ensure_ascii=False,
        )
        with (destino / f"{agora:%Y-%m}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(linha + "\n")
    except OSError as exc:
        logger.warning("não consegui gravar a trilha do normalizador: %s", exc)


def normalize_transcription(
    kobe_home: Path,
    texto: str,
    *,
    enabled: bool,
    origem: Optional[str] = None,
) -> str:
    """Ponto de entrada do caminho do turno: normaliza e audita, ou devolve igual.

    `enabled=False` é no-op absoluto — nem lê o glossário.
    """
    if not enabled or not texto:
        return texto
    regras = load_glossary(kobe_home)
    normalizado, mudancas = normalize(texto, regras)
    if mudancas:
        logger.info(
            "normalizador: %d regra(s) aplicada(s) em transcrição (%s)",
            len(mudancas), ", ".join(f"{c.de}→{c.para}×{c.n}" for c in mudancas),
        )
        registrar(
            kobe_home,
            original=texto,
            normalizado=normalizado,
            mudancas=mudancas,
            origem=origem,
        )
    return normalizado
