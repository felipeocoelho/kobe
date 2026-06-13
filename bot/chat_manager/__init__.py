"""New Chat Manager (2026-06-01).

Redesenho do Chat Manager pra matar latência e granularidade macro.
Princípio: o turno é burro e rápido; toda inteligência cara roda atrás,
assíncrona, no daemon Keyko.

Componentes:
- `activity` — sinal de debounce (bot toca, daemon lê) + watermark de estado.
- `classifier` — detecção de borda retrospectiva (bibliotecário) + stamping.
- `source` — ClassifierSource: ofício do Keyko que dispara a classificação.
- `context` — montagem dos ponteiros residentes + camada imediata (no turno).

Tudo atrás da feature flag CHAT_MANAGER_ENABLED. Flag off = ocioso.
"""
