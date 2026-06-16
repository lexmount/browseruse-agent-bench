"""Tests for Lexmount backend URL handling."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from browseruse_bench.browsers.providers import lexmount as lexmount_module
from browseruse_bench.browsers.providers.lexmount import LexmountBackend


def _install_fake_lexmount_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inspect_url: str = "",
    ws: str | None = "wss://cdp.lexmount.test/devtools",
    connect_url: str | None = "wss://cdp.lexmount.test/devtools",
    base_url: str = "https://api.lexmount.cn",
) -> Dict[str, Any]:
    state: Dict[str, Any] = {"write_calls": [], "create_calls": [], "fork_calls": [], "ctx_delete_calls": []}

    class FakeSession:
        def __init__(self) -> None:
            self.session_id = "session_1770797297774_ax3sde1w9"
            self.id = self.session_id
            self.ws = ws
            self.connect_url = connect_url  # backward-compat alias
            self.inspect_url = inspect_url

        def close(self) -> None:
            return None

    class FakeSessions:
        def create(
            self,
            browser_mode: str,
            official_proxy: bool = False,
            proxy: str | None = None,
            context: dict | None = None,
        ) -> FakeSession:
            state["create_calls"].append({
                "browser_mode": browser_mode,
                "official_proxy": official_proxy,
                "context": context,
            })
            return FakeSession()

        def delete(self, session_id: str) -> None:
            return None

    class FakeForkedContext:
        def __init__(self, ctx_id: str) -> None:
            self.id = ctx_id

    class FakeContexts:
        def fork(self, context_id: str) -> FakeForkedContext:
            state["fork_calls"].append(context_id)
            return FakeForkedContext("forked_ctx_9876")

        def delete(self, context_id: str) -> None:
            state["ctx_delete_calls"].append(context_id)

    class FakeLexmount:
        def __init__(self) -> None:
            self.sessions = FakeSessions()
            self.contexts = FakeContexts()
            self.base_url = base_url

    def fake_write_session_state(
        *,
        backend_id: str,
        session_id: str,
        forked_context_id: str | None = None,
    ) -> None:
        state["write_calls"].append((backend_id, session_id, forked_context_id))

    monkeypatch.setattr(lexmount_module, "Lexmount", FakeLexmount)
    monkeypatch.setattr(lexmount_module, "write_session_state", fake_write_session_state)
    return state


def test_lexmount_backend_uses_inspect_url_from_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """inspect_url returned by the API is used directly."""
    expected_url = (
        "https://browser.lexmount.cn/browser_dev/index.html"
        "?session_id=session_1770797297774_ax3sde1w9#api_host=api.lexmount.cn"
    )
    state = _install_fake_lexmount_sdk(monkeypatch, inspect_url=expected_url)
    backend = LexmountBackend("lexmount")

    session_context = backend.open(agent_name="browser-use", agent_config={})

    assert session_context.metadata["inspect_url"] == expected_url
    assert state["write_calls"] == [("lexmount", "session_1770797297774_ax3sde1w9", None)]


def test_lexmount_backend_falls_back_to_computed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """When session.inspect_url is empty, _build_debug_url is used as fallback."""
    state = _install_fake_lexmount_sdk(monkeypatch, inspect_url="")
    backend = LexmountBackend("lexmount")

    session_context = backend.open(agent_name="browser-use", agent_config={})

    assert session_context.metadata["inspect_url"] == (
        "https://browser.lexmount.cn/browser_dev/index.html"
        "?session_id=session_1770797297774_ax3sde1w9#api_host=api.lexmount.cn"
    )
    assert state["write_calls"] == [("lexmount", "session_1770797297774_ax3sde1w9", None)]


def test_lexmount_backend_falls_back_to_connect_url_when_ws_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When session.ws is None (older SDK), connect_url is used as the CDP URL."""
    cdp = "wss://cdp.lexmount.test/devtools"
    _install_fake_lexmount_sdk(monkeypatch, ws=None, connect_url=cdp)
    backend = LexmountBackend("lexmount")

    session_context = backend.open(agent_name="browser-use", agent_config={})

    assert session_context.cdp_url == cdp


