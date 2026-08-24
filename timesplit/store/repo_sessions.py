"""Reading and writing sessions and idle periods."""

from __future__ import annotations

from ..core.models import IdleRecord, SessionRecord

from .db import Database


def _app_id(db: Database, cur, exe_name: str, exe_path: str, is_browser: bool) -> int | None:
    if not exe_name:
        return None
    cur.execute(
        "INSERT INTO apps(exe_name, exe_path, friendly_name, is_browser) VALUES(?,?,?,?)"
        " ON CONFLICT(exe_name, exe_path) DO NOTHING",
        (exe_name, exe_path, exe_name.rsplit(".", 1)[0].title(), 1 if is_browser else 0),
    )
    row = cur.execute(
        "SELECT id FROM apps WHERE exe_name = ? AND exe_path = ?", (exe_name, exe_path)
    ).fetchone()
    return int(row["id"]) if row else None


def insert_session(db: Database, s: SessionRecord, is_browser: bool = False) -> int:
    with db.write() as cur:
        app_id = _app_id(db, cur, s.exe_name, s.exe_path, is_browser)
        cur.execute(
            "INSERT INTO sessions(app_id, title, url, domain, start_ts, end_ts, duration_s,"
            " local_day, category_id, confidence, source, rule_id, is_locked, needs_review, closed)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (app_id, s.title, s.url, s.domain, int(s.start_ts), int(s.end_ts), s.duration_s,
             s.local_day, s.category_id, s.confidence, s.source, s.rule_id,
             1 if s.is_locked else 0, 1 if s.needs_review else 0, 1 if s.closed else 0),
        )
        s.id = int(cur.lastrowid or 0)
        return s.id


def update_session_end(db: Database, session_id: int, end_ts: float) -> None:
    db.execute(
        "UPDATE sessions SET end_ts = ?, duration_s = MAX(0, ? - start_ts) WHERE id = ?",
        (int(end_ts), int(end_ts), session_id),
    )


def close_session(db: Database, session_id: int, end_ts: float) -> None:
    db.execute(
        "UPDATE sessions SET end_ts = ?, duration_s = MAX(0, ? - start_ts), closed = 1"
        " WHERE id = ?",
        (int(end_ts), int(end_ts), session_id),
    )


def delete_session(db: Database, session_id: int) -> None:
    db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def set_category(
    db: Database,
    session_id: int,
    category_id: int | None,
    *,
    confidence: float,
    source: str,
    rule_id: int | None = None,
    needs_review: bool = False,
    lock: bool = False,
) -> None:
    db.execute(
        "UPDATE sessions SET category_id=?, confidence=?, source=?, rule_id=?, needs_review=?,"
        " is_locked = MAX(is_locked, ?) WHERE id = ?",
        (category_id, confidence, source, rule_id, 1 if needs_review else 0,
         1 if lock else 0, session_id),
    )


def set_url(db: Database, session_id: int, url: str | None, domain: str | None) -> None:
    db.execute("UPDATE sessions SET url = ?, domain = ? WHERE id = ?", (url, domain, session_id))


def finalize_open_sessions(db: Database, end_ts: float) -> int:
    """Close rows left open by a crash or power-off.

    The heartbeat means end_ts is already at most one heartbeat stale, so we
    trust the stored value rather than crediting time up to 'now' -- which
    could be days later.
    """
    rows = db.query("SELECT id, end_ts, start_ts FROM sessions WHERE closed = 0")
    for row in rows:
        db.execute(
            "UPDATE sessions SET closed = 1, duration_s = MAX(0, end_ts - start_ts) WHERE id = ?",
            (row["id"],),
        )
    return len(rows)


def insert_idle(db: Database, rec: IdleRecord) -> int:
    if rec.duration_s <= 0:
        return 0
    return db.execute(
        "INSERT INTO idle_periods(start_ts, end_ts, duration_s, local_day, reason)"
        " VALUES(?,?,?,?,?)",
        (int(rec.start_ts), int(rec.end_ts), rec.duration_s, rec.local_day, rec.reason),
    )


# ---- reads ---------------------------------------------------------------

SESSION_SELECT = """
SELECT s.*, a.exe_name, a.exe_path, a.is_browser, c.key AS category_key,
       c.display_name AS category_name, c.color AS category_color
FROM sessions s
LEFT JOIN apps a ON a.id = s.app_id
LEFT JOIN categories c ON c.id = s.category_id
"""


def sessions_for_day(db: Database, day: str) -> list[dict]:
    return [
        dict(r)
        for r in db.query(SESSION_SELECT + " WHERE s.local_day = ? ORDER BY s.start_ts", (day,))
    ]


def sessions_between(db: Database, start_day: str, end_day: str) -> list[dict]:
    return [
        dict(r)
        for r in db.query(
            SESSION_SELECT + " WHERE s.local_day BETWEEN ? AND ? ORDER BY s.start_ts",
            (start_day, end_day),
        )
    ]


def session(db: Database, session_id: int) -> dict | None:
    row = db.query_one(SESSION_SELECT + " WHERE s.id = ?", (session_id,))
    return dict(row) if row else None


def review_queue(db: Database, limit: int = 100) -> list[dict]:
    """Unreviewed time, longest first -- so the first few clicks fix the most minutes."""
    return [
        dict(r)
        for r in db.query(
            SESSION_SELECT
            + " WHERE s.needs_review = 1 AND s.is_locked = 0"
            " ORDER BY s.duration_s DESC LIMIT ?",
            (limit,),
        )
    ]


def review_totals(db: Database) -> tuple[int, int]:
    row = db.query_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(duration_s), 0) AS secs FROM sessions"
        " WHERE needs_review = 1 AND is_locked = 0"
    )
    return (int(row["n"]), int(row["secs"])) if row else (0, 0)


def idle_for_day(db: Database, day: str) -> list[dict]:
    return [
        dict(r)
        for r in db.query(
            "SELECT * FROM idle_periods WHERE local_day = ? ORDER BY start_ts", (day,)
        )
    ]


def matching_sessions(
    db: Database, *, title: str | None = None, domain: str | None = None, exe: str | None = None
) -> list[int]:
    """Session ids matching a correction scope. Never returns locked rows."""
    where, params = ["s.is_locked = 0"], []
    if title is not None:
        where.append("s.title = ?")
        params.append(title)
    if domain is not None:
        where.append("s.domain = ?")
        params.append(domain)
    if exe is not None:
        where.append("LOWER(a.exe_name) = ?")
        params.append(exe.lower())
    sql = (
        "SELECT s.id FROM sessions s LEFT JOIN apps a ON a.id = s.app_id WHERE "
        + " AND ".join(where)
    )
    return [int(r["id"]) for r in db.query(sql, params)]


def days_with_data(db: Database) -> list[str]:
    return [r["local_day"] for r in db.query("SELECT DISTINCT local_day FROM sessions ORDER BY local_day")]


def latest_day(db: Database) -> str | None:
    return db.scalar("SELECT MAX(local_day) FROM sessions")
