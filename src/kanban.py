#!/usr/bin/env python3
"""kanban — helper de quadro (GitHub Projects V2) para os agentes.

Lê a configuração do ambiente:
  AW_OWNER    login da org/usuário dono do projeto (ex.: phprise-foundation)
  AW_REPO     repositório no formato owner/repo (ex.: phprise-foundation/koenma-id)
  AW_PROJECT  número do Project V2 (ex.: 1)

Campos do quadro esperados:
  Status   (single-select): to-do -> qa-refinement -> coding -> reviewing -> testing -> done
  Blocked  (single-select): opção "blocked" setada = bloqueado, vazia = livre
  Role     (single-select): product-owner | tech-lead | qa | coder
  Worker   (single-select): nome do agente que está trabalhando

O fluxo é linear (itens NÃO voltam); correção gera uma nova issue.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

def owner():
    return os.environ.get("AW_OWNER", "")


def project():
    return os.environ.get("AW_PROJECT", "")


def repo():
    return os.environ.get("AW_REPO", "")


STATUSES = ["to-do", "qa-refinement", "coding", "reviewing", "testing", "done"]
ACTIVE = ["qa-refinement", "coding", "reviewing", "testing"]
BLOCKED_OPTION = "blocked"


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


class KanbanError(Exception):
    pass


class RateLimitError(KanbanError):
    pass


_RATE_LIMIT_MARKERS = ("rate limit", "secondary rate limit", "abuse", "429")


def _rate_limited(result):
    text = f"{result.stderr or ''} {result.stdout or ''}".lower()
    return any(m in text for m in _RATE_LIMIT_MARKERS)


def gh(args):
    """Roda um comando `gh` e converte falha em exceção (distinguindo rate limit)."""
    r = run(args)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if _rate_limited(r):
            raise RateLimitError(err or "rate limit do GitHub atingido")
        raise KanbanError(err or f"falha ao executar {args[0]}")
    return r


def fail(msg):
    raise KanbanError(msg)


CACHE_TTL = 30


def _cache_path():
    base = os.environ.get("AW_WORKDIR") or os.getcwd()
    return os.path.join(base, f".agent-watcher-cache-{project()}.json")


def _cache_read(max_age):
    try:
        with open(_cache_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if max_age is not None and time.time() - data.get("ts", 0) > max_age:
        return None
    return data.get("items")


def _cache_store(items):
    path = _cache_path()
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"ts": time.time(), "items": items}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def invalidate_cache():
    try:
        os.remove(_cache_path())
    except OSError:
        pass


def require_config():
    if not owner() or not project() or not repo():
        fail("config ausente: defina AW_OWNER, AW_REPO e AW_PROJECT")


def items():
    require_config()
    cached = _cache_read(CACHE_TTL)
    if cached is not None:
        return cached
    try:
        r = gh(["gh", "project", "item-list", str(project()), "--owner", owner(),
                "--format", "json", "--limit", "200"])
    except RateLimitError:
        stale = _cache_read(None)
        if stale is not None:
            return stale
        raise
    data = json.loads(r.stdout)
    out = []
    for it in data.get("items", []):
        content = it.get("content") or {}
        if content.get("type") != "Issue":
            continue
        out.append({
            "number": content.get("number"),
            "url": content.get("url"),
            "title": content.get("title"),
            "status": it.get("status") or "",
            "blocked": bool(it.get("blocked")),
            "blocked_by": it.get("blockedBy") or it.get("BlockedBy") or it.get("blocked_by") or "",
            "role": it.get("role") or "",
            "worker": it.get("worker") or "",
        })
    _cache_store(out)
    return out


def sort_key(it):
    m = re.search(r"RFC-(\d+(?:-\d+)*)", it["title"] or "")
    nums = tuple(int(x) for x in m.group(1).split("-")) if m else (999999,)
    return nums


def issue_url(number):
    if str(number).startswith("http"):
        return number
    return f"https://github.com/{repo()}/issues/{number}"


def cmd_list(args):
    rows = items()
    if args.role:
        rows = [r for r in rows if r["role"] == args.role]
    if args.status:
        rows = [r for r in rows if r["status"] == args.status]
    if args.worker_empty:
        rows = [r for r in rows if not r["worker"]]
    if args.blocked:
        rows = [r for r in rows if r["blocked"]]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    for r in rows:
        flag = "BLK" if r["blocked"] else "   "
        blkby = f" <- {r['blocked_by']}" if r.get("blocked_by") else ""
        print(f"#{r['number']}\t{r['status'] or '-'}\t{r['role'] or '-'}\t"
              f"{r['worker'] or '-'}\t{flag}\t{r['title']}{blkby}")


def cmd_next(args):
    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
    rows = [r for r in items()
            if r["status"] in statuses and not r["worker"] and not r["blocked"]]
    rows.sort(key=lambda r: (statuses.index(r["status"]) if r["status"] in statuses else 99,
                             sort_key(r)))
    if not rows:
        sys.exit(1)
    r = rows[0]
    print(f"{r['number']}\t{r['status']}\t{r['title']}")


def cmd_get(args):
    rows = [r for r in items() if str(r["number"]) == str(args.number)]
    if not rows:
        fail(f"no item for issue #{args.number}")
    print(json.dumps(rows[0], ensure_ascii=False))


def cmd_busy(args):
    for r in items():
        if r["status"] in ACTIVE and not r["blocked"]:
            print("busy")
            return
    print("idle")


def set_field(number, field, value):
    if not value:
        return
    url = issue_url(number)
    gh(["gh", "project", "item-edit", str(project()), "--owner", owner(),
        "--url", url, "--field", field, "--value", value])
    invalidate_cache()


def clear_field(number, field):
    url = issue_url(number)
    gh(["gh", "project", "item-edit", str(project()), "--owner", owner(),
        "--url", url, "--field", field, "--clear"])
    invalidate_cache()


def cmd_set(args):
    if args.status:
        set_field(args.number, "Status", args.status)
    if args.role:
        set_field(args.number, "Role", args.role)
    if args.worker:
        set_field(args.number, "Worker", args.worker)
    if args.clear_worker:
        clear_field(args.number, "Worker")
    if args.blocked:
        set_field(args.number, "Blocked", BLOCKED_OPTION)
    if args.unblocked:
        clear_field(args.number, "Blocked")
    if args.blocked_by:
        set_field(args.number, "BlockedBy", args.blocked_by)
    if args.clear_blocked_by:
        clear_field(args.number, "BlockedBy")


def cmd_clear(args):
    clear_field(args.number, args.field)


def cmd_comment(args):
    url = issue_url(args.number)
    body = args.text
    if args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    gh(["gh", "issue", "comment", url, "--body", body])


def cmd_create(args):
    require_config()
    body = ""
    if args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    r = gh(["gh", "api", f"repos/{repo()}/issues",
            "-f", f"title={args.title}", "-f", f"body={body}"])
    data = json.loads(r.stdout)
    number = data["number"]
    url = data["html_url"]
    gh(["gh", "project", "item-add", str(project()), "--owner", owner(), "--url", url])
    set_field(number, "Status", args.status or "to-do")
    set_field(number, "Role", args.role)
    if args.blocked_by:
        set_field(number, "Blocked", BLOCKED_OPTION)
        set_field(number, "BlockedBy", args.blocked_by)
    print(number)


def main():
    ap = argparse.ArgumentParser(prog="kanban")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--role")
    p.add_argument("--status")
    p.add_argument("--worker-empty", action="store_true")
    p.add_argument("--blocked", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("next")
    p.add_argument("--statuses", required=True)
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("get")
    p.add_argument("number")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("busy")
    p.set_defaults(func=cmd_busy)

    p = sub.add_parser("set")
    p.add_argument("number")
    p.add_argument("--status")
    p.add_argument("--role")
    p.add_argument("--worker")
    p.add_argument("--clear-worker", action="store_true")
    p.add_argument("--blocked", action="store_true")
    p.add_argument("--unblocked", action="store_true")
    p.add_argument("--blocked-by")
    p.add_argument("--clear-blocked-by", action="store_true")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("comment")
    p.add_argument("number")
    p.add_argument("--text")
    p.add_argument("--body-file")
    p.set_defaults(func=cmd_comment)

    p = sub.add_parser("clear")
    p.add_argument("number")
    p.add_argument("--field", required=True)
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("create")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file")
    p.add_argument("--status")
    p.add_argument("--role")
    p.add_argument("--blocked-by")
    p.set_defaults(func=cmd_create)

    args = ap.parse_args()
    try:
        args.func(args)
    except KanbanError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
