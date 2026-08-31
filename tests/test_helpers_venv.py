#!/usr/bin/env python3
"""A trava que impede o helper de nascer no interpretador errado.

O QUE ESTE ARQUIVO EXISTE PRA IMPEDIR
-------------------------------------
Três vezes o mesmo defeito: um helper de `bot/bin/` com `#!/usr/bin/env python3`
(o Python do SISTEMA) e um guarda que conferia uma **lista de dependências**
incompleta. A lista estava certa no dia em que foi escrita e ficou errada
depois, sozinha, quando o helper passou a importar mais uma coisa.

    27/08/2026  kobe-reflect        lista dizia psycopg   faltava httpx
    27/08/2026  kobe-remember       lista dizia psycopg   faltava openai/dotenv
    30/08/2026  kobe-recall-since   lista dizia psycopg   faltava psycopg_pool

O terceiro custou caro: `kobe-recall-since` é o comando da **janela de frescor**
de toda run em background. Quebrado, o agente perde a chance de ver o follow-up
que o operador mandou depois do despacho — e a mensagem de erro
(`No module named 'psycopg_pool'`) não diz isso a ninguém.

O erro não era de disciplina, era de desenho: **nada obrigava a lista a
acompanhar os imports.** A correção foi tirar a lista de cena
(`bot/bin/_venv.py`), e este teste é o que impede a lista de voltar.

POR QUE ELE NÃO TOCA EM NADA — e por que isso é o ponto
--------------------------------------------------------
Lê os arquivos e olha o texto. Sem banco, sem rede, sem venv, sem subprocesso.
Roda em qualquer máquina, em todo `pytest`, inclusive em clone limpo — e não é
do tipo que "pula", que é verde por ausência.

Rodar: .venv/bin/python -m pytest tests/test_helpers_venv.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
BIN = RAIZ / "bot" / "bin"

# Os helpers deliberadamente STDLIB-PURA no caminho comum. Não é esquecimento:
# `kobe-notify` e `kobe-attach` são o CANAL com o operador, e fazê-los depender
# do venv seria trocar um defeito raro (falta de dependência) por um pior (o
# relatório não chega). Eles importam `_kobe_topic`, que só toca `psycopg` no
# caminho `--topic`, e ali o re-exec é tardio, dentro da função.
CANAL_STDLIB = {"kobe-notify", "kobe-attach"}

# Helpers que não importam nada fora da stdlib. Se um deles passar a importar,
# o teste `test_todo_helper_com_dependencia_chama_ensure` acusa.
SEM_DEPENDENCIA = {"kobe-dispatch", "kobe-heartbeat-run", "kobe-kb-shortindex", "kobe-whatsapp"}

# Módulos de terceiros que aparecem nos helpers. Não é uma lista de guarda (o
# ponto do _venv é justamente não ter uma) — é o gatilho do teste: "este helper
# precisa do venv?".
TERCEIROS = {"psycopg", "psycopg_pool", "dotenv", "httpx", "openai", "croniter", "telegram"}


def helpers() -> list[Path]:
    return sorted(p for p in BIN.glob("kobe-*") if p.is_file())


def _texto(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _precisa_do_venv(texto: str) -> bool:
    """Importa `bot.` (que puxa a árvore inteira) ou um pacote de terceiros."""
    if "from bot" in texto or "import bot" in texto:
        return True
    return any(f"import {m}" in texto for m in TERCEIROS)


def test_ha_helpers_para_conferir():
    """Se o glob quebrar, os testes abaixo passariam por vacuidade."""
    assert len(helpers()) >= 10


def test_nenhum_helper_confere_lista_de_dependencias():
    """A regressão que este arquivo existe pra pegar.

    `find_spec("<modulo>")` como condição de re-exec É a lista. Reaparecendo em
    qualquer helper, o bug volta com ela.
    """
    reincidentes = []
    for p in helpers():
        t = _texto(p)
        if "find_spec" in t and "execv" in t:
            reincidentes.append(p.name)
    assert not reincidentes, (
        "estes helpers voltaram a decidir o re-exec por LISTA DE DEPENDÊNCIAS: "
        f"{reincidentes}. Use `from _venv import ensure` — a pergunta certa é "
        '"já estou no venv do projeto?", não "falta algum módulo?". '
        "Ver bot/bin/_venv.py."
    )


def test_nenhum_helper_reimplementa_o_reexec():
    """Um `execv` solto num helper é o preâmbulo duplicado voltando."""
    duplicadores = []
    for p in helpers():
        if "os.execv" in _texto(p):
            duplicadores.append(p.name)
    assert not duplicadores, (
        f"{duplicadores} reimplementam o re-exec. Ele mora em bot/bin/_venv.py, "
        "num lugar só, justamente porque cada cópia envelheceu de um jeito."
    )


@pytest.mark.parametrize("helper", [p.name for p in helpers()])
def test_todo_helper_com_dependencia_chama_ensure(helper: str):
    """Quem precisa do venv tem que ir pro venv — e a lista de exceções é curta
    e justificada, não uma gaveta."""
    p = BIN / helper
    t = _texto(p)
    if not _precisa_do_venv(t):
        assert helper in SEM_DEPENDENCIA or helper in CANAL_STDLIB, (
            f"{helper} não importa nada de fora da stdlib mas também não está "
            "declarado como tal. Se isso for verdade, acrescente-o a "
            "SEM_DEPENDENCIA; se não for, o detector de dependência ficou cego."
        )
        return
    if helper in CANAL_STDLIB:
        # Estes importam `bot`, mas de forma preguiçosa e opcional. O que se
        # cobra deles é o contrário: NÃO chamar `ensure()` no topo.
        assert "_ensure_venv(" not in t, (
            f"{helper} é o canal com o operador e é stdlib-pura de propósito "
            "no caminho comum. Ver CANAL_STDLIB neste arquivo."
        )
        return
    assert "from _venv import ensure" in t and "_ensure_venv(__file__)" in t, (
        f"{helper} importa dependência de fora da stdlib e NÃO chama "
        "`_venv.ensure()`. Sem isso ele roda no python do sistema e morre com "
        "`ModuleNotFoundError` exatamente no caminho em que o agente o chama."
    )


def test_ensure_e_ancorado_no_arquivo_e_nao_no_cwd():
    """O agente chama o helper por caminho relativo, de qualquer cwd, sem venv
    ativo. Uma âncora em `os.getcwd()` funcionaria no teste e falharia lá."""
    t = _texto(BIN / "_venv.py")
    assert "Path(__file__).resolve()" in t
    assert "getcwd" not in t


def test_ensure_tem_sentinela_contra_laco():
    """`.venv` torto → `sys.prefix` nunca casa → re-exec infinito. A sentinela
    no ambiente é o que transforma isso em "seguiu com o que tinha"."""
    t = _texto(BIN / "_venv.py")
    assert "SENTINELA" in t and 'os.environ.get(SENTINELA)' in t


def test_ensure_nunca_levanta_em_arvore_sem_venv(monkeypatch):
    """Preâmbulo que derruba helper é pior que a doença — foi assim que o
    relatório da F2 não chegou ao operador.

    ⚠️ **`ensure()` NÃO se chama de dentro do pytest**, e isto foi aprendido
    doendo: chamá-lo aqui trocou o interpretador **do próprio pytest** no meio
    da suíte (a árvore era uma worktree do Coder, sem `.venv`, então ele caiu
    no `$KOBE_HOME`). A suíte morreu em 38% com `rc=1` e **sem uma linha de
    erro** — que é exatamente como um `execve` se parece de fora: o processo
    não falha, ele vira outro. O que se testa aqui são as **peças**; o `exec`
    de verdade é exercitado por subprocesso, no teste seguinte.
    """
    sys.path.insert(0, str(BIN))
    import _venv  # noqa: PLC0415

    # Âncora numa árvore que não existe e sem `KOBE_HOME`: não há venv, e a
    # resposta certa é `None` em silêncio — nunca uma exceção.
    monkeypatch.delenv("KOBE_HOME", raising=False)
    assert _venv.venv_do_projeto(Path("/tmp/nao/existe/bot/bin/_venv.py")) is None


def test_ensure_leva_um_script_do_python_do_sistema_pro_venv(tmp_path):
    """A prova de ponta a ponta, no caminho em que o agente de fato chama:
    **python do SISTEMA**, cwd estranho, sem venv ativo.

    O script de sonda repete o preâmbulo canônico e depois importa `bot.db` —
    que é justamente quem puxa `psycopg_pool`, o módulo cuja ausência derrubou
    o `kobe-recall-since`. Se o re-exec não acontecer, o import morre.
    """
    import os
    import subprocess

    venv = _venv_para_teste()
    if venv is None:
        pytest.skip("sem venv na árvore nem em KOBE_HOME — nada a exercitar")

    sonda = tmp_path / "sonda.py"
    sonda.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(BIN)!r})\n"
        f"sys.path.insert(0, {str(RAIZ)!r})\n"
        "from _venv import ensure\n"
        "ensure(__file__)\n"
        "import bot.db\n"
        "print('PREFIXO', sys.prefix)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["KOBE_HOME"] = str(venv.parent)
    env.pop("VIRTUAL_ENV", None)
    env.pop("KOBE_HELPER_VENV_REEXEC", None)
    r = subprocess.run(
        ["/usr/bin/python3", str(sonda)],
        capture_output=True, text=True, cwd="/tmp", env=env, timeout=120,
    )
    saida = r.stdout + r.stderr
    assert "No module named" not in saida, (
        "a sonda morreu por dependência ausente rodando no python do sistema — "
        f"é exatamente o defeito que o _venv existe pra matar:\n{saida[:800]}"
    )
    assert r.returncode == 0, saida[:800]
    assert str(venv) in r.stdout, (
        f"o re-exec não aconteceu: a sonda terminou em {r.stdout.strip()!r}, "
        f"e não no venv {venv}"
    )


def test_ensure_nao_reexecuta_um_python_menos_c(tmp_path):
    """`python -c` não tem script em disco. Re-executar daria
    `can't open file '.../-c'` — o preâmbulo matando o processo que deveria
    salvar."""
    import os
    import subprocess

    venv = _venv_para_teste()
    if venv is None:
        pytest.skip("sem venv na árvore nem em KOBE_HOME")

    env = dict(os.environ)
    env["KOBE_HOME"] = str(venv.parent)
    env.pop("KOBE_HELPER_VENV_REEXEC", None)
    r = subprocess.run(
        [
            "/usr/bin/python3", "-c",
            f"import sys; sys.path.insert(0, {str(BIN)!r});"
            " from _venv import ensure; ensure(); print('VIVO')",
        ],
        capture_output=True, text=True, cwd="/tmp", env=env, timeout=60,
    )
    assert r.returncode == 0 and "VIVO" in r.stdout, (r.stdout + r.stderr)[:500]


def _venv_para_teste():
    import os

    for cand in (RAIZ / ".venv", Path(os.environ.get("KOBE_HOME", "/nao/existe")) / ".venv"):
        if (cand / "bin" / "python").is_file():
            return cand
    return None


def test_ensure_nao_sequestra_quem_apenas_IMPORTA_o_helper(tmp_path):
    """A garantia 4, e ela custou uma suíte morta pra ser descoberta.

    `tests/test_kobe_remember.py` carrega `bot/bin/kobe-remember` com
    `SourceFileLoader` para testá-lo. Sem o guarda do chamador, o preâmbulo do
    helper trocava o interpretador **do pytest** no meio da suíte — que morria
    em 38%, com `rc=1` e **sem uma linha de erro**, porque um `execve` não falha:
    ele vira outro processo.
    """
    import os
    import subprocess

    venv = _venv_para_teste()
    if venv is None:
        pytest.skip("sem venv na árvore nem em KOBE_HOME")

    importador = tmp_path / "importador.py"
    importador.write_text(
        "import importlib.machinery, importlib.util, sys, os\n"
        f"sys.path.insert(0, {str(BIN)!r})\n"
        f"sys.path.insert(0, {str(RAIZ)!r})\n"
        "marca = os.getpid()\n"
        f"l = importlib.machinery.SourceFileLoader('h', {str(BIN / 'kobe-remember')!r})\n"
        "s = importlib.util.spec_from_loader(l.name, l)\n"
        "m = importlib.util.module_from_spec(s)\n"
        "l.exec_module(m)\n"
        "print('SOBREVIVI', marca == os.getpid())\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["KOBE_HOME"] = str(venv.parent)
    env.pop("KOBE_HELPER_VENV_REEXEC", None)
    r = subprocess.run(
        ["/usr/bin/python3", str(importador)],
        capture_output=True, text=True, cwd="/tmp", env=env, timeout=120,
    )
    assert "SOBREVIVI True" in r.stdout, (
        "importar o helper trocou o interpretador de quem importou:\n"
        + (r.stdout + r.stderr)[:800]
    )
