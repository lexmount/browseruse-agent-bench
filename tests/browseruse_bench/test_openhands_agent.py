"""Tests for OpenHandsAgent: JSONL parsing, MCP config, and run_task."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.agents.openhands import OPENHANDS_BROWSER_RULES, OpenHandsAgent, _parse_jsonl
from browseruse_bench.browsers.types import BrowserSessionContext
from browseruse_bench.schemas import AgentResult


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj) + "\n"


TASK_INFO: dict[str, Any] = {
    "task_id": "t1",
    "task_text": "Go to example.com",
    "url": "https://example.com",
}

AGENT_CONFIG: dict[str, Any] = {
    "model_id": "gpt-test",
    "timeout": 10,
    "api_key": "test-key",
    "base_url": "https://proxy.example/v1",
}


class TestParseJsonl:
    def test_empty_input(self) -> None:
        answer, items, error = _parse_jsonl([])
        assert answer == ""
        assert items == []
        assert error is None

    def test_final_answer_and_browser_action_parsed(self) -> None:
        lines = [
            _line({
                "type": "action",
                "action": {
                    "tool": "browser_navigate",
                    "arguments": {"url": "https://example.com"},
                },
            }),
            _line({"type": "finish", "final_answer": "The price is $42"}),
        ]
        answer, items, error = _parse_jsonl(lines)
        assert answer == "The price is $42"
        assert items[0]["type"] == "mcp_tool_call"
        assert items[0]["tool"] == "browser_navigate"
        assert items[0]["arguments"] == {"url": "https://example.com"}
        assert error is None

    def test_openhands_action_event_and_agent_message_parsed(self) -> None:
        lines = [
            _line({
                "kind": "AgentErrorEvent",
                "source": "agent",
                "tool_name": "browser_navigate",
                "error": "first attempt used wrong schema",
            }),
            _line({
                "kind": "ActionEvent",
                "source": "agent",
                "tool_name": "browser_navigate",
                "action": {"kind": "MCPToolAction", "data": {"url": "https://example.com"}},
            }),
            _line({
                "kind": "MessageEvent",
                "source": "agent",
                "llm_message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "The price is $42"}],
                },
            }),
        ]
        answer, items, error = _parse_jsonl(lines)
        assert answer == "The price is $42"
        assert items == [{
            "type": "mcp_tool_call",
            "tool": "browser_navigate",
            "arguments": {"url": "https://example.com"},
            "status": "completed",
        }]
        assert error is None

    def test_command_action_normalized(self) -> None:
        _, items, _ = _parse_jsonl([_line({"type": "action", "action": {"command": "ls"}})])
        assert items[0]["type"] == "command_execution"
        assert items[0]["command"] == "ls"

    def test_error_event_captured(self) -> None:
        _, _, error = _parse_jsonl([_line({"type": "error", "error": {"message": "bad key"}})])
        assert error == "bad key"

    def test_conversation_error_event_captured(self) -> None:
        _, _, error = _parse_jsonl([
            _line({
                "source": "environment",
                "code": "NotFoundError",
                "detail": "model group not found",
                "kind": "ConversationErrorEvent",
            })
        ])
        assert error == "model group not found"

    def test_invalid_lines_skipped(self) -> None:
        answer, _, _ = _parse_jsonl(["not json\n", "{broken\n", _line({"type": "result", "result": "ok"})])
        assert answer == "ok"


class TestOpenHandsAgentRunTask:
    def _stream(self) -> list[str]:
        return [
            _line({
                "type": "action",
                "action": {
                    "tool": "browser_navigate",
                    "arguments": {"url": "https://example.com"},
                },
            }),
            _line({"type": "finish", "final_answer": "The price is $42"}),
        ]

    def test_successful_run_returns_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenHandsAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, self._stream(), None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert isinstance(result, AgentResult)
        assert result.answer == "The price is $42"
        assert result.env_status == "success"
        assert result.agent_done == "done"
        assert result.metrics.steps == 1
        assert result.action_history == ["Navigate to https://example.com"]
        assert "browser automation agent" not in OPENHANDS_BROWSER_RULES

    def test_workspace_config_and_env_written(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            captured_env.update(kw.get("env") or {})
            return 0, self._stream(), None

        agent = OpenHandsAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)

        mcp_config = json.loads(
            (tmp_path / ".openhands-home" / ".openhands" / "mcp.json").read_text()
        )
        server = mcp_config["mcpServers"]["playwright"]
        assert server["command"] == "npx"
        assert "@playwright/mcp@latest" in server["args"]
        assert captured_env["HOME"] == str(tmp_path / ".openhands-home")
        assert captured_env["OH_PERSISTENCE_DIR"] == str(
            tmp_path / ".openhands-home" / ".openhands"
        )
        assert captured_env["OPENHANDS_SUPPRESS_BANNER"] == "1"
        assert captured_env["UV_CACHE_DIR"]
        assert captured_env["LLM_MODEL"] == "gpt-test"
        assert captured_env["LLM_API_KEY"] == "test-key"
        assert captured_env["LLM_BASE_URL"] == "https://proxy.example/v1"

    def test_command_flags(self) -> None:
        cmd = OpenHandsAgent._build_command("do it")
        assert cmd[:3] == ["openhands", "--headless", "--json"]
        assert "--override-with-envs" in cmd
        assert cmd[cmd.index("--task") + 1] == "do it"

    def test_command_can_use_uv_tool_run_prefix(self) -> None:
        cmd = OpenHandsAgent._build_command(
            "do it", {"openhands_command": ["uv", "tool", "run", "openhands"]}
        )
        assert cmd[:6] == ["uv", "tool", "run", "openhands", "--headless", "--json"]

    def test_timeout_keeps_partial_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenHandsAgent()
        monkeypatch.setattr(
            agent,
            "_run_subprocess",
            lambda *a, **kw: (-1, [_line({"type": "finish", "final_answer": "4.5 stars"})], "Timeout after 10 seconds"),
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "success"
        assert result.agent_done == "timeout"
        assert "4.5 stars" in result.answer

    def test_executable_not_found_returns_error_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenHandsAgent()

        def _raise(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("openhands not found")

        monkeypatch.setattr(agent, "_run_subprocess", _raise)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "not found" in (result.error or "").lower()

    def test_managed_browser_opens_backend_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from browseruse_bench.agents import openhands as openhands_module

        opened: dict[str, str] = {}

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            opened["browser_id"] = browser_id
            yield BrowserSessionContext(
                backend_id=browser_id, transport="cdp", cdp_url="ws://cdp.example/1"
            )

        monkeypatch.setattr(openhands_module, "open_browser_session", fake_session)
        agent = OpenHandsAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, self._stream(), None))
        result = agent.run_task(TASK_INFO, {**AGENT_CONFIG, "browser_id": "lexmount"}, tmp_path)
        mcp_config = json.loads(
            (tmp_path / ".openhands-home" / ".openhands" / "mcp.json").read_text()
        )
        args = mcp_config["mcpServers"]["playwright"]["args"]
        assert opened["browser_id"] == "lexmount"
        assert "--cdp-endpoint" in args
        assert "ws://cdp.example/1" in args
        assert result.env_status == "success"

    def test_non_cdp_backend_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from browseruse_bench.agents import openhands as openhands_module

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            yield BrowserSessionContext(backend_id=browser_id, transport="cloud_native")

        monkeypatch.setattr(openhands_module, "open_browser_session", fake_session)
        agent = OpenHandsAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: pytest.fail("subprocess must not run")
        )
        result = agent.run_task(
            TASK_INFO, {**AGENT_CONFIG, "browser_id": "browser-use-cloud"}, tmp_path
        )
        assert result.env_status == "failed"
        assert "browser-use-cloud" in (result.error or "")
