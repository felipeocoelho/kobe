"""A saúde do LUCIEN: quando ele PAROU, e quando avisar disso.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
De 04/10/2026 17:45 a 05/10/2026 17:12 (Brasília), **144 rodadas seguidas
falharam** — a CLI sem login — e o operador não recebeu nada. Medido no código,
eram quatro buracos em fila: o worker só logava; o log ia para `/dev/null`
(worker destacado); a fonte disparava e esquecia; e o `_avisar()` da fonte só
escrevia no log. O mesmo lote voltava a ficar devido e era redisparado, até o teto
de 6 por hora, por um dia inteiro.

Aqui mora a REGRA de quando falar. Ela é uma função pura — recebe o que foi
observado no banco e o estado anterior, devolve o estado novo e, talvez, uma
mensagem —, para que dê para provar o comportamento com a sequência real de
04-05/10 sem banco, sem Telegram e sem relógio.

A REGRA (o represamento)
------------------------
Avisar não pode virar enxurrada: um aviso por falha teria dado 144 mensagens.

- **abre** um incidente quando há `LUCIEN_FALHAS_SEGUIDAS` rodadas seguidas sem
  êxito (padrão 3), **ou** o mesmo número de disparos sem que nenhuma rodada nova
  apareça no banco (o worker que morre antes de registrar — import quebrado,
  banco fora). Abrir manda **uma** mensagem, com a causa classificada e o que
  fazer;
- **durante**, a repetição só soma. Um **lembrete** a cada
  `LUCIEN_AVISO_LEMBRETE_H` horas (padrão 6). Se a CLASSE da causa mudar, isso é
  informação nova e sai uma mensagem;
- **fecha** quando aparece uma rodada bem-sucedida depois do início, com **uma**
  mensagem de "voltou" dizendo quantas falharam e que nada se perdeu.

Na sequência de 04-05/10 isso dá 5 mensagens em 23h30: abertura, 3 lembretes,
"voltou". O teste `test_lucien_saude.py` prova exatamente esse número.

O ESTADO VIVE EM DISCO
----------------------
`user-data/lucien/saude.json`. Em memória, um restart do Keyko esqueceria o
incidente aberto e mandaria outro aviso de abertura (ou, pior, nunca mandaria o
"voltou"). Arquivo ilegível é tratado como vazio — e logado —, nunca como erro:
a saúde do aviso não pode virar uma segunda fonte de pane.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("kobe.lucien.saude")

FUSO_OPERADOR = "America/Sao_Paulo"

# ── classificação da causa ───────────────────────────────────────────────

_CLASSES = (
    ("login", re.compile(
        r"not logged in|/login|oauth|\b401\b|authenticat|credential|"
        r"invalid (api )?(key|token)|token (has )?expired|unauthori[sz]ed",
        re.I)),
    ("tempo", re.compile(r"não respondeu em|timed? ?out", re.I)),
    ("ilegivel", re.compile(r"json|envelope|truncad|respondeu vazio", re.I)),
    ("cli_ausente", re.compile(r"não foi encontrada", re.I)),
)

_O_QUE_FAZER = {
    "login": ("a credencial da CLI venceu. Na VPS, rode `claude` e use `/login` "
              "(é a mesma credencial do Hal e das salas — elas também param)"),
    "tempo": ("o modelo não está respondendo a tempo. Costuma passar sozinho; "
              "se durar, vale olhar o status da Anthropic"),
    "ilegivel": ("o modelo respondeu, mas fora do formato. O lote é relido; se "
                 "insistir, o texto cru está em `lucien_runs.error`"),
    "cli_ausente": "a CLI `claude` sumiu do PATH do Keyko",
    "worker": ("o worker morre antes de registrar a rodada. Ver "
               "`user-data/lucien/worker.log`"),
    "outro": "ver `kobe-lucien status` e `user-data/lucien/worker.log`",
}

_ROTULO = {
    "login": "sem login",
    "tempo": "modelo sem responder",
    "ilegivel": "resposta ilegível",
    "cli_ausente": "CLI ausente",
    "worker": "worker morrendo sem registrar",
    "outro": "erro",
}


def classificar(erro: Optional[str]) -> str:
    """A classe da causa, a partir do texto de `lucien_runs.error`."""
    texto = erro or ""
    for classe, padrao in _CLASSES:
        if padrao.search(texto):
            return classe
    return "outro"


def _causa_curta(erro: Optional[str]) -> str:
    """O trecho que diz a causa — o `causa: …` que o brain põe na frente, ou o
    começo do texto. Nunca o envelope inteiro numa mensagem de Telegram."""
    texto = " ".join((erro or "").split())
    m = re.search(r"causa: (.*?)(?: · |$)", texto)
    if m:
        return m.group(1)[:200]
    return texto[:200]


# ── observação e estado ──────────────────────────────────────────────────

@dataclass
class Observacao:
    """O que o banco diz agora. Montada pela fonte; nada aqui consulta nada."""

    falhas_seguidas: int = 0
    primeira_falha_em: Optional[datetime] = None
    ultimo_erro: Optional[str] = None
    ultimo_exito_em: Optional[datetime] = None
    disparos_sem_rodada: int = 0
    ultimo_codigo_worker: Optional[int] = None


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.astimezone(timezone.utc).isoformat() if d else None


def _de_iso(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _hora(d: Optional[datetime]) -> str:
    """Data e hora no fuso do operador. O servidor está em UTC; o operador não."""
    if d is None:
        return "?"
    try:
        from zoneinfo import ZoneInfo

        d = d.astimezone(ZoneInfo(FUSO_OPERADOR))
    except Exception:  # noqa: BLE001 — sem tzdata, cai pra UTC, dito
        return d.astimezone(timezone.utc).strftime("%d/%m %H:%M UTC")
    return d.strftime("%d/%m %H:%M")


def _duracao(de: Optional[datetime], ate: datetime) -> str:
    if de is None:
        return "?"
    minutos = max(0, int((ate - de).total_seconds() // 60))
    if minutos < 60:
        return f"{minutos} min"
    h, m = divmod(minutos, 60)
    return f"{h}h{m:02d}" if m else f"{h}h"


def decidir(estado: dict, obs: Observacao, agora: datetime, *,
            limiar: int = 3, lembrete_h: float = 6.0,
            o_que_fazer_login: Optional[str] = None) -> tuple[dict, Optional[str]]:
    """Estado novo e, talvez, a mensagem a mandar. **Pura**: não lê nem grava.

    `o_que_fazer_login` troca a instrução da classe `login` — ela depende de
    QUAL credencial o LUCIEN usa (a compartilhada ou o token próprio).
    """
    fazer = dict(_O_QUE_FAZER)
    if o_que_fazer_login:
        fazer["login"] = o_que_fazer_login
    novo = dict(estado or {})
    aberto = bool(novo.get("aberto"))
    desde = _de_iso(novo.get("desde"))

    por_falha = obs.falhas_seguidas >= limiar
    por_silencio = obs.disparos_sem_rodada >= limiar
    parado = por_falha or por_silencio

    if por_falha:
        classe = classificar(obs.ultimo_erro)
        causa = _causa_curta(obs.ultimo_erro)
    elif por_silencio:
        classe = "worker"
        causa = (f"{obs.disparos_sem_rodada} disparos seguidos sem nenhuma "
                 f"rodada registrada"
                 + (f" (o worker saiu com código {obs.ultimo_codigo_worker})"
                    if obs.ultimo_codigo_worker not in (None, 0) else ""))
    else:
        classe, causa = None, None

    # ── fecha: houve êxito depois do começo do incidente ──
    if aberto and obs.ultimo_exito_em and desde and obs.ultimo_exito_em > desde \
            and not parado:
        falhas = int(novo.get("falhas") or 0)
        msg = (
            f"voltou a funcionar. Entre {_hora(desde)} e {_hora(obs.ultimo_exito_em)} "
            f"({_duracao(desde, obs.ultimo_exito_em)}), {falhas} rodada(s) falharam "
            f"— causa: {_ROTULO.get(novo.get('classe'), 'erro')}. O cursor não "
            f"andou nesse tempo: o lote é relido agora, nada se perdeu."
        )
        return {"aberto": False, "ultimo_incidente": {
            "desde": novo.get("desde"), "ate": _iso(obs.ultimo_exito_em),
            "falhas": falhas, "classe": novo.get("classe")}}, msg

    if not parado:
        return novo, None

    falhas = max(obs.falhas_seguidas, obs.disparos_sem_rodada)

    # ── abre ──
    if not aberto:
        inicio = obs.primeira_falha_em or agora
        novo.update({
            "aberto": True, "desde": _iso(inicio), "falhas": falhas,
            "classe": classe, "causa": causa, "ultimo_aviso": _iso(agora),
            "avisos": 1,
        })
        msg = (
            f"PAREI — o registro de estado não está sendo atualizado. "
            f"{falhas} tentativa(s) seguidas falharam desde {_hora(inicio)}. "
            f"Causa: {_ROTULO[classe]} ({causa}). O que fazer: {fazer[classe]}. "
            f"Vou te lembrar a cada {lembrete_h:g}h enquanto durar, e avisar quando voltar."
        )
        return novo, msg

    # ── durante ──
    novo["falhas"] = falhas
    novo["causa"] = causa
    if classe != novo.get("classe"):
        anterior = novo.get("classe")
        novo["classe"] = classe
        novo["ultimo_aviso"] = _iso(agora)
        novo["avisos"] = int(novo.get("avisos") or 0) + 1
        msg = (
            f"continuo parado, mas a causa MUDOU: de "
            f"{_ROTULO.get(anterior, 'erro')} para {_ROTULO[classe]} ({causa}). "
            f"O que fazer: {fazer[classe]}."
        )
        return novo, msg

    ultimo = _de_iso(novo.get("ultimo_aviso"))
    if ultimo is None or agora - ultimo >= timedelta(hours=lembrete_h):
        novo["ultimo_aviso"] = _iso(agora)
        novo["avisos"] = int(novo.get("avisos") or 0) + 1
        msg = (
            f"continuo parado há {_duracao(desde, agora)} — {falhas} tentativa(s) "
            f"falharam desde {_hora(desde)}. Causa: {_ROTULO[classe]} ({causa}). "
            f"O que fazer: {fazer[classe]}."
        )
        return novo, msg
    return novo, None


# ── persistência ─────────────────────────────────────────────────────────

def caminho(kobe_home: str) -> str:
    return os.path.join(kobe_home, "user-data", "lucien", "saude.json")


def ler(kobe_home: str) -> dict:
    """O estado salvo. Ausente ou ilegível = vazio (e ilegível é logado)."""
    p = caminho(kobe_home)
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001 — estado ruim não pode virar pane
        logger.warning("lucien: %s ilegível — recomeçando do zero", p)
        return {}


def gravar(kobe_home: str, estado: dict) -> None:
    """Grava atômico (temporário + rename): um crash no meio não deixa meio JSON."""
    p = caminho(kobe_home)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".saude-", dir=os.path.dirname(p))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(estado, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── a observação, do banco ───────────────────────────────────────────────

def observar(cx, *, janela: int = 200) -> Observacao:
    """Lê as rodadas incrementais mais recentes e conta as falhas seguidas.

    Só rodadas TERMINADAS contam: uma que está acontecendo agora não é falha
    nem êxito ainda.
    """
    cur = cx.cursor()
    cur.execute(
        "SELECT started_at, ok, error FROM lucien_runs"
        " WHERE mode = 'incremental' AND finished_at IS NOT NULL"
        " ORDER BY started_at DESC LIMIT %s",
        (janela,),
    )
    linhas = cur.fetchall()
    obs = Observacao()
    for ln in linhas:
        if ln["ok"]:
            break
        obs.falhas_seguidas += 1
        obs.primeira_falha_em = ln["started_at"]
        if obs.ultimo_erro is None:
            obs.ultimo_erro = ln["error"]
    cur.execute(
        "SELECT MAX(started_at) AS m FROM lucien_runs"
        " WHERE mode = 'incremental' AND ok"
    )
    obs.ultimo_exito_em = cur.fetchone()["m"]
    return obs


def ultima_rodada_em(cx) -> Optional[datetime]:
    cur = cx.cursor()
    cur.execute("SELECT MAX(started_at) AS m FROM lucien_runs WHERE mode = 'incremental'")
    return cur.fetchone()["m"]
