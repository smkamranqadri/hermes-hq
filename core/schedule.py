"""Recurrence engine for task schedules (Group 7-1).

A schedule stores a five-field cron expression plus an IANA zone (owner default
Asia/Karachi); firing decisions happen in the dispatcher tick. croniter does the
math in the schedule's zone, timestamps are stored as epoch seconds (UTC).
Presets exist so the UI can offer human choices that compile to cron.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

DEFAULT_ZONE = "Asia/Karachi"
DOW = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DOW_NUM = {d: i for i, d in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}


def preset_to_cron(kind: str, *, at: str = "09:00", dow: str = "mon", day: int = 1, every_hours: int = 6) -> str:
    """Compile a UI preset to a cron expression. `at` is HH:MM in the schedule's zone."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", at or "")
    if not m or not (0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59):
        raise ValueError("time must be HH:MM")
    hh, mm = int(m.group(1)), int(m.group(2))
    if kind == "daily":
        return f"{mm} {hh} * * *"
    if kind == "weekdays":
        return f"{mm} {hh} * * 1-5"
    if kind == "weekly":
        if dow not in DOW_NUM:
            raise ValueError("dow must be one of %s" % (DOW,))
        return f"{mm} {hh} * * {DOW_NUM[dow]}"
    if kind == "monthly":
        if not 1 <= int(day) <= 28:
            raise ValueError("day must be 1..28 (every month has them)")
        return f"{mm} {hh} {int(day)} * *"
    if kind == "hours":
        if not 1 <= int(every_hours) <= 24:
            raise ValueError("every_hours must be 1..24")
        return f"{mm} */{int(every_hours)} * * *"
    raise ValueError("unknown preset %r" % kind)


def validate(expr: str, zone: str = DEFAULT_ZONE) -> None:
    if not croniter.is_valid(expr or ""):
        raise ValueError("not a valid cron expression: %r" % (expr,))
    ZoneInfo(zone)          # raises on unknown zones


def next_fires(expr: str, zone: str = DEFAULT_ZONE, n: int = 3, after: float | None = None) -> list[float]:
    """The next n fire times as epoch seconds, strictly after `after` (default: now)."""
    validate(expr, zone)
    import time as _t
    base = datetime.fromtimestamp(after if after is not None else _t.time(), ZoneInfo(zone))
    it = croniter(expr, base)
    return [it.get_next(datetime).timestamp() for _ in range(n)]


def describe(expr: str) -> str:
    """Small human description for the common shapes; falls back to the raw expression."""
    m = re.fullmatch(r"(\d+) (\d+) \* \* \*", expr or "")
    if m:
        return "every day at %02d:%02d" % (int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d+) (\d+) \* \* 1-5", expr or "")
    if m:
        return "weekdays at %02d:%02d" % (int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d+) (\d+) \* \* ([0-6])", expr or "")
    if m:
        return "every %s at %02d:%02d" % (("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")[int(m.group(3))], int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d+) (\d+) (\d+) \* \*", expr or "")
    if m:
        return "monthly on day %d at %02d:%02d" % (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.fullmatch(r"(\d+) \*/(\d+) \* \* \*", expr or "")
    if m:
        return "every %s hours at :%02d" % (m.group(2), int(m.group(1)))
    return expr


def expand_tokens(text: str, zone: str = DEFAULT_ZONE, when: float | None = None) -> str:
    """{date} → YYYY-MM-DD, {week} → ISO YYYY-Www, {month} → YYYY-MM, in the schedule's zone."""
    if not text:
        return text
    import time as _t
    d = datetime.fromtimestamp(when if when is not None else _t.time(), ZoneInfo(zone))
    iso = d.isocalendar()
    return (text.replace("{date}", d.strftime("%Y-%m-%d"))
                .replace("{week}", "%d-W%02d" % (iso.year, iso.week))
                .replace("{month}", d.strftime("%Y-%m")))
