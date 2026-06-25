"""LexBenchBrowserEvaluator: per-task threshold scoring with stepwise screenshots."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from browseruse_bench.eval.base import BaseEvaluator
from browseruse_bench.eval.lexbench_browser.lexmount_eval import evaluate_task
from browseruse_bench.eval.lexbench_browser.screenshot_cleaner import clean_screenshots
from browseruse_bench.eval.score import calculate_success, extract_score_from_response
from browseruse_bench.eval.summary import aggregate_evaluation_costs, generate_evaluation_summary
from browseruse_bench.schemas import (
    AgentMetrics,
    AgentResultRef,
    AgentUsage,
    EvalDetails,
    EvalResult,
    EvalUsage,
)
from browseruse_bench.utils import (
    REPO_ROOT,
    DataSource,
    get_default_version,
    load_data_info,
    load_dataset_file,
    load_tasks,
)

logger = logging.getLogger(__name__)

_SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg"}
_NOT_EVALUATED_FAILURE_CATEGORY = "not_evaluated"
_SYNTHETIC_FAILURE_REASONING = "Task was not evaluated and is counted as failed by policy."
_NOT_EVALUATED_MISSING_REASON = "not_evaluated"
_ARTIFACT_EVIDENCE_KEY = "_artifact_evidence"
_ARTIFACT_EVIDENCE_META_KEY = "_artifact_evidence_meta"
_MAX_ARTIFACT_EVIDENCE_CHARS = 14_000
_MAX_ACTION_HISTORY_ITEMS = 40
_MAX_ACTION_HISTORY_CHARS = 7_000
_MAX_API_SUMMARY_CHARS = 7_000
_MAX_API_STEP_FILES = 6
_MAX_API_STEP_CHARS = 1_200


def _normalize_task_id(raw_task_id: Any) -> str | None:
    if raw_task_id is None:
        return None
    task_id = str(raw_task_id).strip()
    return task_id or None


def _is_synthetic_not_evaluated_record(record: dict[str, Any]) -> bool:
    if record.get("failure_category") != _NOT_EVALUATED_FAILURE_CATEGORY:
        return False
    return record.get("predicted_label") == 0


def _extract_number(filename: str) -> int:
    match = re.search(r"\d+", filename)
    return int(match.group()) if match else 0


def _find_screenshots(trajectory_dir: Path) -> list[Path]:
    if not trajectory_dir.exists():
        return []
    screenshots: list[Path] = []
    for file in sorted(trajectory_dir.iterdir(), key=lambda x: _extract_number(x.name)):
        if file.suffix.lower() in _SCREENSHOT_SUFFIXES:
            screenshots.append(file)
    return screenshots


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...[truncated {len(text) - max_chars} chars]"


def _safe_read_text(path: Path, max_chars: int) -> str | None:
    try:
        return _truncate_text(path.read_text(encoding="utf-8", errors="replace"), max_chars)
    except OSError as exc:
        logger.warning("Failed to read artifact evidence %s: %s", path, exc)
        return None


def _compact_json_file(path: Path, max_chars: int) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("Could not parse JSON artifact %s: %s", path, exc)
        return _safe_read_text(path, max_chars)
    text = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
    return _truncate_text(text, max_chars)


def _compact_api_summary(text: str) -> str:
    # The summary starts with a full system-prompt <details> block. Keep the
    # task/action evidence and omit prompt boilerplate so the judge sees signal.
    text = re.sub(
        r"<details>\s*<summary>Click to expand system prompt</summary>.*?</details>",
        "<details>system prompt omitted</details>",
        text,
        flags=re.DOTALL,
    )
    return text.strip()


def _summarize_action_history(action_history: Any) -> str:
    if not isinstance(action_history, list) or not action_history:
        return ""

    if len(action_history) <= _MAX_ACTION_HISTORY_ITEMS:
        selected = list(enumerate(action_history, start=1))
    else:
        head_count = _MAX_ACTION_HISTORY_ITEMS // 2
        tail_count = _MAX_ACTION_HISTORY_ITEMS - head_count
        head = list(enumerate(action_history[:head_count], start=1))
        tail_start = len(action_history) - tail_count + 1
        tail = list(enumerate(action_history[-tail_count:], start=tail_start))
        selected = (
            head
            + [(-1, f"... {len(action_history) - _MAX_ACTION_HISTORY_ITEMS} actions omitted ...")]
            + tail
        )

    lines: list[str] = []
    for index, item in selected:
        if index == -1:
            lines.append(str(item))
            continue
        if isinstance(item, str):
            rendered = item
        else:
            rendered = json.dumps(item, ensure_ascii=False, default=str)
        lines.append(f"{index}. {_truncate_text(rendered, 1_500)}")
    return _truncate_text("\n".join(lines), _MAX_ACTION_HISTORY_CHARS)


def _collect_artifact_evidence(
    task_dir: Path, agent_result: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Collect non-visual evidence that can prove a browser task completed.

    Runtime-assisted agents often finish through API calls, page programs, or
    generated output files. Those can be stronger evidence than screenshots, so
    the evaluator should present them to the judge instead of failing early.
    """
    sections: list[str] = []
    meta: dict[str, Any] = {
        "has_agent_answer": bool(agent_result.get("answer") or agent_result.get("response")),
        "has_action_history": False,
        "api_summary_included": False,
        "api_step_files_included": 0,
        "char_count": 0,
    }

    action_summary = _summarize_action_history(agent_result.get("action_history"))
    if action_summary:
        meta["has_action_history"] = True
        sections.append("### Agent action history\n" + action_summary)

    api_logs_dir = task_dir / "api_logs"
    summary_path = api_logs_dir / "summary.md"
    if summary_path.exists():
        summary_text = _safe_read_text(summary_path, _MAX_API_SUMMARY_CHARS * 2)
        if summary_text:
            meta["api_summary_included"] = True
            sections.append(
                "### API/runtime call log summary\n"
                + _truncate_text(_compact_api_summary(summary_text), _MAX_API_SUMMARY_CHARS)
            )

    if api_logs_dir.exists():
        step_previews: list[str] = []
        for step_file in sorted(
            api_logs_dir.glob("step_*.json"), key=lambda p: _extract_number(p.name)
        )[:_MAX_API_STEP_FILES]:
            preview = _compact_json_file(step_file, _MAX_API_STEP_CHARS)
            if preview:
                step_previews.append(f"- {step_file.name}: {preview}")
        if step_previews:
            meta["api_step_files_included"] = len(step_previews)
            sections.append("### API/runtime step JSON previews\n" + "\n".join(step_previews))

    evidence = "\n\n".join(sections).strip()
    if evidence:
        evidence = _truncate_text(evidence, _MAX_ARTIFACT_EVIDENCE_CHARS)
    else:
        evidence = "No non-visual artifact evidence was found."
    meta["char_count"] = len(evidence)
    return evidence, meta


