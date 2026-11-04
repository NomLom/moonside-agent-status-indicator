from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "blinkstick_status.py"
SPEC = importlib.util.spec_from_file_location("blinkstick_status", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
blinkstick_status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(blinkstick_status)


class BlinkStickStatusTests(unittest.TestCase):
    def test_effective_state_defaults_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(blinkstick_status.effective_state(Path(directory)), "idle")

    def test_effective_state_prefers_permission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "thinking-session").write_text("thinking", encoding="utf-8")
            (root / "permission-session").write_text("permission", encoding="utf-8")
            self.assertEqual(blinkstick_status.effective_state(root), "permission")

    def test_effective_state_ignores_hidden_and_invalid_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".hidden").write_text("permission", encoding="utf-8")
            (root / "invalid").write_text("not-a-state", encoding="utf-8")
            self.assertEqual(blinkstick_status.effective_state(root), "idle")

    def test_tool_use_uses_thinking_blue(self) -> None:
        self.assertEqual(blinkstick_status.colour_for("tool_use"), (0, 0, 255))

    def test_permission_pulse_uses_two_purple_shades(self) -> None:
        self.assertEqual(blinkstick_status.colour_for("permission"), (214, 0, 255))
        self.assertEqual(blinkstick_status.colour_for("permission", permission_phase=True), (120, 0, 117))

    def test_idle_is_dim_amber(self) -> None:
        self.assertEqual(blinkstick_status.colour_for("idle"), (120, 55, 0))

    def test_terminal_states_have_explicit_colours(self) -> None:
        self.assertEqual(blinkstick_status.colour_for("success"), (0, 100, 30))
        self.assertEqual(blinkstick_status.colour_for("failed"), (130, 0, 0))
        self.assertEqual(blinkstick_status.colour_for("cancelled"), (0, 0, 0))

    def test_active_work_beats_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "completed").write_text("success", encoding="utf-8")
            (root / "working").write_text("thinking", encoding="utf-8")
            self.assertEqual(blinkstick_status.effective_state(root), "thinking")

    def test_walk_frame_is_smooth_symmetric_blob(self) -> None:
        frame = blinkstick_status.walk_colours(3, 8)
        self.assertEqual(len(frame), 8)
        self.assertEqual([c[2] for c in frame],
                         [10, 10, 61, 165, 61, 10, 10, 10])  # ramp, never hard off
        self.assertTrue(all(c[0] == 0 and c[1] == 0 for c in frame))

    def test_walk_frame_wraps_around_the_strip(self) -> None:
        frame = blinkstick_status.walk_colours(0, 8)
        self.assertEqual(frame[0][2], 165)
        self.assertEqual(frame[7][2], 61)   # partner ramps in across the seam
        self.assertEqual(frame[1][2], 61)

    def test_walk_never_exceeds_peak_or_drops_below_base(self) -> None:
        for step in range(160):             # fractional positions across a full lap
            for colour in blinkstick_status.walk_colours(step / 20.0, 8):
                self.assertLessEqual(colour[2], blinkstick_status.CHASE_MAX)
                self.assertGreaterEqual(colour[2], blinkstick_status.CHASE_BASE)

    def test_thinking_and_tool_use_are_animated(self) -> None:
        self.assertEqual(blinkstick_status.ANIMATED_STATES, {"thinking", "tool_use"})
        self.assertNotIn("permission", blinkstick_status.ANIMATED_STATES)


if __name__ == "__main__":
    unittest.main()
