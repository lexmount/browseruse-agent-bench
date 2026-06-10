from __future__ import annotations

import json
from pathlib import Path

import pytest

from browseruse_bench.browsers import orphan_cleanup as orphan_cleanup_module


def test_remove_state_file_returns_false_on_oserror(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state_dir"
    state_dir.mkdir()

    assert orphan_cleanup_module._remove_state_file(state_dir) is False
    assert state_dir.exists()


def test_cleanup_returns_failure_when_remove_state_file_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"backend_id": "unknown", "session_id": "session-1"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(orphan_cleanup_module, "_remove_state_file", lambda path: False)

    result = orphan_cleanup_module.cleanup_orphaned_session_state(state_file)

    assert result == 1


def test_cleanup_returns_success_when_state_file_removed(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"backend_id": "unknown", "session_id": "session-1"}),
        encoding="utf-8",
    )

    result = orphan_cleanup_module.cleanup_orphaned_session_state(state_file)

    assert result == 0
    assert not state_file.exists()


def test_cleanup_browserbase_session_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"backend_id": "browserbase", "session_id": "bb-session-1"}),
        encoding="utf-8",
    )

    def fake_post_json(**kwargs):
        calls.append(kwargs)
        return {"id": "bb-session-1"}

    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb-key")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "project-1")
    monkeypatch.setattr(
        "browseruse_bench.browsers.providers.cloud_utils.post_json",
        fake_post_json,
    )

    result = orphan_cleanup_module.cleanup_orphaned_session_state(state_file)

    assert result == 0
    assert not state_file.exists()
    assert calls == [
        {
            "url": "https://api.browserbase.com/v1/sessions/bb-session-1",
            "headers": {"X-BB-API-Key": "bb-key"},
            "body": {"status": "REQUEST_RELEASE", "projectId": "project-1"},
            "timeout_seconds": 30,
        }
    ]


def test_cleanup_browserbase_session_uses_state_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "backend_id": "browserbase",
                "session_id": "bb-session-1",
                "cleanup_metadata": json.dumps(
                    {
                        "api_key": "metadata-key",
                        "base_url": "https://browserbase.example",
                        "project_id": "metadata-project",
                        "request_timeout": "12",
                    }
                ),
            }
        ),
        encoding="utf-8",
    )

    def fake_post_json(**kwargs):
        calls.append(kwargs)
        return {"id": "bb-session-1"}

    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    monkeypatch.setattr(
        "browseruse_bench.browsers.providers.cloud_utils.post_json",
        fake_post_json,
    )

    result = orphan_cleanup_module.cleanup_orphaned_session_state(state_file)

    assert result == 0
    assert not state_file.exists()
    assert calls == [
        {
            "url": "https://browserbase.example/v1/sessions/bb-session-1",
            "headers": {"X-BB-API-Key": "metadata-key"},
            "body": {"status": "REQUEST_RELEASE", "projectId": "metadata-project"},
            "timeout_seconds": 12,
        }
    ]


def test_cleanup_steel_session_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"backend_id": "steel", "session_id": "steel-session-1"}),
        encoding="utf-8",
    )

    def fake_post_json(**kwargs):
        calls.append(kwargs)
        return {"id": "steel-session-1"}

    monkeypatch.setenv("STEEL_API_KEY", "steel-key")
    monkeypatch.setattr(
        "browseruse_bench.browsers.providers.cloud_utils.post_json",
        fake_post_json,
    )

    result = orphan_cleanup_module.cleanup_orphaned_session_state(state_file)

    assert result == 0
    assert not state_file.exists()
    assert calls == [
        {
            "url": "https://api.steel.dev/v1/sessions/steel-session-1/release",
            "headers": {"steel-api-key": "steel-key"},
            "body": None,
            "timeout_seconds": 30,
        }
    ]
