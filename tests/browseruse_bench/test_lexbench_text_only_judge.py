"""Tests for the text-only judge mode (api_max_images=0) in LexBench eval."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PIL")

from PIL import Image

from browseruse_bench.eval import failure
from browseruse_bench.eval.lexbench_browser.lexmount_eval import evaluate_task


class FakeJudgeModel:
    model = "GLM-5.2"

    def __init__(self):
        self.last_usage = None
        self.calls: list[list[dict]] = []

    def generate(self, messages, max_tokens=None, temperature=None):
        self.calls.append(messages)
        return "score: 8/10"


@pytest.fixture
def screenshots(tmp_path: Path) -> list[Path]:
    paths = []
    for i in range(3):
        path = tmp_path / f"step_{i}.png"
        Image.new("RGB", (32, 32), color=(i * 40, 10, 10)).save(path)
        paths.append(path)
    return paths


@pytest.fixture
def task_data() -> dict:
    return {
        "query": "Find the pricing page",
        "target_website": "example.com",
        "reference_answer": {
            "steps": ["Open site", "Click pricing"],
            "key_points": ["Pricing page reached"],
            "scoring": {"items": [{"name": "done", "score": 10, "description": "ok"}]},
        },
    }


def _image_parts(messages: list[dict]) -> list[dict]:
    return [
        part
        for message in messages
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]


def test_api_max_images_zero_sends_no_images(task_data, screenshots):
    model = FakeJudgeModel()
    result = evaluate_task(
        task_data=task_data,
        agent_result={"answer": "Reached pricing page"},
        screenshot_paths=screenshots,
        model=model,
        api_max_images=0,
    )

    assert len(model.calls) == 1
    assert _image_parts(model.calls[0]) == []
    assert result["screenshot_count"] == 0
    assert result["original_screenshot_count"] == 3
    assert "text-only" in result["prompt"]


def test_api_max_images_positive_still_sends_images(task_data, screenshots):
    model = FakeJudgeModel()
    result = evaluate_task(
        task_data=task_data,
        agent_result={"answer": "Reached pricing page"},
        screenshot_paths=screenshots,
        model=model,
        api_max_images=10,
    )

    assert len(_image_parts(model.calls[0])) == 3
    assert result["screenshot_count"] == 3
    assert "text-only" not in result["prompt"]


def test_failure_classification_text_only(monkeypatch, screenshots):
    model = FakeJudgeModel()
    model.generate = lambda messages, **kwargs: (
        model.calls.append(messages) or '{"category": "M2", "reasoning": "misread page"}'
    )

    monkeypatch.setattr(failure, "_FAILURE_TEXT_ONLY", True)
    failure.classify_single_failure(
        task_description="Find the pricing page",
        screenshots=[str(p) for p in screenshots],
        action_history=["navigate", "click"],
        agent_response="Reached pricing page",
        evaluator_response="score too low",
        model=model,
    )

    assert len(model.calls) == 1
    assert _image_parts(model.calls[0]) == []
    user_text = model.calls[0][1]["content"][0]["text"]
    assert "text-only" in user_text

    monkeypatch.setattr(failure, "_FAILURE_TEXT_ONLY", False)
    model.calls.clear()
    failure.classify_single_failure(
        task_description="Find the pricing page",
        screenshots=[str(p) for p in screenshots],
        action_history=["navigate", "click"],
        agent_response="Reached pricing page",
        evaluator_response="score too low",
        model=model,
    )
    assert len(_image_parts(model.calls[0])) == 3
