"""Tipos e enums do Sistema de Missões.

Uma **Missão** é um trabalho multi-tarefa coordenado. Vive em
`user-data/missoes/<id>/` com dois arquivos chave:

- `estado.json` — view materializada (campo `status` de cada tarefa,
  narrativa, painel_msg_id). Lido pelo Keyko, escrito tanto pelo
  orquestrador (decisões) quanto pelo Keyko (aplicação de eventos).
  Coordenado por lock fcntl + escrita atômica (vide `storage.py`).
- `eventos.jsonl` — log append-only. Fonte da verdade de "o que aconteceu".
  Cada linha = um evento JSON. O Keyko lê com offset persistido.

A id da missão segue `YYYY-MM-DD-<slug>` derivado do objetivo, com sufixo
`-N` em caso de colisão no mesmo dia. Reaproveita `topic_manager.slugify`
pra consistência com o resto do Kobe.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class StatusMissao(str, Enum):
    PLANEJADA = "planejada"          # criada, orquestrador ainda não planejou
    EM_ANDAMENTO = "em-andamento"    # ao menos uma tarefa em execução
    CONCLUIDA = "concluida"          # todas as tarefas fecharam ok
    FALHOU = "falhou"                # alguma tarefa crítica falhou e orquestrador decidiu fechar
    ABORTADA = "abortada"            # operador rodou /missao_abortar


class StatusTarefa(str, Enum):
    PENDENTE = "pendente"            # aguarda dependências ou plano
    RODANDO = "rodando"              # executor disparado, PID conhecido
    CONCLUIDA = "concluida"          # terminou com sucesso
    FALHOU = "falhou"                # exit_code != 0 ou exception


class TipoEvento(str, Enum):
    """Tipos válidos do `eventos.jsonl`. Mudança de nome aqui = breaking.

    `narrativa-atualizada` e `tarefa-progresso` são "rasos" — Keyko só
    repinta painel. Os demais são "marcos" — Keyko repinta painel **e**
    acorda orquestrador (sujeito ao circuit breaker).
    """
    MISSAO_CRIADA = "missao-criada"
    MISSAO_CONCLUIDA = "missao-concluida"
    MISSAO_ABORTADA = "missao-abortada"
    TAREFA_INICIADA = "tarefa-iniciada"
    TAREFA_PROGRESSO = "tarefa-progresso"
    TAREFA_CONCLUIDA = "tarefa-concluida"
    TAREFA_FALHOU = "tarefa-falhou"
    NARRATIVA_ATUALIZADA = "narrativa-atualizada"
    INCONSISTENCIA_DETECTADA = "inconsistencia-detectada"


# Tipos que disparam acorda-orquestrador (além de atualizar painel).
# Os outros só refrescam painel sem custo de LLM.
#
# `MISSAO_CRIADA` fica DE FORA: o handler /missao já dispara `planejar`
# direto. Deixar aqui criava corrida entre o `planejar` (handler) e um
# `reagir-marco` (Keyko vendo missao-criada) — o segundo via `tarefas: []`
# e fechava missão prematuramente (bug v0.13). Sem custo perdido: o
# despertar do Keyko era duplicado.
TIPOS_MARCO: frozenset[str] = frozenset({
    TipoEvento.MISSAO_CONCLUIDA.value,
    TipoEvento.MISSAO_ABORTADA.value,
    TipoEvento.TAREFA_CONCLUIDA.value,
    TipoEvento.TAREFA_FALHOU.value,
})


@dataclass
class Tarefa:
    id: str                              # T1, T2, T3...
    titulo: str
    executor: str = "ad-hoc-prompt"      # único valor na Fase 1
    prompt: str = ""                     # o que o claude -p vai receber
    depende_de: list[str] = field(default_factory=list)
    status: str = StatusTarefa.PENDENTE.value
    progresso: int = 0                   # 0-100, opcional (não usado na Fase 1)
    pid: Optional[int] = None
    iniciado_em: Optional[str] = None
    terminado_em: Optional[str] = None
    output_path: Optional[str] = None    # caminho do resultado pra anexar no fim
    log_path: Optional[str] = None       # stdout do claude -p da tarefa
    erro: Optional[str] = None           # mensagem de erro, se falhou


@dataclass
class Missao:
    id: str                              # YYYY-MM-DD-<slug>
    objetivo: str                        # texto livre que o operador mandou
    criado_em: str                       # ISO 8601 com tz
    atualizado_em: str                   # ISO 8601 com tz
    status: str = StatusMissao.PLANEJADA.value
    chat_id: int = 0
    thread_id: Optional[int] = None      # None = chat raiz / general
    painel_msg_id: Optional[int] = None  # id da mensagem-painel no Telegram
    narrativa: str = ""                  # frase do orquestrador, 1-3 linhas
    tarefas: list[Tarefa] = field(default_factory=list)

    # --- (de)serialização --------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Dict pronto pra `json.dumps`. Dataclasses não fazem nada de
        especial com lista de dataclasses aninhada — `asdict` resolve."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Missao":
        """Inverso de `to_dict`. Tolera campos faltando (default do
        dataclass) e ignora campos extras (futureproof contra migrações)."""
        tarefas_raw = data.get("tarefas") or []
        tarefas = [Tarefa(**{k: v for k, v in t.items() if k in Tarefa.__annotations__})
                   for t in tarefas_raw]
        # Mesmo filtro pra Missao — se aparecer campo novo no arquivo que o
        # código não conhece (downgrade do código), ignora em vez de explodir.
        kwargs = {k: v for k, v in data.items()
                  if k in Missao.__annotations__ and k != "tarefas"}
        return cls(tarefas=tarefas, **kwargs)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Missao":
        return cls.from_dict(json.loads(text))

    # --- helpers de consulta ---------------------------------------------

    def tarefa(self, tarefa_id: str) -> Optional[Tarefa]:
        for t in self.tarefas:
            if t.id == tarefa_id:
                return t
        return None

    def tarefas_prontas(self) -> list[Tarefa]:
        """Tarefas pendentes cujas dependências já concluíram. O orquestrador
        ou um helper usa pra decidir o que disparar a seguir."""
        concluidas = {t.id for t in self.tarefas if t.status == StatusTarefa.CONCLUIDA.value}
        prontas = []
        for t in self.tarefas:
            if t.status != StatusTarefa.PENDENTE.value:
                continue
            if all(dep in concluidas for dep in t.depende_de):
                prontas.append(t)
        return prontas

    def is_terminal(self) -> bool:
        return self.status in (
            StatusMissao.CONCLUIDA.value,
            StatusMissao.FALHOU.value,
            StatusMissao.ABORTADA.value,
        )


@dataclass(frozen=True)
class Evento:
    """Uma linha do `eventos.jsonl`. `dados` é payload livre — cada tipo
    convenciona o que vai dentro (vide docstring de `TipoEvento` e
    `storage.append_evento`)."""
    ts: str                              # ISO 8601 com tz
    tipo: str                            # valor de TipoEvento
    tarefa_id: Optional[str] = None      # T1 etc., quando aplicável
    dados: dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        """Uma linha — sem indent, sem \\n no fim (caller adiciona)."""
        payload = {"ts": self.ts, "tipo": self.tipo}
        if self.tarefa_id is not None:
            payload["tarefa_id"] = self.tarefa_id
        if self.dados:
            payload["dados"] = self.dados
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "Evento":
        data = json.loads(line)
        return cls(
            ts=data["ts"],
            tipo=data["tipo"],
            tarefa_id=data.get("tarefa_id"),
            dados=data.get("dados") or {},
        )
