"""Tests for BaseEvaluator scaffolding."""
from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from browseruse_bench.eval.base import BaseEvaluator, EvaluatorArgs
from browseruse_bench.schemas import (
    AgentResultRef,
    EvalDetails,
    EvalResult,
)
from browseruse_bench.utils.json_io import load_jsonl


def test_evaluator_args_is_dataclass():
    assert is_dataclass(EvaluatorArgs)


def test_evaluator_args_required_fields():
    field_names = {f.name for f in fields(EvaluatorArgs)}
    expected = {
        "benchmark", "model", "api_key", "base_url",
        "trajectories_dir", "output_path", "score_threshold",
        "num_worker", "temperature", "split", "data_source", "mode", "extra",
    }
    assert expected.issubset(field_names)


def _make_args(tmp_path: Path, force_reeval: bool = False) -> EvaluatorArgs:
    traj = tmp_path / "tasks"
    traj.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    return EvaluatorArgs(
        benchmark="Fake",
        model="fake-model",
        api_key="x",
        base_url=None,
        trajectories_dir=traj,
        output_path=out,
        score_threshold=None,
        num_worker=1,
        temperature=None,
        split="All",
        data_source="local",
        mode="fake_mode",
        force_reeval=force_reeval,
    )


class _FakeEvaluator(BaseEvaluator):
    name = "Fake"
    default_mode = "fake_mode"

    def __init__(self, args, model, tasks):
        super().__init__(args, model)
        self._tasks = tasks
        self.calls = []

    def load_tasks(self):
        self.calls.append("load_tasks")
        return self._tasks

    def evaluate_one(self, task_id, task, agent_result, trajectory_dir):
        self.calls.append(f"evaluate:{task_id}")
        now = datetime.now(timezone.utc)
        return EvalResult(
            task_id=task_id,
            task=task.get("desc", ""),
            timestamp=now,
            agent_result_ref=AgentResultRef(
                task_id=task_id,
                timestamp=now,
                result_dir=str(trajectory_dir),
                model_id="",
                browser_id="",
            ),
            predicted_label=1,
            model_id="",
            browser_id="",
            evaluation_details=EvalDetails(response="ok"),
        )

    def post_eval_hook(self, results):
        self.calls.append(f"post_hook:{len(results)}")


def _seed_trajectories(args: EvaluatorArgs, task_ids):
    for tid in task_ids:
        d = args.trajectories_dir / tid
        d.mkdir()
        (d / "result.json").write_text('{"task": "demo"}', encoding="utf-8")


def test_run_orchestration_order(tmp_path):
    args = _make_args(tmp_path)
    _seed_trajectories(args, ("t1", "t2"))
    tasks = {"t1": {"desc": "task one"}, "t2": {"desc": "task two"}}
    ev = _FakeEvaluator(args, model=None, tasks=tasks)
    assert ev.run() == 0
    assert ev.calls[0] == "load_tasks"
    assert "evaluate:t1" in ev.calls
    assert "evaluate:t2" in ev.calls
    assert ev.calls[-1] == "post_hook:2"
    assert ev.results_path().exists()
    assert ev.summary_path().exists()


def test_run_resumes_already_evaluated(tmp_path):
    args = _make_args(tmp_path)
    _seed_trajectories(args, ("t1", "t2"))
    out = args.output_path / "Fake_fake-model_results.json"
    out.write_text('{"task_id": "t1"}\n', encoding="utf-8")
    tasks = {"t1": {"desc": "one"}, "t2": {"desc": "two"}}
    ev = _FakeEvaluator(args, model=None, tasks=tasks)
    ev.run()
    assert "evaluate:t1" not in ev.calls
    assert "evaluate:t2" in ev.calls


def test_force_reeval_discards_stale_results(tmp_path):
    """--force-reeval must re-judge every task; no stale record may survive."""
    args = _make_args(tmp_path, force_reeval=True)
    _seed_trajectories(args, ("t1", "t2"))
    out = args.output_path / "Fake_fake-model_results.json"
    out.write_text(
        '{"task_id": "t1", "predicted_label": 0, "stale": true}\n', encoding="utf-8"
    )
    tasks = {"t1": {"desc": "one"}, "t2": {"desc": "two"}}
    ev = _FakeEvaluator(args, model=None, tasks=tasks)
    assert ev.run() == 0
    assert "evaluate:t1" in ev.calls
    assert "evaluate:t2" in ev.calls
    records = load_jsonl(out)
    assert sorted(r["task_id"] for r in records) == ["t1", "t2"]
    assert not any(r.get("stale") for r in records)
    summary = json.loads(ev.summary_path().read_text(encoding="utf-8"))
    assert summary["overall_statistics"]["evaluated_tasks"] == 2
    assert summary["task_list"]["failed_task_ids"] == []


