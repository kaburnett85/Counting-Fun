"""Logging: small rotating file, WARNING by default, never records titles.

Window titles and URLs are the sensitive part of what this app sees, so they
are kept out of the log unless debug.verbose is explicitly turned on -- and
that path logs a warning saying so.
"""

from __future__ import annotations

import logging
import logging.handlers

from . import paths

_configured = False


def setup(verbose: bool = False) -> logging.Logger:
    global _configured
    root = logging.getLogger("timesplit")
    if _configured:
        root.setLevel(logging.DEBUG if verbose else logging.WARNING)
        return root

    root.setLevel(logging.DEBUG if verbose else logging.WARNING)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        paths.ensure_data_dir()
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            paths.log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(fmt)
    root.addHandler(handler)
    root.propagate = False
    _configured = True

    if verbose:
        root.warning("verbose logging is ON -- window titles and URLs will be written to disk")
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"timesplit.{name}")
