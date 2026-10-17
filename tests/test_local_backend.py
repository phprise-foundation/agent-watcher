"""Unit tests for the local (self-hosted board) backend."""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from backends import KanbanError, github, local  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LocalBackendTest(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {
            "AW_KANBAN_URL": "http://board",
            "AW_KANBAN_TOKEN": "secret",
            "AW_AGENT_NAME": "Davi",
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)
        self.requests = []

    def respond(self, *payloads):
        def side_effect(request, *args, **kwargs):
            self.requests.append(request)
            return FakeResponse(payloads[len(self.requests) - 1])

        patcher = mock.patch("urllib.request.urlopen", side_effect=side_effect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fail_with(self, error):
        patcher = mock.patch("urllib.request.urlopen", side_effect=error)
        patcher.start()
        self.addCleanup(patcher.stop)

    def body(self, request):
        return json.loads(request.data.decode()) if request.data else None

    def header(self, request, name):
        return {k.lower(): v for k, v in request.header_items()}.get(name.lower())

    def test_items_maps_board_shape(self):
        self.respond({"items": [{
            "id": 7,
            "title": "RFC-1 board",
            "status": "coding",
            "role": "coder",
            "worker": "Davi",
            "blocked": True,
            "blocked_by": "RFC-2",
            "comments": [{"author": "user", "body": "hi", "created_at": "2024-01-01T00:00:00+00:00"}],
        }]})

        rows = local.items()

        self.assertEqual(rows[0]["number"], 7)
        self.assertEqual(rows[0]["url"], "")
        self.assertEqual(rows[0]["status"], "coding")
        self.assertTrue(rows[0]["blocked"])
        self.assertEqual(rows[0]["blocked_by"], "RFC-2")
        self.assertEqual(rows[0]["comments"][0], {
            "body": "hi", "author": "user", "created_at": "2024-01-01T00:00:00+00:00",
        })

    def test_set_field_status_patches_item(self):
        self.respond({"item": {}})

        local.set_field(5, "Status", "coding")

        request = self.requests[0]
        self.assertEqual(request.get_method(), "PATCH")
        self.assertEqual(request.full_url, "http://board/api/items/5")
        self.assertEqual(self.body(request), {"status": "coding"})
        self.assertEqual(self.header(request, "Authorization"), "Bearer secret")

    def test_set_field_blocked_is_boolean(self):
        self.respond({"item": {}})

        local.set_field(5, "Blocked", "blocked")

        self.assertEqual(self.body(self.requests[0]), {"blocked": True})

    def test_set_field_skips_empty_value(self):
        local.set_field(5, "Worker", "")

        self.assertEqual(self.requests, [])

    def test_clear_field_worker(self):
        self.respond({"item": {}})

        local.clear_field(5, "Worker")

        self.assertEqual(self.body(self.requests[0]), {"worker": ""})

    def test_comment_signs_agent_name(self):
        self.respond({"comment": {}})

        local.comment(5, "working on it")

        request = self.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "http://board/api/items/5/comments")
        self.assertEqual(self.body(request), {"author": "Davi", "body": "working on it"})

    def test_close_posts_comment(self):
        self.respond({"item": {}})

        local.close(5, "done", "completed")

        request = self.requests[0]
        self.assertEqual(request.full_url, "http://board/api/items/5/close")
        self.assertEqual(self.body(request), {"comment": "done"})

    def test_create_returns_id_and_blocks(self):
        self.respond({"item": {"id": 9}}, {"item": {}}, {"item": {}})

        number = local.create("RFC-2 x", "body", "intent", "coder", "RFC-1")

        self.assertEqual(number, 9)
        self.assertEqual(self.body(self.requests[0]), {
            "title": "RFC-2 x", "body": "body", "status": "intent", "role": "coder",
        })
        self.assertEqual(self.requests[0].full_url, "http://board/api/items")
        self.assertEqual(self.body(self.requests[1]), {"blocked": True})
        self.assertEqual(self.body(self.requests[2]), {"blocked_by": "RFC-1"})

    def test_workflow_maps_columns(self):
        self.respond({"board": {
            "name": "Multi-Agent",
            "columns": [
                {"id": "intent", "name": "Intent", "position": 0, "final": False,
                 "role": "product-owner", "transitions": ["feasibility"]},
                {"id": "done", "name": "Done", "position": 1, "final": True,
                 "role": None, "transitions": []},
            ],
        }})

        data = local.workflow()

        self.assertEqual(data["name"], "Multi-Agent")
        self.assertEqual(data["columns"][0]["role"], "product-owner")
        self.assertEqual(data["columns"][0]["transitions"], ["feasibility"])
        self.assertEqual(data["columns"][1]["role"], "")
        self.assertTrue(data["columns"][1]["final"])
        self.assertEqual(self.requests[0].full_url, "http://board/api/board")

    def test_advance_posts_target(self):
        self.respond({"item": {}})

        local.advance(5, "feasibility")

        request = self.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, "http://board/api/items/5/advance")
        self.assertEqual(self.body(request), {"to": "feasibility"})

    def test_advance_without_target_sends_empty_body(self):
        self.respond({"item": {}})

        local.advance(5)

        self.assertEqual(self.body(self.requests[0]), {})

    def test_agents_maps_spec(self):
        self.respond({"agents": [{
            "id": "coder", "name": "Coder", "active": True,
            "profile": "skeptic", "model": "deepseek/deepseek-flash",
            "fallback_model": "", "interval": 120, "workspace": "/workspace",
        }]})

        rows = local.agents()

        self.assertEqual(rows[0]["id"], "coder")
        self.assertEqual(rows[0]["interval"], 120)
        self.assertEqual(rows[0]["profile"], "skeptic")

    def test_missing_url_raises(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(KanbanError):
                local.items()

    def test_http_error_raises_kanban_error(self):
        error = urllib.error.HTTPError(
            "http://board/api/items", 401, "Unauthorized", {},
            io.BytesIO(b'{"error":"unauthorized"}'),
        )
        self.fail_with(error)

        with self.assertRaises(KanbanError) as ctx:
            local.items()

        self.assertIn("401", str(ctx.exception))


class GithubParseTest(unittest.TestCase):
    def test_parse_board_maps_fields(self):
        data = {"data": {"organization": {"projectV2": {"items": {"nodes": [{
            "fieldValues": {"nodes": [
                {"name": "coding", "field": {"name": "Status"}},
                {"name": "coder", "field": {"name": "Role"}},
                {"text": "Davi", "field": {"name": "Worker"}},
                {"name": "blocked", "field": {"name": "Blocked"}},
                {"text": "RFC-1", "field": {"name": "BlockedBy"}},
            ]},
            "content": {
                "number": 3,
                "title": "RFC-1 x",
                "url": "https://github.com/o/r/issues/3",
                "comments": {"nodes": [
                    {"body": "hi", "createdAt": "2024-01-01T00:00:00Z",
                     "author": {"login": "user"}},
                ]},
            },
        }]}}}}}

        rows = github._parse_board(data)

        self.assertEqual(rows[0]["number"], 3)
        self.assertEqual(rows[0]["status"], "coding")
        self.assertEqual(rows[0]["role"], "coder")
        self.assertEqual(rows[0]["worker"], "Davi")
        self.assertTrue(rows[0]["blocked"])
        self.assertEqual(rows[0]["blocked_by"], "RFC-1")
        self.assertEqual(rows[0]["comments"][0]["author"], "user")


if __name__ == "__main__":
    unittest.main()
