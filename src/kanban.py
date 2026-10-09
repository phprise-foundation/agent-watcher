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

OWNER = os.environ.get("AW_OWNER", "")
PROJECT = os.environ.get("AW_PROJECT", "")
REPO = os.environ.get("AW_REPO", "")

STATUSES = ["to-do", "qa-refinement", "coding", "reviewing", "testing", "done"]
ACTIVE = ["qa-refinement", "coding", "reviewing", "testing"]
BLOCKED_OPTION = "blocked"


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def fail(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def require_config():
    if not OWNER or not PROJECT or not REPO:
        fail("config ausente: defina AW_OWNER, AW_REPO e AW_PROJECT")


def items():
    require_config()
    r = run(["gh", "project", "item-list", str(PROJECT), "--owner", OWNER,
             "--format", "json", "--limit", "200"])
    if r.returncode != 0:
        fail(r.stderr.strip())
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
            "role": it.get("role") or "",
            "worker": it.get("worker") or "",
        })
    return out


def sort_key(it):
    m = re.search(r"RFC-(\d+(?:-\d+)*)", it["title"] or "")
    nums = tuple(int(x) for x in m.group(1).split("-")) if m else (999999,)
    return nums


def issue_url(number):
    if str(number).startswith("http"):
        return number
    return f"https://github.com/{REPO}/issues/{number}"


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
        print(f"#{r['number']}\t{r['status'] or '-'}\t{r['role'] or '-'}\t"
              f"{r['worker'] or '-'}\t{flag}\t{r['title']}")


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
    r = run(["gh", "project", "item-edit", str(PROJECT), "--owner", OWNER,
             "--url", url, "--field", field, "--value", value])
    if r.returncode != 0:
        fail(r.stderr.strip())


def clear_field(number, field):
    url = issue_url(number)
    r = run(["gh", "project", "item-edit", str(PROJECT), "--owner", OWNER,
             "--url", url, "--field", field, "--clear"])
    if r.returncode != 0:
        fail(r.stderr.strip())


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


def cmd_clear(args):
    clear_field(args.number, args.field)


def cmd_comment(args):
    url = issue_url(args.number)
    body = args.text
    if args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    r = run(["gh", "issue", "comment", url, "--body", body])
    if r.returncode != 0:
        fail(r.stderr.strip())


def cmd_create(args):
    require_config()
    body = ""
    if args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    r = run(["gh", "api", f"repos/{REPO}/issues",
             "-f", f"title={args.title}", "-f", f"body={body}"])
    if r.returncode != 0:
        fail(r.stderr.strip())
    data = json.loads(r.stdout)
    number = data["number"]
    url = data["html_url"]
    r2 = run(["gh", "project", "item-add", str(PROJECT), "--owner", OWNER, "--url", url])
    if r2.returncode != 0:
        fail(r2.stderr.strip())
    set_field(number, "Status", args.status or "to-do")
    set_field(number, "Role", args.role)
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
    p.set_defaults(func=cmd_create)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