def test_lexmount_backend_forks_login_context_when_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUBENCH_LOGIN_CONTEXT_ID triggers contexts.fork + read_write mount; close deletes fork."""
    state = _install_fake_lexmount_sdk(monkeypatch)
    monkeypatch.setenv(lexmount_module.LOGIN_CONTEXT_ID_ENV_KEY, "base_ctx_abc")
    backend = LexmountBackend("lexmount")

    session_context = backend.open(agent_name="browser-use", agent_config={})

    # fork was called with the base id from env, not user config.
    assert state["fork_calls"] == ["base_ctx_abc"]
    # session was created mounted on the forked id in read_write mode.
    assert state["create_calls"] == [
        {
            "browser_mode": "normal",
            "official_proxy": False,
            "context": {"id": "forked_ctx_9876", "mode": "read_write"},
        }
    ]
    # write_session_state received the fork id so orphan cleanup can reach it.
    assert state["write_calls"] == [
        ("lexmount", "session_1770797297774_ax3sde1w9", "forked_ctx_9876")
    ]
    # metadata carries both ids for close() to find.
    assert session_context.metadata["forked_context_id"] == "forked_ctx_9876"
    assert session_context.metadata["base_login_context_id"] == "base_ctx_abc"

    # close() must delete the fork and leave the base alone.
    backend.close(session_context)
    assert state["ctx_delete_calls"] == ["forked_ctx_9876"]


def test_lexmount_backend_skips_fork_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No BUBENCH_LOGIN_CONTEXT_ID → no fork, no context mount, no fork delete."""
    state = _install_fake_lexmount_sdk(monkeypatch)
    monkeypatch.delenv(lexmount_module.LOGIN_CONTEXT_ID_ENV_KEY, raising=False)
    backend = LexmountBackend("lexmount")

    session_context = backend.open(agent_name="browser-use", agent_config={})

    assert state["fork_calls"] == []
    assert state["create_calls"] == [
        {"browser_mode": "normal", "official_proxy": False, "context": None}
    ]
    assert "forked_context_id" not in session_context.metadata
    backend.close(session_context)
    assert state["ctx_delete_calls"] == []


