"""The session state machine.

``SessionTracker.feed(snapshot)`` is a pure function of (previous state,
snapshot). It performs no I/O and never reads a clock -- it returns a list of
operations describing what should happen, and the application applies them to
the database. That is what lets the tests replay a simulated workday, complete
with idle, lock, suspend and midnight, in a millisecond.

Invariant the tests enforce: for any snapshot sequence, the recorded session
time plus the recorded idle time equals the wall time observed, sessions never
overlap, and no session spans local midnight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from zoneinfo import ZoneInfo

from ..config import Config
from .clock import local_day, next_midnight
from .idle import (
    MODE_ACTIVE,
    MODE_PAUSED,
    IdlePolicy,
    IdleVerdict,
)
from .models import (
    IDLE_PAUSED,
    IDLE_SHUTDOWN,
    IDLE_SLEEP,
    CloseSession,
    IdleRecord,
    Op,
    OpenSession,
    RecordIdle,
    SessionRecord,
    Snapshot,
    TrackerStatus,
    UpdateSession,
)


@dataclass(slots=True)
class _Live:
    """The session currently in progress."""

    record: SessionRecord
    #: Last moment we have positive evidence the user was present.
    last_active_ts: float
    #: Last end_ts flushed to the database.
    flushed_ts: float
    persisted: bool = False


@dataclass(slots=True)
class TrackerState:
    mode: str = MODE_ACTIVE
    live: _Live | None = None
    last_ts: float | None = None
    last_monotonic: float | None = None
    away_since: float | None = None
    away_reason: str = ""
    #: Start of a session that was discarded as flicker. The seconds it covered
    #: still happened, so whoever closed it decides where they go rather than
    #: letting them silently vanish from the day.
    discarded_start: float | None = None
    #: The earliest timestamp not yet attributed to anything. Backdating (an
    #: idle trim, a resume) may never reach behind this, or the app would claim
    #: time it has already accounted for -- or time from before it was running.
    floor_ts: float | None = None
    pending: list[Op] = field(default_factory=list)


class SessionTracker:
    def __init__(self, cfg: Config, tz: timezone | ZoneInfo):
        self.cfg = cfg
        self.tz = tz
        self.idle_policy = IdlePolicy(cfg.idle)
        self.state = TrackerState()
        self._paused = False

    # ---- public API ----------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self, ts: float) -> list[Op]:
        """Stop attributing time. The paused span is recorded explicitly, so a
        day still adds up rather than quietly missing hours."""
        if self._paused:
            return []
        self._paused = True
        ops = self._close_live(ts)
        self.state.mode = MODE_PAUSED
        self.state.away_since = ts
        self.state.away_reason = IDLE_PAUSED
        return ops

    def resume(self, ts: float) -> list[Op]:
        if not self._paused:
            return []
        self._paused = False
        ops = self._flush_away(ts)
        self.state.mode = MODE_ACTIVE
        return ops

    def shutdown(self, ts: float) -> list[Op]:
        """Close cleanly on exit so nothing is left open for the next start."""
        ops = self._close_live(ts)
        if self.state.discarded_start is not None:
            # A sliver was in progress when we stopped. Record it as
            # not-running rather than letting the day come up short.
            start = self.state.discarded_start
            self.state.discarded_start = None
            if self.state.away_since is None or start < self.state.away_since:
                ops.extend(self._idle_ops(start, ts, IDLE_SHUTDOWN))
                self.state.away_since = None
        if self.state.away_since is not None:
            ops.extend(self._flush_away(ts))
        return ops

    def feed(self, snapshot: Snapshot) -> list[Op]:
        ops: list[Op] = []
        st = self.state

        # 1. Did the machine sleep, hibernate, or get starved of CPU?
        gap = self.idle_policy.wall_gap(snapshot, st.last_ts, st.last_monotonic)
        if gap <= 0:
            steady_gap = self.idle_policy.sleep_gap(snapshot, st.last_monotonic)
            # A steady-clock gap this large means we simply were not running.
            gap = steady_gap if steady_gap else 0.0
        if gap > 0 and st.last_ts is not None:
            ops.extend(self._handle_gap(st.last_ts, snapshot.ts, gap))

        if st.floor_ts is None:
            # We cannot speak for time before our first observation.
            st.floor_ts = snapshot.ts
        st.last_ts = snapshot.ts
        st.last_monotonic = snapshot.monotonic

        if self._paused:
            return ops

        # 2. The backend could not read the foreground window (secure desktop,
        #    a transient failure). Treat it as away rather than guessing.
        if snapshot.unavailable:
            ops.extend(self._go_away(IdleVerdict(True, "idle", "idle", snapshot.ts)))
            return ops

        # 3. Locked, screensaver, or no input for long enough?
        verdict = self.idle_policy.verdict(snapshot)
        if verdict.away:
            ops.extend(self._go_away(verdict))
            return ops

        # 4. Active. If we were away, close out that period first.
        if st.away_since is not None:
            resume_at = max(self.idle_policy.resume_ts(snapshot), st.away_since)
            ops.extend(self._flush_away(resume_at))
            st.mode = MODE_ACTIVE
            ops.extend(self._start_session(snapshot, start_ts=resume_at))
            return ops

        st.mode = MODE_ACTIVE

        # 5. Same window as last tick?
        live = st.live
        if live is None:
            ops.extend(self._start_session(snapshot, start_ts=snapshot.ts))
            return ops

        if self._focus_changed(live.record, snapshot):
            ops.extend(self._close_live(snapshot.ts))
            ops.extend(self._start_session(snapshot, start_ts=snapshot.ts))
            return ops

        # 6. Same window: extend it, splitting at midnight and at max length.
        ops.extend(self._extend(snapshot))
        return ops

    def status(self) -> TrackerStatus:
        live = self.state.live
        return TrackerStatus(
            mode=self.state.mode,
            current_exe=live.record.exe_name if live else "",
            current_title=live.record.title if live else "",
            session_start_ts=live.record.start_ts if live else None,
        )

    def attach_url(self, url: str | None, domain: str | None) -> list[Op]:
        """A URL resolved after the fact (the UIA worker is asynchronous).

        Patching the live session rather than dropping the URL is what keeps
        the first few seconds of a page visit correctly attributed.
        """
        live = self.state.live
        if live is None or not url or live.record.url == url:
            return []
        live.record.url = url
        live.record.domain = domain
        if not live.persisted:
            return []
        return [UpdateSession(end_ts=live.record.end_ts, url=url, domain=domain, reclassify=True)]

    # ---- internals -----------------------------------------------------

    def _focus_changed(self, record: SessionRecord, snapshot: Snapshot) -> bool:
        if record.exe_name.lower() != snapshot.exe_name.lower():
            return True
        if record.title != snapshot.title:
            return True
        # A URL arriving late is a patch, not a new session.
        return bool(snapshot.url and record.url and snapshot.url != record.url)

    def _start_session(self, snapshot: Snapshot, start_ts: float) -> list[Op]:
        # A window we flicked through for two seconds was still time at the
        # machine. Rather than drop it, the activity that follows absorbs it.
        carried = self.state.discarded_start
        if carried is not None:
            if carried <= start_ts:
                start_ts = carried
            self.state.discarded_start = None
        if self.state.floor_ts is not None:
            start_ts = max(start_ts, self.state.floor_ts)
        record = SessionRecord(
            start_ts=start_ts,
            end_ts=max(start_ts, snapshot.ts),
            exe_name=snapshot.exe_name,
            exe_path=snapshot.exe_path,
            title=snapshot.title[: self.cfg.privacy.title_max_len],
            url=snapshot.url,
            local_day=local_day(start_ts, self.tz),
        )
        self.state.live = _Live(record=record, last_active_ts=snapshot.ts, flushed_ts=start_ts)
        # Nothing is written yet: a session shorter than min_session_s never
        # reaches the database at all.
        return []

    def _extend(self, snapshot: Snapshot) -> list[Op]:
        live = self.state.live
        assert live is not None
        ops: list[Op] = []
        record = live.record
        live.last_active_ts = snapshot.ts

        # Split at local midnight so local_day is never ambiguous.
        boundary = next_midnight(record.start_ts, self.tz)
        if snapshot.ts >= boundary:
            ops.extend(self._close_live(boundary))
            resumed = Snapshot(
                ts=snapshot.ts,
                monotonic=snapshot.monotonic,
                exe_name=snapshot.exe_name,
                exe_path=snapshot.exe_path,
                title=snapshot.title,
                hwnd=snapshot.hwnd,
                url=snapshot.url,
            )
            ops.extend(self._start_session(resumed, start_ts=boundary))
            live2 = self.state.live
            if live2 is not None:
                live2.record.end_ts = snapshot.ts
                live2.last_active_ts = snapshot.ts
                ops.extend(self._maybe_flush(live2, snapshot.ts))
            return ops

        # Split long uninterrupted focus so each piece stays reclassifiable.
        if snapshot.ts - record.start_ts >= self.cfg.sampling.max_session_s:
            ops.extend(self._close_live(snapshot.ts))
            ops.extend(self._start_session(snapshot, start_ts=snapshot.ts))
            return ops

        record.end_ts = snapshot.ts
        ops.extend(self._maybe_flush(live, snapshot.ts))
        return ops

    def _maybe_flush(self, live: _Live, now_ts: float) -> list[Op]:
        """Persist the in-progress session periodically.

        Bounds what a power cut can lose to one heartbeat, and is why an
        unclosed row can be trusted at next startup.
        """
        record = live.record
        duration = record.end_ts - record.start_ts
        if not live.persisted:
            if duration >= self.cfg.sampling.min_session_s:
                live.persisted = True
                live.flushed_ts = record.end_ts
                return [OpenSession(record)]
            return []
        if record.end_ts - live.flushed_ts >= self.cfg.sampling.heartbeat_s:
            live.flushed_ts = record.end_ts
            return [UpdateSession(end_ts=record.end_ts)]
        return []

    def _close_live(self, end_ts: float) -> list[Op]:
        live = self.state.live
        if live is None:
            return []
        self.state.live = None
        record = live.record
        # end_ts can move backwards here: an idle trim ends the session at the
        # last moment of real input, which is earlier than the last tick.
        record.end_ts = max(record.start_ts, end_ts)
        duration = record.end_ts - record.start_ts
        self.state.floor_ts = record.end_ts

        if duration < self.cfg.sampling.min_session_s:
            # Too short to be real work -- alt-tab flicker. The span is handed
            # to the caller via discarded_start so the time is not simply lost,
            # which means the floor moves back to where it began.
            self.state.discarded_start = record.start_ts
            self.state.floor_ts = record.start_ts
            return [CloseSession(end_ts=record.end_ts, discard=True)] if live.persisted else []
        if not live.persisted:
            record.closed = True
            return [OpenSession(record)]
        return [CloseSession(end_ts=record.end_ts)]

    def _go_away(self, verdict: IdleVerdict) -> list[Op]:
        st = self.state
        ops: list[Op] = []
        if st.away_since is None:
            # Trim the session back to the last moment of real presence.
            live = st.live
            end_at = verdict.since_ts
            if live is not None:
                end_at = max(live.record.start_ts, min(verdict.since_ts, live.last_active_ts))
                end_at = max(end_at, live.record.start_ts)
                ops.extend(self._close_live(end_at))
            if st.discarded_start is not None:
                end_at = min(end_at, st.discarded_start)
                st.discarded_start = None
            if st.floor_ts is not None:
                end_at = max(end_at, st.floor_ts)
            st.away_since = end_at
            st.away_reason = verdict.reason
        elif verdict.reason and verdict.reason != st.away_reason:
            # Idle turned into locked, say. Keep the earliest start.
            st.away_reason = verdict.reason
        st.mode = verdict.mode
        return ops

    def _flush_away(self, until_ts: float) -> list[Op]:
        st = self.state
        if st.away_since is None:
            return []
        start, reason = st.away_since, st.away_reason or "idle"
        st.away_since = None
        st.away_reason = ""
        if until_ts <= start:
            return []
        return self._idle_ops(start, until_ts, reason)

    def _handle_gap(self, last_ts: float, now_ts: float, gap: float) -> list[Op]:
        """The machine was not running. Close at last-known-good, record the gap."""
        ops: list[Op] = []
        live = self.state.live
        anchor = last_ts
        if live is not None:
            anchor = max(live.record.start_ts, min(live.last_active_ts, last_ts))
            ops.extend(self._close_live(anchor))
        if self.state.discarded_start is not None:
            anchor = min(anchor, self.state.discarded_start)
            self.state.discarded_start = None
        if self.state.away_since is not None:
            ops.extend(self._flush_away(anchor))
        if now_ts > anchor:
            ops.extend(self._idle_ops(anchor, now_ts, IDLE_SLEEP))
        return ops

    def _idle_ops(self, start: float, end: float, reason: str) -> list[Op]:
        """Emit idle records, split at local midnight so day totals stay honest."""
        ops: list[Op] = []
        if self.state.floor_ts is not None:
            start = max(start, self.state.floor_ts)
        if end <= start:
            return ops
        self.state.floor_ts = end
        cursor = start
        guard = 0
        while cursor < end and guard < 400:
            guard += 1
            boundary = next_midnight(cursor, self.tz)
            stop = min(end, boundary)
            ops.append(
                RecordIdle(
                    IdleRecord(
                        start_ts=cursor,
                        end_ts=stop,
                        reason=reason,
                        local_day=local_day(cursor, self.tz),
                    )
                )
            )
            cursor = stop
        return ops
