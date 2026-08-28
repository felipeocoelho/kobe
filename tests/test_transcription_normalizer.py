"""Bloco D do plano de testes da F0 — normalizador determinístico (E11).

O que está em jogo: este é o único componente da F0 que **reescreve o que o
operador disse**. Por isso a bateria é toda sobre os limites — o que ele NÃO
pode tocar (D4, D9), o que acontece quando está desligado (D7) ou sem glossário
(D8), e a garantia de que o original nunca se perde (D10).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot import transcription_normalizer as tn

GLOSSARIO = """# Glossário de teste

> prosa que o parser tem que ignorar

Fora da seção, esta linha `Kobi -> Kobe` é PROSA e não pode virar regra.

## Regras

Kobi -> Kobe
Cobi -> Kobe
Colby -> Kobe
Koby -> Kobe
Cloud Code -> Claude Code
Raul -> HAL
Hau -> HAL
# comentário
"""


@pytest.fixture
def home(tmp_path) -> Path:
    (tmp_path / "user-data").mkdir()
    (tmp_path / "user-data" / "transcription-glossary.md").write_text(
        GLOSSARIO, encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def regras(home):
    return tn.load_glossary(home)


def _n(texto, regras):
    return tn.normalize(texto, regras)[0]


def test_d1_variantes_do_nome_do_framework(regras):
    """D1 — as quatro grafias que o Whisper produz viram `Kobe`."""
    assert _n("o Kobi roda na VPS", regras) == "o Kobe roda na VPS"
    assert _n("o Cobi caiu", regras) == "o Kobe caiu"
    assert _n("instalei o Colby", regras) == "instalei o Kobe"
    assert _n("plugins do Koby", regras) == "plugins do Kobe"


def test_d2_cloud_code_vira_claude_code(regras):
    """D2 — regra de duas palavras, com espaço no meio."""
    assert _n("abri o Cloud Code no VS Code", regras) == "abri o Claude Code no VS Code"


def test_d3_nome_do_agente(regras):
    assert _n("fala Raul", regras) == "fala HAL"
    assert _n("o Hau respondeu", regras) == "o HAL respondeu"


def test_d4_limite_de_palavra_protege_o_que_apenas_contem_o_termo(regras):
    """D4 — a proteção mais importante: não mexer no meio de outra palavra."""
    assert _n("o sobrenome dele é Kobierski", regras) == "o sobrenome dele é Kobierski"
    assert _n("Raulzito veio", regras) == "Raulzito veio"
    assert _n("cobiça", regras) == "cobiça"


def test_d5_caixa_e_acento(regras):
    """D5 — casa "KOBI" e "cóbi"; a saída sai na grafia do glossário."""
    assert _n("o KOBI subiu", regras) == "o Kobe subiu"
    assert _n("o cóbi subiu", regras) == "o Kobe subiu"
    assert _n("O CÓBI SUBIU", regras) == "O Kobe SUBIU"


def test_d5b_o_resto_da_frase_mantem_os_acentos(regras):
    """A normalização não pode destruir o texto ao redor — ela troca palavra."""
    esperado = "à noite o Kobe não respondeu, então reiniciei a instância"
    assert _n("à noite o Kobi não respondeu, então reiniciei a instância", regras) == esperado


def test_d6_idempotente(regras):
    """D6 — rodar de novo não muda mais nada."""
    uma = _n("o Kobi e o Cloud Code", regras)
    assert _n(uma, regras) == uma == "o Kobe e o Claude Code"


def test_d7_flag_off_e_no_op(home):
    """D7 — desligado, o texto sai idêntico. Rollback é a flag."""
    texto = "o Kobi está no ar"
    assert tn.normalize_transcription(home, texto, enabled=False) == texto
    assert not (home / "user-data" / "transcription-normalizer").exists()


def test_d8_sem_glossario_e_no_op(tmp_path):
    """D8 — instalação nova (sem o arquivo): no-op, sem exceção."""
    assert tn.load_glossary(tmp_path) == []
    texto = "o Kobi está no ar"
    assert tn.normalize_transcription(tmp_path, texto, enabled=True) == texto


def test_d10_trilha_de_auditoria_guarda_o_original(home):
    """D10 — nada se perde: o original fica gravado com as regras que bateram."""
    saida = tn.normalize_transcription(
        home, "o Kobi e o Cloud Code", enabled=True, origem="telegram:42"
    )
    assert saida == "o Kobe e o Claude Code"

    trilhas = list((home / "user-data" / "transcription-normalizer").glob("*.jsonl"))
    assert len(trilhas) == 1
    registro = json.loads(trilhas[0].read_text(encoding="utf-8").strip())
    assert registro["original"] == "o Kobi e o Cloud Code"
    assert registro["normalizado"] == saida
    assert registro["origem"] == "telegram:42"
    assert {r["de"] for r in registro["regras"]} == {"Kobi", "Cloud Code"}


def test_d10b_sem_mudanca_nao_escreve_nada(home):
    """A trilha é o registro do que MUDOU — texto intacto não vira linha."""
    texto = "nada aqui bate com o glossário"
    assert tn.normalize_transcription(home, texto, enabled=True) == texto
    assert not (home / "user-data" / "transcription-normalizer").exists()


def test_glossario_so_le_a_secao_de_regras(regras):
    """Prosa com seta FORA da seção `## Regras` não vira regra.

    Bug real, achado rodando o relatório contra as 3.521 mensagens de dev: a
    prosa do próprio template explica o formato usando uma seta, e o parser
    ingênuo transformou a frase inteira em regra — inclusive aplicando-a.
    """
    assert len(regras) == 7
    assert all("pega" not in r.de for r in regras)
    assert all(r.de and r.para for r in regras)


def test_avisa_ciclo_no_glossario(tmp_path, caplog):
    """Ciclo (`a->b` e `b->a`) quebraria a idempotência: avisar > adivinhar."""
    (tmp_path / "user-data").mkdir()
    (tmp_path / "user-data" / "transcription-glossary.md").write_text(
        "## Regras\nKobi -> Kobe\nKobe -> Kobi\n", encoding="utf-8"
    )
    with caplog.at_level("WARNING"):
        tn.load_glossary(tmp_path)
    assert "idempotente" in caplog.text


def test_d9_o_normalizador_so_e_chamado_no_caminho_de_audio():
    """D9 — trava estrutural: só a função de áudio chama o normalizador.

    Se um dia alguém fiar isto no caminho de texto digitado, este teste quebra —
    e é pra quebrar: reescrever o que o operador DIGITOU é outra decisão, que
    ele não tomou.
    """
    fonte = Path(__file__).resolve().parent.parent / "bot" / "telegram_handler.py"
    linhas = fonte.read_text(encoding="utf-8").splitlines()
    chamadas = [
        i for i, l in enumerate(linhas) if "normalize_transcription(" in l and "import" not in l
    ]
    assert len(chamadas) == 1, "o normalizador deve ter exatamente um ponto de chamada"

    # E esse ponto tem que estar dentro de `_download_and_transcribe`.
    inicio = next(
        i for i, l in enumerate(linhas) if l.startswith("async def _download_and_transcribe")
    )
    fim = next(
        i for i, l in enumerate(linhas[inicio + 1 :], start=inicio + 1)
        if l.startswith("async def ") or l.startswith("def ")
    )
    assert inicio < chamadas[0] < fim
