"""Failure classification utilities for browseruse_bench.

Utility functions related to failure case classification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from browseruse_bench.utils.config_loader import load_config_file
from browseruse_bench.utils.repo_root import REPO_ROOT

_root_cfg = load_config_file(REPO_ROOT / "config.yaml")
_FAILURE_TEMPERATURE: float = float(_root_cfg.get("eval", {}).get("temperature", 0))

try:
    from openai import APIConnectionError, APIError, RateLimitError
except ImportError:
    APIConnectionError = None
    APIError = None
    RateLimitError = None

try:
    from PIL import Image
except ImportError:
    Image = None

from browseruse_bench.eval.model import EvaluationModel, default_temperature_for_model, encode_image

logger = logging.getLogger(__name__)

MODEL_GENERATE_EXCEPTIONS: tuple[type[BaseException], ...] = tuple(
    exc
    for exc in (APIError, APIConnectionError, RateLimitError)
    if isinstance(exc, type) and issubclass(exc, BaseException)
) + (OSError, RuntimeError, TypeError, ValueError, ImportError)

# ============================================================================
# Failure Classification Constants
# ============================================================================

FAILURE_CLASSIFICATION_SYSTEM_PROMPT = """You are a professional browser Agent failure analysis expert. Your task is to analyze the root cause of the Agent's task failure based on the provided information and provide a specific classification category.

## Classification System

**A: Agent Causes**
- **A1**: Agent capability insufficiency
  - Model issues
    - Environment understanding error: Visual/Page understanding error (Misreading DOM text, truncated extraction, screenshot misunderstanding)
    - Plan error: Intent parsing/Task planning failure (Model misunderstood task, missed key conditions, unreasonable path planning or missing key steps)
      - Leading to wrong results: Wrong input content, arriving at wrong page, unable to auto-login etc.
  - Model service error: No response from model service due to context length exceeded or parameter error etc.
  - Agent paradigm issues
    - Context engineering issue: Redundant or overly long context leading to model errors
    - Prompt engineering issue: Used plan-and-execute, react, reflexion paradigms but insufficient for complex problems, leading to inaccurate planning or sudden stop (no new steps planned)

- **A2**: Agent Code BUG
  - Coordinate mismatch between image and browser leading to wrong clicks, failed selection (dropdown) etc.
  - Failed to parse model result, tool call failure etc., leading to execution failure or stuck

**B: Browser Causes**
- **B1**: Triggered bot detection (Direct access forbidden or CAPTCHA triggered)
- **B2**: Unable to login (Session expired, login forbidden by risk control etc.)

**C: Website Causes (Unreachable)**
- **C1**: Network interruption, geo-blocking
- **C2**: Website unavailable (Website itself is down)

## Output Format

Please strictly output a JSON object containing the following fields:
{
  "reasoning": "<Detailed analysis process, explaining how you reached the conclusion based on task description, screenshots, action history and evaluation feedback>",
  "category": "<Classification category, must be one of: A1, A2, B1, B2, C1, C2>"
}
"""

FAILURE_CLASSIFICATION_USER_PROMPT = """Please analyze the following failed browser Agent task:

**Task Description**:
{task_description}

**Agent Action History** (Recent actions):
{action_history}

**Agent Final Response**:
{agent_response}

**Evaluation Model Feedback**:
{evaluator_response}

**The last 3 screenshots of the task execution process** are provided below, showing the final state of the task.

