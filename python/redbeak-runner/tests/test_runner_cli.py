"""CLI doctor and start against the in-memory reference server."""

from __future__ import annotations

import json
from pathlib import Path

from _runner_fixtures import CASE_A, PROJECT_ID, RUN_ID, SCENARIO_A
from redbeak_runner.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_doctor_loads_a_local_adapter() -> None:
    result = runner.invoke(
        app,
        ["runner", "doctor", "--adapter", "_runner_fixtures:NoteAdapter", "--reference-server"],
    )
    assert result.exit_code == 0, result.output
    assert "adapter ok" in result.output
    assert "reference server ok" in result.output
    assert "rbk_live_" not in result.output


def test_start_until_idle_with_work_file(tmp_path: Path) -> None:
    work = {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "cases": [
            {
                "case_execution_id": CASE_A,
                "scenario_id": SCENARIO_A,
                "scenario_external_id": "note",
                "setup": {"type": "inline", "payload": {"note": ""}},
                "turns": [{"sequence": 0, "type": "user_message", "content": "hello"}],
                "observation_request": {"fact_names": ["note_state"]},
            }
        ],
    }
    work_path = tmp_path / "work.json"
    work_path.write_text(json.dumps(work), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "runner",
            "start",
            "--adapter",
            "_runner_fixtures:NoteAdapter",
            "--reference-server",
            "--work-file",
            str(work_path),
            "--until-idle",
            "--checkpoint-dir",
            str(tmp_path / "ckpt"),
            "--artifacts-dir",
            str(tmp_path / "art"),
            "--heartbeat-interval",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "runner finished cases=1" in result.output
    assert (tmp_path / "art" / RUN_ID / "cases" / CASE_A / "turn-000.json").is_file()
    assert (tmp_path / "art" / RUN_ID / "cases" / CASE_A / "completion.manifest.json").is_file()
