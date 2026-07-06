from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from browseruse_bench.cli.attribute import locate_results_file


def _mk_run(root: Path, ts: str, name: str = "task_judge_per_task_eval_results.json") -> Path:
    d = root / ts / "tasks_eval_result"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    rows = [
        {"task_id": "1", "predicted_label": 0, "failure_category": "M1"},
        {"task_id": "2", "predicted_label": 1},
    ]
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return f


def test_locate_results_file_latest_and_explicit(tmp_path: Path) -> None:
    f1 = _mk_run(tmp_path, "20260101_000000")
    f2 = _mk_run(tmp_path, "20260102_000000")

    assert locate_results_file(tmp_path, None) == f2
    assert locate_results_file(tmp_path, "20260101_000000") == f1


def test_locate_results_file_ignores_non_timestamp_dirs(tmp_path: Path) -> None:
    f = _mk_run(tmp_path, "20260101_000000")
    _mk_run(tmp_path, "backup_old")

    assert locate_results_file(tmp_path, None) == f


def test_locate_results_file_matches_all_evaluator_namings(tmp_path: Path) -> None:
    f = _mk_run(tmp_path, "20260101_000000", name="BrowseComp_grader_eval_gpt_results.json")

    assert locate_results_file(tmp_path, None) == f


def test_locate_results_file_picks_most_recent_of_many(tmp_path: Path) -> None:
    old = _mk_run(tmp_path, "20260101_000000", name="task_a_stepwise_eval_results.json")
    new = _mk_run(tmp_path, "20260101_000000", name="task_a_final_eval_results.json")
    past = time.time() - 100
    os.utime(old, (past, past))

    assert locate_results_file(tmp_path, "20260101_000000") == new


def test_locate_results_file_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        locate_results_file(tmp_path, "29990101_000000")
