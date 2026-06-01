"""Odysseys rubric grader."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from browseruse_bench.utils import encode_image

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are evaluating a long-horizon browser-agent task using rubric checkpoints. "
    "Use the task instruction, the agent's final response, and screenshots from the "
    "trajectory as evidence. Grade each rubric independently. Do not browse the web. "
    "Return only valid JSON with this shape: "
    '{"rubric_results":{"R1":{"passed":true,"reasoning":"..."}},"reasoning":"overall notes"}.'
)

_USER_TEMPLATE = (
    "Task instruction:\n{task}\n\n"
    "Agent final response:\n{answer}\n\n"
    "Rubrics:\n{rubrics}\n\n"
    "{num} trajectory screenshots are attached in chronological order."
)


def _safe_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(stripped[start:end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def grade_rubrics(
    task: str,
    answer: str,
    rubrics: dict[str, Any],
    screenshot_paths: list[Path],
    model: Any,
    image_scale_factor: float = 1.0,
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> dict[str, Any]:
    """Grade Odysseys rubric checkpoints from screenshots and final answer."""
    rubrics_text = json.dumps(rubrics, ensure_ascii=False, indent=2)
    user_text = _USER_TEMPLATE.format(
        task=task,
        answer=answer or "No answer provided.",
        rubrics=rubrics_text,
        num=len(screenshot_paths),
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": [{"type": "text", "text": user_text}]},
    ]

    for path in screenshot_paths:
        try:
            img = Image.open(path)
            b64 = encode_image(img, scale_factor=image_scale_factor)
            messages[1]["content"].append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
            })
        except OSError as exc:
            logger.warning("Failed to load screenshot %s: %s", path, exc)

    messages[1]["content"].append({"type": "text", "text": "JSON verdict:"})
    response = model.generate(messages, max_tokens=max_tokens, temperature=temperature)
    parsed = _safe_json_object(response)
    raw_results = parsed.get("rubric_results")
    rubric_results = raw_results if isinstance(raw_results, dict) else {}

    normalized: dict[str, dict[str, Any]] = {}
    for rubric_id in rubrics:
        result = rubric_results.get(rubric_id, {})
        if not isinstance(result, dict):
            result = {}
        normalized[rubric_id] = {
            "passed": bool(result.get("passed")),
            "reasoning": str(result.get("reasoning") or ""),
        }

    passed = sum(1 for result in normalized.values() if result["passed"])
    total = len(normalized)
    score = passed / total if total else 0.0

    return {
        "rubric_results": normalized,
        "rubric_score": score,
        "passed_rubrics": passed,
        "total_rubrics": total,
        "is_correct": total > 0 and passed == total,
        "response": response,
        "reasoning": str(parsed.get("reasoning") or response.strip()),
        "usage": getattr(model, "last_usage", None),
        "system_prompt": _SYSTEM_PROMPT,
        "user_prompt": user_text,
    }
