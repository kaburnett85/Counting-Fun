"""Corrections: the record of every time you overruled the app.

This table is the source of truth for the learned model. Because every
correction is stored with the exact features it was made on, the model can be
rebuilt from scratch and must come out identical -- that is both the repair
path for a damaged model and a property the tests assert.
"""

from __future__ import annotations

import json
import time

from .db import Database


def add(
    db: Database,
    *,
    session_id: int | None,
    old_category_id: int | None,
    new_category_id: int,
    features: list[str],
    scope: str = "session",
    weight: float = 1.0,
) -> int:
    return db.execute(
        "INSERT INTO corrections(session_id, old_category_id, new_category_id, features_json,"
        " scope, weight, applied_to_model, created_at) VALUES(?,?,?,?,?,?,1,?)",
        (session_id, old_category_id, new_category_id, json.dumps(features), scope,
         float(weight), int(time.time())),
    )


def all_corrections(db: Database) -> list[dict]:
    out = []
    for row in db.query("SELECT * FROM corrections ORDER BY id"):
        d = dict(row)
        try:
            d["features"] = json.loads(d.pop("features_json") or "[]")
        except json.JSONDecodeError:
            d["features"] = []
        out.append(d)
    return out


def count(db: Database) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM corrections", default=0))


def recent(db: Database, limit: int = 20) -> list[dict]:
    return [
        dict(r)
        for r in db.query(
            "SELECT co.*, cn.display_name AS new_name, cn.color AS new_color"
            " FROM corrections co LEFT JOIN categories cn ON cn.id = co.new_category_id"
            " ORDER BY co.id DESC LIMIT ?",
            (limit,),
        )
    ]
