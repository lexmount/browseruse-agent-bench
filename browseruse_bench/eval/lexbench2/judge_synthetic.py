#!/usr/bin/env python3
"""Binary LLM-as-judge for LexBench2.0 synthetic tasks.

Reads rubrics from the task JSONL, reads agent answers and screenshots from trajectory
result directories, calls GPT-4.1 to decide success/failure per task, and reports
success rate + avg steps.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402

from browseruse_bench.eval.lexbench_browser.screenshot_cleaner import clean_screenshots  # noqa: E402
from browseruse_bench.eval.model import encode_image  # noqa: E402

TASKS_JSONL = (
    REPO_ROOT / "browseruse_bench" / "data" / "LexBench2.0" / "synthetic" / "lexbench2_synthetic_tasks.jsonl"
)

LOGGER = logging.getLogger("lexbench2-judge")

NUM_WORKERS = 15
JUDGE_MODEL = "gpt-5.5"
JUDGE_MAX_TOKENS = 8192
JUDGE_TEMPERATURE = 1.0
IMAGE_SCALE_FACTOR = 0.2
API_MAX_IMAGES = 50
MAX_SCREENSHOTS: int | None = None
SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

SYSTEM_PROMPT = """\
You are a professional AI Agent task evaluation expert. Please evaluate the Agent's performance based on the task description, rubric requirements, Agent's screenshot trajectory, action history, and final answer.

You will be given:
1. The task description
2. A set of rubric requirements (R1, R2, ...) that define what a successful completion looks like
3. The agent's final answer
4. The agent's action history
5. Key screenshots from the Agent's execution process, in chronological order

For each rubric requirement, judge whether the screenshots, action history, and final answer together provide clear evidence of fulfilling it. Then give an overall binary success verdict.

Rules:
- Base your judgment only on the provided task, rubrics, screenshots, action history, and final answer.
- A rubric is met if the provided evidence contains concrete support matching the requirement.
- Do not require proof to be repeated in the final answer if the attached screenshots or action history clearly establish it.
- If the final answer contradicts the screenshot trajectory or action history, prefer the screenshot trajectory/action history.
- The overall task is SUCCESS only if ALL rubric requirements are met
- If any rubric requirement is unmet or the evidence is absent/fabricated, the overall task is FAILURE
- Be strict: vague or incomplete evidence that doesn't clearly satisfy a requirement should be marked as unmet

Respond in JSON with this exact schema:
{
  "rubric_scores": {"R1": true_or_false, "R2": true_or_false, ...},
  "success": true_or_false,
  "reasoning": "brief explanation of your verdict, noting which rubrics failed if any"
}
"""

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "lexbench2_judge",
        "schema": {
            "type": "object",
            "properties": {
                "rubric_scores": {
                    "type": "object",
                    "additionalProperties": {"type": "boolean"},
                },
                "success": {"type": "boolean"},
                "reasoning": {"type": "string"},
            },
            "required": ["rubric_scores", "success", "reasoning"],
            "additionalProperties": False,
        },
    },
}


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _compact_text(text: str, limit: int) -> str:
    text = text.strip()
    if limit <= 0 or len(text) <= limit:
        return text
    head = max(1, limit // 2)
    tail = max(1, limit - head)
    return text[:head] + f"\n...[truncated {len(text) - limit} chars]...\n" + text[-tail:]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _extract_number(filename: str) -> int:
    match = re.search(r"\d+", filename)
    return int(match.group()) if match else 0


def _find_screenshots(trajectory_dir: Path) -> list[Path]:
    if not trajectory_dir.exists():
        return []
    return [
        file
        for file in sorted(trajectory_dir.iterdir(), key=lambda path: _extract_number(path.name))
        if file.suffix.lower() in SCREENSHOT_SUFFIXES
    ]


def _select_screenshots(screenshot_paths: list[Path]) -> tuple[list[Path], int]:
    original_count = len(screenshot_paths)
    effective_max = API_MAX_IMAGES
    if MAX_SCREENSHOTS is not None:
        effective_max = min(MAX_SCREENSHOTS, API_MAX_IMAGES)

    if len(screenshot_paths) > effective_max:
        indices = [0] + [
            int(i * (len(screenshot_paths) - 1) / (effective_max - 1))
            for i in range(1, effective_max)
        ]
        screenshot_paths = [screenshot_paths[i] for i in indices]

    return screenshot_paths, original_count


def _image_content(screenshot_paths: list[Path]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for screenshot_path in screenshot_paths:
        try:
            image = Image.open(screenshot_path)
            base64_image = encode_image(image, scale_factor=IMAGE_SCALE_FACTOR)
        except OSError as exc:
            LOGGER.warning("Failed to load screenshot %s: %s", screenshot_path, exc)
            continue
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}",
                    "detail": "high",
                },
            }
        )
    return content


class SimpleChatModel:
    def __init__(self, model: str, api_key: str, base_url: str) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        if not self.api_key:
            raise ValueError("API key required: set OPENAI_API_KEY or EVAL_MODEL_API_KEY")

    def _chat_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def generate(self, messages: list[dict[str, Any]], response_format: Any = None) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": JUDGE_MAX_TOKENS,
            "temperature": JUDGE_TEMPERATURE,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        request = urllib.request.Request(
            self._chat_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(request, timeout=120) as resp:
                    body = resp.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if (exc.code >= 500 or exc.code == 429) and attempt < 3:
                    LOGGER.warning("HTTP %s attempt %s/3, retrying: %s", exc.code, attempt, detail[:200])
                    time.sleep(4 * attempt)
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {detail[:400]}") from exc
            except (socket.timeout, TimeoutError, urllib.error.URLError) as exc:
                if attempt < 3:
                    LOGGER.warning("Connection error attempt %s/3, retrying: %s", attempt, exc)
                    time.sleep(4 * attempt)
                    continue
                raise RuntimeError(f"Connection error: {exc}") from exc

        parsed = json.loads(body)
        return parsed["choices"][0]["message"].get("content") or ""


def _build_user_prompt(task: dict[str, Any], result: dict[str, Any], screenshot_count: int) -> str:
    rubrics = task.get("rubrics", {})
    rubric_lines = []
    for key in sorted(rubrics.keys()):
        req = rubrics[key].get("requirement", "")
        rubric_lines.append(f"{key}: {req}")

    action_history = result.get("action_history") or []
    action_summary = "\n".join(f"{i+1}. {a}" for i, a in enumerate(action_history))

    answer = result.get("answer") or ""

    steps = (result.get("metrics") or {}).get("steps") or "unk"
    execution_time = (result.get("metrics") or {}).get("end_to_end_ms") or "unk"

    return f"""## Task Description
{task.get("query", "")}

