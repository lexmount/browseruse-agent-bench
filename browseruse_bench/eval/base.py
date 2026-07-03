"""Base class and shared dataclass for benchmark evaluators."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Optional, Set

import openai

from browseruse_bench.eval.model import EvaluationModel, current_task_id
from browseruse_bench.eval.summary import (
    aggregate_evaluation_costs,
    generate_evaluation_summary,
)
from browseruse_bench.schemas.eval_result import EvalResult

logger = logging.getLogger(__name__)


@dataclass
class EvaluatorArgs:
    """Uniform argument bundle passed to every BaseEvaluator subclass."""

    benchmark: str
    model: str
    api_key: str
    base_url: Optional[str]
    trajectories_dir: Path
    output_path: Path
    score_threshold: Optional[int]
    num_worker: int
    temperature: Optional[float]
    split: str
    data_source: str
    mode: str
    extra: Dict[str, Any] = field(default_factory=dict)


class BaseEvaluator(ABC):
    """Abstract base class for benchmark evaluators.

    Subclasses must implement ``load_tasks`` and ``evaluate_one``. The
    ``run`` method orchestrates resume → iteration → JSONL append → summary
    → post-hook and is intentionally non-virtual.
    """

    name: ClassVar[str]
    default_mode: ClassVar[str]
    # When True, the per-task `score_threshold` field in the dataset takes
    # precedence over the global --score-threshold CLI flag.
    uses_per_task_threshold: ClassVar[bool] = False

    def __init__(self, args: EvaluatorArgs, model: Optional[EvaluationModel]) -> None:
        self.args = args
        self.model = model

    # ---- Subclass hooks (mandatory) -----------------------------------
    @abstractmethod
    def load_tasks(self) -> Dict[str, Dict[str, Any]]:
        """Return mapping of task_id -> task data (split-aware if needed)."""

    @abstractmethod
    def evaluate_one(
        self,
        task_id: str,
        task: Dict[str, Any],
        agent_result: Dict[str, Any],
        trajectory_dir: Path,
    ) -> EvalResult:
        """Judge a single task and return a populated EvalResult."""

    # ---- Subclass hooks (optional) ------------------------------------
    def list_completed_tasks(self) -> List[Path]:
        return [
            d for d in sorted(self.args.trajectories_dir.iterdir())
            if d.is_dir() and (d / "result.json").exists()
        ]

    def results_filename(self) -> str:
        return f"{self.name}_{self.args.model}_results.json"

    def summary_filename(self) -> str:
        return f"{self.name}_{self.args.model}_summary.json"

    def post_eval_hook(self, records: List[Dict[str, Any]]) -> None:
        return None

    def _run_iteration(
        self,
        pending: List[str],
        tasks: Dict[str, Dict[str, Any]],
    ) -> Iterator[EvalResult]:
        # Per-task isolation: a single task's failure (transient API error,
        # upstream content-policy rejection on a screenshot, malformed result
        # JSON) must not abort the whole batch. Subclasses with post-eval hooks
        # may backfill synthetic failure records for skipped tasks.
        for task_id in pending:
            trajectory_dir = self.args.trajectories_dir / task_id
            token = current_task_id.set(task_id)
            try:
                with open(trajectory_dir / "result.json", encoding="utf-8") as fh:
                    agent_result = json.load(fh)
                yield self.evaluate_one(task_id, tasks[task_id], agent_result, trajectory_dir)
            except (OSError, ValueError, KeyError, RuntimeError, openai.OpenAIError) as exc:
                logger.exception("Error evaluating task %s: %s", task_id, exc)
                continue
            finally:
                current_task_id.reset(token)

    # ---- Final scaffolding (do not override) --------------------------
    def results_path(self) -> Path:
        return self.args.output_path / self.results_filename()

    def summary_path(self) -> Path:
        return self.args.output_path / self.summary_filename()

    def run(self) -> int:
        self.args.output_path.mkdir(parents=True, exist_ok=True)
        tasks = self.load_tasks()
        completed = self.list_completed_tasks()
        already = self._resume_skip_set()
        pending = [
            p.name for p in completed
            if p.name not in already and p.name in tasks
        ]
        logger.info("Evaluating %d tasks (skip %d already done)", len(pending), len(already))
        for result in self._run_iteration(pending, tasks):
            self._append_result(result)
        # Hook runs before summary so subclasses (e.g. LexBench coverage backfill)
        # can mutate the JSONL on disk; we re-read after the hook to pick up any
        # records the hook added.
        records = self._load_all_records()
        self.post_eval_hook(records)
        records = self._load_all_records()
        self._generate_summary(records)
        return 0

    def _load_all_records(self) -> List[Dict[str, Any]]:
        """Load every record currently appended to the results JSONL on disk."""
        path = self.results_path()
        records: List[Dict[str, Any]] = []
        if not path.exists():
            return records
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def _resume_skip_set(self) -> Set[str]:
        path = self.results_path()
        if not path.exists():
            return set()
        seen: Set[str] = set()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tid = record.get("task_id")
                if isinstance(tid, str):
                    seen.add(tid)
        return seen

    def _append_result(self, result: EvalResult) -> None:
        with open(self.results_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(result.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def _generate_summary(self, records: List[Dict[str, Any]]) -> None:
        summary = generate_evaluation_summary(records, len(records))
        usages: List[Any] = []
        for record in records:
            details = record.get("evaluation_details") or {}
            usage = details.get("eval_usage")
            if usage:
                usages.append(usage)
        cost_summary = aggregate_evaluation_costs(usages)
        if cost_summary:
            summary["evaluation_cost"] = cost_summary
        summary["evaluation_config"] = {
            "mode": self.args.mode,
            "model": self.args.model,
            "trajectories_dir": str(self.args.trajectories_dir),
            "output_path": str(self.args.output_path),
        }
        with open(self.summary_path(), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        logger.info("Summary written to %s", self.summary_path())
