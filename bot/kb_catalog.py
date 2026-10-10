"""O catálogo da base de conhecimento do tópico — o porteiro.

A pasta `user-data/topics/<slug>/knowledge/` não tinha porteiro: qualquer arquivo
que caísse lá entrava no prompt, em todo turno, e a ordem era a **ordem
alfabética do nome do arquivo**. Ninguém escolheu isso.

Este módulo é o `WHERE` dessa view. Três estados, e a distinção entre os dois
últimos é o que faz o mecanismo não virar chateação:

| estado | o que significa | o que acontece |
|---|---|---|
| **admitido** | o operador mandou entrar | entra no prompt |
| **removido** | o operador mandou sair | **não** entra, e **não** pergunta de novo |
| **batendo na porta** | ninguém decidiu ainda | não entra, mas **aparece no prompt** pedindo decisão |

*"Nunca decidi"* e *"decidi que não"* são coisas diferentes. Um arquivo removido
que voltasse a pedir admissão transformaria uma decisão do operador em pergunta
recorrente — e pergunta recorrente é como um aviso vira ruído e para de ser lido.

O CONTEÚDO NÃO MORA AQUI
-------------------------
O texto continua no arquivo `.md`, editável à mão. Este módulo governa **quem
entra**, não **o que está escrito**. É decisão declarada do operador em
04/09/2026, e a divisão é: *o arquivo guarda o texto; o banco governa quem entra
no prompt.*
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("kobe.kb_catalog")


@dataclass
class Catalogo:
    """O inventário de um tópico, do jeito que o carregador de contexto precisa.

    `admitidos` é uma LISTA e não um conjunto porque a ordem importa: é ela que
    substitui o critério alfabético quando não couber tudo.
    """

    admitidos: list[str] = field(default_factory=list)
    conhecidos: set[str] = field(default_factory=set)

    @property
    def vazio(self) -> bool:
        return not self.conhecidos

    def batendo_na_porta(self, na_pasta: list[str]) -> list[str]:
        """Arquivos que existem em disco e sobre os quais ninguém decidiu nada.

        Calculado na hora, por diferença — o catálogo não guarda estado sobre
        isto, então não há o que dessincronizar.
        """
        return [n for n in na_pasta if n not in self.conhecidos]


def carregar(db, topic_id: str) -> Catalogo:
    """O catálogo de um tópico.

    Uma consulta. O caminho do turno já abre o banco para resolver o slug do
    tópico, então isto não acrescenta ida nenhuma.

    **Não trata falha de banco.** É decisão do operador, de 04/09/2026: *"banco
    fora do ar é basicamente sinônimo de Kobe fora do ar"*. Um segundo caminho
    de carregamento seria resiliência que ele não pediu, e superfície a mais
    para manter.
    """
    linhas = db.query(
        "SELECT caminho, status FROM kb_catalog"
        " WHERE topic_id = %s"
        " ORDER BY (ordem IS NULL), ordem, admitido_em",
        (topic_id,),
    )
    cat = Catalogo()
    for l in linhas:
        cat.conhecidos.add(l["caminho"])
        if l["status"] == "admitido":
            cat.admitidos.append(l["caminho"])
    return cat


def admitir(
    db,
    topic_id: str,
    caminho: str,
    *,
    titulo: str,
    motivo: Optional[str] = None,
    ordem: Optional[int] = None,
    source_message_id: Optional[str] = None,
) -> str:
    """Põe um documento na base. Readmitir um removido reusa a mesma linha.

    Devolve `"admitido"` ou `"readmitido"` — o agente relata coisas diferentes
    nos dois casos, e confundi-los faria o operador achar que mandou entrar algo
    que já estava lá.
    """
    caminho, titulo = caminho.strip(), titulo.strip()
    if not caminho:
        raise ValueError("caminho vazio")
    if not titulo:
        raise ValueError("título vazio — é o que aparece na lista quando você perguntar")

    ja = db.one(
        "SELECT id, status FROM kb_catalog WHERE topic_id = %s AND caminho = %s",
        (topic_id, caminho),
    )
    if ja is None:
        db.execute(
            "INSERT INTO kb_catalog (topic_id, caminho, titulo, motivo, ordem,"
            " source_message_id) VALUES (%s,%s,%s,%s,%s,%s)",
            (topic_id, caminho, titulo, motivo, ordem, source_message_id),
        )
        return "admitido"

    db.execute(
        "UPDATE kb_catalog SET status = 'admitido', removido_em = NULL,"
        " titulo = %s, motivo = COALESCE(%s, motivo), ordem = %s,"
        " admitido_em = NOW(), updated_at = NOW() WHERE id = %s",
        (titulo, motivo, ordem, ja["id"]),
    )
    return "readmitido" if ja["status"] == "removido" else "admitido"


def remover(db, topic_id: str, caminho: str) -> bool:
    """Tira do prompt. **Não apaga o arquivo** — data a saída.

    `False` quando não havia o que tirar, para o agente poder dizer isso em vez
    de relatar sucesso de uma operação que não aconteceu.
    """
    linha = db.one(
        "UPDATE kb_catalog SET status = 'removido', removido_em = NOW(),"
        " updated_at = NOW()"
        " WHERE topic_id = %s AND caminho = %s AND status = 'admitido'"
        " RETURNING id",
        (topic_id, caminho),
    )
    return linha is not None


def listar(db, topic_id: str) -> list[dict]:
    """Tudo que o catálogo conhece, admitidos primeiro, na ordem de injeção."""
    return db.query(
        "SELECT caminho, titulo, motivo, status, ordem, admitido_em, removido_em"
        "  FROM kb_catalog WHERE topic_id = %s"
        " ORDER BY (status <> 'admitido'), (ordem IS NULL), ordem, admitido_em",
        (topic_id,),
    )
