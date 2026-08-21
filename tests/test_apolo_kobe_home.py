"""Testes do resolvedor de raiz do Kobe em `bot/apolo_handlers.py`.

Bug de portabilidade que estes testes fecham: os quatro pontos do módulo faziam
`os.environ.get("KOBE_HOME", <caminho absoluto da máquina de um operador>)`. Num
clone público, sem a env, o default apontava pra uma pasta que **não existe na
máquina de quem instalou** — e o erro só aparecia lá adiante, disfarçado de
"script do Apolo não encontrado", apontando pro lugar errado.

A trava central é `test_sem_env_resolve_pra_raiz_do_repo`: sem `KOBE_HOME`, o
módulo tem que achar a raiz **por si**, e o caminho resolvido não pode conter
diretório de máquina de operador nenhum.

Rodar: .venv/bin/python -m pytest tests/test_apolo_kobe_home.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bot import apolo_handlers


RAIZ = Path(apolo_handlers.__file__).resolve().parent.parent


def test_sem_env_resolve_pra_raiz_do_repo(monkeypatch):
    """A trava. Env ausente → deriva da localização do módulo."""
    monkeypatch.delenv("KOBE_HOME", raising=False)
    assert apolo_handlers._kobe_home() == RAIZ
    assert (RAIZ / "bot").is_dir()


def test_sem_env_nao_inventa_caminho_de_operador(monkeypatch):
    """Nenhum default de máquina alheia — nem o antigo, nem outro qualquer.

    Checa a FORMA do caminho, não um valor específico: o que não pode é o
    módulo devolver um diretório que ele não descobriu sozinho.
    """
    monkeypatch.delenv("KOBE_HOME", raising=False)
    resolvido = apolo_handlers._kobe_home()
    assert resolvido.is_dir(), "resolveu pra um caminho que não existe"
    assert resolvido == Path(apolo_handlers.__file__).resolve().parent.parent


def test_env_vazia_cai_na_derivacao(monkeypatch):
    """`KOBE_HOME=` no .env é o mesmo que ausente — não vira Path('')."""
    monkeypatch.setenv("KOBE_HOME", "   ")
    assert apolo_handlers._kobe_home() == RAIZ


def test_env_valida_vence(monkeypatch, tmp_path):
    (tmp_path / "bot").mkdir()
    monkeypatch.setenv("KOBE_HOME", str(tmp_path))
    assert apolo_handlers._kobe_home() == tmp_path.resolve()


def test_env_apontando_pra_lugar_errado_avisa_e_deriva(monkeypatch, tmp_path, caplog):
    """Env setada mas sem `bot/` dentro: não obedece calado, avisa e deriva.

    Obedecer a uma env errada em silêncio é como o bug antigo se disfarçava.
    """
    monkeypatch.setenv("KOBE_HOME", str(tmp_path))  # sem bot/ dentro
    with caplog.at_level("WARNING"):
        assert apolo_handlers._kobe_home() == RAIZ
    assert "não parece uma raiz do Kobe" in caplog.text


def test_derivacao_impossivel_falha_alto(monkeypatch):
    """Se nem env nem derivação servem, LEVANTA com o motivo escrito — não
    devolve um caminho fantasma pra quebrar três chamadas adiante."""
    monkeypatch.delenv("KOBE_HOME", raising=False)
    monkeypatch.setattr(apolo_handlers, "__file__", "/nao/existe/bot/apolo_handlers.py")
    with pytest.raises(RuntimeError) as exc:
        apolo_handlers._kobe_home()
    assert "não consegui resolver a raiz do Kobe" in str(exc.value)


def test_os_quatro_pontos_usam_a_fonte_unica(monkeypatch, tmp_path):
    """Antes eram 4 cópias da mesma expressão; agora derivam do mesmo helper.

    Redirecionar o helper tem que mover TODOS os caminhos — se algum tiver
    ficado com cópia própria, este teste pega.
    """
    (tmp_path / "bot").mkdir()
    monkeypatch.setenv("KOBE_HOME", str(tmp_path))
    assert apolo_handlers._apolo_script("x.py") == (
        tmp_path / "plugins" / "public" / "apolo" / "scripts" / "x.py")
    assert apolo_handlers._venv_python() == tmp_path / ".venv" / "bin" / "python"


def test_modulo_nao_tem_caminho_absoluto_de_maquina():
    """Varredura no fonte: nenhum `/home/<usuário>` cravado sobrou."""
    import re
    fonte = Path(apolo_handlers.__file__).read_text(encoding="utf-8")
    achados = re.findall(r"/home/[a-z][a-z0-9_-]*", fonte)
    assert not achados, f"caminho de máquina no fonte: {achados}"
