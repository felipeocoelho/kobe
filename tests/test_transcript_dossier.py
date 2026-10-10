#!/usr/bin/env python3
"""O dossiê por sala — legível ANTES de a sala fechar.

Highlander v3, F1, **Bloco D** do plano de testes.

O critério de pronto da fase pede, com essas palavras, que o `dossier.md` seja
*"legível **antes** de a sala fechar"*. Isso não é um detalhe de conveniência: é
a decisão E3 do briefing (*"nenhuma peça pode ter 'fechar a sala' como
gatilho"*) aplicada ao artefato mais visível da fase. Uma sala que morre de
forma feia — cota estourada, crash, OOM — não pode levar junto o registro do que
fez, e é exatamente isso que acontece quando o resumo só é gerado no fim.

O outro tema destes testes é **procedência**. O dossiê é determinístico de
propósito: cada seção sai de uma fonte literal (as mensagens que a própria sala
mandou, as caixas do plano, os arquivos que ela escreveu), nunca de uma
interpretação. A missão inteira existe pra curar a dor de tratar texto plausível
como fato — seria irônico o artefato dela inventar o resumo.

    .venv/bin/python -m pytest -q tests/test_transcript_dossier.py
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from bot.transcripts import dossier  # noqa: E402


def _l(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _assistente(*blocos, ts="2026-08-29T20:00:00.000Z"):
    return _l({"type": "assistant", "timestamp": ts, "cwd": "/w",
               "message": {"content": list(blocos)}})


def _bash(comando: str):
    return {"type": "tool_use", "name": "Bash", "input": {"command": comando}}


def _write(caminho: str, conteudo: str):
    return {"type": "tool_use", "name": "Write",
            "input": {"file_path": caminho, "content": conteudo}}


def _edit(caminho: str, antigo: str, novo: str, todas: bool = False):
    return {"type": "tool_use", "name": "Edit",
            "input": {"file_path": caminho, "old_string": antigo,
                      "new_string": novo, "replace_all": todas}}


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    p = tmp_path / f"{uuid.uuid4()}.jsonl"
    p.write_bytes(b"")
    return p


# ══════════════════════════════════════════════════════════════════════════
# D1 — legível antes de fechar
# ══════════════════════════════════════════════════════════════════════════

def test_sala_viva_gera_dossie_com_status_em_andamento(tmp_path, transcript):
    """`running` e `idle` viram "em andamento"; `closed` e `dead`, "concluída".

    O rótulo é traduzido em vez de repetido porque quem lê o dossiê é gente, e
    "dead" não quer dizer pra um humano o que quer dizer pra o dispatcher: uma
    sala morta pode ter entregado tudo antes de morrer.
    """
    transcript.write_bytes(_assistente({"type": "text", "text": "oi"}))
    sid = transcript.stem

    for bruto, esperado in [("running", "em andamento"), ("idle", "em andamento"),
                            ("closed", "concluída"), ("dead", "concluída")]:
        p = dossier.gerar(session_id=sid, transcript=transcript,
                          catalogo={"status": bruto, "system_name": "Kobe"})
        assert f"status: {esperado}" in p.read_text(encoding="utf-8")


def test_dossie_sai_mesmo_sem_catalogo(tmp_path, transcript):
    """Uma sala anterior à F1 não tem linha nenhuma — e mesmo assim tem
    transcript pra salvar e dossiê pra ler. O catálogo é enriquecimento."""
    transcript.write_bytes(_assistente({"type": "text", "text": "oi"}))
    p = dossier.gerar(session_id=transcript.stem, transcript=transcript)
    texto = p.read_text(encoding="utf-8")
    assert "status: em andamento" in texto
    assert "(não catalogada)" in texto


def test_dossie_e_regenerado_por_acumulo_sem_duplicar(tmp_path, transcript):
    """**D2.** Regenerar depois de mais atividade acumula, não repete.

    A regeneração reescreve o arquivo inteiro, e isso é seguro porque o dossiê é
    DERIVADO: a fonte de verdade é o `.jsonl` ao lado, que é append-only e nunca
    é tocado. Perder um dossiê custa uma regeração; duplicar conteúdo dentro
    dele custaria a confiança em tudo que ele diz.
    """
    transcript.write_bytes(_assistente(_bash('kobe-notify "primeiro marco"')))
    sid = transcript.stem
    p = dossier.gerar(session_id=sid, transcript=transcript)
    assert p.read_text(encoding="utf-8").count("primeiro marco") == 1

    with transcript.open("ab") as fh:
        fh.write(_assistente(_bash('kobe-notify "segundo marco"')))
    p = dossier.gerar(session_id=sid, transcript=transcript)

    texto = p.read_text(encoding="utf-8")
    assert texto.count("primeiro marco") == 1, "o marco antigo foi duplicado"
    assert texto.count("segundo marco") == 1
    assert texto.index("primeiro marco") < texto.index("segundo marco")


# ══════════════════════════════════════════════════════════════════════════
# Procedência — cada seção vem de uma fonte literal
# ══════════════════════════════════════════════════════════════════════════

def test_marcos_saem_das_mensagens_que_a_sala_mandou(tmp_path, transcript):
    """As mensagens do `kobe-notify` SÃO os marcos — não uma leitura deles.

    Por construção do rito do Coder, a sala é obrigada a anunciar cada marco,
    bloqueio e conclusão por esse canal. Então a lista mais fiel de decisões que
    existe já está escrita, pela própria sala, no momento em que aconteceram.
    """
    transcript.write_bytes(
        _assistente(_bash('bot/bin/kobe-notify "✅ [coder] migration aplicada"'))
        + _assistente(_bash("git status"))          # não é marco
        + _assistente(_bash("$KOBE_HOME/bot/bin/kobe-notify '🟡 [coder] travei em X'"))
    )
    p = dossier.gerar(session_id=transcript.stem, transcript=transcript)
    texto = p.read_text(encoding="utf-8")
    assert "✅ [coder] migration aplicada" in texto
    assert "🟡 [coder] travei em X" in texto
    assert "git status" not in texto


def test_o_que_entrou_na_sala_nao_e_rotulado_como_fala_do_operador(tmp_path, transcript):
    """**A honestidade que este dossiê se obriga a ter.**

    Numa sala do Coder, a fala do operador e os prompts injetados pelo sistema
    chegam ao transcript do mesmo jeito — linhas `user` —, e não há como
    separá-los com confiança. Rotular texto de máquina como fala do operador
    criaria exatamente o tipo de falso que a F3 vai ter de desfazer depois. A
    seção diz o que pode ser dito: *o que entrou na sala*.
    """
    transcript.write_bytes(_l({
        "type": "user", "timestamp": "2026-08-29T20:00:00Z",
        "message": {"content": "arruma o gate do plano"},
    }))
    texto = dossier.gerar(session_id=transcript.stem,
                          transcript=transcript).read_text(encoding="utf-8")
    assert "## O que entrou na sala" in texto
    assert "arruma o gate do plano" in texto

    # A asserção é sobre os TÍTULOS de seção, não sobre o texto inteiro: a nota
    # explicativa do dossiê usa a frase de propósito, pra dizer que NÃO é isso
    # ("a seção diz o que entrou na sala, e não o que o operador disse").
    titulos = [l for l in texto.splitlines() if l.startswith("## ")]
    assert not any("operador" in t.lower() for t in titulos), titulos
    assert "não há como separá-los com confiança" in texto


def test_lembretes_de_sistema_nao_viram_pedido(tmp_path, transcript):
    """`<system-reminder>` é ruído de plataforma injetado no meio do texto.
    Contá-lo como pedido encheria o dossiê de coisa que ninguém pediu."""
    transcript.write_bytes(_l({
        "type": "user",
        "message": {"content": [
            {"type": "text",
             "text": "faz X\n<system-reminder>lembre de Y</system-reminder>"},
        ]},
    }))
    texto = dossier.gerar(session_id=transcript.stem,
                          transcript=transcript).read_text(encoding="utf-8")
    assert "faz X" in texto
    assert "lembre de Y" not in texto


def test_pendencias_saem_das_caixas_nao_marcadas_do_plano(tmp_path, transcript):
    """"O que ficou aberto" é literal: a lista que a sala escreveu e foi
    marcando. Não é inferência sobre o que ela fez."""
    cwd = tmp_path / "trabalho"
    (cwd / ".local").mkdir(parents=True)
    plano = cwd / ".local" / "plano-x.md"
    conteudo = "# Plano\n- [x] feito\n- [ ] falta isto\n- [ ] e isto\n"
    plano.write_text(conteudo, encoding="utf-8")
    # O plano conta porque a SALA o escreveu — está no transcript dela.
    transcript.write_bytes(_assistente(_write(str(plano), conteudo)))

    texto = dossier.gerar(
        session_id=transcript.stem, transcript=transcript,
        catalogo={"status": "running", "cwd": str(cwd)},
    ).read_text(encoding="utf-8")

    assert "**1 de 3** concluídos" in texto
    assert "- [ ] falta isto" in texto
    assert "- [ ] e isto" in texto


def test_sem_plano_o_dossie_diz_que_nao_achou(tmp_path, transcript):
    """Ausência declarada, não seção vazia — quem lê tem que saber que a
    informação não existe, e não que ela é "nenhuma"."""
    transcript.write_bytes(_assistente({"type": "text", "text": "."}))
    texto = dossier.gerar(
        session_id=transcript.stem, transcript=transcript,
        catalogo={"cwd": str(tmp_path / "vazio")},
    ).read_text(encoding="utf-8")
    assert "Nenhum plano identificado para esta sala" in texto


def test_produzidos_saem_de_arquivos_escritos_e_commits(tmp_path, transcript):
    transcript.write_bytes(
        _assistente({"type": "tool_use", "name": "Write",
                     "input": {"file_path": "/w/a.py"}})
        + _assistente({"type": "tool_use", "name": "Edit",
                       "input": {"file_path": "/w/a.py"}})   # mesmo arquivo
        + _assistente({"type": "tool_use", "name": "Edit",
                       "input": {"file_path": "/w/b.py"}})
        + _assistente(_bash('git commit -m "feat: coisa nova\n\ncorpo"'))
    )
    texto = dossier.gerar(session_id=transcript.stem,
                          transcript=transcript).read_text(encoding="utf-8")
    assert "Arquivos escritos ou editados (2)" in texto   # sem repetir a.py
    assert "feat: coisa nova" in texto
    assert "corpo" not in texto                            # só a 1ª linha


def test_blocos_thinking_sao_contados(tmp_path, transcript):
    """O raciocínio é guardado cru no `.jsonl`; no dossiê entra a CONTAGEM.

    O dossiê é índice, não segunda cópia — e despejar o raciocínio inteiro aqui
    o tornaria ilegível justamente pra quem quer saber o que aconteceu.
    """
    transcript.write_bytes(
        _assistente({"type": "thinking", "thinking": "por que X"},
                    {"type": "text", "text": "faço X"})
    )
    texto = dossier.gerar(session_id=transcript.stem,
                          transcript=transcript).read_text(encoding="utf-8")
    assert "| blocos de raciocínio | 1 |" in texto
    assert "por que X" not in texto


# ══════════════════════════════════════════════════════════════════════════
# Robustez — o dossiê é gerado sobre um arquivo VIVO
# ══════════════════════════════════════════════════════════════════════════

def test_linha_corrompida_nao_impede_o_dossie(tmp_path, transcript):
    """Deixar de gerar o dossiê por uma linha torta trocaria um artefato quase
    completo por nenhum. A linha é contada e o dossiê sai."""
    transcript.write_bytes(
        _assistente(_bash('kobe-notify "marco antes"'))
        + b"{isto nao e json\n"
        + _assistente(_bash('kobe-notify "marco depois"'))
    )
    texto = dossier.gerar(session_id=transcript.stem,
                          transcript=transcript).read_text(encoding="utf-8")
    assert "marco antes" in texto and "marco depois" in texto
    assert "| linhas ilegíveis | 1 |" in texto


def test_transcript_vazio_ou_ausente_nao_levanta(tmp_path):
    ausente = tmp_path / f"{uuid.uuid4()}.jsonl"
    p = dossier.gerar(session_id=ausente.stem, transcript=ausente)
    assert p.is_file()
    assert "0 linhas" in p.read_text(encoding="utf-8")


def test_notify_sem_aspas_reconheciveis_mostra_o_comando_e_nao_inventa(tmp_path, transcript):
    """Quando o desembrulho do shell não é seguro, mostrar o comando cru é feio;
    inventar a mensagem seria pior — e é o erro que esta missão existe pra
    combater."""
    transcript.write_bytes(_assistente(_bash("kobe-notify $MENSAGEM")))
    texto = dossier.gerar(session_id=transcript.stem,
                          transcript=transcript).read_text(encoding="utf-8")
    assert "$MENSAGEM" in texto


def test_o_dossie_nunca_toca_o_transcript(tmp_path, transcript):
    transcript.write_bytes(_assistente({"type": "text", "text": "."}))
    antes = (transcript.read_bytes(), transcript.stat().st_mtime_ns)
    dossier.gerar(session_id=transcript.stem, transcript=transcript)
    dossier.gerar(session_id=transcript.stem, transcript=transcript)
    assert (transcript.read_bytes(), transcript.stat().st_mtime_ns) == antes


def test_o_nome_do_dossie_fica_ao_lado_do_transcript(tmp_path, transcript):
    esperado = transcript.with_name(transcript.stem + ".dossier.md")
    assert dossier.dossier_path_for(transcript) == esperado


# ══════════════════════════════════════════════════════════════════════════
# D5 — o plano é o que a PRÓPRIA sala escreveu (Etapa 0, 07/10/2026)
#
# Até 07/10 o plano vinha do disco: "o `.local/plano-*.md` mais recente da pasta
# de trabalho". Salas com pasta na raiz dividem um `.local/` só — e 742 de 797
# dossiês de produção citavam um plano que a sala nunca escreveu.
# ══════════════════════════════════════════════════════════════════════════

def _gera(transcript, cwd) -> str:
    return dossier.gerar(
        session_id=transcript.stem, transcript=transcript,
        catalogo={"status": "running", "cwd": str(cwd)},
    ).read_text(encoding="utf-8")


def test_plano_de_outra_sala_na_pasta_compartilhada_nao_e_atribuido(tmp_path, transcript):
    """O BUG. Uma sala que não escreveu plano nenhum, com pasta na raiz onde
    OUTRA sala deixou o dela. No código antigo isto citava o plano alheio."""
    cwd = tmp_path / "raiz"
    (cwd / ".local").mkdir(parents=True)
    (cwd / ".local" / "plano-de-outra-sala.md").write_text(
        "- [ ] item que não é desta sala\n", encoding="utf-8")
    transcript.write_bytes(_assistente({"type": "text", "text": "."}))

    texto = _gera(transcript, cwd)
    assert "plano-de-outra-sala" not in texto
    assert "item que não é desta sala" not in texto
    assert "Nenhum plano identificado para esta sala" in texto


def test_com_dois_planos_na_pasta_vale_o_que_a_sala_escreveu_nao_o_mais_recente(
        tmp_path, transcript):
    cwd = tmp_path / "raiz"
    (cwd / ".local").mkdir(parents=True)
    meu = cwd / ".local" / "plano-meu.md"
    meu.write_text("- [ ] meu item\n", encoding="utf-8")
    import os, time
    alheio = cwd / ".local" / "plano-alheio.md"
    alheio.write_text("- [ ] item alheio\n", encoding="utf-8")
    futuro = time.time() + 3600
    os.utime(alheio, (futuro, futuro))  # o alheio é o MAIS RECENTE
    transcript.write_bytes(_assistente(_write(str(meu), "- [ ] meu item\n")))

    texto = _gera(transcript, cwd)
    assert "plano-meu.md" in texto and "meu item" in texto
    assert "plano-alheio" not in texto and "item alheio" not in texto


def test_caminho_relativo_e_resolvido_contra_o_cwd_da_sala(tmp_path, transcript):
    cwd = tmp_path / "w"
    (cwd / ".local").mkdir(parents=True)
    (cwd / ".local" / "plano-r.md").write_text("- [x] a\n- [ ] b\n", encoding="utf-8")
    linha = json.dumps({"type": "assistant", "cwd": str(cwd), "message": {"content": [
        _write(".local/plano-r.md", "- [x] a\n- [ ] b\n")]}}) + "\n"
    transcript.write_text(linha, encoding="utf-8")

    texto = _gera(transcript, cwd)
    assert f"`{cwd}/.local/plano-r.md`" in texto
    assert "**1 de 2** concluídos" in texto


def test_disco_traz_as_marcacoes_mais_recentes_do_mesmo_plano(tmp_path, transcript):
    """Se o arquivo em disco tem os MESMOS itens, ele vale — é o estado mais
    atual (a sala pode ter marcado caixa por `sed`, que o transcript não replica)."""
    cwd = tmp_path / "w"
    (cwd / ".local").mkdir(parents=True)
    p = cwd / ".local" / "plano-m.md"
    p.write_text("- [x] a\n- [x] b\n", encoding="utf-8")
    transcript.write_bytes(_assistente(_write(str(p), "- [ ] a\n- [ ] b\n")))

    texto = _gera(transcript, cwd)
    assert "**2 de 2** concluídos" in texto
    assert "reconstruído" not in texto


def test_arquivo_sumido_e_reconstruido_do_transcript(tmp_path, transcript):
    """A worktree é apagada depois do merge — e o plano ia junto. As escritas
    estão no transcript, então o plano volta reaplicando-as em ordem."""
    caminho = str(tmp_path / "wt-apagada" / ".local" / "plano-z.md")
    transcript.write_bytes(
        _assistente(_write(caminho, "# P\n- [ ] um\n- [ ] dois\n- [ ] três\n"))
        + _assistente(_edit(caminho, "- [ ] um", "- [x] um"))
        + _assistente(_edit(caminho, "- [ ] dois", "- [x] dois"))
    )
    texto = _gera(transcript, tmp_path)
    assert "**2 de 3** concluídos" in texto
    assert "- [ ] três" in texto
    assert "reconstruído do transcript" in texto


def test_multiedit_e_replace_all_sao_reaplicados(tmp_path, transcript):
    caminho = str(tmp_path / "sumiu" / ".local" / "plano-me.md")
    multi = {"type": "tool_use", "name": "MultiEdit", "input": {
        "file_path": caminho, "edits": [
            {"old_string": "- [ ] a", "new_string": "- [x] a"},
            {"old_string": "TODO", "new_string": "feito", "replace_all": True}]}}
    transcript.write_bytes(
        _assistente(_write(caminho, "- [ ] a TODO\n- [ ] b TODO\n"))
        + _assistente(multi))
    texto = _gera(transcript, tmp_path)
    assert "**1 de 2** concluídos" in texto
    assert "- [ ] b feito" in texto


def test_edicao_que_nao_casa_nao_vira_meio_plano(tmp_path, transcript):
    """Se uma edição não casa, o arquivo foi mexido por fora do que o transcript
    registra — a partir daí qualquer texto seria palpite. Diz isso."""
    caminho = str(tmp_path / "sumiu" / ".local" / "plano-q.md")
    transcript.write_bytes(
        _assistente(_write(caminho, "- [ ] a\n"))
        + _assistente(_edit(caminho, "- [ ] TRECHO QUE NÃO EXISTE", "x")))
    texto = _gera(transcript, tmp_path)
    assert "plano-q.md" in texto
    assert "não dá pra reconstruí-lo" in texto
    assert "concluídos" not in texto


def test_nome_reaproveitado_por_outra_sala_usa_a_versao_desta(tmp_path, transcript):
    """Mesmo nome, outra sala sobrescreveu depois: os itens do disco não são os
    que esta sala escreveu. Vale a reconstrução, e o dossiê diz por quê."""
    cwd = tmp_path / "raiz"
    (cwd / ".local").mkdir(parents=True)
    p = cwd / ".local" / "plano-v1.md"
    p.write_text("- [ ] item da OUTRA sala\n", encoding="utf-8")
    transcript.write_bytes(_assistente(_write(str(p), "- [x] meu 1\n- [ ] meu 2\n")))

    texto = _gera(transcript, cwd)
    assert "item da OUTRA sala" not in texto
    assert "- [ ] meu 2" in texto
    assert "já não é o que esta sala escreveu" in texto


def test_plano_escrito_por_shell_e_atribuido_e_lido_do_disco(tmp_path, transcript):
    cwd = tmp_path / "w"
    (cwd / ".local").mkdir(parents=True)
    (cwd / ".local" / "plano-sh.md").write_text("- [ ] via shell\n", encoding="utf-8")
    linha = json.dumps({"type": "assistant", "cwd": str(cwd), "message": {"content": [
        _bash("cat > .local/plano-sh.md <<'EOF'\n- [ ] via shell\nEOF")]}}) + "\n"
    transcript.write_text(linha, encoding="utf-8")

    texto = _gera(transcript, cwd)
    assert "- [ ] via shell" in texto


def test_plano_por_shell_sumido_diz_que_nao_reconstroi(tmp_path, transcript):
    linha = json.dumps({"type": "assistant", "cwd": str(tmp_path / "x"), "message": {
        "content": [_bash("cat > .local/plano-sh.md <<'EOF'\n- [ ] a\nEOF")]}}) + "\n"
    transcript.write_text(linha, encoding="utf-8")
    texto = _gera(transcript, tmp_path)
    assert "plano-sh.md" in texto
    assert "comando de shell" in texto


def test_dois_planos_da_sala_vale_o_ultimo_tocado_e_os_outros_sao_citados(
        tmp_path, transcript):
    a = str(tmp_path / "s" / ".local" / "plano-a.md")
    b = str(tmp_path / "s" / ".local" / "plano-b.md")
    transcript.write_bytes(
        _assistente(_write(a, "- [ ] A1\n"))
        + _assistente(_write(b, "- [ ] B1\n"))
        + _assistente(_edit(a, "- [ ] A1", "- [x] A1")))  # A foi o último tocado
    texto = _gera(transcript, tmp_path)
    assert f"Plano: `{a}`" in texto
    assert f"Outros planos escritos por esta sala: `{b}`" in texto


def test_arquivo_que_nao_e_plano_nao_e_confundido(tmp_path, transcript):
    """`plano-x.md` fora de `.local/` (doc versionado) não é o plano do rito."""
    transcript.write_bytes(_assistente(_write(str(tmp_path / "docs" / "plano-x.md"),
                                              "- [ ] doc\n")))
    texto = _gera(transcript, tmp_path)
    assert "Nenhum plano identificado para esta sala" in texto


def test_texto_de_notify_com_caminho_de_plano_nao_vira_escrita(tmp_path, transcript):
    """Medido no acervo: `→ (\\`.local/plano-x.md\\`` dentro de uma mensagem
    casava com o detector de redirecionamento e atribuía o plano à sala."""
    linha = json.dumps({"type": "assistant", "cwd": str(tmp_path), "message": {"content": [
        _bash('bot/bin/kobe-notify "plano -> (`.local/plano-f3.md`) e => `.local/plano-g.md`"')
    ]}}) + "\n"
    transcript.write_text(linha, encoding="utf-8")
    assert "Nenhum plano identificado para esta sala" in _gera(transcript, tmp_path)


def test_redirecionamento_dentro_de_corpo_de_heredoc_nao_conta(tmp_path, transcript):
    """Uma sala que escreve um TESTE contendo `cat > .local/plano-sh.md` não
    escreveu esse plano — o texto está no corpo do heredoc, não é comando."""
    cmd = ("cat >> tests/t.py <<'PYEOF'\n"
           "x = \"cat > .local/plano-sh.md <<'EOF'\"\n"
           "PYEOF")
    linha = json.dumps({"type": "assistant", "cwd": str(tmp_path), "message": {
        "content": [_bash(cmd)]}}) + "\n"
    transcript.write_text(linha, encoding="utf-8")
    assert "Nenhum plano identificado para esta sala" in _gera(transcript, tmp_path)


def test_heredoc_que_ESCREVE_o_plano_continua_contando(tmp_path, transcript):
    cwd = tmp_path / "w"
    (cwd / ".local").mkdir(parents=True)
    (cwd / ".local" / "plano-h.md").write_text("- [ ] h\n", encoding="utf-8")
    cmd = "cat > .local/plano-h.md <<'EOF'\n- [ ] h\nEOF"
    linha = json.dumps({"type": "assistant", "cwd": str(cwd), "message": {
        "content": [_bash(cmd)]}}) + "\n"
    transcript.write_text(linha, encoding="utf-8")
    assert "- [ ] h" in _gera(transcript, cwd)


def test_sala_que_reescreveu_o_plano_por_script_tem_o_disco_respeitado(tmp_path, transcript):
    """Medido: 7 de 7 divergências disco×transcript no acervo eram a própria
    sala mexendo no plano por script. Dizer "não é o que a sala escreveu" ali
    seria FALSO — vale o disco."""
    cwd = tmp_path / "w"
    (cwd / ".local").mkdir(parents=True)
    p = cwd / ".local" / "plano-s.md"
    p.write_text("- [x] a\n- [ ] b2\n- [ ] c\n", encoding="utf-8")
    script = ("python3 - <<'EOF'\nt=open('.local/plano-s.md').read()\n"
              "open('.local/plano-s.md','w').write(t.replace('b','b2'))\nEOF")
    linha_w = json.dumps({"type": "assistant", "cwd": str(cwd), "message": {"content": [
        _write(str(p), "- [x] a\n- [ ] b\n")]}}) + "\n"
    linha_s = json.dumps({"type": "assistant", "cwd": str(cwd), "message": {"content": [
        _bash(script)]}}) + "\n"
    transcript.write_text(linha_w + linha_s, encoding="utf-8")

    texto = _gera(transcript, cwd)
    assert "- [ ] b2" in texto and "- [ ] c" in texto
    assert "já não é o que esta sala escreveu" not in texto


def test_reconstruido_com_edicao_por_shell_avisa_que_pode_estar_desatualizado(
        tmp_path, transcript):
    caminho = str(tmp_path / "sumiu" / ".local" / "plano-k.md")
    linha_w = json.dumps({"type": "assistant", "cwd": str(tmp_path / "sumiu"),
                          "message": {"content": [_write(caminho, "- [ ] a\n")]}}) + "\n"
    linha_s = json.dumps({"type": "assistant", "cwd": str(tmp_path / "sumiu"),
                          "message": {"content": [
                              _bash("sed -i 's/\\[ \\]/[x]/' .local/plano-k.md")]}}) + "\n"
    transcript.write_text(linha_w + linha_s, encoding="utf-8")
    texto = _gera(transcript, tmp_path)
    assert "reconstruído do transcript" in texto
    assert "as marcações podem estar desatualizadas" in texto
