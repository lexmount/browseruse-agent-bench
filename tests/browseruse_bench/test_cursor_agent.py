"""Tests for CursorAgent: _parse_stream, tool-call normalization, and run_task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.agents.cursor import CursorAgent, _parse_stream
from browseruse_bench.schemas import AgentResult


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj) + "\n"


def _assistant(text: str) -> str:
    return _line({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    })


def _mcp_started(call_id: str, tool: str, arguments: dict[str, Any]) -> str:
    return _line({
        "type": "tool_call",
        "subtype": "started",
        "call_id": call_id,
        "tool_call": {"mcpToolCall": {"args": {
            "name": f"playwright-{tool}",
            "args": arguments,
            "toolName": tool,
            "providerIdentifier": "playwright",
        }}},
    })


def _mcp_completed(call_id: str, text: str | None = None, rejected: str | None = None) -> str:
    if rejected is not None:
        result: dict[str, Any] = {"rejected": {"reason": rejected, "isReadonly": False}}
    else:
        result = {"success": {"content": [{"text": {"text": text or ""}}], "isError": False}}
    return _line({
        "type": "tool_call",
        "subtype": "completed",
        "call_id": call_id,
        "tool_call": {"mcpToolCall": {"result": result}},
    })


def _result(text: str, is_error: bool = False, subtype: str = "success") -> str:
    return _line({
        "type": "result",
        "subtype": subtype,
        "duration_ms": 1500,
        "is_error": is_error,
        "result": text,
        "usage": {"inputTokens": 100, "outputTokens": 20, "cacheReadTokens": 40, "cacheWriteTokens": 0},
    })


TASK_INFO: dict[str, Any] = {
    "task_id": "t1",
    "task_text": "Go to example.com",
    "url": "https://example.com",
}

AGENT_CONFIG: dict[str, Any] = {
    "model_id": "gpt-test",
    "timeout": 10,
}


class TestParseStream:
    def test_empty_input(self) -> None:
        answer, items, usage, result_obj = _parse_stream([])
        assert answer == ""
        assert items == []
        assert usage == {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
        assert result_obj == {}

    def test_last_assistant_message_wins(self) -> None:
        lines = [_assistant("first"), _assistant("final answer"), _result("first final answer")]
        answer, _, usage, result_obj = _parse_stream(lines)
        assert answer == "final answer"
        assert usage == {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}
        assert result_obj["is_error"] is False

    def test_tool_call_started_completed_joined_by_call_id(self) -> None:
        lines = [
            _mcp_started("c1", "browser_navigate", {"url": "https://example.com"}),
            _mcp_completed("c1", text="Page Title: Example Domain"),
        ]
        _, items, _, _ = _parse_stream(lines)
        assert len(items) == 1
        item = items[0]
        assert item["type"] == "mcp_tool_call"
        assert item["tool"] == "browser_navigate"
        assert item["arguments"] == {"url": "https://example.com"}
        assert item["status"] == "completed"
        assert item["result"]["content"][0]["text"] == "Page Title: Example Domain"

    def test_rejected_tool_call_marked_failed(self) -> None:
        lines = [
            _mcp_started("c1", "browser_navigate", {"url": "https://example.com"}),
            _mcp_completed("c1", rejected="User rejected MCP: playwright-browser_navigate"),
        ]
        _, items, _, _ = _parse_stream(lines)
        assert items[0]["status"] == "failed"
        assert "rejected" in items[0]["error"]["message"]

    def test_shell_tool_call_normalized(self) -> None:
        lines = [
            _line({
                "type": "tool_call", "subtype": "started", "call_id": "s1",
                "tool_call": {"shellToolCall": {"args": {"command": "whoami"}}},
            }),
            _line({
                "type": "tool_call", "subtype": "completed", "call_id": "s1",
                "tool_call": {"shellToolCall": {"result": {"permissionDenied": {"command": "whoami"}}}},
            }),
        ]
        _, items, _, _ = _parse_stream(lines)
        assert items[0]["type"] == "command_execution"
        assert items[0]["command"] == "whoami"

    def test_invalid_lines_skipped(self) -> None:
        lines = ["not json\n", "{broken\n", _assistant("ok")]
        answer, _, _, _ = _parse_stream(lines)
        assert answer == "ok"


class TestCursorAgentRunTask:
    def _stream(self) -> list[str]:
        return [
            _mcp_started("c1", "browser_navigate", {"url": "https://example.com"}),
            _mcp_completed("c1", text="Page Title: Example Domain"),
            _assistant("The price is $42"),
            _result("The price is $42"),
        ]

    def test_successful_run_returns_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = CursorAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, self._stream(), None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert isinstance(result, AgentResult)
        assert result.answer == "The price is $42"
        assert result.env_status == "success"
        assert result.agent_done == "done"
        assert result.metrics.steps == 1
        assert result.metrics.usage is not None
        assert result.metrics.usage.total_prompt_tokens == 100
        assert result.metrics.usage.total_prompt_cached_tokens == 40
        assert result.action_history == ["Navigate to https://example.com"]

    def test_workspace_config_written(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = CursorAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, self._stream(), None))
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)

        mcp_config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        assert mcp_config["mcpServers"]["playwright"]["command"] == "npx"
        assert "@playwright/mcp@latest" in mcp_config["mcpServers"]["playwright"]["args"]

        permissions = json.loads((tmp_path / ".cursor" / "cli.json").read_text())
        assert permissions["permissions"]["allow"] == ["Mcp(playwright)"]
        assert permissions["permissions"]["deny"] == [
            "Shell(**)", "WebFetch(**)", "Read(**)", "Write(**)",
        ]

    def test_command_flags(self, tmp_path: Path) -> None:
        cmd = CursorAgent._build_command("do it", "gpt-test", tmp_path)
        assert cmd[:3] == ["cursor-agent", "-p", "do it"]
        assert "--force" in cmd
        assert "--trust" in cmd
        assert "--approve-mcps" in cmd
        i = cmd.index("--output-format")
        assert cmd[i:i + 2] == ["--output-format", "stream-json"]

    def test_error_result_marks_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stream = [_assistant("partial narration"), _result("boom", is_error=True, subtype="error")]
        agent = CursorAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, stream, None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert result.answer == "partial narration"

    def test_timeout_keeps_partial_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stream = [_assistant("rating is 4.5 stars")]
        agent = CursorAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (-1, stream, "Timeout after 10 seconds")
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "success"
        assert result.agent_done == "timeout"
        assert "4.5 stars" in result.answer

    def test_missing_result_event_is_error_despite_partial_text(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A crash after a preamble (e.g. MCP startup failure) must not be
        # recorded as success just because some assistant text was emitted.
        stream = [_assistant("I'll navigate to the page now")]
        agent = CursorAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (1, stream, None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "terminal result" in (result.error or "")

    def test_isolates_user_config_dir_by_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            captured_env.update(kw.get("env") or {})
            return 0, self._stream(), None

        agent = CursorAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert captured_env["CURSOR_CONFIG_DIR"] == str(tmp_path / ".cursor-config")

        captured_env.clear()
        agent.run_task(TASK_INFO, {**AGENT_CONFIG, "isolate_user_config": False}, tmp_path)
        assert str(tmp_path / ".cursor-config") != captured_env.get("CURSOR_CONFIG_DIR", "")

    def test_executable_not_found_returns_error_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = CursorAgent()

        def _raise(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("cursor-agent not found")

        monkeypatch.setattr(agent, "_run_subprocess", _raise)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "not found" in (result.error or "").lower()

    def test_managed_browser_opens_backend_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import contextlib

        from browseruse_bench.agents import cursor as cursor_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        opened: dict[str, str] = {}

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            opened["browser_id"] = browser_id
            yield BrowserSessionContext(
                backend_id=browser_id, transport="cdp", cdp_url="ws://cdp.example/1"
            )

        monkeypatch.setattr(cursor_module, "open_browser_session", fake_session)
        agent = CursorAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, self._stream(), None))
        config = {**AGENT_CONFIG, "browser_id": "lexmount"}
        result = agent.run_task(TASK_INFO, config, tmp_path)

        mcp_config = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
        args = mcp_config["mcpServers"]["playwright"]["args"]
        assert opened["browser_id"] == "lexmount"
        assert "--cdp-endpoint" in args
        assert "ws://cdp.example/1" in args
        assert result.env_status == "success"

    def test_non_cdp_backend_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import contextlib

        from browseruse_bench.agents import cursor as cursor_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            yield BrowserSessionContext(backend_id=browser_id, transport="cloud_native")

        monkeypatch.setattr(cursor_module, "open_browser_session", fake_session)
        agent = CursorAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda *a, **kw: pytest.fail("subprocess must not be launched"),
        )
        config = {**AGENT_CONFIG, "browser_id": "browser-use-cloud"}
        result = agent.run_task(TASK_INFO, config, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "browser-use-cloud" in (result.error or "")
