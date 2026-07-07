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
_FAILURE_MAX_TOKENS: int = int(_root_cfg.get("eval", {}).get("max_tokens") or 2048)
# api_max_images=0 declares the judge model text-only (same switch as LexBench
# eval). None-check rather than `or`: a bare `api_max_images:` key must fall
# back to the default without turning a meaningful 0 into it.
_raw_max_images = _root_cfg.get("eval", {}).get("api_max_images")
_FAILURE_TEXT_ONLY: bool = int(50 if _raw_max_images is None else _raw_max_images) == 0

# Sentinel written by LexBench coverage backfill for tasks that were never
# judged; it must survive attribution so eval resume can find those records.
NOT_EVALUATED_SENTINEL = "not_evaluated"

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

FAILURE_TAXONOMY: dict[str, tuple[str, str]] = {
    "H1": ("Harness", "Execution Defect"),
    "H2": ("Harness", "Orchestration Guard Absence"),
    "M1": ("Model", "Task Planning"),
    "M2": ("Model", "Page Understanding & Grounding"),
    "M3": ("Model", "Evidence Fidelity"),
    "M4": ("Model", "Error Recovery"),
    "M5": ("Model", "Tool/Structured Output"),
    "M6": ("Model", "Model Service Error"),
    "E1": ("Environment", "Bot Defense"),
    "E2": ("Environment", "Access Barrier"),
    "E3": ("Environment", "Site Limitation"),
    "OTHER": ("Other", "Other"),
}

# Deterministic mapping to the pre-fusion single-label codes, kept for
# continuity of historical reports. "U" marks attribution-pipeline failures
# and is never selectable by the judge.
LEGACY_CATEGORY_MAP: dict[str, str] = {
    "H1": "A2",
    "H2": "A4",
    "M1": "A1",
    "M2": "A1",
    "M3": "A1",
    "M4": "A1",
    "M5": "A2",
    "M6": "A3",
    "E1": "B1",
    "E2": "B2",
    "E3": "C2",
    "OTHER": "OTHER",
    "U": "U",
}


def legacy_category(code: str) -> str:
    """Map a unified taxonomy code to the pre-fusion A/B/C code."""
    return LEGACY_CATEGORY_MAP.get(code, "U")


