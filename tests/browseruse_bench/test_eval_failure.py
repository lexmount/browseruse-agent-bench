from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.eval.failure import classify_failure_case


class _CapturingModel:
    """Fake EvaluationModel that records the messages it receives."""

    model = "fake-eval-model"

    def __init__(self) -> None:
        self.captured_messages: list[Any] | None = None

    def generate(self, messages: list[Any], **_kwargs: Any) -> str:
        self.captured_messages = messages
        return json.dumps({"reasoning": "stub", "category": "C1"})


def _user_prompt_text(messages: list[Any]) -> str:
    content = messages[1]["content"]
    return "\n".join(part["text"] for part in content if part.get("type") == "text")


def _write_agent_result(trajectories_dir: Path, task_id: str, payload: dict[str, Any]) -> None:
    task_dir = trajectories_dir / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")


def test_classifier_reads_lexbench_record_fields(tmp_path: Path) -> None:
    """LexBench eval records keep the judge text in evaluation_details.response and
    agent answer/action history in the task's result.json - the classifier prompt
    must contain all three instead of placeholder text."""
    _write_agent_result(
        tmp_path,
        "294",
        {
            "answer": "Starting URL returned 403 Forbidden",
            "action_history": ["Navigated to https://www.thepaper.cn", "Read page source"],
            "error": "Navigation failed: timeout after 30s",
        },
    )
    record = {
        "task_id": "294",
        "task": "Find the latest headline on thepaper.cn",
        "predicted_label": 0,
        "agent_response": None,
        "evaluation_details": {"response": "Judge saw a 403 block page in the trajectory"},
    }
    model = _CapturingModel()

    classify_failure_case(record, tmp_path, model)

    prompt = _user_prompt_text(model.captured_messages)
    assert "Navigated to https://www.thepaper.cn" in prompt
    assert "Starting URL returned 403 Forbidden" in prompt
    assert "Judge saw a 403 block page in the trajectory" in prompt
    assert "Navigation failed: timeout after 30s" in prompt
    assert "No action history" not in prompt
    assert "No response" not in prompt
    assert "No evaluation feedback" not in prompt
    assert record["failure_category"] == "C1"


def test_classifier_prefers_record_fields_over_result_json(tmp_path: Path) -> None:
    """BrowseComp-style records that already carry the fields keep working."""
    _write_agent_result(
        tmp_path,
        "7",
        {"answer": "fallback answer", "action_history": ["fallback action"]},
    )
    record = {
        "task_id": "7",
        "task": "some task",
        "predicted_label": 0,
        "agent_response": "inline agent answer",
        "action_history": ["inline action"],
        "evaluation_details": {"grader_response": "inline grader feedback"},
    }
    model = _CapturingModel()

    classify_failure_case(record, tmp_path, model)

    prompt = _user_prompt_text(model.captured_messages)
    assert "inline agent answer" in prompt
    assert "inline action" in prompt
    assert "inline grader feedback" in prompt
    assert "fallback answer" not in prompt
    assert "fallback action" not in prompt


class _A3Model(_CapturingModel):
    def generate(self, messages: list[Any], **_kwargs: Any) -> str:
        self.captured_messages = messages
        return json.dumps({"reasoning": "LLM API timed out repeatedly", "category": "A3"})


class _FailingModel(_CapturingModel):
    def generate(self, messages: list[Any], **_kwargs: Any) -> str:
        raise RuntimeError("content filter rejected the classification call")


def test_classifier_accepts_new_a3_a4_categories(tmp_path: Path) -> None:
    record = {
        "task_id": "1",
        "task": "some task",
        "predicted_label": 0,
        "evaluation_details": {},
    }

    classify_failure_case(record, tmp_path, _A3Model())

    assert record["failure_category"] == "A3"


def test_classifier_call_failure_is_unclassified_not_a1(tmp_path: Path) -> None:
    """A failure of the classification call itself must not pollute the A* buckets."""
    record = {
        "task_id": "2",
        "task": "some task",
        "predicted_label": 0,
        "evaluation_details": {},
    }

    classify_failure_case(record, tmp_path, _FailingModel())

    assert record["failure_category"] == "U"
    fc = record["evaluation_details"]["failure_classification"]
    assert fc["reasoning"].startswith("Classification error:")


class _TruncatedModel(_CapturingModel):
    """Simulates a max_tokens-truncated JSON response."""

    def generate(self, messages: list[Any], **_kwargs: Any) -> str:
        self.captured_messages = messages
        return '{"reasoning":"页面被风控拦截，导航全部超时……","category":"B1'


def test_classifier_recovers_category_from_truncated_json(tmp_path: Path) -> None:
    """A response cut off at max_tokens must still yield the category instead of U."""
    record = {
        "task_id": "190",
        "task": "some task",
        "predicted_label": 0,
        "evaluation_details": {},
    }
    model = _TruncatedModel()

    classify_failure_case(record, tmp_path, model)

    assert record["failure_category"] == "B1"


def test_classifier_handles_missing_result_json(tmp_path: Path) -> None:
    """No result.json on disk must not crash; placeholders are used instead."""
    record = {
        "task_id": "999",
        "task": "some task",
        "predicted_label": 0,
        "evaluation_details": {},
    }
    model = _CapturingModel()

    classify_failure_case(record, tmp_path, model)

    prompt = _user_prompt_text(model.captured_messages)
    assert "No action history" in prompt
    assert "No response" in prompt
    assert "No evaluation feedback" in prompt
