from __future__ import annotations

import json
from pathlib import Path

import pytest

from browseruse_bench.cli.attribute import clear_failure_labels, locate_results_file


def _mk_run(root: Path, ts: str) -> Path:
    d = root / ts / "tasks_eval_result"
    d.mkdir(parents=True)
    f = d / "task_judge_per_task_eval_results.json"
    rows = [
        {
            "task_id": "1",
            "predicted_label": 0,
            "failure_category": "M1.1",
            "evaluation_details": {"failure_classification": {"category": "M1.1"}},
        },
        {"task_id": "2", "predicted_label": 1},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return f


def test_locate_results_file_latest_and_explicit(tmp_path: Path) -> None:
    f1 = _mk_run(tmp_path, "20260101_000000")
    f2 = _mk_run(tmp_path, "20260102_000000")

    assert locate_results_file(tmp_path, None) == f2
    assert locate_results_file(tmp_path, "20260101_000000") == f1


def test_locate_results_file_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        locate_results_file(tmp_path, "29990101_000000")


def test_clear_failure_labels(tmp_path: Path) -> None:
    f = _mk_run(tmp_path, "20260101_000000")

    assert clear_failure_labels(f) == 1

    rows = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    failed = next(r for r in rows if r["task_id"] == "1")
    assert failed["failure_category"] is None
    assert "failure_classification" not in failed["evaluation_details"]
    passed = next(r for r in rows if r["task_id"] == "2")
    assert passed == {"task_id": "2", "predicted_label": 1}
