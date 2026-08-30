"""O catálogo de desenvolvimento — quem escreve nas quatro tabelas do §6.2.

Highlander v3, F1. Toda sala de trabalho (Coder ou Mission Control) passa a
nascer com uma linha em `work_sessions`, declarando **sistema** e **subsistema**.

──────────────────────────────────────────────────────────────────────────
POR QUE O CONHECIMENTO DE BANCO MORA AQUI, NO CORE
──────────────────────────────────────────────────────────────────────────

Os dois dispatchers que precisam registrar são de naturezas diferentes: o do
Mission Control é código do core (`bot/mission_control/sala_dispatch.py`) e o do
Coder é de um **plugin, em repositório separado**
(`plugins/public/coder/scripts/run_remote.py`), que hoje não importa `bot.*` e
não fala com o Postgres — importa só os módulos locais dele.

Isso não é acidente, é um limite arquitetural que vale manter: um plugin com
driver de banco vira acoplamento que ninguém desfaz depois. Então o
conhecimento de banco fica aqui, e o plugin chama o CLI fino
`$KOBE_HOME/bot/bin/kobe-work-session`, que é um processo e um contrato de
saída — não um `import`.

──────────────────────────────────────────────────────────────────────────
AS TRÊS CAMADAS DA DECLARAÇÃO (§6.3 do briefing)
──────────────────────────────────────────────────────────────────────────

1. **O banco garante.** `work_sessions.system_id` é `NOT NULL` com chave
   estrangeira, e há uma FK composta que impede o par impossível (subsistema de
   um sistema declarado com outro). Não depende de ninguém lembrar.
2. **O dispatch exige.** `--system` e `--subsystem` são obrigatórios; sem eles o
   comando falha e **nenhuma sala é aberta**. E "nenhum subsistema" se declara
   explicitamente com `none` — **omissão é esquecimento, `none` é decisão**.
3. **Quem preenche é o agente**, a partir do pedido, do tópico e do repositório
   — e pergunta uma linha quando genuinamente ambíguo.

──────────────────────────────────────────────────────────────────────────
A DISTINÇÃO QUE ESTE MÓDULO SE OBRIGA A FAZER
──────────────────────────────────────────────────────────────────────────

Uma recusa **de regra** ("você não declarou o sistema", "esse sistema não existe
no catálogo") é uma coisa; uma falha **de instrumento** ("o Postgres não
respondeu") é outra. Elas exigem reações opostas do agente que lê a saída: a
primeira se resolve declarando direito, a segunda se resolve consertando o
serviço — e tratar a segunda como a primeira faria o agente inventar um sistema
pra "satisfazer" um erro que não era sobre sistema nenhum.

É a mesma lição do conserto do `kobe-reflect` de 29/08/2026, onde um timeout do
serviço era impresso com a mesma frase de "não há registro". Aqui a distinção é
estrutural: `CatalogRefusal` (regra, exit 2) e `CatalogUnavailable`
(infraestrutura, exit 3) são tipos diferentes, e o CLI os imprime diferente.

**Nos dois casos a sala NÃO nasce** — a diferença é o que se faz a respeito.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

# Raiz do projeto: bot/work_catalog.py → bot → raiz.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Os tipos de artefato que o §6.2 fixa. `test-report` é tipo próprio de
# propósito: é o resultado do plano de testes que toda sessão Coder executa.
ARTIFACT_KINDS = ("code", "doc", "diagram", "commit", "migration", "test-report")
SESSION_KINDS = ("coder", "mission")
SESSION_STATUSES = ("running", "idle", "closed", "dead")

# A palavra que declara ausência de subsistema. Existe pra que a ausência seja
# uma DECISÃO escrita, e não o silêncio de quem esqueceu.
SUBSYSTEM_NONE = "none"


# --- exceções -----------------------------------------------------------

class CatalogError(Exception):
    """Base. Nunca levantada diretamente."""


class CatalogRefusal(CatalogError):
    """Recusa DE REGRA: a declaração está ausente, vazia ou não bate com o
    catálogo. Culpa de quem chamou; conserta-se declarando direito.

    `hint` carrega o que fazer a respeito — é lido por um agente, não por um
    humano, então dizer "o que fazer" vale mais que dizer "o que houve".
    """

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


class CatalogUnavailable(CatalogError):
    """Falha DE INSTRUMENTO: o banco não respondeu, o schema não está aplicado.
    Não é culpa da declaração, e nenhuma redeclaração conserta."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


# --- configuração -------------------------------------------------------

