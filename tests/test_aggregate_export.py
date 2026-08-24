"""Aggregation, timezone correctness, retention and the exported files."""

from __future__ import annotations

import csv

import pytest

from timesplit.config import Config
from timesplit.core import aggregate, retention
from timesplit.core.clock import add_days, day_bounds, day_range, resolve_zone, week_start
from timesplit.core.models import IdleRecord, SessionRecord
from timesplit.engine import Engine
from timesplit.export import csv_export
from timesplit.store import repo_sessions
from timesplit.store.db import Database


@pytest.fixture
def engine(tmp_path):
    cfg = Config()
    cfg.timezone = "America/New_York"
    db = Database(tmp_path / "agg.db")
    engine = Engine(db, cfg)
    yield engine
    db.close()


def add(engine, day, exe, title, seconds, category_key, *, offset=0, review=False):
    start, _ = day_bounds(day, engine.tz)
    record = SessionRecord(
        start_ts=start + offset, end_ts=start + offset + seconds, exe_name=exe,
        exe_path=f"C:\\{exe}", title=title, local_day=day, closed=True,
        category_id=engine.category_ids[category_key], source="user_rule",
        confidence=1.0, needs_review=review,
    )
    return repo_sessions.insert_session(engine.db, record)


# ---- day and week arithmetic --------------------------------------------


def test_spring_forward_day_is_twenty_three_hours():
    tz = resolve_zone("America/New_York")
    start, end = day_bounds("2026-03-08", tz)
    assert (end - start) / 3600 == 23


def test_fall_back_day_is_twenty_five_hours():
    tz = resolve_zone("America/New_York")
    start, end = day_bounds("2026-11-01", tz)
    assert (end - start) / 3600 == 25


def test_week_starts_on_the_configured_day():
    assert week_start("2026-08-24", "monday") == "2026-08-24"
    assert week_start("2026-08-23", "monday") == "2026-08-17"
    assert week_start("2026-08-23", "sunday") == "2026-08-23"


def test_day_range_is_inclusive_and_ordered():
    assert day_range("2026-08-24", "2026-08-26") == ["2026-08-24", "2026-08-25", "2026-08-26"]
    assert day_range("2026-08-26", "2026-08-24") == ["2026-08-24", "2026-08-25", "2026-08-26"]


# ---- totals --------------------------------------------------------------


def test_day_totals_group_by_job(engine):
    day = "2026-08-24"
    add(engine, day, "chrome.exe", "Canvas", 3600, "school")
    add(engine, day, "word.exe", "Lesson", 1800, "school", offset=3600)
    add(engine, day, "chrome.exe", "Zillow", 2700, "real_estate", offset=5400)
    totals = aggregate.totals_for_day(engine.db, day)
    assert totals.seconds_for("school") == 5400
    assert totals.seconds_for("real_estate") == 2700
    assert totals.tracked_seconds == 8100


def test_idle_is_reported_separately_and_not_in_any_job(engine):
    day = "2026-08-24"
    start, _ = day_bounds(day, engine.tz)
    add(engine, day, "word.exe", "Doc", 3600, "school")
    repo_sessions.insert_idle(
        engine.db,
        IdleRecord(start_ts=start + 3600, end_ts=start + 3600 + 1800, reason="idle",
                   local_day=day),
    )
    totals = aggregate.totals_for_day(engine.db, day)
    assert totals.idle_seconds == 1800
    assert totals.seconds_for("school") == 3600
    assert all(c.key != "idle" for c in totals.categories)


def test_review_seconds_are_tracked_per_job(engine):
    day = "2026-08-24"
    add(engine, day, "word.exe", "A", 600, "school")
    add(engine, day, "word.exe", "B", 900, "school", offset=600, review=True)
    totals = aggregate.totals_for_day(engine.db, day)
    assert totals.by_key("school").review_seconds == 900


def test_empty_day_is_not_an_error(engine):
    totals = aggregate.totals_for_day(engine.db, "2020-01-01")
    assert totals.tracked_seconds == 0
    assert totals.categories == []
    assert "Nothing tracked" in aggregate.summary_line(totals)


def test_week_totals_cover_every_day(engine):
    days = aggregate.week_of("2026-08-24", "monday")
    add(engine, days[0], "word.exe", "A", 3600, "school")
    add(engine, days[3], "chrome.exe", "B", 1800, "real_estate")
    per_day = aggregate.totals_for_days(engine.db, days)
    assert len(per_day) == 7
    assert per_day[0].seconds_for("school") == 3600
    assert per_day[3].seconds_for("real_estate") == 1800
    assert per_day[1].tracked_seconds == 0


