"""Odysseys rubric grader."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image

from browseruse_bench.utils import encode_image

logger = logging.getLogger(__name__)

FINAL_JUDGMENT_MAX_COMPLETION_TOKENS = 8192

_SYSTEM_PROMPT = """You are an expert evaluator of web-navigation agent trajectories.

You will receive:
- The user task (for context).
- ONE specific rubric item with a requirement and a verification description.
- The agent's full action history (one line per step).
- Every screenshot from the trajectory, in chronological order.

Your goal is to decide whether this single rubric item is satisfied by the trajectory.

Evaluation rules:
- Judge ONLY the one rubric item you are given; ignore all other implicit requirements.
- Ground your judgment in what the screenshots and actions actually show. Do not invent state.
- Filtering / sorting / form requirements must be applied and confirmed to count as satisfied.
- If the agent was blocked (captcha, access denied, etc.) and therefore could not satisfy the rubric, report failure.

Respond in exactly this format:

Thoughts: <your reasoning, citing specific steps/screenshots>
Status: "success" or "failure"
"""

_STATUS_RE = re.compile(r'Status:\s*["\']?(success|failure)["\']?', re.IGNORECASE)
_THOUGHTS_RE = re.compile(r"Thoughts:\s*(.+?)(?:Status:|$)", re.IGNORECASE | re.DOTALL)


def _stringify_action(action: Any) -> str:
    if isinstance(action, str):
        return action.strip()
    if isinstance(action, dict):
        return " ".join(
            f"{key}={value}" for key, value in action.items()
            if value not in (None, "")
        ).strip()
    return str(action).strip()


def _format_action_history(action_history: Any) -> str:
    if isinstance(action_history, list):
        lines = [
            f"{idx}. {text}"
            for idx, action in enumerate(action_history, start=1)
            if (text := _stringify_action(action))
        ]
        return "\n".join(lines) if lines else "No actions recorded."
    if isinstance(action_history, str) and action_history.strip():
        return action_history.strip()
    return "No actions recorded."


def _iter_rubrics(rubrics: dict[str, Any] | list[Any]) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(rubrics, dict):
        return [
            (str(rubric_id), value if isinstance(value, dict) else {"requirement": str(value)})
            for rubric_id, value in rubrics.items()
        ]
    if isinstance(rubrics, list):
        items: list[tuple[str, dict[str, Any]]] = []
        for idx, item in enumerate(rubrics, start=1):
            if isinstance(item, dict):
                items.append((str(item.get("id", f"R{idx}")), item))
                continue
            items.append((f"R{idx}", {"requirement": str(item)}))
        return items
    return []


def _rubric_prompt(
    task: str,
    rubric_id: str,
    rubric: dict[str, Any],
    action_history: str,
    screenshot_count: int,
    total_steps: int,
) -> str:
    rubric_lines = [
        f"Rubric ID: {rubric_id}",
        f"Requirement: {str(rubric.get('requirement', '')).strip()}",
    ]
    verification = str(rubric.get("verification", "")).strip()
    if verification:
        rubric_lines.append(f"Verification: {verification}")

    return (
        f"User Task (context only): {task}\n\n"
        "Evaluate ONLY this rubric item:\n"
        + "\n".join(rubric_lines)
        + f"\n\nFull Action History:\n{action_history}\n\n"
        f"Screenshots attached below: {screenshot_count} "
        f"(trajectory had {total_steps} total step(s)).\n\n"
        f"Decide whether the rubric ({rubric_id}) is satisfied. "
        "Use the required 'Thoughts:' / 'Status:' format."
    )


def _parse_status(response: str) -> tuple[bool, str]:
    status_match = _STATUS_RE.search(response)
    thoughts_match = _THOUGHTS_RE.search(response)
    reasoning = thoughts_match.group(1).strip() if thoughts_match else response.strip()
    return bool(status_match and status_match.group(1).lower() == "success"), reasoning


def _image_items(screenshot_paths: list[Path], image_scale_factor: float) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in screenshot_paths:
        try:
            img = Image.open(path)
            b64 = encode_image(img, scale_factor=image_scale_factor)
        except OSError as exc:
            logger.warning("Failed to load screenshot %s: %s", path, exc)
            continue
        items.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        })
    return items


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        data = usage
    elif hasattr(usage, "model_dump"):
        data = usage.model_dump()
    elif hasattr(usage, "__dict__"):
        data = usage.__dict__
    else:
        return None

    prompt_tokens = int(data.get("prompt_tokens") or 0)
    completion_tokens = int(data.get("completion_tokens") or 0)
    total_tokens = int(data.get("total_tokens") or prompt_tokens + completion_tokens)
    prompt_details = data.get("prompt_tokens_details") or {}
    if hasattr(prompt_details, "model_dump"):
        prompt_details = prompt_details.model_dump()
    elif hasattr(prompt_details, "__dict__"):
        prompt_details = prompt_details.__dict__
    cached_tokens = 0
    if isinstance(prompt_details, dict):
        cached_tokens = int(prompt_details.get("cached_tokens") or 0)
    cached_tokens = int(data.get("cached_tokens") or cached_tokens)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "non_cached_prompt": max(0, prompt_tokens - cached_tokens),
        "prompt_tokens_details": {"cached_tokens": cached_tokens},
    }


def _aggregate_usages(usages: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not usages:
        return None
    prompt_tokens = sum(int(usage.get("prompt_tokens") or 0) for usage in usages)
    completion_tokens = sum(int(usage.get("completion_tokens") or 0) for usage in usages)
    cached_tokens = sum(int(usage.get("cached_tokens") or 0) for usage in usages)
    total_tokens = sum(
        int(usage.get("total_tokens") or 0)
        or int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        for usage in usages
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "non_cached_prompt": max(0, prompt_tokens - cached_tokens),
        "prompt_tokens_details": {"cached_tokens": cached_tokens},
    }


def grade_rubrics(
    task: str,
    answer: str,
    rubrics: dict[str, Any] | list[Any],
    screenshot_paths: list[Path],
    model: Any,
    action_history: Any = None,
    image_scale_factor: float = 1.0,
    temperature: float = 0.0,
    max_tokens: int = FINAL_JUDGMENT_MAX_COMPLETION_TOKENS,
) -> dict[str, Any]:
    """Grade Odysseys rubric checkpoints using the official per-rubric protocol."""
    rubric_items = _iter_rubrics(rubrics)
    action_history_text = _format_action_history(action_history)
    images = _image_items(screenshot_paths, image_scale_factor)
    total_steps = len(action_history) if isinstance(action_history, list) else len(screenshot_paths)

    normalized: dict[str, dict[str, Any]] = {}
    raw_responses: list[str] = []
    official_results: list[dict[str, Any]] = []
    usages: list[dict[str, Any]] = []
    for rubric_id, rubric in rubric_items:
        user_text = _rubric_prompt(
            task=task,
            rubric_id=rubric_id,
            rubric=rubric,
            action_history=action_history_text,
            screenshot_count=len(images),
            total_steps=total_steps,
        )
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": [{"type": "text", "text": user_text}] + images},
        ]
        response = model.generate(messages, max_tokens=max_tokens, temperature=temperature)
        usage = _usage_to_dict(getattr(model, "last_usage", None))
        if usage is not None:
            usages.append(usage)
        raw_responses.append(f"### {rubric_id}\n{response}")
        success, reasoning = _parse_status(response)
        normalized[rubric_id] = {
            "passed": success,
            "reasoning": reasoning,
        }
        official_results.append({
            "rubric_id": rubric_id,
            "requirement": str(rubric.get("requirement", "")),
            "verification": str(rubric.get("verification", "")),
            "score": 1 if success else 0,
            "success": success,
            "reasoning": reasoning,
            "response": response,
        })

    passed = sum(1 for result in normalized.values() if result["passed"])
    total = len(normalized)
    score = passed / total if total else 0.0

    return {
        "rubric_results": normalized,
        "rubric_score": score,
        "passed_rubrics": passed,
        "total_rubrics": total,
        "is_correct": total > 0 and passed == total,
        "response": "\n\n".join(raw_responses),
        "reasoning": "\n".join(
            f"{rubric_id}: {result['reasoning']}"
            for rubric_id, result in normalized.items()
        ),
        "usage": _aggregate_usages(usages),
        "system_prompt": _SYSTEM_PROMPT,
        "action_history": action_history_text,
        "official_rubric_results": official_results,
    }
