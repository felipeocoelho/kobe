#!/usr/bin/env python3
"""Testes do boot-resume que re-situa o agente (bug-retomada-contexto, 2026-06-04).

Cobrem o comportamento novo: na retomada após restart, o agente é INVOCADO
com o contexto imediato (não só um ping-template), sintetiza onde a conversa
estava e a síntese é enviada/persistida. Mais as salvaguardas: guarda de
atividade pós-restart e fallback pro template antigo quando o agente falha.

Sem rede: db/claude/bot são fakes; as camadas de contexto são monkeypatch.
Rodar:

    .venv/bin/python tests/test_resume.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot.resume as resume
from bot.claude_runner import ClaudeError
from bot.config import Config
from bot.resume import (
    RESUME_DIRECTIVE,
    build_resume_prompt,
    has_activity_after,
)


# ---------------------------------------------------------------- fakes


class FakeResult:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeClaude:
    def __init__(self, text: str = "", raise_exc: Exception | None = None) -> None:
        self.text = text
        self.raise_exc = raise_exc
        self.calls: list[tuple[str, dict]] = []

    async def run(self, prompt: str, **kw):
        self.calls.append((prompt, kw))
        if self.raise_exc is not None:
            raise self.raise_exc
        return FakeResult(self.text)


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kw):
        self.sent.append(kw)


class FakeApp:
    def __init__(self, db, claude, config) -> None:
        self.bot = FakeBot()
        self.bot_data = {"db": db, "claude": claude, "config": config}


# ── Fixtures DERIVADOS da fonte, não copiados à mão ────────────────────────
#
# Estes dois helpers existem por causa de um apodrecimento real: a `Config`
# falsa era um `SimpleNamespace` com 4 campos escritos à mão, e o pacote de
# contexto era um dict com 6 chaves escritas à mão. Quando a produção ganhou
# campos e chaves novos (núcleo curado, memória de trabalho, sinais de
# grounding), a produção seguiu correta e os testes quebraram — quatro deles,
# por dois motivos diferentes, com uma mensagem de erro que escondia a causa.
#
# A lição não é "atualizar os literais": é **parar de ter literais**. Os dois
# helpers abaixo derivam a forma da própria fonte, então um campo novo em
# `Config` ou uma chave nova em `_load_resume_context` chegam aqui sozinhos.


def _valor_default(f: dataclasses.Field):
    """Valor plausível pro campo, escolhido pelo TIPO declarado."""
    t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
    if "bool" in t:
        return False
    if "int" in t or "float" in t:
        return 0
    if "Path" in t:
        return Path("/tmp/kobe-test")
    if "frozenset" in t or "set" in t:
        return frozenset()
    if "Optional" in t:
        return None
    return ""


def _config(chat_manager_enabled: bool = True, **over):
    """`Config` falsa DERIVADA da dataclass real (41 campos e contando).

    Antes eram 4 campos escritos à mão; qualquer flag nova lida pelo resume
    explodia com `AttributeError`. Agora todo campo declarado em `Config`
    aparece aqui com um default do tipo certo, e o teste sobrescreve só o que
    lhe interessa. Campo novo na produção não quebra mais estes testes — e,
    melhor, um campo REMOVIDO da produção também não passa despercebido, porque
    o teste que o sobrescrever vai apontar pra um nome que não existe mais.
    """
    valores = {f.name: _valor_default(f) for f in dataclasses.fields(Config)}
    valores.update(
        telegram_bot_token="tok",
        kobe_home=Path("/tmp/kobe-test"),
        recent_messages_limit=20,
        chat_manager_enabled=chat_manager_enabled,
        # Default de produção: a camada imediata é a janela de memória vigente.
        working_memory_enabled=True,
    )
    valores.update(over)
    return SimpleNamespace(**valores)


def _snap(**over):
    base = {
        "topic_id": "top1",
        "telegram_chat_id": 123,
        "telegram_thread_id": None,
        "session_id": "sess1",
        "saved_at": "2026-06-04T10:00:00+00:00",
        "messages": [{"role": "user", "content": "oi, e aí"}],
        "_artifact_id": "art1",
    }
    base.update(over)
    return base


# ------------------------------------------------------- build_resume_prompt


def test_build_resume_prompt_carries_immediate_and_directive():
    history = [
        {"role": "user", "content": "estávamos no passo 3 da migração"},
        {"role": "assistant", "content": "feito, faltam 2"},
    ]
    prompt = build_resume_prompt(
        thread_id=None,
        immediate_history=history,
        chat_manager_section="[Chat Manager — assunto corrente]",
        topic_context="[KB do tópico]",
    )
    # Contexto imediato presente verbatim.
    assert "estávamos no passo 3 da migração" in prompt
    assert "faltam 2" in prompt
    # Ponteiros do Chat Manager preservados (não regredir).
    assert "Chat Manager — assunto corrente" in prompt
    assert "KB do tópico" in prompt
    # A diretiva de retomada entra como "mensagem nova".
    assert "RETOMADA APÓS REINÍCIO" in prompt
    assert RESUME_DIRECTIVE.splitlines()[0] in prompt
    print("ok: build_resume_prompt carrega imediato + diretiva")


# ----------------------------------------------------------- has_activity_after


def test_has_activity_after_detects_newer_message():
    saved = "2026-06-04T10:00:00+00:00"
    msgs = [{"created_at": "2026-06-04T10:05:00+00:00"}]
    assert has_activity_after(msgs, saved) is True
    print("ok: has_activity_after detecta msg mais nova que o snapshot")


def test_has_activity_after_ignores_older_or_equal():
    saved = "2026-06-04T10:00:00+00:00"
    assert has_activity_after([{"created_at": "2026-06-04T09:59:00+00:00"}], saved) is False
    assert has_activity_after([{"created_at": saved}], saved) is False
    assert has_activity_after([], saved) is False
    assert has_activity_after([{"created_at": "x"}], None) is False
    print("ok: has_activity_after ignora msgs antigas/iguais/vazias")


# ------------------------------------------------------- _load_resume_context


def _patch_context_leaves(monkey: dict):
    """Substitui os carregadores-folha; devolve dict de restauração."""
    originals = {name: getattr(resume, name) for name in monkey}
    for name, fn in monkey.items():
        setattr(resume, name, fn)
    return originals


def _restore(originals: dict):
    for name, fn in originals.items():
        setattr(resume, name, fn)


# Folhas neutras: tudo que `_load_resume_context` chama, devolvendo vazio. Serve
# pra arrancar do produtor a FORMA do pacote sem tocar em rede nem em disco.
_FOLHAS_NEUTRAS = {
    "get_immediate_messages": lambda db, tid: [],
    "get_recent_messages": lambda db, sid, limit=20: [],
    "render_chat_manager_section": lambda db, tid: None,
    "get_active_conversation_for_topic": lambda db, tid: None,
    "get_conversation_session_summaries": lambda db, cid, except_session_id=None: [],
    "get_topic_slug": lambda db, c, t: None,
    "load_topic_context": lambda home, slug: None,
    "load_curated_core": lambda home: None,
    "render_grounding_signals": lambda hist: None,
    "render_alertas_abertos": lambda home, c, t: None,
}


def _ctx(**over):
    """Pacote de contexto DERIVADO do produtor real (`_load_resume_context`).

    Antes cada teste montava esse dict à mão. Quando a produção passou a
    devolver `curated_core` e `grounding_signals`, os dicts à mão ficaram com 6
    das 8 chaves — e o consumidor, que lê as 8, quebrou. Pior: o `except` largo
    do resume engoliu o `KeyError` e caiu no ping, então o teste falhava com
    `assert 0 == 1` e a causa real ficava invisível.

    Chamando o produtor de verdade (com as folhas neutralizadas), a forma vem
    dele. Chave nova na produção aparece aqui automaticamente, com o valor certo
    pra "camada ausente" — e o teste sobrescreve só o que quer exercitar.
    """
    originals = _patch_context_leaves(dict(_FOLHAS_NEUTRAS))
    orig_missao = resume.missoes_storage.find_missao_ativa
    resume.missoes_storage.find_missao_ativa = lambda home, c, t: None
    try:
        base = resume._load_resume_context(object(), _config(), _snap())
    finally:
        _restore(originals)
        resume.missoes_storage.find_missao_ativa = orig_missao
    desconhecidas = set(over) - set(base)
    assert not desconhecidas, (
        f"o teste quer sobrescrever chave(s) que o produtor não devolve: "
        f"{sorted(desconhecidas)} — o pacote de contexto mudou de forma?"
    )
    base.update(over)
    return base


def test_load_resume_context_cm_on_uses_immediate_and_pointers():
    originals = _patch_context_leaves(
        {
            "get_immediate_messages": lambda db, tid: [{"role": "user", "content": "imediato"}],
            "render_chat_manager_section": lambda db, tid: "[CM]",
            "get_active_conversation_for_topic": lambda db, tid: {"id": "c1"},
            "get_conversation_session_summaries": lambda db, cid, except_session_id=None: [{"summary": "s1"}],
            "get_topic_slug": lambda db, c, t: "dev-kobe",
            "load_topic_context": lambda home, slug: "KB",
            "render_alertas_abertos": lambda home, c, t: None,
        }
    )
    # missao via submódulo
    orig_missao = resume.missoes_storage.find_missao_ativa
    resume.missoes_storage.find_missao_ativa = lambda home, c, t: None
    try:
        ctx = resume._load_resume_context(object(), _config(True), _snap())
    finally:
        _restore(originals)
        resume.missoes_storage.find_missao_ativa = orig_missao

    assert ctx["immediate"] == [{"role": "user", "content": "imediato"}]
    assert ctx["chat_manager_section"] == "[CM]"
    assert ctx["conversation_summaries"] == [{"summary": "s1"}]
    assert ctx["topic_context"] == "KB"
    print("ok: _load_resume_context (CM on) usa imediato + ponteiros")


def test_load_resume_context_cm_off_uses_session_history():
    originals = _patch_context_leaves(
        {
            "get_recent_messages": lambda db, sid, limit=20: [{"role": "user", "content": "legado"}],
            "get_topic_slug": lambda db, c, t: None,  # sem KB
            "render_alertas_abertos": lambda home, c, t: None,
        }
    )
    orig_missao = resume.missoes_storage.find_missao_ativa
    resume.missoes_storage.find_missao_ativa = lambda home, c, t: None
    try:
        # O ramo legado (histórico da sessão arquivada) é governado pela flag de
        # MEMÓRIA, não pela de conversas — as duas foram desacopladas na Frente 0
        # e o nome deste teste ficou pra trás. Por isso as duas vêm explícitas:
        # `chat_manager_enabled=False` cobre o que o nome promete (sem ponteiros
        # de conversa) e `working_memory_enabled=False` é o que de fato escolhe
        # o histórico da sessão em vez da janela imediata.
        ctx = resume._load_resume_context(
            object(), _config(False, working_memory_enabled=False), _snap()
        )
    finally:
        _restore(originals)
        resume.missoes_storage.find_missao_ativa = orig_missao

    assert ctx["immediate"] == [{"role": "user", "content": "legado"}]
    assert ctx["chat_manager_section"] is None
    print("ok: _load_resume_context (CM off) usa histórico da sessão")


def test_produtor_e_consumidor_do_contexto_nao_se_desencontram():
    """A TRAVA que impede este apodrecimento de voltar.

    Foi exatamente isto que quebrou 4 testes por dois meses sem ninguém ver: o
    produtor (`_load_resume_context`) ganhou chaves que o consumidor
    (`resume_one_snapshot`) passou a ler, e nada amarrava os dois. Aqui a
    amarração é explícita — e do lado que importa, porque uma chave lida e não
    produzida é `KeyError` em PRODUÇÃO, não só em teste.

    Lê o fonte com `ast` em vez de rodar o consumidor: assim a checagem não
    depende de qual ramo a execução tomaria.
    """
    import ast

    fonte = Path(resume.__file__).read_text(encoding="utf-8")
    arvore = ast.parse(fonte)

    lidas = {
        no.slice.value
        for no in ast.walk(arvore)
        if isinstance(no, ast.Subscript)
        and isinstance(no.value, ast.Name)
        and no.value.id == "ctx"
        and isinstance(no.slice, ast.Constant)
        and isinstance(no.slice.value, str)
    }
    produzidas = set(_ctx())

    assert lidas, "não achei nenhum ctx['...'] — o consumidor mudou de forma?"
    faltando = lidas - produzidas
    assert not faltando, (
        f"o consumidor lê chave(s) que o produtor NÃO devolve: {sorted(faltando)}. "
        "Isso é KeyError em produção, não só teste quebrado."
    )
    print(f"ok: produtor e consumidor casam ({len(lidas)} chaves lidas)")


# ------------------------------------------------------- resume_one_snapshot


def _run_resume(app, snap, *, ctx, **patches):
    """Executa resume_one_snapshot com _load_resume_context e persistência
    substituídos. `patches` permite trocar drop/ensure/insert por gravadores.
    """
    originals = {
        "_load_resume_context": resume._load_resume_context,
        "ensure_active_session": resume.ensure_active_session,
        "insert_message": resume.insert_message,
        "drop_snapshot": resume.drop_snapshot,
    }
    resume._load_resume_context = lambda db, config, s: ctx
    resume.ensure_active_session = patches.get(
        "ensure_active_session", lambda db, tid: "sess1"
    )
    resume.insert_message = patches.get("insert_message", lambda db, **kw: None)
    resume.drop_snapshot = patches.get("drop_snapshot", lambda db, aid: None)
    try:
        asyncio.run(resume.resume_one_snapshot(app, snap))
    finally:
        for name, fn in originals.items():
            setattr(resume, name, fn)


def test_resume_invokes_agent_and_sends_synthesis():
    claude = FakeClaude(text="Voltei — estávamos no passo 3 da migração, sigo daí.")
    app = FakeApp(db=object(), claude=claude, config=_config(True))
    ctx = _ctx(immediate=[
        {
            "role": "user",
            "content": "estávamos no passo 3 da migração",
            "created_at": "2026-06-04T09:59:00+00:00",
        }
    ])
    inserted: list[dict] = []
    dropped: list[str] = []
    _run_resume(
        app,
        _snap(),
        ctx=ctx,
        insert_message=lambda db, **kw: inserted.append(kw),
        drop_snapshot=lambda db, aid: dropped.append(aid),
    )

    # Agente foi invocado com o contexto imediato na frente.
    assert len(claude.calls) == 1
    prompt = claude.calls[0][0]
    assert "estávamos no passo 3 da migração" in prompt
    assert "RETOMADA APÓS REINÍCIO" in prompt
    # A síntese foi enviada ao operador.
    assert len(app.bot.sent) == 1
    assert "Voltei" in app.bot.sent[0]["text"]
    # Síntese persistida como assistant, snapshot consumido.
    assert inserted and inserted[0]["role"] == "assistant"
    assert dropped == ["art1"]
    print("ok: resume invoca agente, envia síntese, persiste e consome snapshot")


def test_resume_falls_back_to_ping_when_agent_fails():
    claude = FakeClaude(raise_exc=ClaudeError("boom"))
    app = FakeApp(db=object(), claude=claude, config=_config(True))
    ctx = _ctx(immediate=[
        {"role": "user", "content": "x", "created_at": "2026-06-04T09:59:00+00:00"}
    ])
    dropped: list[str] = []
    _run_resume(app, _snap(), ctx=ctx, drop_snapshot=lambda db, aid: dropped.append(aid))

    # Agente tentou, falhou → caiu no template antigo.
    assert len(claude.calls) == 1
    assert len(app.bot.sent) == 1
    assert "Voltei" in app.bot.sent[0]["text"]  # render_resume_message
    assert "oi, e aí" in app.bot.sent[0]["text"]  # última fala do operador
    assert dropped == ["art1"]
    print("ok: resume cai no ping-template quando o agente falha")


def test_resume_skips_when_activity_after_snapshot():
    claude = FakeClaude(text="não deveria rodar")
    app = FakeApp(db=object(), claude=claude, config=_config(True))
    # Mensagem MAIS NOVA que saved_at (10:00) → operador já voltou a falar.
    # (Este teste passava com o dict capenga de 6 chaves por SORTE: a guarda de
    # atividade retorna antes da linha que lia as chaves faltantes. Bomba armada
    # — quebraria no dia em que a guarda mudasse. Derivado, deixa de ser sorte.)
    ctx = _ctx(immediate=[
        {"role": "user", "content": "já voltei",
         "created_at": "2026-06-04T10:05:00+00:00"}
    ])
    dropped: list[str] = []
    _run_resume(app, _snap(), ctx=ctx, drop_snapshot=lambda db, aid: dropped.append(aid))

    # Não invoca agente, não pinga — mas consome o snapshot.
    assert claude.calls == []
    assert app.bot.sent == []
    assert dropped == ["art1"]
    print("ok: resume pula quando há atividade pós-restart (sem ping duplo)")


if __name__ == "__main__":
    test_build_resume_prompt_carries_immediate_and_directive()
    test_has_activity_after_detects_newer_message()
    test_has_activity_after_ignores_older_or_equal()
    test_load_resume_context_cm_on_uses_immediate_and_pointers()
    test_load_resume_context_cm_off_uses_session_history()
    test_resume_invokes_agent_and_sends_synthesis()
    test_resume_falls_back_to_ping_when_agent_fails()
    test_resume_skips_when_activity_after_snapshot()
    print("\nTODOS OS TESTES PASSARAM")
