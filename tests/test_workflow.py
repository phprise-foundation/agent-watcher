"""Unit tests for the board-driven workflow (statuses and prompt)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import kanban  # noqa: E402
import watcher  # noqa: E402


WORKFLOW = {
    "columns": [
        {"id": "intent", "name": "Intent", "role": "product-owner",
         "transitions": ["feasibility"], "final": False},
        {"id": "qa-refinement", "name": "QA", "role": "qa",
         "transitions": ["coding"], "final": False},
        {"id": "testing", "name": "Testing", "role": "qa",
         "transitions": ["done"], "final": False},
        {"id": "done", "name": "Done", "role": "", "transitions": [], "final": True},
    ],
}


class StatusesForRoleTest(unittest.TestCase):
    def test_collects_columns_owned_by_the_role(self):
        self.assertEqual(kanban.statuses_for_role("qa", WORKFLOW), ["qa-refinement", "testing"])
        self.assertEqual(kanban.statuses_for_role("product-owner", WORKFLOW), ["intent"])
        self.assertEqual(kanban.statuses_for_role("ghost", WORKFLOW), [])


class PromptTest(unittest.TestCase):
    def config(self, **overrides):
        config = {
            "name": "Davi",
            "backend": "local",
            "workflow": WORKFLOW,
            "rules": [],
            "profile_personality": "",
        }
        config.update(overrides)
        return config

    def test_local_prompt_describes_workflow_and_advance(self):
        prompt = watcher.build_prompt(self.config(), number=3)

        self.assertIn("kanban advance", prompt)
        self.assertIn("qa-refinement (QA): role=qa -> coding", prompt)

    def test_prompt_without_workflow_keeps_static_statuses(self):
        config = self.config()
        config.pop("workflow")
        prompt = watcher.build_prompt(config, number=3)

        self.assertIn("Valid statuses", prompt)
        self.assertNotIn("kanban advance", prompt)


if __name__ == "__main__":
    unittest.main()
