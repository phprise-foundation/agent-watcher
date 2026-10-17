#!/usr/bin/env python3
"""agent-runner — materializes the board's agent registry.

Reads the agent registry from the self-hosted board and keeps exactly one
`agent-watcher` subprocess per active agent (independent and parallel). Every
`AW_RUNNER_POLL` seconds it reconciles: it starts new agents, restarts agents
whose spec changed and stops agents removed or deactivated on the board.

Environment:
  AW_KANBAN_URL / AW_KANBAN_TOKEN   board access (required)
  AW_RUNNER_POLL                    reconcile interval in seconds (default 30)
  AW_AGENT_SCRIPT                   cli.py path (defaults to this directory)
"""
import datetime
import os
import signal
import subprocess
import sys
import time

import backends
from backends import local


def log(message):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def spec_of(agent):
    return (
        agent["name"], agent["profile"], agent["model"],
        agent["fallback_model"], agent["interval"], agent["workspace"],
    )


def command_for(agent):
    cli = os.environ.get("AW_AGENT_SCRIPT") or os.path.join(os.path.dirname(__file__), "cli.py")
    command = [
        sys.executable, cli,
        "--name", agent["name"],
        "--role", agent["id"],
        "--model", agent["model"],
        "--backend", "local",
        "--kanban-url", os.environ.get("AW_KANBAN_URL", ""),
        "--kanban-token", os.environ.get("AW_KANBAN_TOKEN", ""),
        "--workdir", agent["workspace"],
        "--interval", str(agent["interval"]),
    ]
    if agent["profile"]:
        command += ["--profile", agent["profile"]]
    if agent["fallback_model"]:
        command += ["--fallback-model", agent["fallback_model"]]
    return command


def spawn(agent):
    log(f"starting {agent['name']} (role={agent['id']}, model={agent['model']})")
    return subprocess.Popen(command_for(agent))


def terminate(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def wanted_agents():
    return {
        agent["id"]: agent
        for agent in local.agents()
        if agent["id"] and agent["active"] and agent["model"]
    }


def reconcile(running, wanted):
    for agent_id in list(running):
        if agent_id in wanted:
            continue
        terminate(running.pop(agent_id)["process"])
    for agent_id, agent in wanted.items():
        current = running.get(agent_id)
        if current and current["spec"] == spec_of(agent) and current["process"].poll() is None:
            continue
        if current:
            terminate(current["process"])
        running[agent_id] = {"process": spawn(agent), "spec": spec_of(agent)}


def run():
    interval = int(os.environ.get("AW_RUNNER_POLL", "30"))
    log(f"runner started: board={os.environ.get('AW_KANBAN_URL', '')} poll={interval}s")
    running = {}
    try:
        while True:
            try:
                wanted = wanted_agents()
            except backends.KanbanError as error:
                log(f"board unavailable: {error}")
                time.sleep(interval)
                continue
            reconcile(running, wanted)
            time.sleep(interval)
    finally:
        for entry in running.values():
            terminate(entry["process"])


def main():
    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        run()
    except SystemExit:
        pass


if __name__ == "__main__":
    main()
