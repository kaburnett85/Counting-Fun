"""CSV timesheets.

Three files, because three different questions get asked of this data:

  sessions_*.csv       what happened, line by line
  daily_totals_*.csv   hours per job per day
  invoice_*.csv        decimal hours, rounded, ready to paste into a timesheet
"""

from __future__ import annotations

import csv
from pathlib import Path

from .. import paths
from ..core import aggregate
from ..core.clock import day_range, fmt_hours, local_dt
from ..store import repo_sessions


def round_seconds(seconds: float, rounding_min: int) -> float:
    """Round up to the nearest billing increment, the way invoices work."""
    if rounding_min <= 0:
        return seconds
    step = rounding_min * 60
    if seconds <= 0:
        return 0.0
    import math

    return math.ceil(seconds / step) * step


def _target_dir(out: str | Path | None) -> Path:
    if out:
        path = Path(out).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return paths.export_dir()


def write_sessions(engine, start_day: str, end_day: str, *, out=None,
                   exclude_unreviewed: bool = False) -> Path:
    rows = repo_sessions.sessions_between(engine.db, start_day, end_day)
    target = _target_dir(out) / f"sessions_{start_day}_to_{end_day}.csv"
    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "date", "start", "end", "minutes", "hours", "job", "app", "website",
            "window title", "assigned by", "confidence", "unconfirmed",
        ])
        for row in rows:
            if exclude_unreviewed and row["needs_review"]:
                continue
            start = local_dt(float(row["start_ts"]), engine.tz)
            end = local_dt(float(row["end_ts"]), engine.tz)
            seconds = float(row["duration_s"])
            writer.writerow([
                row["local_day"], start.strftime("%H:%M:%S"), end.strftime("%H:%M:%S"),
                f"{seconds / 60:.1f}", fmt_hours(seconds),
                row.get("category_name") or "Uncategorised",
                row.get("exe_name") or "", row.get("domain") or "",
                row.get("title") or "", row["source"],
                f"{float(row['confidence']):.2f}",
                "yes" if row["needs_review"] else "",
            ])
    return target


def write_daily_totals(engine, start_day: str, end_day: str, *, out=None) -> Path:
    days = day_range(start_day, end_day)
    per_day = aggregate.totals_for_days(engine.db, days)
    target = _target_dir(out) / f"daily_totals_{start_day}_to_{end_day}.csv"
    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "job", "hours", "minutes", "sessions", "unconfirmed hours"])
        for day_total in per_day:
            for cat in day_total.categories:
                writer.writerow([
                    day_total.day, cat.name, fmt_hours(cat.seconds),
                    f"{cat.seconds / 60:.1f}", cat.sessions,
                    fmt_hours(cat.review_seconds),
                ])
            if day_total.idle_seconds:
                writer.writerow([
                    day_total.day, "Away / idle", fmt_hours(day_total.idle_seconds),
                    f"{day_total.idle_seconds / 60:.1f}", "", "",
                ])
    return target


def write_invoice(engine, start_day: str, end_day: str, *, out=None,
                  rounding_min: int = 0, exclude_unreviewed: bool = False) -> Path:
    """One row per day and job, with rounded decimal hours, plus a total."""
    days = day_range(start_day, end_day)
    per_day = aggregate.totals_for_days(engine.db, days)
    target = _target_dir(out) / f"invoice_{start_day}_to_{end_day}.csv"
    totals: dict[str, float] = {}

    with target.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        note = f"rounded up to {rounding_min} min" if rounding_min else "exact"
        writer.writerow(["date", "job", f"hours ({note})"])
        for day_total in per_day:
            for cat in day_total.categories:
                if cat.key in ("unknown", "excluded"):
                    continue
                seconds = cat.seconds - (cat.review_seconds if exclude_unreviewed else 0.0)
                if seconds <= 0:
                    continue
                seconds = round_seconds(seconds, rounding_min)
                totals[cat.name] = totals.get(cat.name, 0.0) + seconds
                writer.writerow([day_total.day, cat.name, fmt_hours(seconds)])
        writer.writerow([])
        for name, seconds in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
            writer.writerow(["TOTAL", name, fmt_hours(seconds)])
    return target


def write_all(engine, start_day: str, end_day: str, *, out=None,
              rounding_min: int = 0, exclude_unreviewed: bool = False) -> list[Path]:
    return [
        write_sessions(engine, start_day, end_day, out=out,
                       exclude_unreviewed=exclude_unreviewed),
        write_daily_totals(engine, start_day, end_day, out=out),
        write_invoice(engine, start_day, end_day, out=out, rounding_min=rounding_min,
                      exclude_unreviewed=exclude_unreviewed),
    ]
