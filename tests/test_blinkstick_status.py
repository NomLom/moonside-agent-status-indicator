from __future__ import annotations

import importlib.util
import random
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

    def test_pulse_levels_stay_in_band(self) -> None:
        engine = blinkstick_status.PulseEngine(8, rng=random.Random(42))
        for step in range(3000):                     # ~100 s at 30 fps
            for colour in engine.frame(step / 30.0):
                self.assertGreaterEqual(colour[2], blinkstick_status.CHASE_BASE)
                self.assertLessEqual(colour[2], blinkstick_status.CHASE_MAX)

    def test_pulse_keeps_two_to_three_active(self) -> None:
        engine = blinkstick_status.PulseEngine(8, rng=random.Random(7))
        counts = []
        for step in range(1, 3000):
            engine.frame(step / 30.0)
            counts.append(len(engine.pulses))
        self.assertTrue(all(2 <= c <= blinkstick_status.PULSE_MAX_ACTIVE for c in counts))
        self.assertGreater(sum(counts) / len(counts), 2.0)   # third LED visits

    def test_pulse_fades_in_holds_and_fades_out(self) -> None:
        engine = blinkstick_status.PulseEngine(8, rng=random.Random(1))
        pulse = blinkstick_status.Pulse(3, 0.0, 0.18, 0.5, 0.78, blinkstick_status.CHASE_MAX)
        engine.pulses = {3: pulse}
        head = engine.frame(0.0)[3][2]
        peak = engine.frame(0.18)[3][2]
        hold = engine.frame(0.4)[3][2]
        mid_fade = engine.frame(0.6)[3][2]
        self.assertLess(head, blinkstick_status.CHASE_MAX // 3)
        self.assertEqual(peak, blinkstick_status.CHASE_MAX)
        self.assertEqual(hold, blinkstick_status.CHASE_MAX)
        self.assertLess(mid_fade, peak)
        self.assertGreater(mid_fade, blinkstick_status.CHASE_BASE)

    def test_dead_pulse_cools_down_before_relighting(self) -> None:
        engine = blinkstick_status.PulseEngine(8, rng=random.Random(3))
        pulse = blinkstick_status.Pulse(3, 0.0, 0.1, 0.2, 0.3, 100.0)
        engine.pulses = {3: pulse}
        engine.frame(0.35)                                     # retires LED 3
        self.assertNotIn(3, engine.pulses)
        self.assertIn(3, engine.cooldowns)
        rng = random.Random(9)
        for _ in range(50):                                    # next ~0.2 s of frames
            engine.frame(0.35 + rng.uniform(0.0, 0.2))
            if 3 in engine.pulses:
                self.fail("LED 3 relit inside its cooldown window")


if __name__ == "__main__":
    unittest.main()
