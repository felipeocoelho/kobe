"""Executor de subtarefa — wrapper invocado via kobe-dispatch.

Modo de uso (chamado pelo orquestrador, via subprocess através do
helper `bot/bin/kobe-dispatch`):

    kobe-dispatch -- python3 -m bot.mission_control.executor \\
        --missao <id> --tarefa T1 --prompt-file <path>

Por que arquivo em vez de --prompt na linha de comando: prompts longos
estouram limite de argv (poucos MB em Linux moderno, mas filesystem
arg-list expansion fica feio em logs). O orquestrador escreve o prompt
em `<missao_dir>/prompts/<tarefa>.txt` antes de disparar.

O que o executor faz:
1. Append `tarefa-iniciada` em eventos.jsonl (com PID próprio).
2. Atualiza estado.json: tarefa.status=rodando, tarefa.pid, tarefa.iniciado_em.
3. Roda `claude -p` com o prompt via stdin, captura stdout+stderr.
4. Escreve output em `outputs/<tarefa>.md` se sucesso.
5. Append `tarefa-concluida` ou `tarefa-falhou`.
6. Atualiza estado.json com status final, terminado_em, output_path, erro.

O Keyko observa eventos.jsonl e atualiza painel; o append de
`tarefa-concluida` também conta como marco → Keyko acorda orquestrador,
que decide próximas tarefas.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from bot.mission_control import StatusTarefa, TipoEvento
from bot.mission_control import storage


logger = logging.getLogger("kobe.missoes.executor")

# Timeout duro por subtarefa. Generoso, mas não infinito — se o claude -p
# pendurar (rede, loop), preferimos matar e marcar tarefa-falhou do que
# segurar slot da missão pra sempre. Configurável via env se um dia for
# preciso (alguma tarefa de análise pesada legítima).
TIMEOUT_DEFAULT = 600  # 10 min


def run(
    *,
    kobe_home: Path,
    missao_id: str,
    tarefa_id: str,
    prompt: str,
    timeout_s: int = TIMEOUT_DEFAULT,
) -> int:
    """Executa a subtarefa. Retorna exit code (0=ok, !=0=falhou).

    Função separada do CLI pra facilitar teste programático.
    """
    log_path = storage.path_log_tarefa(kobe_home, missao_id, tarefa_id)
    output_path = storage.path_output_tarefa(kobe_home, missao_id, tarefa_id, ext="md")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pid = os.getpid()
    iniciado_em = storage.now_iso()

    # 1+2. Marca tarefa-iniciada no estado e no log de eventos.
    try:
        with storage.mutar(kobe_home, missao_id) as missao:
            t = missao.tarefa(tarefa_id)
            if t is None:
                logger.error("tarefa %s não existe na missão %s", tarefa_id, missao_id)
                return 2
            t.status = StatusTarefa.RODANDO.value
            t.pid = pid
            t.iniciado_em = iniciado_em
            t.log_path = str(log_path)
    except storage.LockTimeoutError:
        logger.error("lock timeout marcando tarefa-iniciada — abortando execução")
        return 3

    storage.append_evento(
        kobe_home, missao_id, TipoEvento.TAREFA_INICIADA,
        tarefa_id=tarefa_id,
        dados={"pid": pid, "iniciado_em": iniciado_em},
    )

    # 3. Roda claude -p. stdin = prompt; stdout = output da subtarefa;
    #    stderr vai pro log_path junto com qualquer marker do CLI.
    exit_code, stdout_text, stderr_text, duracao_s = _run_claude(prompt, timeout_s)

    # 4. Escreve stdout como output da tarefa (Markdown) e o full log.
    if exit_code == 0 and stdout_text.strip():
        try:
            output_path.write_text(stdout_text, encoding="utf-8")
        except OSError as exc:
            logger.warning("falha gravando output %s: %s", output_path, exc)
            output_path = None  # type: ignore[assignment]
    else:
        # Sem output utilizável.
        output_path = None  # type: ignore[assignment]

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            fh.write(f"# kobe missoes executor — missao={missao_id} tarefa={tarefa_id}\n")
            fh.write(f"# pid={pid} iniciado_em={iniciado_em} duracao_s={duracao_s:.1f}\n")
            fh.write(f"# exit_code={exit_code}\n\n")
            fh.write("## stdout (claude -p)\n")
            fh.write(stdout_text or "(vazio)\n")
            fh.write("\n## stderr\n")
            fh.write(stderr_text or "(vazio)\n")
    except OSError:
        logger.exception("falha gravando log %s", log_path)

    terminado_em = storage.now_iso()
    sucesso = exit_code == 0

    # 5+6. Atualiza estado final + append evento.
    try:
        with storage.mutar(kobe_home, missao_id) as missao:
            t = missao.tarefa(tarefa_id)
            if t is not None:
                t.terminado_em = terminado_em
                if sucesso:
                    t.status = StatusTarefa.CONCLUIDA.value
                    t.output_path = str(output_path) if output_path else None
                    t.erro = None
                else:
                    t.status = StatusTarefa.FALHOU.value
                    t.erro = _resumo_erro(stderr_text, exit_code)
    except storage.LockTimeoutError:
        logger.error("lock timeout marcando fim da tarefa — evento ainda vai ser appendado")

    if sucesso:
        storage.append_evento(
            kobe_home, missao_id, TipoEvento.TAREFA_CONCLUIDA,
            tarefa_id=tarefa_id,
            dados={
                "duracao_s": round(duracao_s, 2),
                "output_path": str(output_path) if output_path else None,
            },
        )
    else:
        storage.append_evento(
            kobe_home, missao_id, TipoEvento.TAREFA_FALHOU,
            tarefa_id=tarefa_id,
            dados={
                "exit_code": exit_code,
                "erro": _resumo_erro(stderr_text, exit_code),
                "log_path": str(log_path),
                "duracao_s": round(duracao_s, 2),
            },
        )

    return exit_code


def _run_claude(prompt: str, timeout_s: int) -> tuple[int, str, str, float]:
    """Invoca `claude -p` com o prompt via stdin. Retorna (exit, out, err, dur)."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            ["claude", "-p", "--permission-mode", "bypassPermissions"],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        duracao = time.monotonic() - started
        return (
            proc.returncode,
            proc.stdout.decode("utf-8", errors="replace"),
            proc.stderr.decode("utf-8", errors="replace"),
            duracao,
        )
    except subprocess.TimeoutExpired as exc:
        duracao = time.monotonic() - started
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (
            (exc.stderr or b"").decode("utf-8", errors="replace")
            + f"\n[executor] timeout após {timeout_s}s — processo morto"
        )
        return 124, stdout, stderr, duracao
    except FileNotFoundError as exc:
        return 127, "", f"[executor] claude CLI não encontrado: {exc}", 0.0


