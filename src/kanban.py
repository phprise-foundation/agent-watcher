#!/usr/bin/env python3
"""kanban — board helper (GitHub Projects V2) for the agents.

Reads configuration from the environment:
  AW_OWNER    org/user login that owns the project (e.g. phprise-foundation)
  AW_REPO     repository as owner/repo (e.g. phprise-foundation/koenma-id)
  AW_PROJECT  Project V2 number (e.g. 1)

Expected board fields:
  Status   (single-select): intent -> feasibility -> design -> plan -> qa-refinement -> coding -> reviewing -> testing -> done
  Blocked  (single-select): option "blocked" set = blocked, empty = free
  Role     (single-select): product-owner | solutions-architect | systems-architect | framework-architect | tech-lead | qa | coder
  Worker   (text): name of the agent currently working on the item

The flow is linear (items do NOT move backwards); a fix creates a new issue.
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


STATUSES = ["intent", "feasibility", "design", "plan", "qa-refinement", "coding", "reviewing", "testing", "done"]
ACTIVE = ["feasibility", "design", "plan", "qa-refinement", "coding", "reviewing", "testing"]
BLOCKED_OPTION = "blocked"


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


class KanbanError(Exception):
    pass


class RateLimitError(KanbanError):
    pass


# "unknown owner type" is what `gh project` returns when owner resolution
# fails due to a GraphQL rate limit (the user/org query comes back with an error).
_RATE_LIMIT_MARKERS = ("rate limit", "secondary rate limit", "abuse", "429",
                       "unknown owner type")


def _rate_limited(result):
    text = f"{result.stderr or ''} {result.stdout or ''}".lower()
    return any(m in text for m in _RATE_LIMIT_MARKERS)


def gh(args):
    """Run a `gh` command and turn failure into an exception (distinguishing rate limit)."""
    r = run(args)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        if _rate_limited(r):
            raise RateLimitError(err or "GitHub rate limit reached")
        raise KanbanError(err or f"failed to run {args[0]}")
    return r


def fail(msg):
    raise KanbanError(msg)


CACHE_VERSION = 2
LOCK_MAX_AGE = 120


def _cache_ttl():
    """Cache TTL in seconds (configurable via AW_CACHE_TTL)."""
    try:
        return max(5, int(os.environ.get("AW_CACHE_TTL", "60")))
    except ValueError:
        return 60


def _cache_path():
    base = os.environ.get("AW_WORKDIR") or os.getcwd()
    return os.path.join(base, f".agent-watcher-cache-{project()}.json")


def _lock_path():
    return _cache_path() + ".lock"


def _cache_read(max_age):
    try:
        with open(_cache_path()) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    if max_age is not None and time.time() - data.get("ts", 0) > max_age:
        return None
    return data.get("items")


def _cache_store(items):
    path = _cache_path()
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"version": CACHE_VERSION, "ts": time.time(), "items": items},
                      f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def invalidate_cache():
    try:
        os.remove(_cache_path())
    except OSError:
        pass


def _acquire_lock():
    """Try to acquire the fetch lock — only one agent re-runs the query at a time."""
    path = _lock_path()
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(path)
        except OSError:
            age = 0
        if age > LOCK_MAX_AGE:
            try:
                os.remove(path)
            except OSError:
                return False
            return _acquire_lock()
        return False


def _release_lock():
    try:
        os.remove(_lock_path())
    except OSError:
        pass


def require_config():
    if not owner() or not project() or not repo():
        fail("missing config: set AW_OWNER, AW_REPO and AW_PROJECT")


def items():
    require_config()
    cached = _cache_read(_cache_ttl())
    if cached is not None:
        return cached
    if _acquire_lock():
        try:
            return _fetch_and_store()
        finally:
            _release_lock()
    # another agent is fetching: wait for the cache to appear, then use stale
    for _ in range(10):
        time.sleep(0.5)
        fresh = _cache_read(_cache_ttl())
        if fresh is not None:
            return fresh
    stale = _cache_read(None)
    if stale is not None:
        return stale
    return _fetch_and_store()


_BOARD_QUERY = """
query($login: String!, $number: Int!) {
  organization(login: $login) { projectV2(number: $number) { ...Board } }
  user(login: $login) { projectV2(number: $number) { ...Board } }
}
fragment Board on ProjectV2 {
  items(first: 200) {
    nodes {
      fieldValues(first: 20) {
        nodes {
          ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2Field { name } } }
          ... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2Field { name } } }
        }
      }
      content {
        ... on Issue {
          number
          title
          url
          comments(last: 50) {
            nodes { body createdAt author { login } }
          }
        }
      }
    }
  }
}
"""


def _fetch_board():
    """Fetch the whole board (items + comments) in a single GraphQL query."""
    r = gh(["gh", "api", "graphql",
            "-f", f"query={_BOARD_QUERY}",
            "-f", f"login={owner()}",
            "-F", f"number={int(project())}"])
    return _parse_board(json.loads(r.stdout))


def _parse_board(data):
    d = data.get("data") or {}
    project = None
    for key in ("organization", "user"):
        node = d.get(key)
        if node and node.get("projectV2"):
            project = node["projectV2"]
            break
    if project is None:
        raise KanbanError("project not found (invalid owner/project?)")
    out = []
    for it in ((project.get("items") or {}).get("nodes")) or []:
        content = it.get("content") or {}
        number = content.get("number")
        if number is None:
            continue
        fields = {}
        for fv in ((it.get("fieldValues") or {}).get("nodes")) or []:
            field = ((fv.get("field") or {}).get("name")) or ""
            if not field:
                continue
            if "name" in fv:
                fields[field] = fv.get("name") or ""
            elif "text" in fv:
                fields[field] = fv.get("text") or ""
        comments = []
        for c in (((content.get("comments") or {}).get("nodes")) or []):
            comments.append({
                "body": c.get("body") or "",
                "author": ((c.get("author") or {}).get("login")) or "",
                "created_at": c.get("createdAt") or "",
            })
        comments.sort(key=lambda x: x["created_at"])
        out.append({
            "number": number,
            "url": content.get("url") or "",
            "title": content.get("title") or "",
            "status": fields.get("Status", ""),
            "blocked": fields.get("Blocked", "") == BLOCKED_OPTION,
            "blocked_by": fields.get("BlockedBy", ""),
            "role": fields.get("Role", ""),
            "worker": fields.get("Worker", ""),
            "comments": comments,
        })
    return out


def _fetch_and_store():
    try:
        out = _fetch_board()
    except KanbanError:
        stale = _cache_read(None)
        if stale is not None:
            return stale
        raise
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
        fail(f"no item for issue #{args.number}")
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


def cmd_close(args):
    url = issue_url(args.number)
    cmd = ["gh", "issue", "close", url, "--reason", args.reason]
    if args.comment:
        cmd += ["--comment", args.comment]
    gh(cmd)


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
    set_field(number, "Status", args.status or "intent")
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
