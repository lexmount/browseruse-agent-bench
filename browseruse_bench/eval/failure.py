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

M_TAXONOMY: dict[str, tuple[str, str]] = {
    "M1.1": ("Task Reasoning", "Requirement Following"),
    "M1.2": ("Task Reasoning", "Target Selection"),
    "M1.3": ("Task Reasoning", "Evidence Grounding"),
    "M2.1": ("Action Execution", "UI Misoperation"),
    "M2.2": ("Action Execution", "Infinite Loop"),
    "M2.3": ("Action Execution", "Format Breakdown"),
    "M2.4": ("Action Execution", "Model Service Error"),
    "M3.1": ("Web Constraints", "Bot Defense"),
    "M3.2": ("Web Constraints", "Access Barrier"),
    "M3.3": ("Web Constraints", "Site Limitation"),
    "OTHER": ("Other", "Other"),
}

# Deterministic mapping to the pre-fusion single-label codes, kept for
# continuity of historical reports. "U" marks attribution-pipeline failures
# and is never selectable by the judge.
LEGACY_CATEGORY_MAP: dict[str, str] = {
    "M1.1": "A1",
    "M1.2": "A1",
    "M1.3": "A1",
    "M2.1": "A2",
    "M2.2": "A4",
    "M2.3": "A2",
    "M2.4": "A3",
    "M3.1": "B1",
    "M3.2": "B2",
    "M3.3": "C2",
    "OTHER": "OTHER",
    "U": "U",
}


def legacy_category(code: str) -> str:
    """Map a unified taxonomy code to the pre-fusion A/B/C code."""
    return LEGACY_CATEGORY_MAP.get(code, "U")


