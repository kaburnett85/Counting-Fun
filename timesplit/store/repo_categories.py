"""Categories: the two jobs, plus the two system buckets."""

from __future__ import annotations

from ..config import EXCLUDED, Config, UNKNOWN
from .db import Database

SYSTEM_CATEGORIES = [
    (UNKNOWN, "Uncategorised", "#9aa0a6", "Time we could not attribute to a job yet."),
    (EXCLUDED, "Private", "#6b7280", "Deliberately not recorded (private windows, password managers)."),
]


def sync_from_config(db: Database, cfg: Config) -> dict[str, int]:
    """Make the categories table match config, then return key -> id."""
    with db.write() as cur:
        for order, cat in enumerate(cfg.categories):
            cur.execute(
                "INSERT INTO categories(key, display_name, color, description, is_billable,"
                " is_system, sort_order) VALUES(?,?,?,?,?,0,?)"
                " ON CONFLICT(key) DO UPDATE SET display_name=excluded.display_name,"
                " color=excluded.color, description=excluded.description,"
                " is_billable=excluded.is_billable, sort_order=excluded.sort_order",
                (cat.key, cat.display_name, cat.color, cat.description,
                 1 if cat.is_billable else 0, order),
            )
        for order, (key, name, color, desc) in enumerate(SYSTEM_CATEGORIES, start=900):
            cur.execute(
                "INSERT INTO categories(key, display_name, color, description, is_billable,"
                " is_system, sort_order) VALUES(?,?,?,?,0,1,?)"
                " ON CONFLICT(key) DO UPDATE SET display_name=excluded.display_name,"
                " color=excluded.color, is_system=1, sort_order=excluded.sort_order",
                (key, name, color, desc, order),
            )
    return id_map(db)


def id_map(db: Database) -> dict[str, int]:
    return {r["key"]: r["id"] for r in db.query("SELECT key, id FROM categories")}


def key_map(db: Database) -> dict[int, str]:
    return {r["id"]: r["key"] for r in db.query("SELECT id, key FROM categories")}


def all_categories(db: Database, include_system: bool = True) -> list[dict]:
    sql = "SELECT * FROM categories"
    if not include_system:
        sql += " WHERE is_system = 0"
    sql += " ORDER BY sort_order, id"
    return [dict(r) for r in db.query(sql)]


def job_categories(db: Database) -> list[dict]:
    """The user's real jobs -- what the classifier is allowed to predict."""
    return all_categories(db, include_system=False)


def id_for(db: Database, key: str) -> int | None:
    row = db.query_one("SELECT id FROM categories WHERE key = ?", (key,))
    return int(row["id"]) if row else None


def display_names(db: Database) -> dict[int, str]:
    return {r["id"]: r["display_name"] for r in db.query("SELECT id, display_name FROM categories")}


def colors(db: Database) -> dict[int, str]:
    return {r["id"]: r["color"] for r in db.query("SELECT id, color FROM categories")}