def _install_kwarg_capturing_lexmount(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """Install a Lexmount stub that records the constructor kwargs.

    Mirrors _install_fake_lexmount_sdk but exposes the kwargs captured from
    LexmountBackend.open() so profile-routing tests can assert on which
    credentials reached the SDK.
    """
    state = _install_fake_lexmount_sdk(monkeypatch)
    base_class = lexmount_module.Lexmount
    captured_init_kwargs: dict[str, Any] = {}

    class FakeLexmountCapturing(base_class):
        def __init__(self, **kwargs: Any) -> None:
            captured_init_kwargs.update(kwargs)
            super().__init__()

    state["__init_kwargs"] = captured_init_kwargs
    monkeypatch.setattr(lexmount_module, "Lexmount", FakeLexmountCapturing)
    return state


def test_lexmount_profile_creds_used_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """BUBENCH_LEXMOUNT_PROFILE selects per-profile credentials over top-level fallbacks."""
    state = _install_kwarg_capturing_lexmount(monkeypatch)
    monkeypatch.setenv(lexmount_module.LEXMOUNT_PROFILE_ENV_KEY, "zh")
    monkeypatch.delenv(lexmount_module.LOGIN_CONTEXT_ID_ENV_KEY, raising=False)
    backend = LexmountBackend("lexmount")

    backend.open(
        agent_name="browser-use",
        agent_config={
            "lexmount_api_key": "K-default",
            "lexmount_project_id": "P-default",
            "lexmount_base_url": "https://default.example",
            "lexmount_profiles": {
                "zh": {
                    "api_key": "K-zh",
                    "project_id": "P-zh",
                    "base_url": "https://zh.example",
                },
                "en": {
                    "api_key": "K-en",
                    "project_id": "P-en",
                    "base_url": "https://en.example",
                },
            },
        },
    )

    assert state["__init_kwargs"] == {
        "api_key": "K-zh",
        "project_id": "P-zh",
        "base_url": "https://zh.example",
    }


def test_lexmount_top_level_when_profile_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """No profile env var → top-level lexmount_* keys feed Lexmount()."""
    state = _install_kwarg_capturing_lexmount(monkeypatch)
    monkeypatch.delenv(lexmount_module.LEXMOUNT_PROFILE_ENV_KEY, raising=False)
    monkeypatch.delenv(lexmount_module.LOGIN_CONTEXT_ID_ENV_KEY, raising=False)
    backend = LexmountBackend("lexmount")

    backend.open(
        agent_name="browser-use",
        agent_config={
            "lexmount_api_key": "K-default",
            "lexmount_project_id": "P-default",
            "lexmount_base_url": "https://default.example",
            "lexmount_profiles": {
                "zh": {"api_key": "K-zh", "project_id": "P-zh", "base_url": "https://zh.example"},
            },
        },
    )

    assert state["__init_kwargs"] == {
        "api_key": "K-default",
        "project_id": "P-default",
        "base_url": "https://default.example",
    }


def test_lexmount_profile_partial_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Profile that defines only base_url leaves api_key / project_id falling back to top-level."""
    state = _install_kwarg_capturing_lexmount(monkeypatch)
    monkeypatch.setenv(lexmount_module.LEXMOUNT_PROFILE_ENV_KEY, "en")
    monkeypatch.delenv(lexmount_module.LOGIN_CONTEXT_ID_ENV_KEY, raising=False)
    backend = LexmountBackend("lexmount")

    backend.open(
        agent_name="browser-use",
        agent_config={
            "lexmount_api_key": "K-default",
            "lexmount_project_id": "P-default",
            "lexmount_base_url": "https://default.example",
            "lexmount_profiles": {
                "en": {"base_url": "https://en.example"},
            },
        },
    )

    assert state["__init_kwargs"] == {
        "api_key": "K-default",
        "project_id": "P-default",
        "base_url": "https://en.example",
    }


def test_lexmount_official_proxy_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lexmount sessions default to official_proxy=False unless config opts in."""
    state = _install_fake_lexmount_sdk(monkeypatch)
    backend = LexmountBackend("lexmount")

    backend.open(agent_name="browser-use", agent_config={})

    assert state["create_calls"] == [
        {"browser_mode": "normal", "official_proxy": False, "context": None}
    ]


def test_lexmount_official_proxy_from_top_level_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lexmount_official_proxy controls the SDK sessions.create official_proxy kwarg."""
    state = _install_fake_lexmount_sdk(monkeypatch)
    backend = LexmountBackend("lexmount")

    backend.open(
        agent_name="browser-use",
        agent_config={"lexmount_official_proxy": True},
    )

    assert state["create_calls"] == [
        {"browser_mode": "normal", "official_proxy": True, "context": None}
    ]


def test_lexmount_official_proxy_profile_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-profile official_proxy can override the top-level lexmount_official_proxy."""
    state = _install_fake_lexmount_sdk(monkeypatch)
    monkeypatch.setenv(lexmount_module.LEXMOUNT_PROFILE_ENV_KEY, "zh")
    backend = LexmountBackend("lexmount")

    backend.open(
        agent_name="browser-use",
        agent_config={
            "lexmount_official_proxy": False,
            "lexmount_profiles": {
                "zh": {"official_proxy": True},
            },
        },
    )

    assert state["create_calls"] == [
        {"browser_mode": "normal", "official_proxy": True, "context": None}
    ]
