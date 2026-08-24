"""Command line entry point.

    python -m timesplit run                 start tracking (this is what autostart runs)
    python -m timesplit run --demo          replay a synthetic day, no Windows needed
    python -m timesplit wizard              (re)run first-run setup
    python -m timesplit dashboard           open the dashboard without the tray
    python -m timesplit export ...          write CSV/XLSX timesheets
    python -m timesplit doctor              diagnose an install
    python -m timesplit install-autostart   register the logon task (Windows)
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="timesplit", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start tracking in the background")
    run.add_argument("--demo", action="store_true", help="replay a synthetic workday (no Windows)")
    run.add_argument("--no-tray", action="store_true", help="skip the tray icon")
    run.add_argument("--dashboard", action="store_true", help="open the dashboard on start")
    run.add_argument("--verbose", action="store_true", help="debug logging (records titles)")

    sub.add_parser("wizard", help="(re)run first-run setup")

    dash = sub.add_parser("dashboard", help="serve the dashboard")
    dash.add_argument("--port", type=int, default=None)
    dash.add_argument("--no-open", action="store_true")

    exp = sub.add_parser("export", help="write timesheets")
    exp.add_argument("--from", dest="date_from", help="YYYY-MM-DD (default: 30 days ago)")
    exp.add_argument("--to", dest="date_to", help="YYYY-MM-DD (default: today)")
    exp.add_argument("--format", choices=["csv", "xlsx"], default="csv")
    exp.add_argument("--out", help="output directory")
    exp.add_argument("--exclude-unreviewed", action="store_true")

    sub.add_parser("doctor", help="diagnose this installation")
    sub.add_parser("install-autostart", help="run TimeSplit at logon (Windows)")
    sub.add_parser("uninstall-autostart", help="stop running at logon (Windows)")
    sub.add_parser("selftest-win", help="import every Windows backend (CI smoke test)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "run"

    if command == "run":
        from .app import run_app

        return run_app(
            demo=getattr(args, "demo", False),
            tray=not getattr(args, "no_tray", False),
            open_dashboard=getattr(args, "dashboard", False),
            verbose=getattr(args, "verbose", False),
        )

    if command == "wizard":
        from .wizard.firstrun import run_wizard

        return run_wizard()

    if command == "dashboard":
        from .app import serve_dashboard_only

        return serve_dashboard_only(port=args.port, open_browser=not args.no_open)

    if command == "export":
        from .app import run_export

        return run_export(
            date_from=args.date_from,
            date_to=args.date_to,
            fmt=args.format,
            out=args.out,
            exclude_unreviewed=args.exclude_unreviewed,
        )

    if command == "doctor":
        from .doctor import run_doctor

        return run_doctor()

    if command in ("install-autostart", "uninstall-autostart"):
        from .app import run_autostart

        return run_autostart(install=command == "install-autostart")

    if command == "selftest-win":
        from .doctor import selftest_win

        return selftest_win()

    return 1


if __name__ == "__main__":
    sys.exit(main())
