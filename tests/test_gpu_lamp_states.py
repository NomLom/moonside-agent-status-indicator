"""RED tests for the five-state day/night GPU lamp engine (user-approved map)."""
import asyncio
import pathlib
import sys
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gpu_lamp as g


NIGHT = "night"
DAY = "day"


class StateMapTests(unittest.TestCase):
    def test_all_state_period_pairs_present(self):
        for state in ("unreachable", "thermal", "busy", "low", "extended_low"):
            for period in (DAY, NIGHT):
                entry = g.PALETTES[(state, period)]
                self.assertTrue(entry["command"].startswith(("THEME.", "COLOR")))
                self.assertTrue(10 <= entry["brightness"] <= 100)

    def test_approved_commands_exact(self):
        p = g.PALETTES
        self.assertEqual(p[("thermal", DAY)]["command"],
                         "THEME.LAVA1.255,60,0,180,0,0,255,0,0,")
        self.assertEqual(p[("thermal", NIGHT)]["command"],
                         "THEME.LAVA1.255,60,0,180,0,0,255,0,0,")  # day re-use until approved
        self.assertEqual(p[("busy", DAY)]["command"], "THEME.BEAT2.0,0,255,0,0,0,")
        self.assertEqual(p[("busy", NIGHT)]["command"], "THEME.BEAT2.180,10,0,0,0,0,")
        self.assertEqual(p[("low", DAY)]["command"], "THEME.TWINKLE1.0,0,255,0,60,0,")
        self.assertEqual(p[("low", NIGHT)]["command"], "THEME.TWINKLE1.180,10,0,0,60,0,")
        self.assertEqual(p[("extended_low", DAY)]["command"],
                         "THEME.GRADIENT2.8,175,212,8,175,212,255,255,255,")
        self.assertEqual(p[("extended_low", NIGHT)]["command"],
                         "THEME.FIRE2.0,0,0,255,0,0,25,0,45,255,0,0,")
        self.assertEqual(p[("busy", NIGHT)]["brightness"], 20)
        self.assertEqual(p[("extended_low", NIGHT)]["brightness"], 20)
        self.assertEqual(p[("thermal", NIGHT)]["brightness"], 20)
        self.assertEqual(p[("low", NIGHT)]["brightness"], 20)
        self.assertEqual(p[("unreachable", NIGHT)]["brightness"], 20)
        self.assertEqual(p[("unreachable", DAY)]["command"],
                         "THEME.GRADIENT3.220,0,0,20,0,90,")
        self.assertEqual(p[("unreachable", NIGHT)]["command"],
                         "THEME.GRADIENT3.220,0,0,15,0,25,")

    def test_priority_order_documented(self):
        self.assertEqual(g.STATE_PRIORITY,
                         ["unreachable", "thermal", "busy", "low", "extended_low"])


