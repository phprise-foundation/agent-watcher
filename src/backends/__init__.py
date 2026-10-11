"""Board backend selection for the kanban helper.

The transport is chosen by AW_BACKEND:

  - github (default): GitHub Projects V2 via the `gh` CLI.
  - local: the self-hosted multi-agent kanban board REST API.

Every backend exposes the same primitives:

    items()
    set_field(number, field, value)
    clear_field(number, field)
    comment(number, body)
    close(number, comment, reason)
    create(title, body, status, role, blocked_by) -> number
"""
import os


class KanbanError(Exception):
    pass


class RateLimitError(KanbanError):
    pass


def load(name=None):
    name = name or os.environ.get("AW_BACKEND", "github")
    if name == "local":
        from backends import local

        return local
    from backends import github

    return github
