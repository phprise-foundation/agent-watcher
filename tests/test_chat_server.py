"""Unit tests for the chat-server prompt building and opencode delegation."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import chat_server  # noqa: E402


class ChatServerTest(unittest.TestCase):
    def test_build_prompt_includes_conversation_and_board(self):
        with mock.patch.object(chat_server, "board_snapshot", return_value=[{"number": 1, "title": "RFC-1"}]):
            prompt = chat_server.build_prompt([{"role": "user", "body": "how many items?"}])

        self.assertIn("how many items?", prompt)
        self.assertIn("RFC-1", prompt)
        self.assertIn("user:", prompt)
        self.assertIn("READ-ONLY", prompt)

    def test_reply_to_delegates_to_opencode(self):
        with mock.patch.object(chat_server, "board_snapshot", return_value=[]):
            with mock.patch.object(chat_server, "run_opencode", return_value="olá") as run:
                reply = chat_server.reply_to({"messages": [{"role": "user", "body": "oi"}]})

        self.assertEqual(reply, "olá")
        run.assert_called_once()

    def test_reply_to_handles_missing_messages(self):
        with mock.patch.object(chat_server, "board_snapshot", return_value=[]):
            with mock.patch.object(chat_server, "run_opencode", return_value="ok"):
                self.assertEqual(chat_server.reply_to({}), "ok")


if __name__ == "__main__":
    unittest.main()
