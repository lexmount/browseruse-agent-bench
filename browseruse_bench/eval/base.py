"""Base class and shared dataclass for benchmark evaluators."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, Iterator, List, Optional, Set

import openai

from browseruse_bench.eval.model import EvaluationModel, current_task_id
from browseruse_bench.eval.summary import (
    aggregate_evaluation_costs,
    dedupe_records_keep_newest,
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
    force_reeval: bool = False
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
        # Archive only after load_tasks succeeded, so a startup failure (bad
        # split, missing dataset) leaves the previous results untouched.
        if self.args.force_reeval:
            self._discard_existing_results()
        completed = self.list_completed_tasks()
        already = self._resume_skip_set()
        pending = [
            p.name for p in completed
            if p.name not in already and p.name in tasks
        ]
        logger.info("Evaluating %d tasks (skip %d already done)", len(pending), len(already))
        for result in self._run_iteration(pending, tasks):
            self._append_result(result)
        # Hook runs before the dedupe/summary pass so subclasses (e.g. LexBench
        # coverage backfill) can mutate the JSONL on disk; _dedupe_results_file
        # then re-reads the file, collapses duplicate task_ids (newest wins),
        # and returns the records the summary is built from.
        records = self._load_all_records()
        self.post_eval_hook(records)
        records = self._dedupe_results_file()
        self._generate_summary(records)
        return 0

    def _discard_existing_results(self) -> None:
        """Archive the pre-reeval results file so the run starts from scratch.

        The backup keeps a .bak suffix so it never matches the *_results.json
        globs that leaderboard/visualization use to locate canonical results.
        """
        path = self.results_path()
        if not path.exists():
            return
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        counter = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.{stamp}-{counter}.bak")
            counter += 1
        path.replace(backup)
        logger.info("force-reeval: archived existing results file to %s", backup.name)

    def _dedupe_results_file(self) -> List[Dict[str, Any]]:
        """Collapse duplicate task_id records and return the deduped list.

        Resume runs can legitimately re-judge a task that already has a line on
        disk (e.g. a synthetic placeholder whose trajectory appeared later), and
        _append_result then leaves both lines; without dedupe the summary and
        every downstream reader would double-count that task. The file is only
        rewritten when duplicates were dropped and every line parsed — with
        unparseable lines present the rewrite would silently destroy them, so
        dedupe then applies to the returned records only.
        """
        records, malformed = self._load_records_counting_malformed()
        deduped = dedupe_records_keep_newest(records)
        if len(deduped) == len(records):
            return records
        if malformed:
            logger.warning(
                "Results file %s has %d unparseable lines; skipped dedupe rewrite to preserve them",
                self.results_path().name, malformed,
            )
            return deduped
        self._write_records(deduped)
        logger.info("Deduplicated results file: %d -> %d records", len(records), len(deduped))
        return deduped

    def _write_records(self, records: List[Dict[str, Any]]) -> None:
        """Atomically replace the results JSONL with the given records."""
        path = self.results_path()
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp_path.replace(path)

    def _load_all_records(self) -> List[Dict[str, Any]]:
        """Load every record currently appended to the results JSONL on disk."""
        return self._load_records_counting_malformed()[0]

    def _load_records_counting_malformed(self) -> tuple[List[Dict[str, Any]], int]:
        """Load results records, also counting lines that failed to parse."""
        path = self.results_path()
        records: List[Dict[str, Any]] = []
        malformed = 0
        if not path.exists():
            return records, malformed
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if isinstance(record, dict):
                    records.append(record)
                else:
                    malformed += 1
        return records, malformed

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
        cost_summary = aggregate_evaluation_costs(usages, model_name=self.args.model)
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
