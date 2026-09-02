#!/usr/bin/env python3
"""O boletim quente — os cinco critérios da F4, virados em teste.

O briefing da fase declarou cinco critérios de pronto, e a exigência era que
fossem provados **por teste, não por inspeção visual**. O mapa:

    1. o prompt cresce <= o orçamento          -> T-1a, T-1b
    2. o turno não ganha latência              -> T-2a (tempo), T-2b (desenho)
    3. legível, e cada linha com a origem       -> T-3
    4. ausente/vazio/corrompido não derruba     -> T-4
    5. gerar duas vezes não muda o arquivo      -> vive em test_boletim_escrita.py

O T-2b é o que não envelhece. O de tempo mede a máquina em que rodou; o
estrutural mede o **desenho** — se um dia alguém importar o banco aqui dentro,
ele acusa, mesmo numa máquina rápida o bastante para o cronômetro não notar.
"""

from __future__ import annotations

import ast
import importlib
import re
import statistics as st
import time
from pathlib import Path

import pytest

from bot.memory import boletim as B


def _linhas(n, kind="decision", texto="uma afirmação qualquer com corpo suficiente",
            base=3000):
    # `base` distinto por bloco: dois blocos com os mesmos `seq` seriam artefato
    # do fixture, e fariam o T-6c acusar uma duplicação que o código não comete.
    return [B.Linha(kind=kind, dia="28/08", texto=f"{texto} {i}", seq=base + i)
            for i in range(n)]


def _saidas(n):
    return [B.Saida(acao="superseded", dia="29/08",
                    texto=f"aquilo que deixou de valer {i}", seq=2900 + i)
            for i in range(n)]


def _completo(**kw):
    base = dict(topico="Dev Kobe", apurado_ate="30/08/2026",
                pendencias=_linhas(3, "open", base=4000), vigentes=_linhas(30),
                saiu_de_cena=_saidas(5), total_vigentes=118)
    base.update(kw)
    return B.montar(**base)


# ── 1. O orçamento ────────────────────────────────────────────────────────

def test_t1a_o_texto_gerado_respeita_o_teto():
    """Com material de sobra (30 vigentes num orçamento de ~16 linhas), o que
    sai tem que caber. É o caso normal — o registro real é maior que a vaga."""
    txt = _completo()
    assert len(txt) <= B.BOLETIM_CHAR_LIMIT, (
        f"{len(txt)} chars num teto de {B.BOLETIM_CHAR_LIMIT}")


def test_t1b_arquivo_gigante_em_disco_ainda_cabe_na_leitura(tmp_path):
    """Defesa em profundidade: o teto vale de novo na LEITURA.

    O arquivo pode ter sido editado à mão, ou ter vindo de uma versão com outro
    orçamento. O prompt não pode depender da boa conduta de um arquivo."""
    alvo = B.caminho(tmp_path, "t-1")
    alvo.parent.mkdir(parents=True)
    alvo.write_text("x" * (B.BOLETIM_CHAR_LIMIT * 20), encoding="utf-8")
    lido = B.carregar(tmp_path, "t-1")
    assert lido is not None
    assert len(lido) <= B.BOLETIM_CHAR_LIMIT + 60  # + o marcador de truncagem
    assert "truncado" in lido


def test_t1c_o_orcamento_configurado_fica_preso_na_faixa(monkeypatch):
    """Teto não-ilimitado: 20.000 tokens não é 'configuração agressiva', é o
    bloco comendo o prompt. E valor preso é valor REGISTRADO, nunca corrigido
    em silêncio."""
    monkeypatch.setenv("BOLETIM_TOKEN_BUDGET", "20000")
    monkeypatch.setenv("BOLETIM_CHARS_POR_TOKEN", "99")
    recarregado = importlib.reload(B)
    try:
        assert recarregado.BOLETIM_TOKEN_BUDGET == 1000
        assert recarregado.BOLETIM_CHARS_POR_TOKEN == 5.0
    finally:
        monkeypatch.undo()
        importlib.reload(B)


# ── 2. A latência ─────────────────────────────────────────────────────────

