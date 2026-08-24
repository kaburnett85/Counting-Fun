"""Persistence for the Naive Bayes counts.

The model is just three small tables. Loading it is one query per table, and
learning is an upsert on a handful of rows -- there is no training pass to run.
"""

from __future__ import annotations

from .db import Database


def load_counts(db: Database) -> tuple[dict[int, dict[str, float]], dict[int, tuple[float, float]]]:
    tokens: dict[int, dict[str, float]] = {}
    for row in db.query("SELECT category_id, token, count FROM nb_tokens"):
        tokens.setdefault(int(row["category_id"]), {})[row["token"]] = float(row["count"])
    classes: dict[int, tuple[float, float]] = {
        int(r["category_id"]): (float(r["doc_count"]), float(r["token_total"]))
        for r in db.query("SELECT category_id, doc_count, token_total FROM nb_class")
    }
    return tokens, classes


def save_delta(
    db: Database,
    token_delta: dict[int, dict[str, float]],
    class_delta: dict[int, tuple[float, float]],
) -> None:
    """Apply an incremental change. Counts are clamped at zero (unlearn)."""
    with db.write() as cur:
        for cat_id, toks in token_delta.items():
            for token, delta in toks.items():
                cur.execute(
                    "INSERT INTO nb_tokens(category_id, token, count) VALUES(?,?,MAX(0,?))"
                    " ON CONFLICT(category_id, token) DO UPDATE SET"
                    " count = MAX(0, nb_tokens.count + ?)",
                    (cat_id, token, delta, delta),
                )
        for cat_id, (docs, total) in class_delta.items():
            cur.execute(
                "INSERT INTO nb_class(category_id, doc_count, token_total)"
                " VALUES(?, MAX(0,?), MAX(0,?))"
                " ON CONFLICT(category_id) DO UPDATE SET"
                " doc_count = MAX(0, nb_class.doc_count + ?),"
                " token_total = MAX(0, nb_class.token_total + ?)",
                (cat_id, docs, total, docs, total),
            )
        cur.execute("DELETE FROM nb_tokens WHERE count <= 0")


def replace_all(
    db: Database,
    tokens: dict[int, dict[str, float]],
    classes: dict[int, tuple[float, float]],
) -> None:
    """Overwrite the model wholesale (used by rebuild_from_corrections)."""
    with db.write() as cur:
        cur.execute("DELETE FROM nb_tokens")
        cur.execute("DELETE FROM nb_class")
        cur.executemany(
            "INSERT INTO nb_tokens(category_id, token, count) VALUES(?,?,?)",
            [(c, t, v) for c, toks in tokens.items() for t, v in toks.items() if v > 0],
        )
        cur.executemany(
            "INSERT INTO nb_class(category_id, doc_count, token_total) VALUES(?,?,?)",
            [(c, d, t) for c, (d, t) in classes.items()],
        )


def prune_vocabulary(db: Database, max_vocab: int, min_count: float = 2.0) -> int:
    """Drop rare tokens once the vocabulary gets large.

    Keeps resident memory bounded on a machine that has been running for years.
    Only tokens seen barely at all are eligible, so this costs no real accuracy.
    """
    size = int(db.scalar("SELECT COUNT(*) FROM nb_tokens", default=0))
    if size <= max_vocab:
        return 0
    with db.write() as cur:
        cur.execute("DELETE FROM nb_tokens WHERE count < ?", (min_count,))
        removed = cur.rowcount or 0
        cur.execute(
            "UPDATE nb_class SET token_total = COALESCE("
            " (SELECT SUM(count) FROM nb_tokens WHERE nb_tokens.category_id = nb_class.category_id),"
            " 0)"
        )
    return int(removed)


def get_meta(db: Database, key: str, default: str | None = None) -> str | None:
    row = db.query_one("SELECT value FROM nb_meta WHERE key = ?", (key,))
    return row["value"] if row else default


def set_meta(db: Database, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO nb_meta(key, value) VALUES(?,?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def vocabulary_size(db: Database) -> int:
    return int(db.scalar("SELECT COUNT(DISTINCT token) FROM nb_tokens", default=0))


def top_tokens(db: Database, category_id: int, limit: int = 20) -> list[tuple[str, float]]:
    """The words most associated with a category -- shown on the dashboard."""
    return [
        (r["token"], float(r["count"]))
        for r in db.query(
            "SELECT token, count FROM nb_tokens WHERE category_id = ?"
            " ORDER BY count DESC LIMIT ?",
            (category_id, limit),
        )
    ]
