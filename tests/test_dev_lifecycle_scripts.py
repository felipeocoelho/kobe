#!/usr/bin/env python3
"""Scripts de ciclo de vida do ambiente de dev (Sessão #1, P8).

O teste que dá razão a este arquivo é o do **sentido da cópia**. Copiar
identidade de dev para prod sobrescreveria quem o operador é com o que estiver
na árvore de testes, e isso não tem desfazer barato. A trava tem que ser por
evidência no destino, não por confiança no que a pessoa digitou — e tem que
falhar fechada: na ausência de prova de que o destino é dev, recusa.

Rodar: .venv/bin/python -m pytest tests/test_dev_lifecycle_scripts.py -q
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
SYNC = RAIZ / "infra" / "sync-identity-dev.sh"
DEV_UP = RAIZ / "infra" / "dev-up.sh"
DEV_DOWN = RAIZ / "infra" / "dev-down.sh"
SCRIPTS = [SYNC, DEV_UP, DEV_DOWN]
TEMPLATES = [
    RAIZ / "infra" / "kobe-dev.service.template",
    RAIZ / "infra" / "keyko-dev.service.template",
]


def _arvore(base: Path, *, dev: bool, com_identidade: bool = True) -> Path:
    """Monta uma árvore do Kobe de mentira, com ou sem marca de dev."""
    base.mkdir(parents=True, exist_ok=True)
    (base / ".env").write_text(
        "KOBE_ENV=dev\nTELEGRAM_BOT_TOKEN=x\n" if dev else "TELEGRAM_BOT_TOKEN=x\n",
        encoding="utf-8",
    )
    if com_identidade:
        ident = base / "user-data" / "identity"
        ident.mkdir(parents=True, exist_ok=True)
        (ident / "USER.md").write_text("quem é o operador\n", encoding="utf-8")
        persona = base / "user-data" / "persona"
        persona.mkdir(parents=True, exist_ok=True)
        (persona / "SOUL.md").write_text("quem é o agente\n", encoding="utf-8")
    return base


def _roda(*args: str) -> subprocess.CompletedProcess:
    # HOME próprio: sem isso, a checagem de `systemctl --user cat kobe-dev.service`
    # poderia achar uma unidade real da máquina e mudar o resultado do teste.
    env = {**os.environ, "SYSTEMD_OFFLINE": "1"}
    return subprocess.run(
        ["bash", str(SYNC), *args], capture_output=True, text=True, env=env
    )


# ── A trava do sentido ────────────────────────────────────────────────────


def test_recusa_copiar_para_um_destino_que_nao_prova_ser_dev(tmp_path: Path) -> None:
    prod = _arvore(tmp_path / "prod", dev=False)
    outro_prod = _arvore(tmp_path / "outro", dev=False)

    r = _roda(str(prod), str(outro_prod))

    assert r.returncode == 1
    assert "DESENVOLVIMENTO" in r.stderr
    # E, o que mais importa: não escreveu nada no destino.
    assert not (outro_prod / "user-data" / "identity" / "USER.md").exists() or (
        outro_prod / "user-data" / "identity" / "USER.md"
    ).read_text() == "quem é o operador\n"


def test_o_sentido_invertido_e_recusado(tmp_path: Path) -> None:
    """dev → prod: exatamente o que não pode acontecer."""
    prod = _arvore(tmp_path / "prod", dev=False)
    dev = _arvore(tmp_path / "dev", dev=True)

    r = _roda(str(dev), str(prod))  # origem dev, destino prod — invertido

    assert r.returncode == 1
    assert "sentido só" in r.stderr or "prod → dev" in r.stderr


def test_mesma_arvore_dos_dois_lados_e_recusada(tmp_path: Path) -> None:
    dev = _arvore(tmp_path / "dev", dev=True)
    r = _roda(str(dev), str(dev))
    assert r.returncode == 2
    assert "mesma árvore" in r.stderr


def test_caminho_inexistente_e_recusado(tmp_path: Path) -> None:
    dev = _arvore(tmp_path / "dev", dev=True)
    assert _roda(str(tmp_path / "nao-existe"), str(dev)).returncode == 2
    assert _roda(str(dev), str(tmp_path / "nao-existe")).returncode == 2


def test_sem_argumentos_mostra_uso(tmp_path: Path) -> None:
    r = _roda()
    assert r.returncode == 2
    assert "uso:" in r.stderr


# ── O caminho feliz, e o que ele NÃO leva ─────────────────────────────────


def test_copia_identidade_e_persona_para_um_dev_de_verdade(tmp_path: Path) -> None:
    prod = _arvore(tmp_path / "prod", dev=False)
    dev = _arvore(tmp_path / "dev", dev=True, com_identidade=False)

    r = _roda(str(prod), str(dev))

    assert r.returncode == 0, r.stderr
    assert (dev / "user-data" / "identity" / "USER.md").read_text() == "quem é o operador\n"
    assert (dev / "user-data" / "persona" / "SOUL.md").read_text() == "quem é o agente\n"


def test_nada_fora_da_lista_branca_atravessa(tmp_path: Path) -> None:
    """A propriedade que a lista branca existe para garantir.

    Alertas, conversas, sessões do Coder, backups — tudo isso mora em
    `user-data/` e não pode vazar da produção pro ambiente de testes. Com lista
    negra, uma pasta nova nasceria sendo copiada por omissão; com lista branca,
    nasce de fora, que é o lado seguro do esquecimento.
    """
    prod = _arvore(tmp_path / "prod", dev=False)
    for proibida in ("alertas", "topics", "knowledge", "coder-sessions", "backups"):
        p = prod / "user-data" / proibida
        p.mkdir(parents=True)
        (p / "segredo.md").write_text("não pode atravessar\n", encoding="utf-8")

    dev = _arvore(tmp_path / "dev", dev=True, com_identidade=False)
    assert _roda(str(prod), str(dev)).returncode == 0

    atravessaram = [
        d.name for d in (dev / "user-data").iterdir() if d.is_dir()
    ]
    assert sorted(atravessaram) == ["identity", "persona"]


def test_dry_run_nao_escreve_nada(tmp_path: Path) -> None:
    prod = _arvore(tmp_path / "prod", dev=False)
    dev = _arvore(tmp_path / "dev", dev=True, com_identidade=False)

    r = _roda("--dry-run", str(prod), str(dev))

    assert r.returncode == 0
    assert "nada foi copiado" in r.stdout
    assert not (dev / "user-data" / "identity").exists()
    # ...mas mostrou o que faria: o operador decide vendo, não adivinhando.
    assert "USER.md" in r.stdout


def test_nada_e_apagado_no_destino(tmp_path: Path) -> None:
    """Sem `--delete`: o que existir só no dev fica onde está."""
    prod = _arvore(tmp_path / "prod", dev=False)
    dev = _arvore(tmp_path / "dev", dev=True, com_identidade=False)
    so_do_dev = dev / "user-data" / "identity"
    so_do_dev.mkdir(parents=True)
    (so_do_dev / "anotacao-local.md").write_text("minha\n", encoding="utf-8")

    assert _roda(str(prod), str(dev)).returncode == 0
    assert (so_do_dev / "anotacao-local.md").exists()
    assert (so_do_dev / "USER.md").exists()


# ── Sanidade dos artefatos ────────────────────────────────────────────────


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_tem_sintaxe_valida_e_e_executavel(script: Path) -> None:
    assert script.exists(), script
    assert os.access(script, os.X_OK), f"{script.name} sem bit de execução"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_nenhum_script_invoca_rsync(script: Path) -> None:
    """Regra dura do projeto: rsync não é método de deploy de nada.

    A verificação ignora comentários de propósito: explicar POR QUE não se usa
    rsync é justamente o que se quer que esteja escrito nesses arquivos. O que
    não pode existir é a chamada.
    """
    codigo = "\n".join(
        linha
        for linha in script.read_text(encoding="utf-8").splitlines()
        if not linha.lstrip().startswith("#")
    )
    assert "rsync" not in codigo


def test_dev_down_confere_o_alvo_antes_de_derrubar() -> None:
    """Sem `--env-file` o alvo do compose é a PRODUÇÃO. Tem que checar antes."""
    texto = DEV_DOWN.read_text(encoding="utf-8")
    assert "name: hindsight-dev" in texto
    assert "--env-file .env.dev" in texto
    assert "down -v" not in texto, "derrubar é reversível; apagar volume não é"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_unidade_de_dev_forca_o_ambiente(template: Path) -> None:
    """`.env` de dev incompleto não pode fazer o bot de dev se achar em prod."""
    texto = template.read_text(encoding="utf-8")
    assert "Environment=KOBE_ENV=dev" in texto
    assert texto.index("Environment=KOBE_ENV=dev") < texto.index("EnvironmentFile=")
    assert "{{KOBE_HOME}}" in texto, "template tem que ser parametrizado"


def test_nao_existe_unidade_de_webhook_para_dev() -> None:
    """Dev não recebe WhatsApp: um webhook de dev competiria com o de produção
    pelo mesmo evento da Evolution, e há um chip só."""
    assert not (RAIZ / "infra" / "apolo-webhook-dev.service.template").exists()
    assert "apolo-webhook-dev" not in DEV_UP.read_text(encoding="utf-8").replace(
        "Não há apolo-webhook-dev", ""
    )
