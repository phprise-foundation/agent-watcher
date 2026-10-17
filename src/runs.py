"""Runs ledger: one JSON line per opencode invocation, keyed by session id.

The row records what the agent-watcher knows at run time (agent, role, item,
model, outcome, timing, session id). Tokens and cost are not stored here: they
live in opencode's own `session` table and are joined later on `session_id`.
"""
import dataclasses
import json
import os
import uuid


@dataclasses.dataclass
class RunRecord:
    session_id: str
    outcome: str
    error: str
    started_at: float
    ended_at: float

    @property
    def duration_s(self):
        return round(self.ended_at - self.started_at, 3)


def new_run_id():
    return uuid.uuid4().hex


def runs_file(config):
    return config.get("runs_file") or os.path.join(
        config["workdir"], ".agent-watcher", "runs.jsonl"
    )


def append_run(path, row):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