FAILURE_CLASSIFICATION_SYSTEM_PROMPT = """You are an expert browser-agent benchmark analyst. A browser agent failed a benchmark task. Classify the failure into the taxonomy below.

Use the supplied task description, agent action history, agent final answer (including any runtime error), evaluator feedback, and screenshots. Prefer evidence from the trajectory and evaluator feedback over assumptions.

## Taxonomy

### M1 Task Reasoning
Failures in task understanding, decision making, selection, evidence use, or safety judgment.

- **M1.1 Requirement Following**: The agent misses explicit task requirements, required websites, required fields, required output format, required number of items, or the required safety/legal response. Use this for incomplete fulfillment of the user's objective even when the browser interactions were technically possible.
- **M1.2 Target Selection**: The agent applies the wrong scope, entity, date, city, item, channel, season, product, ranking criterion, filter, sort order, or comparison logic. Use this when it reaches usable pages but chooses the wrong target or fails to enforce "latest", "highest", "most viewed", "top N", date windows, or cross-platform comparison criteria.
- **M1.3 Evidence Grounding**: The agent fails to extract information that is available, extracts the wrong fields, mixes fields from different items, fabricates or hallucinates values, reports unverifiable data, or answers without enough evidence.

### M2 Action Execution
Failures in controlling the browser-agent loop, UI operations, recovery behavior, tool/output protocol, or the model service behind the agent. These are agent-side failures, not external website failures.

- **M2.1 UI Misoperation**: The agent cannot operate normal UI elements: search boxes, buttons, date pickers, dropdowns, filters, tabs, popups, modals, pagination, detail-page links, window/tab switching, or page scrolling. Use this when the site is accessible but the agent cannot drive the interface to the needed state.
- **M2.2 Infinite Loop**: The agent repeats ineffective actions, gets stuck, fails to recover from a bad page state, runs out of steps, times out, or completes only a small part of a long multi-item task due to poor workflow control. Use this for loops, dead ends, and poor long-horizon task management.
- **M2.3 Format Breakdown**: Malformed JSON action output, invalid tool-call structure, parser failures, missing final response, failed file saving, corrupted artifacts, or required output files not being produced. Use this only when protocol or artifact generation is a direct cause of failure.
- **M2.4 Model Service Error**: The LLM service behind the agent fails: no response from the model service, API timeout, provider rate limiting, context length exceeded, parameter error, or content-filter rejection of the agent's own model calls. Infrastructure failure, not reasoning quality.

### M3 Web Constraints
Failures mainly caused by external web environment constraints. These may still expose agent limits, but the primary obstacle is the website or access environment.

- **M3.1 Bot Defense**: The target site blocks automation with CAPTCHA, Cloudflare, PerimeterX, slider verification, "robot or human", 403 caused by automation, rate limits, "Too Many Requests", security control, abnormal traffic, or similar bot-detection defenses.
- **M3.2 Access Barrier**: The needed content or action is blocked by login, session expiry, SMS/QR authentication, membership, VIP, paywall, permissions, account-only views, paid downloads, copyright restrictions, or regional access restrictions.
- **M3.3 Site Limitation**: The site is down, unreachable, returns 404/server errors, has empty DOM or SPA rendering failure, does not expose the requested content, lacks the requested filter/data, or the target content genuinely does not exist on the specified site.

### OTHER
Use OTHER only when none of the categories captures the core failure. If OTHER is used, provide a short phrase in other_phrase. Do not use OTHER for common combinations of the above categories.

## Multi-label rules

- Assign every category that substantially contributed to the failed outcome; one or multiple codes.
- Choose primary_code as the most direct cause that explains why the run failed.
- If the agent is blocked by CAPTCHA or rate limiting, include M3.1 even if it also fails later.
- If the page is accessible but the agent misses filters, sorting, or target selection, use M1.2, not M3.3.
- If the page is accessible and the answer is unsupported, use M1.3.
- If the agent cannot click or manipulate a normal accessible interface, use M2.1.
- If repeated ineffective attempts, timeout, or step exhaustion prevent completion, use M2.2.
- If the run stops because of malformed output or missing artifacts, use M2.3; if the model service itself errored (timeout, rate limit, content filter), use M2.4.

## Output Format

Strictly output a JSON object:
{
  "reasoning": "<How you reached the conclusion from task, screenshots, action history and evaluation feedback>",
  "codes": ["<every contributing category code>"],
  "primary_code": "<the single most direct cause>",
  "other_phrase": "<short phrase when OTHER is used, else null>"
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
                "codes": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(M_TAXONOMY)},
                    "minItems": 1,
                },
                "primary_code": {"type": "string", "enum": list(M_TAXONOMY)},
                "other_phrase": {"type": ["string", "null"]},
            },
            "required": ["reasoning", "codes", "primary_code"],
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
            max_tokens=2048,
            temperature=temperature,
            response_format=FAILURE_CLASSIFICATION_RESPONSE_FORMAT,
        )
    except MODEL_GENERATE_EXCEPTIONS as exc:
        logger.error("   [FAILED] Classification failed: %s", exc)
        return {
            # "U" (unclassified) keeps classification-pipeline failures out of
            # the M buckets; M2.4 is reserved for LLM service errors that
            # happened during the agent run itself.
            "category": "U",
            "codes": [],
            "reasoning": f"Classification error: {exc}",
            "other_phrase": None,
            "raw_response": "",
        }

    # Parse response
    result = _parse_classification_response(response)
    result["raw_response"] = response
    return result


def _parse_classification_response(response: str) -> dict[str, Any]:
    """Parse a multi-label classification response, tolerating truncation."""
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        parsed = None

    codes: list[str] = []
    primary = None
    reasoning = ""
    other_phrase = None
    if isinstance(parsed, dict):
        codes = [c for c in parsed.get("codes") or [] if c in M_TAXONOMY]
        primary = parsed.get("primary_code")
        reasoning = parsed.get("reasoning", "") or ""
        other_phrase = parsed.get("other_phrase") or None

    if primary not in M_TAXONOMY:
        # Recover from a max_tokens-truncated JSON response: grab the
        # (possibly unterminated) primary_code or first codes entry directly.
        match = re.search(r'"primary_code"\s*:\s*"?(M[123]\.[1-4]|OTHER)', response)
        if not match:
            match = re.search(r'"codes"\s*:\s*\[\s*"(M[123]\.[1-4]|OTHER)', response)
        primary = match.group(1) if match else None

    if primary not in M_TAXONOMY and codes:
        primary = codes[0]
    if primary not in M_TAXONOMY:
        logger.warning("   [WARNING] Invalid classification response, defaulting to U")
        primary = "U"
    if primary != "U" and primary not in codes:
        codes.insert(0, primary)

    return {
        "category": primary,
        "codes": codes,
        "reasoning": reasoning,
        "other_phrase": other_phrase,
    }


def _load_agent_result(trajectories_dir: Path, task_id: str) -> dict[str, Any]:
    """Load the agent-side result.json for a task, if present.

    Eval records do not carry the agent answer or action history for every
    benchmark schema (LexBench keeps them only in the run artifacts), so the
    classifier falls back to ``<trajectories_dir>/<task_id>/result.json``.
    """
    result_file = trajectories_dir / task_id / "result.json"
    if not result_file.exists():
        return {}
    try:
        with open(result_file, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("   [WARNING] Failed to load agent result %s: %s", result_file, exc)
        return {}
    return data if isinstance(data, dict) else {}


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

    agent_result = _load_agent_result(trajectories_dir, task_id)
    task_description = result.get("task", "")
    agent_response = (
        result.get("agent_response") or result.get("response") or agent_result.get("answer") or ""
    )
    agent_error = agent_result.get("error")
    if agent_error:
        agent_response = f"{agent_response}\n[Agent runtime error]: {agent_error}".strip()
    evaluator_details = result.get("evaluation_details", {}) or {}
    evaluator_response = (
        evaluator_details.get("grader_response") or evaluator_details.get("response") or ""
    )
    action_history = result.get("action_history") or agent_result.get("action_history") or []
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
        "codes": classification["codes"],
        "reasoning": classification["reasoning"],
        "other_phrase": classification["other_phrase"],
        "legacy_category": legacy_category(classification["category"]),
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
