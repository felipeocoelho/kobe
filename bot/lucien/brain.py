"""A chamada ao modelo e o parser estrito.

CUSTA COTA, NÃO DINHEIRO
------------------------
LUCIEN fala com o modelo pelo mesmo caminho que o Keyko já usa para acordar o
agente: a CLI `claude`, pela **assinatura** do operador. Zero de API paga
(seção 7.1 do briefing). O custo é cota, e por isso ele roda em lote, com teto
por hora, e sem pressa.

O ISOLAMENTO DA CHAMADA — E ELE NÃO É PARANOIA, É MEDIÇÃO
-----------------------------------------------------------
A F0.5 registrou por que o provider de assinatura do Hindsight força o
`CLAUDE_CONFIG_DIR` para um diretório temporário: **para não disparar os hooks e
plugins do operador dentro de uma chamada de LLM** — o que produz laço recursivo
de gravação de memória (issue #1751 do Hindsight).

LUCIEN corre exatamente o mesmo risco, e pior: ele roda dentro do `KOBE_HOME`,
onde existem `CLAUDE.md`, hooks e plugins. Uma chamada de extração que carregue
o contrato inteiro do Kobe não é só cara — ela muda o comportamento do modelo,
que passa a se achar o agente em vez do arquivista.

Então a chamada sai com:

- **cwd neutro** — um diretório próprio e vazio, para não carregar `CLAUDE.md`;
- **`CLAUDE_CONFIG_DIR` isolado** e `CLAUDE_SECURESTORAGE_CONFIG_DIR` vazio (o
  par exato que a F0.5 provou que MANTÉM a autenticação);
- **`--tools ""`** — sem ferramenta nenhuma. O cérebro recebe texto e devolve
  JSON; ele não precisa ler arquivo nem rodar comando, e não deve poder;
- **`--strict-mcp-config`** — sem os servidores MCP do operador;
- **`--system-prompt`** curto, substituindo o do agente. LUCIEN não é o Hal: é
  um extrator, e carregar o contrato inteiro do Kobe não só custa caro como
  muda o comportamento do modelo, que passa a se achar o agente;
- **timeout duro**, sem retry automático. A rodada seguinte relê o mesmo lote,
  porque o cursor não avançou.

**Os três primeiros custam cota, e isso foi MEDIDO em 30/08/2026**, com o mesmo
prompt trivial dos dois lados:

    sem as flags   43.039 tokens de contexto por chamada
    com as flags   13.154 tokens                          — 70% a menos

E há um segundo efeito, visto na medição seguinte: a chamada logo depois leu os
13.154 do **cache** e criou zero. Ou seja, uma reconstrução de ~145 chamadas
seguidas paga o prefixo **uma vez**, não 145. É o que torna a varredura do
passado inteiro viável em cota, e não só em teoria.

O PARSER ACEITA TRÊS FORMAS, DE PROPÓSITO
------------------------------------------
O formato de saída da CLI é a premissa mais frágil deste desenho — foi declarada
como tal no plano (risco R1) e não estava verificada quando ele foi escrito.
Então o parser não aposta numa forma só: tenta o envelope JSON da CLI, o bloco
cercado dentro de texto, e o JSON cru. **Se nenhuma servir, a rodada inteira é
descartada e o cursor não anda** — o que é sempre melhor que gravar metade.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from typing import Optional

from bot import lucien as cfg
from bot.lucien.models import ClaimProposta, Encerramento, Lote, Proposta
from bot.lucien import prompts

logger = logging.getLogger("kobe.lucien.brain")

# O prompt de sistema do cérebro. Curto de propósito: substitui o do agente
# inteiro (que traz o contrato do Kobe, as regras de conversa e o tom), porque
# nada disso serve a quem só extrai afirmação de um texto — e tudo isso custa.
SYSTEM = (
    "Você é LUCIEN, o arquivista da memória do Kobe. Você lê trechos de "
    "conversa e devolve, em JSON, as afirmações duráveis que eles estabelecem e "
    "as que eles contradizem. Você NÃO conversa, NÃO usa ferramentas e NÃO "
    "escreve nada além do JSON pedido. Quando o trecho não estabelece nada "
    "durável, dizer isso é a resposta certa — e é a mais comum."
)


class CerebroIndisponivel(RuntimeError):
    """A chamada não chegou a produzir resposta utilizável.

    Nome escolhido para não ser confundido com "não havia nada durável": são
    coisas opostas, e é essa confusão que já transformou falha de instrumento em
    "não há registro" duas vezes neste sistema.
    """


def _workdir(kobe_home: str) -> str:
    """O diretório neutro. Fica sob `user-data/lucien/` para ser inspecionável,
    e é criado vazio — o que importa é NÃO ter um `CLAUDE.md` dentro."""
    d = os.path.join(kobe_home, "user-data", "lucien", "workdir")
    os.makedirs(d, exist_ok=True)
    return d


def _ambiente(kobe_home: str, config_dir: str) -> dict:
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = config_dir
    env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = ""
    # O cérebro não fala com o operador. Sem estas, um `kobe-notify` disparado
    # por engano lá dentro não teria para onde ir — e é assim que se garante que
    # LUCIEN não vire uma voz no chat. O relatório é F5, e é do operador.
    for fora in ("KOBE_CHAT_ID", "KOBE_THREAD_ID"):
        env.pop(fora, None)
    return env


def chamar(prompt: str, *, kobe_home: str, reconstrucao: bool = False,
           timeout: Optional[float] = None) -> str:
    """Manda o prompt e devolve a saída crua. Levanta `CerebroIndisponivel`."""
    modelo = cfg.modelo(reconstrucao=reconstrucao)
    cmd = [
        "claude", "-p", "--output-format", "json",
        # Ver o cabeçalho: 43k → 13k tokens de contexto por chamada, medido.
        "--tools", "",
        "--strict-mcp-config",
        "--system-prompt", SYSTEM,
    ]
    if modelo:
        cmd += ["--model", modelo]

    with tempfile.TemporaryDirectory(prefix="lucien-cfg-") as config_dir:
        try:
            proc = subprocess.run(
                cmd,
                input=prompt.encode("utf-8"),
                capture_output=True,
                cwd=_workdir(kobe_home),
                env=_ambiente(kobe_home, config_dir),
                timeout=timeout or cfg.timeout_s(),
            )
        except FileNotFoundError as exc:
            raise CerebroIndisponivel("a CLI `claude` não foi encontrada") from exc
        except subprocess.TimeoutExpired as exc:
            raise CerebroIndisponivel(
                f"o modelo não respondeu em {timeout or cfg.timeout_s():.0f}s"
            ) from exc

    saida = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        # O stderr E o stdout. Medido em 30/08/2026: numa rajada de 70 falhas
        # seguidas, a CLI saiu com código 1 e **stderr vazio** — a mensagem de
        # erro dizia só "(sem stderr)", que não diagnostica nada. O motivo vinha
        # no stdout, no envelope de erro da própria CLI. Uma mensagem de falha
        # que não diz a causa custa uma investigação inteira depois.
        erro = proc.stderr.decode("utf-8", "replace").strip()[:400]
        detalhe = " ".join(saida.split())[:400]
        pistas = " · ".join(x for x in (erro, detalhe) if x) or "(sem stderr nem stdout)"
        raise CerebroIndisponivel(
            f"a CLI saiu com código {proc.returncode}: {pistas}"
        )
    if not saida.strip():
        raise CerebroIndisponivel("a CLI respondeu vazio")
    return saida


# ── O parser ─────────────────────────────────────────────────────────────

_CERCA = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def extrair_json(bruto: str) -> dict:
    """As três formas, nesta ordem. Levanta `CerebroIndisponivel` se nenhuma
    servir — nunca devolve um dicionário vazio se passando por resposta."""
    texto = (bruto or "").strip()

    # 1. O envelope da CLI: {"type": "result", "result": "<a resposta>"}.
    try:
        env = json.loads(texto)
    except ValueError:
        env = None
    if isinstance(env, dict):
        if _parece_proposta(env):
            return env
        interno = env.get("result")
        if isinstance(interno, str):
            return _do_texto(interno)
        if isinstance(interno, dict) and _parece_proposta(interno):
            return interno
        raise CerebroIndisponivel(
            "a CLI devolveu um envelope JSON sem proposta reconhecível "
            f"(chaves: {sorted(env)[:8]})"
        )

    # 2 e 3: bloco cercado dentro de texto, ou JSON cru no meio de prosa.
    return _do_texto(texto)


def _parece_proposta(d: dict) -> bool:
    return any(c in d for c in ("claims", "closures", "nothing_durable"))


def _do_texto(texto: str) -> dict:
    m = _CERCA.search(texto)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError as exc:
            raise CerebroIndisponivel(f"bloco cercado com JSON inválido: {exc}") from exc

    ini, fim = texto.find("{"), texto.rfind("}")
    if ini >= 0 and fim > ini:
        try:
            return json.loads(texto[ini : fim + 1])
        except ValueError as exc:
            raise CerebroIndisponivel(f"JSON inválido na resposta: {exc}") from exc

    if ini >= 0:
        # Abriu e não fechou. O diagnóstico importa: resposta TRUNCADA quase
        # sempre é teto de saída batido, e isso se conserta encurtando o lote —
        # ao contrário de "o modelo não respondeu em JSON", que é outra doença.
        raise CerebroIndisponivel(
            "JSON inválido na resposta: parece TRUNCADO (abre chave e não "
            f"fecha, {len(texto)} caracteres). Se repetir, o lote está grande "
            "demais para o teto de saída do modelo."
        )

    raise CerebroIndisponivel(
        "não achei JSON nenhum na resposta do modelo "
        f"(primeiros 120 caracteres: {texto[:120]!r})"
    )


def parsear(bruto: str) -> Proposta:
    """Da saída crua à `Proposta`. **Nada aqui valida conteúdo** — validar é
    trabalho de `store.aplicar`, e ter os dois num lugar só faria a trava viver
    ao lado de quem ela desconfia."""
    d = extrair_json(bruto)
    if not isinstance(d, dict):
        raise CerebroIndisponivel(f"a resposta não é um objeto: {type(d).__name__}")

    claims = []
    for item in _lista(d.get("claims")):
        if not isinstance(item, dict):
            continue
        claims.append(ClaimProposta(
            subject=str(item.get("subject") or ""),
            statement=str(item.get("statement") or ""),
            kind=str(item.get("kind") or ""),
            source_seq=item.get("source_seq"),
            evidence_seqs=_lista(item.get("evidence_seqs")),
            supersedes=[str(x) for x in _lista(item.get("supersedes"))],
            supersede_reason=str(item.get("supersede_reason") or ""),
            legibility_doubt=bool(item.get("legibility_doubt")),
            legibility_reason=str(item.get("legibility_reason") or ""),
        ))

    closures = []
    for item in _lista(d.get("closures")):
        if not isinstance(item, dict):
            continue
        closures.append(Encerramento(
            apelido=str(item.get("claim_id") or ""),
            action=str(item.get("action") or ""),
            source_seq=item.get("source_seq"),
            reason=str(item.get("reason") or ""),
        ))

    return Proposta(claims=claims, closures=closures,
                    nothing_durable=bool(d.get("nothing_durable")))


def _lista(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def pensar(lote: Lote, *, kobe_home: str, reconstrucao: bool = False) -> Proposta:
    """O caminho inteiro: prompt → modelo → proposta."""
    prompt = prompts.montar(lote)
    return parsear(chamar(prompt, kobe_home=kobe_home, reconstrucao=reconstrucao))
