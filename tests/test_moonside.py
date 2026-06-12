from __future__ import annotations

import unittest

from agent_status.moonside import classify_state


class MoonsideStateTests(unittest.TestCase):
    def test_permission_becomes_input(self) -> None:
        self.assertEqual(classify_state(["permission", None, None, None]), "input")

    def test_working_beats_success(self) -> None:
        self.assertEqual(classify_state(["thinking", "success", None, None]), "working")

    def test_failed_beats_idle(self) -> None:
        self.assertEqual(classify_state(["idle", "failed", None, None]), "failed")


if __name__ == '__main__':
    unittest.main()
