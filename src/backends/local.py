"""Local backend: talks to the self-hosted multi-agent kanban board REST API.

Reads configuration from the environment:
  AW_KANBAN_URL    board base URL (e.g. http://board)
  AW_KANBAN_TOKEN  bearer token (the board password)
  AW_AGENT_NAME    name signed on comments (defaults to "agent")

Uses only the standard library (urllib), so no extra runtime dependency is
needed inside the container.
"""
import json
import os
import urllib.error
import urllib.request

from backends import KanbanError

_FIELD_KEYS = {
    "Status": "status",
    "Role": "role",
    "Worker": "worker",
    "Blocked": "blocked",
    "BlockedBy": "blocked_by",
}

_CLEAR_PAYLOADS = {
    "Worker": {"worker": ""},
    "Role": {"role": None},
    "Blocked": {"blocked": False},
    "BlockedBy": {"blocked": False},
}


def _base_url():
    return os.environ.get("AW_KANBAN_URL", "").rstrip("/")


def _token():
    return os.environ.get("AW_KANBAN_TOKEN", "")


def _agent_name():
    return os.environ.get("AW_AGENT_NAME", "agent")


def _require_config():
    if not _base_url():
        raise KanbanError("missing config: set AW_KANBAN_URL (and AW_KANBAN_TOKEN)")


def _request(method, path, payload=None):
    _require_config()
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        _base_url() + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return _send(request)


def _send(request):
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise KanbanError(f"board error {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise KanbanError(f"board unreachable: {error.reason}") from error
    return json.loads(body) if body else {}


def _comment(raw):
    return {
        "body": raw.get("body") or "",
        "author": raw.get("author") or "",
        "created_at": raw.get("created_at") or "",
    }


def _row(raw):
    return {
        "number": raw.get("id"),
        "url": "",
        "title": raw.get("title") or "",
        "status": raw.get("status") or "",
        "blocked": bool(raw.get("blocked")),
        "blocked_by": raw.get("blocked_by") or "",
        "role": raw.get("role") or "",
        "worker": raw.get("worker") or "",
        "comments": [_comment(c) for c in (raw.get("comments") or [])],
    }


def items():
    return [_row(raw) for raw in _request("GET", "/api/items").get("items", [])]


def set_field(number, field, value):
    key = _FIELD_KEYS.get(field)
    if not value or key is None:
        return
    _request("PATCH", _item_path(number), {key: True if field == "Blocked" else value})


def clear_field(number, field):
    payload = _CLEAR_PAYLOADS.get(field)
    if payload is None:
        return
    _request("PATCH", _item_path(number), payload)


def comment(number, body):
    _request("POST", f"{_item_path(number)}/comments",
             {"author": _agent_name(), "body": body})


def close(number, comment_text, reason):
    _request("POST", f"{_item_path(number)}/close", {"comment": comment_text or ""})


def create(title, body, status, role, blocked_by):
    item = _request("POST", "/api/items",
                    {"title": title, "body": body, "status": status or "intent",
                     "role": role}).get("item", {})
    number = item.get("id")
    if blocked_by:
        set_field(number, "Blocked", "blocked")
        set_field(number, "BlockedBy", blocked_by)
    return number


def workflow():
    """Board workflow: columns with their role and allowed transitions."""
    board = _request("GET", "/api/board").get("board", {})
    return {
        "name": board.get("name") or "",
        "columns": [_workflow_column(raw) for raw in (board.get("columns") or [])],
    }


def advance(number, to=None):
    payload = {} if to is None else {"to": to}
    _request("POST", f"{_item_path(number)}/advance", payload)


def agents():
    """Agent registry entries (the runner materializes the active ones)."""
    rows = _request("GET", "/api/agents").get("agents", [])
    return [_agent_row(raw) for raw in rows]


def _workflow_column(raw):
    return {
        "id": raw.get("id") or "",
        "name": raw.get("name") or "",
        "position": raw.get("position", 0),
        "final": bool(raw.get("final")),
        "role": raw.get("role") or "",
        "transitions": [str(t) for t in (raw.get("transitions") or [])],
    }


def _agent_row(raw):
    return {
        "id": raw.get("id") or "",
        "name": raw.get("name") or "",
        "active": bool(raw.get("active")),
        "rule": raw.get("rule") or "",
        "profile": raw.get("profile") or "",
        "model": raw.get("model") or "",
        "fallback_model": raw.get("fallback_model") or "",
        "interval": int(raw.get("interval") or 300),
        "workspace": raw.get("workspace") or "/workspace",
    }


def _item_path(number):
    return f"/api/items/{int(number)}"
