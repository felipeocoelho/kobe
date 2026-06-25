#!/usr/bin/env python3
"""Smoke test isolado do Hindsight (Highlander Frente 2.2).

Roda no PROD VPS (o operador não testa em dev), DEPOIS de subir o stack:
    cd $KOBE_HOME/infra/hindsight && sg docker -c "docker compose up -d"
    python3 smoke_test.py

NÃO toca o bot, o Supabase nem a Evolution. Exercita o serviço vivo via REST:
sobe um "bank" de teste, faz RETAIN de um fato plantado, faz RECALL e exige que
o fato volte. Mede a latência do retain e imprime o `usage` (custo do retain).
Critério de PASSAGEM: o fato plantado volta no recall.

Stdlib só (urllib/json) — roda em qualquer Python 3.9+ sem instalar nada.

Reversível: o bank de teste é descartável; `docker compose down -v` apaga tudo.

Saída: imprime PASS/FAIL e sai com código 0 (pass) ou 1 (fail) — pra encadear
em scripts. Em erro de schema/path, imprime o que tentou pra facilitar iterar.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8888"
BANK = "kobe-smoke"
TIMEOUT = 60

# Fato plantado: específico o bastante pra não dar falso-positivo no recall.
PLANTED_FACT = "O operador do Kobe se chama Felipe e prefere respostas diretas."
RECALL_QUERY = "Como o operador do Kobe prefere as respostas?"
RECALL_NEEDLE = "diret"  # substring que deve aparecer no que o recall devolver


def _req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 0, f"URLError: {e.reason}"


def _discover_paths() -> dict[str, str]:
    """Descobre os paths de retain/recall via /openapi.json do serviço vivo.
    Fallback pra candidatos conhecidos se o openapi não estiver acessível."""
    retain_candidates = [
        f"/v1/default/banks/{BANK}/memories",
        f"/v1/default/banks/{BANK}/memories/retain",
        f"/v1/default/banks/{BANK}/retain",
    ]
    recall_candidates = [
        f"/v1/default/banks/{BANK}/memories/recall",
        f"/v1/default/banks/{BANK}/recall",
    ]
    status, spec = _req("GET", "/openapi.json")
    if status == 200 and isinstance(spec, dict):
        paths = list((spec.get("paths") or {}).keys())
        # casa o template {bank_id} com o nosso BANK
        def _match(cands_kw: str) -> str | None:
            for p in paths:
                pl = p.lower()
                if "recall" in cands_kw and pl.endswith("/recall") and "bank" in pl:
                    return p.replace("{bank_id}", BANK).replace("{bankId}", BANK)
            for p in paths:
                pl = p.lower()
                if cands_kw == "retain" and pl.rstrip("/").endswith("memories") and "bank" in pl:
                    return p.replace("{bank_id}", BANK).replace("{bankId}", BANK)
            return None
        r = _match("retain")
        rc = _match("recall")
        if r and rc:
            print(f"  paths via openapi: retain={r} recall={rc}")
            return {"retain": r, "recall": rc}
        print("  openapi sem match claro — usando candidatos")
    return {"retain": retain_candidates[0], "recall": recall_candidates[0],
            "_retain_alts": retain_candidates, "_recall_alts": recall_candidates}


def _post_first_ok(candidates: list[str], body: dict) -> tuple[str, int, dict | str]:
    """Tenta cada candidato; retorna o primeiro que não der 404."""
    last = ("", 0, "")
    for path in candidates:
        status, resp = _req("POST", path, body)
        last = (path, status, resp)
        if status != 404:
            return last
    return last


def main() -> int:
    print(f"== Smoke Hindsight @ {BASE} (bank={BANK}) ==")

    # 1) Health — o serviço responde?
    status, _ = _req("GET", "/")
    print(f"[1] health GET / -> {status}")
    if status == 0:
        print("FAIL: serviço não respondeu em 8888. Container up? `docker compose ps`")
        return 1

    # 2) Cria/atualiza o bank de teste (idempotente).
    status, resp = _req("PUT", f"/v1/default/banks/{BANK}", {})
    print(f"[2] PUT bank -> {status}")

    # 3) Descobre paths e faz RETAIN (async=false pra poder dar recall já).
    paths = _discover_paths()
    retain_body = {"items": [{"content": PLANTED_FACT}], "async": False}
    retain_cands = paths.get("_retain_alts", [paths["retain"]])
    t0 = time.monotonic()
    rpath, status, resp = _post_first_ok(retain_cands, retain_body)
    dt = time.monotonic() - t0
    print(f"[3] RETAIN {rpath} -> {status} em {dt:.2f}s")
    if status not in (200, 201, 202):
        print(f"FAIL: retain não OK. resp={str(resp)[:400]}")
        return 1
    if isinstance(resp, dict) and resp.get("usage"):
        print(f"    usage (custo do retain): {json.dumps(resp['usage'])}")
    else:
        print("    (sem campo usage na resposta — medir custo pela conta OpenAI)")

    # 4) RECALL — o fato plantado volta?
    recall_cands = paths.get("_recall_alts", [paths["recall"]])
    rpath, status, resp = _post_first_ok(recall_cands, {"query": RECALL_QUERY})
    print(f"[4] RECALL {rpath} -> {status}")
    if status != 200:
        print(f"FAIL: recall não OK. resp={str(resp)[:400]}")
        return 1
    blob = json.dumps(resp).lower() if isinstance(resp, (dict, list)) else str(resp).lower()
    if RECALL_NEEDLE in blob:
        print(f"[OK] recall trouxe o fato plantado (achou '{RECALL_NEEDLE}').")
        print("== PASS ==")
        return 0
    print(f"FAIL: recall não trouxe o fato. needle='{RECALL_NEEDLE}' ausente.")
    print(f"    resp={str(resp)[:600]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
