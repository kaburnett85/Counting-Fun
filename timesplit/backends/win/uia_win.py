"""Reading the address bar of Chrome and Perplexity Comet.

Both are Chromium, so one path covers both. The address bar is an Edit control
exposing a ValuePattern; it is found by three strategies in order, because a
Chromium fork may not keep the upstream automation id:

  1. AutomationId "addressEditBox"          (upstream Chromium / Edge)
  2. a control named like an address bar    (localised names still usually match)
  3. the first ValuePattern-bearing Edit    (what actually makes forks work)

Four things keep this cheap enough to run all day:

* It is only attempted when the foreground program is one of the browsers.
* Results are cached on (window, title). A Chromium window title changes on
  every tab switch and every navigation, so the title is very nearly a perfect
  cache key -- in practice this is a few dozen real lookups an hour.
* **It never blocks the tracker.** A miss is queued, a worker thread resolves
  it, and the session is patched when the answer arrives. A hung COM call
  therefore stalls one background thread and nothing else, which matters
  because an in-flight COM call cannot be reliably timed out.
* The resolved element is cached per window, so repeat lookups skip the search.

If any of it fails, the tracker carries on with window titles alone. Titles are
still informative ("Purchase Agreement - dotloop"), so accuracy degrades but
nothing breaks.
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from collections import OrderedDict

from ...config import BrowserConfig
from ...core.models import Snapshot
from ...logging_setup import get as get_logger

log = get_logger("backends.uia")

ADDRESS_AUTOMATION_IDS = ("addressEditBox", "urlbar-input", "view_1022")
ADDRESS_NAME_HINTS = (
    "address and search bar",
    "address bar",
    "search or enter",
    "search or type",
    "address field",
)
SEARCH_DEPTH = 8


class UrlResolver:
    def __init__(self, cfg: BrowserConfig):
        self.cfg = cfg
        self.browsers = {e.lower() for e in cfg.browser_exes}
        self._cache: OrderedDict[tuple[int, str], tuple[str, float]] = OrderedDict()
        self._queue: queue.Queue[tuple[int, str]] = queue.Queue(maxsize=8)
        self._resolved: dict[tuple[int, str], str] = {}
        self._elements: dict[int, object] = {}
        self._failures: dict[str, int] = {}
        self._disabled: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.available = False

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        try:
            import uiautomation  # noqa: F401
        except ImportError:
            log.info("uiautomation is not installed; tracking window titles only")
            return
        self.available = True
        self._thread = threading.Thread(target=self._worker, name="timesplit-uia", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait((0, ""))

    # ---- the tracker's side (never blocks) -----------------------------

    def lookup(self, snapshot: Snapshot) -> str | None:
        """Return a cached address, or queue a lookup and return None."""
        exe = (snapshot.exe_name or "").lower()
        if not self.available or exe not in self.browsers or exe in self._disabled:
            return None
        if not snapshot.hwnd:
            return None

        key = (snapshot.hwnd, snapshot.title)
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                url, stamp = hit
                if time.monotonic() - stamp <= self.cfg.cache_ttl_s:
                    self._cache.move_to_end(key)
                    return url
                del self._cache[key]

        # If the worker is busy, this tick simply stays title-only.
        with contextlib.suppress(queue.Full):
            self._queue.put_nowait(key)
        return None

    def take_resolved(self, hwnd: int, title: str) -> str | None:
        """Collect an address the worker resolved since the last tick."""
        with self._lock:
            return self._resolved.pop((hwnd, title), None)

    # ---- the worker's side ---------------------------------------------

    def _worker(self) -> None:
        import comtypes  # noqa: F401
        import uiautomation

        # Not present in every uiautomation release.
        with contextlib.suppress(Exception):
            uiautomation.SetGlobalSearchTimeout(1.0)

        while not self._stop.is_set():
            try:
                key = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if self._stop.is_set() or key == (0, ""):
                break
            hwnd, title = key
            try:
                url = self._resolve(uiautomation, hwnd)
            except Exception as exc:
                url = None
                log.debug("address lookup failed: %s", exc)
            if url:
                with self._lock:
                    self._cache[key] = (url, time.monotonic())
                    while len(self._cache) > self.cfg.cache_size:
                        self._cache.popitem(last=False)
                    self._resolved[key] = url
                    if len(self._resolved) > 32:
                        self._resolved.clear()

    def _resolve(self, uiautomation, hwnd: int) -> str | None:
        control = self._elements.get(hwnd)
        if control is not None:
            url = _read_value(control)
            if url:
                return _normalise(url)
            self._elements.pop(hwnd, None)

        window = uiautomation.ControlFromHandle(hwnd)
        if window is None:
            return None

        edit = _find_address_bar(uiautomation, window)
        if edit is None:
            return None
        self._elements[hwnd] = edit
        if len(self._elements) > 24:
            self._elements.clear()
        value = _read_value(edit)
        return _normalise(value) if value else None

    def note_failure(self, exe: str) -> None:
        """Give up on a browser that keeps failing, rather than retrying forever."""
        count = self._failures.get(exe, 0) + 1
        self._failures[exe] = count
        if count >= self.cfg.max_failures:
            self._disabled.add(exe)
            log.info("address lookup disabled for %s after %d failures", exe, count)


def _find_address_bar(uiautomation, window):
    """Three strategies, most specific first."""
    for automation_id in ADDRESS_AUTOMATION_IDS:
        try:
            control = window.EditControl(AutomationId=automation_id, searchDepth=SEARCH_DEPTH)
            if control.Exists(0.4, 0.1):
                return control
        except Exception:
            continue

    for hint in ADDRESS_NAME_HINTS:
        try:
            control = window.EditControl(
                searchDepth=SEARCH_DEPTH,
                Compare=lambda c, _d, h=hint: h in (c.Name or "").lower(),
            )
            if control.Exists(0.4, 0.1):
                return control
        except Exception:
            continue

    # Last resort, and what generally makes an unfamiliar Chromium fork work.
    try:
        control = window.EditControl(searchDepth=SEARCH_DEPTH)
        if control.Exists(0.4, 0.1) and _read_value(control):
            return control
    except Exception:
        return None
    return None


def _read_value(control) -> str | None:
    try:
        pattern = control.GetValuePattern()
    except Exception:
        return None
    try:
        return pattern.Value
    except Exception:
        return None


def _normalise(value: str) -> str | None:
    """The omnibox shows a display form; turn it into a real address."""
    text = (value or "").strip()
    if not text or " " in text.strip() and "://" not in text:
        # A search term, not an address.
        return None
    if text.startswith(("http://", "https://")):
        return text
    if "." not in text.split("/")[0]:
        return None
    return "https://" + text
