"""Tests for browser-use agent session abstraction integration."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from browseruse_bench.agents import browser_use as browser_use_module
from browseruse_bench.agents.browser_use import BrowserUseAgent
from browseruse_bench.browsers.types import BrowserSessionContext


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
        lambda self, model_type, model_id, agent_config, config_info: object(),
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
        lambda self, model_type, model_id, agent_config, config_info: object(),
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
        lambda self, model_type, model_id, agent_config, config_info: object(),
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
