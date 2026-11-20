"""End-to-end CLI tests over a fixture database and bench file."""

import sqlite3
from pathlib import Path

from fleetops.stocking.cli import main

from fixtures import BENCH_TEXT


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
                ("t2", "nova", "running"),
                ("t3", "nova", "ready"),
                ("t4", "orbit", "running"),
                ("t5", "orbit", "ready"),
                ("t6", "orbit", "ready"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_cli_prints_report_for_fixture_board(tmp_path, capsys):
    db = tmp_path / "kanban.db"
    bench = tmp_path / "bench.md"
    make_db(db)
    bench.write_text(BENCH_TEXT, encoding="utf-8")
    exit_code = main(["--db", str(db), "--bench", str(bench)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "nova: running=2 ready=1 [BELOW FLOOR]" in out
    assert "orbit: running=1 ready=2 [OK]" in out
    assert "quartz: running=0 ready=0 [IDLE]" in out
    assert "- Refresh the sitemap audit" in out
    assert "- Quartz bench item" in out


def test_cli_floor_flag_changes_the_verdict(tmp_path, capsys):
    db = tmp_path / "kanban.db"
    make_db(db)
    exit_code = main(["--db", str(db), "--floor", "1"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "nova: running=2 ready=1 [OK]" in out


def test_cli_missing_database_is_an_error_not_a_traceback(tmp_path, capsys):
    exit_code = main(["--db", str(tmp_path / "nope.db")])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "cannot read board" in captured.err


def test_cli_malformed_bench_is_an_error(tmp_path, capsys):
    db = tmp_path / "kanban.db"
    bench = tmp_path / "bench.md"
    make_db(db)
    bench.write_text("## Broken\nOwner: nova\nState: QUEUED\n", encoding="utf-8")
    exit_code = main(["--db", str(db), "--bench", str(bench)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "cannot load bench file" in captured.err
