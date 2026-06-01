"""Tests for WebwrightAgent SDK integration."""

from __future__ import annotations

import json
import os
import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.agents import webwright as webwright_module
from browseruse_bench.agents.webwright import WebwrightAgent, _wall_clock_timeout
from browseruse_bench.browsers.types import BrowserSessionContext
from browseruse_bench.schemas import AgentResult

TASK_INFO = {
    "task_id": "ww-1",
    "task_text": "Find the title",
    "url": "https://example.com",
}


def test_build_config_spec_maps_openai_config() -> None:
    spec = WebwrightAgent._build_config_spec(
        agent_config={
            "api_key": "sk-test",
            "base_url": "https://gateway.example/v1/responses",
            "max_output_tokens": 1234,
            "request_timeout_seconds": 77,
        },
        model_type="openai",
        model_id="gpt-test",
        timeout=600,
        max_steps=42,
        session_transport="local",
    )

    assert spec[:2] == ["base.yaml", "model_openai.yaml"]
    assert "model.model_name=gpt-test" in spec
    assert "agent.step_limit=42" in spec
    assert "environment.command_timeout_seconds=600" in spec
    assert "environment.browser_mode=local" in spec
    assert all("sk-test" not in item for item in spec)
    assert "model.openai_endpoint=https://gateway.example/v1/responses" in spec
    assert "model.max_output_tokens=1234" in spec
    assert "model.request_timeout_seconds=77" in spec


def test_build_config_spec_normalizes_openai_base_url_to_responses_endpoint() -> None:
    spec = WebwrightAgent._build_config_spec(
        agent_config={"base_url": "https://gateway.example/v1"},
        model_type="openai",
        model_id="gpt-test",
        timeout=600,
        max_steps=42,
        session_transport="local",
    )

    assert "model.openai_endpoint=https://gateway.example/v1/responses" in spec


def test_resolve_model_type_uses_chat_completions_for_custom_openai_gateway() -> None:
    assert (
        WebwrightAgent._resolve_model_type({"model_type": "OPENAI", "model_api_style": "chat"})
        == "openrouter"
    )


def test_resolve_model_type_preserves_custom_openai_responses_gateway() -> None:
    assert (
        WebwrightAgent._resolve_model_type(
            {"model_type": "OPENAI", "base_url": "https://responses.example/v1"}
        )
        == "openai"
    )


def test_build_config_spec_maps_chat_completions_endpoint() -> None:
    spec = WebwrightAgent._build_config_spec(
        agent_config={
            "api_key": "sk-test",
            "base_url": "https://litellm.local.lexmount.net/v1",
        },
        model_type="openrouter",
        model_id="gpt-test",
        timeout=600,
        max_steps=42,
        session_transport="local",
    )

    assert spec[:2] == ["base.yaml", "model_openrouter.yaml"]
    assert all("sk-test" not in item for item in spec)
    assert "model.openrouter_endpoint=https://litellm.local.lexmount.net/v1/chat/completions" in spec


def test_build_config_spec_maps_anthropic_config() -> None:
    spec = WebwrightAgent._build_config_spec(
        agent_config={"api_key": "anthropic-key", "base_url": "https://anthropic.example/messages"},
        model_type="anthropic",
        model_id="claude-test",
        timeout=300,
        max_steps=100,
        session_transport="local",
    )

    assert spec[:2] == ["base.yaml", "model_claude.yaml"]
    assert all("anthropic-key" not in item for item in spec)
    assert "model.anthropic_endpoint=https://anthropic.example/messages" in spec


def test_build_config_spec_rejects_cloud_native_transport() -> None:
    with pytest.raises(ValueError, match="cloud_native"):
        WebwrightAgent._build_config_spec(
            agent_config={},
            model_type="openai",
            model_id="gpt-test",
            timeout=600,
            max_steps=42,
            session_transport="cloud_native",
        )


