#!/usr/bin/env python3
"""`kobe-remember` — os quatro desfechos e o texto de cada um (Highlander v3, F2).

POR QUE O TEXTO É TESTADO, E NÃO SÓ O CÓDIGO DE SAÍDA
------------------------------------------------------
Quem lê a saída deste comando é **um agente**, e o que ele faz depois depende
inteiramente das palavras que encontrar ali. A distinção que mais importa —
*"não há registro"* contra *"não deu pra saber"* — só existe se ela estiver
escrita. Foi exatamente assim que o `kobe-reflect` errou por meses: dois
desfechos diferentes no código, **um texto só na tela**, e um timeout virou a
afirmação "não há registro sobre isso".

Por isso há teste asseverando que o texto do `SEM REGISTRO` diz *"pode
afirmar"*, que o da falha diz *"NÃO conclua que não há registro"*, e que o da
menção literal manda **não costurar** os trechos numa resposta.

COMO RODAR
----------
    .venv/bin/python -m pytest tests/test_kobe_remember.py -q
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_HELPER = RAIZ / "bot" / "bin" / "kobe-remember"

from bot.search import query  # noqa: E402


def _mod():
    """Carrega `bot/bin/kobe-remember` como módulo (o arquivo não tem extensão)."""
    loader = importlib.machinery.SourceFileLoader("kobe_remember_helper", str(_HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _achado(seq=3059, cos=0.63, literal=False):
    return query.Achado(
        seq=seq,
        message_id="00000000-0000-0000-0000-000000000001",
        role="user",
        topico="Dev Kobe",
        created_at="2026-07-13 14:22:00+00",
        corpo="HAL, eu quero retornar aqui o assunto da nossa nova arquitetura de borda",
        cos=cos,
        literal=literal,
        pernas=["sentido"] + (["literal"] if literal else []),
    )


@pytest.fixture
def rodar(monkeypatch, capsys):
    """Roda o `main` com a busca dublada e devolve `(codigo, saida)`."""
    mod = _mod()
    monkeypatch.setattr(mod, "_topico_atual", lambda db: None)

    def _executar(resultado, argv=None):
        import types

        falso_db = object()
        monkeypatch.setitem(
            sys.modules,
            "bot.db",
            types.SimpleNamespace(build_client=lambda cfg: falso_db),
        )
        monkeypatch.setitem(
            sys.modules,
            "bot.config",
            types.SimpleNamespace(load_config=lambda: None),
        )
        monkeypatch.setattr(query, "buscar", lambda *a, **k: resultado)
        codigo = mod.main(argv or ["arquitetura de borda"])
        return codigo, capsys.readouterr().out

    return _executar


# ── O arquivo, antes de qualquer coisa ────────────────────────────────────


def test_o_helper_existe_e_e_executavel():
    import os

    assert _HELPER.is_file()
    assert os.access(_HELPER, os.X_OK), "o helper precisa do bit de execução"


def test_o_reexec_nao_depende_de_lista_de_dependencias():
    """A versão anterior deste teste cobrava que a LISTA de dependências do
    re-exec citasse `psycopg`, `openai` e `dotenv` — o conserto de 27/08/2026
    (F0.2), quando conferir só a primeira deixava a segunda estourar tarde.

    **A lista inteira foi aposentada em 30/08/2026 (F3)**, porque ela falhou uma
    terceira vez, em outro helper: o `kobe-recall-since` conferia `psycopg` e
    morria em `psycopg_pool`, cegando a janela de frescor de toda run em
    background. Uma lista que precisa ser mantida à mão vai ficar velha; a
    pergunta certa é *"já estou no venv do projeto?"*, e ela não tem lista.

    O que se cobra aqui agora é o oposto do que se cobrava antes: que **não**
    haja lista. A cobrança de que todo helper use `_venv.ensure()` mora em
    `tests/test_helpers_venv.py`, que vale para todos e não só para este.
    """
    texto = _HELPER.read_text(encoding="utf-8")
    assert "from _venv import ensure" in texto and "_ensure_venv(__file__)" in texto
    assert "find_spec" not in texto, "a lista de dependências voltou"


# ── Os quatro desfechos ───────────────────────────────────────────────────


def test_achou_imprime_numero_data_e_topico(rodar):
    """Os três juntos são o que torna a citação CONFERÍVEL. Faltando um, o
    operador não consegue voltar na fonte."""
    codigo, saida = rodar(query.Resultado(veredito="ACHOU", achados=[_achado()]))
    assert codigo == 0
    assert "#3059" in saida
    assert "13/07/2026" in saida
    assert "Dev Kobe" in saida
    assert "operador" in saida


def test_sem_registro_diz_que_PODE_afirmar(rodar):
    """A trava anti-invenção só funciona se o agente souber que este "não achei"
    é confiável. Sem essa frase, ele tenderia a completar de memória."""
    codigo, saida = rodar(
        query.Resultado(veredito="SEM_REGISTRO", radicais_ausentes=["salesforc"])
    )
    assert codigo == 0
    assert "SEM REGISTRO" in saida
    assert "pode afirmar" in saida.lower()
    assert "não complete de memória" in saida


def test_termo_ausente_e_reportado_como_ausente_e_nao_como_banal(rodar):
    """Dizer "Salesforce é comum demais no acervo" é o oposto exato da verdade,
    e é o tipo de frase que faz o operador desconfiar de tudo o mais."""
    _, saida = rodar(
        query.Resultado(
            veredito="SEM_REGISTRO",
            radicais_ausentes=["salesforc"],
            radicais_banais=["gent", "sobr"],
        )
    )
    assert "nunca apareceu no histórico: salesforc" in saida
    assert "comuns demais" in saida
    assert "salesforc" not in saida.split("comuns demais")[1]


def test_mencao_literal_manda_NAO_costurar(rodar):
    """`Japão` dá 7 ocorrências soltas no acervo, sobre Copa do Mundo. Sem esta
    instrução, sete menções viram uma resposta sobre uma viagem que não existe."""
    codigo, saida = rodar(
        query.Resultado(
            veredito="MENCAO_LITERAL",
            literais=["Japão"],
            achados=[_achado(cos=0.42, literal=True)],
        )
    )
    assert codigo == 0
    assert "MENÇÃO LITERAL" in saida
    assert "NÃO costure" in saida


def test_mencao_literal_NAO_afirma_que_nada_responde(rodar):
    """O overclaim que a 3ª execução da bateria pegou.

    O texto antigo dizia *"NADA no acervo responde à pergunta"* — e disse isso
    sobre um conjunto que **respondia**: a consulta "portão permanente, ordem
    física das colunas, carga posicional" trouxe `#3436`, `#3438` e `#3443`, que
    eram exatamente as mensagens certas, sob um carimbo declarando o contrário.

    O que esta ferramenta SABE é: a palavra aparece, e o sentido não passou do
    piso. Ela não sabe que nada responde. Afirmar isso é o mesmo pecado que a
    fase inteira combate, cometido pela própria fase.
    """
    _, saida = rodar(
        query.Resultado(
            veredito="MENCAO_LITERAL",
            literais=["compat_gate"],
            achados=[_achado(cos=0.52, literal=True)],
        )
    )
    assert "NADA no acervo responde" not in saida
    assert "não consigo afirmar que não respondem" in saida
    assert "LEIA e julgue" in saida
    assert "se algum deles responder, cite normalmente" in saida


def test_falha_sai_com_3_e_manda_NAO_concluir_ausencia(rodar):
    """O falso negativo silencioso, cometido duas vezes neste sistema. O código
    de saída distinto existe para quem chama por script; o texto, para quem lê."""
    codigo, saida = rodar(
        query.Resultado(veredito="FALHA", erro="connection refused")
    )
    assert codigo == 3
    assert "FALHA DO INSTRUMENTO" in saida
    assert "NÃO conclua que não há registro" in saida


# ── O caso mais perigoso: "não achei" SEM a árbitra ───────────────────────


def test_sem_registro_com_sentido_fora_e_dito_como_PARCIAL(rodar):
    """A busca por sentido é a única árbitra. Sem ela, um "não achei" não é
    ausência confirmada — e apresentá-lo como se fosse seria a mesma mentira
    que o desfecho FALHA existe pra impedir, só que mais difícil de notar."""
    codigo, saida = rodar(
        query.Resultado(
            veredito="SEM_REGISTRO",
            sentido_ativo=False,
            motivo_sentido_fora="OPENAI_API_KEY não configurada",
        )
    )
    assert codigo == 0
    assert "PARCIAL" in saida
    assert "OPENAI_API_KEY" in saida
    assert "não o apresente como ausência confirmada" in saida


def test_indice_atrasado_e_avisado(rodar):
    """Mensagem gravada há dois minutos pode não estar no índice de sentido
    ainda. Omitir isso faria o comando parecer estar dizendo mais do que sabe."""
    _, saida = rodar(
        query.Resultado(
            veredito="ACHOU",
            achados=[_achado()],
            pendencia={"trechos_sem_vetor": 12, "mensagens_sem_trecho": 3,
                       "idade_segundos": 600},
        )
    )
    assert "15 trecho(s) ainda sem vetor" in saida
    assert "10 min" in saida


def test_indice_em_dia_nao_gera_ruido(rodar):
    _, saida = rodar(
        query.Resultado(
            veredito="ACHOU",
            achados=[_achado()],
            pendencia={"trechos_sem_vetor": 0, "mensagens_sem_trecho": 0},
        )
    )
    assert "sem vetor" not in saida


# ── Uso ───────────────────────────────────────────────────────────────────


def test_sem_argumento_ensina_o_uso(capsys):
    mod = _mod()
    assert mod.main([]) == 2
    assert "Uso:" in capsys.readouterr().err


def test_o_corte_do_trecho_nao_estoura_a_tela():
    mod = _mod()
    saida = mod._cortar("x" * 5000)
    assert len(saida) <= 421
    assert saida.endswith("…")


def test_a_data_sai_no_fuso_do_operador():
    """A ponte fixa `TimeZone=UTC` na conexão de propósito, então o carimbo chega
    em UTC. Citar em UTC faz a citação NÃO BATER com o que o operador viu na
    tela — ele apontou isso durante a bateria. 14:22 UTC é 11:22 em Brasília."""
    mod = _mod()
    assert mod._data("2026-07-13T14:22:00.123+00:00") == "13/07/2026 11:22"


def test_data_vazia_ou_torta_nao_derruba_a_citacao():
    """Uma data que não parseia não pode fazer o comando morrer no meio de uma
    resposta — degrada para o texto cru."""
    mod = _mod()
    assert mod._data("") == ""
    assert mod._data("nao-e-data") == "nao-e-data"[:16]
