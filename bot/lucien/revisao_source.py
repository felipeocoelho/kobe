"""A fonte do Keyko que manda a revisão da memória (F5).

Como as outras fontes do LUCIEN, **nunca devolve `Despertar`**: a mensagem é um
modelo fixo montado em código (`revisao.py`), e acordar um modelo para escrever
uma lista que o código já sabe seria gastar cota e abrir espaço para inventar
item.

O trabalho cabe no `tick()`: é um punhado de `SELECT` e uma chamada ao
`kobe-notify`, uma vez por semana. A pergunta de todo tick — *"chegou a hora?"* —
é só ler um arquivo e fazer uma conta de calendário; o banco só é aberto quando a
resposta é sim.

QUANDO É A HORA
---------------
O horário vem de `LUCIEN_REVIEW_WHEN` (cron, fuso de Brasília; padrão segunda 9h).
A cada tick calcula-se o **último horário agendado que já passou**. É hora se
esse horário é mais novo que o último cumprido (`ultimo_marco`) **e** mais novo
que o momento em que a fonte foi ligada (`ligada_em`).

Daí saem as duas propriedades que importam:

- **VPS fora na segunda → reposição única.** Quando o Keyko volta, o último
  horário agendado ainda não foi cumprido: a revisão sai uma vez, e o marco
  cumprido impede a segunda.
- **Ligar a chave não dispara na hora.** A primeira revisão espera o próximo
  horário agendado — ligar uma chave numa quarta não pode mandar uma mensagem
  na quarta.

ENVIO QUE FALHA
---------------
Nada é marcado: a revisão anterior continua aberta e o marco não é cumprido, então
o próximo tick tenta de novo. Na terceira falha seguida, um aviso de saúde
(`aviso.avisar`) — uma vez, não a cada tentativa.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from bot import lucien as cfg
from bot.keyko.models import Despertar

logger = logging.getLogger("kobe.lucien.revisao_source")

INTERVALO_S = 600.0
FALHAS_PARA_AVISAR = 3


def ultimo_agendado(expr: str, agora: datetime) -> datetime:
    """O horário agendado mais recente que já passou (≤ agora), em UTC."""
    from croniter import croniter

    from bot.lucien.revisao import TZ

    base = agora.astimezone(TZ)
    prev = croniter(expr, base).get_prev(datetime)
    if prev.tzinfo is None:
        prev = prev.replace(tzinfo=TZ)
    return prev.astimezone(timezone.utc)


class RevisaoSource:
    def __init__(self, *, kobe_home, db_factory, bot_token: str = "") -> None:
        self._kobe_home = str(kobe_home)
        self._db_factory = db_factory
        self._bot_token = bot_token

    @property
    def nome(self) -> str:
        return "lucien-review"

    @property
    def intervalo_s(self) -> float:
        return INTERVALO_S

    def tick(self, *, agora: Optional[datetime] = None) -> list[Despertar]:
        """**Nunca levanta** — uma fonte bugada não pode derrubar os Alertas."""
        try:
            self._tick(agora or datetime.now(timezone.utc))
        except Exception:  # noqa: BLE001
            logger.exception("revisao: o tick falhou")
        return []

    def _tick(self, agora: datetime) -> None:
        from bot.lucien import revisao

        if not cfg.revisao_habilitada():
            return
        marco = ultimo_agendado(cfg.revisao_quando(), agora)
        with revisao.travado(self._kobe_home):
            estado = revisao.ler_estado(self._kobe_home)
            if not estado.get("ligada_em"):
                estado["ligada_em"] = revisao._iso(agora)
                revisao.gravar_estado(self._kobe_home, estado)
                logger.info("revisao: ligada agora — a primeira sai no próximo "
                            "horário agendado depois deste")
                return
        ligada = revisao._de_iso(estado.get("ligada_em"))
        cumprido = revisao._de_iso(estado.get("ultimo_marco"))
        if ligada is not None and marco <= ligada:
            return
        if cumprido is not None and marco <= cumprido:
            return

        cx = self._db_factory()
        try:
            r = revisao.rodar_ciclo(cx, self._kobe_home, agora=agora, marco=marco,
                                    token=self._bot_token)
        finally:
            try:
                cx.rollback()  # só leitura — fecha a transação implícita
                cx.close()
            except Exception:  # noqa: BLE001
                pass
        if r.get("enviada"):
            logger.info("revisao: enviada %s com %s itens", r.get("id"), r.get("itens"))
        elif r.get("ok"):
            logger.info("revisao: %s", r.get("motivo"))
        else:
            logger.warning("revisao: não saiu — %s", r.get("motivo"))
            if int(r.get("falhas") or 0) == FALHAS_PARA_AVISAR:
                from bot.lucien import aviso

                aviso.avisar(
                    self._kobe_home,
                    f"a revisão da memória não consegue sair ({r.get('motivo')}, "
                    f"{FALHAS_PARA_AVISAR} tentativas seguidas). Sigo tentando a "
                    "cada 10 minutos; a revisão anterior continua aberta.",
                )


def build(*, kobe_home=None, bot_token: str = "") -> Optional[RevisaoSource]:
    """A fonte, ou `None` com a chave desligada ou sem destino.

    Sem destino resolvível (`LUCIEN_REVIEW_CHAT_ID`, ou um único privado na
    whitelist), a fonte também não se registra: ligada e sem para onde mandar,
    ela apareceria no log como trabalhando sem nunca entregar nada.
    """
    if not cfg.revisao_habilitada():
        return None
    if cfg.revisao_chat_id() is None:
        logger.warning(
            "revisao: LUCIEN_REVIEW_ENABLED ligada, mas sem destino — defina "
            "LUCIEN_REVIEW_CHAT_ID (o chat privado do operador com o bot)"
        )
        return None

    from pathlib import Path

    home = Path(kobe_home) if kobe_home else Path(__file__).resolve().parents[2]

    def _abrir():
        from bot.lucien import store

        url = (os.environ.get("DATABASE_URL") or "").strip()
        if not url:
            raise RuntimeError("DATABASE_URL ausente")
        return store.conectar(url)

    return RevisaoSource(kobe_home=home, db_factory=_abrir, bot_token=bot_token)