def test_run_task_calls_webwright_run_one_and_parses_artifacts(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_one(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        assert os.environ["OPENAI_API_KEY"] == "sk-test"
        screenshots_dir = tmp_path / "screenshots"
        screenshots_dir.mkdir(parents=True)
        (screenshots_dir / "step_0001.png").write_bytes(b"\x89PNG fake")
        steps_dir = tmp_path / "steps"
        steps_dir.mkdir()
        (steps_dir / "step_0001.py").write_text("await page.goto('https://example.com')\n")
        (tmp_path / "trajectory.json").write_text(
            json.dumps(
                {
                    "info": {"api_calls": 7},
                    "model": {
                        "usage": {
                            "cumulative_response": {
                                "input_tokens": 123,
                                "output_tokens": 45,
                                "total_tokens": 168,
                                "cached_input_tokens": 10,
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "exit_status": "Submitted",
            "final_response": "Example Domain",
            "_output_dir": str(tmp_path),
        }

    @contextmanager
    def fake_open_browser_session(**_: Any):
        yield BrowserSessionContext(
            backend_id="lexmount",
            transport="cdp",
            cdp_url="wss://lexmount.example/cdp",
        )

    monkeypatch.setattr(webwright_module, "_run_one", fake_run_one)
    monkeypatch.setattr(webwright_module, "_WEBWRIGHT_IMPORT_ERROR", None)
    monkeypatch.setattr(webwright_module, "open_browser_session", fake_open_browser_session)

    with monkeypatch.context() as env_patch:
        env_patch.delenv("OPENAI_API_KEY", raising=False)
        result = WebwrightAgent().run_task(
            task_info=TASK_INFO,
            agent_config={
                "model_type": "OPENAI",
                "model_id": "gpt-test",
                "api_key": "sk-test",
                "timeout": 30,
                "max_steps": 5,
            },
            task_workspace=tmp_path,
        )
        assert "OPENAI_API_KEY" not in os.environ

    assert isinstance(result, AgentResult)
    assert result.env_status.value == "success"
    assert result.agent_done.value == "done"
    assert result.agent_success is True
    assert result.answer == "Example Domain"
    assert result.model_id == "gpt-test"
    assert result.browser_id == "lexmount"
    assert result.action_history == ["step_0001: await page.goto('https://example.com')"]
    assert result.screenshots == ["screenshots/step_0001.png"]
    assert result.metrics.steps == 7
    assert result.metrics.usage is not None
    assert result.metrics.usage.total_prompt_tokens == 123
    assert result.metrics.usage.total_completion_tokens == 45
    assert result.metrics.usage.total_prompt_cached_tokens == 10

    assert captured["task_id"] == "ww-1"
    assert captured["start_url"] == "https://example.com"
    assert captured["resolved_output_dir"] == tmp_path
    assert captured["debug"] is False
    assert "model.model_name=gpt-test" in captured["config_spec"]
    assert "agent.step_limit=5" in captured["config_spec"]
    assert captured["config_spec"][:3] == ["base.yaml", "local_browser.yaml", "model_openai.yaml"]
    assert (
        "environment.environment_class="
        "browseruse_bench.agents.webwright_remote_cdp.RemoteCDPEnvironment"
        in captured["config_spec"]
    )
    assert "environment.browser_mode=local_cdp" in captured["config_spec"]
    assert "environment.remote_cdp_url=wss://lexmount.example/cdp" in captured["config_spec"]


def test_run_task_maps_openai_env_key_for_chat_gateway(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    def fake_run_one(**_: Any) -> dict[str, Any]:
        assert os.environ["OPENROUTER_API_KEY"] == "env-openai-key"
        return {
            "exit_status": "Submitted",
            "final_response": "ok",
            "api_calls": 1,
        }

    monkeypatch.setattr(webwright_module, "_run_one", fake_run_one)
    monkeypatch.setattr(webwright_module, "_WEBWRIGHT_IMPORT_ERROR", None)
    monkeypatch.setattr(webwright_module, "open_browser_session", _fake_open_lexmount)

    with monkeypatch.context() as env_patch:
        env_patch.setenv("OPENAI_API_KEY", "env-openai-key")
        env_patch.delenv("OPENROUTER_API_KEY", raising=False)
        result = WebwrightAgent().run_task(
            task_info=TASK_INFO,
            agent_config={
                "model_type": "OPENAI",
                "model_api_style": "chat_completions",
                "model_id": "gpt-test",
            },
            task_workspace=tmp_path,
        )
        assert "OPENROUTER_API_KEY" not in os.environ

    assert result.env_status.value == "success"


def test_run_task_maps_limits_exceeded_to_max_steps(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(webwright_module, "_run_one", lambda **_: {"exit_status": "LimitsExceeded"})
    monkeypatch.setattr(webwright_module, "_WEBWRIGHT_IMPORT_ERROR", None)
    monkeypatch.setattr(webwright_module, "open_browser_session", _fake_open_lexmount)

    result = WebwrightAgent().run_task(
        task_info=TASK_INFO,
        agent_config={"model_type": "OPENAI", "model_id": "gpt-test"},
        task_workspace=tmp_path,
    )

    assert result.env_status.value == "success"
    assert result.agent_done.value == "max_steps"
    assert result.agent_success is None


def test_run_task_maps_webwright_limits_exception_to_max_steps(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    class FakeLimitsExceeded(BaseException):
        pass

    def fake_run_one(**_: Any) -> dict[str, Any]:
        raise FakeLimitsExceeded("step limit")

    monkeypatch.setattr(webwright_module, "_run_one", fake_run_one)
    monkeypatch.setattr(webwright_module, "_WEBWRIGHT_IMPORT_ERROR", None)
    monkeypatch.setattr(webwright_module, "_WEBWRIGHT_LIMITS_EXCEEDED", FakeLimitsExceeded)
    monkeypatch.setattr(webwright_module, "open_browser_session", _fake_open_lexmount)

    result = WebwrightAgent().run_task(
        task_info=TASK_INFO,
        agent_config={"model_type": "OPENAI", "model_id": "gpt-test"},
        task_workspace=tmp_path,
    )

    assert result.env_status.value == "success"
    assert result.agent_done.value == "max_steps"
    assert result.error is None


def test_run_task_returns_failed_result_on_webwright_error(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    def fake_run_one(**_: Any) -> dict[str, Any]:
        raise RuntimeError("webwright crashed")

    monkeypatch.setattr(webwright_module, "_run_one", fake_run_one)
    monkeypatch.setattr(webwright_module, "_WEBWRIGHT_IMPORT_ERROR", None)
    monkeypatch.setattr(webwright_module, "open_browser_session", _fake_open_lexmount)

    result = WebwrightAgent().run_task(
        task_info=TASK_INFO,
        agent_config={"model_type": "OPENAI", "model_id": "gpt-test"},
        task_workspace=tmp_path,
    )

    assert result.env_status.value == "failed"
    assert result.agent_done.value == "error"
    assert result.error == "webwright crashed"
    assert "webwright crashed" in result.answer


def test_run_task_returns_failed_result_on_playwright_error(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    playwright = pytest.importorskip("playwright.async_api")

    def fake_run_one(**_: Any) -> dict[str, Any]:
        raise playwright.Error("remote session closed")

    monkeypatch.setattr(webwright_module, "_run_one", fake_run_one)
    monkeypatch.setattr(webwright_module, "_WEBWRIGHT_IMPORT_ERROR", None)
    monkeypatch.setattr(webwright_module, "open_browser_session", _fake_open_lexmount)

    result = WebwrightAgent().run_task(
        task_info=TASK_INFO,
        agent_config={"model_type": "OPENAI", "model_id": "gpt-test"},
        task_workspace=tmp_path,
    )

    assert result.env_status.value == "failed"
    assert result.agent_done.value == "error"
    assert result.error == "remote session closed"


def test_wall_clock_timeout_raises_and_restores_signal_timer() -> None:
    original_handler = signal.getsignal(signal.SIGALRM)

    try:
        with _wall_clock_timeout(1):
            signal.raise_signal(signal.SIGALRM)
    except TimeoutError as exc:
        assert str(exc) == "Timeout after 1 seconds"
    else:
        raise AssertionError("Expected TimeoutError")

    assert signal.getsignal(signal.SIGALRM) == original_handler


@contextmanager
def _fake_open_lexmount(**_: Any):
    yield BrowserSessionContext(
        backend_id="lexmount",
        transport="cdp",
        cdp_url="wss://lexmount.example/cdp",
    )
