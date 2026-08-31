"""Os tipos do LUCIEN — o que entra no modelo e o que sai dele.

A separação entre `ClaimProposta` e a linha de `lucien_claims` é o ponto de todo
o desenho: **proposta é o que o modelo disse; linha é o que sobreviveu à
validação.** Nada atravessa sem passar por `store.aplicar`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class Mensagem:
    """Uma mensagem do lote, como o modelo a vê."""

    seq: int
    id: str
    role: str
    created_at: datetime
    content: str
    audio: bool

    @property
    def quem(self) -> str:
        return "operador" if self.role == "user" else "agente"


@dataclass
class Lote:
    """O que se mostra ao modelo numa rodada, e é **exatamente** o universo de
    coisas que ele pode citar.

    `seqs` é o conjunto contra o qual a trava de origem confere. Ele existe como
    campo, e não como um `SELECT` refeito na hora de validar, porque um segundo
    `SELECT` poderia trazer mensagens que chegaram no meio — e aí o modelo
    "poderia" citar algo que ele não viu.
    """

    topic_id: str
    topico_nome: str
    mensagens: list[Mensagem] = field(default_factory=list)
    # As afirmações vigentes mostradas, por apelido (`E1`, `E2`, …).
    estado: dict[str, dict] = field(default_factory=dict)

    @property
    def seqs(self) -> set[int]:
        return {m.seq for m in self.mensagens}

    @property
    def por_seq(self) -> dict[int, Mensagem]:
        return {m.seq: m for m in self.mensagens}

    @property
    def vazio(self) -> bool:
        return not self.mensagens

    @property
    def de_seq(self) -> Optional[int]:
        return min(self.seqs) if self.mensagens else None

    @property
    def ate_seq(self) -> Optional[int]:
        return max(self.seqs) if self.mensagens else None

    @property
    def caracteres(self) -> int:
        return sum(len(m.content) for m in self.mensagens)


@dataclass
class ClaimProposta:
    """Uma afirmação **proposta** pelo modelo. Ainda não é verdade nenhuma."""

    subject: str
    statement: str
    kind: str
    source_seq: int
    evidence_seqs: list[int] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)  # apelidos (`E1`)
    supersede_reason: str = ""
    # O ÚNICO julgamento de confiança que se pede ao modelo, e ele só REBAIXA.
    # Ver `prompts.py`: a pergunta não é "veio de áudio?" nem "tem erro de
    # transcrição na mensagem?", é "o trecho corrompido é justamente o que
    # SUSTENTA esta afirmação?".
    legibility_doubt: bool = False
    legibility_reason: str = ""


@dataclass
class Encerramento:
    """"Isto fechou" ou "isto foi abandonado" — sem uma afirmação nova no lugar.

    É diferente de superação, e a diferença importa: superar é trocar uma
    decisão por outra; encerrar é dizer que a pergunta deixou de estar aberta.
    Sem este caso, um `ABERTO` de julho ficaria aberto para sempre.
    """

    apelido: str
    action: str  # closed | abandoned
    source_seq: int
    reason: str = ""


@dataclass
class Proposta:
    """A resposta inteira do modelo, já parseada e ainda não validada."""

    claims: list[ClaimProposta] = field(default_factory=list)
    closures: list[Encerramento] = field(default_factory=list)
    nothing_durable: bool = False


@dataclass
class Recusa:
    """Uma proposta que uma trava barrou. Existe como dado, e não como uma linha
    de log, porque **recusa silenciosa é o mesmo defeito de origem inventada,
    visto do outro lado**: se o modelo estiver alucinando, isto tem que
    aparecer no relatório, não só no `journalctl`."""

    trava: str
    motivo: str
    trecho: str = ""


@dataclass
class ResultadoDaRodada:
    run_id: Optional[str] = None
    criadas: int = 0
    superadas: int = 0
    encerradas: int = 0
    recusas: list[Recusa] = field(default_factory=list)
    mensagens_vistas: int = 0
    cursor_avancou_para: Optional[int] = None
    erro: Optional[str] = None
    # O modelo passou do teto e o lote AINDA PODE ser dividido. Nada foi
    # gravado, o cursor não andou, e quem chamou tem que partir o lote ao meio.
    excedeu: bool = False
    divisoes: int = 0

    @property
    def rejeitadas(self) -> int:
        return len(self.recusas)

    @property
    def ok(self) -> bool:
        return self.erro is None

    def resumo(self) -> str:
        if self.erro:
            return f"falhou: {self.erro}"
        if self.excedeu:
            return "passou do teto — nada gravado, o lote vai ser dividido"
        divid = f" · {self.divisoes} divisão(ões)" if self.divisoes else ""
        return (
            f"{self.mensagens_vistas} mensagem(ns) lidas · "
            f"{self.criadas} afirmação(ões) criada(s) · "
            f"{self.superadas} superada(s) · {self.encerradas} encerrada(s) · "
            f"{self.rejeitadas} recusada(s)" + divid
        )


def como_dict(obj: Any) -> dict:
    """`asdict` sem arrastar `datetime` para dentro de JSON."""
    from dataclasses import asdict

    def _limpar(v):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, dict):
            return {k: _limpar(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_limpar(x) for x in v]
        return v

    return _limpar(asdict(obj))
