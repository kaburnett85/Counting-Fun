"""Time sources.

Every part of the app that needs the time takes a Clock, so tests can drive a
simulated workday in milliseconds instead of sleeping through it. The clock
also owns local-day arithmetic, which is where timezone bugs would otherwise
hide.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Clock(Protocol):
    def now(self) -> float:  # wall clock, epoch seconds
        ...

    def monotonic(self) -> float:  # steady clock, for detecting sleep
        ...

    def sleep(self, seconds: float) -> None: ...

    def tzinfo(self) -> timezone | ZoneInfo: ...


def resolve_zone(name: str = "") -> timezone | ZoneInfo:
    """Return the named zone, falling back to the system's local offset."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    local = datetime.now().astimezone().tzinfo
    if isinstance(local, timezone | ZoneInfo):
        return local
    return UTC


class SystemClock:
    def __init__(self, zone: str = ""):
        self._zone = resolve_zone(zone)

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    def tzinfo(self) -> timezone | ZoneInfo:
        return self._zone


class FakeClock:
    """A clock the tests drive by hand.

    ``advance`` moves wall and monotonic time together; ``sleep_gap`` moves only
    wall time, which is exactly what a machine that suspended looks like.
    """

    def __init__(self, start: float = 1_700_000_000.0, zone: str = "UTC"):
        self._now = float(start)
        self._mono = 1000.0
        self._zone = resolve_zone(zone)
        self.slept: list[float] = []

    def now(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def sleep(self, seconds: float) -> None:
        # A fake clock never really sleeps; it records and advances.
        self.slept.append(seconds)
        self.advance(seconds)

    def tzinfo(self) -> timezone | ZoneInfo:
        return self._zone

    def advance(self, seconds: float) -> float:
        self._now += seconds
        self._mono += seconds
        return self._now

    def suspend(self, seconds: float) -> float:
        """Simulate sleep/hibernate: wall time moves, the steady clock does not."""
        self._now += seconds
        self._mono += 0.0
        return self._now

    def set_zone(self, zone: str) -> None:
        self._zone = resolve_zone(zone)


# ---- local-day helpers ---------------------------------------------------


def local_day(ts: float, tz: timezone | ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def local_dt(ts: float, tz: timezone | ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ts, tz)


def day_bounds(day: str, tz: timezone | ZoneInfo) -> tuple[float, float]:
    """Epoch bounds [start, end) of a local calendar day.

    Computed by taking the next calendar date rather than adding 24 hours, so
    DST transitions produce 23- and 25-hour days correctly.
    """
    d = date.fromisoformat(day)
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return start.timestamp(), end.timestamp()


def next_midnight(ts: float, tz: timezone | ZoneInfo) -> float:
    """Epoch seconds of the next local midnight strictly after ``ts``."""
    _, end = day_bounds(local_day(ts, tz), tz)
    if end <= ts:
        # DST edge: recompute from the following calendar day.
        d = date.fromisoformat(local_day(ts, tz)) + timedelta(days=1)
        _, end = day_bounds(d.isoformat(), tz)
    return end


def week_start(day: str, first_day: str = "monday") -> str:
    d = date.fromisoformat(day)
    offset = d.weekday() if first_day.lower() == "monday" else (d.weekday() + 1) % 7
    return (d - timedelta(days=offset)).isoformat()


def day_range(start_day: str, end_day: str) -> list[str]:
    a, b = date.fromisoformat(start_day), date.fromisoformat(end_day)
    if b < a:
        a, b = b, a
    out, cur = [], a
    while cur <= b:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def add_days(day: str, n: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def fmt_duration(seconds: float) -> str:
    """Human duration: '3h 12m', '48m', '9s'."""
    s = int(round(max(0.0, seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m"
    return f"{sec}s"


def fmt_hours(seconds: float, dp: int = 2) -> str:
    return f"{seconds / 3600.0:.{dp}f}"
