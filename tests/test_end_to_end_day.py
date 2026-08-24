"""A full day through the real application, from window feed to timesheet.

This exercises the actual Application object -- the same code that runs on
Windows -- driven by a scripted window feed instead of Win32. If this passes,
the only untested surface left is the thin adapter layer.
"""

from __future__ import annotations

import csv

import pytest

from timesplit.app import Application
from timesplit.config import Config
from timesplit.core.aggregate import totals_for_day
from timesplit.core.clock import local_day
from timesplit.core.models import Activity
from timesplit.export import csv_export
from timesplit.store import repo_sessions
from timesplit.wizard.seeds import apply_seed_rules


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    cfg = Config()
    cfg.timezone = "UTC"
    cfg.first_run_complete = True
    application = Application(cfg, demo=True)
    apply_seed_rules(application.db, application.engine.category_ids)
    application.engine.reload()
    yield application
    application.db.close()


def replay(application) -> str:
    application.capture = application._build_capture()
    application.capture.start()
    ticks = 0
    while not application.capture.exhausted and ticks < 20_000:
        snapshot = application.capture.capture()
        if snapshot.unavailable and application.capture.exhausted:
            break
        application._apply(application.tracker.feed(snapshot))
        ticks += 1
    application._apply(application.tracker.shutdown(application.clock.now()))
    return local_day(application.clock.now(), application.engine.tz)


def test_a_replayed_day_splits_between_both_jobs(app):
    day = replay(app)
    totals = totals_for_day(app.db, day)
    assert totals.seconds_for("school") > 3600
    assert totals.seconds_for("real_estate") > 3600


def test_the_day_adds_up(app):
    """Work plus idle must equal the time the app actually observed."""
    day = replay(app)
    totals = totals_for_day(app.db, day)
    accounted = totals.tracked_seconds + totals.idle_seconds
    scripted = sum(
        step.get("duration_s", 0) + step.get("suspend_s", 0)
        for step in __import__("timesplit.demo", fromlist=["demo_script"]).demo_script()
    )
    assert abs(accounted - scripted) <= app.cfg.sampling.interval_s


def test_the_lunchtime_lock_is_not_billed(app):
    day = replay(app)
    idle = repo_sessions.idle_for_day(app.db, day)
    locked = sum(i["duration_s"] for i in idle if i["reason"] == "locked")
    assert locked >= 2300, "a 40 minute lock must be excluded"
    assert not any(s["duration_s"] > 3000 for s in repo_sessions.sessions_for_day(app.db, day))


def test_the_coffee_break_is_not_billed(app):
    day = replay(app)
    idle = repo_sessions.idle_for_day(app.db, day)
    assert sum(i["duration_s"] for i in idle if i["reason"] == "idle") >= 600


def test_seeded_sites_are_categorised_without_any_training(app):
    day = replay(app)
    rows = repo_sessions.sessions_for_day(app.db, day)
    canvas = next(r for r in rows if "canvas" in (r["domain"] or ""))
    zillow = next(r for r in rows if "zillow" in (r["domain"] or ""))
    assert canvas["category_key"] == "school"
    assert zillow["category_key"] == "real_estate"
    assert canvas["source"] == "seed_rule"


def test_unrecognised_programs_reach_the_review_queue(app):
    replay(app)
    queue = repo_sessions.review_queue(app.db)
    apps_needing_review = {r["exe_name"] for r in queue}
    assert "quickbooks.exe" in apps_needing_review
    # Longest first, so the first clicks are worth the most.
    durations = [r["duration_s"] for r in queue]
    assert durations == sorted(durations, reverse=True)


def test_one_correction_resolves_every_matching_session(app):
    day = replay(app)
    before = totals_for_day(app.db, day).seconds_for("real_estate")
    quickbooks = next(
        r for r in repo_sessions.review_queue(app.db) if r["exe_name"] == "quickbooks.exe"
    )

    result = app.engine.apply_correction(int(quickbooks["id"]), "real_estate", "same_exe")
    assert result["ok"]

    after = totals_for_day(app.db, day).seconds_for("real_estate")
    assert after > before
    # And it now knows for next time.
    decision = app.engine.classify(Activity("quickbooks.exe", "Anything at all"))
    assert app.engine.category_key(decision.category_id) == "real_estate"


def test_the_exported_timesheet_matches_the_dashboard(app, tmp_path):
    day = replay(app)
    totals = totals_for_day(app.db, day)
    written = csv_export.write_all(app.engine, day, day, out=tmp_path / "out")

    rows = list(csv.DictReader(written[1].open(encoding="utf-8-sig")))
    school = next(r for r in rows if r["job"] == "School")
    assert abs(float(school["hours"]) - totals.seconds_for("school") / 3600) < 0.01


def test_running_it_twice_does_not_double_count(app):
    """Restarting the tracker must not re-bill the day."""
    day = replay(app)
    first = totals_for_day(app.db, day).tracked_seconds

    recovered = repo_sessions.finalize_open_sessions(app.db, app.clock.now())
    assert recovered == 0, "a clean shutdown should leave nothing open"
    assert totals_for_day(app.db, day).tracked_seconds == first


def test_the_dashboard_renders_the_replayed_day(app):
    day = replay(app)
    from timesplit.web.server import DashboardServer

    server = DashboardServer(app.engine, app.cfg, port=0)
    server.port = 0
    try:
        server.start()
        import http.client

        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("GET", f"/day?d={day}", headers={"Cookie": f"ts_auth={server.token}"})
        response = conn.getresponse()
        html = response.read().decode()
        conn.close()
        assert response.status == 200
        assert "School" in html and "Real Estate" in html
        assert "Review" in html
    finally:
        server.stop()