def test_top_apps_and_domains(engine):
    day = "2026-08-24"
    add(engine, day, "chrome.exe", "A", 3600, "school")
    add(engine, day, "word.exe", "B", 1800, "school", offset=3600)
    apps = aggregate.top_apps(engine.db, day, day)
    assert apps[0]["exe_name"] == "chrome.exe"


def test_timeline_orders_work_and_idle_together(engine):
    day = "2026-08-24"
    start, _ = day_bounds(day, engine.tz)
    add(engine, day, "word.exe", "A", 600, "school")
    repo_sessions.insert_idle(
        engine.db,
        IdleRecord(start_ts=start + 600, end_ts=start + 1200, reason="idle", local_day=day),
    )
    add(engine, day, "chrome.exe", "B", 600, "real_estate", offset=1200)
    blocks = aggregate.timeline(engine.db, day, engine.tz)
    assert [b.kind for b in blocks] == ["work", "idle", "work"]
    assert blocks == sorted(blocks, key=lambda b: b.start_ts)


# ---- retention -----------------------------------------------------------


def test_retention_removes_only_what_is_past_its_window(engine):
    today = "2026-08-24"
    add(engine, today, "word.exe", "recent", 600, "school")
    add(engine, add_days(today, -900), "word.exe", "ancient", 600, "school")
    assert engine.db.scalar("SELECT COUNT(*) FROM sessions") == 2

    retention.prune(engine.db, engine.cfg.retention, today)
    remaining = [r["title"] for r in engine.db.query("SELECT title FROM sessions")]
    assert remaining == ["recent"]


def test_vacuum_is_not_repeated(engine):
    assert retention.vacuum_if_due(engine.db, engine.cfg.retention) is True
    assert retention.vacuum_if_due(engine.db, engine.cfg.retention) is False


# ---- export --------------------------------------------------------------


def test_invoice_rounding_rounds_up_to_the_increment():
    assert csv_export.round_seconds(0, 15) == 0
    assert csv_export.round_seconds(60, 15) == 900
    assert csv_export.round_seconds(900, 15) == 900
    assert csv_export.round_seconds(901, 15) == 1800
    assert csv_export.round_seconds(1234, 0) == 1234


def test_csv_export_writes_three_usable_files(engine, tmp_path):
    day = "2026-08-24"
    add(engine, day, "chrome.exe", "Canvas Gradebook", 3600, "school")
    add(engine, day, "chrome.exe", "Zillow", 1800, "real_estate", offset=3600)

    written = csv_export.write_all(engine, day, day, out=tmp_path)
    assert len(written) == 3
    assert all(p.exists() and p.stat().st_size > 0 for p in written)

    sessions = list(csv.DictReader(written[0].open(encoding="utf-8-sig")))
    assert len(sessions) == 2
    assert sessions[0]["job"] == "School"
    assert sessions[0]["hours"] == "1.00"

    invoice = written[2].read_text(encoding="utf-8-sig")
    assert "TOTAL" in invoice


def test_export_can_leave_out_unconfirmed_time(engine, tmp_path):
    day = "2026-08-24"
    add(engine, day, "word.exe", "certain", 3600, "school")
    add(engine, day, "word.exe", "unsure", 1800, "school", offset=3600, review=True)

    with_all = csv_export.write_invoice(engine, day, day, out=tmp_path / "a")
    without = csv_export.write_invoice(
        engine, day, day, out=tmp_path / "b", exclude_unreviewed=True
    )
    assert "1.50" in with_all.read_text(encoding="utf-8-sig")
    assert "1.00" in without.read_text(encoding="utf-8-sig")


def test_uncategorised_time_stays_out_of_the_invoice(engine, tmp_path):
    day = "2026-08-24"
    add(engine, day, "word.exe", "real work", 3600, "school")
    add(engine, day, "steam.exe", "no idea", 1800, "unknown", offset=3600)
    invoice = csv_export.write_invoice(engine, day, day, out=tmp_path).read_text(
        encoding="utf-8-sig"
    )
    assert "Uncategorised" not in invoice


def test_export_of_an_empty_range_still_produces_files(engine, tmp_path):
    written = csv_export.write_all(engine, "2020-01-01", "2020-01-02", out=tmp_path)
    assert all(p.exists() for p in written)
