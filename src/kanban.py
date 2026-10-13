#!/usr/bin/env python3
"""kanban — board helper used by both the loop and the opencode agents.

The transport is chosen by AW_BACKEND (github | local) and implemented under
backends/. This module holds the board-agnostic logic and the CLI. The flow is
linear (items do NOT move backwards); a fix creates a new item.
"""
import argparse
import json
import re
import sys

import backends

STATUSES = ["intent", "feasibility", "design", "plan", "qa-refinement", "coding", "reviewing", "testing", "done"]
ACTIVE = ["feasibility", "design", "plan", "qa-refinement", "coding", "reviewing", "testing"]
BLOCKED_OPTION = "blocked"

KanbanError = backends.KanbanError
RateLimitError = backends.RateLimitError

_backend = None


def _board():
    global _backend
    if _backend is None:
        _backend = backends.load()
    return _backend


def items():
    return _board().items()


def set_field(number, field, value):
    _board().set_field(number, field, value)


def clear_field(number, field):
    _board().clear_field(number, field)


def workflow():
    """The board workflow (columns, roles and transitions); empty if unsupported."""
    fn = getattr(_board(), "workflow", None)
    return fn() if fn else {"columns": []}


def advance(number, to=None):
    fn = getattr(_board(), "advance", None)
    if fn is None:
        raise KanbanError("advance is not supported by this backend")
    fn(number, to)


def statuses_for_role(role, data=None):
    """Columns whose stage role is `role`, in board order."""
    workflow_data = data if data is not None else workflow()
    return [c["id"] for c in workflow_data.get("columns", []) if c.get("role") == role]


def sort_key(it):
    m = re.search(r"RFC-(\d+(?:-\d+)*)", it["title"] or "")
    if m:
        return tuple(int(x) for x in m.group(1).split("-"))
    return (999999, int(it.get("number") or 0))


def unresponded_mentions(name):
    """Items with a mention `@name` still without a subsequent reply from the agent."""
    marker = f"@{name}"
    sig = f"{name}:"
    out = []
    for it in items():
        if it["status"] == "done" or it["worker"]:
            continue
        comments = it.get("comments") or []
        for i, c in enumerate(comments):
            if marker in c["body"]:
                answered = any(sig in d["body"] for d in comments[i + 1:])
                if not answered:
                    out.append({
                        "number": it["number"],
                        "title": it["title"],
                        "status": it["status"],
                        "mention": c["body"][:500],
                    })
                    break
    return out


def next_mention(name):
    rows = unresponded_mentions(name)
    return rows[0] if rows else None


def unresponded_comments(name, statuses):
    """Items (in `statuses`) with user activity not yet answered by the agent.

    An item is actionable when:
      - it has no comments (new backlog need), or
      - the last comment is NOT signed by the agent (`name: `), i.e. there is a
        new user reply waiting for analysis.

    This avoids reprocessing the same issue on every scan (token drain), without
    editing/unmarking comments.
    """
    sig = f"{name}:"
    out = []
    for it in items():
        if it["status"] not in statuses:
            continue
        if it["status"] == "done" or it["worker"]:
            continue
        comments = it.get("comments") or []
        last = comments[-1]["body"] if comments else ""
        if not comments or sig not in last:
            out.append({
                "number": it["number"],
                "title": it["title"],
                "status": it["status"],
                "mention": last[:500],
            })
    out.sort(key=sort_key)
    return out


def next_comment(name, statuses):
    rows = unresponded_comments(name, statuses)
    return rows[0] if rows else None


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
        raise KanbanError(f"no item for issue #{args.number}")
    print(json.dumps(rows[0], ensure_ascii=False))


def cmd_mentions(args):
    rows = unresponded_mentions(args.name)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False))
        return
    for r in rows:
        print(f"#{r['number']}\t{r['status'] or '-'}\t{r['title']}")


def cmd_busy(args):
    for r in items():
        if r["status"] in ACTIVE and not r["blocked"]:
            print("busy")
            return
    print("idle")


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
    body = args.text
    if args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    _board().comment(args.number, body)


def cmd_close(args):
    _board().close(args.number, args.comment, args.reason)


def cmd_workflow(args):
    data = workflow()
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return
    for column in data.get("columns", []):
        role = column.get("role") or "-"
        transitions = ",".join(column.get("transitions") or []) or "-"
        final = " (final)" if column.get("final") else ""
        print(f"{column['id']}\t{column['name']}{final}\trole={role}\ttransitions={transitions}")


def cmd_advance(args):
    advance(args.number, args.to)


def cmd_create(args):
    body = ""
    if args.body_file:
        with open(args.body_file) as f:
            body = f.read()
    number = _board().create(args.title, body, args.status, args.role, args.blocked_by)
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

    p = sub.add_parser("mentions")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_mentions)

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

    p = sub.add_parser("close")
    p.add_argument("number")
    p.add_argument("--comment")
    p.add_argument("--reason", default="not planned")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("workflow")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_workflow)

    p = sub.add_parser("advance")
    p.add_argument("number")
    p.add_argument("--to")
    p.set_defaults(func=cmd_advance)

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
