"""Tests for browser-use agent session abstraction integration."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from browser_use.llm.exceptions import ModelProviderError
from browser_use.llm.messages import SystemMessage, UserMessage
from pydantic import BaseModel, ValidationError

from browseruse_bench.agents import browser_use as browser_use_module
from browseruse_bench.agents.browser_use import BrowserUseAgent
from browseruse_bench.browsers.types import BrowserSessionContext


class _StubLLM:
    async def ainvoke(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_run_task_uses_backend_manager_session_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_open_browser_session(
        browser_id: str,
        agent_name: str,
        agent_config: dict[str, Any],
    ) -> Iterator[BrowserSessionContext]:
        captured["browser_id"] = browser_id
        captured["agent_name"] = agent_name
        captured["agent_config"] = agent_config
        yield BrowserSessionContext(
            backend_id="agentbay",
            transport="cdp",
            cdp_url="wss://agentbay.example/cdp",
        )

    async def fake_run_task_async(
        self: BrowserUseAgent,
        task_info: dict[str, Any],
        task_workspace: Path,
        timeout: int,
        flash_mode: bool,
        agent_config: dict[str, Any],
        session_context: BrowserSessionContext,
    ) -> dict[str, Any]:
        captured["session_context"] = session_context
        captured["timeout"] = timeout
        captured["flash_mode"] = flash_mode
        return {
            "task_id": task_info["task_id"],
            "status": "success",
            "answer": "ok",
            "metrics": {},
            "browser_id": session_context.backend_id,
        }

    monkeypatch.setattr(browser_use_module, "open_browser_session", fake_open_browser_session)
    monkeypatch.setattr(BrowserUseAgent, "_run_task_async", fake_run_task_async)

    result = BrowserUseAgent().run_task(
        task_info={"task_id": "t1", "task_text": "open", "url": "https://example.com"},
        agent_config={"BROWSER_ID": "agentbay", "FLASH_MODE": False, "timeout_seconds": 120},
        task_workspace=tmp_path,
    )

    assert result["status"] == "success"
    assert result["browser_id"] == "agentbay"
    assert captured["browser_id"] == "agentbay"
    assert captured["agent_name"] == "browser-use"
    assert captured["timeout"] == 120
    assert captured["flash_mode"] is False
    assert captured["session_context"].cdp_url == "wss://agentbay.example/cdp"


def test_run_task_rejects_unknown_backend(tmp_path: Path) -> None:
    agent = BrowserUseAgent()
    with pytest.raises(ValueError, match="Unknown browser backend"):
        agent.run_task(
            task_info={"task_id": "t1", "task_text": "open", "url": "https://example.com"},
            agent_config={"BROWSER_ID": "not-exists"},
            task_workspace=tmp_path,
        )


def test_browser_use_browser_extends_sdk_cdp_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeouts: list[float | None] = []

    async def fake_wait_for(fut: Any, timeout: float | None = None) -> Any:
        observed_timeouts.append(timeout)
        return await fut

    async def fake_start(self: Any) -> str:
        del self

        async def ready() -> str:
            return "started"

        return await browser_use_module.browser_use_session_module.asyncio.wait_for(
            ready(),
            timeout=15.0,
        )

    async def fake_auto_reconnect(self: Any) -> str:
        del self

        async def ready() -> str:
            return "reconnected"

        return await browser_use_module.browser_use_session_module.asyncio.wait_for(
            ready(),
            timeout=15.0,
        )

    monkeypatch.setattr(
        browser_use_module.browser_use_session_module.asyncio,
        "wait_for",
        fake_wait_for,
    )
    monkeypatch.setattr(browser_use_module.BrowserUseSDKBrowser, "start", fake_start)
    monkeypatch.setattr(
        browser_use_module.BrowserUseSDKBrowser,
        "_auto_reconnect",
        fake_auto_reconnect,
    )

    browser = browser_use_module.Browser(cdp_url="wss://agentbay.example/cdp")

    assert asyncio.run(browser.start()) == "started"
    assert asyncio.run(browser._auto_reconnect()) == "reconnected"
    assert observed_timeouts == [
        browser_use_module.BROWSER_USE_CDP_CONNECT_TIMEOUT_SECONDS,
        browser_use_module.BROWSER_USE_CDP_CONNECT_TIMEOUT_SECONDS,
    ]
    assert browser_use_module.browser_use_session_module.asyncio.wait_for is fake_wait_for


def test_browser_use_browser_rewrites_sdk_cdp_timeout_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_start(self: Any) -> None:
        del self
        raise RuntimeError(
            "connect() timed out after 15s - CDP connection to wss://lexmount.example/cdp "
            "is too slow or unresponsive"
        )

    monkeypatch.setattr(browser_use_module.BrowserUseSDKBrowser, "start", fake_start)

    browser = browser_use_module.Browser(cdp_url="wss://lexmount.example/cdp")

    with pytest.raises(RuntimeError, match="timed out after 30s"):
        asyncio.run(browser.start())


class _OutputForParserTest(BaseModel):
    memory: str
    action: list[dict[str, Any]]


class _OutputForValidationKwargsTest(BaseModel):
    count: int


def test_patch_output_model_json_parser_accepts_markdown_fence() -> None:
    browser_use_module._patch_output_model_json_parser(_OutputForParserTest)

    parsed = _OutputForParserTest.model_validate_json(
        '```json\n{"memory": "ok", "action": [{"wait": {"seconds": 5}}]}\n```'
    )

    assert parsed.memory == "ok"
    assert parsed.action == [{"wait": {"seconds": 5}}]


def test_patch_output_model_json_parser_accepts_natural_language_prefix() -> None:
    browser_use_module._patch_output_model_json_parser(_OutputForParserTest)

    parsed = _OutputForParserTest.model_validate_json(
        'The page is still loading.\n{"memory": "loaded", "action": [{"wait": {"seconds": 3}}]}'
    )

    assert parsed.memory == "loaded"


def test_patch_output_model_json_parser_accepts_trailing_text() -> None:
    browser_use_module._patch_output_model_json_parser(_OutputForParserTest)

    parsed = _OutputForParserTest.model_validate_json(
        '{"memory": "done", "action": [{"done": {"text": "ok"}}]}\nExtra explanation.'
    )

    assert parsed.action == [{"done": {"text": "ok"}}]


def test_patch_output_model_json_parser_skips_non_matching_json_candidate() -> None:
    browser_use_module._patch_output_model_json_parser(_OutputForParserTest)

    parsed = _OutputForParserTest.model_validate_json(
        'I observed {"not": "agent output"} before deciding.\n'
        '{"memory": "chosen", "action": [{"wait": {"seconds": 1}}]}'
    )

    assert parsed.memory == "chosen"


def test_patch_output_model_json_parser_still_rejects_schema_mismatch() -> None:
    browser_use_module._patch_output_model_json_parser(_OutputForParserTest)

    with pytest.raises(ValidationError):
        _OutputForParserTest.model_validate_json('{"memory": "missing action"}')


def test_patch_output_model_json_parser_preserves_validation_kwargs() -> None:
    browser_use_module._patch_output_model_json_parser(_OutputForValidationKwargsTest)

    with pytest.raises(ValidationError):
        _OutputForValidationKwargsTest.model_validate_json('prefix {"count": "1"}', strict=True)

    parsed = _OutputForValidationKwargsTest.model_validate_json('prefix {"count": "1"}')
    assert parsed.count == 1


def test_patch_output_model_json_parser_preserves_validation_kwargs_for_wrapped_json() -> None:
    browser_use_module._patch_output_model_json_parser(_OutputForValidationKwargsTest)

    with pytest.raises(ValidationError):
        _OutputForValidationKwargsTest.model_validate_json(
            '{"arguments": "{\\"count\\": \\"1\\"}"}',
            strict=True,
        )

    parsed = _OutputForValidationKwargsTest.model_validate_json(
        '{"arguments": "{\\"count\\": \\"1\\"}"}'
    )
    assert parsed.count == 1


def test_strip_numeric_bounds_removes_nested_schema_limits() -> None:
    schema = {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 0, "maximum": 5},
            "items": {
                "type": "array",
                "items": {"type": "number", "exclusiveMinimum": 1, "exclusiveMaximum": 10},
            },
        },
    }

    browser_use_module._strip_numeric_bounds(schema)

    assert schema == {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "items": {"type": "array", "items": {"type": "number"}},
        },
    }


def test_enable_claude_thinking_injects_reasoning_params() -> None:
    captured: dict[str, Any] = {}

    class FakeCompletions:
        async def create(self, *args: Any, **kwargs: Any) -> str:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return "ok"

    class FakeChat:
        def __init__(self) -> None:
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self) -> None:
            self.chat = FakeChat()

    class FakeLLM:
        def get_client(self) -> FakeClient:
            return FakeClient()

    llm = FakeLLM()
    browser_use_module._enable_claude_thinking(llm, "medium")

    result = asyncio.run(
        llm.get_client().chat.completions.create(
            messages=[],
            extra_body={"allowed_openai_params": ["temperature"]},
        )
    )

    assert result == "ok"
    assert captured["kwargs"]["extra_body"] == {
        "reasoning_effort": "medium",
        "allowed_openai_params": ["temperature", "reasoning_effort"],
    }


def test_create_llm_enables_claude_schema_and_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeLLM:
        def __init__(self, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

    def fake_patch_schema_optimizer() -> None:
        captured["schema_patched"] = True

    def fake_enable_thinking(llm: Any, reasoning_effort: str) -> None:
        captured["thinking_llm"] = llm
        captured["reasoning_effort"] = reasoning_effort

    monkeypatch.setattr(browser_use_module, "ChatOpenAI", FakeLLM)
    monkeypatch.setattr(
        browser_use_module,
        "_patch_schema_optimizer_for_claude",
        fake_patch_schema_optimizer,
    )
    monkeypatch.setattr(browser_use_module, "_enable_claude_thinking", fake_enable_thinking)

    config_info: dict[str, Any] = {}
    llm = BrowserUseAgent()._create_llm(
        "OPENAI",
        "openrouter/claude-opus-4.8",
        {
            "api_key": "key",
            "base_url": "https://gateway.example/v1",
            "claude_reasoning_effort": "medium",
        },
        config_info,
    )

    assert captured["schema_patched"] is True
    assert captured["thinking_llm"] is llm
    assert captured["reasoning_effort"] == "medium"
    assert captured["kwargs"]["model"] == "openrouter/claude-opus-4.8"
    assert config_info["claude_schema_numeric_bounds_stripped"] is True
    assert config_info["claude_reasoning_effort"] == "medium"


def test_create_llm_uses_responses_adapter_for_responses_api_style() -> None:
    config_info: dict[str, Any] = {}

    llm = BrowserUseAgent()._create_llm(
        "OPENAI",
        "grok-4.5",
        {
            "api_key": "xai-key",
            "base_url": "https://api.x.ai/v1",
            "model_api_style": "responses",
            "max_tokens": 1234,
        },
        config_info,
    )

    assert isinstance(llm, browser_use_module._BrowserUseResponsesLLM)
    assert llm.model == "grok-4.5"
    assert llm.api_key == "xai-key"
    assert llm.base_url == "https://api.x.ai/v1"
    assert llm.max_output_tokens == 1234
    assert config_info["model_api_style"] == "responses"


def test_responses_adapter_parses_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponses:
        async def create(self, **kwargs: Any) -> Any:
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                output_text='{"memory": "ok", "action": [{"done": {"text": "done"}}]}',
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )

    class FakeClient:
        responses = FakeResponses()

    llm = browser_use_module._BrowserUseResponsesLLM(
        model="grok-4.5",
        api_key="xai-key",
        base_url="https://api.x.ai/v1",
        max_output_tokens=100,
    )
    monkeypatch.setattr(llm, "get_client", lambda: FakeClient())

    result = asyncio.run(
        llm.ainvoke(
            [SystemMessage(content="system"), UserMessage(content="task")],
            output_format=_OutputForParserTest,
        )
    )

    assert result.completion.memory == "ok"
    assert result.usage is not None
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 5
    assert captured["kwargs"]["model"] == "grok-4.5"
    assert captured["kwargs"]["max_output_tokens"] == 100
    assert captured["kwargs"]["text"]["format"]["type"] == "json_schema"
    assert captured["kwargs"]["text"]["format"]["name"] == "OutputForParserTest"
    assert captured["kwargs"]["text"]["format"]["strict"] is True
    assert captured["kwargs"]["text"]["format"]["schema"]["type"] == "object"


def test_responses_adapter_does_not_force_text_format_without_output_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponses:
        async def create(self, **kwargs: Any) -> Any:
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                output_text="plain text",
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            )

    class FakeClient:
        responses = FakeResponses()

    llm = browser_use_module._BrowserUseResponsesLLM(model="grok-4.5")
    monkeypatch.setattr(llm, "get_client", lambda: FakeClient())

    result = asyncio.run(llm.ainvoke([UserMessage(content="task")]))

    assert result.completion == "plain text"
    assert "text" not in captured["kwargs"]


def test_create_browser_instance_rejects_cloud_transport_for_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported browser backend for browser-use agent"):
        BrowserUseAgent._create_browser_instance(
            session_context=BrowserSessionContext(
                backend_id="skyvern-cloud",
                transport="cloud_native",
            )
        )


def test_close_browser_runtime_supports_stop() -> None:
    class FakeBrowser:
        def __init__(self) -> None:
            self.stop_calls = 0

        async def stop(self) -> None:
            self.stop_calls += 1

    browser = FakeBrowser()
    asyncio.run(BrowserUseAgent._close_browser_runtime(browser=browser, task_id="t-sync"))
    assert browser.stop_calls == 1


def test_close_browser_runtime_tolerates_close_error() -> None:
    class BrokenBrowser:
        async def stop(self) -> None:
            raise OSError("close failed")

    asyncio.run(BrowserUseAgent._close_browser_runtime(browser=BrokenBrowser(), task_id="t-error"))


def test_run_task_async_tolerates_temp_dir_cleanup_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FakeAgent:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self, max_steps: int) -> None:
            del max_steps
            return None

    class FakeBrowser:
        async def stop(self) -> None:
            return None

    class BrokenTempDir:
        def cleanup(self) -> None:
            raise OSError("cleanup failed")

    monkeypatch.setattr(browser_use_module, "Agent", FakeAgent)
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_browser_instance",
        staticmethod(lambda session_context: (FakeBrowser(), BrokenTempDir())),
    )
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_llm",
        lambda self, model_type, model_id, agent_config, config_info: _StubLLM(),
    )
    caplog.set_level("WARNING")

    result = asyncio.run(
        BrowserUseAgent()._run_task_async(
            task_info={"task_id": "t-cleanup", "task_text": "open page", "url": "https://example.com"},
            task_workspace=tmp_path,
            timeout=1,
            flash_mode=False,
            agent_config={"MODEL_TYPE": "OPENAI", "MODEL_ID": "gpt-test"},
            session_context=BrowserSessionContext(backend_id="Chrome-Local", transport="local"),
        )
    )

    assert result.env_status.value == "failed"
    assert result.agent_done.value == "error"
    assert result.error == "Agent returned no history before completion"
    assert any("Failed to cleanup temporary directory" in record.message for record in caplog.records)


def test_run_task_async_maps_early_unfinished_history_to_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeHistory:
        history: list[Any] = []

        def extracted_content(self) -> list[str]:
            return ["Waited for 3 seconds"]

        def number_of_steps(self) -> int:
            return 4

        def screenshots(self) -> list[str]:
            return []

        def errors(self) -> list[str | None]:
            return [None, None, None, None]

        def is_done(self) -> bool:
            return False

        def final_result(self) -> str:
            return "Waited for 3 seconds"

    class FakeAgent:
        def __init__(self, **_: Any) -> None:
            self.history = FakeHistory()

        async def run(self, max_steps: int) -> FakeHistory:
            assert max_steps == 40
            return self.history

    class FakeBrowser:
        async def stop(self) -> None:
            return None

    monkeypatch.setattr(browser_use_module, "Agent", FakeAgent)
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_browser_instance",
        staticmethod(lambda session_context: (FakeBrowser(), None)),
    )
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_llm",
        lambda self, model_type, model_id, agent_config, config_info: _StubLLM(),
    )

    result = asyncio.run(
        BrowserUseAgent()._run_task_async(
            task_info={"task_id": "t-incomplete", "task_text": "search", "url": "https://example.com"},
            task_workspace=tmp_path,
            timeout=600,
            flash_mode=False,
            agent_config={"MODEL_TYPE": "OPENAI", "MODEL_ID": "gpt-test", "SAVE_API_LOGS": False},
            session_context=BrowserSessionContext(backend_id="Chrome-Local", transport="local"),
        )
    )

    assert result.env_status.value == "failed"
    assert result.agent_done.value == "error"
    assert result.agent_success is None
    assert result.metrics.steps == 4
    assert result.error == "Agent stopped before completion after 4 steps without reporting done"
    assert result.answer == "[Task Failed: Agent stopped before completion after 4 steps without reporting done]"


def test_run_task_async_keeps_real_max_steps_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeHistory:
        history: list[Any] = []

        def extracted_content(self) -> list[str]:
            return []

        def number_of_steps(self) -> int:
            return 40

        def screenshots(self) -> list[str]:
            return []

        def errors(self) -> list[str | None]:
            return [None, "Failed to complete task in maximum steps"]

        def is_done(self) -> bool:
            return False

        def final_result(self) -> str:
            return "last non-final content"

    class FakeAgent:
        def __init__(self, **_: Any) -> None:
            self.history = FakeHistory()

        async def run(self, max_steps: int) -> FakeHistory:
            assert max_steps == 40
            return self.history

    class FakeBrowser:
        async def stop(self) -> None:
            return None

    monkeypatch.setattr(browser_use_module, "Agent", FakeAgent)
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_browser_instance",
        staticmethod(lambda session_context: (FakeBrowser(), None)),
    )
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_llm",
        lambda self, model_type, model_id, agent_config, config_info: _StubLLM(),
    )

    result = asyncio.run(
        BrowserUseAgent()._run_task_async(
            task_info={"task_id": "t-max", "task_text": "search", "url": "https://example.com"},
            task_workspace=tmp_path,
            timeout=600,
            flash_mode=False,
            agent_config={"MODEL_TYPE": "OPENAI", "MODEL_ID": "gpt-test", "SAVE_API_LOGS": False},
            session_context=BrowserSessionContext(backend_id="Chrome-Local", transport="local"),
        )
    )

    assert result.env_status.value == "success"
    assert result.agent_done.value == "max_steps"
    assert result.agent_success is None
    assert result.metrics.steps == 40
    assert result.error == "Failed to complete task in maximum steps"
    assert result.answer == "[Task Failed: Failed to complete task in maximum steps]"


def test_run_task_async_passes_configured_llm_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class FakeHistory:
        history: list[Any] = []

        def extracted_content(self) -> list[str]:
            return ["ok"]

        def number_of_steps(self) -> int:
            return 1

        def screenshots(self) -> list[str]:
            return []

        def errors(self) -> list[str | None]:
            return [None]

        def is_done(self) -> bool:
            return True

        def is_successful(self) -> bool:
            return True

        def final_result(self) -> str:
            return "ok"

    class FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)
            self.history = FakeHistory()

        async def run(self, max_steps: int) -> FakeHistory:
            assert max_steps == 40
            return self.history

    class FakeBrowser:
        async def stop(self) -> None:
            return None

    monkeypatch.setattr(browser_use_module, "Agent", FakeAgent)
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_browser_instance",
        staticmethod(lambda session_context: (FakeBrowser(), None)),
    )
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_llm",
        lambda self, model_type, model_id, agent_config, config_info: _StubLLM(),
    )

    result = asyncio.run(
        BrowserUseAgent()._run_task_async(
            task_info={"task_id": "t-timeout", "task_text": "search", "url": "https://example.com"},
            task_workspace=tmp_path,
            timeout=600,
            flash_mode=False,
            agent_config={
                "MODEL_TYPE": "OPENAI",
                "MODEL_ID": "gpt-test",
                "SAVE_API_LOGS": False,
                "llm_timeout": "120",
            },
            session_context=BrowserSessionContext(backend_id="Chrome-Local", transport="local"),
        )
    )

    assert captured["llm_timeout"] == 120
    assert result.config["llm_timeout_seconds"] == 120
    assert result.agent_success is True


# ---------------------------------------------------------------------------
# local_proxy → BrowserUseProxySettings → Browser kwargs
# ---------------------------------------------------------------------------


def test_create_browser_instance_passes_local_proxy_to_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeBrowser:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(browser_use_module, "Browser", FakeBrowser)

    ctx = BrowserSessionContext(
        backend_id="local",
        transport="local",
        metadata={
            "local_proxy": {
                "server": "http://127.0.0.1:7890",
                "username": "alice",
                "password": "s3cr3t",
                "bypass": "127.0.0.1,localhost",
            }
        },
    )
    _, temp_dir = BrowserUseAgent._create_browser_instance(session_context=ctx)
    try:
        proxy = captured["proxy"]
        # ProxySettings is a pydantic model; access is attribute-based.
        assert proxy.server == "http://127.0.0.1:7890"
        assert proxy.username == "alice"
        assert proxy.password == "s3cr3t"
        assert proxy.bypass == "127.0.0.1,localhost"
        assert captured["headless"] is False
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def test_create_browser_instance_no_proxy_omits_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeBrowser:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(browser_use_module, "Browser", FakeBrowser)

    ctx = BrowserSessionContext(backend_id="local", transport="local")
    _, temp_dir = BrowserUseAgent._create_browser_instance(session_context=ctx)
    try:
        assert "proxy" not in captured
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def test_create_browser_instance_uses_local_headless_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeBrowser:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(browser_use_module, "Browser", FakeBrowser)

    ctx = BrowserSessionContext(
        backend_id="local",
        transport="local",
        metadata={"headless": True, "executable_path": "/opt/chrome"},
    )
    _, temp_dir = BrowserUseAgent._create_browser_instance(session_context=ctx)
    try:
        assert captured["headless"] is True
        assert captured["executable_path"] == "/opt/chrome"
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


# ---------------------------------------------------------------------------
# Raw LLM response capture for failed/unparseable calls
# ---------------------------------------------------------------------------


_RAW_LLM_TEXT = '{"action": []}\n{"trailing": true}'
_PARSE_FAIL_MESSAGE = "Invalid JSON: trailing characters at line 2 column 1"


class _FakeUsage:
    def model_dump(self) -> dict[str, Any]:
        return {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def _fake_chat_completion(raw_text: str) -> Any:
    message = SimpleNamespace(content=raw_text)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=_FakeUsage())


class _OpenAIStyleLLM:
    """ChatOpenAI-shaped fake: get_client() serves a raw completion, ainvoke fails to parse it."""

    def __init__(self, raw_text: str | None = _RAW_LLM_TEXT, fail: bool = True) -> None:
        self.raw_text = raw_text
        self.fail = fail

    def get_client(self) -> Any:
        async def create(*args: Any, **kwargs: Any) -> Any:
            return _fake_chat_completion(self.raw_text or "")

        completions = SimpleNamespace(create=create)
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        if self.raw_text is not None:
            await self.get_client().chat.completions.create()
        if self.fail:
            raise ModelProviderError(message=_PARSE_FAIL_MESSAGE, model="gpt-test")
        return "parsed"


def test_capture_llm_failures_records_raw_response_and_usage() -> None:
    llm = _OpenAIStyleLLM()
    recorder = browser_use_module._LLMFailureRecorder()
    browser_use_module._capture_llm_failures(llm, recorder)

    with pytest.raises(ModelProviderError):
        asyncio.run(llm.ainvoke([]))

    assert len(recorder.failures) == 1
    failure = recorder.failures[0]
    assert failure["raw_response"] == _RAW_LLM_TEXT
    assert failure["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert failure["error"] == _PARSE_FAIL_MESSAGE
    assert failure["status_code"] == 502
    assert isinstance(failure["timestamp"], float)


def test_capture_llm_failures_keeps_no_records_on_success() -> None:
    llm = _OpenAIStyleLLM(fail=False)
    recorder = browser_use_module._LLMFailureRecorder()
    browser_use_module._capture_llm_failures(llm, recorder)

    assert asyncio.run(llm.ainvoke([])) == "parsed"
    assert recorder.failures == []


def test_capture_llm_failures_clears_stale_raw_response() -> None:
    llm = _OpenAIStyleLLM(fail=False)
    recorder = browser_use_module._LLMFailureRecorder()
    browser_use_module._capture_llm_failures(llm, recorder)

    asyncio.run(llm.ainvoke([]))

    # Second call raises before any completion arrives; the first call's raw
    # response must not leak into this failure record.
    llm.raw_text = None
    llm.fail = True
    with pytest.raises(ModelProviderError):
        asyncio.run(llm.ainvoke([]))

    assert len(recorder.failures) == 1
    assert recorder.failures[0]["raw_response"] is None
    assert recorder.failures[0]["usage"] is None


def test_capture_llm_failures_supports_llm_without_get_client() -> None:
    class NoClientLLM:
        async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
            raise ModelProviderError(message="provider down", model="other")

    llm = NoClientLLM()
    recorder = browser_use_module._LLMFailureRecorder()
    browser_use_module._capture_llm_failures(llm, recorder)

    with pytest.raises(ModelProviderError):
        asyncio.run(llm.ainvoke([]))

    assert recorder.failures[0]["error"] == "provider down"
    assert recorder.failures[0]["raw_response"] is None


def test_match_step_llm_failures_pops_only_step_window() -> None:
    hist_item = SimpleNamespace(
        metadata=SimpleNamespace(step_start_time=100.0, step_end_time=110.0)
    )
    pending = [
        {"timestamp": 99.0, "error": "before"},
        {"timestamp": 105.0, "error": "inside"},
        {"timestamp": 111.0, "error": "after"},
    ]

    matched = browser_use_module._match_step_llm_failures(pending, hist_item)

    assert [failure["error"] for failure in matched] == ["inside"]
    assert [failure["error"] for failure in pending] == ["before", "after"]


def test_match_step_llm_failures_without_metadata_returns_empty() -> None:
    pending = [{"timestamp": 105.0, "error": "inside"}]

    matched = browser_use_module._match_step_llm_failures(
        pending,
        SimpleNamespace(metadata=None),
    )

    assert matched == []
    assert len(pending) == 1


def test_run_task_async_writes_llm_failure_into_step_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeActionResult:
        extracted_content = None
        error = "Could not parse response"
        is_done = False

    class FakeHistory:
        def __init__(self, start: float, end: float) -> None:
            self.history = [
                SimpleNamespace(
                    model_output=None,
                    result=[FakeActionResult()],
                    state=None,
                    state_message=None,
                    metadata=SimpleNamespace(step_start_time=start, step_end_time=end),
                )
            ]

        def extracted_content(self) -> list[str]:
            return []

        def number_of_steps(self) -> int:
            return 1

        def screenshots(self) -> list[str]:
            return []

        def errors(self) -> list[str | None]:
            return ["Could not parse response"]

        def is_done(self) -> bool:
            return False

        def final_result(self) -> str:
            return ""

    class FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.llm = kwargs["llm"]
            self.history: FakeHistory | None = None

        async def run(self, max_steps: int) -> FakeHistory:
            del max_steps
            start = time.time()
            with contextlib.suppress(ModelProviderError):
                await self.llm.ainvoke([])
            self.history = FakeHistory(start, time.time())
            return self.history

    class FakeBrowser:
        async def stop(self) -> None:
            return None

    monkeypatch.setattr(browser_use_module, "Agent", FakeAgent)
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_browser_instance",
        staticmethod(lambda session_context: (FakeBrowser(), None)),
    )
    monkeypatch.setattr(
        BrowserUseAgent,
        "_create_llm",
        lambda self, model_type, model_id, agent_config, config_info: _OpenAIStyleLLM(),
    )

    asyncio.run(
        BrowserUseAgent()._run_task_async(
            task_info={"task_id": "t-raw", "task_text": "search", "url": "https://example.com"},
            task_workspace=tmp_path,
            timeout=600,
            flash_mode=False,
            agent_config={"MODEL_TYPE": "OPENAI", "MODEL_ID": "gpt-test"},
            session_context=BrowserSessionContext(backend_id="Chrome-Local", transport="local"),
        )
    )

    step_data = json.loads((tmp_path / "api_logs" / "step_001.json").read_text())
    assert len(step_data["llm_failures"]) == 1
    failure = step_data["llm_failures"][0]
    assert failure["raw_response"] == _RAW_LLM_TEXT
    assert failure["usage"]["total_tokens"] == 10
    assert failure["error"] == _PARSE_FAIL_MESSAGE
    assert not (tmp_path / "api_logs" / "llm_failures_unmatched.json").exists()
