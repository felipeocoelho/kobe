"""Roda o verificador de portabilidade junto da suíte.

A lógica mora no shell (`tests/portability_guard.sh`), que usa `git grep` — a
ferramenta certa pra varrer o tree RASTREADO, respeitando `.gitignore` de graça.
Este arquivo existe só pra amarrar o guard ao `pytest`: sem isso ele viraria um
script que ninguém lembra de rodar, e a sujeira volta em dois meses.

Rodar sozinho: bash tests/portability_guard.sh
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent / "portability_guard.sh"
RAIZ = GUARD.parent.parent


@pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")
def test_nenhum_caminho_de_maquina_no_que_vai_pro_publico():
    r = subprocess.run(["bash", str(GUARD)], cwd=RAIZ,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (
        "verificador de portabilidade falhou:\n" + r.stdout + r.stderr
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")
def test_o_guard_sabe_falhar(tmp_path):
    """Um verificador que não sabe acusar não vale nada.

    Planta um caminho de operador num arquivo rastreado de um repo git
    descartável e confirma que o guard acusa. Sem este teste, um erro de regex
    deixaria o placar verde para sempre — que é exatamente o modo de falha que
    um guard tem que não ter.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    shutil.copy(GUARD, repo / "tests" / "portability_guard.sh")
    (repo / "sujo.md").write_text(
        "cd /home/" + "alguem" + "/projetos/kobe\n", encoding="utf-8")
    for cmd in (["git", "init", "-q"],
                ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=repo, capture_output=True, check=True)

    r = subprocess.run(["bash", "tests/portability_guard.sh"], cwd=repo,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 1, "o guard NÃO acusou um caminho de operador plantado"
    assert "PORTABILIDADE" in r.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="git ausente")
def test_placeholder_generico_nao_dispara_alarme_falso(tmp_path):
    """`/home/seu_usuario` é o que queremos NO LUGAR do caminho real — se o
    guard acusasse isso, ele empurraria a correção certa de volta pra errada."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    shutil.copy(GUARD, repo / "tests" / "portability_guard.sh")
    (repo / "limpo.md").write_text(
        "KOBE_HOME=/home/seu_usuario/projetos/kobe\n"
        "outro=/home/usuario/kobe\n", encoding="utf-8")
    for cmd in (["git", "init", "-q"],
                ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=repo, capture_output=True, check=True)

    r = subprocess.run(["bash", "tests/portability_guard.sh"], cwd=repo,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, "alarme falso em placeholder genérico:\n" + r.stdout


def test_exclusoes_do_guard_espelham_o_publish():
    """As duas listas têm que andar juntas: o guard só deve ignorar o que o
    publish de fato não manda pro público. Se alguém acrescentar uma exclusão
    no guard sem que ela exista no publish, o guard passa a esconder vazamento
    de verdade — e este teste pega."""
    guard = GUARD.read_text(encoding="utf-8")
    publish = (RAIZ / "infra" / "publish.sh").read_text(encoding="utf-8")
    for path in ("docs/runbooks/", "infra/publish.sh", "TESTE_*.md"):
        assert path in guard, f"{path} sumiu das exclusões do guard"
        assert path in publish, (
            f"{path} está excluído no guard mas não em EXCLUDE_PATHS do "
            "publish.sh — o guard estaria escondendo vazamento real"
        )
