"""Idle handling: the rules that decide whether time counts at all.

Every assertion here is a claim about what appears on a timesheet, so they are
written in those terms.
"""

from __future__ import annotations

from conftest import make_sim

from timesplit.config import Config
from timesplit.core.models import IDLE_INPUT, IDLE_LOCKED, IDLE_SCREENSAVER, IDLE_SLEEP


def test_idle_is_trimmed_back_to_the_last_real_input():
    """Notice idle at 15:03 after 5 minutes away -> the session ended at 14:58."""
    cfg = Config()
    cfg.idle.threshold_s = 180
    # 10 min of work, then the machine reports growing idle time.
    steps = [{"exe": "word.exe", "title": "Lesson plan", "duration_s": 600}]
    # idle_ms climbs: at the moment we cross the threshold, idle has been
    # running for 180s already.
    steps.append({"exe": "word.exe", "title": "Lesson plan", "duration_s": 300, "idle_ms": 180_000})
    sim = make_sim(steps, cfg=cfg).run()

    worked = sim.session_seconds
    # Work stops at the last input, which is 180s before the first idle tick.
    assert worked == 600 - 180 + 0, f"expected work trimmed back 180s, got {worked}"
    assert sim.idle_seconds_for(IDLE_INPUT) > 0


def test_coffee_break_is_not_billed_to_any_job():
    cfg = Config()
    cfg.idle.threshold_s = 180
    steps = [
        {"exe": "excel.exe", "title": "Commissions", "duration_s": 1800},
        {"exe": "excel.exe", "title": "Commissions", "duration_s": 900, "idle_ms": 180_000},
        {"exe": "excel.exe", "title": "Commissions", "duration_s": 1800, "idle_ms": 0},
    ]
    sim = make_sim(steps, cfg=cfg).run()
    total_wall = 1800 + 900 + 1800
    assert sim.session_seconds + sim.idle_seconds == total_wall
    # Roughly 900s + the 180s of pre-threshold idle should be excluded.
    assert 900 <= sim.idle_seconds <= 1200
    assert sim.session_seconds < total_wall


def test_return_from_idle_backdates_the_new_session():
    """You started typing 40s ago; the new session starts then, not now."""
    cfg = Config()
    cfg.idle.threshold_s = 180
    cfg.sampling.interval_s = 60
    steps = [
        {"exe": "word.exe", "title": "Doc", "duration_s": 600},
        {"exe": "word.exe", "title": "Doc", "duration_s": 600, "idle_ms": 600_000},
        {"exe": "word.exe", "title": "Doc", "duration_s": 600, "idle_ms": 40_000},
    ]
    sim = make_sim(steps, cfg=cfg).run()
    # The final session must have started before the tick that observed it.
    later = sorted(sim.sessions, key=lambda s: s.start_ts)[-1]
    idle_end = max(i.end_ts for i in sim.idles)
    assert later.start_ts <= idle_end + 1


def test_locked_screen_stops_the_clock():
    cfg = Config()
    steps = [
        {"exe": "chrome.exe", "title": "MLS", "duration_s": 600},
        {"exe": "", "title": "", "duration_s": 1800, "locked": True},
        {"exe": "chrome.exe", "title": "MLS", "duration_s": 600},
    ]
    sim = make_sim(steps, cfg=cfg).run()
    assert sim.idle_seconds_for(IDLE_LOCKED) >= 1700
    assert sim.session_seconds <= 1250


def test_screensaver_stops_the_clock():
    steps = [
        {"exe": "word.exe", "title": "Doc", "duration_s": 300},
        {"exe": "word.exe", "title": "Doc", "duration_s": 900, "screensaver": True},
    ]
    sim = make_sim(steps).run()
    assert sim.idle_seconds_for(IDLE_SCREENSAVER) > 0


def test_overnight_sleep_is_one_idle_period_not_an_eight_hour_session():
    """The single worst failure mode for a tracker like this."""
    steps = [
        {"exe": "word.exe", "title": "Lesson plan", "duration_s": 600},
        # The machine suspends for 8 hours: wall time jumps, steady clock does not.
        {"exe": "word.exe", "title": "Lesson plan", "duration_s": 300, "suspend_s": 8 * 3600},
    ]
    sim = make_sim(steps).run()
    assert all(s.duration_s < 3600 for s in sim.sessions), "no session may absorb the sleep"
    slept = sim.idle_seconds_for(IDLE_SLEEP)
    assert slept >= 8 * 3600 - 60, f"expected ~8h recorded as sleep, got {slept}"


def test_idle_periods_are_split_at_midnight():
    start = 86400 * 100 - 600  # ten minutes before midnight UTC
    steps = [
        {"exe": "word.exe", "title": "Doc", "duration_s": 300},
        {"exe": "word.exe", "title": "Doc", "duration_s": 60, "suspend_s": 7200},
    ]
    sim = make_sim(steps, start=start).run()
    days = {i.local_day for i in sim.idles}
    assert len(days) >= 2, f"idle spanning midnight should be split, got {days}"


def test_pause_records_the_paused_span_explicitly():
    """Paused time is accounted for, not silently missing from the day."""
    from timesplit.backends.fake.capture_fake import ScriptedCaptureBackend
    from timesplit.core.clock import FakeClock
    from timesplit.core.tracker import SessionTracker

    cfg = Config()
    clock = FakeClock(zone="UTC")
    tracker = SessionTracker(cfg, clock.tzinfo())
    backend = ScriptedCaptureBackend(
        clock, [{"exe": "word.exe", "title": "Doc", "duration_s": 3600}], interval_s=3
    )
    for _ in range(100):
        tracker.feed(backend.capture())
    ops = tracker.pause(clock.now())
    assert tracker.paused
    clock.advance(1800)
    ops = tracker.resume(clock.now())
    from timesplit.core.models import RecordIdle

    idles = [o.idle for o in ops if isinstance(o, RecordIdle)]
    assert idles and idles[0].reason == "paused"
    assert abs(idles[0].duration_s - 1800) <= 1


def test_unreadable_foreground_window_is_treated_as_away():
    """The secure desktop and transient failures must not be attributed to a job."""
    steps = [
        {"exe": "word.exe", "title": "Doc", "duration_s": 300},
        {"exe": "", "title": "", "duration_s": 600, "locked": True},
    ]
    sim = make_sim(steps).run()
    assert sim.session_seconds <= 320
