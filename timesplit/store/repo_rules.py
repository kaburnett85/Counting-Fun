"""Rules: explicit app/site/keyword to category mappings.

Rules come from three places and carry different weight:
  priority 1000  you (the wizard, or a correction with a wider scope)
  priority  500  a Claude assist suggestion you have not vetoed
  priority  100  the shipped seed pack
"""

from __future__ import annotations

import time

from ..core.models import (
    RULE_PRIORITY_LLM,
    RULE_PRIORITY_SEED,
    RULE_PRIORITY_USER,
    Rule,
)
from .db import Database

#: Most specific first. The classifier tries kinds in this order.
KIND_ORDER = [
    "url_prefix",
    "domain",
    "domain_suffix",
    "title_regex",
    "title_contains",
    "exe",
]

SOURCE_PRIORITY = {
    "user": RULE_PRIORITY_USER,
    "wizard": RULE_PRIORITY_USER,
    "llm": RULE_PRIORITY_LLM,
    "seed": RULE_PRIORITY_SEED,
}


def add_rule(
    db: Database,
    kind: str,
    pattern: str,
    category_id: int,
    *,
    source: str = "user",
    note: str = "",
    replace: bool = True,
) -> int:
    """Insert a rule. A user rule always wins over a weaker existing one."""
    if kind not in KIND_ORDER:
        raise ValueError(f"unknown rule kind: {kind}")
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("empty rule pattern")
    if kind in ("exe", "domain", "domain_suffix"):
        pattern = pattern.lower()
    priority = SOURCE_PRIORITY.get(source, RULE_PRIORITY_USER)
    now = int(time.time())

    existing = db.query_one(
        "SELECT id, priority FROM rules WHERE kind = ? AND pattern = ?", (kind, pattern)
    )
    if existing:
        if not replace and int(existing["priority"]) >= priority:
            return int(existing["id"])
        db.execute(
            "UPDATE rules SET category_id=?, priority=?, source=?, enabled=1, note=? WHERE id=?",
            (category_id, priority, source, note, existing["id"]),
        )
        return int(existing["id"])

    return db.execute(
        "INSERT INTO rules(kind, pattern, category_id, priority, source, enabled, created_at, note)"
        " VALUES(?,?,?,?,?,1,?,?)",
        (kind, pattern, category_id, priority, source, now, note),
    )


def delete_rule(db: Database, rule_id: int) -> None:
    db.execute("UPDATE sessions SET rule_id = NULL WHERE rule_id = ?", (rule_id,))
    db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def set_enabled(db: Database, rule_id: int, enabled: bool) -> None:
    db.execute("UPDATE rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))


def all_rules(db: Database, enabled_only: bool = True) -> list[Rule]:
    sql = "SELECT * FROM rules"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY priority DESC, LENGTH(pattern) DESC, id"
    return [
        Rule(
            id=int(r["id"]),
            kind=r["kind"],
            pattern=r["pattern"],
            category_id=int(r["category_id"]),
            priority=int(r["priority"]),
            source=r["source"],
            enabled=bool(r["enabled"]),
            note=r["note"],
        )
        for r in db.query(sql)
    ]


def rules_with_stats(db: Database) -> list[dict]:
    return [
        dict(r)
        for r in db.query(
            "SELECT r.*, c.key AS category_key, c.display_name AS category_name,"
            " c.color AS category_color FROM rules r"
            " JOIN categories c ON c.id = r.category_id"
            " ORDER BY r.priority DESC, r.hit_count DESC, r.id"
        )
    ]


def record_hits(db: Database, counts: dict[int, int]) -> None:
    """Bump hit counters in one transaction (called after a reclassify sweep)."""
    if not counts:
        return
    now = int(time.time())
    with db.write() as cur:
        cur.executemany(
            "UPDATE rules SET hit_count = hit_count + ?, last_hit_ts = ? WHERE id = ?",
            [(n, now, rid) for rid, n in counts.items()],
        )


def count(db: Database) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM rules", default=0))
