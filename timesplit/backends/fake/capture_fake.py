"""A capture backend driven by a script instead of by Windows.

This is what makes an eight-hour workday a millisecond-long unit test, and
what powers ``run --demo`` on a machine with no Windows at all.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...core.clock import FakeClock
from ...core.models import Snapshot


class ScriptedCaptureBackend:
    """Replays a list of (duration_s, window) steps against a FakeClock.

    Each step describes a window and how long it stays focused. The backend
    advances the clock by the sampling interval on every capture() call, so
    the tracker sees exactly the tick sequence it would see on real hardware.
    """

    supports_urls = True

    def __init__(
        self,
        clock: FakeClock,
        steps: Iterable[dict],
        interval_s: int = 3,
    ):
        self.clock = clock
        self.interval_s = interval_s
        self.steps = [dict(s) for s in steps]
        self._step_index = 0
        self._elapsed_in_step = 0.0
        self._started = False
        self._first = True
        self.exhausted = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    @property
    def supports_urls_flag(self) -> bool:
        return self.supports_urls

    def describe(self) -> dict[str, str]:
        return {"backend": "scripted", "steps": str(len(self.steps))}

    def _current(self) -> dict | None:
        if self._step_index >= len(self.steps):
            self.exhausted = True
            return None
        return self.steps[self._step_index]

    def capture(self) -> Snapshot:
        # Real backends are polled after the interval has elapsed; mirror that
        # so timestamps line up with what the tracker would see on Windows.
        if self._first:
            self._first = False
        else:
            self.clock.advance(self.interval_s)
            self._elapsed_in_step += self.interval_s

        step = self._current()
        while step is not None and self._elapsed_in_step >= float(step.get("duration_s", 0)):
            self._elapsed_in_step -= float(step.get("duration_s", 0))
            self._step_index += 1
            step = self._current()

        if step is None:
            return Snapshot(
                ts=self.clock.now(), monotonic=self.clock.monotonic(), unavailable=True
            )

        if step.get("suspend_s"):
            # The machine slept: wall time jumps, the steady clock does not.
            self.clock.suspend(float(step["suspend_s"]))

        return Snapshot(
            ts=self.clock.now(),
            monotonic=self.clock.monotonic(),
            exe_name=step.get("exe", ""),
            exe_path=step.get("exe_path", "C:\\Apps\\" + step.get("exe", "")),
            title=step.get("title", ""),
            hwnd=int(step.get("hwnd", 1)),
            url=step.get("url"),
            idle_ms=int(step.get("idle_ms", 0)),
            locked=bool(step.get("locked", False)),
            screensaver=bool(step.get("screensaver", False)),
        )


class ListCaptureBackend:
    """Replays an explicit list of Snapshots, in order. Used by precise tests."""

    supports_urls = True

    def __init__(self, snapshots: Iterable[Snapshot]):
        self._snapshots = list(snapshots)
        self._i = 0

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def describe(self) -> dict[str, str]:
        return {"backend": "list", "count": str(len(self._snapshots))}

    def capture(self) -> Snapshot:
        if self._i >= len(self._snapshots):
            last = self._snapshots[-1] if self._snapshots else Snapshot(0.0, 0.0)
            return Snapshot(ts=last.ts, monotonic=last.monotonic, unavailable=True)
        snap = self._snapshots[self._i]
        self._i += 1
        return snap

    @property
    def exhausted(self) -> bool:
        return self._i >= len(self._snapshots)
