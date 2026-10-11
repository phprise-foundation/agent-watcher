"""Loading of rules (--rule) with YAML frontmatter.

Each rule is a markdown file:

    ---
    statuses: [reviewing]   # board columns this rule consumes (order = priority)
    role: tech-lead         # value of the Role field this agent represents
    unblock: true           # optional: this agent also handles blocked items
    interactive: true       # optional: acts on new user comments instead of reprocessing
    ---
    # Tech Lead
    ... instructions ...

The reference passed in `--rule` can be:
  - a predefined name (e.g. "tech-lead" -> <rules_dir>/tech-lead.md)
  - a path mounted in the container (e.g. "/rules/my-role.md")
"""
import dataclasses
import os
import pathlib
import re

import yaml

RULES_DIR = pathlib.Path(os.environ.get("AW_RULES_DIR", "/app/rules"))


@dataclasses.dataclass
class Rule:
    name: str
    statuses: list
    role: str
    unblock: bool
    interactive: bool
    personality: str
    body: str


def _resolve(ref: str) -> pathlib.Path:
    p = pathlib.Path(ref)
    if p.exists():
        return p
    if ref.endswith(".md"):
        cand = RULES_DIR / ref
    else:
        cand = RULES_DIR / f"{ref}.md"
    if cand.exists():
        return cand
    raise FileNotFoundError(f"rule not found: {ref}")


def _parse(text: str):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    return meta, m.group(2).strip()


def load_rule(ref: str) -> Rule:
    path = _resolve(ref)
    text = path.read_text()
    meta, body = _parse(text)
    statuses = meta.get("statuses") or []
    if isinstance(statuses, str):
        statuses = [s.strip() for s in statuses.split(",") if s.strip()]
    statuses = [str(s) for s in statuses]
    return Rule(
        name=path.stem,
        statuses=statuses,
        role=meta.get("role") or "",
        unblock=bool(meta.get("unblock", False)),
        interactive=bool(meta.get("interactive", False)),
        personality=meta.get("personality") or "",
        body=body,
    )


def load_rules(refs: list) -> list:
    return [load_rule(r) for r in refs]


def combined_statuses(rules: list) -> list:
    """Ordered union of the statuses of all rules (preserves order)."""
    seen = []
    for r in rules:
        for s in r.statuses:
            if s not in seen:
                seen.append(s)
    return seen