def _resumo_erro(stderr_text: str, exit_code: int) -> str:
    """1-3 linhas curtas pro painel. Stderr completo fica no log_path."""
    if not stderr_text.strip():
        return f"exit_code={exit_code}, sem stderr"
    linhas_uteis = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()]
    # Pega as últimas 3 linhas — em CLI o erro útil costuma estar no fim.
    cauda = " | ".join(linhas_uteis[-3:])
    if len(cauda) > 300:
        cauda = cauda[:297] + "…"
    return cauda


# --- CLI ----------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bot.mission_control.executor",
        description="Executa uma subtarefa de Missão. Tipicamente invocado via kobe-dispatch.",
    )
    parser.add_argument("--missao", required=True, help="id da missão")
    parser.add_argument("--tarefa", required=True, help="id da tarefa (T1, T2, ...)")
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--prompt", help="prompt inline (cuidado com size)")
    grupo.add_argument("--prompt-file", help="path pra arquivo com o prompt")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_DEFAULT)
    parser.add_argument(
        "--kobe-home",
        default=os.environ.get("KOBE_HOME"),
        help="raiz do Kobe (default: env KOBE_HOME)",
    )
    args = parser.parse_args(argv)

    if not args.kobe_home:
        print("--kobe-home não fornecido e KOBE_HOME não está no env", file=sys.stderr)
        return 2
    kobe_home = Path(args.kobe_home).expanduser().resolve()

    if args.prompt is not None:
        prompt = args.prompt
    else:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    return run(
        kobe_home=kobe_home,
        missao_id=args.missao,
        tarefa_id=args.tarefa,
        prompt=prompt,
        timeout_s=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