def _has_evaluable_artifact_evidence(agent_result: dict[str, Any], meta: dict[str, Any]) -> bool:
    return bool(
        meta.get("has_agent_answer")
        or meta.get("has_action_history")
        or meta.get("api_summary_included")
        or meta.get("api_step_files_included")
    )


def _resolve_split_entry(splits: dict[str, Any], split: str) -> str:
    if split not in splits:
        available = ", ".join(sorted(splits.keys()))
        raise ValueError(f"Unknown split '{split}'. Available: {available}")
    split_conf = splits[split]
    if isinstance(split_conf, str):
        return split_conf
    if isinstance(split_conf, dict):
        for key in ("path", "file", "filename"):
            candidate = split_conf.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        raise ValueError(
            "Split config must be a string or include a valid path key "
            "('path', 'file', or 'filename')"
        )
    raise ValueError(f"Invalid split config type: {type(split_conf).__name__}")


def _resolve_tasks_file_from_split(
    split: str,
    data_source: str = DataSource.LOCAL,
    force_download: bool = False,
) -> Path:
    benchmark_path = REPO_ROOT / "browseruse_bench/data/LexBench-Browser"
    data_info = load_data_info(benchmark_path)

    if "split" in data_info:
        tasks_filename = _resolve_split_entry(data_info["split"], split)
        local_path = REPO_ROOT / "browseruse_bench/data/LexBench-Browser" / tasks_filename
        return load_dataset_file(
            local_path=local_path,
            data_info=data_info,
            data_source=data_source,
            force_download=force_download,
            split=split,
        )

    if "version_split" in data_info:
        version_split = data_info["version_split"]
        version = get_default_version(data_info)
        if not version:
            raise ValueError("Missing default_version for legacy version_split")
        logger.info("Using default version: %s", version)
        tasks_filename = _resolve_split_entry(version_split.get(version, {}), split)
        local_path = REPO_ROOT / "browseruse_bench/data/LexBench-Browser" / version / tasks_filename
        return load_dataset_file(
            local_path=local_path,
            data_info=data_info,
            data_source=data_source,
            force_download=force_download,
            split=split,
        )

    raise ValueError("data_info.json does not contain split structure")


