#!/usr/bin/env python3
"""O coletor incremental — as três garantias, provadas byte a byte.

Highlander v3, F1. Este arquivo é o **Bloco A** do plano de testes da fase.

O QUE PRECISA SER PROVADO, E POR QUE ASSIM
-------------------------------------------
O coletor copia de um arquivo que **está sendo escrito neste instante** por outro
processo. As três garantias que ele promete são:

1. **nunca corrompe** — a cópia para no último fim-de-linha completo;
2. **nunca duplica** — duas passadas sem novidade copiam zero bytes;
3. **nunca sobrescreve** — a escrita é sempre `append`.

Nenhuma das três se prova olhando o código: as três se provam comparando bytes.
Por isso quase toda asserção aqui é sobre `sha256` de prefixo, contagem de linhas
e `json.loads` linha a linha — e não sobre "a função retornou o que eu esperava".
Um coletor que retornasse o dicionário certo e escrevesse lixo passaria num teste
de retorno e reprovaria em todos estes.

Os transcripts de verdade são grandes, vivos e do sistema de arquivos do usuário.
Aqui a origem é um `tmp_path` que os testes controlam integralmente — a coleta
contra os transcripts reais é feita na bateria da sessão, e o resultado dela vai
no relatório. Aqui se testa a mecânica, com o relógio na mão.

    .venv/bin/python -m pytest -q tests/test_transcript_collector.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.transcripts import collector as col  # noqa: E402
from bot.transcripts import state as st  # noqa: E402


# --- utilidades ---------------------------------------------------------

def _linha(i: int, tipo: str = "assistant") -> bytes:
    """Uma linha de transcript plausível — com um `uuid` próprio, que é o que
    permite contar duplicatas sem ambiguidade."""
    return (json.dumps({
        "type": tipo,
        "uuid": f"linha-{i:05d}",
        "message": {"content": [{"type": "text", "text": f"conteúdo {i}"}]},
    }, ensure_ascii=False) + "\n").encode("utf-8")


def _sha(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


@pytest.fixture
def origem(tmp_path: Path) -> Path:
    raiz = tmp_path / "projects"
    (raiz / "-projeto-de-teste").mkdir(parents=True)
    return raiz


@pytest.fixture
def destino(tmp_path: Path) -> Path:
    return tmp_path / "colhidos"


@pytest.fixture
def sessao(origem: Path):
    """Um transcript na origem, com um `session_id` de verdade."""
    sid = str(uuid.uuid4())
    caminho = origem / "-projeto-de-teste" / f"{sid}.jsonl"
    caminho.write_bytes(b"")
    return sid, caminho


def _colhe(origem, destino, **kw):
    return col.collect_once(source_root=origem, dest_root=destino,
                            update_catalog=False, **kw)


def _dest_de(destino: Path, sid: str) -> Path:
    return destino / "-projeto-de-teste" / f"{sid}.jsonl"


# ══════════════════════════════════════════════════════════════════════════
# A1 — copiar de sala VIVA sem corromper
# ══════════════════════════════════════════════════════════════════════════

def test_copia_de_sala_viva_sem_corromper(origem, destino, sessao):
    """A sala escreve entre as passadas; o colhido tem que ficar íntegro.

    "Íntegro" aqui tem definição operacional, não impressionista: **toda** linha
    do destino é JSON válido, o arquivo termina em `\\n`, e nenhum `uuid` de
    linha aparece duas vezes. É assim que se lê um `.jsonl` — se qualquer uma
    das três falhar, o arquivo colhido é inutilizável, por mais que o tamanho
    esteja certo.
    """
    sid, src = sessao

    with src.open("ab") as fh:
        for i in range(50):
            fh.write(_linha(i))
    _colhe(origem, destino)

    # a sala continua trabalhando…
    with src.open("ab") as fh:
        for i in range(50, 120):
            fh.write(_linha(i))
    r = _colhe(origem, destino)
    assert r.ok

    dest = _dest_de(destino, sid)
    bruto = dest.read_bytes()
    assert bruto.endswith(b"\n")

    linhas = bruto.decode("utf-8").splitlines()
    assert len(linhas) == 120

    uuids = []
    for n, linha in enumerate(linhas, 1):
        obj = json.loads(linha)          # levanta se corrompeu — é a asserção
        uuids.append(obj["uuid"])
    assert len(set(uuids)) == len(uuids), "linha duplicada no destino"
    assert bruto == src.read_bytes(), "o colhido não é byte a byte igual à origem"


def test_conteudo_e_cru_nenhum_bloco_e_peneirado(origem, destino, sessao):
    """Requisito fechado do operador: `thinking` é guardado, cru.

    A cópia é byte a byte, então isto vale por construção — mas o teste existe
    pra que a construção não mude sem alguém perceber. Se algum dia entrar uma
    peneira de bloco no caminho, é aqui que ela aparece.
    """
    sid, src = sessao
    raciocinio = json.dumps({
        "type": "assistant", "uuid": "com-thinking",
        "message": {"content": [
            {"type": "thinking", "thinking": "o porquê da decisão"},
            {"type": "text", "text": "a conclusão"},
        ]},
    }, ensure_ascii=False).encode() + b"\n"
    src.write_bytes(raciocinio)

    _colhe(origem, destino)
    colhido = json.loads(_dest_de(destino, sid).read_text(encoding="utf-8"))
    tipos = [b["type"] for b in colhido["message"]["content"]]
    assert tipos == ["thinking", "text"]
    assert colhido["message"]["content"][0]["thinking"] == "o porquê da decisão"


# ══════════════════════════════════════════════════════════════════════════
# A2 e A3 — não duplica, não sobrescreve
# ══════════════════════════════════════════════════════════════════════════

def test_duas_passadas_sem_novidade_copiam_zero_bytes(origem, destino, sessao):
    """A idempotência, medida em bytes e não em "parece igual"."""
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(30)))

    r1 = _colhe(origem, destino)
    assert r1.total_copied > 0

    antes = _dest_de(destino, sid).read_bytes()
    r2 = _colhe(origem, destino)

    assert r2.total_copied == 0
    assert _dest_de(destino, sid).read_bytes() == antes
    assert _sha(_dest_de(destino, sid).read_bytes()) == _sha(antes)


def test_a_coleta_nova_e_append_puro_o_prefixo_nao_muda(origem, destino, sessao):
    """**A prova formal de que não sobrescreve.**

    Guarda-se o tamanho `N` do destino antes; depois de uma coleta nova, os
    primeiros `N` bytes têm que dar exatamente o mesmo `sha256`. Isso é mais
    forte que "o arquivo cresceu": um arquivo pode crescer e ainda assim ter
    tido o miolo reescrito. O prefixo idêntico não deixa essa porta aberta.
    """
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(40)))
    _colhe(origem, destino)

    dest = _dest_de(destino, sid)
    antes = dest.read_bytes()
    n = len(antes)

    with src.open("ab") as fh:
        for i in range(40, 90):
            fh.write(_linha(i))
    _colhe(origem, destino)

    depois = dest.read_bytes()
    assert len(depois) > n
    assert _sha(depois[:n]) == _sha(antes)


def test_dry_run_nao_escreve_nada(origem, destino, sessao):
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(10)))

    r = _colhe(origem, destino, dry_run=True)
    assert r.total_copied > 0                    # diz o que FARIA
    assert not _dest_de(destino, sid).exists()   # e não faz
    assert not (destino / st.STATE_FILENAME).exists()


# ══════════════════════════════════════════════════════════════════════════
# A4 — o corte no último fim-de-linha
# ══════════════════════════════════════════════════════════════════════════

def test_linha_incompleta_nao_e_copiada_e_depois_entra_inteira(origem, destino, sessao):
    """O cenário exato de uma coleta que cai no meio de uma escrita.

    Este é o teste que justifica a garantia nº 1. Sem o corte, o destino
    terminaria com meio objeto JSON — e `json.loads` da última linha levantaria,
    tornando o arquivo ilegível pela única via que existe pra lê-lo. Pior: a
    passada seguinte anexaria o resto da linha depois de já ter anexado o
    começo, e o pedaço partido ficaria lá pra sempre.
    """
    sid, src = sessao
    completas = b"".join(_linha(i) for i in range(5))
    parcial = _linha(5)
    src.write_bytes(completas + parcial[:20])    # linha 5 cortada no meio

    _colhe(origem, destino)
    dest = _dest_de(destino, sid)
    assert dest.read_bytes() == completas
    assert len(dest.read_text(encoding="utf-8").splitlines()) == 5

    # a sala termina de escrever a linha 5
    with src.open("ab") as fh:
        fh.write(parcial[20:])
    _colhe(origem, destino)

    linhas = dest.read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 6
    assert [json.loads(x)["uuid"] for x in linhas][-1] == "linha-00005"
    assert dest.read_bytes() == src.read_bytes()


def test_arquivo_so_com_linha_incompleta_nao_copia_nada(origem, destino, sessao):
    """Meia linha não é dado — é lixo que quebraria a leitura."""
    sid, src = sessao
    src.write_bytes(b'{"type":"assistant","uuid":"sem-fim"')

    r = _colhe(origem, destino)
    assert r.total_copied == 0
    assert not _dest_de(destino, sid).exists()


# ══════════════════════════════════════════════════════════════════════════
# A5 — quando a origem deixa de ser a mesma
# ══════════════════════════════════════════════════════════════════════════

def test_inicio_do_arquivo_mudou_preserva_o_antigo_e_recopia(origem, destino, sessao):
    """O caso que o briefing nomeia: *"se mudar, recopia inteiro em vez de
    anexar lixo"*.

    Anexar a partir do deslocamento antigo produziria um Frankenstein — metade
    do arquivo velho, metade do novo, emendados num ponto arbitrário, e nada no
    arquivo denunciando isso. O coletor detecta pelo hash do começo e recopia.

    E **preserva** o antigo em vez de apagá-lo: se a detecção estiver errada,
    nada se perdeu; se estiver certa, as duas versões ficam lá pra comparação.
    É reversibilidade aplicada a dado, e custa disco — o recurso abundante aqui.
    """
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(20)))
    _colhe(origem, destino)

    dest = _dest_de(destino, sid)
    antigo = dest.read_bytes()

    # o arquivo é OUTRO: começo diferente, e menor
    src.write_bytes(b"".join(_linha(i, tipo="user") for i in range(900, 905)))
    r = _colhe(origem, destino)

    alvo = next(s for s in r.sessions if s.session_id == sid)
    assert alvo.action == "recopied"
    assert alvo.superseded_to and alvo.superseded_to.startswith(f"{sid}.jsonl.superseded-")

    preservado = dest.parent / alvo.superseded_to
    assert preservado.is_file()
    assert preservado.read_bytes() == antigo, "o antigo não foi preservado íntegro"
    assert dest.read_bytes() == src.read_bytes(), "o novo não foi recopiado inteiro"


def test_destino_apagado_a_mao_e_recopiado_do_zero(origem, destino, sessao):
    """O estado diz "já copiei 4 KB" e o destino não existe mais.

    Continuar do deslocamento do estado deixaria um buraco no começo do arquivo
    colhido — dado faltando, sem nada indicando a falta. O destino é a verdade
    sobre quanto já foi escrito, e o estado se dobra a ele.
    """
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(30)))
    _colhe(origem, destino)

    dest = _dest_de(destino, sid)
    original = dest.read_bytes()
    dest.unlink()                      # alguém apagou

    _colhe(origem, destino)
    assert dest.read_bytes() == original


def test_arquivo_recriado_com_mesmo_inicio_mas_inode_novo(origem, destino, sessao):
    """Rotação de arquivo: mesmo começo, inode diferente.

    O hash do começo sozinho não pegaria este caso — daí os três sinais.
    """
    sid, src = sessao
    conteudo = b"".join(_linha(i) for i in range(20))
    src.write_bytes(conteudo)
    _colhe(origem, destino)

    novo = src.with_suffix(".novo")
    novo.write_bytes(conteudo + b"".join(_linha(i) for i in range(20, 25)))
    novo.replace(src)                  # inode trocou, começo igual

    r = _colhe(origem, destino)
    alvo = next(s for s in r.sessions if s.session_id == sid)
    assert alvo.action == "recopied"
    assert _dest_de(destino, sid).read_bytes() == src.read_bytes()


# ══════════════════════════════════════════════════════════════════════════
# A7 — a trava
# ══════════════════════════════════════════════════════════════════════════

def test_duas_passadas_simultaneas_a_segunda_desiste(origem, destino, sessao):
    """Não-bloqueante de propósito: quem chama é um relógio, e relógio que
    espera acumula fila. A passada que já está rodando faz o mesmo trabalho."""
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(10)))

    with st.exclusive_lock(destino):
        with pytest.raises(st.LockBusy):
            _colhe(origem, destino)

    # solta a trava e a coleta volta a funcionar
    r = _colhe(origem, destino)
    assert r.total_copied > 0


def test_a_trava_nao_e_o_arquivo_de_estado(destino):
    """A trava mora num arquivo separado.

    O estado é reescrito por substituição atômica (`rename`), e travar um
    arquivo que vai ser substituído trava um inode que deixou de ser o arquivo —
    duas passadas achariam que têm a trava, em silêncio.
    """
    with st.exclusive_lock(destino):
        pass
    assert (destino / st.LOCK_FILENAME).exists()
    assert st.LOCK_FILENAME != st.STATE_FILENAME


# ══════════════════════════════════════════════════════════════════════════
# Descoberta e estado
# ══════════════════════════════════════════════════════════════════════════

def test_descobre_so_arquivos_nomeados_por_session_id(origem, destino):
    projeto = origem / "-projeto-de-teste"
    sid = str(uuid.uuid4())
    (projeto / f"{sid}.jsonl").write_bytes(_linha(1))
    (projeto / "anotacoes.jsonl").write_bytes(_linha(2))
    (projeto / "outro.txt").write_bytes(b"nada")

    achados = col.discover(origem)
    assert [t.session_id for t in achados] == [sid]


def test_colhe_sala_morta_do_mesmo_jeito_que_viva(origem, destino):
    """*"viva ou morta, aberta ou fechada"* — e sem consultar o catálogo.

    As ~24 salas anteriores à F1 não têm linha nenhuma, e são justamente as que
    estão prestes a expirar. Se a coleta dependesse do catálogo, elas se
    perderiam — o que é o oposto do motivo pelo qual esta fase existe.
    """
    projeto = origem / "-projeto-de-teste"
    mortas = [str(uuid.uuid4()) for _ in range(3)]
    for sid in mortas:
        (projeto / f"{sid}.jsonl").write_bytes(b"".join(_linha(i) for i in range(4)))

    r = _colhe(origem, destino)
    assert sorted(s.session_id for s in r.touched) == sorted(mortas)


def test_filtro_por_session_aceita_o_short_id(origem, destino):
    projeto = origem / "-projeto-de-teste"
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    for sid in (a, b):
        (projeto / f"{sid}.jsonl").write_bytes(_linha(1))

    r = _colhe(origem, destino, only=[a[:8]])
    assert [s.session_id for s in r.touched] == [a]


def test_estado_corrompido_nao_derruba_o_coletor(origem, destino, sessao):
    """O pior caso de um estado ilegível é recopiar — caro em disco, barato em
    consequência, porque a recópia preserva. O pior caso de ABORTAR seria parar
    de colher, e aí o dado perecível continua evaporando."""
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(10)))
    _colhe(origem, destino)

    (destino / st.STATE_FILENAME).write_text("{isto não é json", encoding="utf-8")
    r = _colhe(origem, destino)
    assert r.ok
    assert _dest_de(destino, sid).read_bytes() == src.read_bytes()


def test_estado_e_gravado_por_substituicao_atomica(origem, destino, sessao):
    sid, src = sessao
    src.write_bytes(_linha(1))
    _colhe(origem, destino)

    estado = json.loads((destino / st.STATE_FILENAME).read_text(encoding="utf-8"))
    assert estado["version"] == st.STATE_VERSION
    assert estado["last_success_at"]
    chave = f"-projeto-de-teste/{sid}"
    assert estado["sessions"][chave]["bytes_copied"] == len(_linha(1))
    # nenhum temporário deixado pra trás
    assert not list(destino.glob(".collector-state-*"))


# ══════════════════════════════════════════════════════════════════════════
# A6 — a marca do relógio (mitigação da lacuna L4)
# ══════════════════════════════════════════════════════════════════════════

def test_nunca_ter_rodado_conta_como_envelhecido(destino):
    """Um coletor ligado que nunca coletou é exatamente o caso que esta marca
    existe pra pegar. Tratar "nunca" como "em dia" seria o silêncio de volta."""
    info = col.staleness(destino)
    assert info["stale"] is True
    assert info["last_success_at"] is None
    assert "NUNCA" in col.staleness_warning(destino)


def test_coleta_bem_sucedida_zera_o_envelhecimento(origem, destino, sessao):
    sid, src = sessao
    src.write_bytes(_linha(1))
    _colhe(origem, destino)

    info = col.staleness(destino)
    assert info["stale"] is False
    assert info["age_hours"] < 1
    assert col.staleness_warning(destino) is None


def test_execucao_antiga_dispara_o_aviso(origem, destino, sessao):
    """A falha de agendamento — a lacuna L4 — vira algo que se pode VER.

    Um coletor que para de rodar não produz erro nenhum: produz silêncio, e
    silêncio é indistinguível de "não havia nada novo". A marca de execução é o
    que transforma isso em observável.
    """
    sid, src = sessao
    src.write_bytes(_linha(1))
    _colhe(origem, destino)

    estado = st.load(destino)
    estado["last_success_at"] = "2026-08-26T03:00:00+00:00"   # 3 dias antes
    st.save(destino, estado)

    info = col.staleness(destino)
    assert info["stale"] is True
    aviso = col.staleness_warning(destino)
    assert "não conclui uma coleta há" in aviso
    assert "expiram em 30 dias" in aviso


def test_tentativa_e_exito_sao_campos_diferentes(destino):
    """Se os dois envelhecem juntos, o relógio parou; se só o de êxito envelhece,
    o relógio bate e o coletor falha. Um campo só não distinguiria."""
    estado = st.empty_state()
    st.mark_run(estado, success=False)
    assert estado["last_run_at"] is not None
    assert estado["last_success_at"] is None

    st.mark_run(estado, success=True)
    assert estado["last_success_at"] is not None


# ══════════════════════════════════════════════════════════════════════════
# Conformidade
# ══════════════════════════════════════════════════════════════════════════

def test_coletor_nasce_desligado(monkeypatch):
    """A chave é o rollback nomeado no briefing.

    E ela importa mesmo o coletor sendo inofensivo: `~/.claude/projects` é do
    HOST e é **um só**. Dev e produção colhendo juntos dariam duas verdades e o
    dobro do disco. Quem colhe de verdade é a produção.
    """
    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "false")
    assert col.collector_enabled() is False
    monkeypatch.setenv("TRANSCRIPT_COLLECTOR_ENABLED", "true")
    assert col.collector_enabled() is True


def test_o_coletor_nunca_escreve_na_origem(origem, destino, sessao):
    """A origem é do Claude Code, não nossa. Uma escrita ali — mesmo bem
    intencionada — poderia corromper a sessão de quem está trabalhando."""
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(15)))
    antes = (src.read_bytes(), src.stat().st_mtime_ns, src.stat().st_ino)

    _colhe(origem, destino)
    _colhe(origem, destino)

    depois = (src.read_bytes(), src.stat().st_mtime_ns, src.stat().st_ino)
    assert antes == depois


def test_o_codigo_do_coletor_nao_tem_unlink_nem_wb():
    """Rede contra regressão de intenção.

    As garantias "nunca sobrescreve" e "preserva em vez de apagar" são fáceis de
    quebrar sem querer num refactor — basta trocar `'ab'` por `'wb'` ou "mover"
    por "remover", e todos os testes de conteúdo acima continuariam passando na
    maior parte dos cenários. Este teste lê o próprio arquivo do coletor.
    """
    fonte = (RAIZ / "bot" / "transcripts" / "collector.py").read_text(encoding="utf-8")
    corpo = "\n".join(
        linha for linha in fonte.splitlines()
        if not linha.lstrip().startswith("#")
    )
    assert ".unlink(" not in corpo, "o coletor não apaga arquivo — ele preserva"
    assert '"wb"' not in corpo and "'wb'" not in corpo, "a escrita é sempre append"
    assert 'open("ab")' in corpo


def test_estado_perdido_com_destino_valido_nao_duplica(origem, destino, sessao):
    """**Perder o estado não pode duplicar o acervo.**

    Este teste existe porque a primeira versão do coletor duplicava: com o
    estado zerado, `bytes_copied` voltava a 0 e o arquivo inteiro era ANEXADO
    por cima do que já estava lá — a escrita é append, então "recopiar" ali não
    substituía, dobrava. O destino ficava com o dobro do tamanho e cada linha
    duas vezes, e nada no arquivo denunciava.

    O conserto é reconstruir o deslocamento a partir do próprio destino, quando
    ele é prefixo byte a byte da origem.
    """
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(30)))
    _colhe(origem, destino)

    dest = _dest_de(destino, sid)
    tamanho_certo = dest.stat().st_size

    (destino / st.STATE_FILENAME).unlink()      # o estado se perdeu
    with src.open("ab") as fh:                  # e a sala escreveu mais
        for i in range(30, 40):
            fh.write(_linha(i))

    _colhe(origem, destino)

    linhas = dest.read_text(encoding="utf-8").splitlines()
    uuids = [json.loads(x)["uuid"] for x in linhas]
    assert len(uuids) == 40, f"esperava 40 linhas, veio {len(uuids)}"
    assert len(set(uuids)) == 40, "linha duplicada — o acervo foi corrompido"
    assert dest.read_bytes() == src.read_bytes()
    assert dest.stat().st_size > tamanho_certo


def test_estado_perdido_com_destino_de_outro_arquivo_preserva(origem, destino, sessao):
    """E se o destino NÃO for prefixo da origem, não dá pra continuar dali —
    aí vale o caminho de sempre: preserva o antigo e recopia."""
    sid, src = sessao
    src.write_bytes(b"".join(_linha(i) for i in range(20)))
    _colhe(origem, destino)

    dest = _dest_de(destino, sid)
    intruso = b"".join(_linha(i, tipo="user") for i in range(500, 520))
    dest.write_bytes(intruso)                   # destino de outra procedência
    (destino / st.STATE_FILENAME).unlink()

    r = _colhe(origem, destino)
    alvo = next(s for s in r.sessions if s.session_id == sid)
    assert alvo.action == "recopied"
    preservado = dest.parent / alvo.superseded_to
    assert preservado.read_bytes() == intruso   # nada se perdeu
    assert dest.read_bytes() == src.read_bytes()
