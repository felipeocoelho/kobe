"""Tipos e enums do Sistema de Alertas.

Um **Alerta** é uma instrução proativa: o operador pede em linguagem
natural ("me lembra toda terça…", "todo dia 7h faça X") e o Kobe passa a
disparar sozinho no horário. Vive em `user-data/alertas/` com dois
arquivos por alerta:

- `<id>.yaml` — definição (escrita 1× pelo Hal na criação) + bloco
  `estado` (escrito SÓ pela `AlertasSource`, código determinístico).
  Demarcado em duas seções pra deixar claro quem é dono de quê. O
  operador lê/edita à mão.
- `<id>.eventos.jsonl` — log append-only. Confirmação do operador NÃO
  edita estado direto: vira evento aqui (via helper `kobe-alerta`), e a
  source aplica a transição. Espelha o padrão Missões.

Princípio reitor: a lógica de programação (quando disparar, estado,
transições, escalonamento) mora no código. O Claude/Hal só é invocado
pra linguagem (traduzir pedido→YAML, redigir o lembrete, julgar
"já marquei"). Código é dono do estado.

Máquina de estado (alertas com `aguarda_confirmacao: true`):

    abertura (cron) ──► ABERTO ──confirma──► CONFIRMADO
                          │                       │
                          └──bate limite──► EXPIRADO
                          ▲                       │
                          └──próxima abertura reabre o ciclo

CONFIRMADO/EXPIRADO dormem (sem disparos) até a próxima `abertura`.
Alertas simples (`aguarda_confirmacao: false`): só `ativo`/`pausado`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

import yaml


class StatusAlerta(str, Enum):
    """Estados possíveis de um alerta.

    Eixo "simples" (aguarda_confirmacao=false):
        ATIVO    — agendado, dispara e reagenda.
        PAUSADO  — suspenso pelo operador; não dispara até retomar.

    Eixo "ciclo de vida" (aguarda_confirmacao=true):
        ABERTO     — cobrando ativamente (a cada cron de cobranca).
        CONFIRMADO — operador confirmou; dorme até a próxima abertura.
        EXPIRADO   — bateu o limite sem confirmar; dorme até reabrir.
        PAUSADO    — suspenso pelo operador (vide `estado.status_antes_pausa`).

    Terminal (qualquer eixo):
        CONCLUIDO  — one-shot que já disparou; auto-arquivado.
    """

    ATIVO = "ativo"
    PAUSADO = "pausado"
    ABERTO = "aberto"
    CONFIRMADO = "confirmado"
    EXPIRADO = "expirado"
    CONCLUIDO = "concluido"


# Status terminais — fora de "vivo" o alerta não é mais tickado nem listado
# como ativo. `concluido` é o one-shot que já disparou. Definido como
# conjunto positivo de terminais (pequeno) — qualquer status desconhecido
# gravado à mão é tratado como VIVO (conservador: melhor tickar um alerta
# estranho e logar do que silenciá-lo).
_STATUS_TERMINAIS: frozenset[str] = frozenset({
    StatusAlerta.CONCLUIDO.value,
})


class TipoEvento(str, Enum):
    """Tipos válidos do `<id>.eventos.jsonl`.

    Só `CONFIRMADO` (e `DISPENSADO`) são *comandos* vindos do Hal que a
    source consome pra transicionar estado. Os demais são *auditoria*
    escritos pela própria source/handlers — a source os ignora ao
    reprocessar (idempotente, espelha Missões).
    """

    CRIADO = "criado"              # auditoria — handler criou o alerta
    DISPARO = "disparo"            # auditoria — source acordou o Hal
    CONFIRMADO = "confirmado"      # COMANDO — operador confirmou (Hal/kobe-alerta)
    DISPENSADO = "dispensado"      # COMANDO — operador dispensou o ciclo ("deixa pra lá")
    EXPIRADO = "expirado"          # auditoria — bateu limite sem confirmar
    REABERTO = "reaberto"          # auditoria — nova abertura reabriu o ciclo
    PAUSADO = "pausado"            # auditoria — handler pausou
    RETOMADO = "retomado"          # auditoria — handler retomou
    APAGADO = "apagado"            # auditoria — handler apagou


# Eventos que a source CONSOME pra aplicar transição. Os outros são log.
TIPOS_COMANDO: frozenset[str] = frozenset({
    TipoEvento.CONFIRMADO.value,
    TipoEvento.DISPENSADO.value,
})


class Acao(str, Enum):
    """Ação que a source deve executar quando `proximo_disparo` vencer.

    Computada pelo scheduler (determinístico) e gravada em
    `estado.proxima_acao` junto com `proximo_disparo`, pra a source saber
    o que fazer ao acordar sem ter que re-derivar do cron (evita
    ambiguidade em fronteira de cron).

    - DISPARAR: alerta simples (recorrente ou one-shot) — acorda o Hal.
      One-shot vira CONCLUIDO após disparar.
    - ABRIR: alerta com confirmação — (re)abre o ciclo e acorda o Hal
      (1º lembrete da janela). status → ABERTO.
    - COBRAR: alerta com confirmação em ABERTO — re-cobra (acorda o Hal).
    - EXPIRAR: alerta com confirmação — bateu o `limite` sem confirmar.
      NÃO acorda o Hal; status → EXPIRADO, dorme até a próxima abertura.
    """

    DISPARAR = "disparar"
    ABRIR = "abrir"
    COBRAR = "cobrar"
    EXPIRAR = "expirar"


@dataclass
class Agenda:
    """Quando o alerta dispara.

    - `abertura`: cron (5 campos) que dispara / (re)abre o ciclo. None só
      em one-shot.
    - `quando`: ISO 8601 pra one-shot. Dispara 1× e auto-arquiva.
    - `cobranca`: cron que re-cobra ENQUANTO status==aberto (só
      confirmation alerts).
    - `limite`: cron/horário após o qual para de cobrar no ciclo atual
      (marca EXPIRADO se não confirmou).
    """

    abertura: Optional[str] = None
    quando: Optional[str] = None
    cobranca: Optional[str] = None
    limite: Optional[str] = None

    @property
    def is_one_shot(self) -> bool:
        return bool(self.quando) and not self.abertura


@dataclass
class Confirmacao:
    """Critério em linguagem natural pra fechar o ciclo. Lido pelo Hal
    no turno normal de conversa pra decidir se o operador confirmou."""

    fecha_quando: str = ""


@dataclass
class Canal:
    """Por onde o lembrete sai.

    - `telegram`: usa o chat/tópico onde o alerta foi criado
      (`Alerta.chat_id`/`thread_id`). `destino` fica None.
    - `whatsapp`: `destino` = número (ex.: "+55 21 98753-4566"). O envio
      sai pelo helper-seam `bot/bin/kobe-whatsapp` (Telegram de origem é
      fallback se falhar). Os Alertas não conhecem o backend de WhatsApp —
      a costura mora só no helper.
    """

    tipo: str = "telegram"
    destino: Optional[str] = None


@dataclass
class Limites:
    """Circuit breaker por alerta — teto de disparos por dia, defesa
    contra cron mal-configurado que dispararia em loop."""

    disparos_dia: int = 3


@dataclass
class Estado:
    """Bloco escrito SÓ pela AlertasSource (código). O operador não toca.

    - `status`: vide StatusAlerta.
    - `proximo_disparo`: ISO 8601 — quando a source deve agir a seguir.
      None = nada agendado (one-shot já concluído, ou pausado). É a
      "agulha" que o tick compara com o relógio.
    - `proxima_acao`: vide Acao — o que fazer quando `proximo_disparo`
      vencer (disparar/abrir/cobrar/expirar). Gravado junto pra a source
      não re-derivar do cron na hora.
    - `ciclo_iniciado_em`: data (YYYY-MM-DD) em que a abertura atual abriu
      o ciclo. Só confirmation alerts. Pro Hal dizer "essa semana".
    - `status_antes_pausa`: pra `/alerta retomar` restaurar o estado de
      antes da pausa.
    - `disparos_hoje` / `disparos_hoje_data`: contador do circuit breaker
      por alerta, resetado quando vira o dia.
    """

    status: str = StatusAlerta.ATIVO.value
    criado_em: str = ""
    ultimo_disparo: Optional[str] = None
    proximo_disparo: Optional[str] = None
    proxima_acao: Optional[str] = None
    ciclo_iniciado_em: Optional[str] = None
    status_antes_pausa: Optional[str] = None
    disparos_hoje: int = 0
    disparos_hoje_data: Optional[str] = None


@dataclass
class Alerta:
    """Um alerta completo (definição + estado).

    `chat_id`/`thread_id` capturam o tópico de origem — é pra onde o
    lembrete telegram vai e pra onde caem avisos de fallback do whatsapp.
    None em thread_id = chat raiz / private / general (igual Missões).
    """

    id: str
    titulo: str
    instrucao: str
    criado_em: str
    chat_id: int = 0
    thread_id: Optional[int] = None
    agenda: Agenda = field(default_factory=Agenda)
    aguarda_confirmacao: bool = False
    confirmacao: Optional[Confirmacao] = None
    canal: Canal = field(default_factory=Canal)
    limites: Limites = field(default_factory=Limites)
    estado: Estado = field(default_factory=Estado)

    # --- helpers de consulta ---------------------------------------------

    def is_terminal(self) -> bool:
        return self.estado.status in _STATUS_TERMINAIS

    @property
    def esta_pausado(self) -> bool:
        return self.estado.status == StatusAlerta.PAUSADO.value

    @property
    def esta_aberto(self) -> bool:
        """ABERTO = cobrando ativamente (só confirmation alerts)."""
        return self.estado.status == StatusAlerta.ABERTO.value

    # --- (de)serialização -------------------------------------------------

    def _def_dict(self) -> dict[str, Any]:
        """Só a seção definição (tudo menos `estado`), na ordem do design."""
        d: dict[str, Any] = {
            "id": self.id,
            "titulo": self.titulo,
            "instrucao": self.instrucao,
            "chat_id": self.chat_id,
            "thread_id": self.thread_id,
            "agenda": _compact(asdict(self.agenda)),
            "aguarda_confirmacao": self.aguarda_confirmacao,
        }
        if self.confirmacao is not None:
            d["confirmacao"] = asdict(self.confirmacao)
        d["canal"] = _compact(asdict(self.canal))
        d["limites"] = asdict(self.limites)
        d["criado_em"] = self.criado_em
        return d

    def to_dict(self) -> dict[str, Any]:
        d = self._def_dict()
        d["estado"] = asdict(self.estado)
        return d

    def to_yaml(self) -> str:
        """Serializa em YAML com as duas seções demarcadas por comentário.

        Definição e estado são dumpados em blocos separados pra poder
        injetar os cabeçalhos de comentário (PyYAML não preserva
        comentários). Carregamento (`from_yaml`) lê o arquivo inteiro de
        uma vez — os comentários são ignorados pelo parser.
        """
        def_block = yaml.dump(
            self._def_dict(),
            Dumper=_AlertaDumper,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        estado_block = yaml.dump(
            {"estado": asdict(self.estado)},
            Dumper=_AlertaDumper,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        return (
            "# --- definição (Hal escreve na criação) ---\n"
            + def_block
            + "\n# --- estado (só o código escreve) ---\n"
            + estado_block
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Alerta":
        """Inverso de to_dict. Tolera campos faltando (default do
        dataclass) e ignora campos extras (futureproof contra migração)."""
        agenda = Agenda(**_filtra(data.get("agenda") or {}, Agenda))
        canal = Canal(**_filtra(data.get("canal") or {}, Canal))
        limites = Limites(**_filtra(data.get("limites") or {}, Limites))
        estado = Estado(**_filtra(data.get("estado") or {}, Estado))
        confirmacao_raw = data.get("confirmacao")
        confirmacao = (
            Confirmacao(**_filtra(confirmacao_raw, Confirmacao))
            if isinstance(confirmacao_raw, dict)
            else None
        )
        kwargs = {
            k: v
            for k, v in data.items()
            if k in Alerta.__annotations__
            and k not in ("agenda", "canal", "limites", "estado", "confirmacao")
        }
        return cls(
            agenda=agenda,
            canal=canal,
            limites=limites,
            estado=estado,
            confirmacao=confirmacao,
            **kwargs,
        )

    @classmethod
    def from_yaml(cls, text: str) -> "Alerta":
        return cls.from_dict(yaml.safe_load(text) or {})


@dataclass(frozen=True)
class Evento:
    """Uma linha do `<id>.eventos.jsonl`. `dados` é payload livre — cada
    tipo convenciona o que vai dentro."""

    ts: str
    tipo: str
    dados: dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        payload: dict[str, Any] = {"ts": self.ts, "tipo": self.tipo}
        if self.dados:
            payload["dados"] = self.dados
        return json.dumps(payload, ensure_ascii=False)

    @classmethod
    def from_json_line(cls, line: str) -> "Evento":
        data = json.loads(line)
        return cls(
            ts=data["ts"],
            tipo=data["tipo"],
            dados=data.get("dados") or {},
        )


# --- helpers de (de)serialização ---------------------------------------

def _filtra(raw: dict[str, Any], klass: type) -> dict[str, Any]:
    """Mantém só as chaves que `klass` (dataclass) conhece."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k in klass.__annotations__}


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """Remove chaves com valor None — deixa o YAML enxuto pro operador
    (sem `cobranca: null` poluindo alerta simples)."""
    return {k: v for k, v in d.items() if v is not None}


class _AlertaDumper(yaml.SafeDumper):
    """Dumper dedicado pra não poluir o estado global do PyYAML."""


def _str_representer(dumper: yaml.Dumper, data: str):
    """Strings multilinha (ex.: `instrucao`) saem em bloco literal `|`,
    bem mais legível pro operador editar à mão."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_AlertaDumper.add_representer(str, _str_representer)
