#!/usr/bin/env python3
"""O lado ESCRITA do boletim — e o critério 5, que é o que sustenta o desenho.

O critério 5 do briefing da F4 exige: *regenerar duas vezes seguidas sem conversa
nova NÃO muda o arquivo*. Ele parece um detalhe de higiene e não é — foi ele que
decidiu a arquitetura da fase inteira.

A forma ingênua de datar um boletim é gravar `gerado_em = now()`. Com ela, a
segunda geração produziria bytes diferentes da primeira (o delta apareceria
vazio) e o arquivo mudaria sozinho, sem ninguém ter conversado. Para consertar
isso seria preciso guardar estado de geração — coluna, tabela, migration.

O que se fez em vez disso: **cada byte é função do banco**. O cabeçalho traz a
marca d'água do próprio registro (`MAX(GREATEST(created_at, valid_to))`), não o
relógio de quem gerou. Mesma tabela, mesmos bytes. **E foi por isso que a F4
inteira não precisou de migration.** O T-5 é quem guarda essa propriedade.

Sem banco: um cursor falso que responde por trecho de consulta, no padrão de
`tests/test_lucien_consulta.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.lucien import boletim as W
from bot.memory import boletim as fmt

AGORA = datetime(2026, 8, 30, 14, 33, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, dados):
        self._d = dados
        self._r = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "FROM topics WHERE id" in s:
            self._r = [{"nome": self._d["nome"]}] if self._d["nome"] else []
        elif "MAX(GREATEST" in s:
            self._r = [{"marca": self._d["marca"],
                        "vigentes": self._d["total_vigentes"]}]
        elif "kind = ANY" in s:
            kinds = list(params[1])
            self._r = [c for c in self._d["claims"] if c["kind"] in kinds]
        elif "lucien_events" in s:
            self._r = self._d["eventos"]
        elif "DISTINCT topic_id" in s:
            self._r = [{"t": "topico-1"}]
        else:
            self._r = []
        return self

    def fetchone(self):
        return self._r[0] if self._r else None

    def fetchall(self):
        return list(self._r)


class _Cx:
    def __init__(self, **kw):
        base = dict(
            nome="Dev Kobe",
            marca=AGORA,
            total_vigentes=118,
            claims=[
                {"statement": "o deploy é git, a produção puxa a versão",
                 "kind": "decision", "valid_from": AGORA - timedelta(days=2),
                 "source_seq": 3059},
                {"statement": "falta decidir se o boletim nasce ligado",
                 "kind": "open", "valid_from": AGORA - timedelta(days=1),
                 "source_seq": 3101},
                {"statement": "o operador prefere resposta curta e direta",
                 "kind": "preference", "valid_from": AGORA - timedelta(days=5),
                 "source_seq": 2990},
            ],
            eventos=[
                {"action": "superseded",
                 "statement": "a sincronização dev→prod deve ser feita via rsync",
                 "at": AGORA - timedelta(days=3), "source_seq": 1201},
            ],
        )
        base.update(kw)
        self._d = base

    def cursor(self):
        return _Cursor(self._d)


# ── 5. Idempotência — o critério que decidiu a arquitetura ────────────────

def test_t5_gerar_duas_vezes_sem_conversa_nova_nao_toca_no_arquivo(tmp_path):
    cx = _Cx()
    assert W.gerar(cx, "topico-1", kobe_home=tmp_path) is True

    alvo = fmt.caminho(tmp_path, "topico-1")
    bytes1 = alvo.read_bytes()
    mtime1 = alvo.stat().st_mtime_ns

    assert W.gerar(cx, "topico-1", kobe_home=tmp_path) is False, (
        "a segunda geração disse que mudou alguma coisa")
    assert alvo.read_bytes() == bytes1
    assert alvo.stat().st_mtime_ns == mtime1, (
        "o arquivo foi reescrito com conteúdo igual — o mtime mentiu")


def test_t5b_o_cabecalho_usa_a_marca_do_REGISTRO_e_nao_o_relogio(tmp_path):
    """Se o texto dependesse de `now()`, o T-5 seria impossível de satisfazer."""
    W.gerar(cx := _Cx(), "topico-1", kobe_home=tmp_path)
    texto = fmt.caminho(tmp_path, "topico-1").read_text(encoding="utf-8")
    assert "apurado até 30/08/2026" in texto
    assert W.montar_texto(cx, "topico-1") == texto


def test_t5c_conversa_nova_muda_o_arquivo(tmp_path):
    """A outra metade: idempotente não pode virar inerte."""
    assert W.gerar(_Cx(), "topico-1", kobe_home=tmp_path) is True
    novo = _Cx(marca=AGORA + timedelta(days=1), claims=[
        {"statement": "decisão nova que ainda não estava no boletim",
         "kind": "decision", "valid_from": AGORA + timedelta(days=1),
         "source_seq": 3200}])
    assert W.gerar(novo, "topico-1", kobe_home=tmp_path) is True
    assert "decisão nova" in fmt.caminho(tmp_path, "topico-1").read_text("utf-8")


# ── O conteúdo, ponta a ponta ─────────────────────────────────────────────

def test_o_boletim_gerado_tem_os_tres_blocos_e_a_origem(tmp_path):
    W.gerar(_Cx(), "topico-1", kobe_home=tmp_path)
    txt = fmt.caminho(tmp_path, "topico-1").read_text(encoding="utf-8")
    assert txt.startswith("# Dev Kobe")
    assert "PENDÊNCIAS ABERTAS" in txt and "←#3101" in txt
    assert "O QUE VALE HOJE" in txt and "←#3059" in txt
    assert "O QUE SAIU DE CENA" in txt and "←#1201" in txt
    assert "rsync" in txt, "o que saiu de cena é o sinal de 'mudamos de ideia'"
    assert len(txt) <= fmt.BOLETIM_CHAR_LIMIT


def test_o_que_o_turno_le_e_exatamente_o_que_o_worker_escreveu(tmp_path):
    """As duas metades falam do mesmo arquivo — parece óbvio e é o que quebra
    quando uma delas muda de convenção de caminho."""
    W.gerar(_Cx(), "topico-1", kobe_home=tmp_path)
    assert fmt.carregar(tmp_path, "topico-1") == W.montar_texto(_Cx(), "topico-1")


# ── Falhar aqui nunca desfaz uma rodada ───────────────────────────────────

def test_topico_sem_afirmacao_nao_gera_arquivo(tmp_path):
    vazio = _Cx(marca=None, total_vigentes=0, claims=[], eventos=[])
    assert W.gerar(vazio, "topico-1", kobe_home=tmp_path) is False
    assert not fmt.caminho(tmp_path, "topico-1").exists()


def test_banco_explodindo_nao_levanta(tmp_path):
    """Quem chama é uma rodada que JÁ comitou. Um arquivo de conveniência não
    pode desfazer estado que já vale."""
    class _Explode:
        def cursor(self):
            raise RuntimeError("o banco caiu no meio")

    assert W.gerar(_Explode(), "topico-1", kobe_home=tmp_path) is False


def test_disco_somente_leitura_nao_levanta(tmp_path):
    d = fmt.diretorio(tmp_path)
    d.mkdir(parents=True)
    d.chmod(0o500)
    try:
        assert W.gerar(_Cx(), "topico-1", kobe_home=tmp_path) is False
    finally:
        d.chmod(0o700)


def test_gerar_todos_conta_o_que_mudou(tmp_path):
    cx = _Cx()
    assert W.gerar_todos(cx, kobe_home=tmp_path) == (1, 1)
    assert W.gerar_todos(cx, kobe_home=tmp_path) == (0, 1)
