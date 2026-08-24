"""Properties that must hold for any sequence of observations.

These are the guarantees a timesheet depends on. Rather than test a handful of
hand-picked scenarios, generate many random workdays -- mixing focus changes,
idle, locks, suspends and midnight crossings -- and assert the same four
properties every time.
"""

from __future__ import annotations

import random

from conftest import make_sim

from timesplit.config import Config

APPS = [
    ("chrome.exe", "Canvas - Grading", "https://canvas.instructure.com/courses/1"),
    ("chrome.exe", "Zillow - 12 Oak St", "https://zillow.com/homes/12-oak"),
    ("comet.exe", "MLS search", "https://matrix.mlsmatrix.com/search"),
    ("excel.exe", "Commissions.xlsx", None),
    ("word.exe", "Lesson plan.docx", None),
    ("outlook.exe", "Inbox", None),
    ("vscode.exe", "gradebook.py", None),
]


def random_script(rng: random.Random, n_steps: int = 40) -> list[dict]:
    steps: list[dict] = []
    for _ in range(n_steps):
        roll = rng.random()
        if roll < 0.12:
            # Away from the keyboard for a while.
            idle_for = rng.choice([200, 400, 900, 1800])
            exe, title, url = rng.choice(APPS)
            steps.append(
                {"exe": exe, "title": title, "url": url,
                 "duration_s": idle_for, "idle_ms": idle_for * 1000}
            )
        elif roll < 0.18:
            steps.append({"exe": "", "title": "", "duration_s": rng.choice([300, 1200]),
                          "locked": True})
        elif roll < 0.22:
            exe, title, url = rng.choice(APPS)
            steps.append({"exe": exe, "title": title, "url": url,
                          "duration_s": 60, "suspend_s": rng.choice([600, 3600, 28800])})
        elif roll < 0.26:
            exe, title, url = rng.choice(APPS)
            steps.append({"exe": exe, "title": title, "url": url, "duration_s": 3})
        else:
            exe, title, url = rng.choice(APPS)
            steps.append({"exe": exe, "title": title, "url": url,
                          "duration_s": rng.choice([60, 300, 900, 1800, 4000])})
    return steps


def wall_time_of(script: list[dict]) -> float:
    return sum(s.get("duration_s", 0) + s.get("suspend_s", 0) for s in script)


def test_time_is_conserved_across_random_days():
    """Recorded work + recorded idle == the wall time we actually observed.

    Any drift here is time appearing on, or vanishing from, a timesheet.
    """
    for seed in range(120):
        rng = random.Random(seed)
        script = random_script(rng)
        sim = make_sim(script, start=1_700_000_000.0 + seed * 7919).run()

        # Measured from the first sample: the tracker cannot speak for time
        # before it was running.
        observed = sim.clock.now() - (sim.first_ts or 0.0)
        accounted = sim.session_seconds + sim.idle_seconds
        # Exact: not "close enough". Every observed second is either work or a
        # recorded reason for not working.
        assert accounted == observed, (
            f"seed {seed}: accounted {accounted} vs observed {observed}"
        )


def test_sessions_never_overlap_across_random_days():
    for seed in range(40):
        rng = random.Random(1000 + seed)
        sim = make_sim(random_script(rng)).run()
        ordered = sorted(sim.sessions, key=lambda s: s.start_ts)
        for prev, nxt in zip(ordered, ordered[1:], strict=False):
            assert prev.end_ts <= nxt.start_ts + 1e-6, (
                f"seed {seed}: {prev.exe_name} overlaps {nxt.exe_name}"
            )


def test_no_session_spans_local_midnight():
    from timesplit.core.clock import local_day

    for seed in range(30):
        rng = random.Random(2000 + seed)
        # Start near midnight so crossings are likely.
        start = 86400 * (19000 + seed) - 3600
        sim = make_sim(random_script(rng, n_steps=25), start=start).run()
        tz = sim.clock.tzinfo()
        for s in sim.sessions:
            assert s.local_day == local_day(s.start_ts, tz)
            if s.end_ts > s.start_ts:
                assert local_day(s.end_ts - 1, tz) == s.local_day, (
                    f"seed {seed}: session {s.start_ts}-{s.end_ts} crosses midnight"
                )


def test_no_session_is_shorter_than_the_minimum():
    for seed in range(30):
        rng = random.Random(3000 + seed)
        cfg = Config()
        sim = make_sim(random_script(rng), cfg=cfg).run()
        for s in sim.sessions:
            assert s.duration_s >= cfg.sampling.min_session_s or s.duration_s == 0


def test_durations_are_never_negative():
    for seed in range(30):
        rng = random.Random(4000 + seed)
        sim = make_sim(random_script(rng)).run()
        assert all(s.end_ts >= s.start_ts for s in sim.sessions)
        assert all(i.end_ts >= i.start_ts for i in sim.idles)


def test_idle_periods_never_overlap_sessions():
    """A given second is either work or idle -- never both."""
    for seed in range(25):
        rng = random.Random(5000 + seed)
        sim = make_sim(random_script(rng)).run()
        spans = [(s.start_ts, s.end_ts, "work") for s in sim.sessions]
        spans += [(i.start_ts, i.end_ts, "idle") for i in sim.idles]
        spans.sort()
        for (a_start, a_end, a_kind), (b_start, b_end, b_kind) in zip(spans, spans[1:], strict=False):
            assert a_end <= b_start + 1e-6, (
                f"seed {seed}: {a_kind} {a_start}-{a_end} overlaps {b_kind} {b_start}-{b_end}"
            )
