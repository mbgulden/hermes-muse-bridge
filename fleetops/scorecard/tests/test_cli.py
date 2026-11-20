"""End-to-end CLI tests over a throwaway sqlite board."""

import json
import sqlite3
from pathlib import Path

from fleetops.scorecard.cli import main

NOW = 1_800_000_000


def make_board(path: Path) -> str:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tasks (id TEXT, title TEXT, body TEXT, assignee TEXT,
                            status TEXT, created_at REAL, completed_at REAL);
        CREATE TABLE task_runs (id INTEGER, task_id TEXT, profile TEXT,
                                status TEXT, outcome TEXT, started_at REAL,
                                ended_at REAL, metadata TEXT);
        CREATE TABLE task_comments (id INTEGER, task_id TEXT, author TEXT,
                                    body TEXT, created_at REAL);
        """
    )
    conn.execute(
        "INSERT INTO tasks VALUES ('t1', 'fixture', 'body', 'kai', 'done', ?, ?)",
        (NOW - 9000, NOW - 1000),
    )
    conn.execute(
        "INSERT INTO task_runs VALUES (1, 't1', 'kai', 'finished', 'completed', ?, ?, NULL)",
        (NOW - 2000, NOW - 1000),
    )
    conn.execute(
        "INSERT INTO task_comments VALUES (1, 't1', 'orchestrator', 'VERDICT: PASS', ?)",
        (NOW - 500,),
    )
    conn.commit()
    conn.close()
    return str(path)


def test_cli_prints_the_extract_as_json(tmp_path, capsys):
    db = make_board(tmp_path / "kanban.db")
    exit_code = main([db, "--now", str(NOW), "--cap", "8"])
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["first_run_value_success"]["all_time"]["rate"] == 100.0
    assert out["verdict_coverage"]["rate"] == 100.0
    assert out["saturation"]["concurrency_cap"] == 8
    assert out["saturation"]["running_runs"] == 0


def test_cli_markdown_format(tmp_path, capsys):
    db = make_board(tmp_path / "kanban.db")
    exit_code = main([db, "--now", str(NOW), "--format", "markdown"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert out.startswith("# Fleet scorecard")
    assert "First-run value success" in out


def test_cli_rates_file_sets_the_basis(tmp_path, capsys):
    db = make_board(tmp_path / "kanban.db")
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps({"reference_model": "cli-model",
                                 "input_per_million": 1.0,
                                 "output_per_million": 4.0}), encoding="utf-8")
    exit_code = main([db, "--now", str(NOW), "--rates", str(rates)])
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["tokens"]["rate_table"]["reference_model"] == "cli-model"


def test_cli_missing_board_is_an_error_not_a_traceback(tmp_path, capsys):
    exit_code = main([str(tmp_path / "nope.db")])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "cannot read board" in captured.err


def test_cli_missing_rates_file_is_an_error(tmp_path, capsys):
    db = make_board(tmp_path / "kanban.db")
    exit_code = main([db, "--rates", str(tmp_path / "nope.json")])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "cannot load rate table" in captured.err
