"""Turning stored sessions into the numbers the dashboard and exports show."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from zoneinfo import ZoneInfo

from ..store import repo_sessions
from ..store.db import Database
from .clock import add_days, day_range, local_day, week_start


@dataclass
class CategoryTotal:
    category_id: int | None
    key: str
    name: str
    color: str
    seconds: float = 0.0
    sessions: int = 0
    review_seconds: float = 0.0

    @property
    def hours(self) -> float:
        return self.seconds / 3600.0


@dataclass
class DayTotals:
    day: str
    categories: list[CategoryTotal] = field(default_factory=list)
    idle_seconds: float = 0.0
    tracked_seconds: float = 0.0

    def by_key(self, key: str) -> CategoryTotal | None:
        for c in self.categories:
            if c.key == key:
                return c
        return None

    def seconds_for(self, key: str) -> float:
        c = self.by_key(key)
        return c.seconds if c else 0.0


UNCATEGORISED = ("unknown", "Uncategorised", "#9aa0a6")


def totals_for_day(db: Database, day: str) -> DayTotals:
    rows = repo_sessions.sessions_for_day(db, day)
    return _aggregate(day, rows, _idle_seconds(db, day))


def totals_for_days(db: Database, days: list[str]) -> list[DayTotals]:
    if not days:
        return []
    rows = repo_sessions.sessions_between(db, days[0], days[-1])
    idle_by_day = _idle_by_day(db, days[0], days[-1])
    by_day: dict[str, list[dict]] = {d: [] for d in days}
    for row in rows:
        by_day.setdefault(row["local_day"], []).append(row)
    return [_aggregate(d, by_day.get(d, []), idle_by_day.get(d, 0.0)) for d in days]


def totals_for_range(db: Database, start_day: str, end_day: str) -> DayTotals:
    """One combined total across a date range."""
    rows = repo_sessions.sessions_between(db, start_day, end_day)
    idle = sum(_idle_by_day(db, start_day, end_day).values())
    return _aggregate(f"{start_day}..{end_day}", rows, idle)


def week_of(day: str, first_day: str = "monday") -> list[str]:
    start = week_start(day, first_day)
    return day_range(start, add_days(start, 6))


def _aggregate(day: str, rows: list[dict], idle_seconds: float) -> DayTotals:
    buckets: dict[int | None, CategoryTotal] = {}
    for row in rows:
        cid = row["category_id"]
        bucket = buckets.get(cid)
        if bucket is None:
            bucket = CategoryTotal(
                category_id=cid,
                key=row.get("category_key") or UNCATEGORISED[0],
                name=row.get("category_name") or UNCATEGORISED[1],
                color=row.get("category_color") or UNCATEGORISED[2],
            )
            buckets[cid] = bucket
        seconds = float(row["duration_s"])
        bucket.seconds += seconds
        bucket.sessions += 1
        if row["needs_review"]:
            bucket.review_seconds += seconds

    ordered = sorted(buckets.values(), key=lambda c: c.seconds, reverse=True)
    return DayTotals(
        day=day,
        categories=ordered,
        idle_seconds=idle_seconds,
        tracked_seconds=sum(c.seconds for c in ordered),
    )


def _idle_seconds(db: Database, day: str) -> float:
    return float(
        db.scalar(
            "SELECT COALESCE(SUM(duration_s), 0) FROM idle_periods WHERE local_day = ?",
            (day,),
            default=0,
        )
    )


def _idle_by_day(db: Database, start_day: str, end_day: str) -> dict[str, float]:
    return {
        r["local_day"]: float(r["secs"])
        for r in db.query(
            "SELECT local_day, SUM(duration_s) AS secs FROM idle_periods"
            " WHERE local_day BETWEEN ? AND ? GROUP BY local_day",
            (start_day, end_day),
        )
    }


# ---- breakdowns ----------------------------------------------------------


def top_apps(db: Database, start_day: str, end_day: str, limit: int = 10) -> list[dict]:
    return [
        dict(r)
        for r in db.query(
            "SELECT a.exe_name, c.display_name AS category_name, c.color AS category_color,"
            " SUM(s.duration_s) AS seconds, COUNT(*) AS sessions"
            " FROM sessions s LEFT JOIN apps a ON a.id = s.app_id"
            " LEFT JOIN categories c ON c.id = s.category_id"
            " WHERE s.local_day BETWEEN ? AND ? AND a.exe_name IS NOT NULL"
            " GROUP BY a.exe_name, s.category_id ORDER BY seconds DESC LIMIT ?",
            (start_day, end_day, limit),
        )
    ]


def top_domains(db: Database, start_day: str, end_day: str, limit: int = 10) -> list[dict]:
    return [
        dict(r)
        for r in db.query(
            "SELECT s.domain, c.display_name AS category_name, c.color AS category_color,"
            " SUM(s.duration_s) AS seconds, COUNT(*) AS sessions"
            " FROM sessions s LEFT JOIN categories c ON c.id = s.category_id"
            " WHERE s.local_day BETWEEN ? AND ? AND s.domain IS NOT NULL AND s.domain != ''"
            " GROUP BY s.domain, s.category_id ORDER BY seconds DESC LIMIT ?",
            (start_day, end_day, limit),
        )
    ]


@dataclass
class TimelineBlock:
    start_ts: float
    end_ts: float
    label: str
    color: str
    kind: str  # work | idle
    session_id: int | None = None
    needs_review: bool = False
    title: str = ""
    category_name: str = ""


def timeline(db: Database, day: str, tz: timezone | ZoneInfo) -> list[TimelineBlock]:
    """Work and idle blocks for one day, in order."""
    blocks: list[TimelineBlock] = []
    for row in repo_sessions.sessions_for_day(db, day):
        blocks.append(
            TimelineBlock(
                start_ts=float(row["start_ts"]),
                end_ts=float(row["end_ts"]),
                label=row.get("category_name") or "Uncategorised",
                color=row.get("category_color") or UNCATEGORISED[2],
                kind="work",
                session_id=int(row["id"]),
                needs_review=bool(row["needs_review"]),
                title=row.get("title") or "",
                category_name=row.get("category_name") or "Uncategorised",
            )
        )
    for row in repo_sessions.idle_for_day(db, day):
        blocks.append(
            TimelineBlock(
                start_ts=float(row["start_ts"]),
                end_ts=float(row["end_ts"]),
                label=row["reason"],
                color="#d5d8dd",
                kind="idle",
            )
        )
    blocks.sort(key=lambda b: b.start_ts)
    return blocks


def today(tz: timezone | ZoneInfo, now_ts: float) -> str:
    return local_day(now_ts, tz)


def summary_line(totals: DayTotals, limit: int = 2) -> str:
    """The one-line status the tray shows: 'School 3h 12m - Real Estate 1h 48m'."""
    from .clock import fmt_duration

    parts = [
        f"{c.name} {fmt_duration(c.seconds)}"
        for c in totals.categories
        if c.key not in ("unknown", "excluded")
    ][:limit]
    return " · ".join(parts) if parts else "Nothing tracked yet"
