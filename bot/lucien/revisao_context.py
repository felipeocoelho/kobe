"""O bloco da revisão da memória aberta no prompt do turno (F5).

Módulo leve de propósito: é importado pelo caminho de toda mensagem
(`telegram_handler`, `resume`), e o trabalho de verdade — ler o estado em disco
e montar o texto — só acontece quando a chave está ligada e a mensagem veio do
privado configurado. Nenhuma consulta ao banco: o bloco sai do arquivo de estado.
"""

from __future__ import annotations

from typing import Optional


def render_revisao_aberta(kobe_home, chat_id, thread_id) -> Optional[str]:
    """`[Revisão da memória aguardando tua resposta]` ou `None`. Nunca levanta."""
    try:
        from bot import lucien as cfg

        if not cfg.revisao_habilitada():
            return None
        from bot.lucien import revisao

        return revisao.render_bloco(kobe_home, chat_id, thread_id)
    except Exception:  # noqa: BLE001 — contexto nunca derruba turno
        return None


def juntar_ciclos_abertos(*blocos: Optional[str]) -> Optional[str]:
    """Junta os blocos de "ciclo aberto esperando resposta" num só slot."""
    presentes = [b for b in blocos if b]
    return "\n\n".join(presentes) if presentes else None