def _env(name: str) -> str:
    """Lê do ambiente e, se faltar, do `.env` do projeto (parser mínimo).

    Os dispatchers rodam ora como subprocess do bot (env injetada), ora na mão
    no terminal. Procura em `$KOBE_HOME/.env` antes da raiz derivada do arquivo
    porque este módulo pode estar sendo lido de uma worktree — que não tem
    `.env` (ele é gitignorado) — enquanto o `KOBE_HOME` aponta pra instalação.
    """
    value = os.environ.get(name)
    if value:
        return value.strip()

    candidates = []
    kobe_home = os.environ.get("KOBE_HOME", "").strip()
    if kobe_home:
        candidates.append(Path(kobe_home).expanduser() / ".env")
    candidates.append(_PROJECT_ROOT / ".env")

    for env_path in candidates:
        if not env_path.is_file():
            continue
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() == name:
                    return val.strip().strip("'").strip('"')
        except OSError:
            continue
    return ""


def catalog_enabled() -> bool:
    """A chave-mestra da F1, lado catálogo.

    **Nasce desligada, de propósito.** É o rollback que o briefing nomeia ("os
    dispatchers voltam a aceitar abertura sem declaração") e é o que impede o
    pior cenário de implantação: o código novo dos dispatchers chegar num
    ambiente onde a migration `006` ainda não foi aplicada e, aí sim, derrubar
    TODA abertura de sala. Desligada, o comportamento é bit a bit o de antes.
    """
    return _env("WORK_CATALOG_ENABLED").lower() in ("1", "true", "on", "yes")


def _database_url() -> str:
    url = _env("DATABASE_URL")
    if not url:
        raise CatalogUnavailable(
            "DATABASE_URL ausente — não há como registrar a sessão no catálogo.",
            detail="procurado em os.environ, $KOBE_HOME/.env e na raiz do projeto",
        )
    return url


# Teto de espera pra abrir a conexão, em segundos. Sobrescrevível por
# `WORK_CATALOG_CONNECT_TIMEOUT`.
#
# O NÚMERO É PEQUENO DE PROPÓSITO. Quem espera por esta conexão é um operador
# olhando o Telegram depois de pedir uma sala. Se o banco está fora, a resposta
# certa é "falhou, e por isto" em segundos — não um silêncio indefinido.
CONNECT_TIMEOUT_DEFAULT = 5.0


def _connect_timeout() -> int:
    raw = _env("WORK_CATALOG_CONNECT_TIMEOUT")
    try:
        valor = float(raw) if raw else CONNECT_TIMEOUT_DEFAULT
    except ValueError:
        valor = CONNECT_TIMEOUT_DEFAULT
    # libpq só aceita inteiro em `connect_timeout`, e 0 significa "sem limite" —
    # que é exatamente o que não se quer aqui.
    return max(1, int(round(valor)))


class _CatalogDB:
    """Ponte curta: mesmos quatro verbos do `KobeDB`, sem pool e sem repetição.

    **Por que não reusar o `KobeDB` direto**, sendo que ele é a ponte oficial:
    ele existe pra um processo LONGO (o bot), e por isso traz pool, reciclagem
    de conexão ociosa e repetição com espera progressiva. Aqui o processo é um
    CLI que faz duas ou três consultas e morre — e essas três coisas, boas lá,
    são exatamente o que trava aqui.

    Medido ao escrever o teste de "banco fora": apontando pra uma porta fechada,
    o helper com `KobeDB` **pendurou até o teste estourar em 60 s**, em vez de
    devolver a falha. Ou seja: com o Postgres fora, o dispatcher ficaria preso
    esperando pra sempre no meio de abrir uma sala — o pior desfecho possível,
    porque o operador não recebe nem a sala nem o erro. Uma conexão direta com
    `connect_timeout` transforma isso num `exit 3` em cinco segundos.

    O que se reusa do `bot.db` é o que importa reusar: `_normalize_row`, o
    contrato de fronteira que transforma `UUID` em `str` e `datetime` em texto
    ISO. Sem ele, os tipos que saem daqui seriam diferentes dos que saem do
    resto do Kobe — e a divergência apareceria longe da causa.
    """

    def __init__(self, conn, normalize_row) -> None:
        self._conn = conn
        self._normalize_row = normalize_row

    def _run(self, sql: str, params) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql, tuple(params) if params else None)
            if cur.description is None:
                return []
            return [self._normalize_row(row) for row in cur.fetchall()]

    def query(self, sql: str, params=()) -> list[dict]:
        return self._run(sql, params)

    def one(self, sql: str, params=()):
        rows = self._run(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params=()):
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    def execute(self, sql: str, params=()) -> list[dict]:
        return self._run(sql, params)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 — fechar não pode mascarar a saída
            pass


