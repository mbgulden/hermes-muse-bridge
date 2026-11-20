"""End-to-end CLI tests over JSON input files."""

import json
from pathlib import Path

from fleetops.distiller.cli import main


def write_input(path: Path, payload: dict) -> str:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def base_payload(**overrides):
    payload = {
        "task": {
            "id": "t_42",
            "title": "Build the fixture report",
            "body": "Build it.\n\nDELIVERABLE-FIRST (hard rule): skeleton "
                    "first.\n\nREPORT-BEFORE-TIMEOUT (hard rule): report "
                    "by 80% of the cap.",
            "assignee": "kai",
        },
        "cause": "task shape",
        "strike_count": 1,
        "failure_summary": "Burned the cap with no deliverable.",
    }
    payload.update(overrides)
    return payload


def test_cli_prints_a_distill_decision_as_json(tmp_path, capsys):
    src = write_input(tmp_path / "in.json", base_payload(rotate_profile=True))
    exit_code = main(["--input", src])
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["action"] == "distill"
    assert out["successor"]["assignee"] == "george"
    assert out["successor"]["supersedes"] == "t_42"
    assert out["log_record"]["cause"] == "task shape"


def test_cli_positional_input_and_escalate_decision(tmp_path, capsys):
    src = write_input(tmp_path / "in.json", base_payload(strike_count=3))
    exit_code = main([src])
    out = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert out["action"] == "escalate"
    assert out["escalate_to"] == "jimmy-direct"
    assert out["successor"] is None


def test_cli_appends_the_log_record_when_asked(tmp_path, capsys):
    log = tmp_path / "distillations.jsonl"
    src = write_input(tmp_path / "in.json", base_payload(log_path=str(log)))
    exit_code = main(["--input", src])
    capsys.readouterr()
    assert exit_code == 0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["task_id"] == "t_42"


def test_cli_missing_file_is_an_error_not_a_traceback(tmp_path, capsys):
    exit_code = main(["--input", str(tmp_path / "nope.json")])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "cannot load input" in captured.err


def test_cli_malformed_input_is_an_error(tmp_path, capsys):
    src = write_input(tmp_path / "in.json", {"cause": "task shape"})
    exit_code = main(["--input", src])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "cannot load input" in captured.err


def test_cli_no_input_path_is_an_error(capsys):
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "required" in captured.err
