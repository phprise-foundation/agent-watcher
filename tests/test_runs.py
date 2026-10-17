"""Unit tests for the runs ledger."""
import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import runs  # noqa: E402
import watcher  # noqa: E402


class ParseRunStdoutTest(unittest.TestCase):
    def test_extracts_session_id_and_text(self):
        stdout = (
            '{"type":"step_start","sessionID":"ses_abc"}\n'
            '{"type":"text","sessionID":"ses_abc","part":{"type":"text","text":"done"}}\n'
        )
        session_id, text = watcher._parse_run_stdout(stdout)
        self.assertEqual(session_id, "ses_abc")
        self.assertEqual(text, "done")

    def test_ignores_non_json_and_missing_session(self):
        stdout = 'noise\n{"type":"text","part":{"text":"x"}}\n'
        session_id, text = watcher._parse_run_stdout(stdout)
        self.assertIsNone(session_id)
        self.assertEqual(text, "x")


class RunsFileTest(unittest.TestCase):
    def test_default_path_under_workdir(self):
        self.assertEqual(
            runs.runs_file({"workdir": "/ws"}), "/ws/.agent-watcher/runs.jsonl"
        )

    def test_override(self):
        self.assertEqual(
            runs.runs_file({"workdir": "/ws", "runs_file": "/tmp/runs.jsonl"}),
            "/tmp/runs.jsonl",
        )


class AppendRunTest(unittest.TestCase):
    def test_writes_json_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "nested", "runs.jsonl")
            runs.append_run(path, {"a": 1})
            runs.append_run(path, {"b": 2})
            with open(path) as handle:
                lines = handle.read().splitlines()
            self.assertEqual([json.loads(line) for line in lines], [{"a": 1}, {"b": 2}])


class RunAgentTest(unittest.TestCase):
    def config(self):
        return {
            "name": "Vivy",
            "role": "product-owner",
            "model": "m/1",
            "fallback_model": "m/2",
            "workdir": "/ws",
        }

    @staticmethod
    def fake_run(returncode, stdout, stderr=b""):
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_ok_captures_session_and_timing(self):
        stdout = b'{"type":"step_start","sessionID":"ses_1"}\n'
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with mock.patch("watcher.subprocess.run",
                            return_value=self.fake_run(0, stdout)):
                record = watcher.run_agent(self.config(), "m/1", "prompt", number=7)
        self.assertEqual(record.outcome, "ok")
        self.assertEqual(record.session_id, "ses_1")
        self.assertEqual(record.error, "")
        self.assertGreaterEqual(record.ended_at, record.started_at)

    def test_rate_limited(self):
        stderr = b"429 too many requests"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with mock.patch("watcher.subprocess.run",
                            return_value=self.fake_run(1, b"", stderr)):
                record = watcher.run_agent(self.config(), "m/1", "prompt")
        self.assertEqual(record.outcome, "rate_limited")
        self.assertIn("429", record.error)

    def test_plain_error(self):
        stderr = b"model not found"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            with mock.patch("watcher.subprocess.run",
                            return_value=self.fake_run(1, b"", stderr)):
                record = watcher.run_agent(self.config(), "m/1", "prompt")
        self.assertEqual(record.outcome, "error")
        self.assertEqual(record.session_id, None)


class RecordRunTest(unittest.TestCase):
    def test_ledger_row(self):
        config = {
            "name": "Vivy",
            "role": "product-owner",
            "model": "m/1",
            "fallback_model": "m/2",
            "workdir": "/ws",
        }
        record = runs.RunRecord(
            session_id="ses_x", outcome="ok", error="", started_at=1.0, ended_at=2.5
        )
        with mock.patch("runs.append_run") as append, \
                mock.patch("runs.runs_file", return_value="/tmp/x"), \
                mock.patch("runs.new_run_id", return_value="rid"):
            watcher.record_run(config, record, 7, "intent", False)
        row = append.call_args[0][1]
        self.assertEqual(row["run_id"], "rid")
        self.assertEqual(row["session_id"], "ses_x")
        self.assertEqual(row["agent"], "Vivy")
        self.assertEqual(row["role"], "product-owner")
        self.assertEqual(row["issue_id"], 7)
        self.assertEqual(row["from_column"], "intent")
        self.assertEqual(row["model_configured"], "m/1")
        self.assertEqual(row["model_used"], "m/1")
        self.assertFalse(row["fallback_used"])
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(row["duration_s"], 1.5)
        self.assertEqual(row["workdir"], "/ws")


class ProcessTest(unittest.TestCase):
    def config(self):
        return {
            "name": "Vivy",
            "role": "product-owner",
            "model": "m/1",
            "fallback_model": "m/2",
            "workdir": "/ws",
            "roles": [],
        }

    @staticmethod
    def record(outcome, error="", session_id="ses_x"):
        return runs.RunRecord(
            session_id=session_id, outcome=outcome, error=error,
            started_at=1.0, ended_at=2.0,
        )

    def test_records_after_run(self):
        with mock.patch("watcher.kanban") as kanban, \
                mock.patch("watcher.build_prompt", return_value="p"), \
                mock.patch("watcher.run_agent", return_value=self.record("ok")) as run_agent, \
                mock.patch("watcher.record_run") as record_run:
            result = watcher.process(self.config(), "Vivy", 5, title="t", status="intent")
        self.assertEqual(result, "ok")
        run_agent.assert_called_once()
        kanban.set_field.assert_called_once()
        kanban.clear_field.assert_called_once()
        record_run.assert_called_once()
        self.assertEqual(record_run.call_args[0][2], 5)
        self.assertEqual(record_run.call_args[0][3], "intent")
        self.assertFalse(record_run.call_args[0][4])

    def test_fallback_when_rate_limited(self):
        primary = self.record("rate_limited", error="429", session_id="ses_a")
        fallback = self.record("ok", session_id="ses_b")
        with mock.patch("watcher.kanban"), \
                mock.patch("watcher.build_prompt", return_value="p"), \
                mock.patch("watcher.run_agent", side_effect=[primary, fallback]), \
                mock.patch("watcher.record_run") as record_run:
            result = watcher.process(self.config(), "Vivy", 5, status="intent")
        self.assertEqual(result, "ok")
        self.assertTrue(record_run.call_args[0][4])


if __name__ == "__main__":
    unittest.main()
