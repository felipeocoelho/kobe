"""Keyko — daemon genérico de despertar do Claude por gatilho.

Nome em homenagem a um pastor alemão do operador. Late quando algo
acontece nas sources registradas (Missões na Fase 1; Alertas no futuro).

Use `python -m bot.keyko` pra rodar (entrypoint em `__main__.py`).
"""

from bot.keyko.models import Despertar, Source

__all__ = ["Despertar", "Source"]
