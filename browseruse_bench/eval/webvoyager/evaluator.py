"""WebVoyagerEvaluator: binary screenshot-based judgment following the original paper."""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List

from browseruse_bench.eval.base import BaseEvaluator
from browseruse_bench.eval.summary import aggregate_evaluation_costs, generate_evaluation_summary
from browseruse_bench.eval.webvoyager.grader import grade_screenshot
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

_TASKS_FILE_PATH: Path = REPO_ROOT / "browseruse_bench/data/WebVoyager/task.jsonl"

_SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _extract_number(filename: str) -> int:
    match = re.search(r"\d+", filename)
    return int(match.group()) if match else 0


def _find_screenshots(trajectory_dir: Path) -> List[Path]:
    if not trajectory_dir.exists():
        return []
    return sorted(
        (f for f in trajectory_dir.iterdir() if f.suffix.lower() in _SCREENSHOT_SUFFIXES),
        key=lambda x: _extract_number(x.name),
    )


class WebVoyagerEvaluator(BaseEvaluator):
    name: ClassVar[str] = "WebVoyager"
    default_mode: ClassVar[str] = "WebVoyager_eval"

    @property
    def eval_strategy(self) -> str:
        return self.args.extra.get("eval_strategy", "final")

    @property
    def image_scale_factor(self) -> float:
        return self.args.extra.get("image_scale_factor", 1.0)

    def results_filename(self) -> str:
        return f"WebVoyager_{self.args.model}_{self.eval_strategy}_eval_results.json"

    def summary_filename(self) -> str:
        return f"WebVoyager_{self.args.model}_{self.eval_strategy}_summary.json"

    def load_tasks(self) -> Dict[str, Dict[str, Any]]:
        tasks: Dict[str, Dict[str, Any]] = {}
        for record in load_task_file(_TASKS_FILE_PATH):
            task_id = str(record.get("id") or record.get("task_id", "")).strip()
            if not task_id:
                continue
            if task_id in tasks:
                logger.warning("Duplicate task_id, keeping first: %s", task_id)
                continue
            tasks[task_id] = record
        logger.info("Loaded %d WebVoyager tasks", len(tasks))
        if not tasks:
            raise ValueError(f"No valid tasks found in {_TASKS_FILE_PATH}")
        return tasks

    def evaluate_one(self, task_id, task_data, agent_result, trajectory_dir):
        task_description = task_data.get("ques") or task_data.get("query") or ""
        web_name = task_data.get("web_name", "")
        screenshots = _find_screenshots(trajectory_dir / "trajectory")

        if not screenshots:
            return self._synthetic_failure(task_id, task_description, agent_result, trajectory_dir, "no_screenshots", web_name=web_name)

        if self.eval_strategy == "final":
            screenshots = [screenshots[-1]]

        answer = agent_result.get("answer") or agent_result.get("response", "")

        grading = grade_screenshot(
            task=task_description,
            answer=answer,
            screenshot_paths=screenshots,
            model=self.model,
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

        is_correct = bool(grading.get("is_correct"))
        eval_details = EvalDetails(
            response=grading["response"],
            is_correct=is_correct,
            reasoning=grading.get("reasoning"),
            eval_usage=eval_usage,
            agent_metrics=agent_metrics,
            benchmark_details={
                "web_name": web_name,
                "screenshot_count": len(screenshots),
                "eval_strategy": self.eval_strategy,
            },
        )

        agent_timestamp = agent_result.get("timestamp") or datetime.now(UTC)
        agent_result_ref = AgentResultRef(
            task_id=task_id,
            timestamp=agent_timestamp,
            result_dir=str(trajectory_dir),
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
        )

        status = "PASS" if is_correct else "FAIL"
        logger.info("%s %s", status, task_id)

        return EvalResult(
            task_id=task_id,
            task=task_description,
            timestamp=datetime.now(UTC),
            agent_result_ref=agent_result_ref,
            predicted_label=1 if is_correct else 0,
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
            evaluation_details=eval_details,
        )

    def _synthetic_failure(
        self,
        task_id: str,
        task_description: str,
        agent_result: Dict[str, Any],
        trajectory_dir: Path,
        reason: str,
        web_name: str = "",
    ) -> EvalResult:
        now = datetime.now(UTC)
        agent_timestamp = agent_result.get("timestamp") or now
        agent_result_ref = AgentResultRef(
            task_id=task_id,
            timestamp=agent_timestamp,
            result_dir=str(trajectory_dir),
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
        )
        eval_details = EvalDetails(
            response="",
            reasoning=f"No usable screenshots: {reason}",
            benchmark_details={"web_name": web_name, "screenshot_count": 0, "eval_strategy": "not_evaluated"},
        )
        return EvalResult(
            task_id=task_id,
            task=task_description,
            timestamp=now,
            agent_result_ref=agent_result_ref,
            predicted_label=0,
            evaluation_details=eval_details,
            failure_category="not_evaluated",
        )

    def _generate_summary(self, all_records: List[Dict[str, Any]]) -> None:
        total = len(all_records)
        success = sum(1 for r in all_records if r.get("predicted_label") == 1)
        accuracy = success / total if total > 0 else 0.0

        summary = generate_evaluation_summary(all_records, total)
        summary["webvoyager_metrics"] = {
            "accuracy": accuracy,
            "success_count": success,
            "total_tasks": total,
        }
        summary["evaluation_config"] = {
            "mode": self.args.mode,
            "model": self.args.model,
            "eval_strategy": self.eval_strategy,
            "trajectories_dir": str(self.args.trajectories_dir),
            "output_path": str(self.args.output_path),
        }

        website_breakdown: Dict[str, Dict[str, Any]] = {}
        for r in all_records:
            web_name = (r.get("evaluation_details") or {}).get("benchmark_details", {}).get("web_name", "Unknown")
            bucket = website_breakdown.setdefault(web_name, {"total": 0, "success": 0})
            bucket["total"] += 1
            if r.get("predicted_label") == 1:
                bucket["success"] += 1
        for stats in website_breakdown.values():
            stats["success_rate"] = stats["success"] / stats["total"] if stats["total"] > 0 else 0.0
        summary["website_breakdown"] = website_breakdown

        usage_list = [
            r["evaluation_details"]["eval_usage"]
            for r in all_records
            if (r.get("evaluation_details") or {}).get("eval_usage") is not None
        ]
        cost_summary = aggregate_evaluation_costs(usage_list, model_name=self.args.model)
        if cost_summary:
            summary["evaluation_cost"] = cost_summary

        with open(self.summary_path(), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        logger.info("SUCCESS: %d/%d (%.2f%%)", success, total, accuracy * 100)