def connect():
    """A ponte do módulo. Falha RÁPIDO e com motivo, ou não falha.

    Envelopa importação e conexão em `CatalogUnavailable` — do ponto de vista de
    quem chamou, "psycopg não está instalado" e "o banco não respondeu" dizem a
    mesma coisa (*o instrumento não está disponível*) e pedem a mesma reação.
    """
    try:
        import psycopg
        from psycopg.rows import dict_row

        from bot.db import _normalize_row
    except Exception as exc:  # noqa: BLE001 — dependência ausente é indisponibilidade
        raise CatalogUnavailable(
            "não consegui carregar a ponte do banco (`psycopg` / `bot.db`).",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    url = _database_url()
    timeout = _connect_timeout()
    try:
        conn = psycopg.connect(
            url,
            row_factory=dict_row,
            autocommit=True,
            connect_timeout=timeout,
            # Mesmo motivo do `bot/db.py`: o texto de um `timestamptz` sai no
            # fuso da sessão, e a ponte não pode depender do que o cluster tenha
            # configurado.
            options="-c TimeZone=UTC",
        )
    except Exception as exc:  # noqa: BLE001
        raise CatalogUnavailable(
            f"não consegui abrir conexão com o banco em até {timeout}s.",
            detail=f"{type(exc).__name__}: {str(exc).strip().splitlines()[0][:200]}",
        ) from exc

    return _CatalogDB(conn, _normalize_row)


# --- normalização -------------------------------------------------------

def slugify(name: str) -> str:
    """Mesma regra de `bot.topic_manager.slugify` — kebab-case sem acento."""
    nfkd = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    s = ascii_only.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# --- resolução ----------------------------------------------------------

def list_systems(db) -> list[dict]:
    return db.query("SELECT id, name, slug, notes FROM work_systems ORDER BY name")


def list_subsystems(db, system_id: Optional[str] = None) -> list[dict]:
    sql = (
        "SELECT sub.id, sub.name, sub.slug, sub.notes, sys.slug AS system_slug, "
        "       sys.name AS system_name "
        "  FROM work_subsystems sub "
        "  JOIN work_systems sys ON sys.id = sub.system_id "
    )
    params: list[Any] = []
    if system_id:
        sql += " WHERE sub.system_id = %s"
        params.append(system_id)
    sql += " ORDER BY sys.name, sub.name"
    return db.query(sql, params)


def _table_missing(exc: Exception) -> bool:
    txt = str(exc).lower()
    return "work_systems" in txt and ("does not exist" in txt or "não existe" in txt)


def resolve_system(db, ref: Optional[str]) -> dict:
    """O sistema declarado → a linha do catálogo. Recusa se ausente ou desconhecido.

    Aceita slug OU nome, sem diferenciar maiúsculas: quem escreve a declaração é
    um agente escrevendo prosa (`--system Kobe`), não um programa manipulando
    identificadores. Exigir o slug exato transformaria uma questão de grafia numa
    sala que não abre.

    **Sistema desconhecido é EVENTO, não erro de digitação a ser corrigido no
    silêncio.** A recusa diz o que existe e manda perguntar ao operador antes de
    registrar — é o mesmo motivo pelo qual não se deixa aplicação criar tabela
    sozinha: um erro de digitação viraria um sistema fantasma no catálogo.
    """
    ref = (ref or "").strip()
    if not ref:
        raise CatalogRefusal(
            "sistema_nao_declarado",
            "nenhum sistema declarado — a sala NÃO foi aberta.",
            hint=(
                "passe `--system <Sistema>`. Regra: código do Kobe é "
                "`--system Kobe --subsystem none`; código de plugin do Kobe é "
                "`--system Kobe --subsystem <Coder|Atrus|Apolo|Monet|Flow>`. "
                "A pasta de trabalho NÃO decide o sistema."
            ),
        )
    try:
        rows = list_systems(db)
    except Exception as exc:  # noqa: BLE001
        if _table_missing(exc):
            raise CatalogUnavailable(
                "o catálogo não existe neste banco — a migration 006 não foi aplicada.",
                detail="rode `python infra/migrate.py up`",
            ) from exc
        raise CatalogUnavailable(
            "o banco não respondeu ao consultar o catálogo.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    alvo = slugify(ref)
    for row in rows:
        if row["slug"] == alvo or slugify(row["name"]) == alvo:
            return row

    conhecidos = ", ".join(r["name"] for r in rows) or "(nenhum)"
    raise CatalogRefusal(
        "sistema_desconhecido",
        f"{ref!r} não está no catálogo de sistemas — a sala NÃO foi aberta.",
        hint=(
            f"sistemas conhecidos: {conhecidos}. Sistema novo é EVENTO: pergunte "
            f"ao operador (\"{ref} é sistema novo? registro no catálogo?\") antes "
            f"de registrar. Não invente."
        ),
    )


def resolve_subsystem(db, system: dict, ref: Optional[str]) -> Optional[dict]:
    """O subsistema declarado → a linha, ou `None` quando declarado como `none`.

    A assimetria entre omissão e `none` é o ponto inteiro desta função, e vem
    direto do §6.3: **omissão é esquecimento, `none` é decisão**. Um sistema sem
    subsistema é legítimo e frequente (código do Kobe em si) — mas tem que ser
    dito, porque o catálogo precisa distinguir "não tem" de "ninguém preencheu".
    """
    ref = (ref or "").strip()
    if not ref:
        raise CatalogRefusal(
            "subsistema_nao_declarado",
            "nenhum subsistema declarado — a sala NÃO foi aberta.",
            hint=(
                "`--subsystem` é obrigatório. Se o trabalho é no sistema em si e "
                "não num subsistema, declare `--subsystem none` — explicitamente. "
                "Omissão é esquecimento; `none` é decisão."
            ),
        )
    if slugify(ref) == SUBSYSTEM_NONE:
        return None

    rows = list_subsystems(db, system["id"])
    alvo = slugify(ref)
    for row in rows:
        if row["slug"] == alvo or slugify(row["name"]) == alvo:
            return row

    conhecidos = ", ".join(r["name"] for r in rows) or "(nenhum)"
    raise CatalogRefusal(
        "subsistema_desconhecido",
        f"{ref!r} não é subsistema de {system['name']!r} — a sala NÃO foi aberta.",
        hint=(
            f"subsistemas de {system['name']}: {conhecidos}. Se o trabalho é no "
            f"sistema em si, use `--subsystem none`. Subsistema novo é evento: "
            f"pergunte ao operador antes de registrar."
        ),
    )


def resolve_topic_id(
    db, chat_id: Optional[int], thread_id: Optional[int]
) -> Optional[str]:
    """`topics.id` a partir do PAR (chat, thread). `None` se não der pra resolver.

    **Pelo par, e não só pelo thread.** A restrição real da tabela é
    `UNIQUE (telegram_chat_id, telegram_thread_id)` — verificado no banco, e
    diferente do que o `schema.sql` sugere. Há `telegram_thread_id = 2` em dois
    chats distintos (AMBIENTE DEV e Olimpo). Resolver só pelo thread devolveria
    o tópico errado, e a linha do catálogo apontaria pra conversa de outra
    pessoa.

    Falta de tópico **não impede** a sessão de nascer (a coluna é nula-permitida):
    um dispatch de linha de comando ou de teste não tem tópico, e o que não pode
    faltar é o sistema, não o tópico.
    """
    if chat_id is None:
        return None
    # `general`/`private` (raiz do chat, sem thread) é gravado como 0 na tabela,
    # e chega aqui como None — os dois querem dizer a mesma coisa.
    thread = 0 if thread_id is None else int(thread_id)
    try:
        row = db.one(
            "SELECT id FROM topics "
            " WHERE telegram_chat_id = %s AND telegram_thread_id = %s",
            (int(chat_id), thread),
        )
    except Exception:  # noqa: BLE001 — tópico é enriquecimento, nunca porteiro
        return None
    return row["id"] if row else None


# --- escrita ------------------------------------------------------------

def register_session(
    db,
    *,
    session_id: str,
    kind: str,
    system: Optional[str],
    subsystem: Optional[str],
    title: Optional[str] = None,
    slug: Optional[str] = None,
    briefing: Optional[str] = None,
    motivation: Optional[str] = None,
    cwd: Optional[str] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    transcript_path: Optional[str] = None,
) -> dict:
    """Grava a linha da sala. **Chamada ANTES de a sala nascer.**

    A ordem importa e é o desenho: registra-se primeiro, abre-se depois. Se o
    registro falhar — por regra ou por instrumento —, o dispatcher aborta e
    nenhuma sala é aberta. O contrário (abrir e registrar depois) produziria
    exatamente o que a F1 existe pra evitar: sala trabalhando sem linha nenhuma.

    Idempotente por `session_id`: reexecução devolve a linha existente sem
    duplicar. O dispatcher pode ser reexecutado (`--force`, retomada de um start
    interrompido) e isso não pode virar erro nem linha dupla.
    """
    if kind not in SESSION_KINDS:
        raise CatalogRefusal(
            "kind_invalido",
            f"kind {kind!r} inválido.",
            hint=f"use um de: {', '.join(SESSION_KINDS)}",
        )

    sistema = resolve_system(db, system)
    subsistema = resolve_subsystem(db, sistema, subsystem)
    topic_id = resolve_topic_id(db, chat_id, thread_id)

    try:
        rows = db.execute(
            "INSERT INTO work_sessions "
            "  (id, system_id, subsystem_id, kind, topic_id, title, slug, "
            "   briefing, motivation, cwd, transcript_path, last_activity_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW()) "
            "ON CONFLICT (id) DO NOTHING "
            "RETURNING id",
            (
                session_id, sistema["id"],
                subsistema["id"] if subsistema else None,
                kind, topic_id, title, slug, briefing, motivation, cwd,
                transcript_path,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise CatalogUnavailable(
            "o banco recusou a escrita da linha da sessão.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return {
        "session_id": session_id,
        "created": bool(rows),          # False = já existia (reexecução)
        "system": sistema["name"],
        "system_slug": sistema["slug"],
        "subsystem": subsistema["name"] if subsistema else None,
        "subsystem_slug": subsistema["slug"] if subsistema else None,
        "topic_id": topic_id,
        "kind": kind,
        "cwd": cwd,
    }


def touch_session(
    db,
    *,
    session_id: str,
    status: Optional[str] = None,
    transcript_path: Optional[str] = None,
    transcript_bytes_copied: Optional[int] = None,
    dossier_path: Optional[str] = None,
    last_activity: bool = True,
) -> bool:
    """Atualiza o que muda ao longo da vida da sala. `False` se não há linha.

    Ausência de linha **não é erro**: as ~24 salas que já existiam antes do
    catálogo não têm registro, e o coletor precisa poder colher o transcript
    delas do mesmo jeito. O catálogo é enriquecimento; o que é perecível se
    salva primeiro.
    """
    campos, params = [], []
    if status is not None:
        if status not in SESSION_STATUSES:
            raise CatalogRefusal(
                "status_invalido",
                f"status {status!r} inválido.",
                hint=f"use um de: {', '.join(SESSION_STATUSES)}",
            )
        campos.append("status = %s"); params.append(status)
    if transcript_path is not None:
        campos.append("transcript_path = %s"); params.append(transcript_path)
    if transcript_bytes_copied is not None:
        campos.append("transcript_bytes_copied = %s")
        params.append(int(transcript_bytes_copied))
    if dossier_path is not None:
        campos.append("dossier_path = %s"); params.append(dossier_path)
    if last_activity:
        campos.append("last_activity_at = NOW()")
    if not campos:
        return False
    params.append(session_id)
    rows = db.execute(
        f"UPDATE work_sessions SET {', '.join(campos)} WHERE id = %s RETURNING id",
        params,
    )
    return bool(rows)


def close_session(
    db, *, session_id: str, status: str = "closed",
    outcome_summary: Optional[str] = None,
) -> bool:
    """Rótulo de ESTADO, não evento de sistema (E3 do briefing).

    Fechar uma sala não dispara nada: não dispara coleta, não dispara destilação,
    não dispara catalogação. Só muda o rótulo. Toda peça da F1 é dirigida por
    relógio ou por acúmulo, justamente pra que uma sala que morra de forma feia
    (cota, crash, OOM) não leve junto o registro do que ela fez.
    """
    if status not in SESSION_STATUSES:
        raise CatalogRefusal(
            "status_invalido", f"status {status!r} inválido.",
            hint=f"use um de: {', '.join(SESSION_STATUSES)}",
        )
    rows = db.execute(
        "UPDATE work_sessions SET status = %s, "
        "       outcome_summary = COALESCE(%s, outcome_summary), "
        "       last_activity_at = NOW() "
        " WHERE id = %s RETURNING id",
        (status, outcome_summary, session_id),
    )
    return bool(rows)


def add_artifact(
    db, *, session_id: str, path: str, kind: str,
    description: Optional[str] = None,
) -> dict:
    """Registra o que a sessão produziu. Idempotente por (sessão, caminho, tipo).

    A idempotência é o que permite chamar isto de dentro de um passo que pode
    ser repetido (uma bateria rodada duas vezes, um commit refeito) sem encher a
    tabela de linhas iguais.
    """
    if kind not in ARTIFACT_KINDS:
        raise CatalogRefusal(
            "kind_invalido", f"tipo de artefato {kind!r} inválido.",
            hint=f"use um de: {', '.join(ARTIFACT_KINDS)}",
        )
    existente = db.one(
        "SELECT id FROM work_session_artifacts "
        " WHERE session_id = %s AND path = %s AND kind = %s",
        (session_id, path, kind),
    )
    if existente:
        return {"id": existente["id"], "created": False}
    try:
        rows = db.execute(
            "INSERT INTO work_session_artifacts (session_id, path, kind, description) "
            "VALUES (%s,%s,%s,%s) RETURNING id",
            (session_id, path, kind, description),
        )
    except Exception as exc:  # noqa: BLE001
        raise CatalogUnavailable(
            "o banco recusou a escrita do artefato.",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    return {"id": rows[0]["id"] if rows else None, "created": True}


def get_session(db, session_id: str) -> Optional[dict]:
    """A linha da sala, com sistema e subsistema já resolvidos por nome."""
    return db.one(
        "SELECT s.*, sys.name AS system_name, sys.slug AS system_slug, "
        "       sub.name AS subsystem_name, sub.slug AS subsystem_slug "
        "  FROM work_sessions s "
        "  JOIN work_systems sys ON sys.id = s.system_id "
        "  LEFT JOIN work_subsystems sub ON sub.id = s.subsystem_id "
        " WHERE s.id = %s",
        (session_id,),
    )


def list_artifacts(db, session_id: str) -> list[dict]:
    return db.query(
        "SELECT path, kind, description, created_at "
        "  FROM work_session_artifacts WHERE session_id = %s "
        " ORDER BY created_at, path",
        (session_id,),
    )


# --- a porta que os dispatchers usam ------------------------------------

def register_from_dispatch(
    *,
    session_id: str,
    kind: str,
    system: Optional[str],
    subsystem: Optional[str],
    **campos: Any,
) -> dict:
    """Abre a conexão, registra e fecha — a chamada de uma linha do dispatcher.

    **É chamada ANTES de a sala nascer, e o dispatcher aborta se ela falhar.**
    A ordem é o desenho inteiro: registra-se primeiro, abre-se depois. O
    contrário (abrir e registrar depois) produziria exatamente o que a F1 existe
    pra evitar — uma sala trabalhando sem linha nenhuma, que é o estado de todas
    as salas de hoje.

    Devolve `{"skipped": True}` quando a chave está desligada. **Isso não é
    falha**: é o estado de rollback, em que os dispatchers voltam a abrir sala
    sem declaração, como antes da F1. Quem chama segue em frente.

    Levanta `CatalogRefusal` (declaração errada) ou `CatalogUnavailable` (banco
    fora) — dois tipos porque pedem reações opostas de quem lê.
    """
    if not catalog_enabled():
        return {"skipped": True, "reason": "WORK_CATALOG_ENABLED off"}

    db = connect()
    try:
        return register_session(
            db, session_id=session_id, kind=kind,
            system=system, subsystem=subsystem, **campos,
        )
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001 — fechar não pode mascarar a saída
            pass


def refusal_payload(exc: CatalogError) -> dict:
    """Traduz a exceção no dicionário-erro que os dispatchers já devolvem.

    A frase **"NENHUMA sala foi aberta"** aparece nos dois casos, e de propósito:
    o desfecho é o mesmo, o que muda é o que fazer a respeito. Sem dizê-lo, quem
    lê pode achar que a sala subiu e só o registro falhou — e ficaria procurando
    uma sala que não existe.
    """
    if isinstance(exc, CatalogRefusal):
        return {
            "error": exc.code,
            "message": f"{exc.message} {exc.hint}".strip(),
            "refusal": True,
            "note": "NENHUMA sala foi aberta.",
        }
    return {
        "error": "catalogo_indisponivel",
        "message": f"{exc.message} {getattr(exc, 'detail', '')}".strip(),
        "unavailable": True,
        "note": (
            "FALHA DE INSTRUMENTO — isto NÃO é 'sistema não declarado'. "
            "Redeclarar não conserta. NENHUMA sala foi aberta."
        ),
    }
