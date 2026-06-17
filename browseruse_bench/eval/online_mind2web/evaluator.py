"""OnlineMind2WebEvaluator: Web navigation benchmark with screenshot-based judging."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import multiprocessing
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from typing import Any, ClassVar, Dict, Iterator, List, Optional

import openai

from browseruse_bench.eval.base import BaseEvaluator
from browseruse_bench.eval.online_mind2web.utils import (
    OpenaiEngine,
    extract_failure_category,
    extract_predication,
    extract_reasoning,
)
from browseruse_bench.eval.online_mind2web.webjudge import WebJudge_Online_Mind2Web_eval
from browseruse_bench.schemas import (
    AgentMetrics,
    AgentResultRef,
    AgentUsage,
    EvalDetails,
    EvalResult,
    EvalUsage,
)

logger = logging.getLogger(__name__)


def _judge_one(
    task_id: str,
    trajectory_dir: Path,
    agent_result: Dict[str, Any],
    model: OpenaiEngine,
    score_threshold: int,
    mode: str,
) -> EvalResult:
    """Run WebJudge over a single task's screenshots and build an EvalResult.

    Pure function (no evaluator state) so multiprocessing workers can call it
    after instantiating their own OpenaiEngine.
    """
    output_results = copy.deepcopy(agent_result)
    task_description = agent_result["task"]
    action_history = agent_result.get("action_history")

    trajectory_images_path = trajectory_dir / "trajectory"
    screenshot_paths = []
    if trajectory_images_path.exists():
        for image in sorted(
            os.listdir(trajectory_images_path),
            key=lambda x: int(re.findall(r"\d+", x)[0]) if re.findall(r"\d+", x) else 0,
        ):
            screenshot_paths.append(str(trajectory_images_path / image))

    logger.info("Start evaluation for %s", task_description)
    messages, _text, _system_msg, record, key_points, prompt_snapshots = asyncio.run(
        WebJudge_Online_Mind2Web_eval(
            task_description, action_history, screenshot_paths, model, score_threshold,
        )
    )
    output_results["image_judge_record"] = record
    output_results["key_points"] = key_points

    response = model.generate(messages)[0]
    predicted_label = extract_predication(response, mode)
    failure_category = extract_failure_category(response) if predicted_label == 0 else None
    grader_reasoning = extract_reasoning(response)

    raw_usage = getattr(model, "last_usage", None)
    eval_usage = None
    if raw_usage is not None:
        if isinstance(raw_usage, dict):
            eval_usage = EvalUsage(**raw_usage)
        elif hasattr(raw_usage, "model_dump"):
            eval_usage = EvalUsage(**raw_usage.model_dump())
        elif hasattr(raw_usage, "__dict__"):
            eval_usage = EvalUsage(**raw_usage.__dict__)

    agent_metrics = None
    raw_metrics = output_results.get("metrics")
    if isinstance(raw_metrics, dict):
        usage_data = raw_metrics.get("usage")
        agent_metrics = AgentMetrics(
            ttft_ms=raw_metrics.get("ttft_ms"),
            end_to_end_ms=raw_metrics.get("end_to_end_ms", 0),
            steps=raw_metrics.get("steps", 0),
            usage=AgentUsage(**usage_data) if isinstance(usage_data, dict) and usage_data else None,
        )

    benchmark_details = {}
    if "image_judge_record" in output_results:
        benchmark_details["image_judge_record"] = output_results["image_judge_record"]
    if "key_points" in output_results:
        benchmark_details["key_points"] = output_results["key_points"]

    additional_prompts = None
    if prompt_snapshots:
        additional_prompts = {
            k: v for k, v in prompt_snapshots.items()
            if k.startswith(("identify_key_points", "judge_image"))
        }

    webjudge_system = prompt_snapshots.get("webjudge_system") if prompt_snapshots else None
    webjudge_user = prompt_snapshots.get("webjudge_user") if prompt_snapshots else None

    eval_details = EvalDetails(
        system_prompt=webjudge_system,
        user_prompt=webjudge_user,
        additional_prompts=additional_prompts if additional_prompts else None,
        response=response,
        reasoning=grader_reasoning,
        eval_usage=eval_usage,
        agent_metrics=agent_metrics,
        benchmark_details=benchmark_details,
    )

    agent_timestamp = agent_result.get("timestamp") or datetime.now(UTC)

    agent_result_ref = AgentResultRef(
        task_id=task_id,
        timestamp=agent_timestamp,
        result_dir=str(trajectory_dir),
        model_id=agent_result.get("model_id", ""),
        browser_id=agent_result.get("browser_id", ""),
    )

    logger.info("Finish evaluation for %s", task_description)
    return EvalResult(
        task_id=task_id,
        task=task_description or "",
        timestamp=datetime.now(UTC),
        agent_result_ref=agent_result_ref,
        predicted_label=predicted_label,
        model_id=agent_result.get("model_id", ""),
        browser_id=agent_result.get("browser_id", ""),
        evaluation_details=eval_details,
        failure_category=failure_category,
    )


def _worker_main(
    task_subset: List[str],
    trajectories_dir: str,
    engine_kwargs: Dict[str, Any],
    score_threshold: int,
    mode: str,
    queue: multiprocessing.Queue,
) -> None:
    """Multiprocessing worker: run _judge_one for each task in subset.

    Top-level function so it is picklable. Creates a fresh OpenaiEngine per
    subprocess (the OpenAI client is not picklable across the fork boundary).
    """
    from browseruse_bench.utils import setup_logger

    # The "done" sentinel must always be sent so the parent's `queue.get()` loop
    # in `_run_iteration` can terminate. A try/finally around the whole body
    # guards against early failures (e.g. OpenaiEngine constructor raising on
    # bad credentials, network connect error) that would otherwise leave the
    # parent blocked forever and zombie processes accumulating.
    try:
        setup_logger("online-mind2web-eval")
        model = OpenaiEngine(**engine_kwargs)
        traj_root = Path(trajectories_dir)
        for task_id in task_subset:
            trajectory_dir = traj_root / task_id
            try:
                with open(trajectory_dir / "result.json", encoding="utf-8") as fh:
                    agent_result = json.load(fh)
                result = _judge_one(task_id, trajectory_dir, agent_result, model, score_threshold, mode)
                queue.put(("ok", result.model_dump(mode="json")))
            except (OSError, ValueError, RuntimeError, openai.OpenAIError) as exc:
                logger.error("Worker failed for task %s: %s", task_id, exc)
                queue.put(("err", task_id))
    except (OSError, ValueError, RuntimeError, openai.OpenAIError) as exc:
        logger.error("Worker init failed: %s", exc)
    finally:
        queue.put(("done", None))


def _start_progress_monitor(output_json_path: Path, total_tasks: int, poll_interval: int):
    """Spawn a daemon thread that logs cumulative evaluated count from JSONL."""
    stop_event = Event()
    pattern = re.compile(r'"task_id"\s*:\s*"([^"]+)"')

    def _monitor():
        seen = set()
        last_count = -1
        last_pos = 0
        while not stop_event.is_set():
            done = last_count if last_count >= 0 else 0
            if output_json_path.exists():
                try:
                    with open(output_json_path, encoding="utf-8") as fh:
                        fh.seek(0, os.SEEK_END)
                        size = fh.tell()
                        if size < last_pos:
                            seen.clear()
                            last_pos = 0
                        fh.seek(last_pos)
                        for line in fh:
                            for tid in pattern.findall(line):
                                seen.add(tid)
                        last_pos = fh.tell()
                        done = len(seen)
                except OSError as exc:
                    logger.warning("progress monitor read failed: %s", exc)
            if done != last_count:
                logger.info("[PROGRESS] Evaluated %d/%d tasks", done, total_tasks)
                last_count = done
            stop_event.wait(poll_interval)

    thread = Thread(target=_monitor, daemon=True)
    thread.start()
    return stop_event, thread


class OnlineMind2WebEvaluator(BaseEvaluator):
    name: ClassVar[str] = "Online-Mind2Web"
    default_mode: ClassVar[str] = "WebJudge_Online_Mind2Web_eval"

    def __init__(self, args, model):
        super().__init__(args, model)
        self._engine: Optional[OpenaiEngine] = None

    def _engine_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.args.model,
            "api_key": self.args.api_key,
            "base_url": self.args.base_url,
        }
        if self.args.temperature is not None:
            kwargs["temperature"] = self.args.temperature
        return kwargs

    @property
    def engine(self) -> OpenaiEngine:
        """List-returning engine required by the WebJudge pipeline.

        WebJudge indexes ``model.generate(...)[0]`` expecting ``list[str]`` (the
        OpenaiEngine contract). The shared ``EvaluationModel.generate`` returns a
        ``str`` for LexBench, so reusing it here would slice the first character
        of every judge response. Build a dedicated OpenaiEngine for both paths.
        """
        if self._engine is None:
            self._engine = OpenaiEngine(**self._engine_kwargs())
        return self._engine

    def results_filename(self) -> str:
        threshold = self.args.score_threshold
        return (
            f"{self.args.mode}_{self.args.model}_score_threshold_"
            f"{threshold}_auto_eval_results.json"
        )

    def summary_filename(self) -> str:
        threshold = self.args.score_threshold
        return (
            f"{self.args.mode}_{self.args.model}_score_threshold_"
            f"{threshold}_summary.json"
        )

    def load_tasks(self) -> Dict[str, Dict[str, Any]]:
        # Mind2Web has no external task file; task_id == trajectory dir name.
        return {p.name: {"task_id": p.name} for p in self.list_completed_tasks()}

    def evaluate_one(self, task_id, task, agent_result, trajectory_dir):
        return _judge_one(
            task_id, trajectory_dir, agent_result, self.engine,
            self.args.score_threshold or 3, self.args.mode,
        )

    def _run_iteration(
        self,
        pending: List[str],
        tasks: Dict[str, Dict[str, Any]],
    ) -> Iterator[EvalResult]:
        num_workers = self.args.num_worker or 1
        if num_workers <= 1 or len(pending) <= 1:
            yield from super()._run_iteration(pending, tasks)
            return

        progress_interval = int(self.args.extra.get("progress_interval", 15))
        stop_event, monitor_thread = _start_progress_monitor(
            self.results_path(), len(pending), max(1, progress_interval),
        )

        engine_kwargs = self._engine_kwargs()

        chunk_size = max(1, len(pending) // num_workers)
        subsets = [pending[i:i + chunk_size] for i in range(0, len(pending), chunk_size)]
        queue: multiprocessing.Queue = multiprocessing.Queue()
        processes: List[multiprocessing.Process] = []
        for subset in subsets:
            p = multiprocessing.Process(
                target=_worker_main,
                args=(
                    subset,
                    str(self.args.trajectories_dir),
                    engine_kwargs,
                    self.args.score_threshold or 3,
                    self.args.mode,
                    queue,
                ),
            )
            p.start()
            processes.append(p)

        try:
            done_workers = 0
            while done_workers < len(processes):
                kind, payload = queue.get()
                if kind == "ok":
                    yield EvalResult(**payload)
                elif kind == "err":
                    logger.warning("worker reported error for task %s", payload)
                elif kind == "done":
                    done_workers += 1
        finally:
            for p in processes:
                p.join(timeout=5)
            stop_event.set()
            monitor_thread.join(timeout=1)

    def _generate_summary(self, records: List[Dict[str, Any]]) -> None:
        super()._generate_summary(records)
        # Augment evaluation_config with score_threshold (legacy field)
        path = self.summary_path()
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                summary = json.load(fh)
            summary.setdefault("evaluation_config", {})["score_threshold"] = self.args.score_threshold
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, ensure_ascii=False, indent=2)
