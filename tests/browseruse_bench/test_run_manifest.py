"""Tests for run manifest snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from browseruse_bench.cli.run import _write_run_manifest


def test_write_run_manifest_redacts_secrets(tmp_path: Path) -> None:
    _write_run_manifest(
        tmp_path,
        resolved_agent_config={
            "use_vision": False,
            "max_steps": 3,
            "model_id": "gpt-5.4",
            "api_key": "resolved-secret",
            "base_url": "https://gateway.example/v1",
            "browserbase_api_key": "resolved-bb-secret",
            "browserbase_project_id": "project-1",
            "headers": [{"Authorization": "Bearer token-secret"}],
            "empty_api_key": "",
            "max_tokens": 1000,
            "max_output_tokens": 4000,
            "hf_token": "hf-secret",
        },
        run_context={
            "agent": "browser-use",
            "benchmark": "LexBench-Browser",
            "model_id": "gpt-5.4",
        },
        machine_identity={
            "machine_id": "worker-a",
            "hostname": "host-a",
            "machine_id_source": "test",
        },
    )

    snapshot = json.loads((tmp_path / "config_snapshot.json").read_text(encoding="utf-8"))

    assert sorted(snapshot.keys()) == ["machine", "run", "runtime_config"]
    assert "models" not in snapshot
    assert "browsers" not in snapshot
    assert "agents" not in snapshot
    assert snapshot["run"]["agent"] == "browser-use"
    assert snapshot["run"]["benchmark"] == "LexBench-Browser"
    assert snapshot["machine"]["machine_id"] == "worker-a"
    assert snapshot["machine"]["hostname"] == "host-a"
    assert snapshot["runtime_config"]["use_vision"] is False
    assert snapshot["runtime_config"]["max_steps"] == 3
    assert snapshot["runtime_config"]["model_id"] == "gpt-5.4"
    assert snapshot["runtime_config"]["api_key"] == "<redacted>"
    assert snapshot["runtime_config"]["base_url"] == "https://gateway.example/v1"
    assert snapshot["runtime_config"]["browserbase_api_key"] == "<redacted>"
    assert snapshot["runtime_config"]["browserbase_project_id"] == "project-1"
    assert snapshot["runtime_config"]["headers"][0]["Authorization"] == "<redacted>"
    assert snapshot["runtime_config"]["empty_api_key"] == ""
    assert snapshot["runtime_config"]["max_tokens"] == 1000
    assert snapshot["runtime_config"]["max_output_tokens"] == 4000
    assert snapshot["runtime_config"]["hf_token"] == "<redacted>"
