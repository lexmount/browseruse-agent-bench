"""Tests for browser backend abstraction and lifecycle management."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from browseruse_bench.browsers import manager as manager_module
from browseruse_bench.browsers.manager import open_browser_session
from browseruse_bench.browsers.providers import agentbay as agentbay_module
from browseruse_bench.browsers.providers import local as local_module
from browseruse_bench.browsers.providers.agentbay import AgentBayBackend
from browseruse_bench.browsers.providers.local import (
    LocalBackend,
    warn_if_local_proxy_unsupported,
)
from browseruse_bench.browsers.types import BrowserSessionContext


def _install_fake_agentbay_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    create_success: bool = True,
    initialize_success: bool = True,
    endpoint_url: str = "wss://agentbay.example/cdp",
    delete_exception: BaseException | None = None,
) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "api_key": None,
        "create_params": None,
        "initialize_options": [],
        "delete_calls": 0,
    }

    class FakeBrowserOption:
        def __init__(self, use_stealth: bool = False) -> None:
            self.use_stealth = use_stealth

    class FakeCreateSessionParams:
        def __init__(self, image_id: str | None = None, enable_browser_replay: bool = True) -> None:
            self.image_id = image_id
            self.enable_browser_replay = enable_browser_replay

    class FakeSessionBrowser:
        def initialize(self, option: Any) -> bool:
            state["initialize_options"].append(option)
            return initialize_success

        def get_endpoint_url(self) -> str:
            return endpoint_url

    class FakeSession:
        def __init__(self) -> None:
            self.browser = FakeSessionBrowser()

    class FakeCreateResult:
        def __init__(self) -> None:
            self.success = create_success
            self.error_message = "create failed"
            self.session = FakeSession() if create_success else None

    class FakeDeleteResult:
        def __init__(self) -> None:
            self.success = True
            self.error_message = ""

    class FakeAgentBay:
        def __init__(self, api_key: str) -> None:
            state["api_key"] = api_key

        def create(self, params: Any) -> Any:
            state["create_params"] = params
            return FakeCreateResult()

        def delete(self, session: Any) -> Any:
            state["delete_calls"] += 1
            if delete_exception is not None:
                raise delete_exception
            return FakeDeleteResult()

    monkeypatch.setattr(agentbay_module, "AgentBaySDK", FakeAgentBay)
    monkeypatch.setattr(agentbay_module, "BrowserOptionSDK", FakeBrowserOption)
    monkeypatch.setattr(agentbay_module, "CreateSessionParamsSDK", FakeCreateSessionParams)
    return state


def test_agentbay_backend_open_and_close_success(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_agentbay_sdk(monkeypatch)
    monkeypatch.setenv("AGENTBAY_API_KEY", "test-key")
    backend = AgentBayBackend("agentbay")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={
            "agentbay_image_id": "browser_latest",
            "agentbay_enable_browser_replay": "false",
            "agentbay_browser_use_stealth": "true",
        },
    )

    assert session_context.transport == "cdp"
    assert session_context.cdp_url == "wss://agentbay.example/cdp"
    assert state["api_key"] == "test-key"
    assert state["create_params"].enable_browser_replay is False
    assert state["initialize_options"][0].use_stealth is True

    backend.close(session_context)
    assert state["delete_calls"] == 1


def test_agentbay_backend_accepts_legacy_uppercase_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_agentbay_sdk(monkeypatch)
    monkeypatch.setenv("AGENTBAY_API_KEY", "test-key")
    backend = AgentBayBackend("agentbay")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={
            "AGENTBAY_IMAGE_ID": "browser_latest",
            "AGENTBAY_ENABLE_BROWSER_REPLAY": "false",
            "AGENTBAY_BROWSER_USE_STEALTH": "true",
        },
    )

    assert session_context.transport == "cdp"
    assert session_context.cdp_url == "wss://agentbay.example/cdp"
    assert state["create_params"].enable_browser_replay is False
    assert state["initialize_options"][0].use_stealth is True

    backend.close(session_context)
    assert state["delete_calls"] == 1


def test_agentbay_backend_initialize_failure_triggers_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_agentbay_sdk(monkeypatch, initialize_success=False)
    monkeypatch.setenv("AGENTBAY_API_KEY", "test-key")
    backend = AgentBayBackend("agentbay")

    with pytest.raises(RuntimeError, match="initialization failed"):
        backend.open(agent_name="browser-use", agent_config={})
    assert state["delete_calls"] == 1


def test_agentbay_backend_empty_endpoint_triggers_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_agentbay_sdk(monkeypatch, endpoint_url="")
    monkeypatch.setenv("AGENTBAY_API_KEY", "test-key")
    backend = AgentBayBackend("agentbay")

    with pytest.raises(RuntimeError, match="endpoint URL is empty"):
        backend.open(agent_name="browser-use", agent_config={})
    assert state["delete_calls"] == 1


def test_agentbay_backend_missing_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_agentbay_sdk(monkeypatch)
    monkeypatch.delenv("AGENTBAY_API_KEY", raising=False)
    backend = AgentBayBackend("agentbay")

    with pytest.raises(ValueError, match="requires an API key"):
        backend.open(agent_name="browser-use", agent_config={})


def test_agentbay_backend_accepts_api_key_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_agentbay_sdk(monkeypatch)
    monkeypatch.delenv("AGENTBAY_API_KEY", raising=False)
    backend = AgentBayBackend("agentbay")

    session_context = backend.open(
        agent_name="browser-use",
        agent_config={"agentbay_api_key": "config-key"},
    )

    assert session_context.transport == "cdp"
    assert session_context.cdp_url == "wss://agentbay.example/cdp"
    assert state["api_key"] == "config-key"

    backend.close(session_context)
    assert state["delete_calls"] == 1


def test_agentbay_backend_import_error_raises_module_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    class MissingAgentBay:
        def __init__(self, api_key: str) -> None:
            raise ModuleNotFoundError("No module named 'agentbay'")

    monkeypatch.setattr(agentbay_module, "AgentBaySDK", MissingAgentBay)
    monkeypatch.setenv("AGENTBAY_API_KEY", "test-key")
    backend = AgentBayBackend("agentbay")
    with pytest.raises(ModuleNotFoundError, match="agentbay"):
        backend.open(agent_name="browser-use", agent_config={})


def test_agentbay_backend_close_delete_failure_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_agentbay_sdk(monkeypatch, delete_exception=ConnectionError("delete failed"))
    monkeypatch.setenv("AGENTBAY_API_KEY", "test-key")
    backend = AgentBayBackend("agentbay")
    session_context = backend.open(agent_name="browser-use", agent_config={})

    backend.close(session_context)
    assert state["delete_calls"] == 1


def test_agentbay_backend_close_sdk_failure_is_tolerated(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _install_fake_agentbay_sdk(monkeypatch, delete_exception=RuntimeError("delete failed"))
    monkeypatch.setenv("AGENTBAY_API_KEY", "test-key")
    backend = AgentBayBackend("agentbay")
    session_context = backend.open(agent_name="browser-use", agent_config={})

    backend.close(session_context)
    assert state["delete_calls"] == 1


def test_open_browser_session_manager_always_calls_close(monkeypatch: pytest.MonkeyPatch) -> None:
    close_state = {"calls": 0}

    class FakeBackend:
        backend_id = "fake"

        def open(self, agent_name: str, agent_config: Dict[str, Any]) -> BrowserSessionContext:
            return BrowserSessionContext(backend_id="fake", transport="local")

        def close(self, session_context: BrowserSessionContext) -> None:
            close_state["calls"] += 1

    def fake_get_backend(browser_id: str) -> FakeBackend:
        return FakeBackend()

    monkeypatch.setattr(manager_module, "get_backend", fake_get_backend)
    with open_browser_session(browser_id="fake", agent_name="browser-use", agent_config={}):
        pass

    assert close_state["calls"] == 1


def test_open_browser_session_manager_tolerates_close_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBackend:
        backend_id = "fake"

        def open(self, agent_name: str, agent_config: Dict[str, Any]) -> BrowserSessionContext:
            return BrowserSessionContext(backend_id="fake", transport="local")

        def close(self, session_context: BrowserSessionContext) -> None:
            raise OSError("close failed")

    def fake_get_backend(browser_id: str) -> FakeBackend:
        return FakeBackend()

    monkeypatch.setattr(manager_module, "get_backend", fake_get_backend)
    with open_browser_session(browser_id="fake", agent_name="browser-use", agent_config={}):
        pass


# ---------------------------------------------------------------------------
# LocalBackend.local_proxy_*
# ---------------------------------------------------------------------------


def test_local_backend_no_proxy_keeps_metadata_empty() -> None:
    backend = LocalBackend("local")
    ctx = backend.open(agent_name="browser-use", agent_config={})
    assert ctx.transport == "local"
    assert "local_proxy" not in ctx.metadata


def test_local_backend_empty_proxy_server_treated_as_no_proxy() -> None:
    backend = LocalBackend("local")
    ctx = backend.open(
        agent_name="browser-use",
        agent_config={"local_proxy_server": "   "},  # whitespace-only
    )
    assert "local_proxy" not in ctx.metadata


def test_local_backend_passes_explicit_headless_setting() -> None:
    backend = LocalBackend("local")

    enabled = backend.open(agent_name="browser-use", agent_config={"headless": True})
    disabled = backend.open(agent_name="browser-use", agent_config={"headless": "false"})

    assert enabled.metadata["headless"] is True
    assert disabled.metadata["headless"] is False


def test_local_backend_passes_explicit_executable_path() -> None:
    backend = LocalBackend("local")
    ctx = backend.open(
        agent_name="browser-use",
        agent_config={"local_executable_path": "/opt/chrome"},
    )

    assert ctx.metadata["executable_path"] == "/opt/chrome"


def test_local_backend_server_only() -> None:
    backend = LocalBackend("local")
    ctx = backend.open(
        agent_name="browser-use",
        agent_config={"local_proxy_server": "http://127.0.0.1:7890"},
    )
    assert ctx.metadata["local_proxy"] == {"server": "http://127.0.0.1:7890"}


def test_local_backend_server_with_auth_and_bypass() -> None:
    backend = LocalBackend("local")
    ctx = backend.open(
        agent_name="browser-use",
        agent_config={
            "local_proxy_server": "http://proxy.corp:3128",
            "local_proxy_username": "alice",
            "local_proxy_password": "s3cr3t",
            "local_proxy_bypass": "127.0.0.1,localhost,*.local",
        },
    )
    assert ctx.metadata["local_proxy"] == {
        "server": "http://proxy.corp:3128",
        "username": "alice",
        "password": "s3cr3t",
        "bypass": "127.0.0.1,localhost,*.local",
    }


@pytest.fixture
def isolated_warned_agents(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Give each test its own _warned_agents set so the dedup state never
    leaks across tests (especially under pytest-xdist parallel workers)."""
    fresh: set[str] = set()
    monkeypatch.setattr(local_module, "_warned_agents", fresh)
    return fresh


