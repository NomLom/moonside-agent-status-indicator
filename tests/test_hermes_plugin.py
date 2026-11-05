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
        hp._LAST_EVENT.clear()
        hp._LAST_WRITTEN.clear()

    def tearDown(self) -> None:
        os.environ.pop("AGENT_STATUS_DIR", None)
        os.environ.pop("BK_LIGHT_STATUS_DIR", None)
        hp._SESSION_STATE.clear()
        hp._THREAD_TO_SESSION.clear()
        hp._TASK_TO_SESSION.clear()
        hp._LAST_EVENT.clear()
        hp._LAST_WRITTEN.clear()

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


class WatchdogTests(unittest.TestCase):
    """Interrupted turns fire no closing hook — the sweep must expire them."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["AGENT_STATUS_DIR"] = self.tmp.name
        for d in (hp._SESSION_STATE, hp._THREAD_TO_SESSION, hp._TASK_TO_SESSION,
                  hp._LAST_EVENT, hp._LAST_WRITTEN):
            d.clear()

    def tearDown(self) -> None:
        os.environ.pop("AGENT_STATUS_DIR", None)
        for d in (hp._SESSION_STATE, hp._THREAD_TO_SESSION, hp._TASK_TO_SESSION,
                  hp._LAST_EVENT, hp._LAST_WRITTEN):
            d.clear()

    def _read(self, name: str) -> str:
        return (Path(self.tmp.name) / name).read_text(encoding="utf-8")

    def _make_stale(self, safe: str, seconds: float) -> None:
        hp._LAST_EVENT[safe] = 1000.0 - seconds

    def test_thinking_expires_after_quiet(self) -> None:
        hp._on_session_start("z1")
        hp._on_pre_llm_call("z1")
        self.assertEqual(self._read("z1"), "thinking")
        self._make_stale("z1", hp.STUCK_TURN_SECONDS + 1)
        self.assertEqual(hp.sweep_stuck_sessions(now=1000.0), ["z1"])
        self.assertEqual(self._read("z1"), "idle")

    def test_recent_activity_is_not_expired(self) -> None:
        hp._on_session_start("z2")
        hp._on_pre_llm_call("z2")
        self._make_stale("z2", hp.STUCK_TURN_SECONDS - 10)
        self.assertEqual(hp.sweep_stuck_sessions(now=1000.0), [])
        self.assertEqual(self._read("z2"), "thinking")

    def test_permission_is_never_auto_expired(self) -> None:
        hp._on_session_start("z3")
        hp._on_pre_approval_request("z3")
        self.assertEqual(self._read("z3"), "permission")
        self._make_stale("z3", hp.STUCK_TOOL_SECONDS + 100)
        self.assertEqual(hp.sweep_stuck_sessions(now=1000.0), [])
        self.assertEqual(self._read("z3"), "permission")

    def test_in_tool_gets_longer_grace(self) -> None:
        hp._on_session_start("z4")
        hp._on_pre_tool_call(task_id="", session_id="z4")
        self.assertEqual(self._read("z4"), "tool_use")
        # Quiet past the turn limit but still inside the tool grace: alive.
        self._make_stale("z4", hp.STUCK_TURN_SECONDS + 1)
        self.assertEqual(hp.sweep_stuck_sessions(now=1000.0), [])
        self.assertEqual(self._read("z4"), "tool_use")
        # Past the tool grace: reset and depth cleared.
        self._make_stale("z4", hp.STUCK_TOOL_SECONDS + 1)
        self.assertEqual(hp.sweep_stuck_sessions(now=1000.0), ["z4"])
        self.assertEqual(self._read("z4"), "idle")
        self.assertEqual(hp._SESSION_STATE["z4"].tool_depth, 0)

    def test_idle_is_left_alone(self) -> None:
        hp._on_session_start("z5")
        hp._on_post_llm_call("z5")
        self.assertEqual(self._read("z5"), "idle")
        self._make_stale("z5", hp.STUCK_TOOL_SECONDS + 100)
        self.assertEqual(hp.sweep_stuck_sessions(now=1000.0), [])
        self.assertEqual(self._read("z5"), "idle")


if __name__ == "__main__":
    unittest.main()

