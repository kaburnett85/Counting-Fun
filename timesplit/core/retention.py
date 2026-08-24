"""Keeping the database small without losing anything you would miss.

Sessions are kept for two years by default -- long enough to answer a question
about last year's taxes. Idle periods and diagnostic samples are far less
interesting after the fact, so they go sooner.
"""

from __future__ import annotations

import time

from ..config import RetentionConfig
from ..logging_setup import get as get_logger
from ..store.db import Database
from .clock import add_days

log = get_logger("retention")


def prune(db: Database, cfg: RetentionConfig, today: str) -> dict[str, int]:
    removed: dict[str, int] = {}
    plans = [
        ("sessions", cfg.sessions_days, "DELETE FROM sessions WHERE local_day < ?"),
        ("idle_periods", cfg.idle_days, "DELETE FROM idle_periods WHERE local_day < ?"),
    ]
    for table, days, sql in plans:
        if days <= 0:
            continue
        cutoff = add_days(today, -days)
        with db.write() as cur:
            cur.execute(sql, (cutoff,))
            removed[table] = cur.rowcount or 0

    if cfg.samples_days > 0:
        cutoff_ts = int(time.time()) - cfg.samples_days * 86400
        with db.write() as cur:
            cur.execute("DELETE FROM samples WHERE ts < ?", (cutoff_ts,))
            removed["samples"] = cur.rowcount or 0

    if cfg.llm_days > 0:
        cutoff_ts = int(time.time()) - cfg.llm_days * 86400
        with db.write() as cur:
            cur.execute(
                "DELETE FROM llm_items WHERE request_id IN"
                " (SELECT id FROM llm_requests WHERE created_at < ?)",
                (cutoff_ts,),
            )
            cur.execute("DELETE FROM llm_requests WHERE created_at < ?", (cutoff_ts,))
            removed["llm"] = cur.rowcount or 0

    # Orphaned app rows accumulate as programs come and go.
    with db.write() as cur:
        cur.execute("DELETE FROM apps WHERE id NOT IN (SELECT DISTINCT app_id FROM sessions"
                    " WHERE app_id IS NOT NULL)")
        removed["apps"] = cur.rowcount or 0

    total = sum(removed.values())
    if total:
        log.info("retention removed %d rows: %s", total, removed)
    return removed


def vacuum_if_due(db: Database, cfg: RetentionConfig) -> bool:
    """Reclaim disk space occasionally. Skipped if it ran recently."""
    if cfg.vacuum_interval_days <= 0:
        return False
    last = db.get_meta("last_vacuum")
    now = int(time.time())
    if last:
        try:
            if now - int(last) < cfg.vacuum_interval_days * 86400:
                return False
        except ValueError:
            pass
    db.vacuum()
    db.set_meta("last_vacuum", str(now))
    return True


def database_size_bytes(db: Database) -> int:
    try:
        return db.path.stat().st_size
    except (OSError, AttributeError):
        return 0