def test_warn_if_local_proxy_unsupported_fires_once_per_agent(
    caplog: pytest.LogCaptureFixture,
    isolated_warned_agents: set[str],
) -> None:
    caplog.set_level("WARNING", logger=local_module.__name__)
    cfg = {"browser_id": "local", "local_proxy_server": "http://127.0.0.1:7890"}
    warn_if_local_proxy_unsupported(cfg, "Agent-TARS")
    warn_if_local_proxy_unsupported(cfg, "Agent-TARS")
    matches = [r for r in caplog.records if "Agent-TARS" in r.message]
    assert len(matches) == 1
    assert "http://127.0.0.1:7890" in matches[0].message


def test_warn_if_local_proxy_unsupported_silent_when_no_proxy(
    caplog: pytest.LogCaptureFixture,
    isolated_warned_agents: set[str],
) -> None:
    caplog.set_level("WARNING", logger=local_module.__name__)
    warn_if_local_proxy_unsupported({"browser_id": "local"}, "claude-code")
    warn_if_local_proxy_unsupported(
        {"browser_id": "local", "local_proxy_server": ""}, "claude-code"
    )
    assert not any("local_proxy" in r.message for r in caplog.records)


def test_warn_if_local_proxy_unsupported_skips_non_local_backend(
    caplog: pytest.LogCaptureFixture,
    isolated_warned_agents: set[str],
) -> None:
    caplog.set_level("WARNING", logger=local_module.__name__)
    warn_if_local_proxy_unsupported(
        {"browser_id": "lexmount", "local_proxy_server": "http://127.0.0.1:7890"},
        "Agent-TARS",
    )
    assert not any("local_proxy" in r.message for r in caplog.records)
