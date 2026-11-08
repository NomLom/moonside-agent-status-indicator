"""RED tests for day/night data: daily fetch file is the single source of truth."""
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import daynight


def write_payload(directory, **overrides):
    payload = {"date": "2026-11-08", "timezone": "Europe/London",
               "sunrise": "07:06", "sunset": "16:18"}
    payload.update(overrides)
    path = pathlib.Path(directory) / "daynight.json"
    path.write_text(json.dumps(payload))
    return path


class DayNightTests(unittest.TestCase):
    def test_day_between_sunrise_and_sunset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_payload(directory)
            self.assertTrue(daynight.is_day(path, datetime(2026, 11, 8, 12, 0, tzinfo=ZoneInfo("Europe/London"))))

    def test_night_before_sunrise_and_after_sunset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_payload(directory)
            london = ZoneInfo("Europe/London")
            self.assertFalse(daynight.is_day(path, datetime(2026, 11, 8, 5, 30, tzinfo=london)))
            self.assertFalse(daynight.is_day(path, datetime(2026, 11, 8, 19, 0, tzinfo=london)))

    def test_boundaries_are_inclusive_of_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_payload(directory)
            london = ZoneInfo("Europe/London")
            self.assertTrue(daynight.is_day(path, datetime(2026, 11, 8, 7, 6, tzinfo=london)))
            self.assertTrue(daynight.is_day(path, datetime(2026, 11, 8, 16, 18, tzinfo=london)))

    def test_missing_file_defaults_to_day(self):
        self.assertTrue(daynight.is_day(pathlib.Path("/nonexistent/daynight.json"),
                                        datetime(2026, 11, 8, 23, 0, tzinfo=ZoneInfo("Europe/London"))))

    def test_stale_file_is_still_used_not_abandoned(self):
        """Yesterday's times beat an unknown; day shifts minutes, not hours."""
        with tempfile.TemporaryDirectory() as directory:
            path = write_payload(directory, date="2026-11-07")
            london = ZoneInfo("Europe/London")
            self.assertFalse(daynight.is_day(path, datetime(2026, 11, 8, 20, 0, tzinfo=london)))

    def test_malformed_file_defaults_to_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "daynight.json"
            path.write_text("{not json")
            self.assertTrue(daynight.is_day(path, datetime(2026, 11, 8, 23, 0, tzinfo=ZoneInfo("Europe/London"))))

    def test_fetch_payload_is_validated_before_write(self):
        import daynight_fetch
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "daynight.json"
            daynight_fetch.write_verified(path, {"date": "2026-11-08", "timezone": "Europe/London",
                                                  "sunrise": "07:06", "sunset": "16:18"})
            self.assertEqual(json.loads(path.read_text())["sunset"], "16:18")
            with self.assertRaises(ValueError):
                daynight_fetch.write_verified(path, {"date": "2026-11-08", "timezone": "Europe/London",
                                                     "sunrise": "nope", "sunset": "16:18"})
            # failed validation must not clobber the previous good file
            self.assertEqual(json.loads(path.read_text())["sunset"], "16:18")


if __name__ == "__main__":
    unittest.main()
