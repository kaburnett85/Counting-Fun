"""The database handle: one connection, WAL, guarded by a lock.

Several threads touch this (tracker, web, reclassify worker), so all access
goes through a single connection behind a threading.Lock. That is simpler and
lighter than a connection pool, and this workload is a few writes a minute.

PRAGMA choices are deliberately memory-frugal: mmap is off and the page cache
is capped at 2MB, because staying small matters more here than shaving
milliseconds off queries that run once a minute.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import paths
from ..logging_setup import get as get_logger
from .schema import MIGRATIONS

log = get_logger("store.db")


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else paths.db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = self._connect()
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,  # explicit transactions only
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if str(self.path) != ":memory:":
            cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute("PRAGMA cache_size=-2000")  # 2MB, not the 2MB-per-connection default
        cur.execute("PRAGMA mmap_size=0")  # keep it out of RSS
        cur.close()
        return conn

    # ---- transactions --------------------------------------------------

    @contextmanager
    def write(self) -> Iterator[sqlite3.Cursor]:
        """An exclusive write transaction. Commits on success, rolls back on error."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                yield cur
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()
            finally:
                cur.close()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            try:
                return cur.fetchall()
            finally:
                cur.close()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Single-statement write. Returns lastrowid."""
        with self.write() as cur:
            cur.execute(sql, tuple(params))
            return int(cur.lastrowid or 0)

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self.write() as cur:
            cur.executemany(sql, [tuple(r) for r in rows])

    # ---- meta ----------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM schema_meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ---- lifecycle -----------------------------------------------------

    def migrate(self) -> int:
        """Apply pending migrations. Idempotent -- safe to call on every start."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            self._conn.commit()
            row = cur.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            current = int(row["value"]) if row else 0

            for version, sql in MIGRATIONS:
                if version <= current:
                    continue
                cur.execute("BEGIN IMMEDIATE")
                try:
                    cur.executescript(sql)
                    cur.execute(
                        "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (str(version),),
                    )
                except BaseException:
                    self._conn.rollback()
                    raise
                else:
                    self._conn.commit()
                current = version
            cur.close()
        return current

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")

    def close(self) -> None:
        with self._lock:
            with contextlib.suppress(sqlite3.Error):
                self._conn.commit()
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_database(path: Path | str | None = None) -> Database:
    """Open the database, quarantining and recreating it if it is unreadable.

    Losing history is bad; refusing to track time because of a corrupt file is
    worse. A damaged database is renamed aside so it can still be recovered.
    """
    target = Path(path) if path is not None else paths.db_path()
    try:
        return Database(target)
    except sqlite3.DatabaseError as exc:
        if str(target) == ":memory:":
            raise
        log.error("database unreadable (%s); quarantining and starting fresh", exc)
        stamp = 0
        bad = target.with_suffix(f".corrupt{stamp}.db")
        while bad.exists():
            stamp += 1
            bad = target.with_suffix(f".corrupt{stamp}.db")
        try:
            target.replace(bad)
        except OSError:
            target.unlink(missing_ok=True)
        return Database(target)
