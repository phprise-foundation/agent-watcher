"""GitHub Projects V2 backend for the kanban helper.

Reads configuration from the environment:
  AW_OWNER    org/user login that owns the project (e.g. phprise-foundation)
  AW_REPO     repository as owner/repo (e.g. phprise-foundation/koenma-id)
  AW_PROJECT  Project V2 number (e.g. 1)

Expected board fields:
  Status   (single-select): intent -> feasibility -> design -> plan -> qa-refinement -> coding -> reviewing -> testing -> done
  Blocked  (single-select): option "blocked" set = blocked, empty = free
  Role     (single-select): product-owner | solutions-architect | systems-architect | framework-architect | tech-lead | qa | coder
  Worker   (text): name of the agent currently working on the item
"""
import json
import os
import subprocess
import time

from backends import KanbanError, RateLimitError

BLOCKED_OPTION = "blocked"

CACHE_VERSION = 2
LOCK_MAX_AGE = 120


def owner():
    return os.environ.get("AW_OWNER", "")


def project():
    return os.environ.get("AW_PROJECT", "")


def repo():
    return os.environ.get("AW_REPO", "")


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


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


def require_config():
    if not owner() or not project() or not repo():
        raise KanbanError("missing config: set AW_OWNER, AW_REPO and AW_PROJECT")


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


def issue_url(number):
    if str(number).startswith("http"):
        return number
    return f"https://github.com/{repo()}/issues/{number}"


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


def comment(number, body):
    gh(["gh", "issue", "comment", issue_url(number), "--body", body])


def close(number, comment_text, reason):
    cmd = ["gh", "issue", "close", issue_url(number), "--reason", reason]
    if comment_text:
        cmd += ["--comment", comment_text]
    gh(cmd)


def create(title, body, status, role, blocked_by):
    require_config()
    r = gh(["gh", "api", f"repos/{repo()}/issues",
            "-f", f"title={title}", "-f", f"body={body}"])
    data = json.loads(r.stdout)
    number = data["number"]
    url = data["html_url"]
    gh(["gh", "project", "item-add", str(project()), "--owner", owner(), "--url", url])
    set_field(number, "Status", status or "intent")
    set_field(number, "Role", role)
    if blocked_by:
        set_field(number, "Blocked", BLOCKED_OPTION)
        set_field(number, "BlockedBy", blocked_by)
    return number
