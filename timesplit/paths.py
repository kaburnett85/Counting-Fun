"""Filesystem locations for TimeSplit data.

Everything the app writes lives under one directory. On Windows that is
%LOCALAPPDATA%\\TimeSplit; elsewhere it is ~/.local/share/timesplit. Setting
$TIMESPLIT_HOME overrides it entirely -- that is how the test suite and
``--demo`` keep out of the real profile.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_HOME = "TIMESPLIT_HOME"


def data_dir() -> Path:
    """The directory holding the database, config, logs and secrets."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return Path(base) / "TimeSplit"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "timesplit"


def ensure_data_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "timesplit.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def log_path() -> Path:
    return data_dir() / "timesplit.log"


def secret_path() -> Path:
    return data_dir() / "apikey.bin"


def export_dir() -> Path:
    d = data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def package_data_dir() -> Path:
    """Read-only data shipped inside the package (seed rules, stopwords, PSL)."""
    return Path(__file__).resolve().parent / "data"
