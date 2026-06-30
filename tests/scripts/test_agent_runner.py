"""Tests for cost enrichment integration in browseruse_bench.runner.agent_runner."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from browseruse_bench.utils import llm_cost
from browseruse_bench.utils.run_identity import MACHINE_IDENTITY_ENV_KEY
from browseruse_bench.runner import agent_runner

TEST_PRICE_TABLE = {
    "gpt-4.1": {
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 8e-06,
        "cache_read_input_token_cost": 5e-07,
    }
}


class _FakeAgent:
    def prepare(self, agent_config: dict) -> None:
        del agent_config

    def run_task(self, task_info: dict, agent_config: dict, workspace: Path) -> dict:
        del task_info, agent_config, workspace
        return {
            "model_id": "gpt-4.1",
            "metrics": {
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "total_tokens": 1100,
                }
            },
        }


class _InterruptingAgent:
    def prepare(self, agent_config: dict) -> None:
        del agent_config

    def run_task(self, task_info: dict, agent_config: dict, workspace: Path) -> dict:
        del task_info, agent_config, workspace
        raise KeyboardInterrupt("Process interrupted")


def test_agent_runner_enriches_usage_cost_before_result_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_info_path = tmp_path / "task_info.json"
    task_info_path.write_text(json.dumps({"task_id": "task_001"}), encoding="utf-8")
    workspace = tmp_path / "workspace"

    args = Namespace(
        agent="browser-use",
        task_info=task_info_path,
        agent_config=None,
        workspace=workspace,
        timeout=300,
    )

    monkeypatch.setattr(agent_runner, "parse_args", lambda: args)
    monkeypatch.setattr(agent_runner, "get_agent", lambda _agent_name: _FakeAgent())
    monkeypatch.setattr(agent_runner, "load_agent_config_from_path", lambda _path: {})
    monkeypatch.setattr(agent_runner, "resolve_timeout_value", lambda _timeout, _config: 300)
    monkeypatch.setattr(agent_runner.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_cost, "load_litellm_price_table", lambda: TEST_PRICE_TABLE)
    monkeypatch.setenv(
        MACHINE_IDENTITY_ENV_KEY,
        json.dumps({"machine_id": "worker-a", "hostname": "host-a"}),
    )

    exit_code = agent_runner.main()
    assert exit_code == 0

    result_path = workspace / "result.json"
    saved_result = json.loads(result_path.read_text(encoding="utf-8"))
    usage = saved_result["metrics"]["usage"]
    assert usage["total_prompt_tokens"] == 1000
    assert usage["total_completion_tokens"] == 100
    assert usage["total_tokens"] == 1100
    assert usage["total_cost"] == pytest.approx(0.0028)
    assert saved_result["run_metadata"]["machine"]["machine_id"] == "worker-a"
    assert saved_result["run_metadata"]["machine"]["hostname"] == "host-a"


def test_agent_runner_marks_interrupts_as_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_info_path = tmp_path / "task_info.json"
    task_info_path.write_text(json.dumps({"task_id": "task_002"}), encoding="utf-8")
    workspace = tmp_path / "workspace"

    args = Namespace(
        agent="browser-use",
        task_info=task_info_path,
        agent_config=None,
        workspace=workspace,
        timeout=300,
    )

    monkeypatch.setattr(agent_runner, "parse_args", lambda: args)
    monkeypatch.setattr(agent_runner, "get_agent", lambda _agent_name: _InterruptingAgent())
    monkeypatch.setattr(agent_runner, "load_agent_config_from_path", lambda _path: {})
    monkeypatch.setattr(agent_runner, "resolve_timeout_value", lambda _timeout, _config: 300)
    monkeypatch.setattr(agent_runner.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setenv(
        MACHINE_IDENTITY_ENV_KEY,
        json.dumps({"machine_id": "worker-b", "hostname": "host-b"}),
    )

    exit_code = agent_runner.main()
    assert exit_code == 130

    result_path = workspace / "result.json"
    saved_result = json.loads(result_path.read_text(encoding="utf-8"))
    assert saved_result["env_status"] == "interrupted"
    assert saved_result["agent_done"] == "interrupted"
    assert saved_result["run_metadata"]["machine"]["machine_id"] == "worker-b"
