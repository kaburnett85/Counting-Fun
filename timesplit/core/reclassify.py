"""Re-running classification over stored sessions.

Triggered when a rule changes or a correction has a scope wider than one
session. Runs in chunks so a multi-year database never holds a write
transaction long enough to make the dashboard feel stuck, and it always skips
sessions you set by hand.
"""

from __future__ import annotations

from collections.abc import Callable

from ..logging_setup import get as get_logger
from ..store import repo_rules, repo_sessions
from ..store.db import Database
from .classify import Classifier
from .features import activity_from_row

log = get_logger("reclassify")

CHUNK = 500


def reclassify_sessions(
    db: Database,
    classifier: Classifier,
    session_ids: list[int],
    *,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Re-decide specific sessions. Returns how many actually changed."""
    changed = 0
    hits: dict[int, int] = {}
    total = len(session_ids)
    for offset in range(0, total, CHUNK):
        batch = session_ids[offset : offset + CHUNK]
        rows = _rows_for(db, batch)
        updates = []
        for row in rows:
            if row["is_locked"]:
                continue
            decision = classifier.classify(activity_from_row(row))
            if _unchanged(row, decision):
                continue
            updates.append((decision, int(row["id"])))
            if decision.rule_id:
                hits[decision.rule_id] = hits.get(decision.rule_id, 0) + 1
        if updates:
            with db.write() as cur:
                cur.executemany(
                    "UPDATE sessions SET category_id=?, confidence=?, source=?, rule_id=?,"
                    " needs_review=? WHERE id = ? AND is_locked = 0",
                    [
                        (d.category_id, d.confidence, d.source, d.rule_id,
                         1 if d.needs_review else 0, sid)
                        for d, sid in updates
                    ],
                )
            changed += len(updates)
        if progress:
            progress(min(offset + CHUNK, total), total)

    repo_rules.record_hits(db, hits)
    if changed:
        log.info("reclassified %d of %d sessions", changed, total)
    return changed


def reclassify_range(
    db: Database,
    classifier: Classifier,
    start_day: str,
    end_day: str,
    *,
    include_all_unknown: bool = True,
) -> int:
    """The default sweep after a rule change.

    Recent history plus every still-uncategorised session of any age -- old
    unknowns are exactly what a new rule is most likely to resolve.
    """
    rows = db.query(
        "SELECT id FROM sessions WHERE is_locked = 0 AND local_day BETWEEN ? AND ?",
        (start_day, end_day),
    )
    ids = [int(r["id"]) for r in rows]
    if include_all_unknown:
        seen = set(ids)
        extra = db.query(
            "SELECT s.id FROM sessions s LEFT JOIN categories c ON c.id = s.category_id"
            " WHERE s.is_locked = 0 AND (s.category_id IS NULL OR c.key = 'unknown')"
        )
        ids.extend(int(r["id"]) for r in extra if int(r["id"]) not in seen)
    return reclassify_sessions(db, classifier, ids)


def reclassify_all(db: Database, classifier: Classifier) -> int:
    rows = db.query("SELECT id FROM sessions WHERE is_locked = 0")
    return reclassify_sessions(db, classifier, [int(r["id"]) for r in rows])


def _rows_for(db: Database, ids: list[int]) -> list[dict]:
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    return [
        dict(r)
        for r in db.query(
            repo_sessions.SESSION_SELECT + f" WHERE s.id IN ({placeholders})", ids
        )
    ]


def _unchanged(row: dict, decision) -> bool:
    return (
        row["category_id"] == decision.category_id
        and row["source"] == decision.source
        and bool(row["needs_review"]) == decision.needs_review
        and row["rule_id"] == decision.rule_id
    )
