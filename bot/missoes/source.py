"""MissoesSource — fonte de despertares do Keyko, baseada em Missões.

A cada tick:
1. Lista missões NÃO-terminais.
2. Pra cada uma:
   a) Lê novos eventos em eventos.jsonl (a partir do offset persistido).
   b) Aplica deltas no estado.json (tarefa-iniciada → status=rodando,
      tarefa-concluida → status=concluida etc.). Toma o lock fcntl.
   c) Re-renderiza painel e edita no Telegram (throttle interno).
   d) Pra cada evento tipo MARCO, emite um Despertar (Keyko enfileira
      e dispara `claude -p` com prompt 'reagir-marco' do orquestrador).
3. Pra missões que viraram terminais durante este tick (missao-concluida/
   missao-falhou/missao-abortada), anexa os output_paths via kobe-attach.

Offset persistido em `user-data/missoes/.keyko-state.json` — sobrevive
a restart sem reprocessar eventos antigos.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from bot.keyko.models import Despertar, Source
from bot.missoes import (
    Evento,
    Missao,
    StatusMissao,
    StatusTarefa,
    TIPOS_MARCO,
    TipoEvento,
    storage,
)
from bot.missoes import painel
from bot.missoes.prompts import PROMPTS


logger = logging.getLogger("kobe.missoes.source")

# Pasta de estado interno do Keyko (offsets de leitura, set de missões
# já finalizadas/anexadas — pra não anexar duas vezes).
_ARQUIVO_STATE = ".keyko-state.json"


class MissoesSource:
    """Implementa `keyko.models.Source` pra Missões.

    Estado em memória:
    - `_offsets[missao_id]` — bytes já lidos de eventos.jsonl
    - `_anexadas`           — set de missao_id já anexadas (evita re-attach)

    Persistido em disco a cada tick (escrita rápida, JSON pequeno).
    """

    nome = "missoes"
    intervalo_s = 2.0

    def __init__(self, *, kobe_home: Path, bot_token: str):
        self._kobe_home = kobe_home
        self._bot_token = bot_token
        self._state_path = storage.missoes_root(kobe_home) / _ARQUIVO_STATE
        self._offsets: dict[str, int] = {}
        self._anexadas: set[str] = set()
        self._load_state()

    # --- estado interno persistido ----------------------------------

    def _load_state(self) -> None:
        if not self._state_path.is_file():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._offsets = {k: int(v) for k, v in (data.get("offsets") or {}).items()}
            self._anexadas = set(data.get("anexadas") or [])
            logger.info(
                "keyko-state carregado: %d offsets, %d anexadas",
                len(self._offsets), len(self._anexadas),
            )
        except (OSError, json.JSONDecodeError, ValueError):
            logger.exception("keyko-state corrompido — recomeçando do zero")
            self._offsets = {}
            self._anexadas = set()

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "offsets": self._offsets,
            "anexadas": sorted(self._anexadas),
        }
        # Escrita atômica simples (state file pequeno, sem coordenação
        # com outros writers — só MissoesSource escreve aqui).
        tmp = self._state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            logger.exception("falha gravando keyko-state em %s", self._state_path)

    # --- API Source -------------------------------------------------

    def tick(self) -> list[Despertar]:
        # Lista TODAS (não só ativas) — missões recém-fechadas ainda
        # podem ter o evento `missao-concluida` por processar (anexar).
        # Filtramos abaixo individualmente.
        todas = storage.listar_missoes(self._kobe_home)
        despertares: list[Despertar] = []
        houve_mudanca_state = False

        for missao in todas:
            # Já finalizada e já anexada? skip — não precisa mais tickar.
            if missao.is_terminal() and missao.id in self._anexadas:
                continue
            try:
                evs_novos, novo_offset = self._processar_eventos(missao)
            except Exception:  # noqa: BLE001 — uma missão bugada não derruba
                logger.exception("falha processando missao=%s", missao.id)
                continue
            if novo_offset != self._offsets.get(missao.id, 0):
                self._offsets[missao.id] = novo_offset
                houve_mudanca_state = True

            # Aplicar deltas pode ter mudado o estado — recarregar pra
            # render. Mesmo se evs_novos for vazio, repintamos quando
            # apropriado (cobre caso de restart com painel desatualizado).
            try:
                missao_atual = storage.carregar(self._kobe_home, missao.id)
            except (OSError, json.JSONDecodeError):
                logger.exception("falha recarregando missao=%s", missao.id)
                continue

            # Repinta painel se tem painel_msg_id (caso anômalo: handler
            # crashou antes de gravar — não há nada pra editar).
            if missao_atual.painel_msg_id and evs_novos:
                self._repintar_painel(missao_atual)

            # Pra cada evento MARCO, emite despertar (sujeito a CB no Keyko)
            for ev in evs_novos:
                if ev.tipo in TIPOS_MARCO:
                    despertares.append(self._monta_despertar(missao_atual, ev))

            # Missão acabou de virar terminal? Anexa outputs.
            if missao_atual.is_terminal() and missao_atual.id not in self._anexadas:
                self._anexar_outputs(missao_atual)
                self._anexadas.add(missao_atual.id)
                houve_mudanca_state = True

        if houve_mudanca_state:
            self._save_state()
        return despertares

    # --- internas ---------------------------------------------------

    def _processar_eventos(
        self, missao: Missao,
    ) -> tuple[list[Evento], int]:
        """Lê eventos novos e aplica deltas no estado da missão. Devolve
        (eventos_processados, novo_offset)."""
        offset_atual = self._offsets.get(missao.id, 0)
        novos, novo_offset = storage.ler_eventos_a_partir(
            self._kobe_home, missao.id, offset_bytes=offset_atual,
        )
        if not novos:
            return [], novo_offset

        # Aplica deltas em UMA seção crítica (lock único pra todos os
        # eventos novos do tick).
        try:
            with storage.mutar(self._kobe_home, missao.id) as m_mut:
                for ev in novos:
                    _aplicar_evento(m_mut, ev)
        except storage.LockTimeoutError:
            logger.warning(
                "lock timeout aplicando eventos em missao=%s — tentamos no próximo tick",
                missao.id,
            )
            # NÃO avança offset — próximo tick re-lê e tenta de novo.
            return [], offset_atual

        return novos, novo_offset

    def _repintar_painel(self, missao: Missao) -> None:
        try:
            texto = painel.render(missao)
            painel.editar_painel(
                bot_token=self._bot_token,
                chat_id=missao.chat_id,
                message_id=missao.painel_msg_id,  # type: ignore[arg-type]
                texto=texto,
            )
        except Exception:  # noqa: BLE001 — telegram fail não derruba daemon
            logger.exception("falha repintando painel missao=%s", missao.id)

    def _monta_despertar(self, missao: Missao, ev: Evento) -> Despertar:
        prompt = PROMPTS["reagir-marco"].format(
            missao_id=missao.id,
            kobe_home=str(self._kobe_home),
            motivo=f"reagir-marco (evento {ev.tipo})",
            objetivo=missao.objetivo,
            estado_json=missao.to_json(indent=2),
            mensagem_operador="(sem mensagem)",
        )
        return Despertar(
            fonte="missoes",
            chave=missao.id,
            motivo=f"{ev.tipo} {ev.tarefa_id or ''}".strip(),
            prompt=prompt,
            chat_id=missao.chat_id,
            thread_id=missao.thread_id,
            cwd=str(self._kobe_home),
            env_extra={"KOBE_HOME": str(self._kobe_home)},
            # Roteia stdout/stderr do claude -p despertado pro orquestrador.log
            # da missão — mesmo arquivo que `acordar_orquestrador` (handler
            # /missao direto) usa. Bug 4 do v0.13: sem isso, falhas do claude
            # despertado pelo Keyko ficavam invisíveis (T2 nunca disparava).
            log_path=storage.path_log_orquestrador(self._kobe_home, missao.id),
        )

    def _anexar_outputs(self, missao: Missao) -> None:
        """Manda kobe-attach pra cada output das tarefas concluídas.

        Best-effort: erro de attach loga mas não atrapalha. Painel já
        reflete o fim da missão (status read-only com ✅/🔴/⏸️).
        """
        attach_bin = self._kobe_home / "bot" / "bin" / "kobe-attach"
        if not attach_bin.is_file():
            logger.warning("kobe-attach não encontrado em %s", attach_bin)
            return

        env = dict(os.environ)
        env["KOBE_TELEGRAM_BOT_TOKEN"] = self._bot_token
        env["KOBE_CHAT_ID"] = str(missao.chat_id)
        if missao.thread_id is not None:
            env["KOBE_THREAD_ID"] = str(missao.thread_id)
        else:
            env.pop("KOBE_THREAD_ID", None)

        for t in missao.tarefas:
            if t.status != StatusTarefa.CONCLUIDA.value or not t.output_path:
                continue
            output = Path(t.output_path)
            if not output.is_file():
                logger.warning("output_path não existe: %s", output)
                continue
            caption = f"{missao.id} · {t.id} — {t.titulo[:80]}"
            try:
                subprocess.run(
                    [str(attach_bin), str(output), caption],
                    env=env, timeout=120, check=False,
                    capture_output=True,
                )
            except Exception:  # noqa: BLE001
                logger.exception("falha anexando output %s", output)

        # Mensagem final de fechamento (curta — painel já é o cabeçalho)
        glyph = {
            StatusMissao.CONCLUIDA.value: "🟢",
            StatusMissao.FALHOU.value: "🔴",
            StatusMissao.ABORTADA.value: "⏸️",
        }.get(missao.status, "•")
        msg = f"{glyph} Missão {missao.id} encerrada — status: {missao.status}"
        notify_bin = self._kobe_home / "bot" / "bin" / "kobe-notify"
        try:
            subprocess.run(
                [str(notify_bin), msg],
                env=env, timeout=15, check=False, capture_output=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("falha mandando notify de fechamento")


# --- aplicação de deltas (puro, sem IO) ----------------------------

def _aplicar_evento(missao: Missao, ev: Evento) -> None:
    """Muta o `Missao` em memória conforme o tipo do evento. Caller fica
    com o lock e dá salvar. Eventos desconhecidos são ignorados (log)."""
    if ev.tipo == TipoEvento.MISSAO_CRIADA.value:
        # Idempotente — handler já cria estado. Mantemos pra log/audit.
        return
    if ev.tipo == TipoEvento.NARRATIVA_ATUALIZADA.value:
        nova = (ev.dados or {}).get("narrativa")
        if isinstance(nova, str):
            missao.narrativa = nova
        return
    if ev.tipo == TipoEvento.TAREFA_INICIADA.value and ev.tarefa_id:
        t = missao.tarefa(ev.tarefa_id)
        if t is not None:
            t.status = StatusTarefa.RODANDO.value
            t.pid = (ev.dados or {}).get("pid") or t.pid
            t.iniciado_em = (ev.dados or {}).get("iniciado_em") or t.iniciado_em
            if missao.status == StatusMissao.PLANEJADA.value:
                missao.status = StatusMissao.EM_ANDAMENTO.value
        return
    if ev.tipo == TipoEvento.TAREFA_PROGRESSO.value and ev.tarefa_id:
        t = missao.tarefa(ev.tarefa_id)
        if t is not None:
            prog = (ev.dados or {}).get("progresso")
            if isinstance(prog, int):
                t.progresso = max(0, min(100, prog))
        return
    if ev.tipo == TipoEvento.TAREFA_CONCLUIDA.value and ev.tarefa_id:
        t = missao.tarefa(ev.tarefa_id)
        if t is not None:
            t.status = StatusTarefa.CONCLUIDA.value
            t.output_path = (ev.dados or {}).get("output_path") or t.output_path
        return
    if ev.tipo == TipoEvento.TAREFA_FALHOU.value and ev.tarefa_id:
        t = missao.tarefa(ev.tarefa_id)
        if t is not None:
            t.status = StatusTarefa.FALHOU.value
            t.erro = (ev.dados or {}).get("erro") or t.erro
        return
    if ev.tipo == TipoEvento.MISSAO_CONCLUIDA.value:
        if missao.status not in (
            StatusMissao.CONCLUIDA.value, StatusMissao.FALHOU.value,
            StatusMissao.ABORTADA.value,
        ):
            missao.status = StatusMissao.CONCLUIDA.value
        return
    if ev.tipo == TipoEvento.MISSAO_ABORTADA.value:
        missao.status = StatusMissao.ABORTADA.value
        return
    if ev.tipo == TipoEvento.INCONSISTENCIA_DETECTADA.value:
        # Informativo — orquestrador appendou pra registrar que detectou
        # estado estranho e NÃO consertou por conta. Sem mudança de estado.
        # Painel não muda; operador foi avisado via kobe-notify pelo próprio
        # orquestrador (vide regra de honestidade no PREAMBULO).
        return
    logger.debug("evento ignorado (tipo desconhecido): %s", ev.tipo)