class ControllerTests(unittest.TestCase):
    def make(self, is_day=lambda path=None, now=None: True, stale_after=10.0):
        return g.Controller(is_day=is_day, stale_after=stale_after)

    def test_no_sample_starts_unreachable_not_calm(self):
        c = self.make()
        c.observe(now=0)  # heartbeat without a sample
        self.assertEqual(c.desired, ("unreachable", DAY))

    def test_stale_sample_promotes_unreachable(self):
        c = self.make()
        c.observe(util=10, temp=50, now=0)
        c.observe(util=10, temp=50, now=2)
        self.assertEqual(c.desired[0], "low")
        c.observe(now=15)  # no new reading, past 10s staleness
        c.observe(now=17)
        self.assertEqual(c.desired, ("unreachable", DAY))

    def test_stale_timer_resets_on_fresh_sample(self):
        c = self.make()
        c.observe(util=10, temp=50, now=0)
        c.observe(util=10, temp=50, now=2)
        c.observe(util=10, temp=50, now=9)
        c.observe(now=15)
        self.assertEqual(c.desired[0], "low")  # last fresh sample only 6s ago

    def test_thermal_beats_busy_and_low(self):
        c = self.make()
        for t in (0, 2):
            c.observe(util=90, temp=90, now=t)
        self.assertEqual(c.desired, ("thermal", DAY))

    def test_extended_low_needs_30_continuous_minutes(self):
        c = self.make()
        for t in (0, 2):
            c.observe(util=10, temp=50, now=t)
        self.assertEqual(c.desired[0], "low")
        for t in (1802, 1804):
            c.observe(util=10, temp=50, now=t)
        self.assertEqual(c.desired[0], "extended_low")

    def test_busy_resets_extended_low_timer(self):
        c = self.make()
        for t in (0, 2):
            c.observe(util=10, temp=50, now=t)
        for t in (100, 102):
            c.observe(util=90, temp=50, now=t)
        for t in (200, 202):
            c.observe(util=10, temp=50, now=t)
        self.assertEqual(c.desired[0], "low")
        for t in (500, 502):
            c.observe(util=10, temp=50, now=t)
        self.assertEqual(c.desired[0], "low")          # only 300s of continuous low
        for t in (2002, 2004):
            c.observe(util=10, temp=50, now=t)
        self.assertEqual(c.desired[0], "extended_low")  # 1800s from t=200

    def test_lost_telemetry_resets_extended_low_timer(self):
        c = self.make()
        for t in (0, 2):
            c.observe(util=10, temp=50, now=t)
        c.observe(util=10, temp=50, now=1000)
        c.observe(now=1030)
        c.observe(now=1032)
        self.assertEqual(c.desired[0], "unreachable")
        for t in (1040, 1042):
            c.observe(util=10, temp=50, now=t)
        self.assertEqual(c.desired[0], "low")
        c.observe(util=10, temp=50, now=1500)
        self.assertEqual(c.desired[0], "low")          # low timer restarted at 1040

    def test_day_night_uses_is_day_hook(self):
        seen = {"is_day": False}
        c = self.make(is_day=lambda path=None, now=None: seen["is_day"])
        for t in (0, 2):
            c.observe(util=90, temp=50, now=t)
        self.assertEqual(c.desired, ("busy", NIGHT))
        seen["is_day"] = True
        for t in (5, 7):
            c.observe(util=90, temp=50, now=t)
        self.assertEqual(c.desired, ("busy", DAY))


class DeliverTests(unittest.IsolatedAsyncioTestCase):
    async def test_palette_sends_brightness_then_command(self):
        c = g.Controller(is_day=lambda path=None, now=None: False)
        for t in (0, 2):
            c.observe(util=90, temp=50, now=t)
        writer = SimpleNamespace(send=AsyncMock())
        await c.deliver(writer, 3)
        state, period = c.desired
        writer.send.assert_awaited_once_with((state, period))
        self.assertEqual(c.delivered, (state, period))

    async def test_unchanged_delivered_state_stays_quiet(self):
        c = g.Controller(is_day=lambda path=None, now=None: True)
        for t in (0, 2):
            c.observe(util=10, temp=50, now=t)
        writer = SimpleNamespace(send=AsyncMock())
        await c.deliver(writer, 3)
        await c.deliver(writer, 999)
        self.assertEqual(writer.send.await_count, 1)

    async def test_ble_retry_backoff_caps_at_ten_minutes(self):
        """Phone grabbing the lamp or a radio drop must not cause hammering."""
        c = g.Controller(is_day=lambda path=None, now=None: True)
        for t in (0, 2):
            c.observe(util=10, temp=50, now=t)
        writer = SimpleNamespace(send=AsyncMock(side_effect=OSError("no link")))
        now = 3.0
        delays = []
        for _ in range(12):
            await c.deliver(writer, now)
            delays.append(c.retry_at - now)
            now = c.retry_at
        self.assertEqual(delays[:4], [2.0, 4.0, 8.0, 16.0])
        self.assertEqual(delays[-3:], [600.0, 600.0, 600.0])
        # recovery: success resets the ladder
        writer.send.side_effect = None
        await c.deliver(writer, now)
        self.assertEqual(c.failures, 0)
        self.assertIsNotNone(c.delivered)


if __name__ == "__main__":
    unittest.main()
