"""O preâmbulo que põe todo helper do Kobe no interpretador certo.

O PROBLEMA, E POR QUE ELE VOLTOU TRÊS VEZES
--------------------------------------------
Todo helper de `bot/bin/` nasce com `#!/usr/bin/env python3`, que resolve pro
Python do SISTEMA. O Python do sistema tem algumas dependências do Kobe e não
tem outras — e QUAIS ele tem muda com o tempo, sem aviso. Só o `.venv` do
projeto tem a lista inteira.

A correção histórica foi, em cada helper, um guarda com uma LISTA de
dependências: *"se faltar `psycopg`, re-exec no venv"*. **É esse desenho que
está errado**, e ele falhou três vezes, sempre do mesmo jeito — a lista de um
helper não conhecia a dependência que ele viria a usar:

| quando | helper | a lista dizia | o que faltava de verdade |
|---|---|---|---|
| 27/08/2026 (F0.2) | `kobe-reflect` | `psycopg` | `httpx` |
| 27/08/2026 (F0.2) | `kobe-remember` | `psycopg` | `openai`, `dotenv` |
| **30/08/2026 (F3)** | **`kobe-recall-since`** | `psycopg` | **`psycopg_pool`** |

O terceiro é o que dói: `kobe-recall-since` é o comando que o protocolo de run
em background manda o agente rodar pra ler a **janela de frescor** — o que o
operador disse DEPOIS que o pedido foi despachado. Ele falhando significa que
toda run de background perde a chance de ver um follow-up ou um "deixa pra lá".
Um mecanismo de segurança desarmado, e em silêncio: a mensagem que aparecia era
`No module named 'psycopg_pool'`, que ninguém lê como "a janela de frescor está
cega".

A LEI DESTE ARQUIVO: **NÃO EXISTE LISTA DE DEPENDÊNCIAS.**
-----------------------------------------------------------
A pergunta certa nunca foi *"falta alguma dependência?"* — é
**"eu já estou no interpretador do projeto?"**. Se não estou, vou pra lá. Ponto.
Não há lista pra ficar desatualizada, então não há como o bug voltar por
esquecimento: um helper que passe a usar uma biblioteca nova amanhã já está
coberto hoje.

O preço é um `exec` a mais (~30 ms) quando o helper é chamado pelo Python do
sistema — que é como o agente chama. Correção vale 30 ms.

AS TRÊS GARANTIAS
------------------
1. **Funciona de qualquer cwd, por caminho relativo, sem venv ativo.** A âncora é
   `__file__`, nunca o diretório de trabalho: `bot/bin/x` → a raiz é
   `parents[2]`. `KOBE_HOME` entra só como plano B, para o helper que tenha sido
   ligado (symlink) pra fora da árvore.
2. **Nunca entra em laço.** Um `.venv` torto (um `python` que não é venv de
   verdade) faria `sys.prefix` nunca casar e o processo se re-executar pra
   sempre. A sentinela no ambiente corta isso na segunda tentativa.
3. **Nunca derruba o helper.** Qualquer falha aqui — venv ausente, `exec`
   recusado, permissão — é engolida e a execução segue no interpretador atual.
   Um preâmbulo que quebra o `kobe-notify` seria pior que a doença: era assim
   que o relatório da F2 não chegava ao operador.

4. **Só sequestra quem PEDIU pra ser sequestrado.** O helper passa o próprio
   `__file__`, e a troca só acontece se ele for o programa em execução
   (`sys.argv[0]`). Sem isso, `ensure()` dispara também quando alguém IMPORTA o
   helper como módulo — e foi o que aconteceu na primeira versão deste arquivo:
   `tests/test_kobe_remember.py` carrega `bot/bin/kobe-remember` com
   `SourceFileLoader` e o preâmbulo trocou o interpretador **do pytest**, no
   meio da suíte. Ela morreu em 38% com `rc=1` e sem uma linha de erro — que é
   como um `execve` se parece de fora: o processo não falha, ele vira outro.

USO (as quatro linhas canônicas, no topo do helper, antes de qualquer
`from bot...`):

    _HELPER_DIR = Path(__file__).resolve().parent
    if str(_HELPER_DIR) not in sys.path:
        sys.path.insert(0, str(_HELPER_DIR))
    from _venv import ensure as _ensure_venv  # noqa: E402
    _ensure_venv(__file__)

`tests/test_helpers_venv.py` cobra isso de todo helper que precise — a trava é
um teste, não a disciplina de quem escreve o próximo.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Marca no ambiente que uma troca de interpretador JÁ foi tentada. É o que
# transforma um `.venv` torto em "seguiu com o que tinha" em vez de numa bomba
# de processos.
SENTINELA = "KOBE_HELPER_VENV_REEXEC"


def venv_do_projeto(anchor: Path | None = None) -> Path | None:
    """O diretório do venv do Kobe, ou `None` se não houver.

    Duas fontes, nesta ordem, e **nenhuma busca subindo a árvore**: procurar
    `.venv` de pai em pai acharia o venv de um diretório qualquer acima da
    instalação — que é exatamente o tipo de acerto por acaso que este arquivo
    existe pra não depender.
    """
    candidatos: list[Path] = []

    # 1. A raiz do projeto, deduzida do próprio arquivo: `<raiz>/bot/bin/_venv.py`.
    base = (anchor or Path(__file__).resolve()).parents[2]
    candidatos.append(base / ".venv")

    # 2. `KOBE_HOME`, para o helper alcançado por symlink de fora da árvore.
    kobe_home = (os.environ.get("KOBE_HOME") or "").strip()
    if kobe_home:
        candidatos.append(Path(kobe_home).expanduser() / ".venv")

    for venv in candidatos:
        try:
            if (venv / "bin" / "python").is_file():
                return venv
        except OSError:
            continue
    return None


def ja_estamos_no_venv(venv: Path) -> bool:
    """`sys.prefix` é o que o próprio interpretador diz sobre onde ele mora —
    mais confiável que comparar caminhos de executável, que diverge quando o
    binário é alcançado por symlink."""
    try:
        return Path(sys.prefix).resolve() == venv.resolve()
    except OSError:
        return False


def _e_o_programa_em_execucao(caller: str) -> bool:
    """O arquivo que chamou é o mesmo que está sendo executado?

    `realpath` dos dois lados porque o helper costuma ser alcançado por symlink
    (em `PATH`, ou pela instalação), e aí `sys.argv[0]` e `__file__` são nomes
    diferentes do mesmo arquivo.
    """
    try:
        alvo = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        return bool(alvo) and alvo == os.path.realpath(caller)
    except OSError:
        return False


def ensure(caller: str | None = None) -> None:
    """Troca o processo pelo Python do venv do projeto, se ainda não for ele.

    `caller` é o `__file__` do helper. Passando-o, a troca só acontece quando
    esse arquivo É o programa em execução — o que impede o preâmbulo de
    sequestrar quem apenas **importa** o helper (pytest, uma ferramenta, outro
    script). Ver garantia 4 no topo. Omitir `caller` mantém o comportamento
    aberto, e é o que o re-exec tardio de `_kobe_topic` precisa: lá quem chama
    não é o programa, é uma função dentro dele.

    Não devolve nada porque, no caminho normal, **não devolve**: `execve`
    substitui o processo. Se voltar, é porque não havia o que trocar (ou a troca
    falhou) — e aí o helper segue, que é o comportamento seguro.
    """
    try:
        if caller is not None and not _e_o_programa_em_execucao(caller):
            return
        venv = venv_do_projeto()
        if venv is None or ja_estamos_no_venv(venv):
            return
        if os.environ.get(SENTINELA) == "1":
            # Já tentamos uma vez e continuamos fora do venv. Insistir seria o
            # laço; seguir é o que dá ao usuário o `ImportError` de verdade,
            # que ao menos diz qual módulo falta.
            return

        # Só se re-executa um SCRIPT. Num `python -c "..."` ou num REPL,
        # `sys.argv[0]` é `-c` (ou vazio) e não existe em disco: re-executar
        # daria `can't open file '.../-c'` — o preâmbulo matando o processo que
        # ele deveria salvar. Helper é sempre arquivo; o resto segue como está.
        script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
        if not script or not os.path.isfile(script):
            return

        python = venv / "bin" / "python"
        # Um `python` presente mas não executável (permissão, montagem
        # `noexec`) faria o `exec` estourar — e o `except` lá embaixo já
        # seguraria. Conferir antes é mais barato e deixa o motivo explícito.
        if not os.access(python, os.X_OK):
            return
        env = dict(os.environ)
        env[SENTINELA] = "1"
        os.execve(str(python), [str(python), script, *sys.argv[1:]], env)
    except Exception:  # noqa: BLE001 — preâmbulo não derruba helper. Ver garantia 3.
        return
