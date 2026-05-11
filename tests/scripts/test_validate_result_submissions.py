from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_result_submissions import validate


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _valid_submission() -> dict:
    return {
        "benchmark": "LexBench-Browser",
        "split": "All",
        "benchmark_version": "1.0.0",
        "agent": {
            "name": "example-agent",
            "version": "0.1.0",
            "source": "open-source",
            "repository": "https://github.com/example/example-agent",
            "reproducibility_limitations": "None",
        },
        "model": {
            "provider": "OpenAI",
            "model_id": "gpt-4.1",
        },
        "browser": {
            "backend": "Chrome-Local",
        },
        "evaluation": {
            "judge_model": "gpt-5.4",
            "strategy": "stepwise",
        },
        "commands": {
            "run": "bubench run --agent example-agent --data LexBench-Browser --mode all",
            "eval": "bubench eval --agent example-agent --data LexBench-Browser --model-id gpt-4.1",
        },
        "metrics": {
            "success_rate": 0.5,
            "average_steps": 12.3,
            "average_end_to_end_seconds": 90.0,
            "total_tasks": 210,
            "accounted_tasks": 210,
        },
        "artifacts": {
            "run_directory": "experiments/LexBench-Browser/All/example-agent/gpt-4.1/20260101_120000",
        },
        "notes": {
            "skips": "No silent task drops.",
        },
    }


def _write_leaderboard(path: Path) -> None:
    _write_json(
        path,
        {
            "schema_version": "1.0",
            "generated_from": "community/results",
            "results": [],
        },
    )


def test_validate_accepts_valid_submission(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    leaderboard = tmp_path / "leaderboard.json"
    _write_json(results_root / "LexBench-Browser/All/example/submission.json", _valid_submission())
    _write_leaderboard(leaderboard)

    assert validate(results_root=results_root, leaderboard_path=leaderboard, include_examples=False) == []


def test_validate_reports_missing_required_field(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    leaderboard = tmp_path / "leaderboard.json"
    submission = _valid_submission()
    del submission["agent"]["version"]
    _write_json(results_root / "LexBench-Browser/All/example/submission.json", submission)
    _write_leaderboard(leaderboard)

    errors = validate(results_root=results_root, leaderboard_path=leaderboard, include_examples=False)

    assert any("agent.version" in error for error in errors)


def test_validate_rejects_unaccounted_tasks(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    leaderboard = tmp_path / "leaderboard.json"
    submission = _valid_submission()
    submission["metrics"]["accounted_tasks"] = 209
    _write_json(results_root / "LexBench-Browser/All/example/submission.json", submission)
    _write_leaderboard(leaderboard)

    errors = validate(results_root=results_root, leaderboard_path=leaderboard, include_examples=False)

    assert any("must equal" in error for error in errors)


def test_validate_flags_secret_like_values(tmp_path: Path) -> None:
    results_root = tmp_path / "results"
    leaderboard = tmp_path / "leaderboard.json"
    submission = _valid_submission()
    submission["notes"]["provider_incidents"] = "OPENAI_API_KEY leaked in logs"
    _write_json(results_root / "LexBench-Browser/All/example/submission.json", submission)
    _write_leaderboard(leaderboard)

    errors = validate(results_root=results_root, leaderboard_path=leaderboard, include_examples=False)

    assert any("possible secret-like" in error for error in errors)