def test_t2a_leitura_de_arquivo_no_tamanho_maximo_e_instantanea(tmp_path):
    alvo = B.caminho(tmp_path, "t-2")
    alvo.parent.mkdir(parents=True)
    alvo.write_text("y" * B.BOLETIM_CHAR_LIMIT, encoding="utf-8")
    B.carregar(tmp_path, "t-2")  # aquece o cache de página
    tempos = []
    for _ in range(100):
        t0 = time.perf_counter()
        B.carregar(tmp_path, "t-2")
        tempos.append((time.perf_counter() - t0) * 1000)
    mediana = st.median(tempos)
    # Folga grande de propósito: o número apertado mediria a carga da máquina,
    # não o desenho. Quem mede o desenho é o T-2b.
    assert mediana < 5.0, f"mediana de {mediana:.2f} ms para ler um arquivo"


def test_t2b_o_modulo_do_turno_nao_conhece_o_banco():
    """A prova que não envelhece: o caminho quente não tem como consultar nada.

    É a garantia da direção da dependência (`lucien` -> `memory`, nunca o
    contrário) escrita como teste. Um `import psycopg` acrescentado aqui um dia
    passaria despercebido numa revisão e seria pego aqui."""
    fonte = Path(B.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    importados = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            importados.update(a.name.split(".")[0] for a in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module:
            importados.add(no.module.split(".")[0])
    assert "psycopg" not in importados
    assert "bot.db" not in fonte
    assert "openai" not in importados
    assert "httpx" not in importados

    import inspect
    assinatura = inspect.signature(B.carregar)
    assert list(assinatura.parameters) == ["kobe_home", "topic_id"], (
        "carregar() não pode receber conexão — se receber, alguém vai usá-la")


# ── 3. Legibilidade e origem ──────────────────────────────────────────────

def test_t3_toda_linha_de_afirmacao_carrega_a_origem():
    """A mitigação número um do risco da fase. Linha curada sem origem é
    julgamento de modelo servido como fato, e é inconferível."""
    txt = _completo()
    corpo = [l for l in txt.splitlines() if l.startswith("· ")]
    assert corpo, "o boletim saiu sem nenhuma linha de conteúdo"
    for linha in corpo:
        assert re.search(r"←#\d+$", linha), f"linha sem origem: {linha}"


def test_t3b_o_cabecalho_avisa_que_e_curado_e_o_rodape_declara_o_recorte():
    txt = _completo()
    assert "MODELO" in txt and "kobe-remember" in txt
    assert "recorte por recência" in txt
    assert "118" in txt, "o rodapé tem que dizer o tamanho real do acervo"
    assert "Não conclua ausência" in txt


def test_t3c_texto_muito_longo_e_cortado_para_o_bloco_seguir_plural():
    longo = "p" * 400  # o máximo que o CHECK da migration 008 permite
    txt = B.montar(topico="T", apurado_ate="30/08/2026", pendencias=[],
                   vigentes=[B.Linha("decision", "28/08", longo, 1)],
                   saiu_de_cena=[], total_vigentes=1)
    assert "…" in txt
    assert len(txt) <= B.BOLETIM_CHAR_LIMIT


# ── 4. Degradação ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("cenario", ["ausente", "vazio", "so_espaco", "bytes_torto"])
def test_t4_boletim_quebrado_nunca_derruba_o_turno(tmp_path, cenario):
    d = B.diretorio(tmp_path)
    d.mkdir(parents=True)
    alvo = B.caminho(tmp_path, "t-4")
    if cenario == "vazio":
        alvo.write_bytes(b"")
    elif cenario == "so_espaco":
        alvo.write_text("   \n\n  ", encoding="utf-8")
    elif cenario == "bytes_torto":
        alvo.write_bytes(b"\xff\xfe\x00 boletim quebrado \xc3\x28")
    assert B.carregar(tmp_path, "t-4") is None


def test_t4b_sem_topico_ou_desligado_e_no_op(tmp_path, monkeypatch):
    assert B.carregar(tmp_path, None) is None
    monkeypatch.setenv("BOLETIM_ENABLED", "false")
    assert B.carregar(tmp_path, "qualquer") is None


def test_t4c_registro_vazio_nao_vira_bloco_vazio():
    """Bloco sem conteúdo é ruído com cara de informação."""
    assert B.montar(topico="T", apurado_ate="30/08/2026", pendencias=[],
                    vigentes=[], saiu_de_cena=[], total_vigentes=0) is None


# ── 6. O corte entre blocos ───────────────────────────────────────────────

def test_t6_cada_bloco_tem_voz_mesmo_com_material_de_sobra():
    """A razão de existir reserva: com pool único ordenado por recência, uma
    rajada de decisões num dia empurraria TODA pendência aberta para fora."""
    txt = B.montar(topico="T", apurado_ate="30/08/2026",
                   pendencias=_linhas(20, "open", base=4000), vigentes=_linhas(60),
                   saiu_de_cena=_saidas(20), total_vigentes=500)
    assert "PENDÊNCIAS ABERTAS" in txt
    assert "O QUE VALE HOJE" in txt
    assert "O QUE SAIU DE CENA" in txt
    assert len(txt) <= B.BOLETIM_CHAR_LIMIT


def test_t6b_bloco_magro_cede_a_sobra_para_o_seguinte():
    """Reserva sem escorrimento desperdiça: um tópico sem pendência aberta
    deixaria 40% do bloco em branco com linha esperando vaga logo abaixo."""
    com_pendencia = B.montar(topico="T", apurado_ate="30/08/2026",
                             pendencias=_linhas(10, "open", base=4000), vigentes=_linhas(60),
                             saiu_de_cena=[], total_vigentes=500)
    sem_pendencia = B.montar(topico="T", apurado_ate="30/08/2026",
                             pendencias=[], vigentes=_linhas(60),
                             saiu_de_cena=[], total_vigentes=500)
    n_com = sum(1 for l in com_pendencia.splitlines() if l.startswith("· "))
    n_sem = sum(1 for l in sem_pendencia.splitlines() if l.startswith("· "))
    assert n_sem >= n_com - 1, "a sobra da reserva de pendências não escorreu"
    assert len(sem_pendencia) <= B.BOLETIM_CHAR_LIMIT


def test_t6c_nada_se_repete_entre_o_que_vale_e_o_que_saiu():
    """'O que vale hoje' e 'o que saiu de cena' são conjuntos disjuntos por
    construção (vigente x encerrada). Repetir gastaria orçamento duas vezes."""
    txt = _completo()
    origens = re.findall(r"←#(\d+)", txt)
    assert len(origens) == len(set(origens))


# ── 8. Vaga vazia fica vazia ──────────────────────────────────────────────

def test_t8_vaga_vazia_nao_se_preenche():
    """Regra dura, e ela contraria o instinto de 'aproveitar o espaço'.

    A pesquisa de 27/08 mede a precisão caindo (~72% -> ~57%) conforme entra
    material irrelevante. Completar o bloco com linha fora de assunto para
    'usar a vaga' não é neutro: é pior que deixar em branco."""
    txt = B.montar(topico="T", apurado_ate="30/08/2026",
                   pendencias=_linhas(2, "open", base=4000), vigentes=[],
                   saiu_de_cena=[], total_vigentes=2)
    corpo = [l for l in txt.splitlines() if l.startswith("· ")]
    assert len(corpo) == 2, "o bloco inventou linha para encher a vaga"
    assert "O QUE VALE HOJE" not in txt, "título de bloco vazio é ruído"


def test_t3d_o_rodape_nunca_conta_mais_do_que_existe():
    """Defeito real, achado no primeiro smoke contra banco de verdade
    (01/09/2026): o rodapé dizia *"3 linha(s) de 2 afirmação(ões) vigente(s)"*.

    A causa era contar TODAS as linhas contra o total de vigentes — e o terceiro
    bloco é feito de afirmações ENCERRADAS, que por definição não estão no
    acervo vigente. Um rodapé que se contradiz destrói a confiança exatamente na
    linha que existe para ser confiável."""
    txt = B.montar(topico="T", apurado_ate="31/08/2026",
                   pendencias=_linhas(1, "open", base=4000),
                   vigentes=_linhas(1),
                   saiu_de_cena=_saidas(1), total_vigentes=2)
    m = re.search(r"recorte por recência: (\d+) de (\d+)", txt)
    assert m, txt
    mostradas, total = int(m.group(1)), int(m.group(2))
    assert mostradas <= total, f"o rodapé diz {mostradas} de {total}"
    assert mostradas == 2 and total == 2
    assert "1 mudança(s) recente(s)" in txt


# ── 1 e 7: o efeito no PROMPT de verdade ─────────────────────────────────
#
# Os testes acima medem o módulo. Estes medem o que o briefing cobrou: *"o
# prompt do turno cresce ≤ o orçamento com o boletim ligado — verificado por
# teste, não por inspeção visual"*.

def _prompt(**kw):
    from bot.claude_runner import build_prompt
    base = dict(thread_id=42, history=[], new_message="e aí?")
    base.update(kw)
    return build_prompt(**base)


def test_t1d_o_prompt_cresce_no_maximo_o_orcamento():
    texto = _completo()
    sem = _prompt()
    com = _prompt(boletim=texto)
    crescimento = len(com) - len(sem)
    assert 0 < crescimento <= B.BOLETIM_CHAR_LIMIT + 2, (
        f"o prompt cresceu {crescimento} chars num teto de {B.BOLETIM_CHAR_LIMIT}")


def test_t1e_boletim_gigante_no_prompt_ainda_respeita_o_teto(tmp_path):
    """Ponta a ponta: mesmo com um arquivo absurdo em disco, o que chega ao
    prompt passou pelo teto — porque quem lê o arquivo é `carregar`, e ele corta."""
    alvo = B.caminho(tmp_path, "t-1e")
    alvo.parent.mkdir(parents=True)
    alvo.write_text("z" * (B.BOLETIM_CHAR_LIMIT * 50), encoding="utf-8")
    com = _prompt(boletim=B.carregar(tmp_path, "t-1e"))
    crescimento = len(com) - len(_prompt())
    assert crescimento <= B.BOLETIM_CHAR_LIMIT + 100


def test_t7_com_a_flag_desligada_o_prompt_e_o_de_hoje_byte_a_byte():
    """A garantia de reversão: `BOLETIM_ENABLED=false` devolve exatamente o
    comportamento anterior. `None` é o que o handler passa com a flag off."""
    assert _prompt(boletim=None) == _prompt()


def test_t7b_o_boletim_entra_depois_do_nucleo_curado():
    """A ordem se lê como frase: quem é o operador -> o que o agente sabe ->
    o que vale hoje NESTE tópico. Trocar a ordem não quebra nada e por isso
    mesmo ninguém perceberia."""
    p = _prompt(curated_core="[Núcleo curado] MARCA_NUCLEO", boletim="MARCA_BOLETIM")
    assert p.index("MARCA_NUCLEO") < p.index("MARCA_BOLETIM")


# ── A seção do CLAUDE.md ──────────────────────────────────────────────────

def test_o_claude_md_ensina_que_o_boletim_e_recorte_e_nao_dispensa_o_remember():
    """Guarda o invariante da seção, não a redação — mesmo princípio de
    `tests/test_claude_md_regra_remember.py`.

    Existe porque o `CLAUDE.md` vai passar por uma dieta (56 mil chars), e a
    linha que **não** pode se perder no enxugamento é esta: o boletim é recorte,
    e ausência nele não dispensa o `kobe-remember`. Se essa distinção sumir, o
    bloco deixa de ser um atalho e vira uma armadilha — o agente passaria a
    concluir ausência a partir de um recorte de 16 linhas."""
    texto = (Path(B.__file__).resolve().parents[2] / "CLAUDE.md").read_text("utf-8")
    secao = texto.split("## Boletim do tópico")[1].split("\n## ")[0]
    assert "RECORTE" in secao and "não índice" in secao
    assert "kobe-remember" in secao and "continua valendo" in secao
    assert "MODELO" in secao, "a linha tem que se declarar julgamento, não fala"
    assert "apurado até" in secao, "sem a data, o agente narra estado velho como novo"
