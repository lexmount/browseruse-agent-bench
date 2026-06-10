"""Tests for browseruse_bench/visualization/generate_index.py — scan_run model field and directory layout detection."""

import json
from pathlib import Path

import pytest

import browseruse_bench.visualization.generate_index as gi


def make_task(task_dir: Path, task_id: str = "task_001") -> None:
    """Write a minimal result.json inside task_dir."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "result.json").write_text(
        json.dumps({
            "task_id": task_id,
            "task": "do something",
            "model_id": "",
            "agent_success": True,
            "agent_done": True,
            "env_status": "ok",
            "action_history": [],
            "metrics": {"steps": 3, "usage": None},
            "config": {},
        })
    )


def make_run_dir(tmp_path: Path, n_tasks: int = 1) -> Path:
    """Create a minimal run directory under tmp_path with n tasks."""
    run_dir = tmp_path / "20260101_120000"
    for i in range(n_tasks):
        make_task(run_dir / "tasks" / f"task_{i:03d}", task_id=f"task_{i:03d}")
    return run_dir


@pytest.fixture(autouse=False)
def patch_repo_root(tmp_path, monkeypatch):
    """Make REPO_ROOT point to tmp_path so relative_to() works inside scan_run."""
    monkeypatch.setattr(gi, "REPO_ROOT", tmp_path)
    return tmp_path


class TestScanRunModelField:
    def test_model_none_when_not_passed(self, tmp_path, patch_repo_root):
        run_dir = make_run_dir(tmp_path)
        result = gi.scan_run("bench", "split", "agent", run_dir)
        assert result is not None
        assert "model" in result
        assert result["model"] is None

    def test_model_set_when_passed(self, tmp_path, patch_repo_root):
        run_dir = make_run_dir(tmp_path)
        result = gi.scan_run("bench", "split", "agent", run_dir, model="gpt-4.1")
        assert result is not None
        assert result["model"] == "gpt-4.1"

    def test_returns_none_when_tasks_dir_missing(self, tmp_path, patch_repo_root):
        run_dir = tmp_path / "20260101_120000"
        run_dir.mkdir()
        result = gi.scan_run("bench", "split", "agent", run_dir, model="gpt-4.1")
        assert result is None

    def test_returns_none_when_tasks_dir_empty(self, tmp_path, patch_repo_root):
        run_dir = tmp_path / "20260101_120000"
        (run_dir / "tasks").mkdir(parents=True)
        result = gi.scan_run("bench", "split", "agent", run_dir, model="gpt-4.1")
        assert result is None

    def test_stats_fields_present(self, tmp_path, patch_repo_root):
        run_dir = make_run_dir(tmp_path, n_tasks=2)
        result = gi.scan_run("bench", "split", "agent", run_dir, model="claude-3")
        assert result is not None
        assert result["benchmark"] == "bench"
        assert result["split"] == "split"
        assert result["agent"] == "agent"
        assert result["model"] == "claude-3"
        assert "stats" in result
        assert "uuid" in result
        assert result["uuid"] == "20260101_120000"

    def test_metrics_null_usage_does_not_raise(self, tmp_path, patch_repo_root):
        """Regression: metrics.usage=null should not crash scan_run."""
        run_dir = tmp_path / "20260101_120000"
        task_dir = run_dir / "tasks" / "task_000"
        task_dir.mkdir(parents=True)
        (task_dir / "result.json").write_text(json.dumps({
            "task_id": "task_000",
            "task": "test",
            "model_id": "",
            "agent_success": None,
            "agent_done": None,
            "env_status": "",
            "action_history": [],
            "metrics": None,
            "config": {},
        }))
        result = gi.scan_run("bench", "split", "agent", run_dir)
        assert result is not None

    def test_metrics_steps_zero_uses_zero_not_action_history(self, tmp_path, patch_repo_root):
        """Regression: metrics.steps=0 must not fall back to len(action_history)."""
        run_dir = tmp_path / "20260101_120000"
        task_dir = run_dir / "tasks" / "task_000"
        task_dir.mkdir(parents=True)
        (task_dir / "result.json").write_text(json.dumps({
            "task_id": "task_000",
            "task": "test",
            "model_id": "",
            "agent_success": True,
            "agent_done": True,
            "env_status": "",
            "action_history": [{"action": "click"}],
            "metrics": {"steps": 0, "usage": None},
            "config": {},
        }))
        result = gi.scan_run("bench", "split", "agent", run_dir)
        assert result is not None
        assert result["stats"]["avg_steps"] == 0

    def test_scan_run_includes_experiment_and_output_logs(self, tmp_path, patch_repo_root):
        run_dir = make_run_dir(tmp_path)
        (run_dir / "run.log").write_text("experiment run log", encoding="utf-8")
        output_log_dir = tmp_path / "output" / "logs" / "run"
        output_log_dir.mkdir(parents=True)
        (output_log_dir / "20260101_120000.log").write_text("outer run log", encoding="utf-8")

        result = gi.scan_run("bench", "split", "agent", run_dir)

        assert result is not None
        assert result["output_logs"] == [
            {
                "name": "run.log",
                "path": "20260101_120000/run.log",
                "source": "experiment",
            },
            {
                "name": "20260101_120000.log",
                "path": "output/logs/run/20260101_120000.log",
                "source": "output/run",
            },
        ]


class TestRepoRootMissing:
    """Regression: import must succeed and generate_index() must fail cleanly when REPO_ROOT is unresolved."""

    def test_loaders_return_empty_when_repo_root_none(self, monkeypatch):
        monkeypatch.setattr(gi, "REPO_ROOT", None)
        monkeypatch.setattr(gi, "EXPERIMENTS_BASE", None)
        assert gi.load_lexbench_task_thresholds() == {}
        assert gi.load_task_rubrics() == {}

    def test_generate_index_raises_when_repo_root_none(self, monkeypatch):
        monkeypatch.setattr(gi, "REPO_ROOT", None)
        monkeypatch.setattr(gi, "EXPERIMENTS_BASE", None)
        with pytest.raises(RuntimeError, match="Cannot locate experiments/ directory"):
            gi.generate_index()


class TestDirectoryLayoutDetection:
    """Verify that generate_index() handles both 4-level and 5-level layouts."""

    def _make_experiments(self, base: Path, layout: str) -> Path:
        """
        layout='4level': base/bench/split/agent/timestamp/tasks/
        layout='5level': base/bench/split/agent/model/timestamp/tasks/
        """
        if layout == "4level":
            run_dir = base / "bench" / "split" / "agent" / "20260101_000000"
        else:
            run_dir = base / "bench" / "split" / "agent" / "gpt-4.1" / "20260101_000000"
        make_task(run_dir / "tasks" / "task_000")
        return base

    def _run_generate(self, experiments_base: Path) -> dict:
        original = gi.EXPERIMENTS_BASE
        original_repo = gi.REPO_ROOT
        try:
            gi.EXPERIMENTS_BASE = experiments_base
            gi.REPO_ROOT = experiments_base.parent
            return gi.generate_index()
        finally:
            gi.EXPERIMENTS_BASE = original
            gi.REPO_ROOT = original_repo

    def test_4level_layout_finds_run(self, tmp_path):
        experiments = tmp_path / "experiments"
        self._make_experiments(experiments, "4level")
        index = self._run_generate(experiments)
        assert len(index["runs"]) == 1
        assert index["runs"][0]["model"] is None
        assert index["runs"][0]["agent"] == "agent"

    def test_5level_layout_finds_run_with_model(self, tmp_path):
        experiments = tmp_path / "experiments"
        self._make_experiments(experiments, "5level")
        index = self._run_generate(experiments)
        assert len(index["runs"]) == 1
        assert index["runs"][0]["model"] == "gpt-4.1"
        assert index["runs"][0]["agent"] == "agent"
