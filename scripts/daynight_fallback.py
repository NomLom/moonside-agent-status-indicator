"""Offline fallback: regenerate daynight.json with astral when Open-Meteo is unreachable.

Primary path is daynight_fetch.py (follows published sunrise/sunset — no local
astronomy). This script exists so the lamp is never blind after a multi-day
network outage or a fresh reproducible install with no internet. Run manually:

    .venv/bin/python scripts/daynight_fallback.py

Uses astral's geometric sunrise/sunset so both sources mark the same moment;
the fallback never shifts the boundary beyond normal day-to-day drift.
"""
import pathlib
import sys
from datetime import date, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from astral import LocationInfo
from astral.sun import sun
from daynight_fetch import write_verified

CAMBRIDGE = LocationInfo("Cambridge", "UK", "Europe/London", 52.205, 0.121)


def build_payload(day: date | None = None) -> dict:
    day = day or date.today()
    sun_times = sun(CAMBRIDGE.observer, date=day, tzinfo=CAMBRIDGE.timezone)
    fmt = lambda key: sun_times[key].strftime("%H:%M")
    return {"date": day.isoformat(), "timezone": "Europe/London",
            "sunrise": fmt("sunrise"), "sunset": fmt("sunset")}


if __name__ == "__main__":
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
        pathlib.Path(__file__).resolve().parents[1] / "daynight.json")
    write_verified(target, build_payload())
    print("astral fallback wrote", target)
