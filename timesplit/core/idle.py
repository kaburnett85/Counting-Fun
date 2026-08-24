"""Deciding when you are actually at the machine.

This is the part that has to be exactly right: any time the app is wrong about
whether you were present, that error lands directly on a job's total.

The rules, in the order they are checked:

  sleep      the steady clock jumped -- the machine suspended or the process
             was starved. Never credit the gap.
  locked     the workstation is locked.
  screensaver
  idle       no keyboard or mouse input for idle.threshold_s.

The important subtlety is *when* idle started. Windows tells us how long it
has been since the last input, so when we notice at 15:03 that there has been
no input for 5 minutes, the truth is that you left at 14:58. The session end
is trimmed back to 14:58, not to 15:03, and the same logic runs in reverse
when you come back.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import IdleConfig
from .models import (
    IDLE_INPUT,
    IDLE_LOCKED,
    IDLE_SCREENSAVER,
    IDLE_SLEEP,
    Snapshot,
)

MODE_ACTIVE = "active"
MODE_IDLE = "idle"
MODE_LOCKED = "locked"
MODE_SCREENSAVER = "screensaver"
MODE_PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class IdleVerdict:
    away: bool
    reason: str = ""
    mode: str = MODE_ACTIVE
    #: When the away period began, in epoch seconds. For input-idle this is
    #: earlier than the observation, because we learn about it after the fact.
    since_ts: float = 0.0


class IdlePolicy:
    """Pure predicate logic over a Snapshot. No I/O, no clock reads."""

    def __init__(self, cfg: IdleConfig):
        self.cfg = cfg

    def sleep_gap(self, snapshot: Snapshot, last_monotonic: float | None) -> float:
        """Seconds of wall time unaccounted for since the previous tick.

        Uses the steady clock so a user changing the system time, or NTP
        stepping it, cannot be mistaken for a suspend.
        """
        if last_monotonic is None:
            return 0.0
        gap = snapshot.monotonic - last_monotonic
        if gap >= self.cfg.sleep_gap_s:
            return gap
        return 0.0

    def wall_gap(self, snapshot: Snapshot, last_ts: float | None, last_monotonic: float | None) -> float:
        """Wall-clock time skipped while the steady clock stood still (suspend)."""
        if last_ts is None or last_monotonic is None:
            return 0.0
        wall = snapshot.ts - last_ts
        steady = snapshot.monotonic - last_monotonic
        skipped = wall - steady
        return skipped if skipped >= self.cfg.sleep_gap_s else 0.0

    def verdict(self, snapshot: Snapshot) -> IdleVerdict:
        if snapshot.locked and self.cfg.treat_lock_as_idle:
            return IdleVerdict(True, IDLE_LOCKED, MODE_LOCKED, snapshot.ts)
        if snapshot.screensaver and self.cfg.treat_screensaver_as_idle:
            return IdleVerdict(True, IDLE_SCREENSAVER, MODE_SCREENSAVER, snapshot.ts)

        idle_s = max(0.0, snapshot.idle_ms / 1000.0)
        if idle_s >= self.cfg.threshold_s:
            # Backdate to the moment input actually stopped.
            return IdleVerdict(True, IDLE_INPUT, MODE_IDLE, snapshot.ts - idle_s)
        return IdleVerdict(False, "", MODE_ACTIVE, snapshot.ts)

    def resume_ts(self, snapshot: Snapshot) -> float:
        """When activity resumed, given a snapshot that is no longer idle.

        Input arrived idle_ms ago, so work restarted then -- not at this tick.
        """
        return snapshot.ts - max(0.0, snapshot.idle_ms / 1000.0)


def reason_label(reason: str) -> str:
    return {
        IDLE_INPUT: "Away from keyboard",
        IDLE_LOCKED: "Screen locked",
        IDLE_SCREENSAVER: "Screensaver",
        IDLE_SLEEP: "Machine asleep",
        "paused": "Paused",
        "shutdown": "Not running",
    }.get(reason, reason.replace("_", " ").title())
