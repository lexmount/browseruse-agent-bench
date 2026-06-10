from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.browsers.providers import browserbase as browserbase_module
from browseruse_bench.browsers.providers import browserless as browserless_module
from browseruse_bench.browsers.providers import steel as steel_module
from browseruse_bench.browsers.providers.browserbase import BrowserbaseBackend
from browseruse_bench.browsers.providers.browserless import BrowserlessBackend
from browseruse_bench.browsers.providers.steel import SteelBackend
from browseruse_bench.browsers.registry import get_backend


def test_browserbase_backend_open_and_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []
    state_file = tmp_path / "session-state.json"

    def fake_post_json(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if kwargs["url"].endswith("/v1/sessions"):
            return {"id": "bb-session-1", "connectUrl": "wss://browserbase.example/cdp"}
        return {"id": "bb-session-1", "status": "COMPLETED"}

    monkeypatch.setattr(browserbase_module.cloud_utils, "post_json", fake_post_json)
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.setenv("BROWSERUSE_BENCH_SESSION_STATE_FILE", str(state_file))
    backend = BrowserbaseBackend("browserbase")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={
            "browserbase_api_key": "bb-key",
            "browserbase_project_id": "project-1",
            "browserbase_region": "us-east-1",
            "browserbase_timeout": "120",
            "browserbase_keep_alive": "true",
        },
    )

    assert session_context.transport == "cdp"
    assert session_context.cdp_url == "wss://browserbase.example/cdp"
    assert calls[0]["headers"] == {"X-BB-API-Key": "bb-key"}
    assert calls[0]["body"] == {
        "projectId": "project-1",
        "browserSettings": {
            "timeout": 120,
            "keepAlive": True,
            "region": "us-east-1",
        },
    }
    state = json.loads(state_file.read_text(encoding="utf-8"))
    cleanup_metadata = json.loads(state["cleanup_metadata"])
    assert cleanup_metadata == {
        "api_key": "bb-key",
        "base_url": "https://api.browserbase.com",
        "project_id": "project-1",
        "request_timeout": "30",
    }

    backend.close(session_context)
    assert calls[1]["url"] == "https://api.browserbase.com/v1/sessions/bb-session-1"
    assert calls[1]["body"] == {"status": "REQUEST_RELEASE", "projectId": "project-1"}
    assert not state_file.exists()


def test_browserbase_backend_keeps_state_when_release_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "session-state.json"

    def fake_post_json(**kwargs: Any) -> dict[str, Any]:
        if kwargs["url"].endswith("/v1/sessions"):
            return {"id": "bb-session-1", "connectUrl": "wss://browserbase.example/cdp"}
        raise RuntimeError("release failed")

    monkeypatch.setattr(browserbase_module.cloud_utils, "post_json", fake_post_json)
    monkeypatch.setenv("BROWSERUSE_BENCH_SESSION_STATE_FILE", str(state_file))
    backend = BrowserbaseBackend("browserbase")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={"browserbase_api_key": "bb-key"},
    )
    backend.close(session_context)

    assert state_file.exists()


def test_browserbase_backend_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    backend = BrowserbaseBackend("browserbase")

    with pytest.raises(ValueError, match="Browserbase requires an API key"):
        backend.open(agent_name="browser-use", agent_config={})


def test_browserbase_backend_empty_connect_url_releases_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post_json(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if kwargs["url"].endswith("/v1/sessions"):
            return {"id": "bb-session-1", "connectUrl": ""}
        return {"id": "bb-session-1", "status": "COMPLETED"}

    monkeypatch.setattr(browserbase_module.cloud_utils, "post_json", fake_post_json)
    backend = BrowserbaseBackend("browserbase")

    with pytest.raises(RuntimeError, match="connectUrl is empty"):
        backend.open(agent_name="browser-use", agent_config={"browserbase_api_key": "bb-key"})
    assert calls[1]["url"] == "https://api.browserbase.com/v1/sessions/bb-session-1"


def test_browserless_backend_resolves_debugger_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERLESS_TOKEN", "browserless-token")
    calls: list[dict[str, Any]] = []

    def fake_get_json(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"webSocketDebuggerUrl": "wss://production-ams.browserless.io/e/session"}

    monkeypatch.setattr(browserless_module.cloud_utils, "get_json", fake_get_json)
    backend = BrowserlessBackend("browserless")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={
            "browserless_ws_url": "production-ams.browserless.io",
            "browserless_browser_path": "chromium",
            "browserless_timeout": "600000",
            "browserless_resolve_debugger_url": True,
        },
    )

    assert session_context.transport == "cdp"
    assert session_context.cdp_url == "wss://production-ams.browserless.io/e/session"
    assert calls[0]["url"] == (
        "https://production-ams.browserless.io/json/version"
        "?token=browserless-token&timeout=600000"
    )


def test_browserless_backend_builds_cdp_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERLESS_TOKEN", "browserless-token")
    backend = BrowserlessBackend("browserless")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={
            "browserless_ws_url": "production-ams.browserless.io",
            "browserless_timeout": "600000",
        },
    )

    assert session_context.transport == "cdp"
    assert session_context.cdp_url == (
        "wss://production-ams.browserless.io"
        "?token=browserless-token&timeout=600000"
    )


def test_browserless_backend_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BROWSERLESS_TOKEN", raising=False)
    monkeypatch.delenv("BROWSERLESS_API_KEY", raising=False)
    backend = BrowserlessBackend("browserless")

    with pytest.raises(ValueError, match="Browserless requires an API token"):
        backend.open(agent_name="browser-use", agent_config={})


def test_steel_backend_open_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post_json(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        if kwargs["url"].endswith("/v1/sessions"):
            return {"id": "steel-session-1"}
        return {"id": "steel-session-1", "released": True}

    monkeypatch.setattr(steel_module.cloud_utils, "post_json", fake_post_json)
    monkeypatch.delenv("STEEL_API_KEY", raising=False)
    backend = SteelBackend("steel")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={
            "steel_api_key": "steel-key",
            "steel_region": "iad",
            "steel_timeout": "600000",
            "steel_use_proxy": "true",
            "steel_solve_captcha": False,
        },
    )

    assert session_context.transport == "cdp"
    assert session_context.cdp_url == (
        "wss://connect.steel.dev?apiKey=steel-key&sessionId=steel-session-1"
    )
    assert calls[0]["headers"] == {"steel-api-key": "steel-key"}
    assert calls[0]["body"] == {
        "region": "iad",
        "timeout": 600000,
        "useProxy": True,
        "solveCaptcha": False,
    }

    backend.close(session_context)
    assert calls[1]["url"] == "https://api.steel.dev/v1/sessions/steel-session-1/release"
    assert calls[1]["body"] is None


def test_steel_backend_uses_websocket_url_from_response(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post_json(**kwargs: Any) -> dict[str, Any]:
        return {"id": "steel-session-1", "websocketUrl": "wss://steel.example/cdp"}

    monkeypatch.setattr(steel_module.cloud_utils, "post_json", fake_post_json)
    backend = SteelBackend("steel")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={"steel_api_key": "steel-key"},
    )

    assert session_context.cdp_url == "wss://steel.example/cdp?apiKey=steel-key"


def test_steel_backend_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STEEL_API_KEY", raising=False)
    backend = SteelBackend("steel")

    with pytest.raises(ValueError, match="Steel requires an API key"):
        backend.open(agent_name="browser-use", agent_config={})


def test_cloud_backends_are_registered() -> None:
    assert get_backend("browserbase").backend_id == "browserbase"
    assert get_backend("browserless").backend_id == "browserless"
    assert get_backend("steel").backend_id == "steel"
