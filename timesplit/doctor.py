"""Diagnosing an installation.

This is what to run first when something looks wrong -- and what tells us
whether Perplexity Comet's address bar behaves like upstream Chromium, which
cannot be determined from a Linux development machine.
"""

from __future__ import annotations

import platform
import sys
import time

from . import paths
from .config import load_config
from .core.clock import fmt_duration, local_day

WIN_MODULES = [
    "timesplit.backends.win.capture_win",
    "timesplit.backends.win.idle_win",
    "timesplit.backends.win.uia_win",
    "timesplit.backends.win.tray_win",
    "timesplit.backends.win.autostart_win",
    "timesplit.backends.win.dpapi_win",
    "timesplit.backends.win.proc_win",
]


def _section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def _line(label: str, value) -> None:
    print(f"  {label:<26} {value}")


def run_doctor() -> int:
    import timesplit

    problems: list[str] = []

    _section("TimeSplit")
    _line("version", timesplit.__version__)
    _line("python", f"{sys.version.split()[0]} ({sys.executable})")
    _line("platform", f"{platform.system()} {platform.release()}")
    _line("data folder", paths.data_dir())
    _line("config", paths.config_path())
    _line("database", paths.db_path())

    cfg = load_config()
    _section("Settings")
    _line("setup complete", "yes" if cfg.first_run_complete else "NO — run: timesplit wizard")
    _line("jobs", ", ".join(c.display_name for c in cfg.categories))
    _line("sample every", f"{cfg.sampling.interval_s}s")
    _line("idle after", f"{cfg.idle.threshold_s // 60} min")
    _line("browsers watched", ", ".join(cfg.browser.browser_exes) or "(none)")
    _line("Claude assist", "on" if cfg.llm_assist.enabled else "off")
    if not cfg.first_run_complete:
        problems.append("setup has not been run")

    _section("Database")
    try:
        from .core.retention import database_size_bytes
        from .engine import Engine
        from .store import repo_rules, repo_sessions
        from .store.db import open_database

        db = open_database()
        engine = Engine(db, cfg)
        _line("schema version", db.get_meta("schema_version"))
        _line("size", f"{database_size_bytes(db) / 1_048_576:.2f} MB")
        _line("sessions", db.scalar("SELECT COUNT(*) FROM sessions", default=0))
        _line("rules", repo_rules.count(db))
        days = repo_sessions.days_with_data(db)
        _line("days recorded", f"{len(days)}" + (f" ({days[0]} to {days[-1]})" if days else ""))
        status = engine.model_status()
        _line(
            "learned model",
            f"{int(status['corrections'])} corrections, {status['vocabulary']} terms, "
            + ("predicting" if status["warm"] else "still warming up"),
        )
        count, seconds = repo_sessions.review_totals(db)
        _line("awaiting review", f"{count} sessions ({fmt_duration(seconds)})")

        today = local_day(time.time(), engine.tz)
        from .core.aggregate import summary_line, totals_for_day

        _line("today", summary_line(totals_for_day(db, today)))
        db.close()
    except Exception as exc:
        _line("ERROR", exc)
        problems.append(f"database: {exc}")

    if sys.platform != "win32":
        _section("Windows")
        _line("status", "not running on Windows — tracking backends are inactive")
        print("\nRun `python -m timesplit run --demo` to see the dashboard with sample data.")
        return _finish(problems)

    _section("Windows backends")
    for name in WIN_MODULES:
        try:
            __import__(name)
            _line(name.rsplit(".", 1)[-1], "ok")
        except Exception as exc:
            _line(name.rsplit(".", 1)[-1], f"FAILED: {exc}")
            problems.append(f"{name}: {exc}")

    try:
        import uiautomation  # noqa: F401

        _line("uiautomation", "installed")
    except ImportError:
        _line("uiautomation", "NOT installed — web addresses will not be read")
        problems.append("uiautomation is missing (pip install uiautomation)")

    try:
        import pystray  # noqa: F401

        _line("pystray", "installed")
    except ImportError:
        _line("pystray", "NOT installed — no tray icon")
        problems.append("pystray is missing (pip install pystray Pillow)")

    _section("Autostart")
    try:
        from .backends.factory import get_autostart

        installed, note = get_autostart().status()
        _line("runs at logon", f"{'yes' if installed else 'no'} ({note})")
        if not installed:
            problems.append("not set to run at logon (timesplit install-autostart)")
    except Exception as exc:
        _line("ERROR", exc)

    _section("Live capture (10 seconds)")
    print("  Switch between a few windows now — including a browser tab.\n")
    try:
        _live_capture(cfg)
    except Exception as exc:
        _line("ERROR", exc)
        problems.append(f"capture: {exc}")

    return _finish(problems)