## Target Website
{task.get("target_website", "")}

## Rubric Requirements
{chr(10).join(rubric_lines)}

## Agent Execution
- Steps Count: {steps}
- Execution Time: {execution_time}ms
- Screenshot Count: {screenshot_count} images

Note: I will provide key screenshots from the Agent's execution process in chronological order. Please evaluate based on the screenshots, action history, and final answer.

## Agent's Final Answer
{_compact_text(answer, 6000)}

## Agent Action History (all steps)
{action_summary}
"""


def _judge_one(
    task: dict[str, Any],
    result: dict[str, Any],
    trajectory_dir: Path,
    model: SimpleChatModel,
) -> dict[str, Any]:
    task_id = task["id"]
    screenshot_paths = _find_screenshots(trajectory_dir / "trajectory")
    screenshot_paths, clean_stats = clean_screenshots(
        screenshot_paths, remove_blank=True, remove_duplicates=True,
    )
    screenshot_paths, original_screenshot_count = _select_screenshots(screenshot_paths)

    user_prompt = _build_user_prompt(task, result, len(screenshot_paths))
    user_content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    user_content.extend(_image_content(screenshot_paths))

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    raw = ""
    for attempt in range(1, 3):
        raw = model.generate(messages, response_format=RESPONSE_FORMAT)
        if raw.strip():
            break
        LOGGER.warning("Empty response for %s attempt %s/2", task_id, attempt)

    if not raw.strip():
        raise RuntimeError(f"empty model response for {task_id}")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(match.group(0)) if match else {}

    if not isinstance(parsed, dict):
        parsed = {}

    raw_rubric_scores = parsed.get("rubric_scores") or {}
    expected_rubrics = sorted((task.get("rubrics") or {}).keys())
    rubric_scores = {
        rubric: bool(raw_rubric_scores.get(rubric, False))
        for rubric in expected_rubrics
    }
    success = bool(rubric_scores) and all(rubric_scores.values())
    reasoning = str(parsed.get("reasoning") or "").strip()

    steps = (result.get("metrics") or {}).get("steps") or 0

    return {
        "task_id": task_id,
        "success": success,
        "rubric_scores": rubric_scores,
        "judge_reported_success": bool(parsed.get("success", False)),
        "reasoning": reasoning,
        "steps": steps,
        "screenshot_count": len(screenshot_paths),
        "original_screenshot_count": original_screenshot_count,
        "screenshot_clean_stats": clean_stats,
        "judge_input": "screenshots_action_history_final_answer",
        "judged_at": _utc_now(),
    }


def _load_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {str(row["task_id"]): row for row in _read_jsonl(path) if row.get("task_id")}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Judge LexBench2.0 synthetic run results.")
    parser.add_argument(
        "--tasks-jsonl",
        type=Path,
        default=TASKS_JSONL,
        help="LexBench2.0 task JSONL file.",
    )
    parser.add_argument(
        "--trajectories-dir",
        type=Path,
        required=True,
        help="Run tasks directory, e.g. experiments/LexBench2.0/synthetic/browser-use/<model>/<timestamp>/tasks.",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Output JSONL path. Defaults to <run_dir>/judge_results_<judge_model>_visual.jsonl.",
    )
    parser.add_argument(
        "--summary-path",
        type=Path,
        default=None,
        help="Summary JSON path. Defaults to <run_dir>/judge_summary_<judge_model>_visual.json.",
    )
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--num-workers", type=int, default=NUM_WORKERS)
    parser.add_argument("--max-tokens", type=int, default=JUDGE_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=JUDGE_TEMPERATURE)
    parser.add_argument("--image-scale-factor", type=float, default=IMAGE_SCALE_FACTOR)
    parser.add_argument("--api-max-images", type=int, default=API_MAX_IMAGES)
    parser.add_argument("--max-screenshots", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    global API_MAX_IMAGES, IMAGE_SCALE_FACTOR, JUDGE_MAX_TOKENS, JUDGE_TEMPERATURE, MAX_SCREENSHOTS

    args = _parse_args()
    API_MAX_IMAGES = args.api_max_images
    IMAGE_SCALE_FACTOR = args.image_scale_factor
    JUDGE_MAX_TOKENS = args.max_tokens
    JUDGE_TEMPERATURE = args.temperature
    MAX_SCREENSHOTS = args.max_screenshots

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _load_env_file(REPO_ROOT / ".env")

    api_key = args.api_key or os.environ.get("EVAL_MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    base_url = args.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    model = SimpleChatModel(args.judge_model, api_key, base_url)

    output_path = args.output_path
    summary_path = args.summary_path
    run_dir = args.trajectories_dir.parent
    judge_suffix = args.judge_model.replace("/", "_")
    if output_path is None:
        output_path = run_dir / f"judge_results_{judge_suffix}_visual.jsonl"
    if summary_path is None:
        summary_path = run_dir / f"judge_summary_{judge_suffix}_visual.json"

    tasks = {row["id"]: row for row in _read_jsonl(args.tasks_jsonl)}
    LOGGER.info("Loaded %d tasks from JSONL", len(tasks))

    traj_dirs = sorted(args.trajectories_dir.iterdir()) if args.trajectories_dir.exists() else []
    LOGGER.info("Found %d trajectory directories", len(traj_dirs))

    existing = _load_existing(output_path)
    LOGGER.info("Already judged: %d", len(existing))

    pending: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    for traj_dir in traj_dirs:
        task_id = traj_dir.name
        if task_id not in tasks:
            LOGGER.warning("No task definition for %s, skipping", task_id)
            continue
        if task_id in existing:
            continue
        result_path = traj_dir / "result.json"
        if not result_path.exists():
            LOGGER.warning("No result.json for %s, skipping", task_id)
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        pending.append((tasks[task_id], result, traj_dir))

    LOGGER.info("Pending to judge: %d", len(pending))

    results: list[dict[str, Any]] = list(existing.values())
    failed_count = 0

    if pending:
        with ThreadPoolExecutor(max_workers=args.num_workers) as executor:
            future_map = {
                executor.submit(_judge_one, task, result, traj_dir, model): task["id"]
                for task, result, traj_dir in pending
            }
            total = len(future_map)
            done_count = 0
            for future in as_completed(future_map):
                task_id = future_map[future]
                done_count += 1
                try:
                    judged = future.result()
                    results.append(judged)
                    LOGGER.info(
                        "[%d/%d] %s → %s",
                        done_count,
                        total,
                        task_id,
                        "SUCCESS" if judged["success"] else "FAILURE",
                    )
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error("Failed to judge %s: %s", task_id, exc)
                    failed_count += 1

    results.sort(key=lambda r: r["task_id"])
    _write_jsonl(output_path, results)

    judged = [r for r in results if "success" in r]
    successful = [r for r in judged if r["success"]]
    all_steps = [r["steps"] for r in judged if r.get("steps")]
    success_steps = [r["steps"] for r in successful if r.get("steps")]

    success_rate = len(successful) / len(judged) if judged else 0.0
    avg_steps = sum(all_steps) / len(all_steps) if all_steps else 0.0
    avg_steps_success = sum(success_steps) / len(success_steps) if success_steps else 0.0

    summary = {
        "judged_at": _utc_now(),
        "judge_model": args.judge_model,
        "total_tasks_in_jsonl": len(tasks),
        "trajectories_found": len(traj_dirs),
        "judged_count": len(judged),
        "success_count": len(successful),
        "failure_count": len(judged) - len(successful),
        "success_rate": round(success_rate, 4),
        "avg_steps": round(avg_steps, 2),
        "avg_steps_success": round(avg_steps_success, 2),
        "judge_errors": failed_count,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("\n" + "=" * 50)
    print(f"Tasks in JSONL:      {len(tasks)}")
    print(f"Trajectories found:  {len(traj_dirs)}")
    print(f"Judged:              {len(judged)}")
    print(f"Successful:          {len(successful)}")
    print(f"Success rate:        {success_rate:.1%}")
    print(f"Avg steps (all):     {avg_steps:.1f}")
    print(f"Avg steps (success): {avg_steps_success:.1f}")
    if failed_count:
        print(f"Judge errors:        {failed_count}")
    print("=" * 50)
    print(f"Results: {output_path}")
    print(f"Summary: {summary_path}")

    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
