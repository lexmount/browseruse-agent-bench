"""Tests for APICallLogger step logging of failed LLM calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from browseruse_bench.utils.api_logger import APICallLogger


def _failure_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "timestamp": 1000.5,
        "error": "Invalid JSON: trailing characters at line 2 column 1",
        "status_code": 502,
        "raw_response": '{"action": []}\n{"action": []}',
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    record.update(overrides)
    return record


def test_log_step_writes_llm_failures(tmp_path: Path) -> None:
    api_logger = APICallLogger(tmp_path, "task-1", "gpt-test")
    failure = _failure_record()

    api_logger.log_step(
        step_number=1,
        model_output=None,
        action_results=None,
        state=None,
        state_message=None,
        llm_failures=[failure],
    )

    step_data = json.loads((tmp_path / "step_001.json").read_text())
    assert step_data["output"] == {}
    assert step_data["llm_failures"] == [failure]


def test_log_step_defaults_to_empty_llm_failures(tmp_path: Path) -> None:
    api_logger = APICallLogger(tmp_path, "task-1", "gpt-test")

    api_logger.log_step(
        step_number=1,
        model_output=None,
        action_results=None,
        state=None,
        state_message=None,
    )

    step_data = json.loads((tmp_path / "step_001.json").read_text())
    assert step_data["llm_failures"] == []


def test_finalize_summary_includes_llm_failures(tmp_path: Path) -> None:
    api_logger = APICallLogger(tmp_path, "task-1", "gpt-test")
    failure = _failure_record()

    api_logger.log_step(
        step_number=1,
        model_output=None,
        action_results=None,
        state=None,
        state_message=None,
        llm_failures=[failure],
    )
    api_logger.finalize()

    summary = (tmp_path / "summary.md").read_text()
    assert "LLM Failures" in summary
    assert failure["error"] in summary
    assert failure["raw_response"] in summary


def test_log_unmatched_llm_failures_writes_file(tmp_path: Path) -> None:
    api_logger = APICallLogger(tmp_path, "task-1", "gpt-test")
    failure = _failure_record()

    api_logger.log_unmatched_llm_failures([failure])

    data = json.loads((tmp_path / "llm_failures_unmatched.json").read_text())
    assert data == [failure]


def test_log_unmatched_llm_failures_skips_empty(tmp_path: Path) -> None:
    api_logger = APICallLogger(tmp_path, "task-1", "gpt-test")

    api_logger.log_unmatched_llm_failures([])

    assert not (tmp_path / "llm_failures_unmatched.json").exists()
