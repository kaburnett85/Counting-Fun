"""Configuration: dataclasses with defaults, tolerant JSON load, atomic save.

Every field has a default, so a missing, partial, or hand-mangled config.json
still boots. Unknown keys are preserved on save rather than silently dropped,
which keeps a newer config readable by an older build.

The API key is deliberately NOT part of this file -- see backends SecretStore.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from . import paths

SCHOOL = "school"
REAL_ESTATE = "real_estate"
UNKNOWN = "unknown"
EXCLUDED = "excluded"


@dataclass
class CategoryConfig:
    key: str
    display_name: str
    color: str
    description: str = ""
    is_billable: bool = True


@dataclass
class SamplingConfig:
    #: Seconds between foreground-window probes. The probe costs ~0.3ms, so even
    #: 1s is cheap; 3s keeps worst-case misattribution to one tick per focus change.
    interval_s: int = 3
    #: How often the in-progress session's end time is flushed to disk. Bounds
    #: data loss on a hard power-off.
    heartbeat_s: int = 30
    #: Sessions shorter than this are discarded -- collapses alt-tab flicker.
    min_session_s: int = 5
    #: Long uninterrupted focus is split at this boundary so each piece stays
    #: individually reclassifiable.
    max_session_s: int = 3600


@dataclass
class IdleConfig:
    #: No keyboard/mouse input for this long means you are away.
    threshold_s: int = 180
    treat_lock_as_idle: bool = True
    treat_screensaver_as_idle: bool = True
    #: A monotonic-clock jump larger than this means the machine slept or the
    #: process was starved. Never credit that span to a job.
    sleep_gap_s: int = 30


@dataclass
class BrowserConfig:
    url_extraction: bool = True
    #: Chromium-based browsers whose address bar we read via UI Automation.
    browser_exes: list[str] = field(default_factory=lambda: ["chrome.exe", "comet.exe"])
    cache_ttl_s: int = 30
    cache_size: int = 64
    #: Consecutive failures before URL extraction is disabled for that browser.
    max_failures: int = 5


@dataclass
class ClassifierConfig:
    enabled: bool = True
    #: Posterior at or above this assigns the category outright.
    tau_assign: float = 0.75
    #: Between tau_review and tau_assign we assign but flag for review.
    tau_review: float = 0.55
    #: Naive Bayes smoothing. Below 1.0 because these vocabularies are small.
    alpha: float = 0.2
    #: The model stays out of the way until it has seen this many corrections.
    min_docs: int = 20
    max_vocab: int = 20000


@dataclass
class LLMAssistConfig:
    enabled: bool = False
    model: str = "claude-opus-5"
    min_batch: int = 10
    max_batch: int = 40
    min_interval_s: int = 3600
    daily_request_cap: int = 12
    monthly_cost_cap_usd: float = 2.00
    #: Registrable domains are always sent; full URLs only if you opt in here.
    send_urls: bool = False
    #: Minimum confidence before a suggestion becomes a rule.
    rule_threshold: float = 0.8
    max_consecutive_failures: int = 3


@dataclass
class DashboardConfig:
    port: int = 8756
    #: The server shuts itself down after this many minutes without a request.
    auto_shutdown_min: int = 10
    open_browser: bool = True


@dataclass
class PrivacyConfig:
    store_full_urls: bool = True
    title_max_len: int = 300
    #: Windows from these programs are never recorded (time still counts).
    exclude_exes: list[str] = field(
        default_factory=lambda: ["keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe"]
    )
    #: Case-insensitive substrings; a matching title is excluded.
    exclude_title_patterns: list[str] = field(
        default_factory=lambda: ["incognito", "inprivate", "private browsing"]
    )


@dataclass
class RetentionConfig:
    sessions_days: int = 730
    idle_days: int = 90
    samples_days: int = 7
    llm_days: int = 180
    vacuum_interval_days: int = 30


@dataclass
class ExportConfig:
    #: Round invoice hours to this many minutes. 0 disables rounding.
    rounding_min: int = 0
    exclude_unreviewed: bool = False


@dataclass
class DebugConfig:
    record_samples: bool = False
    #: Writes window titles and URLs into the log file. Off for a reason.
    verbose: bool = False


@dataclass
class Config:
    categories: list[CategoryConfig] = field(
        default_factory=lambda: [
            CategoryConfig(SCHOOL, "School", "#3b7dd8"),
            CategoryConfig(REAL_ESTATE, "Real Estate", "#2f9e63"),
        ]
    )
    timezone: str = ""  # empty means "use the system zone"
    week_start: str = "monday"
    first_run_complete: bool = False
    sampling: SamplingConfig = field(default_factory=SamplingConfig)
    idle: IdleConfig = field(default_factory=IdleConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    llm_assist: LLMAssistConfig = field(default_factory=LLMAssistConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)

    # ---- helpers -------------------------------------------------------

    def category_keys(self) -> list[str]:
        return [c.key for c in self.categories]

    def category(self, key: str) -> CategoryConfig | None:
        for c in self.categories:
            if c.key == key:
                return c
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path | None = None) -> Path:
        return save_config(self, path)


def _coerce(cls: type, value: Any) -> Any:
    """Build a dataclass from a dict, ignoring unknown keys and bad types."""
    if not isinstance(value, dict):
        return cls()
    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for name, f in known.items():
        if name not in value:
            continue
        raw = value[name]
        if is_dataclass(f.type) if isinstance(f.type, type) else False:
            kwargs[name] = _coerce(f.type, raw)  # pragma: no cover - no nesting today
        else:
            kwargs[name] = raw
    try:
        return cls(**kwargs)
    except TypeError:
        return cls()


_SECTIONS: dict[str, type] = {
    "sampling": SamplingConfig,
    "idle": IdleConfig,
    "browser": BrowserConfig,
    "classifier": ClassifierConfig,
    "llm_assist": LLMAssistConfig,
    "dashboard": DashboardConfig,
    "privacy": PrivacyConfig,
    "retention": RetentionConfig,
    "export": ExportConfig,
    "debug": DebugConfig,
}


def from_dict(data: dict[str, Any]) -> Config:
    cfg = Config()
    if not isinstance(data, dict):
        return cfg
    for name, cls in _SECTIONS.items():
        if name in data:
            setattr(cfg, name, _coerce(cls, data[name]))
    cats = data.get("categories")
    if isinstance(cats, list) and cats:
        parsed = [_coerce(CategoryConfig, c) for c in cats if isinstance(c, dict)]
        parsed = [c for c in parsed if getattr(c, "key", "")]
        if parsed:
            cfg.categories = parsed
    for scalar in ("timezone", "week_start", "first_run_complete"):
        if scalar in data:
            setattr(cfg, scalar, data[scalar])
    return validate(cfg)


def validate(cfg: Config) -> Config:
    """Clamp anything that would make the app misbehave rather than refusing to start."""
    s = cfg.sampling
    s.interval_s = max(1, min(60, int(s.interval_s)))
    s.heartbeat_s = max(s.interval_s, int(s.heartbeat_s))
    s.min_session_s = max(0, int(s.min_session_s))
    s.max_session_s = max(60, int(s.max_session_s))

    i = cfg.idle
    i.threshold_s = max(30, int(i.threshold_s))
    i.sleep_gap_s = max(s.interval_s * 2, int(i.sleep_gap_s))

    c = cfg.classifier
    c.tau_assign = min(0.999, max(0.5, float(c.tau_assign)))
    c.tau_review = min(c.tau_assign, max(0.0, float(c.tau_review)))
    c.alpha = max(0.001, float(c.alpha))
    c.min_docs = max(0, int(c.min_docs))

    d = cfg.dashboard
    d.port = int(d.port) if 1024 <= int(d.port) <= 65535 else 8756

    cfg.privacy.title_max_len = max(20, int(cfg.privacy.title_max_len))
    cfg.privacy.exclude_exes = [e.lower() for e in cfg.privacy.exclude_exes]
    cfg.browser.browser_exes = [e.lower() for e in cfg.browser.browser_exes]

    if cfg.export.rounding_min not in (0, 6, 15, 30):
        cfg.export.rounding_min = 0
    return cfg


def load_config(path: Path | None = None) -> Config:
    p = path or paths.config_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return Config()
    except OSError:
        return Config()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # A corrupt config must not stop time tracking. Keep the bad file for
        # inspection and carry on with defaults.
        try:
            p.with_suffix(".json.bad").write_text(raw, encoding="utf-8")
        except OSError:
            pass
        return Config()
    return from_dict(data)


def save_config(cfg: Config, path: Path | None = None) -> Path:
    """Write atomically: a crash mid-write can never leave a truncated config."""
    p = path or paths.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.to_dict(), indent=2, sort_keys=False)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p
