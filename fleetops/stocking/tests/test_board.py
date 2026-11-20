"""Tests for the board reader, including the read-only property."""

import hashlib
import sqlite3
from pathlib import Path

import pytest

from fleetops.stocking.board import read_board_rows

PACKAGE_DIR = Path(__file__).resolve().parents[1]


def make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE tasks (id TEXT, assignee TEXT, status TEXT)"
        )
        conn.executemany(
            "INSERT INTO tasks VALUES (?, ?, ?)",
            [
                ("t1", "nova", "running"),
                ("t2", "nova", "ready"),
                ("t3", "orbit", "ready"),
                ("t4", "orbit", "done"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reads_assignee_and_status_rows(tmp_path):
    db = tmp_path / "kanban.db"
    make_db(db)
    rows = read_board_rows(db)
    assert {(row["assignee"], row["status"]) for row in rows} == {
        ("nova", "running"),
        ("nova", "ready"),
        ("orbit", "ready"),
        ("orbit", "done"),
    }


def test_database_is_opened_read_only(tmp_path):
    db = tmp_path / "kanban.db"
    make_db(db)
    before = digest(db)
    read_board_rows(db)
    # A read-only open leaves the file byte-identical: no journal, no
    # header updates, nothing written back.
    assert digest(db) == before


def test_missing_database_raises_instead_of_creating_a_file(tmp_path):
    db = tmp_path / "nope.db"
    with pytest.raises(sqlite3.OperationalError):
        read_board_rows(db)
    # mode=ro refuses to create the file — proof the open is read-only.
    assert not db.exists()


def test_package_source_has_no_write_or_task_creation_path():
    banned = ("insert into", "update tasks", "delete from", "kanban create")
    sources = [
        path
        for path in PACKAGE_DIR.rglob("*.py")
        if "tests" not in path.parts
    ]
    assert sources, "expected package modules to scan"
    for path in sources:
        text = path.read_text(encoding="utf-8").lower()
        for token in banned:
            assert token not in text, f"{path.name} contains {token!r}"
