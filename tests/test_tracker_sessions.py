"""Session construction: boundaries, flicker, heartbeat, splits, recovery."""

from __future__ import annotations

from conftest import make_sim

from timesplit.config import Config
from timesplit.core.clock import FakeClock, local_day
from timesplit.core.models import CloseSession, OpenSession, UpdateSession
from timesplit.core.tracker import SessionTracker


def test_single_window_produces_one_session():
    sim = make_sim([{"exe": "excel.exe", "title": "Budget.xlsx", "duration_s": 600}]).run()
    assert len(sim.sessions) == 1
    s = sim.sessions[0]
    assert s.exe_name == "excel.exe"
    assert s.duration_s == 600
    assert s.closed


def test_focus_change_splits_sessions():
    sim = make_sim(
        [
            {"exe": "excel.exe", "title": "Budget.xlsx", "duration_s": 300},
            {"exe": "chrome.exe", "title": "Zillow", "duration_s": 600},
        ]
    ).run()
    assert [s.exe_name for s in sim.sessions] == ["excel.exe", "chrome.exe"]
    assert sim.seconds_for("excel.exe") == 300
    assert sim.seconds_for("chrome.exe") == 600


def test_title_change_within_same_app_splits():
    sim = make_sim(
        [
            {"exe": "chrome.exe", "title": "Canvas - Grading", "duration_s": 300},
            {"exe": "chrome.exe", "title": "Zillow - 12 Oak St", "duration_s": 300},
        ]
    ).run()
    assert len(sim.sessions) == 2
    assert {s.title for s in sim.sessions} == {"Canvas - Grading", "Zillow - 12 Oak St"}


def test_brief_alt_tab_is_discarded():
    """A three-second flick through a window is not its own line item.

    The seconds still happened, though, so they are absorbed by the work that
    follows rather than disappearing from the day.
    """
    sim = make_sim(
        [
            {"exe": "excel.exe", "title": "Budget", "duration_s": 300},
            {"exe": "explorer.exe", "title": "Downloads", "duration_s": 3},
            {"exe": "excel.exe", "title": "Budget", "duration_s": 300},
        ]
    ).run()
    assert "explorer.exe" not in [s.exe_name for s in sim.sessions]
    assert sim.session_seconds == 603
    assert sim.seconds_for("excel.exe") == 603


def test_sessions_never_overlap():
    sim = make_sim(
        [
            {"exe": "a.exe", "title": "A", "duration_s": 120},
            {"exe": "b.exe", "title": "B", "duration_s": 120},
            {"exe": "c.exe", "title": "C", "duration_s": 120},
        ]
    ).run()
    ordered = sorted(sim.sessions, key=lambda s: s.start_ts)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.end_ts <= nxt.start_ts


def test_long_focus_is_split_at_max_session():
    cfg = Config()
    cfg.sampling.max_session_s = 600
    sim = make_sim(
        [{"exe": "vscode.exe", "title": "main.py", "duration_s": 2400}], cfg=cfg
    ).run()
    assert len(sim.sessions) == 4
    assert sim.session_seconds == 2400
    assert all(s.duration_s <= 600 for s in sim.sessions)


def test_session_split_at_local_midnight():
    # Start 30 minutes before midnight UTC and work through it.
    start = 86400 * 100 - 1800  # 23:30 on some day
    sim = make_sim(
        [{"exe": "word.exe", "title": "Lesson plan", "duration_s": 3600}], start=start
    ).run()
    days = {s.local_day for s in sim.sessions}
    assert len(days) == 2, f"expected a split across midnight, got {days}"
    assert sim.session_seconds == 3600
    for s in sim.sessions:
        assert local_day(s.start_ts, sim.clock.tzinfo()) == s.local_day
        assert local_day(s.end_ts - 1, sim.clock.tzinfo()) == s.local_day


def test_heartbeat_persists_before_session_ends():
    """A power cut mid-session must not lose more than one heartbeat."""
    cfg = Config()
    cfg.sampling.heartbeat_s = 30
    clock = FakeClock(zone="UTC")
    tracker = SessionTracker(cfg, clock.tzinfo())
    from timesplit.backends.fake.capture_fake import ScriptedCaptureBackend

    backend = ScriptedCaptureBackend(
        clock, [{"exe": "word.exe", "title": "Doc", "duration_s": 300}], interval_s=3
    )
    ops = []
    for _ in range(40):
        ops.extend(tracker.feed(backend.capture()))
    assert any(isinstance(o, OpenSession) for o in ops), "session should be written early"
    updates = [o for o in ops if isinstance(o, UpdateSession)]
    assert len(updates) >= 3, "heartbeat should flush progress repeatedly"


def test_short_session_never_reaches_storage():
    cfg = Config()
    cfg.sampling.min_session_s = 5
    clock = FakeClock(zone="UTC")
    tracker = SessionTracker(cfg, clock.tzinfo())
    from timesplit.backends.fake.capture_fake import ScriptedCaptureBackend

    backend = ScriptedCaptureBackend(
        clock, [{"exe": "popup.exe", "title": "Toast", "duration_s": 3}], interval_s=3
    )
    ops = []
    for _ in range(2):
        ops.extend(tracker.feed(backend.capture()))
    ops.extend(tracker.shutdown(clock.now()))
    assert not [o for o in ops if isinstance(o, OpenSession)]


def test_shutdown_closes_the_live_session():
    cfg = Config()
    clock = FakeClock(zone="UTC")
    tracker = SessionTracker(cfg, clock.tzinfo())
    from timesplit.backends.fake.capture_fake import ScriptedCaptureBackend

    backend = ScriptedCaptureBackend(
        clock, [{"exe": "word.exe", "title": "Doc", "duration_s": 600}], interval_s=3
    )
    for _ in range(20):
        tracker.feed(backend.capture())
    ops = tracker.shutdown(clock.now())
    assert any(isinstance(o, (CloseSession, OpenSession)) for o in ops)
    assert tracker.state.live is None


def test_url_change_starts_a_new_session():
    sim = make_sim(
        [
            {"exe": "chrome.exe", "title": "Tab", "url": "https://zillow.com/a", "duration_s": 120},
            {"exe": "chrome.exe", "title": "Tab", "url": "https://zillow.com/b", "duration_s": 120},
        ]
    ).run()
    assert len(sim.sessions) == 2


def test_late_url_patches_the_live_session():
    """UIA resolves asynchronously; the URL must land on the session already open."""
    cfg = Config()
    clock = FakeClock(zone="UTC")
    tracker = SessionTracker(cfg, clock.tzinfo())
    from timesplit.backends.fake.capture_fake import ScriptedCaptureBackend

    backend = ScriptedCaptureBackend(
        clock, [{"exe": "chrome.exe", "title": "Loading", "duration_s": 300}], interval_s=3
    )
    for _ in range(10):
        tracker.feed(backend.capture())
    ops = tracker.attach_url("https://dotloop.com/x", "dotloop.com")
    assert any(isinstance(o, UpdateSession) and o.reclassify for o in ops)
    assert tracker.state.live.record.url == "https://dotloop.com/x"