class LexBenchBrowserEvaluator(BaseEvaluator):
    name: ClassVar[str] = "LexBench-Browser"
    default_mode: ClassVar[str] = "LexBench-Browser_eval"
    uses_per_task_threshold: ClassVar[bool] = True

    def __init__(self, args, model):
        super().__init__(args, model)
        self._tasks_json: Path | None = None
        self._dataset_name: str | None = None
        self._expected_task_ids: list[str] = []

    @property
    def eval_strategy(self) -> str:
        return self.args.extra.get("eval_strategy", "stepwise")

    @property
    def image_scale_factor(self) -> float:
        return self.args.extra.get("image_scale_factor", 0.2)

    def results_filename(self) -> str:
        if not self._dataset_name:
            self._tasks_json = _resolve_tasks_file_from_split(
                self.args.split,
                self.args.data_source,
                self.args.extra.get("force_download", False),
            )
            self._dataset_name = self._tasks_json.stem
        return (
            f"{self._dataset_name}_{self.args.model}_per_task_threshold_"
            f"{self.eval_strategy}_eval_results.json"
        )

    def summary_filename(self) -> str:
        if not self._dataset_name:
            self.results_filename()  # populate _dataset_name
        return (
            f"{self._dataset_name}_{self.args.model}_per_task_threshold_"
            f"{self.eval_strategy}_summary.json"
        )

    def load_tasks(self) -> dict[str, dict[str, Any]]:
        if not self.args.split:
            raise ValueError("--split must be specified for LexBench-Browser")
        self._tasks_json = _resolve_tasks_file_from_split(
            self.args.split,
            self.args.data_source,
            self.args.extra.get("force_download", False),
        )
        self._dataset_name = self._tasks_json.stem
        try:
            display = self._tasks_json.relative_to(REPO_ROOT)
        except ValueError:
            display = self._tasks_json
        logger.info("Split '%s' -> %s", self.args.split, display)

        tasks_list = load_tasks(str(self._tasks_json))
        tasks_data: dict[str, dict[str, Any]] = {}
        for task in tasks_list:
            task_id = _normalize_task_id(task.get("task_id"))
            if not task_id:
                continue
            if task_id in tasks_data:
                logger.warning("Duplicate task_id, keeping first: %s", task_id)
                continue
            tasks_data[task_id] = task
        self._expected_task_ids = list(tasks_data.keys())
        logger.info("Loaded %d tasks from %s", len(tasks_data), self._tasks_json.name)
        if not tasks_data:
            raise ValueError(f"No valid task_id found in {self._tasks_json}")
        return tasks_data

    def _resume_skip_set(self) -> set[str]:
        # Skip records that exist but are NOT synthetic-failure placeholders
        # (so a previously-synthetic task gets re-judged when its trajectory
        # is finally available on resume).
        return {
            r["task_id"]
            for r in self._load_all_records()
            if isinstance(r.get("task_id"), str) and not _is_synthetic_not_evaluated_record(r)
        }

    def _write_no_screenshot_result(
        self,
        task_id: str,
        task_data: dict[str, Any],
        task_dir: Path,
        agent_result: dict[str, Any],
        reason: str,
    ) -> EvalResult:
        task_description = task_data.get("query", "Unknown task")
        now = datetime.now(UTC)

        agent_timestamp = agent_result.get("timestamp")
        if not agent_timestamp:
            raise ValueError(f"Missing required timestamp in result.json for task_id={task_id}")
        agent_result_ref = AgentResultRef(
            task_id=task_id,
            timestamp=agent_timestamp,
            result_dir=str(task_dir),
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
        )

        eval_details = EvalDetails(
            response="",
            score=0,
            reasoning=f"No usable screenshots: {reason}",
            benchmark_details={
                "screenshot_count": 0,
                "original_screenshot_count": 0,
                "eval_strategy": "not_evaluated",
                "is_synthetic_failure": True,
                "missing_reason": reason,
            },
        )

        return EvalResult(
            task_id=task_id,
            task=task_description,
            timestamp=now,
            agent_result_ref=agent_result_ref,
            predicted_label=0,
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
            evaluation_details=eval_details,
            failure_category=_NOT_EVALUATED_FAILURE_CATEGORY,
            task_type=task_data.get("task_type") or None,
        )

    def evaluate_one(self, task_id, task_data, agent_result, trajectory_dir):
        task_description = task_data.get("query", "Unknown task")
        artifact_evidence, artifact_meta = _collect_artifact_evidence(trajectory_dir, agent_result)
        agent_result_for_eval = dict(agent_result)
        agent_result_for_eval[_ARTIFACT_EVIDENCE_KEY] = artifact_evidence
        agent_result_for_eval[_ARTIFACT_EVIDENCE_META_KEY] = artifact_meta
        has_artifact_evidence = _has_evaluable_artifact_evidence(agent_result, artifact_meta)

        screenshots = _find_screenshots(trajectory_dir / "trajectory")
        if not screenshots:
            clean_stats = {
                "blank_removed": 0,
                "duplicate_removed": 0,
                "final_count": 0,
                "original_count": 0,
            }
            if has_artifact_evidence:
                logger.info("   No screenshots; evaluating with artifact evidence")
            else:
                logger.info("   No screenshots or artifact evidence, recording as failed")
                return self._write_no_screenshot_result(
                    task_id,
                    task_data,
                    trajectory_dir,
                    agent_result,
                    "no_screenshots",
                )
        else:
            screenshots, clean_stats = clean_screenshots(
                screenshots,
                remove_blank=True,
                remove_duplicates=True,
            )
            if clean_stats["blank_removed"] > 0 or clean_stats["duplicate_removed"] > 0:
                logger.info(
                    "   Cleaned screenshots: removed %d blank, %d duplicates (kept %d/%d)",
                    clean_stats["blank_removed"],
                    clean_stats["duplicate_removed"],
                    clean_stats["final_count"],
                    clean_stats["original_count"],
                )
        if not screenshots:
            if has_artifact_evidence:
                logger.info(
                    "   No valid screenshots after cleaning; evaluating with artifact evidence"
                )
            else:
                logger.info(
                    "   No valid screenshots after cleaning and no artifact evidence, recording as failed"
                )
                return self._write_no_screenshot_result(
                    task_id,
                    task_data,
                    trajectory_dir,
                    agent_result,
                    "no_valid_screenshots_after_cleaning",
                )

        eval_result = evaluate_task(
            task_data=task_data,
            agent_result=agent_result_for_eval,
            screenshot_paths=screenshots,
            model=self.model,
            image_scale_factor=self.image_scale_factor,
            max_screenshots=self.args.extra.get("max_screenshots"),
            api_max_images=self.args.extra.get("api_max_images"),
            eval_strategy=self.eval_strategy,
            temperature=self.args.temperature,
            max_tokens=self.args.extra.get("max_tokens"),
        )

        score = extract_score_from_response(eval_result["response"])
        threshold = task_data.get("score_threshold")
        if threshold is None:
            raise KeyError(f"Missing required per-task score_threshold for task_id={task_id}")
        if not isinstance(threshold, int):
            raise ValueError(
                f"Invalid score_threshold type for task_id={task_id}: {type(threshold).__name__}"
            )
        if threshold < 0 or threshold > 100:
            raise ValueError(f"Invalid score_threshold range for task_id={task_id}: {threshold}")
        is_success = calculate_success(score, threshold)

        raw_usage = eval_result.get("usage")
        eval_usage = None
        if raw_usage is not None:
            if isinstance(raw_usage, dict):
                eval_usage = EvalUsage(**raw_usage)
            elif hasattr(raw_usage, "model_dump"):
                eval_usage = EvalUsage(**raw_usage.model_dump())
            elif hasattr(raw_usage, "__dict__"):
                eval_usage = EvalUsage(**dict(raw_usage.__dict__))

        agent_metrics = None
        raw_metrics = agent_result.get("metrics")
        if isinstance(raw_metrics, dict):
            agent_usage_data = raw_metrics.get("usage")
            agent_metrics = AgentMetrics(
                ttft_ms=raw_metrics.get("ttft_ms"),
                end_to_end_ms=raw_metrics.get("end_to_end_ms", 0),
                steps=raw_metrics.get("steps", 0),
                usage=AgentUsage(**agent_usage_data)
                if isinstance(agent_usage_data, dict) and agent_usage_data
                else None,
            )

        eval_details = EvalDetails(
            system_prompt=eval_result.get("system_prompt"),
            user_prompt=eval_result.get("user_prompt"),
            response=eval_result["response"],
            score=score,
            eval_usage=eval_usage,
            agent_metrics=agent_metrics,
            benchmark_details={
                "score_threshold": threshold,
                "screenshot_count": eval_result.get("screenshot_count", 0),
                "original_screenshot_count": eval_result.get("original_screenshot_count", 0),
                "image_scale_factor": eval_result.get(
                    "image_scale_factor", self.image_scale_factor
                ),
                "max_screenshots_config": self.args.extra.get("max_screenshots"),
                "eval_strategy": eval_result.get("eval_strategy", "stepwise"),
                "artifact_evidence": artifact_meta,
                "image_content_policy_fallback": eval_result.get(
                    "image_content_policy_fallback", False
                ),
                "cyber_safety_prompt_fallback": eval_result.get(
                    "cyber_safety_prompt_fallback", False
                ),
            },
        )

        agent_timestamp = agent_result.get("timestamp")
        if not agent_timestamp:
            raise ValueError(f"Missing required timestamp in result.json for task_id={task_id}")
        agent_result_ref = AgentResultRef(
            task_id=task_id,
            timestamp=agent_timestamp,
            result_dir=str(trajectory_dir),
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
        )

        status = "PASS" if is_success else "FAIL"
        screenshot_used = eval_result.get("screenshot_count", 0)
        screenshot_total = eval_result.get("original_screenshot_count", 0)
        logger.info(
            "   %s Score: %d/100 | Screenshots: %d/%d | Scale: %s",
            status,
            score,
            screenshot_used,
            screenshot_total,
            self.image_scale_factor,
        )

        return EvalResult(
            task_id=task_id,
            task=task_description,
            timestamp=datetime.now(UTC),
            agent_result_ref=agent_result_ref,
            predicted_label=1 if is_success else 0,
            model_id=agent_result.get("model_id") or "",
            browser_id=agent_result.get("browser_id") or "",
            evaluation_details=eval_details,
            task_type=task_data.get("task_type") or None,
        )

    def post_eval_hook(self, records: list[dict[str, Any]]) -> None:
        # Backfill synthetic failures for tasks that were attempted but not evaluated.
        # Scope to task IDs that actually have a trajectory directory — tasks that were
        # never run are not counted as failures in partial-subset runs.
        if not self._expected_task_ids:
            return
        attempted_ids = {d.name for d in self.args.trajectories_dir.iterdir() if d.is_dir()}
        expected = [tid for tid in self._expected_task_ids if tid in attempted_ids]
        include_task_ids = self._task_id_filter("task_ids")
        exclude_task_ids = self._task_id_filter("exclude_task_ids")
        if include_task_ids:
            expected = [tid for tid in expected if tid in include_task_ids]
        if exclude_task_ids:
            expected = [tid for tid in expected if tid not in exclude_task_ids]
        if not expected:
            return
        records_by_task_id = {
            r["task_id"]: r for r in records if isinstance(r, dict) and "task_id" in r
        }
        missing = [tid for tid in expected if tid not in records_by_task_id]
        if not missing:
            return
        tasks = self.load_tasks()
        for tid in missing:
            synthetic = self._build_synthetic_failure(tid, tasks[tid])
            records_by_task_id[tid] = synthetic
        ordered = [records_by_task_id[tid] for tid in expected]
        with open(self.results_path(), "w", encoding="utf-8") as fh:
            for record in ordered:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info("Backfilled %d synthetic failure records", len(missing))

    def _build_synthetic_failure(self, task_id: str, task_data: dict[str, Any]) -> dict[str, Any]:
        task_description = task_data.get("task_text") or task_data.get("query", "Unknown task")
        now = datetime.now(UTC)
        eval_details = EvalDetails(
            response="",
            score=0,
            reasoning=_SYNTHETIC_FAILURE_REASONING,
            benchmark_details={
                "screenshot_count": 0,
                "original_screenshot_count": 0,
                "eval_strategy": "not_evaluated",
                "is_synthetic_failure": True,
                "missing_reason": _NOT_EVALUATED_MISSING_REASON,
            },
        )
        agent_result_ref = AgentResultRef(task_id=task_id, timestamp=now, result_dir="")
        record = EvalResult(
            task_id=task_id,
            task=task_description,
            timestamp=now,
            agent_result_ref=agent_result_ref,
            predicted_label=0,
            evaluation_details=eval_details,
            failure_category=_NOT_EVALUATED_FAILURE_CATEGORY,
            task_type=task_data.get("task_type") or None,
        )
        return record.model_dump(mode="json")

    def _generate_summary(self, all_records: list[dict[str, Any]]) -> None:
        total = len(all_records)
        success = sum(1 for r in all_records if r.get("predicted_label") == 1)
        success_rate = (success / total * 100) if total > 0 else 0

        summary = generate_evaluation_summary(all_records, total)
        summary["lexmount_metrics"] = {
            "success_rate": success_rate,
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

        scores = [r.get("evaluation_details", {}).get("score", 0) for r in all_records]
        if scores:
            summary["score_statistics"] = {
                "mean": sum(scores) / len(scores),
                "min": min(scores),
                "max": max(scores),
                "median": sorted(scores)[len(scores) // 2],
            }

        task_types: dict[str, dict[str, Any]] = {}
        for r in all_records:
            t = r.get("task_type", "Unknown")
            bucket = task_types.setdefault(t, {"total": 0, "success": 0})
            bucket["total"] += 1
            if r.get("predicted_label") == 1:
                bucket["success"] += 1
        for stats in task_types.values():
            stats["success_rate"] = (
                (stats["success"] / stats["total"] * 100) if stats["total"] > 0 else 0
            )
        summary["task_type_breakdown"] = task_types

        usage_list: list[Any] = []
        for r in all_records:
            usage = (r.get("evaluation_details") or {}).get("eval_usage")
            if usage is not None:
                usage_list.append(usage)
        cost_summary = aggregate_evaluation_costs(usage_list)
        if cost_summary:
            summary["evaluation_cost"] = cost_summary

        with open(self.summary_path(), "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        logger.info(
            "SUCCESS: %d/%d (%.2f%%)",
            success,
            total,
            success_rate,
        )
