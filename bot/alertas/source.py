"""AlertasSource — 2ª fonte de despertares do Keyko, baseada em tempo.

Diferente de MissoesSource (gatilho = evento em arquivo), aqui o gatilho
é o RELÓGIO: a cada tick a source compara `agora` com
`estado.proximo_disparo` de cada alerta e, quando vence, executa a ação
agendada (`estado.proxima_acao`) — disparar, abrir, cobrar ou expirar.

Princípio reitor: TODA a lógica de quando/qual-estado mora aqui (código
determinístico). O Claude/Hal só é acordado pra redigir+enviar o lembrete
(ações que acordam: ABRIR/COBRAR/DISPARAR). EXPIRAR e as transições de
confirmação NÃO custam LLM — são puro código.

Fluxo de confirmação (espelha Missões): o operador diz "já marquei" em
conversa normal; o Hal, vendo o alerta aberto no contexto do prompt,
chama `bot/bin/kobe-alerta confirmar <id>`, que appenda um evento
`confirmado` no `<id>.eventos.jsonl`. Esta source lê o evento (offset
persistido) e aplica a transição → CONFIRMADO. Código é dono do estado.

Estado interno (offsets de leitura de cada eventos.jsonl) persistido em
`user-data/alertas/.keyko-alertas.json` — sobrevive a restart.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from bot.alertas import (
    Acao,
    Alerta,
    StatusAlerta,
    TIPOS_COMANDO,
    TipoEvento,
    storage,
)
from bot.alertas import prompts as alertas_prompts
from bot.alertas import scheduler
from bot.keyko.models import Despertar


logger = logging.getLogger("kobe.alertas.source")

# Ações que acordam o Hal (custam LLM). EXPIRAR é só transição de estado.
_ACOES_QUE_ACORDAM = frozenset({Acao.DISPARAR, Acao.ABRIR, Acao.COBRAR})


class AlertasSource:
    """Implementa `keyko.models.Source` pra Alertas.

    intervalo_s = 30 — cron tem granularidade de minuto, 30s dá folga sem
    desperdiçar CPU. Estado em memória: `_offsets[alerta_id]` (bytes já
    lidos do eventos.jsonl). Persistido a cada mudança.
    """

    nome = "alertas"
    intervalo_s = 30.0

    def __init__(self, *, kobe_home: Path, bot_token: str):
        self._kobe_home = kobe_home
        self._bot_token = bot_token
        self._state_path = (
            storage.alertas_root(kobe_home) / storage.ARQUIVO_SOURCE_STATE
        )
        self._offsets: dict[str, int] = {}
        self._load_state()

    # --- estado interno persistido ----------------------------------

    def _load_state(self) -> None:
        if not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._offsets = {k: int(v) for k, v in (data.get("offsets") or {}).items()}
            logger.info("keyko-alertas carregado: %d offsets", len(self._offsets))
        except (OSError, json.JSONDecodeError, ValueError):
            logger.exception("keyko-alertas corrompido — recomeçando do zero")
            self._offsets = {}

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"offsets": self._offsets}
        tmp = self._state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            logger.exception("falha gravando keyko-alertas em %s", self._state_path)

    # --- API Source -------------------------------------------------

    def tick(self) -> list[Despertar]:
        agora = datetime.now(scheduler.OPERATOR_TZ)
        alertas = storage.listar_alertas(self._kobe_home, apenas_vivos=True)
        despertares: list[Despertar] = []
        houve_mudanca_state = False

        for alerta in alertas:
            try:
                mudou_offset = self._processar_eventos(alerta.id)
            except Exception:  # noqa: BLE001 — um alerta bugado não derruba o daemon
                logger.exception("falha processando eventos do alerta=%s", alerta.id)
                continue
            houve_mudanca_state = houve_mudanca_state or mudou_offset

            # Recarrega — _processar_eventos pode ter transicionado o estado.
            try:
                alerta = storage.carregar(self._kobe_home, alerta.id)
            except Exception:  # noqa: BLE001
                logger.exception("falha recarregando alerta=%s", alerta.id)
                continue

            try:
                despertar = self._avaliar_disparo(alerta, agora)
            except Exception:  # noqa: BLE001
                logger.exception("falha avaliando disparo do alerta=%s", alerta.id)
                continue
            if despertar is not None:
                despertares.append(despertar)

        if houve_mudanca_state:
            self._save_state()
        return despertares

    # --- processamento de eventos (confirmações vindas do Hal) ------

    def _processar_eventos(self, alerta_id: str) -> bool:
        """Lê eventos novos e aplica os COMANDOS (confirmado/dispensado).
        Devolve True se o offset avançou (precisa salvar state)."""
        offset_atual = self._offsets.get(alerta_id, 0)
        novos, novo_offset = storage.ler_eventos_a_partir(
            self._kobe_home, alerta_id, offset_bytes=offset_atual,
        )
        if not novos:
            return False

        comandos = [e for e in novos if e.tipo in TIPOS_COMANDO]
        if comandos:
            try:
                with storage.mutar(self._kobe_home, alerta_id) as alerta:
                    for ev in comandos:
                        self._aplicar_comando(alerta, ev.tipo)
            except storage.LockTimeoutError:
                logger.warning(
                    "lock timeout aplicando comandos em alerta=%s — re-tenta no próximo tick",
                    alerta_id,
                )
                return False  # NÃO avança offset — re-lê na próxima

        self._offsets[alerta_id] = novo_offset
        return novo_offset != offset_atual

    def _aplicar_comando(self, alerta: Alerta, tipo: str) -> None:
        """Confirmado/Dispensado fecham o ciclo: status → CONFIRMADO,
        dorme até a próxima abertura. Só faz sentido em alerta com
        confirmação e atualmente ABERTO (ignora fora disso — idempotente)."""
        if not alerta.aguarda_confirmacao:
            return
        if alerta.estado.status != StatusAlerta.ABERTO.value:
            # Já confirmado/dormindo — confirmação duplicada é no-op.
            return
        alerta.estado.status = StatusAlerta.CONFIRMADO.value
        agora = datetime.now(scheduler.OPERATOR_TZ)
        self._reagendar(alerta, desde=agora)
        logger.info(
            "alerta=%s confirmado (%s) — dorme até %s",
            alerta.id, tipo, alerta.estado.proximo_disparo,
        )

    # --- avaliação de disparo (gatilho de tempo) --------------------

    def _avaliar_disparo(self, alerta: Alerta, agora: datetime) -> Despertar | None:
        """Se `proximo_disparo` venceu, executa a ação e (talvez) devolve
        um Despertar. Garante agendamento se ainda não há um."""
        if alerta.esta_pausado or alerta.is_terminal():
            return None

        # Sem agendamento ainda? Calcula e grava (não dispara neste tick).
        if alerta.estado.proximo_disparo is None:
            self._persistir_agendamento(alerta.id, desde=agora)
            return None

        try:
            quando = scheduler.parse_iso(alerta.estado.proximo_disparo)
        except ValueError:
            logger.warning(
                "proximo_disparo inválido em alerta=%s (%r) — reagendando",
                alerta.id, alerta.estado.proximo_disparo,
            )
            self._persistir_agendamento(alerta.id, desde=agora)
            return None

        if agora < quando:
            return None  # ainda não venceu

        acao_str = alerta.estado.proxima_acao or Acao.DISPARAR.value
        try:
            acao = Acao(acao_str)
        except ValueError:
            logger.warning("proxima_acao inválida em alerta=%s (%r)", alerta.id, acao_str)
            acao = Acao.DISPARAR

        return self._executar_acao(alerta.id, acao, agora)

    def _executar_acao(self, alerta_id: str, acao: Acao, agora: datetime) -> Despertar | None:
        """Aplica a transição de estado da ação (sob lock) e devolve o
        Despertar se a ação acorda o Hal. EXPIRAR não acorda."""
        pode_acordar = acao in _ACOES_QUE_ACORDAM
        despertar: Despertar | None = None
        try:
            with storage.mutar(self._kobe_home, alerta_id) as alerta:
                # Circuit breaker por alerta — teto de disparos/dia.
                if pode_acordar and not self._sob_limite_diario(alerta, agora):
                    logger.info(
                        "alerta=%s atingiu disparos_dia=%d — pulando disparo (reagenda)",
                        alerta_id, alerta.limites.disparos_dia,
                    )
                    pode_acordar = False  # não acorda, mas avança o relógio abaixo

                if acao == Acao.EXPIRAR:
                    alerta.estado.status = StatusAlerta.EXPIRADO.value
                    storage.append_evento(self._kobe_home, alerta_id, TipoEvento.EXPIRADO)
                elif acao == Acao.ABRIR:
                    alerta.estado.status = StatusAlerta.ABERTO.value
                    alerta.estado.ciclo_iniciado_em = agora.strftime("%Y-%m-%d")
                    storage.append_evento(self._kobe_home, alerta_id, TipoEvento.REABERTO)

                if pode_acordar:
                    self._registrar_disparo(alerta, agora)
                    storage.append_evento(
                        self._kobe_home, alerta_id, TipoEvento.DISPARO,
                        dados={"acao": acao.value},
                    )
                    despertar = self._monta_despertar(alerta, acao)

                # Reagenda SEMPRE a partir de agora (pula backlog se o
                # daemon ficou fora do ar). One-shot que disparou vira
                # CONCLUIDO dentro de _reagendar (proximo None).
                if acao == Acao.DISPARAR and alerta.agenda.is_one_shot:
                    alerta.estado.status = StatusAlerta.CONCLUIDO.value
                    alerta.estado.proximo_disparo = None
                    alerta.estado.proxima_acao = None
                else:
                    self._reagendar(alerta, desde=agora)
        except storage.LockTimeoutError:
            logger.warning("lock timeout executando ação em alerta=%s — re-tenta", alerta_id)
            return None
        return despertar

    # --- helpers de agendamento -------------------------------------

    def _persistir_agendamento(self, alerta_id: str, *, desde: datetime) -> None:
        """Calcula e grava proximo_disparo/proxima_acao (sob lock)."""
        try:
            with storage.mutar(self._kobe_home, alerta_id) as alerta:
                self._reagendar(alerta, desde=desde)
        except storage.LockTimeoutError:
            logger.warning("lock timeout agendando alerta=%s", alerta_id)

    def _reagendar(self, alerta: Alerta, *, desde: datetime) -> None:
        """Recalcula proximo_disparo/proxima_acao no `alerta` em memória.
        Caller detém o lock e salva ao sair. None zera o agendamento."""
        prox = scheduler.calcular_proximo(alerta, desde=desde)
        if prox is None:
            alerta.estado.proximo_disparo = None
            alerta.estado.proxima_acao = None
        else:
            alerta.estado.proximo_disparo = prox.quando.isoformat(timespec="seconds")
            alerta.estado.proxima_acao = prox.acao.value

    def _registrar_disparo(self, alerta: Alerta, agora: datetime) -> None:
        hoje = agora.strftime("%Y-%m-%d")
        if alerta.estado.disparos_hoje_data != hoje:
            alerta.estado.disparos_hoje_data = hoje
            alerta.estado.disparos_hoje = 0
        alerta.estado.disparos_hoje += 1
        alerta.estado.ultimo_disparo = agora.isoformat(timespec="seconds")

    def _sob_limite_diario(self, alerta: Alerta, agora: datetime) -> bool:
        hoje = agora.strftime("%Y-%m-%d")
        usados = (
            alerta.estado.disparos_hoje
            if alerta.estado.disparos_hoje_data == hoje
            else 0
        )
        return usados < alerta.limites.disparos_dia

    # --- montagem do Despertar --------------------------------------

    def _monta_despertar(self, alerta: Alerta, acao: Acao) -> Despertar:
        prompt = alertas_prompts.montar_prompt_disparo(
            kobe_home=str(self._kobe_home),
            alerta_id=alerta.id,
            titulo=alerta.titulo,
            instrucao=alerta.instrucao,
            acao=acao.value,
            acao_descricao=alertas_prompts.ACAO_DESCRICAO.get(acao.value, acao.value),
            aguarda_confirmacao=alerta.aguarda_confirmacao,
            canal_tipo=alerta.canal.tipo,
            canal_destino=alerta.canal.destino,
            fecha_quando=alerta.confirmacao.fecha_quando if alerta.confirmacao else None,
            ciclo_iniciado_em=alerta.estado.ciclo_iniciado_em,
        )
        return Despertar(
            fonte="alertas",
            chave=alerta.id,
            motivo=f"{acao.value} {alerta.id}",
            prompt=prompt,
            chat_id=alerta.chat_id,
            thread_id=alerta.thread_id,
            cwd=str(self._kobe_home),
            env_extra={"KOBE_HOME": str(self._kobe_home)},
            # Log próprio do alerta — falhas do claude despertado ficam
            # visíveis (lição do Bug 4 do v0.13 em Missões).
            log_path=storage.alertas_root(self._kobe_home) / f"{alerta.id}.disparo.log",
        )
