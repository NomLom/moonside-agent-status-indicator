"""Day/night decision from a daily-refreshed sunrise/sunset file.

The file is the single source of truth (fetched by daynight_fetch.py, scheduled
daily). No local astronomy: we follow published sunrise/sunset for the site.
Missing or unreadable file defaults to day so approved day palettes keep working;
a stale file is still used — yesterday's times are minutes from today's.
"""
import json
import pathlib
from datetime import datetime, time

DEFAULT_PATH = pathlib.Path(__file__).resolve().parent / "daynight.json"


def _load(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


def is_day(path=None, now=None):
    data = _load(path or DEFAULT_PATH)
    if not data:
        return True
    try:
        tzinfo = ZoneInfoCache.get(data["timezone"])
        sunrise = datetime.strptime(data["sunrise"], "%H:%M").time()
        sunset = datetime.strptime(data["sunset"], "%H:%M").time()
    except (KeyError, TypeError, ValueError):
        return True
    if now is None:
        now = datetime.now(tzinfo)
    current = now.timetz().replace(tzinfo=None)
    return sunrise <= current <= sunset


class ZoneInfoCache:
    _cache = {}

    @classmethod
    def get(cls, name):
        from zoneinfo import ZoneInfo
        if name not in cls._cache:
            cls._cache[name] = ZoneInfo(name)
        return cls._cache[name]
