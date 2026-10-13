"""Unit tests for the agent-runner reconciliation."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import runner  # noqa: E402


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def agent(**overrides):
    data = {
        "id": "coder", "name": "Coder", "active": True, "rule": "coder",
        "profile": "", "model": "m", "fallback_model": "", "interval": 300,
        "workspace": "/workspace",
    }
    data.update(overrides)
    return data


class ReconcileTest(unittest.TestCase):
    def test_starts_new_agents(self):
        running = {}
        with mock.patch.object(runner, "spawn", return_value=FakeProcess()) as spawn:
            runner.reconcile(running, {"coder": agent()})

        self.assertIn("coder", running)
        spawn.assert_called_once()

    def test_keeps_unchanged_agents(self):
        process = FakeProcess()
        running = {"coder": {"process": process, "spec": runner.spec_of(agent())}}
        with mock.patch.object(runner, "spawn") as spawn:
            runner.reconcile(running, {"coder": agent()})

        spawn.assert_not_called()
        self.assertFalse(process.terminated)

    def test_restarts_changed_agents(self):
        old = FakeProcess()
        running = {"coder": {"process": old, "spec": runner.spec_of(agent())}}
        with mock.patch.object(runner, "spawn", return_value=FakeProcess()):
            runner.reconcile(running, {"coder": agent(model="other")})

        self.assertTrue(old.terminated)
        self.assertEqual(running["coder"]["spec"], runner.spec_of(agent(model="other")))

    def test_stops_removed_agents(self):
        old = FakeProcess()
        running = {"coder": {"process": old, "spec": runner.spec_of(agent())}}

        runner.reconcile(running, {})

        self.assertTrue(old.terminated)
        self.assertEqual(running, {})


class WantedAgentsTest(unittest.TestCase):
    def test_skips_inactive_or_modelless(self):
        rows = [agent(id="coder"), agent(id="qa", active=False), agent(id="ghost", model="")]
        with mock.patch.object(runner.local, "agents", return_value=rows):
            wanted = runner.wanted_agents()

        self.assertEqual(list(wanted), ["coder"])


class CommandTest(unittest.TestCase):
    def test_command_carries_spec(self):
        with mock.patch.dict(os.environ, {"AW_KANBAN_URL": "http://board", "AW_KANBAN_TOKEN": "t"}):
            command = runner.command_for(agent(profile="skeptic", fallback_model="f", interval=120))

        self.assertEqual(command[command.index("--role") + 1], "coder")
        self.assertEqual(command[command.index("--interval") + 1], "120")
        self.assertIn("skeptic", command)
        self.assertIn("f", command)


if __name__ == "__main__":
    unittest.main()
