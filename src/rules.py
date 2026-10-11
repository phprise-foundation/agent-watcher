"""Carregamento de regras (--rule) com frontmatter YAML.

Cada regra é um arquivo markdown:

    ---
    statuses: [reviewing]   # colunas do quadro que esta regra consome (ordem = prioridade)
    role: tech-lead         # valor do campo Role que este agente representa
    unblock: true           # opcional: este agente também cuida de itens bloqueados
    interactive: true       # opcional: responde a novos comentários do usuário em vez de reprocessar
    ---
    # Revisor
    ... instruções ...

A referência passada em `--rule` pode ser:
  - um nome predefinido (ex.: "revisor" -> <rules_dir>/revisor.md)
  - um caminho montado no container (ex.: "/rules/meu-cargo.md")
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
    raise FileNotFoundError(f"regra não encontrada: {ref}")


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
    """União ordenada dos statuses de todas as regras (preserva a ordem)."""
    seen = []
    for r in rules:
        for s in r.statuses:
            if s not in seen:
                seen.append(s)
    return seen
