"""Tests for CodexAgent: _parse_events, screenshot collection, and run_task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.agents.codex import (
    CodexAgent,
    _collect_screenshots,
    _extract_actions,
    _parse_events,
)
from browseruse_bench.schemas import AgentResult


def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj) + "\n"


def _mcp_item(tool: str, arguments: dict[str, Any] | None = None, status: str = "completed") -> dict[str, Any]:
    return {
        "id": "item_x",
        "type": "mcp_tool_call",
        "server": "playwright",
        "tool": tool,
        "arguments": arguments or {},
        "result": None,
        "error": None,
        "status": status,
    }


TASK_INFO: dict[str, Any] = {
    "task_id": "t1",
    "task_text": "Go to example.com",
    "url": "https://example.com",
}

AGENT_CONFIG: dict[str, Any] = {
    "model_id": "gpt-test",
    "timeout": 10,
}


class TestParseEvents:
    def test_empty_input(self) -> None:
        answer, items, usage, error = _parse_events([])
        assert answer == ""
        assert items == []
        assert usage == {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
        assert error is None

    def test_last_agent_message_wins(self) -> None:
        lines = [
            _line({"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}),
            _line({"type": "item.completed", "item": {"type": "agent_message", "text": "final answer"}}),
        ]
        answer, items, _, _ = _parse_events(lines)
        assert answer == "final answer"
        assert len(items) == 2

    def test_usage_accumulated_across_turns(self) -> None:
        lines = [
            _line({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10}}),
            _line({"type": "turn.completed", "usage": {"input_tokens": 50, "cached_input_tokens": 0, "output_tokens": 5}}),
        ]
        _, _, usage, _ = _parse_events(lines)
        assert usage == {"input_tokens": 150, "cached_input_tokens": 40, "output_tokens": 15}

    def test_error_events_captured(self) -> None:
        lines = [
            _line({"type": "error", "message": "model not supported"}),
            _line({"type": "turn.failed", "error": {"message": "turn exploded"}}),
        ]
        _, _, _, error = _parse_events(lines)
        assert error == "turn exploded"

    def test_transient_error_cleared_by_completed_turn(self) -> None:
        lines = [
            _line({"type": "error", "message": "stream disconnected, retrying"}),
            _line({"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}),
        ]
        _, _, _, error = _parse_events(lines)
        assert error is None

    def test_invalid_and_non_json_lines_skipped(self) -> None:
        lines = ["not json\n", "{broken\n", _line({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}})]
        answer, _, _, _ = _parse_events(lines)
        assert answer == "ok"


class TestExtractActions:
    def test_mcp_tool_calls_mapped(self) -> None:
        items = [
            _mcp_item("browser_navigate", {"url": "https://example.com"}),
            _mcp_item("browser_click", {"element": "Login button"}),
            _mcp_item("browser_take_screenshot"),
            {"id": "i", "type": "command_execution", "command": "ls -la", "status": "completed"},
            {"id": "j", "type": "agent_message", "text": "not an action"},
        ]
        actions = _extract_actions(items)
        assert actions == [
            "Navigate to https://example.com",
            "Click: Login button",
            "Take screenshot",
            "Shell: ls -la",
        ]


class TestCollectScreenshots:
    def test_copies_images_in_mtime_order(self, tmp_path: Path) -> None:
        mcp_dir = tmp_path / ".playwright-mcp"
        mcp_dir.mkdir()
        (mcp_dir / "b.png").write_bytes(b"png-b")
        (mcp_dir / "a.png").write_bytes(b"png-a")
        (mcp_dir / "notes.yml").write_text("not an image")
        import os

        os.utime(mcp_dir / "b.png", (1, 1))
        os.utime(mcp_dir / "a.png", (2, 2))

        trajectory = tmp_path / "trajectory"
        saved = _collect_screenshots(tmp_path, trajectory)

        assert saved == ["screenshot-1.png", "screenshot-2.png"]
        assert (trajectory / "screenshot-1.png").read_bytes() == b"png-b"
        assert (trajectory / "screenshot-2.png").read_bytes() == b"png-a"

    def test_workspace_root_images_also_collected(self, tmp_path: Path) -> None:
        # The model may pass an explicit filename, landing the image in the
        # workspace root instead of .playwright-mcp/.
        (tmp_path / "named-shot.png").write_bytes(b"png-root")
        saved = _collect_screenshots(tmp_path, tmp_path / "trajectory")
        assert saved == ["screenshot-1.png"]

    def test_no_images_returns_empty(self, tmp_path: Path) -> None:
        assert _collect_screenshots(tmp_path, tmp_path / "trajectory") == []


class TestCodexAgentRunTask:
    def _stream(self) -> list[str]:
        return [
            _line({"type": "thread.started", "thread_id": "th_1"}),
            _line({"type": "turn.started"}),
            _line({"type": "item.completed", "item": _mcp_item("browser_navigate", {"url": "https://example.com"})}),
            _line({"type": "item.completed", "item": {"type": "agent_message", "text": "The price is $42"}}),
            _line({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 20}}),
        ]

    def test_successful_run_returns_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = CodexAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, self._stream(), None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert isinstance(result, AgentResult)
        assert result.answer == "The price is $42"
        assert result.env_status == "success"
        assert result.agent_done == "done"
        assert result.metrics.steps == 1
        assert result.metrics.usage is not None
        assert result.metrics.usage.total_prompt_tokens == 100
        assert result.metrics.usage.total_completion_tokens == 20
        assert result.action_history == ["Navigate to https://example.com"]

    def test_timeout_keeps_partial_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stream = [
            _line({"type": "item.completed", "item": {"type": "agent_message", "text": "rating is 4.5 stars"}}),
        ]
        agent = CodexAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (-1, stream, "Timeout after 10 seconds")
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "success"
        assert result.agent_done == "timeout"
        assert "4.5 stars" in result.answer

    def test_turn_failed_without_answer_is_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stream = [
            _line({"type": "turn.failed", "error": {"message": "model not supported"}}),
        ]
        agent = CodexAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, stream, None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "model not supported" in (result.error or "")

    def test_turn_failed_after_partial_message_is_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A partial narration before the failure must not mask the error.
        stream = [
            _line({"type": "item.completed", "item": {"type": "agent_message", "text": "I will navigate now"}}),
            _line({"type": "turn.failed", "error": {"message": "tool crashed"}}),
        ]
        agent = CodexAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, stream, None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "tool crashed" in (result.error or "")
        assert result.answer == "I will navigate now"

    def test_answer_falls_back_to_last_message_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / "last_message.txt").write_text("answer from file", encoding="utf-8")
        agent = CodexAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, [], None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.answer == "answer from file"
        assert result.env_status == "success"

    def test_executable_not_found_returns_error_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = CodexAgent()

        def _raise(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("codex not found")

        monkeypatch.setattr(agent, "_run_subprocess", _raise)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "not found" in (result.error or "").lower()

    def test_command_includes_mcp_overrides(self, tmp_path: Path) -> None:
        cmd = CodexAgent._build_command(
            full_prompt="do it",
            model="gpt-test",
            agent_config={},
            task_workspace=tmp_path,
            last_message_file=tmp_path / "last_message.txt",
        )
        joined = " ".join(cmd)
        assert cmd[:3] == ["codex", "exec", "do it"]
        assert "--json" in cmd
        assert "--ignore-user-config" in cmd
        assert 'mcp_servers.playwright.default_tools_approval_mode="approve"' in joined
        assert 'mcp_servers.playwright.command="npx"' in joined
        assert "@playwright/mcp@latest" in joined
        assert "--cdp-endpoint" not in joined

    def test_command_includes_cdp_endpoint_when_session_provided(self, tmp_path: Path) -> None:
        cmd = CodexAgent._build_command(
            full_prompt="do it",
            model="gpt-test",
            agent_config={},
            task_workspace=tmp_path,
            last_message_file=tmp_path / "last_message.txt",
            cdp_url="ws://127.0.0.1:9222/devtools/browser/abc",
        )
        joined = " ".join(cmd)
        assert "--cdp-endpoint" in joined
        assert "ws://127.0.0.1:9222/devtools/browser/abc" in joined

    def test_managed_browser_opens_backend_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import contextlib

        from browseruse_bench.agents import codex as codex_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        opened: dict[str, str] = {}

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            opened["browser_id"] = browser_id
            yield BrowserSessionContext(
                backend_id=browser_id, transport="cdp", cdp_url="ws://cdp.example/1"
            )

        monkeypatch.setattr(codex_module, "open_browser_session", fake_session)
        captured_cmd: list[str] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            captured_cmd.extend(cmd)
            return 0, self._stream(), None

        agent = CodexAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        config = {**AGENT_CONFIG, "browser_id": "lexmount"}
        result = agent.run_task(TASK_INFO, config, tmp_path)
        joined = " ".join(captured_cmd)
        assert opened["browser_id"] == "lexmount"
        assert "--cdp-endpoint" in joined
        assert "ws://cdp.example/1" in joined
        assert result.env_status == "success"

    def test_non_cdp_backend_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Cloud-native sessions expose no CDP endpoint; the run must fail
        # loudly instead of silently self-launching a local browser.
        import contextlib

        from browseruse_bench.agents import codex as codex_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            yield BrowserSessionContext(backend_id=browser_id, transport="cloud_native")

        monkeypatch.setattr(codex_module, "open_browser_session", fake_session)
        agent = CodexAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda *a, **kw: pytest.fail("subprocess must not be launched"),
        )
        config = {**AGENT_CONFIG, "browser_id": "browser-use-cloud"}
        result = agent.run_task(TASK_INFO, config, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "browser-use-cloud" in (result.error or "")
        assert "cloud_native" in (result.error or "")