def test_force_reeval_archives_old_results_as_backup(tmp_path):
    """The pre-reeval file is renamed to a .bak sidecar, not destroyed.

    The backup must not be picked up by the canonical results glob that
    leaderboard/visualization use, or it would be double-counted.
    """
    args = _make_args(tmp_path, force_reeval=True)
    _seed_trajectories(args, ("t1",))
    out = args.output_path / "Fake_fake-model_results.json"
    stale_line = '{"task_id": "t1", "predicted_label": 0, "stale": true}\n'
    out.write_text(stale_line, encoding="utf-8")
    ev = _FakeEvaluator(args, model=None, tasks={"t1": {"desc": "one"}})
    assert ev.run() == 0
    backups = list(args.output_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == stale_line
    assert list(args.output_path.glob("*_results.json")) == [out]


def test_force_reeval_keeps_results_when_load_tasks_fails(tmp_path):
    """A force-reeval that fails during startup must not touch the results file."""
    args = _make_args(tmp_path, force_reeval=True)
    _seed_trajectories(args, ("t1",))
    out = args.output_path / "Fake_fake-model_results.json"
    stale_line = '{"task_id": "t1", "predicted_label": 1}\n'
    out.write_text(stale_line, encoding="utf-8")

    class _BrokenTasksEvaluator(_FakeEvaluator):
        def load_tasks(self):
            raise ValueError("bad split")

    ev = _BrokenTasksEvaluator(args, model=None, tasks={})
    with pytest.raises(ValueError):
        ev.run()
    assert out.read_text(encoding="utf-8") == stale_line
    assert list(args.output_path.glob("*.bak")) == []


def test_force_reeval_backup_names_do_not_collide(tmp_path, monkeypatch):
    """Two archives within the same second must keep both backups."""
    import browseruse_bench.eval.base as eval_base

    fixed = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

    class _FixedDatetime:
        @staticmethod
        def now(tz):
            return fixed

    monkeypatch.setattr(eval_base, "datetime", _FixedDatetime)
    args = _make_args(tmp_path, force_reeval=True)
    ev = _FakeEvaluator(args, model=None, tasks={})
    out = ev.results_path()
    for content in ("first", "second"):
        out.write_text(content, encoding="utf-8")
        ev._discard_existing_results()
    backups = sorted(args.output_path.glob("*.bak"))
    assert len(backups) == 2
    assert {b.read_text(encoding="utf-8") for b in backups} == {"first", "second"}


def test_summary_dedupes_duplicate_records_keeping_newest(tmp_path):
    """Duplicate JSONL lines for one task_id must not inflate the summary."""
    args = _make_args(tmp_path)
    _seed_trajectories(args, ("t2",))
    out = args.output_path / "Fake_fake-model_results.json"
    out.write_text(
        '{"task_id": "t1", "predicted_label": 0}\n'
        '{"task_id": "t1", "predicted_label": 1}\n',
        encoding="utf-8",
    )
    tasks = {"t1": {"desc": "one"}, "t2": {"desc": "two"}}
    ev = _FakeEvaluator(args, model=None, tasks=tasks)
    assert ev.run() == 0
    records = load_jsonl(out)
    assert [r["task_id"] for r in records] == ["t1", "t2"]
    assert records[0]["predicted_label"] == 1
    summary = json.loads(ev.summary_path().read_text(encoding="utf-8"))
    assert summary["overall_statistics"]["evaluated_tasks"] == 2
    assert summary["task_list"]["successful_task_ids"] == ["t1", "t2"]
    assert summary["task_list"]["failed_task_ids"] == []


def test_dedupe_preserves_file_with_malformed_lines(tmp_path):
    """Unparseable lines block the rewrite but not the in-memory dedupe."""
    args = _make_args(tmp_path)
    _seed_trajectories(args, ("t2",))
    out = args.output_path / "Fake_fake-model_results.json"
    out.write_text(
        '{"task_id": "t1", "predicted_label": 0}\n'
        '{"task_id": "t1", "predicted_label": 1}{"broken": \n'
        '{"task_id": "t1", "predicted_label": 1}\n',
        encoding="utf-8",
    )
    tasks = {"t1": {"desc": "one"}, "t2": {"desc": "two"}}
    ev = _FakeEvaluator(args, model=None, tasks=tasks)
    assert ev.run() == 0
    lines = [
        line for line in out.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 4  # 3 original lines (1 malformed kept) + appended t2
    summary = json.loads(ev.summary_path().read_text(encoding="utf-8"))
    assert summary["overall_statistics"]["evaluated_tasks"] == 2
    assert summary["task_list"]["successful_task_ids"] == ["t1", "t2"]


def test_run_skips_unknown_completed_dirs(tmp_path):
    args = _make_args(tmp_path)
    _seed_trajectories(args, ("t1", "ghost"))
    tasks = {"t1": {"desc": "one"}}
    ev = _FakeEvaluator(args, model=None, tasks=tasks)
    ev.run()
    assert "evaluate:t1" in ev.calls
    assert "evaluate:ghost" not in ev.calls


# ---------------------------------------------------------------------------
# task_id log context (TaskIdLogFilter / current_task_id contextvar)
# ---------------------------------------------------------------------------


def test_current_task_id_default_is_dash():
    """Outside any task scope the contextvar yields '-'."""
    from browseruse_bench.eval.model import current_task_id
    assert current_task_id.get() == "-"


def test_task_id_filter_injects_attribute():
    """TaskIdLogFilter must populate record.task_id from the contextvar."""
    import logging
    from browseruse_bench.eval.model import TaskIdLogFilter, current_task_id

    f = TaskIdLogFilter()
    record = logging.LogRecord("x", logging.INFO, "f.py", 1, "msg", None, None)

    token = current_task_id.set("42")
    try:
        assert f.filter(record) is True
        assert record.task_id == "42"
    finally:
        current_task_id.reset(token)


def test_run_iteration_sets_contextvar_per_task(tmp_path):
    """Each evaluate_one sees the right task_id; contextvar resets after."""
    from browseruse_bench.eval.model import current_task_id

    args = _make_args(tmp_path)
    _seed_trajectories(args, ("t1", "t2"))
    seen: list[str] = []

    class _CapturingEvaluator(_FakeEvaluator):
        def evaluate_one(self, task_id, task, agent_result, trajectory_dir):
            seen.append(current_task_id.get())
            return super().evaluate_one(task_id, task, agent_result, trajectory_dir)

    ev = _CapturingEvaluator(
        args, model=None, tasks={"t1": {"desc": "a"}, "t2": {"desc": "b"}},
    )
    ev.run()

    assert seen == ["t1", "t2"]
    # contextvar must reset to default after the loop completes
    assert current_task_id.get() == "-"
