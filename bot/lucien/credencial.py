"""A credencial do LUCIEN, e quando ela vence.

DE ONDE VEIO ESTE ARQUIVO (Etapa 0, 07/10/2026)
-----------------------------------------------
Medido nesta data, com `strace`: mesmo com `CLAUDE_CONFIG_DIR` isolado, a CLI do
LUCIEN lia **`~/.claude/.credentials.json`** — a credencial do operador, a mesma
do Hal e das salas. O histórico da CLI mostra `/login` manual em 29/05, 29/06,
30/07, 26/08 e 05/10: quase todo mês, essa credencial vence e TUDO para. Em
04-05/10 o LUCIEN foi quem o operador notou.

Decisão do operador em 07/10 (opções B + C do plano da sala `02b175bd`):

- **B — token de longa duração da assinatura** (`claude setup-token`), só para o
  LUCIEN. O operador gera uma vez; o arquivo fica em
  `user-data/secrets/lucien-claude-token` (modo 600, fora do git) e entra no
  subprocesso como `CLAUDE_CODE_OAUTH_TOKEN`. Verificado em 07/10: com a
  variável definida, a CLI troca para `authMethod: oauth_token` e IGNORA a
  credencial compartilhada. **Sem o arquivo, nada muda** — vale a compartilhada.
  Custo: zero (continua sendo a assinatura).
- **C — aviso antecipado**: a credencial compartilhada declara
  `refreshTokenExpiresAt`; o Keyko lê SÓ essa data, de hora em hora, e avisa 7
  dias e 1 dia antes (cada aviso uma vez só). O token do LUCIEN não declara validade nenhuma: a data
  dele é ESTIMADA (data do arquivo + `LUCIEN_TOKEN_VALIDADE_DIAS`, padrão 365) e
  a mensagem diz que é estimativa.

Levar o token de longa duração ao Kobe inteiro (Hal, salas) é OUTRA sala, por
decisão do operador. Até lá, o aviso C é o que impede o `/login` mensal de ser
surpresa.

O QUE ESTE ARQUIVO NUNCA FAZ
----------------------------
Nunca loga, imprime nem devolve em mensagem o conteúdo de token nenhum. Da
credencial compartilhada lê só o campo de data.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("kobe.lucien.credencial")

MARCOS_DIAS = (7, 1)


def _env(nome: str) -> str:
    return (os.environ.get(nome) or "").strip()


def caminho_token(kobe_home: str) -> str:
    return _env("LUCIEN_OAUTH_TOKEN_FILE") or os.path.join(
        kobe_home, "user-data", "secrets", "lucien-claude-token")


def caminho_compartilhada() -> str:
    return _env("LUCIEN_CREDENCIAL_COMPARTILHADA") or os.path.expanduser(
        "~/.claude/.credentials.json")


def ler_token(kobe_home: str) -> Optional[str]:
    """O token de longa duração, ou `None` (ausente, vazio ou exposto demais).

    Arquivo legível por grupo/outros é RECUSADO, não usado com ressalva: um
    token de assinatura com permissão aberta é exatamente o vazamento que o
    modo 600 existe pra impedir. Recusar cai na credencial compartilhada — o
    LUCIEN continua funcionando, e o log diz por quê.
    """
    p = caminho_token(kobe_home)
    try:
        st = os.stat(p)
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("lucien: não consegui ler %s — uso a credencial compartilhada", p)
        return None
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "lucien: %s tem permissão aberta (%o) — RECUSADO; uso a credencial "
            "compartilhada. Conserte com: chmod 600 %s", p, st.st_mode & 0o777, p)
        return None
    try:
        with open(p, encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError:
        logger.warning("lucien: não consegui ler %s — uso a credencial compartilhada", p)
        return None
    return token or None


def modo(kobe_home: str) -> str:
    """`token` (o de longa duração, só do LUCIEN) ou `compartilhada`."""
    return "token" if ler_token(kobe_home) else "compartilhada"


def o_que_fazer_no_login(kobe_home: str) -> str:
    """A instrução certa quando a falha é de login — depende de QUAL credencial."""
    if modo(kobe_home) == "token":
        return ("o token de longa duração do LUCIEN venceu ou foi revogado. Gere "
                "outro com `claude setup-token` e instale com "
                "`bot/bin/kobe-lucien token instalar` (o Hal e as salas NÃO "
                "dependem dele)")
    return ("a credencial da CLI venceu. Na VPS, rode `claude` e use `/login` "
            "(é a mesma credencial do Hal e das salas — elas também param)")


# ── validade ─────────────────────────────────────────────────────────────

@dataclass
class Validade:
    chave: str          # `compartilhada` | `token`
    nome: str           # como a mensagem chama
    vence_em: datetime
    estimada: bool
    como_renovar: str


def validades(kobe_home: str) -> list[Validade]:
    """As datas de vencimento conhecidas. Lista vazia é normal (nada a avisar)."""
    out: list[Validade] = []
    p = caminho_compartilhada()
    try:
        with open(p, encoding="utf-8") as fh:
            dados = json.load(fh)
        ms = ((dados or {}).get("claudeAiOauth") or {}).get("refreshTokenExpiresAt")
        if isinstance(ms, (int, float)) and ms > 0:
            out.append(Validade(
                chave="compartilhada",
                nome="o login da CLI na VPS (Hal, salas e o que não tiver token próprio)",
                vence_em=datetime.fromtimestamp(ms / 1000, tz=timezone.utc),
                estimada=False,
                como_renovar="rode `claude` na VPS e use `/login`",
            ))
    except FileNotFoundError:
        pass
    except Exception:  # noqa: BLE001 — arquivo de terceiro; formato pode mudar
        logger.warning("lucien: não li a validade em %s (formato mudou?)", p)

    tp = caminho_token(kobe_home)
    if ler_token(kobe_home):
        try:
            criado = datetime.fromtimestamp(os.stat(tp).st_mtime, tz=timezone.utc)
            dias = float(_env("LUCIEN_TOKEN_VALIDADE_DIAS") or 365)
            out.append(Validade(
                chave="token",
                nome="o token de longa duração do LUCIEN",
                vence_em=criado + timedelta(days=dias),
                estimada=True,
                como_renovar=("gere outro com `claude setup-token` e instale com "
                              "`bot/bin/kobe-lucien token instalar`"),
            ))
        except (OSError, ValueError):
            pass
    return out


def _dia(d: datetime) -> str:
    try:
        from zoneinfo import ZoneInfo

        return d.astimezone(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y às %H:%M")
    except Exception:  # noqa: BLE001
        return d.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _quanto_falta(falta: timedelta) -> str:
    """Arredondado pra CIMA. Truncar dizia "faltam ~6 dias" no aviso de 7 (a
    checagem sai um pouco depois do marco) — número menor que o marco confunde."""
    import math

    horas = falta.total_seconds() / 3600
    if math.ceil(horas) < 24:
        return f"{max(1, math.ceil(horas))} hora(s)"
    return f"{math.ceil(horas / 24)} dia(s)"


def checar(estado: dict, vals: list[Validade], agora: datetime) -> tuple[dict, list[str]]:
    """Que avisos de vencimento mandar agora. **Pura.**

    Cada marco (7 dias, 1 dia, vencido) sai UMA vez por data de vencimento:
    renovar muda a data, e os marcos da data nova voltam a valer.
    """
    novo = dict(estado or {})
    avisados = dict(novo.get("_validade_avisada") or {})
    msgs: list[str] = []
    for v in vals:
        chave_data = v.vence_em.isoformat()
        ja = avisados.get(v.chave) or {}
        if ja.get("data") != chave_data:
            ja = {"data": chave_data, "marcos": []}
        falta = v.vence_em - agora
        marco = None
        if falta <= timedelta(0):
            marco = "vencido"
        else:
            for d in sorted(MARCOS_DIAS):
                if falta <= timedelta(days=d):
                    marco = str(d)
                    break
        if marco and marco not in ja["marcos"]:
            # Um marco mais urgente cobre os menos urgentes: quem só soube a 1
            # dia não precisa também do "faltam 7".
            for m in ("7", "1", "vencido"):
                if m not in ja["marcos"]:
                    ja["marcos"].append(m)
                if m == marco:
                    break
            est = " (data ESTIMADA: o token não declara validade)" if v.estimada else ""
            if marco == "vencido":
                msgs.append(f"{v.nome} VENCEU em {_dia(v.vence_em)}{est}. Para "
                            f"renovar: {v.como_renovar}.")
            else:
                msgs.append(f"{v.nome} vence em {_dia(v.vence_em)}{est} — faltam "
                            f"{_quanto_falta(falta)}. Para não parar: "
                            f"{v.como_renovar} antes dessa data.")
        avisados[v.chave] = ja
    novo["_validade_avisada"] = avisados
    return novo, msgs
