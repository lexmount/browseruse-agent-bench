"""OdysseysEvaluator: rubric-based evaluation for long-horizon web tasks."""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from browseruse_bench.eval.base import BaseEvaluator
from browseruse_bench.eval.odysseys.grader import grade_rubrics
from browseruse_bench.eval.summary import aggregate_evaluation_costs, generate_evaluation_summary
from browseruse_bench.schemas import (
    AgentMetrics,
    AgentResultRef,
    AgentUsage,
    EvalDetails,
    EvalResult,
    EvalUsage,
)
from browseruse_bench.utils import REPO_ROOT
from browseruse_bench.utils.json_io import load_task_file

logger = logging.getLogger(__name__)

_TASKS_FILE_PATH: Path = REPO_ROOT / "browseruse_bench/data/Odysseys/task.jsonl"
_SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _extract_number(filename: str) -> int:
    match = re.search(r"\d+", filename)
    return int(match.group()) if match else 0


def _find_screenshots(trajectory_dir: Path) -> list[Path]:
    if not trajectory_dir.exists():
        return []
    return sorted(
        (f for f in trajectory_dir.iterdir() if f.suffix.lower() in _SCREENSHOT_SUFFIXES),
        key=lambda x: _extract_number(x.name),
    )


class OdysseysEvaluator(BaseEvaluator):
    name: ClassVar[str] = "Odysseys"
    default_mode: ClassVar[str] = "odysseys_eval"

    @property
    def image_scale_factor(self) -> float:
        return self.args.extra.get("image_scale_factor", 1.0)

    @property
    def max_screenshots(self) -> int:
        return int(self.args.extra.get("max_screenshots", 0))

    def results_filename(self) -> str:
        return f"Odysseys_{self.args.model}_rubric_results.json"

    def summary_filename(self) -> str:
        return f"Odysseys_{self.args.model}_rubric_summary.json"

    def load_tasks(self) -> dict[str, dict[str, Any]]:
        tasks: dict[str, dict[str, Any]] = {}
        for record in load_task_file(_TASKS_FILE_PATH):
            task_id = str(record.get("task_id") or record.get("id", "")).strip()
            if not task_id:
                continue
            if task_id in tasks:
                logger.warning("Duplicate task_id, keeping first: %s", task_id)
                continue
            tasks[task_id] = record
        logger.info("Loaded %d Odysseys tasks", len(tasks))
        if not tasks:
            raise ValueError(f"No valid tasks found in {_TASKS_FILE_PATH}")
        return tasks

    def evaluate_one(
        self,
        task_id: str,
        task: dict[str, Any],
        agent_result: dict[str, Any],
        trajectory_dir: Path,
    ) -> EvalResult:
        task_description = task.get("confirmed_task") or task.get("task") or ""
        rubrics = task.get("rubrics") if isinstance(task.get("rubrics"), dict) else {}
        screenshots = _find_screenshots(trajectory_dir / "trajectory")
        if self.max_screenshots > 0:
            screenshots = screenshots[-self.max_screenshots:]

        answer = agent_result.get("answer") or agent_result.get("response", "")
        grading = grade_rubrics(
            task=task_description,
            answer=answer,
            rubrics=rubrics,
            screenshot_paths=screenshots,
            model=self.model,
            action_history=agent_result.get("action_history"),
            image_scale_factor=self.image_scale_factor,
            temperature=self.args.temperature or 0.0,
        )

        raw_usage = grading.get("usage")
        eval_usage = None
        if isinstance(raw_usage, dict):
            eval_usage = EvalUsage(**raw_usage)
        elif raw_usage is not None and hasattr(raw_usage, "model_dump"):
            eval_usage = EvalUsage(**raw_usage.model_dump())

        agent_metrics = None
        raw_metrics = agent_result.get("metrics")
        if isinstance(raw_metrics, dict):
            usage_data = raw_metrics.get("usage")
            agent_metrics = AgentMetrics(
                ttft_ms=raw_metrics.get("ttft_ms"),
                end_to_end_ms=raw_metrics.get("end_to_end_ms", 0),
                steps=raw_metrics.get("steps", 0),
                usage=AgentUsage(**usage_data) if isinstance(usage_data, dict) and usage_data else None,
            )

        is_correct = bool(grading["is_correct"])
        eval_details = EvalDetails(
            response=grading["response"],
            score=round(float(grading["rubric_score"]) * 100),
            is_correct=is_correct,
            reasoning=grading.get("reasoning"),
            eval_usage=eval_usage,
            agent_metrics=agent_metrics,
            benchmark_details={
                "level": task.get("level"),
                "categories": task.get("categories", []),
                "rubric_results": grading["rubric_results"],
                "rubric_score": grading["rubric_score"],
                "passed_rubrics": grading["passed_rubrics"],
                "total_rubrics": grading["total_rubrics"],
                "screenshot_count": len(screenshots),
                "rubric_results_official": grading["official_rubric_results"],
            },
        )

        now = datetime.now(UTC)
        agent_result_ref = AgentResultRef(
            task_id=task_id,
            timestamp=agent_result.get("timestamp") or now,
            result_dir=str(trajectory_dir),
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
        )

        logger.info(
            "%s %s rubric_score=%.3f",
            "PASS" if is_correct else "FAIL",
            task_id,
            grading["rubric_score"],
        )
        return EvalResult(
            task_id=task_id,
            task=task_description,
            timestamp=now,
            agent_result_ref=agent_result_ref,
            predicted_label=1 if is_correct else 0,
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
            evaluation_details=eval_details,
            agent_response=answer,
        )

    def _generate_summary(self, records: list[dict[str, Any]]) -> None:
        total = len(records)
        rubric_scores = [
            float((record.get("evaluation_details") or {}).get("benchmark_details", {}).get("rubric_score", 0.0))
            for record in records
        ]
        perfect = sum(1 for record in records if record.get("predicted_label") == 1)
        rubric_average = sum(rubric_scores) / total if total else 0.0
        perfect_task_rate = perfect / total if total else 0.0

        efficiency_values: list[float] = []
        for record, score in zip(records, rubric_scores, strict=True):
            details = record.get("evaluation_details") or {}
            metrics = details.get("agent_metrics") or {}
            steps = metrics.get("steps") if isinstance(metrics, dict) else None
            if isinstance(steps, int | float) and steps > 0:
                efficiency_values.append(score / steps)
        trajectory_efficiency = (
            sum(efficiency_values) / len(efficiency_values)
            if efficiency_values else 0.0
        )

        summary = generate_evaluation_summary(records, total)
        summary["odysseys_metrics"] = {
            "rubric_average": rubric_average,
            "perfect_task_rate": perfect_task_rate,
            "perfect_task_count": perfect,
            "total_tasks": total,
            "trajectory_efficiency": trajectory_efficiency,
        }
        usages = [
            (record.get("evaluation_details") or {}).get("eval_usage")
            for record in records
            if (record.get("evaluation_details") or {}).get("eval_usage")
        ]
        cost_summary = aggregate_evaluation_costs(usages)
        if cost_summary:
            summary["evaluation_cost"] = cost_summary
        summary["evaluation_config"] = {
            "mode": self.args.mode,
            "model": self.args.model,
            "max_screenshots": self.max_screenshots,
            "trajectories_dir": str(self.args.trajectories_dir),
            "output_path": str(self.args.output_path),
        }
        with open(self.summary_path(), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        logger.info("Summary written to %s", self.summary_path())
