"""BrowseCompEvaluator: text grading benchmark with encrypted Q/A pairs."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from browseruse_bench.eval.base import BaseEvaluator
from browseruse_bench.eval.browse_comp.grader import (
    grade_response,
    load_grader_model,
)
from browseruse_bench.schemas import (
    AgentMetrics,
    AgentResultRef,
    AgentUsage,
    EvalDetails,
    EvalResult,
    EvalUsage,
)
from browseruse_bench.utils import REPO_ROOT, decrypt, load_task_file

logger = logging.getLogger(__name__)


class BrowseCompEvaluator(BaseEvaluator):
    name: ClassVar[str] = "BrowseComp"
    default_mode: ClassVar[str] = "BrowseComp_grader_eval"

    def __init__(self, args, model):
        # BrowseComp grade_response expects a callable grader (GraderModel.__call__),
        # not the generic EvaluationModel.generate() interface.
        super().__init__(args, model)
        self._grader = load_grader_model(
            args.model, args.api_key, args.base_url, temperature=args.temperature,
        )

    def results_filename(self) -> str:
        return f"BrowseComp_grader_eval_{self.args.model}_results.json"

    def summary_filename(self) -> str:
        return f"BrowseComp_grader_eval_{self.args.model}_summary.json"

    def load_tasks(self) -> dict[str, dict[str, Any]]:
        tasks_jsonl = REPO_ROOT / "browseruse_bench/data/BrowseComp/task.jsonl"
        return {
            str(task["task_id"]): task
            for task in load_task_file(tasks_jsonl)
            if "task_id" in task
        }

    def evaluate_one(self, task_id, task, agent_result, trajectory_dir):
        question = decrypt(task["encrypted_question"], task["canary"])
        correct_answer = decrypt(task["encrypted_answer"], task["canary"])
        agent_response = agent_result.get("answer") or agent_result.get("response", "")

        grading = grade_response(question, correct_answer, agent_response, self._grader)

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

        eval_details = EvalDetails(
            user_prompt=grading.get("user_prompt"),
            response=grading.get("response", ""),
            is_correct=grading.get("is_correct"),
            reasoning=grading.get("reasoning"),
            eval_usage=eval_usage,
            agent_metrics=agent_metrics,
        )

        agent_timestamp = agent_result.get("timestamp") or datetime.now(UTC)
        agent_result_ref = AgentResultRef(
            task_id=task_id,
            timestamp=agent_timestamp,
            result_dir=str(trajectory_dir),
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
        )

        is_correct = bool(grading.get("is_correct"))
        status = "PASS" if is_correct else "FAIL"
        logger.info("%s %s", status, task_id)

        return EvalResult(
            task_id=task_id,
            task=question,
            timestamp=datetime.now(UTC),
            agent_result_ref=agent_result_ref,
            predicted_label=1 if is_correct else 0,
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
            evaluation_details=eval_details,
            correct_answer=correct_answer,
            agent_response=agent_response,
        )

    def _generate_summary(self, records: list[dict[str, Any]]) -> None:
        super()._generate_summary(records)
        path = self.summary_path()
        if not path.exists():
            return
        with open(path, encoding="utf-8") as fh:
            summary = json.load(fh)
        total = len(records)
        correct = sum(
            1 for r in records
            if (r.get("evaluation_details") or {}).get("is_correct")
        )
        accuracy = correct / total if total > 0 else 0
        summary["browsecomp_metrics"] = {"accuracy": accuracy, "correct": correct, "total": total}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        logger.info("BrowseComp results: %d/%d = %.2f%%", correct, total, accuracy * 100)
