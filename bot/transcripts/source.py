"""A fonte do Keyko que roda o coletor por relógio.

──────────────────────────────────────────────────────────────────────────
POR QUE ELA NÃO ACORDA NINGUÉM
──────────────────────────────────────────────────────────────────────────

O Keyko é o daemon de despertar: as fontes dele devolvem `Despertar`, e cada
`Despertar` dispara um `claude -p`. Esta fonte **devolve sempre lista vazia**.

Não é uma adaptação forçada — é o que o próprio protocolo prevê, com todas as
letras: *"Source faz seu trabalho colateral (atualizar painel, marcar
ultimo_disparo etc.) e retorna lista de despertares devidos AGORA. Lista vazia é
normal."*

E é a escolha certa aqui por um motivo concreto: o recurso escasso desta campanha
é **cota de assinatura**, não dinheiro. Copiar bytes de um arquivo pra outro não
precisa de um modelo, e acordar um pra isso, todo dia, seria gastar o recurso mais
caro na tarefa mais burra do sistema. A coleta roda dentro do próprio `tick()`,
em processo, a custo de cota **zero**.

──────────────────────────────────────────────────────────────────────────
O PRIMEIRO TICK É IMEDIATO, E ISSO É O REMÉDIO CONTRA A LACUNA L4
──────────────────────────────────────────────────────────────────────────

O Keyko inicializa `proximo_tick` em zero, então toda fonte roda **assim que o
daemon sobe** e só depois entra na cadência. Para um coletor diário isso importa
mais do que parece: a falha clássica de agendamento — o daemon reiniciar e o
relógio "pular" um dia — deixa de existir, porque reiniciar **causa** uma coleta
em vez de adiá-la.

O que sobra da L4 é o caso em que o daemon fica fora do ar por muito tempo. Para
esse, a fonte compara a idade da última coleta bem-sucedida **antes** de coletar,
e avisa. Assim o buraco aparece justamente no momento em que ele acabou de
existir, e não semanas depois.

──────────────────────────────────────────────────────────────────────────
UMA ESCOLHA DE HONESTIDADE SOBRE O AVISO
──────────────────────────────────────────────────────────────────────────

Mandar o aviso pelo Telegram exige saber **para qual conversa**. Esta fonte não
adivinha: ela usa `TRANSCRIPT_ALERT_CHAT_ID` (e, opcionalmente,
`TRANSCRIPT_ALERT_THREAD_ID`) e, sem isso, **só registra em log**. Chutar um
tópico faria uma mensagem de saúde do sistema cair numa conversa qualquer, o que
é pior que não mandar.

E o aviso não depende só daqui: o dispatcher do Coder também lê a marca a cada
abertura de sala (o terceiro degrau da mitigação). Quem vigia não pode ser só o
vigiado — se o Keyko estiver fora, esta fonte não roda, e é exatamente aí que o
degrau de fora salva.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from bot.keyko.models import Despertar
from bot.transcripts import collector as col
from bot.transcripts import state as st

logger = logging.getLogger("kobe.transcripts.source")

# Uma vez por dia, como o briefing pede. Reiniciar o daemon também dispara (o
# primeiro tick é imediato), então a cadência real é "diária, ou quando o Keyko
# subir" — o que só melhora a garantia.
INTERVALO_PADRAO_S = 24 * 60 * 60

# Não repetir o mesmo aviso de relógio mais que uma vez por dia. Um alerta que
# se repete a cada tick vira ruído, e ruído é ignorado — que é o mesmo destino
# do silêncio que ele veio combater.
INTERVALO_AVISO_S = 24 * 60 * 60


def _env(nome: str) -> str:
    from bot.work_catalog import _env as ler

    return ler(nome)


def _intervalo() -> float:
    raw = _env("TRANSCRIPT_COLLECT_INTERVAL_S")
    try:
        valor = float(raw) if raw else INTERVALO_PADRAO_S
    except ValueError:
        valor = INTERVALO_PADRAO_S
    # Um piso existe pra que um valor torto no `.env` não transforme o coletor
    # num laço apertado lendo 24 MB de cabeçalhos a cada segundo.
    return max(60.0, valor)


class TranscriptsSource:
    """Roda o coletor por relógio. Nunca devolve despertar."""

    def __init__(self, *, kobe_home: Path, bot_token: str) -> None:
        self._kobe_home = kobe_home
        self._bot_token = bot_token

    @property
    def nome(self) -> str:
        return "transcripts"

    @property
    def intervalo_s(self) -> float:
        return _intervalo()

    def tick(self) -> list[Despertar]:
        """Uma passada de coleta. **Nunca levanta** — o Keyko é single-threaded.

        O loop do Keyko já protege contra fonte bugada, mas depender disso seria
        deixar o tratamento pra quem não tem contexto pra tratar. Aqui a falha
        vira log, e a próxima passada tenta de novo.
        """
        if not col.collector_enabled():
            return []

        try:
            self._avisar_se_envelheceu()
        except Exception:  # noqa: BLE001 — o aviso não pode impedir a coleta
            logger.exception("transcripts: falha ao avaliar o envelhecimento")

        try:
            resultado = col.collect_once()
        except st.LockBusy:
            # Já há uma passada rodando (uma execução à mão, por exemplo). A
            # outra faz o mesmo trabalho; não há nada a fazer aqui.
            logger.info("transcripts: coleta já em andamento — tick dispensado")
            return []
        except Exception:  # noqa: BLE001
            logger.exception("transcripts: coleta falhou")
            return []

        if resultado.touched:
            logger.info(
                "transcripts: %d sala(s) com novidade, %d bytes colhidos",
                len(resultado.touched), resultado.total_copied,
            )
        if resultado.errors:
            logger.warning(
                "transcripts: %d sessão(ões) com erro na coleta: %s",
                len(resultado.errors),
                [(e.session_id[:8], e.error) for e in resultado.errors[:5]],
            )
        if resultado.catalog_note:
            logger.warning("transcripts: %s", resultado.catalog_note)

        return []

    # -- o aviso de relógio ---------------------------------------------

    def _avisar_se_envelheceu(self) -> None:
        """Compara a idade da última coleta bem-sucedida ANTES de coletar.

        A ordem é o ponto: depois de coletar, a marca está sempre fresca e não
        haveria nada a avisar. É olhando antes que se enxerga o buraco — e o
        momento em que se enxerga é justamente o instante em que ele terminou.
        """
        aviso = col.staleness_warning()
        if not aviso:
            return

        dest_root = col.default_dest_root()
        with st.exclusive_lock(dest_root):
            estado = st.load(dest_root)
            desde = st.age_hours(estado.get("last_stale_notice_at"))
            if desde is not None and desde * 3600 < INTERVALO_AVISO_S:
                return
            estado["last_stale_notice_at"] = st.now_iso()
            st.save(dest_root, estado)

        logger.warning("transcripts: %s", aviso)
        self._notificar(f"⚠️ [transcripts] {aviso}")

    def _notificar(self, texto: str) -> None:
        """Manda pelo Telegram — só se houver destino DECLARADO.

        Sem `TRANSCRIPT_ALERT_CHAT_ID`, fica no log. Escolher um tópico por
        conta própria faria uma mensagem de saúde do sistema cair numa conversa
        qualquer, o que é pior que não mandar.
        """
        chat_id = _env("TRANSCRIPT_ALERT_CHAT_ID")
        if not chat_id:
            logger.info(
                "transcripts: sem TRANSCRIPT_ALERT_CHAT_ID — o aviso ficou só no log"
            )
            return

        helper = self._kobe_home / "bot" / "bin" / "kobe-notify"
        if not helper.is_file():
            logger.warning("transcripts: %s não existe — aviso não enviado", helper)
            return

        env = dict(os.environ)
        env["KOBE_TELEGRAM_BOT_TOKEN"] = self._bot_token
        env["KOBE_CHAT_ID"] = chat_id
        thread_id = _env("TRANSCRIPT_ALERT_THREAD_ID")
        if thread_id:
            env["KOBE_THREAD_ID"] = thread_id
        else:
            env.pop("KOBE_THREAD_ID", None)

        try:
            subprocess.run(
                [str(helper), texto], env=env, timeout=30,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        except Exception:  # noqa: BLE001 — falhar em avisar não pode derrubar o tick
            logger.exception("transcripts: falha ao enviar o aviso de relógio")


def build(*, kobe_home: Path, bot_token: str) -> Optional[TranscriptsSource]:
    """A fonte, ou `None` se o coletor está desligado.

    Devolver `None` com a chave off mantém o registro do Keyko limpo: uma fonte
    registrada que não faz nada aparece no log de inicialização como se estivesse
    trabalhando, e "quem o Keyko está observando" deixa de ser verdade.
    """
    if not col.collector_enabled():
        return None
    return TranscriptsSource(kobe_home=kobe_home, bot_token=bot_token)
