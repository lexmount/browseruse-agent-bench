from __future__ import annotations

import pytest

import json

from browseruse_bench.cli.eval import _parse_extra_args, refresh_summary_failure_stats


def test_parse_eval_extra_args_coerces_private_options() -> None:
    assert _parse_extra_args([
        "--max-screenshots", "50",
        "--image-scale-factor=0.5",
        "--use-cache", "false",
        "--dry-private-flag",
    ]) == {
        "max_screenshots": 50,
        "image_scale_factor": 0.5,
        "use_cache": False,
        "dry_private_flag": True,
    }


def test_parse_eval_extra_args_rejects_positional() -> None:
    with pytest.raises(SystemExit):
        _parse_extra_args(["unexpected"])


def test_refresh_summary_failure_stats(tmp_path) -> None:
    d = tmp_path / "tasks_eval_result"
    d.mkdir()
    results = d / "task_m_per_task_eval_results.json"
    rows = [
        {"task_id": "1", "predicted_label": 0, "failure_category": "M3.1"},
        {"task_id": "2", "predicted_label": 1},
    ]
    results.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    summary = d / "task_m_per_task_summary.json"
    summary.write_text(json.dumps({"overall_statistics": {}}), encoding="utf-8")

    refresh_summary_failure_stats(results)

    data = json.loads(summary.read_text())
    assert data["failure_category_statistics"]["by_category"]["M3.1"]["count"] == 1


def test_refresh_summary_failure_stats_missing_summary_is_noop(tmp_path) -> None:
    results = tmp_path / "task_m_per_task_eval_results.json"
    results.write_text(json.dumps({"task_id": "1", "predicted_label": 0}), encoding="utf-8")

    refresh_summary_failure_stats(results)
