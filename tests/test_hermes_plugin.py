from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from agent_status import hermes_plugin as hp


class HermesPluginStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["AGENT_STATUS_DIR"] = self.tmp.name
        hp._SESSION_STATE.clear()
        hp._THREAD_TO_SESSION.clear()
        hp._TASK_TO_SESSION.clear()

    def tearDown(self) -> None:
        os.environ.pop("AGENT_STATUS_DIR", None)
        os.environ.pop("BK_LIGHT_STATUS_DIR", None)
        hp._SESSION_STATE.clear()
        hp._THREAD_TO_SESSION.clear()
        hp._TASK_TO_SESSION.clear()

    def _read(self, name: str) -> str:
        return (Path(self.tmp.name) / name).read_text(encoding="utf-8")

    def test_basic_turn_transitions(self) -> None:
        hp._on_session_start("sess-1")
        self.assertEqual(self._read("sess-1"), "idle")

        hp._on_pre_llm_call("sess-1")
        self.assertEqual(self._read("sess-1"), "thinking")

        hp._on_pre_tool_call("sess-1")
        self.assertEqual(self._read("sess-1"), "tool_use")

        hp._on_post_tool_call("sess-1")
        self.assertEqual(self._read("sess-1"), "thinking")

        hp._on_post_llm_call("sess-1")
        self.assertEqual(self._read("sess-1"), "idle")

        hp._on_session_finalize("sess-1")
        self.assertFalse((Path(self.tmp.name) / "sess-1").exists())

    def test_permission_round_trip(self) -> None:
        hp._on_session_start("sess-2")
        hp._on_pre_llm_call("sess-2")
        hp._on_pre_approval_request("sess-2")
        self.assertEqual(self._read("sess-2"), "permission")

        hp._on_post_approval_response("sess-2")
        self.assertEqual(self._read("sess-2"), "thinking")

    def test_nested_tool_depth(self) -> None:
        hp._on_session_start("sess-3")
        hp._on_pre_tool_call("sess-3")
        hp._on_pre_tool_call("sess-3")
        self.assertEqual(self._read("sess-3"), "tool_use")

        hp._on_post_tool_call("sess-3")
        self.assertEqual(self._read("sess-3"), "tool_use")

        hp._on_post_tool_call("sess-3")
        self.assertEqual(self._read("sess-3"), "thinking")

    def test_session_id_wins_over_task_id(self) -> None:
        hp._on_session_start("sess-4")
        hp._on_pre_tool_call(task_id="tool-uuid", session_id="sess-4")
        self.assertEqual(self._read("sess-4"), "tool_use")
        self.assertFalse((Path(self.tmp.name) / "tool-uuid").exists())

        hp._on_post_tool_call(task_id="tool-uuid", session_id="sess-4")
        self.assertEqual(self._read("sess-4"), "thinking")

    def test_thread_fallback_maps_tool_to_current_session(self) -> None:
        hp._on_session_start("sess-5")
        hp._on_pre_llm_call("sess-5")
        hp._on_pre_tool_call(task_id="tool-uuid")
        self.assertEqual(self._read("sess-5"), "tool_use")
        self.assertFalse((Path(self.tmp.name) / "tool-uuid").exists())

        hp._on_post_tool_call(task_id="tool-uuid")
        self.assertEqual(self._read("sess-5"), "thinking")


if __name__ == "__main__":
    unittest.main()