FAILURE_CLASSIFICATION_SYSTEM_PROMPT = """You are an expert browser-agent benchmark analyst. A browser agent failed a benchmark task. Classify the failure into the taxonomy below.

Use the supplied task description, agent action history, agent final answer (including any runtime error), evaluator feedback, and screenshots. Prefer evidence from the trajectory and evaluator feedback over assumptions.

## Taxonomy

### H: Harness causes (the agent framework/scaffolding around the model)

- **H1 Execution Defect**: The framework mishandles a VALID model decision: fails to parse or execute well-formed model output, coordinate-mapping defects (click lands on a different element than the model selected), artifact/file write failures, session plumbing bugs.
- **H2 Orchestration Guard Absence**: The framework withholds information or guards the model needs: an action failure is never surfaced back to the model, no stuck-state detection despite the model being misled about page state, budget mismanagement. Only use when the framework side is provable from the trajectory.

### M: Model causes (the LLM's own capability or service)

- **M1 Task Planning**: Bad task decomposition or path planning; an EXPLICIT stated requirement ignored (required website, fields, output format, item count, safety/legal response). For requirement violations, point to the specific stated requirement. Do NOT use M1 merely because the task is incomplete - attribute the incompleteness to its cause.
- **M2 Page Understanding & Grounding**: Misreads the page, DOM, or screenshot; selects the wrong element, entity, item, date, filter, or sort; fails to enforce "latest", "highest", "most viewed", "top N", date windows, or comparison criteria on usable pages.
- **M3 Evidence Fidelity**: Fails to extract available information, extracts wrong fields, mixes fields across items, fabricates or hallucinates values, reports unverifiable data, or answers without enough evidence.
- **M4 Error Recovery**: The failure signal was visible in the model's context, yet it repeats the same or equivalently futile actions, never switches strategy, wastes the step/time budget, or abandons remaining sub-items. Stuck loops belong here unless the framework provably hid the error (then H2).
- **M5 Tool/Structured Output**: The model emits malformed action JSON or invalid tool calls, produces a final answer or required file in the wrong structure/format, or omits the final response.
- **M6 Model Service Error**: The LLM service itself fails: no response, API timeout, provider rate limiting, context length exceeded, parameter error, or content-filter rejection of the agent's own model calls. Infrastructure failure, not reasoning quality.

### E: Environment causes (the external web environment)

- **E1 Bot Defense**: CAPTCHA, Cloudflare, PerimeterX, slider verification, "robot or human", 403 caused by automation, rate limits, "Too Many Requests", security-control pages, abnormal-traffic blocks.
- **E2 Access Barrier**: Login walls, session expiry, SMS/QR authentication, membership, VIP, paywall, permissions, account-only views, paid downloads, copyright or regional access restrictions.
- **E3 Site Limitation**: Site down, unreachable, 404/server errors, empty DOM or SPA rendering failure, missing filters/data, or the target content genuinely does not exist on the specified site.

### OTHER
Use OTHER only when none of the categories captures the core failure; then provide a short phrase in other_phrase.

## Decision order (apply in this order)

1. **Environment first**: Did the site/environment block or break the needed path (E1/E2/E3)? Include the E code whenever an external obstacle substantially contributed, even if the agent also made mistakes afterwards.
2. **Harness second**: Did the framework mishandle a valid model decision (H1) or provably withhold needed feedback/guards (H2)? The criterion is objective: the model's intended action is visible in the action history and correct, yet the executed effect differs.
3. **Model last**: Otherwise attribute to the model capability that failed: planning/requirements (M1), page understanding and grounding (M2), evidence fidelity (M3), error recovery (M4), tool/structured output (M5). Service-level failures are M6 regardless of order.

Additional tie-breakers:
- Stuck behavior: the failure was visible to the model yet behavior did not adapt -> M4; the framework hid the failure from the model -> H2.
- Wrong element clicked: the model selected the wrong element -> M2; the model selected the right element but the click landed elsewhere -> H1.
- Malformed output: emitted by the model -> M5; valid output mishandled by the framework -> H1.
- "Task incomplete" is an outcome, not a category: code its cause.

## Multi-label rules

- Assign every category that substantially contributed to the failed outcome; one or multiple codes.
- Choose primary_code as the most direct cause that explains why the run failed.

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
                    "items": {"type": "string", "enum": list(FAILURE_TAXONOMY)},
                    "minItems": 1,
                },
                "primary_code": {"type": "string", "enum": list(FAILURE_TAXONOMY)},
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

    if _FAILURE_TEXT_ONLY and screenshots:
        logger.info(
            "   Text-only judge: omitting %d screenshots from failure classification "
            "(api_max_images=0)",
            len(screenshots),
        )
        user_text += (
            "\n\nNote: Screenshot images were omitted because the evaluation model is "
            "text-only. Classify using the action history, agent answer, and evaluator "
            "feedback."
        )
        screenshots = []

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
            max_tokens=_FAILURE_MAX_TOKENS,
            temperature=temperature,
            response_format=FAILURE_CLASSIFICATION_RESPONSE_FORMAT,
        )
    except MODEL_GENERATE_EXCEPTIONS as exc:
        logger.error("   [FAILED] Classification failed: %s", exc)
        return {
            # "U" (unclassified) keeps classification-pipeline failures out of
            # the H/M/E buckets; M6 is reserved for LLM service errors that
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


_CODE_PATTERN = "|".join(sorted((re.escape(c) for c in FAILURE_TAXONOMY), key=len, reverse=True))
_PRIMARY_FALLBACK_RE = re.compile(r'"primary_code"\s*:\s*"?(' + _CODE_PATTERN + r')(?![\w.])')
_CODES_FALLBACK_RE = re.compile(r'"codes"\s*:\s*\[\s*"(' + _CODE_PATTERN + r')(?![\w.])')
_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def _parse_classification_response(response: str) -> dict[str, Any]:
    """Parse a multi-label classification response, tolerating truncation."""
    try:
        parsed = json.loads(_JSON_FENCE_RE.sub("", response))
    except json.JSONDecodeError:
        parsed = None

    codes: list[str] = []
    primary = None
    reasoning = ""
    other_phrase = None
    if isinstance(parsed, dict):
        codes = [c for c in parsed.get("codes") or [] if c in FAILURE_TAXONOMY]
        primary = parsed.get("primary_code")
        reasoning = parsed.get("reasoning", "") or ""
        other_phrase = parsed.get("other_phrase") or None

    if primary not in FAILURE_TAXONOMY:
        # Recover from a max_tokens-truncated JSON response: grab the
        # (possibly unterminated) primary_code or first codes entry directly.
        match = _PRIMARY_FALLBACK_RE.search(response) or _CODES_FALLBACK_RE.search(response)
        primary = match.group(1) if match else None

    if primary not in FAILURE_TAXONOMY and codes:
        primary = codes[0]
    if primary not in FAILURE_TAXONOMY:
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

    has_inline_fields = bool(
        (result.get("agent_response") or result.get("response")) and result.get("action_history")
    )
    agent_result = {} if has_inline_fields else _load_agent_result(trajectories_dir, task_id)
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
        **classification,
        "legacy_category": legacy_category(classification["category"]),
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

        existing = result.get("failure_category")
        # Synthetic never-judged placeholders must keep their sentinel so eval
        # resume can re-judge them; "U" marks an attribution-pipeline failure
        # and is always retried.
        if existing == NOT_EVALUATED_SENTINEL:
            updated_results.append(result)
            continue
        if skip_existing and existing and existing != "U":
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
