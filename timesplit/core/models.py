"""Immutable value types shared across the app.

Nothing here imports Windows, sqlite, or anything else -- these are the plain
shapes that flow between the capture backend, the tracker, and the store.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

# Where a session's category came from, in precedence order.
SOURCE_MANUAL = "manual"  # the user set it by hand
SOURCE_USER_RULE = "user_rule"
SOURCE_LLM_RULE = "llm_rule"
SOURCE_SEED_RULE = "seed_rule"
SOURCE_MODEL = "model"  # the learned classifier
SOURCE_EXCLUDED = "excluded"
SOURCE_NONE = "none"  # nothing matched

# Why time was not attributed to a job.
IDLE_INPUT = "idle"
IDLE_LOCKED = "locked"
IDLE_SCREENSAVER = "screensaver"
IDLE_SLEEP = "sleep"
IDLE_PAUSED = "paused"
IDLE_SHUTDOWN = "shutdown"

RULE_PRIORITY_USER = 1000
RULE_PRIORITY_LLM = 500
RULE_PRIORITY_SEED = 100


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One observation of the machine, produced by a CaptureBackend.

    ``ts`` is wall-clock epoch seconds (used for storage and display).
    ``monotonic`` is a steady clock (used only to detect sleep gaps) -- these
    must come from the same probe so the tracker can compare them coherently.
    """

    ts: float
    monotonic: float
    exe_name: str = ""
    exe_path: str = ""
    title: str = ""
    hwnd: int = 0
    url: str | None = None
    idle_ms: int = 0
    locked: bool = False
    screensaver: bool = False
    #: True when the backend could not read the foreground window at all.
    unavailable: bool = False

    def focus_key(self) -> tuple[str, str, str]:
        return (self.exe_name.lower(), self.title, self.url or "")

    def with_url(self, url: str | None) -> Snapshot:
        return replace(self, url=url)


@dataclass(slots=True)
class SessionRecord:
    """A stretch of continuous work on one window."""

    start_ts: float
    end_ts: float
    exe_name: str = ""
    exe_path: str = ""
    title: str = ""
    url: str | None = None
    domain: str | None = None
    local_day: str = ""
    id: int | None = None
    category_id: int | None = None
    confidence: float = 0.0
    source: str = SOURCE_NONE
    rule_id: int | None = None
    is_locked: bool = False
    needs_review: bool = False
    closed: bool = False

    @property
    def duration_s(self) -> int:
        return max(0, int(round(self.end_ts - self.start_ts)))


@dataclass(slots=True)
class IdleRecord:
    start_ts: float
    end_ts: float
    reason: str
    local_day: str = ""
    id: int | None = None

    @property
    def duration_s(self) -> int:
        return max(0, int(round(self.end_ts - self.start_ts)))


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of classifying one activity."""

    category_id: int | None
    confidence: float = 0.0
    source: str = SOURCE_NONE
    rule_id: int | None = None
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class Rule:
    kind: str
    pattern: str
    category_id: int
    priority: int = RULE_PRIORITY_USER
    source: str = "user"
    id: int | None = None
    enabled: bool = True
    note: str = ""


@dataclass(frozen=True, slots=True)
class Activity:
    """What the classifier looks at. A session stripped down to its signals."""

    exe_name: str = ""
    title: str = ""
    url: str | None = None
    domain: str | None = None


# ---- tracker output -----------------------------------------------------
#
# The tracker is a pure function: it returns operations describing what should
# happen, and the application applies them. That is what makes an eight-hour
# simulated workday a millisecond-long unit test.


@dataclass(frozen=True, slots=True)
class OpenSession:
    session: SessionRecord


@dataclass(frozen=True, slots=True)
class UpdateSession:
    """Extend the live session (heartbeat) or patch a late-arriving URL."""

    end_ts: float
    url: str | None = None
    domain: str | None = None
    reclassify: bool = False


@dataclass(frozen=True, slots=True)
class CloseSession:
    end_ts: float
    #: Sessions below min_session_s are dropped instead of stored.
    discard: bool = False


@dataclass(frozen=True, slots=True)
class RecordIdle:
    idle: IdleRecord


Op = OpenSession | UpdateSession | CloseSession | RecordIdle


@dataclass(slots=True)
class TrackerStatus:
    """A cheap snapshot of tracker state, for the tray tooltip and doctor."""

    mode: str = "active"
    current_exe: str = ""
    current_title: str = ""
    current_category: str = ""
    session_start_ts: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)
