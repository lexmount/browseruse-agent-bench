"""Tests for run manifest snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from browseruse_bench.cli.run import _write_run_manifest


def test_write_run_manifest_redacts_secrets(tmp_path: Path) -> None:
    _write_run_manifest(
        tmp_path,
        agent_config={
            "models": {
                "gpt": {
                    "api_key": "sk-test",
                    "base_url": "https://gateway.example/v1",
                }
            },
            "lexmount_api_key": "lexmount-secret",
            "headers": [{"Authorization": "Bearer token-secret"}],
            "empty_api_key": "",
            "max_tokens": 1000,
            "max_output_tokens": 4000,
            "hf_token": "hf-secret",
        },
    )

    snapshot = json.loads((tmp_path / "config_snapshot.json").read_text(encoding="utf-8"))

    assert snapshot["models"]["gpt"]["api_key"] == "<redacted>"
    assert snapshot["models"]["gpt"]["base_url"] == "https://gateway.example/v1"
    assert snapshot["lexmount_api_key"] == "<redacted>"
    assert snapshot["headers"][0]["Authorization"] == "<redacted>"
    assert snapshot["empty_api_key"] == ""
    assert snapshot["max_tokens"] == 1000
    assert snapshot["max_output_tokens"] == 4000
    assert snapshot["hf_token"] == "<redacted>"
