"""Tipos do Keyko — daemon genérico de despertar do Claude por gatilho.

Pensado pra suportar múltiplas FONTES de gatilho:
- Missões (Fase 1) — gatilho = evento em arquivo (eventos.jsonl mudou).
- Alertas (Fase pós-Missões) — gatilho = tempo (cron / one-shot venceu).
- Futuro — qualquer coisa que precise "acordar o Claude por evento".

Cada fonte implementa `Source`. O loop principal do Keyko itera as
fontes registradas, coleta os despertares devidos, aplica circuit
breaker e dispara `claude -p` em background.

O nome "Keyko" é homenagem a um pastor alemão (com Y, não confundir
com Keiko). A metáfora bate com o papel: late quando algo acontece.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Despertar:
    """Pedido pra acordar o Claude. Source produz, Keyko enfileira e executa.

    Campos:
    - fonte: identificador único da source ("missoes", "alertas", ...)
    - chave: identificador único do que está acordando dentro da source.
      Usado pelo circuit breaker — limita N acordadas por (fonte, chave)
      em janela de tempo. Pra Missões = missao_id; pra Alertas = alerta_id.
    - motivo: texto curto pra log/diagnóstico ("tarefa-concluida T3",
      "cron-7am"). Não vai pro Claude — quem monta prompt é a source.
    - prompt: prompt completo pronto pra alimentar `claude -p` via stdin.
    - chat_id, thread_id: canal pra injetar como KOBE_CHAT_ID/THREAD_ID
      no env do subprocess (pra kobe-notify/kobe-attach funcionarem
      dentro do Claude acordado).
    - cwd: diretório de trabalho do `claude -p`. Default = KOBE_HOME.
    - env_extra: vars adicionais pro env do subprocess (a source pode
      injetar KOBE_HOME, IDs específicos da fonte etc).
    - log_path: se setado, stdout+stderr do claude -p vão pra cá em
      modo append. Senão, DEVNULL. MissoesSource usa pra dirigir o log
      pro orquestrador.log da missão; Alertas (futuro) pode optar por
      log próprio. Sem essa rota, falhas do claude despertado viram
      mistério silencioso (Bug 4 do v0.13).
    """
    fonte: str
    chave: str
    motivo: str
    prompt: str
    chat_id: int
    thread_id: Optional[int] = None
    cwd: Optional[str] = None
    env_extra: dict[str, str] = field(default_factory=dict)
    log_path: Optional[Path] = None


@runtime_checkable
class Source(Protocol):
    """Interface mínima de uma fonte de despertar.

    Sources NÃO chamam `claude -p` diretamente — só descrevem o que
    deve ser despertado. O Keyko centraliza a execução, o circuit
    breaker e o log.
    """

    @property
    def nome(self) -> str:
        """Identificador único, usado em log/circuit breaker."""
        ...

    @property
    def intervalo_s(self) -> float:
        """Periodicidade de chamada de `tick()`. Keyko respeita.

        Fontes podem usar valores diferentes:
        - Missões: 2s (eventos vêm rápido conforme tarefas terminam)
        - Alertas (futuro): 30s ou 60s (cron tem granularidade de minuto)
        """
        ...

    def tick(self) -> list[Despertar]:
        """Chamado periodicamente pelo Keyko.

        Source faz seu trabalho colateral (atualizar painel, marcar
        ultimo_disparo etc.) e retorna lista de despertares devidos
        AGORA. Lista vazia é normal — só significa "nada novo".

        Deve ser idempotente — Keyko pode chamar várias vezes seguidas
        sem efeito duplicado se a source não tiver gatilho devido.
        """
        ...
