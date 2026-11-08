"""Fetch today's sunrise/sunset for the site and write daynight.json atomically.

Source: Open-Meteo (keyless). Validates before writing; on any failure the
existing file is left untouched (the lamp then follows yesterday's times).
Run daily via Hermes cron monitor or systemd timer.
"""
import json
import pathlib
import re
import sys
import urllib.request

LATITUDE = 52.205   # Cambridge, UK
LONGITUDE = 0.121
TIMEZONE = "Europe/London"
URL = (f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}"
       "&daily=sunrise,sunset&timezone=Europe%2FLondon&forecast_days=1")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def write_verified(path, payload):
    for key in ("date", "timezone", "sunrise", "sunset"):
        if key not in payload:
            raise ValueError(f"missing {key}")
    for key in ("sunrise", "sunset"):
        if not TIME_RE.match(str(payload[key])):
            raise ValueError(f"bad {key}: {payload[key]!r}")
    path = pathlib.Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def fetch(url=URL, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as reply:
        data = json.loads(reply.read())
    daily = data["daily"]
    return {
        "date": daily["time"][0],
        "timezone": data.get("timezone", TIMEZONE),
        "sunrise": daily["sunrise"][0].split("T")[1][:5],
        "sunset": daily["sunset"][0].split("T")[1][:5],
    }


if __name__ == "__main__":
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
        pathlib.Path(__file__).resolve().parent / "daynight.json")
    write_verified(target, fetch())
    print("updated", target)