Please analyze the failure cause and provide classification based on the above information."""

FAILURE_CLASSIFICATION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "failure_classification",
        "schema": {
            "type": "object",
            "properties": {
                "reasoning": {"type": "string"},
                "category": {"type": "string", "enum": ["A1", "A2", "B1", "B2", "C1", "C2"]},
            },
            "required": ["reasoning", "category"],
            "additionalProperties": False,
        },
    },
}


# ============================================================================
# Failure Classification Functions
# ============================================================================


def _collect_task_screenshots(trajectories_dir: Path, task_id: str) -> list[str]:
    """Collect list of screenshot file paths for a task.

    Args:
        trajectories_dir: Root directory of trajectories.
        task_id: Task ID.

    Returns:
        List[str]: List of screenshot file paths sorted by chronological order.
    """
    trajectory_dir = trajectories_dir / task_id / "trajectory"
    if not trajectory_dir.exists() or not trajectory_dir.is_dir():
        return []

    def sort_key(path: Path):
        nums = re.findall(r"\d+", path.name)
        return int(nums[0]) if nums else path.name

    screenshot_files = [
        f
        for f in trajectory_dir.iterdir()
        if f.is_file() and f.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp", ".gif"]
    ]
    screenshot_files.sort(key=sort_key)
    return [str(f) for f in screenshot_files]


def classify_single_failure(
    task_description: str,
    screenshots: list[str],  # List of file paths
    action_history: list[str],
    agent_response: str,
    evaluator_response: str,
    model: EvaluationModel,
    max_screenshots: int = 3,
) -> dict[str, Any]:
    """Classify a single failure case.

    Args:
        task_description: Description of the task.
        screenshots: List of screenshot file paths (chronological order).
        action_history: List of agent action history.
        agent_response: Final response from the agent.
        evaluator_response: Feedback from the evaluation model.
        model: Evaluation model instance.
        max_screenshots: Maximum number of screenshots to use (taken from the end).

    Returns:
        Dict[str, Any]: Dictionary containing classification results:
        {
            "category": "A1",  # Failure category
            "reasoning": "...",  # Reasoning process
            "raw_response": "..."  # Raw response
        }
    """
    if Image is None:
        raise ImportError(
            "PIL is required for failure classification. Install with: pip install Pillow"
        )

    # Prepare action history text
    if isinstance(action_history, list):
        # Take only last 10 actions to avoid context overflow
        recent_actions = action_history[-10:] if len(action_history) > 10 else action_history
        action_text = "\n".join([f"{i+1}. {action}" for i, action in enumerate(recent_actions)])
    else:
        action_text = str(action_history)

    # Prepare user prompt text part
    user_text = FAILURE_CLASSIFICATION_USER_PROMPT.format(
        task_description=task_description,
        action_history=action_text if action_text else "No action history",
        agent_response=agent_response if agent_response else "No response",
        evaluator_response=evaluator_response if evaluator_response else "No evaluation feedback",
    )

    # Prepare message content (text + image)
    content = [{"type": "text", "text": user_text}]

    # Add screenshots (take last max_screenshots)
    if screenshots:
        last_screenshots = (
            screenshots[-max_screenshots:] if len(screenshots) > max_screenshots else screenshots
        )
        for screenshot_path in last_screenshots:
            try:
                screenshot_path = Path(screenshot_path)
                if screenshot_path.exists() and screenshot_path.is_file():
                    # Read and encode screenshot
                    img = Image.open(screenshot_path)
                    base64_img = encode_image(img, scale_factor=0.8)  # Compress to save tokens
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_img}",
                                "detail": "high",
                            },
                        }
                    )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                logger.warning(
                    "   [WARNING] Failed to load screenshot %s: %s", screenshot_path, exc
                )
                continue

    # Construct messages
    messages = [
        {"role": "system", "content": FAILURE_CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    # Call model
    try:
        temperature = _FAILURE_TEMPERATURE
        if getattr(model, "model", "").lower().startswith("gpt-5"):
            temperature = default_temperature_for_model(model.model)

        response = model.generate(
            messages,
            max_tokens=768,
            temperature=temperature,
            response_format=FAILURE_CLASSIFICATION_RESPONSE_FORMAT,
        )
    except MODEL_GENERATE_EXCEPTIONS as exc:
        logger.error("   [FAILED] Classification failed: %s", exc)
        return {
            "category": "A1",  # Default category
            "reasoning": f"Classification error: {exc}",
            "raw_response": "",
        }

    # Parse response
    category = None
    reasoning = ""

    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        category = parsed.get("category")
        reasoning = parsed.get("reasoning", "") or ""

    if not category:
        category_match = re.search(r"Category[：:]\s*([ABC][123]?)", response, re.IGNORECASE)
        if category_match:
            category = category_match.group(1).upper()

    if not reasoning:
        reasoning_match = re.search(
            r"Reasoning[：:]\s*(.+?)(?=Category[：:]|$)", response, re.IGNORECASE | re.DOTALL
        )
        if reasoning_match:
            reasoning = reasoning_match.group(1).strip()

    # Validate category
    valid_categories = ["A1", "A2", "B1", "B2", "C1", "C2"]
    if category not in valid_categories:
        logger.warning(f"   [WARNING] Invalid category: {category}, defaulting to U")
        category = "U"

    return {"category": category, "reasoning": reasoning, "raw_response": response}


def classify_failure_case(
    result: dict[str, Any],
    trajectories_dir: Path,
    model: EvaluationModel,
    *,
    max_screenshots: int = 3,
) -> dict[str, Any]:
    """Classify a single failure case (extracting info from result dict).

    Args:
        result: Evaluation result dictionary (containing task_id, task, agent_response, etc.).
        trajectories_dir: Root directory of trajectories.
        model: Evaluation model instance.
        max_screenshots: Maximum number of screenshots to use.

    Returns:
        Dict[str, Any]: Updated result dictionary (with failure_category and failure_classification fields added).
    """
    task_id = result.get("task_id", "")
    logger.info(f"   [INFO] Classifying failure case: {task_id or '<unknown>'}")

    task_description = result.get("task", "")
    agent_response = result.get("agent_response", "") or result.get("response", "")
    evaluator_details = result.get("evaluation_details", {}) or {}
    evaluator_response = evaluator_details.get("grader_response", "")
    action_history = result.get("action_history", [])
    screenshots = _collect_task_screenshots(trajectories_dir, task_id)

    classification = classify_single_failure(
        task_description=task_description,
        screenshots=screenshots,
        action_history=action_history,
        agent_response=agent_response,
        evaluator_response=evaluator_response,
        model=model,
        max_screenshots=max_screenshots,
    )

    result["failure_category"] = classification["category"]
    details = result.get("evaluation_details")
    if not isinstance(details, dict):
        details = {}
        result["evaluation_details"] = details
    details["failure_classification"] = {
        "category": classification["category"],
        "reasoning": classification["reasoning"],
        "raw_response": classification["raw_response"],
    }

    logger.info(f"      Classification result: {classification['category']}")
    return result


def classify_failures_batch(
    eval_results: list[dict[str, Any]],
    trajectories_dir: Path,
    model: EvaluationModel,
    skip_existing: bool = True,
    max_samples: int | None = None,
    num_workers: int = 4,
) -> list[dict[str, Any]]:
    """Batch classify failure cases.

    Args:
        eval_results: List of evaluation results (each element contains task_id, predicted_label, etc.).
        trajectories_dir: Root directory of trajectories (containing subdirectories for each task).
        model: Evaluation model instance.
        skip_existing: Whether to skip cases that are already classified.
        max_samples: Maximum number of samples to process (None for all).
        num_workers: Number of concurrent worker threads.

    Returns:
        List[Dict[str, Any]]: Updated list of evaluation results (with failure_category field added).
    """

    updated_results = []
    failure_count = 0
    classified_count = 0

    pending: list[dict[str, Any]] = []

    for result in eval_results:
        # Only process failed cases
        if result.get("predicted_label") != 0:
            updated_results.append(result)
            continue

        failure_count += 1

        # If classification exists and skip_existing=True, skip
        if skip_existing and result.get("failure_category"):
            updated_results.append(result)
            continue

        pending.append(result)
        updated_results.append(result)

    if max_samples is not None:
        pending = pending[:max_samples]

    if pending:

        async def _run():
            sem = asyncio.Semaphore(max(1, num_workers))

            async def _classify(res: dict[str, Any]):
                async with sem:
                    return await asyncio.to_thread(
                        classify_failure_case, res, trajectories_dir, model
                    )

            await asyncio.gather(*(_classify(res) for res in pending))

        asyncio.run(_run())
        classified_count = len(pending)
    else:
        classified_count = 0

    logger.info(
        f"\n   [STATS] Classification Stats: Total {failure_count} failed cases, classified {classified_count} this time"
    )

    return updated_results
