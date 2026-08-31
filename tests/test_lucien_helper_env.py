#!/usr/bin/env python3
"""O `kobe-lucien` lendo a chave onde ela mora — e dizendo de onde leu.

O DEFEITO
---------
O helper lia `LUCIEN_ENABLED` do ambiente do PROCESSO e não carregava o `.env`
do projeto. Rodado de um shell qualquer — que é exatamente como se roda um
comando de diagnóstico — imprimia `chave LUCIEN_ENABLED: DESLIGADA` com a chave
ligada no arquivo e a fonte registrada no Keyko. Conferido em 30/08/2026: o
`journalctl` mostrava `source registrada: lucien` no mesmo minuto em que o
`status` dizia desligada.

O `status` é o comando que existe para *dar voz ao silêncio* — distinguir
"LUCIEN parou" de "não havia nada novo". Errar a primeira linha manda o operador
investigar problema que não existe.

O QUE ESTES TESTES GUARDAM
---------------------------
1. o `.env` do projeto é lido, e por **prefixo** — não por uma lista de chaves
   que fica desatualizada;
2. o **ambiente do processo vence** o arquivo (quem exportou na mão quis aquilo);
3. a saída diz **de onde** a chave veio;
4. `DATABASE_URL` **não** entra no carregamento. É a trava que impede o conserto
   de um bug de diagnóstico de virar um comando de dev acertando o banco de
   produção.

Nenhum teste aqui toca banco nenhum.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "bot" / "bin"))

_HELPER = RAIZ / "bot" / "bin" / "kobe-lucien"


def _mod():
    """Carrega `bot/bin/kobe-lucien` como módulo (o arquivo não tem extensão).

    Importar não dispara o re-exec do `_venv`: ele só sequestra quem É o
    programa em execução (garantia 4 de `bot/bin/_venv.py`), e aqui o programa é
    o pytest.
    """
    loader = importlib.machinery.SourceFileLoader("kobe_lucien_helper", str(_HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture
def projeto(tmp_path, monkeypatch):
    """Um `.env` sintético, com a raiz do projeto apontada para ele."""
    (tmp_path / ".env").write_text(
        "# comentário que não é chave\n"
        "\n"
        "DATABASE_URL=postgresql:///banco_do_arquivo\n"
        "LUCIEN_ENABLED=true\n"
        "LUCIEN_MODEL_RECONSTRUCAO='sonnet'\n"
        'LUCIEN_BATCH_MAX="40"\n'
        "OUTRA_COISA=nao_e_do_lucien\n",
        encoding="utf-8",
    )
    import _kobe_topic

    monkeypatch.setattr(_kobe_topic, "PROJECT_ROOT", tmp_path)
    for k in ("LUCIEN_ENABLED", "LUCIEN_MODEL_RECONSTRUCAO", "LUCIEN_BATCH_MAX"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


# ── O parser compartilhado ────────────────────────────────────────────────


def test_prefixo_traz_a_configuracao_inteira(projeto):
    from _kobe_topic import read_dotenv

    achado = read_dotenv(set(), prefixo="LUCIEN_")

    assert achado == {
        "LUCIEN_ENABLED": "true",
        "LUCIEN_MODEL_RECONSTRUCAO": "sonnet",  # aspas simples removidas
        "LUCIEN_BATCH_MAX": "40",               # aspas duplas removidas
    }
    assert "OUTRA_COISA" not in achado
    assert "DATABASE_URL" not in achado


def test_ambiente_do_processo_vence_o_arquivo(projeto, monkeypatch):
    from _kobe_topic import read_dotenv

    monkeypatch.setenv("LUCIEN_ENABLED", "false")
    achado = read_dotenv(set(), prefixo="LUCIEN_")

    assert achado["LUCIEN_ENABLED"] == "false"
    assert achado["LUCIEN_BATCH_MAX"] == "40"


def test_leitura_por_lista_de_chaves_continua_igual(projeto, monkeypatch):
    """O modo antigo é o caminho do `kobe-notify`/`kobe-reflect`. Não pode ter
    mudado de comportamento por causa do parâmetro novo.

    `delenv` porque a suíte inteira compartilha `os.environ`: com `DATABASE_URL`
    exportada por outro teste, o fallback pro arquivo nem chega a acontecer — e
    é justamente o fallback que este teste existe para exercitar.
    """
    from _kobe_topic import read_dotenv

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert read_dotenv({"DATABASE_URL"}) == {
        "DATABASE_URL": "postgresql:///banco_do_arquivo"
    }
    assert read_dotenv({"NAO_EXISTE"}) == {}


# ── O helper ──────────────────────────────────────────────────────────────


def test_a_chave_do_env_entra_no_ambiente_e_a_fonte_e_o_arquivo(projeto, monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_RAIZ", projeto)
    mod._FONTE.clear()

    mod._carregar_config_do_projeto()

    assert os.environ["LUCIEN_ENABLED"] == "true"
    assert os.environ["LUCIEN_MODEL_RECONSTRUCAO"] == "sonnet"
    assert str(projeto / ".env") in mod._fonte("LUCIEN_ENABLED")

    from bot import lucien as cfg

    assert cfg.habilitado() is True


def test_a_fonte_diz_ambiente_quando_veio_exportada(projeto, monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_RAIZ", projeto)
    mod._FONTE.clear()
    monkeypatch.setenv("LUCIEN_ENABLED", "false")

    mod._carregar_config_do_projeto()

    assert os.environ["LUCIEN_ENABLED"] == "false"
    assert mod._fonte("LUCIEN_ENABLED") == "ambiente do processo"


def test_sem_env_e_sem_arquivo_a_fonte_diz_que_nao_achou(tmp_path, monkeypatch):
    import _kobe_topic

    monkeypatch.setattr(_kobe_topic, "PROJECT_ROOT", tmp_path)  # sem `.env`
    monkeypatch.delenv("LUCIEN_ENABLED", raising=False)
    mod = _mod()
    monkeypatch.setattr(mod, "_RAIZ", tmp_path)
    mod._FONTE.clear()

    mod._carregar_config_do_projeto()

    fonte = mod._fonte("LUCIEN_ENABLED")
    assert "não achei" in fonte
    assert str(tmp_path) in fonte


def test_o_destino_do_banco_NAO_vem_do_arquivo(projeto, monkeypatch):
    """A trava que impede o conserto de virar outro bug.

    Carregar o `.env` inteiro faria `DATABASE_URL` aparecer sozinho — e o
    cabeçalho do helper declara, com caso real atrás, que apontar para o banco
    errado tem que custar um ato explícito.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    mod = _mod()
    monkeypatch.setattr(mod, "_RAIZ", projeto)
    mod._FONTE.clear()

    mod._carregar_config_do_projeto()

    assert "DATABASE_URL" not in os.environ

    class _Args:
        database_url = None

    with pytest.raises(SystemExit) as saiu:
        mod._url(_Args())
    assert saiu.value.code == 2
