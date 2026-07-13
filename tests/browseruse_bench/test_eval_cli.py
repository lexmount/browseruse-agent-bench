from __future__ import annotations

import argparse
import json

import pytest

from browseruse_bench.cli import eval as eval_cli
from browseruse_bench.cli.eval import (
    _parse_extra_args,
    refresh_summary_failure_stats,
    run_failure_classification,
)


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


def _make_fake_evaluator(out_dir, captured, results_body: str | None = None):
    class _FakeEvaluator:
        default_mode = "fake_mode"
        uses_per_task_threshold = False

        def __init__(self, evaluator_args, model):
            captured["args"] = evaluator_args

        def run(self):
            if results_body is not None:
                out_dir.mkdir(parents=True, exist_ok=True)
                self.results_path().write_text(results_body, encoding="utf-8")
            return 0

        def results_path(self):
            return out_dir / "results.json"

        def summary_path(self):
            return out_dir / "summary.json"

    return _FakeEvaluator


def _patch_eval_cli(monkeypatch, traj, evaluator_cls) -> None:
    monkeypatch.setattr(eval_cli, "get_evaluator_class", lambda name: evaluator_cls)
    monkeypatch.setattr(eval_cli, "load_data_info", lambda path: {})
    monkeypatch.setattr(eval_cli, "resolve_split", lambda split, info: "All")
    monkeypatch.setattr(eval_cli, "find_latest_tasks_dir", lambda base: traj)
    monkeypatch.setattr(eval_cli, "load_evaluation_model", lambda *a, **kw: None)


def _eval_args(**overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        model_id="model-x", split="All", timestamp=None, mode=None,
        api_key="k", base_url="", model="judge", score_threshold=None,
        num_worker=1, dry_run=False, force_reeval=False,
        data_source="local", force_download=False, eval_strategy=None,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def test_run_evaluation_passes_force_reeval_to_evaluator(tmp_path, monkeypatch) -> None:
    traj = tmp_path / "run" / "tasks"
    traj.mkdir(parents=True)
    captured: dict = {}
    evaluator_cls = _make_fake_evaluator(traj.parent / "tasks_eval_result", captured)
    _patch_eval_cli(monkeypatch, traj, evaluator_cls)

    args = _eval_args(force_reeval=True)
    assert eval_cli.run_evaluation("agent-x", "Fake", {"eval": {}}, args, []) == 0
    assert captured["args"].force_reeval is True


def test_run_evaluation_skips_already_classified_failures(tmp_path, monkeypatch) -> None:
    """A resume eval must not re-classify failures that already have a category."""
    traj = tmp_path / "run" / "tasks"
    traj.mkdir(parents=True)
    captured: dict = {}
    evaluator_cls = _make_fake_evaluator(
        traj.parent / "tasks_eval_result",
        captured,
        results_body='{"task_id": "t1", "predicted_label": 0, "failure_category": "M2"}\n',
    )
    _patch_eval_cli(monkeypatch, traj, evaluator_cls)

    def _fake_batch(eval_results, trajectories_dir, model, *, skip_existing, **kwargs):
        captured["skip_existing"] = skip_existing
        return eval_results

    monkeypatch.setattr(eval_cli, "classify_failures_batch", _fake_batch)

    assert eval_cli.run_evaluation("agent-x", "Fake", {"eval": {}}, _eval_args(), []) == 0
    assert captured["skip_existing"] is True


def test_run_failure_classification_dedupes_records(tmp_path, monkeypatch) -> None:
    """Duplicate task_id lines are collapsed (newest wins) before classification."""
    results = tmp_path / "task_m_per_task_eval_results.json"
    results.write_text(
        '{"task_id": "t1", "predicted_label": 0, "failure_category": "M2"}\n'
        '{"task_id": "t1", "predicted_label": 1}\n',
        encoding="utf-8",
    )
    captured: dict = {}

    def _fake_batch(eval_results, trajectories_dir, model, *, skip_existing, **kwargs):
        captured["records"] = eval_results
        return eval_results

    monkeypatch.setattr(eval_cli, "load_evaluation_model", lambda *a, **kw: None)
    monkeypatch.setattr(eval_cli, "classify_failures_batch", _fake_batch)

    assert run_failure_classification(results, tmp_path, "judge", "k", "") == 0
    assert [r["task_id"] for r in captured["records"]] == ["t1"]
    assert captured["records"][0]["predicted_label"] == 1
    lines = [
        line
        for line in results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1


def test_api_key_default_does_not_shadow_config_eval_key(monkeypatch) -> None:
    """An OPENAI_API_KEY in the environment must not become an implicit
    --api-key default: that would win over config.yaml eval.api_key in the
    resolution chain (args.api_key or eval_cfg or env fallback)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stale-env-key")
    parser = argparse.ArgumentParser()
    eval_cli.configure_eval_parser(parser, {})
    args = parser.parse_args(["--agent", "hermes", "--data", "LexBench-Browser"])

    assert args.api_key == ""

    eval_cfg = {"api_key": "sk-config-key"}
    resolved = args.api_key or eval_cfg.get("api_key") or "sk-stale-env-key"
    assert resolved == "sk-config-key"
