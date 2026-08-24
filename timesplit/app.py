"""Wiring it all together and running it.

Threads, and what each is for:

  main      the tray's message loop (blocked in the OS, costs nothing)
  tracker   polls the foreground window and owns every database write
  uia       resolves browser addresses without ever blocking the tracker
  web       only exists while the dashboard is actually open

The tracker thread is the only writer, which is why there is no queue or
lock discipline to get wrong beyond the database's own.
"""

from __future__ import annotations

import contextlib
import signal
import threading
import time
from pathlib import Path

from . import paths
from .config import Config, load_config, save_config
from .core import retention
from .core.aggregate import summary_line, totals_for_day
from .core.clock import SystemClock, local_day
from .core.features import activity_from_row
from .core.models import (
    Activity,
    CloseSession,
    OpenSession,
    RecordIdle,
    SessionRecord,
    UpdateSession,
)
from .core.tracker import SessionTracker
from .engine import Engine
from .logging_setup import get as get_logger
from .logging_setup import setup as setup_logging
from .store import repo_sessions
from .store.db import open_database

log = get_logger("app")


class Application:
    def __init__(self, cfg: Config, *, demo: bool = False):
        self.cfg = cfg
        self.demo = demo
        self.clock = SystemClock(cfg.timezone)
        self.db = open_database()
        self.engine = Engine(self.db, cfg)
        self.tracker = SessionTracker(cfg, self.engine.tz)
        self.capture = None
        self.tray = None
        self.server = None
        self.url_resolver = None
        self._stop = threading.Event()
        self._tracker_thread: threading.Thread | None = None
        self._live_session_id: int | None = None
        self._last_status = ""
        self._last_maintenance = 0.0

    # ---- lifecycle -----------------------------------------------------

    def start(self, *, tray: bool = True, open_dashboard: bool = False) -> None:
        from .backends.factory import get_capture, get_tray
        from .web.server import DashboardServer

        # Anything left open by a crash is closed at its last heartbeat, not
        # extended to now -- otherwise a power cut would bill days of work.
        recovered = repo_sessions.finalize_open_sessions(self.db, self.clock.now())
        if recovered:
            log.info("closed %d session(s) left open by a previous run", recovered)

        self.capture = self._build_capture() if self.demo else get_capture(self.cfg)
        self.capture.start()
        self._start_url_resolver()

        self.server = DashboardServer(self.engine, self.cfg)

        self._tracker_thread = threading.Thread(
            target=self._tracker_loop, name="timesplit-tracker", daemon=True
        )
        self._tracker_thread.start()

        if open_dashboard:
            url = self.server.open_in_browser()
            log.info("dashboard at %s", url)

        if tray:
            self.tray = get_tray(self._tray_actions())
            self._install_signal_handlers()
            self.tray.run()  # blocks until quit
            self.stop()
        else:
            self._install_signal_handlers()
            try:
                while not self._stop.wait(0.5):
                    pass
            except KeyboardInterrupt:
                pass
            self.stop()

    def _install_signal_handlers(self) -> None:
        def handle(_signum, _frame):
            self._stop.set()
            if self.tray is not None:
                self.tray.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            # Fails harmlessly when not on the main thread.
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, handle)

    def stop(self) -> None:
        if self._stop.is_set() and self._tracker_thread is None:
            return
        self._stop.set()
        thread, self._tracker_thread = self._tracker_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._apply(self.tracker.shutdown(self.clock.now()))
        if self.url_resolver is not None:
            self.url_resolver.stop()
        if self.capture is not None:
            self.capture.stop()
        if self.server is not None:
            self.server.stop()
        self.db.close()
        log.info("stopped")

    # ---- the poll loop -------------------------------------------------

    def _tracker_loop(self) -> None:
        interval = self.cfg.sampling.interval_s
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._tick()
            except Exception:
                log.exception("tracker tick failed")
            # Sleep the remainder of the interval, so a slow tick does not drift.
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.2, interval - elapsed))

    def _tick(self) -> None:
        assert self.capture is not None
        snapshot = self.capture.capture()

        if self.url_resolver is not None and snapshot.exe_name:
            url = self.url_resolver.lookup(snapshot)
            if url:
                snapshot = snapshot.with_url(url)

        ops = self.tracker.feed(snapshot)
        self._apply(ops)

        # A late-arriving address patches the session already open.
        if self.url_resolver is not None:
            resolved = self.url_resolver.take_resolved(snapshot.hwnd, snapshot.title)
            if resolved:
                from .core.urls import registrable_domain

                self._apply(
                    self.tracker.attach_url(resolved, registrable_domain(resolved))
                )

        self._maybe_maintenance()
        self._update_tray()

    # ---- applying tracker output ---------------------------------------

    def _apply(self, ops) -> None:
        for op in ops:
            try:
                self._apply_one(op)
            except Exception:
                log.exception("could not apply %s", type(op).__name__)

    def _apply_one(self, op) -> None:
        if isinstance(op, OpenSession):
            self._open_session(op.session)
        elif isinstance(op, UpdateSession):
            if self._live_session_id is None:
                return
            repo_sessions.update_session_end(self.db, self._live_session_id, op.end_ts)
            if op.url:
                repo_sessions.set_url(self.db, self._live_session_id, op.url, op.domain)
            if op.reclassify:
                self._reclassify_live()
        elif isinstance(op, CloseSession):
            if self._live_session_id is None:
                return
            if op.discard:
                repo_sessions.delete_session(self.db, self._live_session_id)
            else:
                repo_sessions.close_session(self.db, self._live_session_id, op.end_ts)
            self._live_session_id = None
        elif isinstance(op, RecordIdle):
            repo_sessions.insert_idle(self.db, op.idle)

    def _open_session(self, record: SessionRecord) -> None:
        from .core.urls import registrable_domain

        if record.url and not record.domain:
            record.domain = registrable_domain(record.url)
        activity = Activity(
            exe_name=record.exe_name, title=record.title,
            url=record.url, domain=record.domain,
        )
        decision = self.engine.classify(activity)

        if decision.source == "excluded":
            # Recorded as a block of time with no content at all.
            record.title = "[not recorded]"
            record.url = None
            record.domain = None

        record.category_id = decision.category_id
        record.confidence = decision.confidence
        record.source = decision.source
        record.rule_id = decision.rule_id
        record.needs_review = decision.needs_review
        record.local_day = record.local_day or local_day(record.start_ts, self.engine.tz)

        is_browser = record.exe_name.lower() in self.cfg.browser.browser_exes
        session_id = repo_sessions.insert_session(self.db, record, is_browser=is_browser)
        self._live_session_id = None if record.closed else session_id

    def _reclassify_live(self) -> None:
        if self._live_session_id is None:
            return
        row = repo_sessions.session(self.db, self._live_session_id)
        if row is None or row["is_locked"]:
            return
        decision = self.engine.classify(activity_from_row(row))
        repo_sessions.set_category(
            self.db, self._live_session_id, decision.category_id,
            confidence=decision.confidence, source=decision.source,
            rule_id=decision.rule_id, needs_review=decision.needs_review,
        )

    # ---- periodic work -------------------------------------------------

    def _maybe_maintenance(self) -> None:
        now = time.time()
        if now - self._last_maintenance < 3600:
            return
        self._last_maintenance = now
        try:
            today = local_day(now, self.engine.tz)
            retention.prune(self.db, self.cfg.retention, today)
            retention.vacuum_if_due(self.db, self.cfg.retention)
            self.engine.prune_vocabulary()
            self._maybe_llm_assist()
        except Exception:
            log.exception("maintenance failed")

    def _maybe_llm_assist(self) -> None:
        if not self.cfg.llm_assist.enabled:
            return
        from .llm import assist

        result = assist.run_once(self.engine)
        if result.get("ok"):
            log.info("Claude assist: %s", result)

    # ---- tray ----------------------------------------------------------

    def _update_tray(self) -> None:
        if self.tray is None:
            return
        try:
            totals = totals_for_day(self.db, local_day(self.clock.now(), self.engine.tz))
            summary = summary_line(totals)
            if summary != self._last_status:
                self._last_status = summary
                colour = totals.categories[0].color if totals.categories else None
                self.tray.set_status(summary, colour)
        except Exception:
            log.exception("could not refresh the tray")

    def _tray_actions(self) -> dict:
        def open_dashboard() -> None:
            assert self.server is not None
            self.server.open_in_browser()

        def toggle_pause() -> None:
            now = self.clock.now()
            ops = self.tracker.resume(now) if self.tracker.paused else self.tracker.pause(now)
            self._apply(ops)

        def pause_an_hour() -> None:
            self._apply(self.tracker.pause(self.clock.now()))
            threading.Timer(
                3600, lambda: self._apply(self.tracker.resume(self.clock.now()))
            ).start()

        def open_folder() -> None:
            import subprocess
            import sys

            folder = str(paths.data_dir())
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["explorer", folder])
                else:
                    subprocess.Popen(["xdg-open", folder])
            except OSError:
                log.warning("could not open %s", folder)

        def quit_app() -> None:
            self._stop.set()
            if self.tray is not None:
                self.tray.stop()

        return {
            "open_dashboard": open_dashboard,
            "toggle_pause": toggle_pause,
            "pause_an_hour": pause_an_hour,
            "open_folder": open_folder,
            "quit": quit_app,
            "status": lambda: self._last_status,
            "is_paused": lambda: self.tracker.paused,
        }

    # ---- browser URLs --------------------------------------------------

    def _start_url_resolver(self) -> None:
        if not self.cfg.browser.url_extraction:
            return
        if not getattr(self.capture, "supports_urls", False):
            return
        try:
            from .backends.win.uia_win import UrlResolver
        except Exception:
            return  # not on Windows, or uiautomation is not installed
        try:
            self.url_resolver = UrlResolver(self.cfg.browser)
            self.url_resolver.start()
        except Exception as exc:  # pragma: no cover - Windows only
            log.warning("browser address lookup unavailable: %s", exc)
            self.url_resolver = None

    # ---- demo ----------------------------------------------------------

    def _build_capture(self):
        """A synthetic workday, so the whole app can be seen without Windows."""
        from .backends.fake.capture_fake import ScriptedCaptureBackend
        from .core.clock import FakeClock
        from .demo import demo_script

        clock = FakeClock(start=self.clock.now() - 8 * 3600, zone=self.cfg.timezone or "UTC")
        self.clock = clock  # the app follows the simulated clock
        self.tracker = SessionTracker(self.cfg, self.engine.tz)
        return ScriptedCaptureBackend(clock, demo_script(), self.cfg.sampling.interval_s)


