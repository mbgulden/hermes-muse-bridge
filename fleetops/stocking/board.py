"""Board reads for the fleetops stocking monitor.

The board is the kanban sqlite database. This module opens it with the
sqlite read-only URI mode (``mode=ro``): the monitor can read a live
board while the fleet is writing to it, it never takes a write lock,
and opening a path that does not exist raises instead of creating an
empty database file. There is no write path in this package at all.
"""

from __future__ import annotations

import sqlite3
import urllib.request
from pathlib import Path


def read_board_rows(db_path: str | Path) -> list[dict]:
    """Return one row dict per kanban task: {"assignee", "status"}.

    The database is opened read-only. Raises sqlite3.OperationalError
    if the file does not exist or has no tasks table.
    """
    uri = "file:{}?mode=ro".format(urllib.request.pathname2url(str(db_path)))
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT assignee, status FROM tasks")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()
