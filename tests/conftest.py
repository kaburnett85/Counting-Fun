"""Shared fixtures.

The Simulator below is the workhorse: it drives the real SessionTracker with a
scripted window feed and a fake clock, applying the ops it emits to plain
lists. That gives the tests a full day of tracking with no database, no
sleeping, and no Windows.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from timesplit.backends.fake.capture_fake import ScriptedCaptureBackend  # noqa: E402
from timesplit.config import Config  # noqa: E402
from timesplit.core.clock import FakeClock  # noqa: E402
from timesplit.core.models import (  # noqa: E402
    CloseSession,
    IdleRecord,
    OpenSession,
    RecordIdle,
    SessionRecord,
    UpdateSession,
)
from timesplit.core.tracker import SessionTracker  # noqa: E402


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Point every path helper at a throwaway directory."""
    home = tmp_path / "ts-home"
    home.mkdir()
    monkeypatch.setenv("TIMESPLIT_HOME", str(home))
    return home


@pytest.fixture
def config():
    return Config()


@dataclass
class Simulator:
    """Runs a tracker over a script and records the resulting rows."""

    cfg: Config
    clock: FakeClock
    tracker: SessionTracker
    backend: ScriptedCaptureBackend
    sessions: list[SessionRecord] = field(default_factory=list)
    idles: list[IdleRecord] = field(default_factory=list)
    #: Timestamp of the first snapshot. The tracker cannot account for time
    #: before it took its first sample, so conservation is measured from here.
    first_ts: float | None = None
    _open: SessionRecord | None = None

    def apply(self, ops) -> None:
        for op in ops:
            if isinstance(op, OpenSession):
                record = op.session
                self.sessions.append(record)
                self._open = record if not record.closed else None
            elif isinstance(op, UpdateSession):
                if self._open is not None:
                    self._open.end_ts = op.end_ts
                    if op.url:
                        self._open.url = op.url
                        self._open.domain = op.domain
            elif isinstance(op, CloseSession):
                if self._open is not None:
                    if op.discard:
                        self.sessions.remove(self._open)
                    else:
                        self._open.end_ts = op.end_ts
                        self._open.closed = True
                    self._open = None
            elif isinstance(op, RecordIdle):
                if op.idle.duration_s > 0:
                    self.idles.append(op.idle)

    def run(self, ticks: int = 10_000) -> Simulator:
        for _ in range(ticks):
            snap = self.backend.capture()
            if snap.unavailable and self.backend.exhausted:
                # The script ran out. Stop here rather than feeding a phantom
                # tick, so totals reflect exactly the scripted wall time.
                break
            if self.first_ts is None:
                self.first_ts = snap.ts
            self.apply(self.tracker.feed(snap))
        self.apply(self.tracker.shutdown(self.clock.now()))
        return self

    # ---- assertions helpers ----

    @property
    def session_seconds(self) -> float:
        return sum(s.end_ts - s.start_ts for s in self.sessions)

    @property
    def idle_seconds(self) -> float:
        return sum(i.end_ts - i.start_ts for i in self.idles)

    def seconds_for(self, exe: str) -> float:
        return sum(s.end_ts - s.start_ts for s in self.sessions if s.exe_name == exe)

    def idle_seconds_for(self, reason: str) -> float:
        return sum(i.end_ts - i.start_ts for i in self.idles if i.reason == reason)


def make_sim(steps, cfg: Config | None = None, start: float = 1_700_000_000.0,
             zone: str = "UTC") -> Simulator:
    cfg = cfg or Config()
    clock = FakeClock(start=start, zone=zone)
    backend = ScriptedCaptureBackend(clock, steps, interval_s=cfg.sampling.interval_s)
    tracker = SessionTracker(cfg, clock.tzinfo())
    return Simulator(cfg=cfg, clock=clock, tracker=tracker, backend=backend)


@pytest.fixture
def sim_factory():
    return make_sim


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Never let a test read the developer's real API key or data directory."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if "TIMESPLIT_HOME" not in os.environ:
        monkeypatch.setenv("TIMESPLIT_HOME", "/nonexistent-timesplit-home")