# ---- entry points --------------------------------------------------------


def _load() -> Config:
    paths.ensure_data_dir()
    return load_config()


def run_app(*, demo: bool = False, tray: bool = True, open_dashboard: bool = False,
            verbose: bool = False) -> int:
    cfg = _load()
    setup_logging(verbose or cfg.debug.verbose)

    if not cfg.first_run_complete and not demo:
        from .wizard.firstrun import run_wizard

        run_wizard()
        cfg = _load()

    from .backends.factory import is_windows

    if is_windows():
        from .backends.win.proc_win import claim_single_instance, lower_priority

        if not claim_single_instance():
            print("TimeSplit is already running.")
            return 1
        lower_priority()

    app = Application(cfg, demo=demo)
    if demo:
        return _run_demo(app)

    _freeze_gc()
    app.start(tray=tray, open_dashboard=open_dashboard)
    return 0


def _run_demo(app: Application) -> int:
    """Replay a day at full speed, then serve the dashboard."""
    from .backends.fake.capture_fake import ScriptedCaptureBackend

    app.capture = app._build_capture()
    app.capture.start()
    assert isinstance(app.capture, ScriptedCaptureBackend)

    ticks = 0
    while not app.capture.exhausted and ticks < 20_000:
        snapshot = app.capture.capture()
        if snapshot.unavailable and app.capture.exhausted:
            break
        app._apply(app.tracker.feed(snapshot))
        ticks += 1
    app._apply(app.tracker.shutdown(app.clock.now()))

    from .web.server import DashboardServer

    app.server = DashboardServer(app.engine, app.cfg)
    url = app.server.start()
    day = local_day(app.clock.now(), app.engine.tz)
    totals = totals_for_day(app.db, day)
    print(f"Replayed a synthetic day into {app.db.path}")
    print(f"  {summary_line(totals)}")
    print(f"\nDashboard: {url}\nPress Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    app.stop()
    return 0


def _freeze_gc() -> None:
    """Move the long-lived startup objects out of the collector's scan set."""
    import gc

    gc.collect()
    with contextlib.suppress(AttributeError):
        gc.freeze()


def serve_dashboard_only(*, port: int | None = None, open_browser: bool = True) -> int:
    cfg = _load()
    setup_logging(cfg.debug.verbose)
    db = open_database()
    engine = Engine(db, cfg)
    from .web.server import DashboardServer

    server = DashboardServer(engine, cfg, port=port)
    url = server.open_in_browser() if open_browser else server.start()
    print(f"Dashboard: {url}\nPress Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    server.stop()
    db.close()
    return 0


def run_export(*, date_from: str | None, date_to: str | None, fmt: str,
               out: str | None, exclude_unreviewed: bool) -> int:
    from .core.clock import add_days

    cfg = _load()
    setup_logging(cfg.debug.verbose)
    db = open_database()
    engine = Engine(db, cfg)
    today = local_day(time.time(), engine.tz)
    start = date_from or add_days(today, -29)
    end = date_to or today

    from .export import csv_export, xlsx_export

    if fmt == "xlsx":
        if not xlsx_export.available():
            print("Excel export needs openpyxl:  pip install openpyxl")
            db.close()
            return 1
        written = [
            xlsx_export.write_workbook(
                engine, start, end, out=out,
                rounding_min=cfg.export.rounding_min,
                exclude_unreviewed=exclude_unreviewed or cfg.export.exclude_unreviewed,
            )
        ]
    else:
        written = csv_export.write_all(
            engine, start, end, out=out,
            rounding_min=cfg.export.rounding_min,
            exclude_unreviewed=exclude_unreviewed or cfg.export.exclude_unreviewed,
        )
    for path in written:
        print(path)
    db.close()
    return 0


def run_autostart(*, install: bool) -> int:
    from .backends.factory import get_autostart

    manager = get_autostart()
    ok, message = manager.install() if install else manager.uninstall()
    print(message)
    return 0 if ok else 1


def save(cfg: Config, path: Path | None = None) -> Path:
    return save_config(cfg, path)
