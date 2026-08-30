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
    (cwd / ".local" / "plano-x.md").write_text(
        "# Plano\n- [x] feito\n- [ ] falta isto\n- [ ] e isto\n", encoding="utf-8")
    transcript.write_bytes(_assistente({"type": "text", "text": "."}))

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
    assert "Nenhum `.local/plano-*.md` encontrado" in texto


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
