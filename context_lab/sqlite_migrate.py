"""Apply numbered SQLite migrations via PRAGMA user_version."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable


Migration = Callable[[sqlite3.Connection], None]


def apply(conn: sqlite3.Connection, steps: list[Migration]) -> int:
    """Run pending migrations in order inside immediate transactions."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index, step in enumerate(steps, start=1):
        if version >= index:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            step(conn)
            conn.execute(f"PRAGMA user_version = {index}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        version = index
    return version
