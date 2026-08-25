"""Testes do script que decomissiona o acervo de WhatsApp.

O script apaga 15 mil linhas e move 2 GB — e o que autoriza isso é a
**conferência do backup**. Então é a conferência que precisa de teste, não o
caminho feliz: um `conferir_dump` que devolve o número errado libera uma
deleção sem rede.

Testado aqui:
- `conferir_dump` **relê o arquivo** e acusa dump truncado ou corrompido (é o
  gate que autoriza apagar — se ele mentir, perde-se dado de verdade);
- `apagar_linhas` esvazia tudo e **termina** (um loop que não avança rodaria
  pra sempre contra a produção);
- `tamanho_dir` e `humano`, que alimentam o relatório que o operador lê antes
  de dizer "pode".
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_modulo():
    """Importa o script por caminho — ele vive em infra/, que não é pacote."""
    caminho = RAIZ / "infra" / "decommission_whatsapp_acervo.py"
    spec = importlib.util.spec_from_file_location("decommission_whatsapp_acervo", caminho)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


dec = _carregar_modulo()


# ---------------------------------------------------------------------------
# conferir_dump — o gate que autoriza apagar
# ---------------------------------------------------------------------------


def _escrever_dump(destino: Path, linhas: list[dict]) -> None:
    with gzip.open(destino, "wt", encoding="utf-8") as fh:
        for linha in linhas:
            fh.write(json.dumps(linha, ensure_ascii=False) + "\n")


def test_conferir_dump_conta_o_que_esta_no_arquivo(tmp_path):
    alvo = tmp_path / "d.jsonl.gz"
    _escrever_dump(alvo, [{"id": f"m{i}"} for i in range(37)])
    assert dec.conferir_dump(alvo, esperado=37) == 37


def test_conferir_dump_acusa_dump_truncado(tmp_path):
    """O caso que importa: o dump gravou menos do que o banco tem. A conferência
    tem que devolver o número REAL do arquivo, pra quem chama abortar."""
    alvo = tmp_path / "d.jsonl.gz"
    _escrever_dump(alvo, [{"id": f"m{i}"} for i in range(10)])
    assert dec.conferir_dump(alvo, esperado=15650) == 10


def test_conferir_dump_estoura_em_arquivo_corrompido(tmp_path):
    """Linha que não é JSON válido = backup não confiável. Melhor estourar aqui
    do que devolver uma contagem que bate por acaso e liberar a deleção."""
    alvo = tmp_path / "d.jsonl.gz"
    with gzip.open(alvo, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "ok"}) + "\n")
        fh.write("{isso não é json\n")
    with pytest.raises(json.JSONDecodeError):
        dec.conferir_dump(alvo, esperado=2)


def test_conferir_dump_ignora_linha_em_branco(tmp_path):
    alvo = tmp_path / "d.jsonl.gz"
    with gzip.open(alvo, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "a"}) + "\n\n")
        fh.write(json.dumps({"id": "b"}) + "\n")
    assert dec.conferir_dump(alvo, esperado=2) == 2


# ---------------------------------------------------------------------------
# apagar_linhas — tem que esvaziar E terminar
# ---------------------------------------------------------------------------


class _TabelaFake:
    """Cliente supabase mínimo — guarda ids numa lista e os remove de verdade."""
    def __init__(self, n): self.ids = [f"msg{i}" for i in range(n)]; self.requests = 0
    def table(self, _nome): return self
    def select(self, *_a, **_k): self._modo = "select"; return self
    def limit(self, n): self._lim = n; return self
    def delete(self): self._modo = "delete"; return self
    def in_(self, _campo, ids):
        self.ids = [i for i in self.ids if i not in ids]
        self.requests += 1
        return self
    def execute(self):
        if self._modo == "select":
            return type("R", (), {"data": [{"id": i} for i in self.ids[: self._lim]]})()
        return type("R", (), {"data": []})()


def test_apagar_linhas_esvazia_tudo():
    fake = _TabelaFake(450)
    apagadas = dec.apagar_linhas(fake, total=450)
    assert apagadas == 450
    assert fake.ids == []


def test_apagar_linhas_termina_em_tabela_vazia():
    """Sem linha nenhuma, tem que sair na hora — não entrar em loop."""
    fake = _TabelaFake(0)
    assert dec.apagar_linhas(fake, total=0) == 0
    assert fake.requests == 0


class _TabelaQueNaoApaga(_TabelaFake):
    """Simula DELETE sem efeito (permissão negada em silêncio): o select devolve
    sempre os mesmos ids. Sem a trava, o laço roda pra sempre."""
    def in_(self, _campo, _ids):
        self.requests += 1
        return self


def test_apagar_linhas_aborta_se_o_delete_nao_tem_efeito():
    """A trava que impede laço infinito contra a produção."""
    fake = _TabelaQueNaoApaga(50)
    with pytest.raises(SystemExit) as exc:
        dec.apagar_linhas(fake, total=50)
    assert "não teve efeito" in str(exc.value)
    assert fake.requests <= 2, "deveria abortar na segunda passada, não insistir"


def test_apagar_linhas_pagina_em_lotes():
    """Confirma que ele não tenta mandar 15 mil ids numa URL só."""
    fake = _TabelaFake(dec.LOTE_DELETE * 3)
    dec.apagar_linhas(fake, total=dec.LOTE_DELETE * 3)
    assert fake.requests == 3


# ---------------------------------------------------------------------------
# Relatório que o operador lê antes de autorizar
# ---------------------------------------------------------------------------


def test_tamanho_dir_conta_arquivos_e_bytes(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "sub" / "b.bin").write_bytes(b"y" * 50)
    assert dec.tamanho_dir(tmp_path) == (2, 150)


def test_tamanho_dir_em_pasta_inexistente(tmp_path):
    assert dec.tamanho_dir(tmp_path / "nao-existe") == (0, 0)


@pytest.mark.parametrize("entrada,esperado", [
    (512, "512.0 B"),
    (2048, "2.0 KB"),
    (5 * 1024 * 1024, "5.0 MB"),
    (2 * 1024 ** 3, "2.0 GB"),
])
def test_humano_formata_tamanho(entrada, esperado):
    assert dec.humano(entrada) == esperado