def _live_capture(cfg) -> None:
    """Show exactly what the tracker sees, including resolved addresses.

    The browser lines are the point: they confirm the executable name and
    whether the address bar can actually be read on this machine.
    """
    from .backends.win.capture_win import WindowsCaptureBackend
    from .backends.win.uia_win import UrlResolver

    backend = WindowsCaptureBackend(cfg)
    backend.start()
    resolver = UrlResolver(cfg.browser)
    resolver.start()
    if not resolver.available:
        print("  (uiautomation unavailable — showing window titles only)\n")

    print(f"  {'app':<22} {'idle':>6}  {'lock':<5} title / address")
    seen_browsers: dict[str, bool] = {}
    try:
        for _ in range(10):
            snapshot = backend.capture()
            url = resolver.lookup(snapshot) or resolver.take_resolved(
                snapshot.hwnd, snapshot.title
            )
            exe = snapshot.exe_name or "(none)"
            detail = url or snapshot.title or "(no title)"
            print(
                f"  {exe:<22} {snapshot.idle_ms // 1000:>5}s  "
                f"{'yes' if snapshot.locked else 'no':<5} {detail[:64]}"
            )
            if exe in {e.lower() for e in cfg.browser.browser_exes}:
                seen_browsers[exe] = seen_browsers.get(exe, False) or bool(url)
            time.sleep(1)
    finally:
        resolver.stop()
        backend.stop()

    if seen_browsers:
        print()
        for exe, resolved in seen_browsers.items():
            if resolved:
                _line(f"{exe} address bar", "readable")
            else:
                _line(
                    f"{exe} address bar",
                    "NOT read — window titles only. If this is a browser you use, "
                    "leave it focused for a few seconds and re-run doctor.",
                )
    else:
        print("\n  (no browser was in the foreground during the sample)")


def _finish(problems: list[str]) -> int:
    _section("Summary")
    if not problems:
        print("  Everything looks fine.")
        return 0
    for problem in problems:
        print(f"  • {problem}")
    return 1


def selftest_win() -> int:
    """Import every Windows backend. Used by the windows-latest CI job."""
    if sys.platform != "win32":
        print("not running on Windows; nothing to check")
        return 0
    failed = []
    for name in WIN_MODULES:
        try:
            __import__(name)
            print(f"ok    {name}")
        except Exception as exc:
            print(f"FAIL  {name}: {exc}")
            failed.append(name)

    # Prove the calls work, not just that the module imports.
    try:
        from .backends.win.idle_win import IdleProbe
        from .config import IdleConfig

        probe = IdleProbe(IdleConfig())
        idle_ms = probe.idle_ms()
        assert isinstance(idle_ms, int) and idle_ms >= 0
        print(f"ok    GetLastInputInfo returned {idle_ms}ms")
    except Exception as exc:
        print(f"FAIL  idle probe: {exc}")
        failed.append("idle probe")

    try:
        from .backends.win.capture_win import WindowsCaptureBackend
        from .config import Config

        snapshot = WindowsCaptureBackend(Config()).capture()
        print(f"ok    capture returned exe={snapshot.exe_name!r}")
    except Exception as exc:
        print(f"FAIL  capture: {exc}")
        failed.append("capture")

    return 1 if failed else 0
