from __future__ import annotations

import importlib.util
import os
import random
import tempfile
import time
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
                         [22, 22, 69, 165, 69, 22, 22, 22])  # ramp, never hard off
        self.assertTrue(all(c[0] == 0 and c[1] == 0 for c in frame))

    def test_walk_frame_wraps_around_the_strip(self) -> None:
        frame = blinkstick_status.walk_colours(0, 8)
        self.assertEqual(frame[0][2], 165)
        self.assertEqual(frame[7][2], 69)   # partner ramps in across the seam
        self.assertEqual(frame[1][2], 69)

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

    def test_stabiliser_ignores_short_blips(self) -> None:
        stab = blinkstick_status.StateStabiliser(0.3)
        self.assertEqual(stab.update("thinking", 1.0), "idle")     # blip starts
        self.assertEqual(stab.update("thinking", 1.15), "idle")    # < 0.3 s
        self.assertEqual(stab.update("idle", 1.25), "idle")        # blip ends
        self.assertEqual(stab.update("idle", 2.0), "idle")         # never promoted

    def test_stabiliser_accepts_sustained_state(self) -> None:
        stab = blinkstick_status.StateStabiliser(0.3)
        stab.update("thinking", 1.0)
        self.assertEqual(stab.update("thinking", 1.35), "thinking")

    def test_stabiliser_holds_animation_through_idle_flicker(self) -> None:
        stab = blinkstick_status.StateStabiliser(0.3)
        stab.update("thinking", 1.0)
        stab.update("thinking", 1.4)                               # stable now
        self.assertEqual(stab.update("idle", 2.0), "thinking")     # rewrite blip
        self.assertEqual(stab.update("thinking", 2.1), "thinking") # recovered

    # ---- multi-agent layer ----

    def test_active_sessions_ignores_stale_dead_writers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alive").write_text("thinking", encoding="utf-8")
            (root / "crashed").write_text("thinking", encoding="utf-8")
            os.utime(root / "crashed", (time.time() - 7200, time.time() - 7200))
            (root / "napping").write_text("idle", encoding="utf-8")
            os.utime(root / "napping", (time.time() - 7200, time.time() - 7200))
            names = dict(blinkstick_status.active_sessions(root, max_state_age=1800))
            self.assertEqual(names, {"alive": "thinking"})
            self.assertEqual(blinkstick_status.effective_state(root), "thinking")

    def test_led_assignment_is_stable_and_in_range(self) -> None:
        first = blinkstick_status.led_for("session-42", 8)
        self.assertEqual(first, blinkstick_status.led_for("session-42", 8))
        self.assertTrue(0 <= first < 8)
        self.assertTrue(0 <= blinkstick_status.led_for("x", 1) < 1)

    def test_permission_wins_collision_with_thinking(self) -> None:
        original = blinkstick_status.led_for
        setattr(blinkstick_status, "led_for", lambda name, count: 0)  # force collision
        try:
            frame = blinkstick_status.paint_sessions(
                [("thinker", "thinking"), ("blocked", "permission")],
                8, {}, now=100.0, permission_phase=False)
            self.assertEqual(frame[0], blinkstick_status.COLOURS["permission"])
            frame_dark = blinkstick_status.paint_sessions(
                [("thinker", "thinking"), ("blocked", "permission")],
                8, {}, now=100.0, permission_phase=True)
            self.assertEqual(frame_dark[0], (120, 0, 117))         # pulse half
        finally:
            setattr(blinkstick_status, "led_for", original)

    def test_non_colliding_agents_get_own_leds(self) -> None:
        frame = blinkstick_status.paint_sessions(
            [("blocked", "permission")], 8, {}, now=100.0, permission_phase=False)
        purple_leds = [i for i, c in enumerate(frame) if c == blinkstick_status.COLOURS["permission"]]
        self.assertEqual(len(purple_leds), 1)
        self.assertTrue(all(c == (0, 0, blinkstick_status.CHASE_BASE)
                            for i, c in enumerate(frame) if i != purple_leds[0]))

    def test_cancelled_session_paints_nothing(self) -> None:
        frame = blinkstick_status.paint_sessions(
            [("done", "cancelled")], 8, {}, now=100.0, permission_phase=False)
        self.assertTrue(all(c == (0, 0, blinkstick_status.CHASE_BASE) for c in frame))

    def test_working_agent_blue_wanders_the_strip(self) -> None:
        engines: dict = {}
        seen = set()
        for step in range(1200):                      # 40 s of frames
            frame = blinkstick_status.paint_sessions(
                [("worker", "thinking")], 8, engines, now=step / 30.0, permission_phase=False)
            seen.update(i for i, c in enumerate(frame) if c[2] > blinkstick_status.CHASE_BASE + 50)
        self.assertGreaterEqual(len(seen), 5)         # genuinely roams, not parked

    def test_working_agent_keeps_about_one_or_two_alive(self) -> None:
        engines: dict = {}
        counts = []
        for step in range(1, 1200):
            frame = blinkstick_status.paint_sessions(
                [("worker", "thinking")], 8, engines, now=step / 30.0, permission_phase=False)
            counts.append(sum(1 for c in frame if c[2] > blinkstick_status.CHASE_BASE + 50))
        self.assertTrue(all(c <= 3 for c in counts))
        self.assertGreater(sum(counts) / len(counts), 1.15)  # ~2 alive with fades/glow
        # Handover frames (old breath gone, new one still rising) are rare:
        self.assertLess(counts.count(0) / len(counts), 0.15)


if __name__ == "__main__":
    unittest.main()
