"""Loading of roles (--role) with YAML frontmatter.

Each role is a markdown file:

    ---
    statuses: [reviewing]   # board columns this role consumes (order = priority)
    unblock: true           # optional: this agent also handles blocked items
    interactive: true       # optional: acts on new user comments instead of reprocessing
    ---
    # Tech Lead
    ... instructions ...

The reference passed in `--role` can be:
  - a predefined name (e.g. "tech-lead" -> <roles_dir>/tech-lead.md)
  - a path mounted in the container (e.g. "/roles/my-role.md")
"""
import dataclasses
import os
import pathlib
import re

import yaml

ROLES_DIR = pathlib.Path(os.environ.get("AW_ROLES_DIR", "/app/roles"))


@dataclasses.dataclass
class Role:
    name: str
    statuses: list
    unblock: bool
    interactive: bool
    personality: str
    body: str


def _resolve(ref: str) -> pathlib.Path:
    p = pathlib.Path(ref)
    if p.exists():
        return p
    if ref.endswith(".md"):
        cand = ROLES_DIR / ref
    else:
        cand = ROLES_DIR / f"{ref}.md"
    if cand.exists():
        return cand
    raise FileNotFoundError(f"role not found: {ref}")


def _parse(text: str):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, m.group(2).strip()


def load_role(ref: str) -> Role:
    path = _resolve(ref)
    text = path.read_text()
    meta, body = _parse(text)
    statuses = meta.get("statuses") or []
    if isinstance(statuses, str):
        statuses = [s.strip() for s in statuses.split(",") if s.strip()]
    statuses = [str(s) for s in statuses]
    return Role(
        name=path.stem,
        statuses=statuses,
        unblock=bool(meta.get("unblock", False)),
        interactive=bool(meta.get("interactive", False)),
        personality=meta.get("personality") or "",
        body=body,
    )


def load_roles(refs: list) -> list:
    return [load_role(r) for r in refs]


def combined_statuses(roles: list) -> list:
    """Ordered union of the statuses of all roles (preserves order)."""
    seen = []
    for r in roles:
        for s in r.statuses:
            if s not in seen:
                seen.append(s)
    return seen
