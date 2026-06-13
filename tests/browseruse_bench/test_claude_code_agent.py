"""Tests for ClaudeCodeAgent: _parse_stream and run_task."""

from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.agents.claude_code import ClaudeCodeAgent, _parse_stream
from browseruse_bench.schemas import AgentResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _line(obj: dict[str, Any]) -> str:
    return json.dumps(obj) + "\n"


def _fake_b64() -> str:
    return base64.b64encode(b"\x89PNG fake image data").decode()


TASK_INFO: dict[str, Any] = {
    "task_id": "t1",
    "task_text": "Go to example.com",
    "url": "https://example.com",
}

AGENT_CONFIG: dict[str, Any] = {
    "model_id": "claude-test",
    "max_turns": 5,
    "timeout": 10,
}


# ---------------------------------------------------------------------------
# _parse_stream
# ---------------------------------------------------------------------------

class TestParseStream:
    def test_empty_input(self, tmp_path: Path) -> None:
        result_obj, msgs, screenshots, turns = _parse_stream([], tmp_path)
        assert result_obj == {}
        assert msgs == []
        assert screenshots == []
        assert turns == []

    def test_result_event_captured(self, tmp_path: Path) -> None:
        lines = [_line({"type": "result", "result": "42", "num_turns": 3, "duration_ms": 1500})]
        result_obj, _, _, _ = _parse_stream(lines, tmp_path)
        assert result_obj["result"] == "42"
        assert result_obj["num_turns"] == 3
        assert result_obj["duration_ms"] == 1500

    def test_assistant_event_creates_turn(self, tmp_path: Path) -> None:
        lines = [_line({
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "thinking..."}]},
        })]
        _, msgs, _, turns = _parse_stream(lines, tmp_path)
        assert len(msgs) == 1
        assert len(turns) == 1

    def test_screenshot_tool_id_tracked(self, tmp_path: Path) -> None:
        lines = [_line({
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "id": "tu_1", "name": "browser_take_screenshot", "input": {}},
            ]},
        })]
        _parse_stream(lines, tmp_path)
        # Indirectly verified: if we then send a matching tool_result, it should save a screenshot

    def test_screenshot_saved_from_user_event(self, tmp_path: Path) -> None:
        b64 = _fake_b64()
        lines = [
            _line({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "tu_1", "name": "browser_take_screenshot", "input": {}},
                ]},
            }),
            _line({
                "type": "user",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "content": [
                            {"type": "image", "source": {"data": b64}},
                        ],
                    }
                ]},
            }),
        ]
        _, _, screenshots, _ = _parse_stream(lines, tmp_path)
        assert len(screenshots) == 1
        assert screenshots[0] == "screenshot-1.png"
        assert (tmp_path / "screenshot-1.png").exists()

    def test_non_screenshot_tool_result_not_saved(self, tmp_path: Path) -> None:
        b64 = _fake_b64()
        lines = [
            _line({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "id": "tu_nav", "name": "browser_navigate", "input": {"url": "https://example.com"}},
                ]},
            }),
            _line({
                "type": "user",
                "message": {"content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_nav",   # not in screenshot_tool_ids
                        "content": [
                            {"type": "image", "source": {"data": b64}},
                        ],
                    }
                ]},
            }),
        ]
        _, _, screenshots, _ = _parse_stream(lines, tmp_path)
        assert screenshots == []

    def test_multiple_screenshots_numbered_sequentially(self, tmp_path: Path) -> None:
        b64 = _fake_b64()
        lines = [
            _line({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "tu_1", "name": "browser_take_screenshot", "input": {}},
            ]}}),
            _line({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_1",
                 "content": [{"type": "image", "source": {"data": b64}}]},
            ]}}),
            _line({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "tu_2", "name": "browser_take_screenshot", "input": {}},
            ]}}),
            _line({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_2",
                 "content": [{"type": "image", "source": {"data": b64}}]},
            ]}}),
        ]
        _, _, screenshots, _ = _parse_stream(lines, tmp_path)
        assert screenshots == ["screenshot-1.png", "screenshot-2.png"]

    def test_failed_screenshot_save_does_not_increment_counter(self, tmp_path: Path) -> None:
        # Invalid base64 → _save_screenshot returns early without appending;
        # counter must not advance so subsequent screenshots stay correctly numbered.
        lines = [
            _line({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "tu_bad", "name": "browser_take_screenshot", "input": {}},
                {"type": "tool_use", "id": "tu_ok",  "name": "browser_take_screenshot", "input": {}},
            ]}}),
            _line({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_bad",
                 "content": [{"type": "image", "source": {"data": "!!!invalid_base64!!!"}}]},
                {"type": "tool_result", "tool_use_id": "tu_ok",
                 "content": [{"type": "image", "source": {"data": _fake_b64()}}]},
            ]}}),
        ]
        _, _, screenshots, turns = _parse_stream(lines, tmp_path)
        # Only the valid screenshot must be saved, numbered from 1
        assert screenshots == ["screenshot-1.png"]
        assert (tmp_path / "screenshot-1.png").exists()
        assert not (tmp_path / "screenshot-2.png").exists()
        # turn must reference the correct file (not a shifted index)
        assert turns[0].get("screenshot") == "trajectory/screenshot-1.png"

    def test_invalid_json_lines_skipped(self, tmp_path: Path) -> None:
        lines = ["not json\n", "{broken\n", _line({"type": "result", "result": "ok"})]
        result_obj, _, _, _ = _parse_stream(lines, tmp_path)
        assert result_obj["result"] == "ok"

    def test_tool_results_attached_to_current_turn(self, tmp_path: Path) -> None:
        lines = [
            _line({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "tu_1", "name": "browser_navigate", "input": {}},
            ]}}),
            _line({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "navigated"},
            ]}}),
        ]
        _, _, _, turns = _parse_stream(lines, tmp_path)
        assert len(turns) == 1
        assert len(turns[0]["tool_results"]) == 1


# ---------------------------------------------------------------------------
# run_task — patching _run_subprocess so no real claude CLI needed
# ---------------------------------------------------------------------------

class TestClaudeCodeAgentRunTask:
    def _agent(self) -> ClaudeCodeAgent:
        return ClaudeCodeAgent()

    def _make_stream(self, extra_lines: list[str] | None = None) -> list[str]:
        lines = [
            _line({"type": "assistant", "message": {"content": [{"type": "text", "text": "I will do it"}]}}),
            _line({"type": "result", "result": "The price is $42", "num_turns": 1, "duration_ms": 2000}),
        ]
        if extra_lines:
            lines = extra_lines + lines
        return lines

    def test_successful_run_returns_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = self._agent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda *a, **kw: (0, self._make_stream(), None),
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert isinstance(result, AgentResult)
        assert result.answer == "The price is $42"
        assert result.env_status == "success"
        assert result.agent_done == "done"
        assert result.metrics.steps == 1
        assert result.metrics.end_to_end_ms == 2000

    def test_timeout_uses_last_assistant_text_as_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # On timeout: result event never arrives, only assistant turn exists
        stream = [
            _line({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "I found the rating is 4.5 stars"},
            ]}}),
        ]
        agent = self._agent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda *a, **kw: (-1, stream, "Timeout after 10 seconds"),
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "success"
        assert result.agent_done == "timeout"
        assert "4.5 stars" in result.answer
        assert result.metrics.steps == 1  # fallback: len(turns)
        assert result.metrics.end_to_end_ms >= 0  # fallback: monotonic elapsed

    def test_timeout_with_no_turns_gives_empty_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = self._agent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda *a, **kw: (-1, [], "Timeout after 10 seconds"),
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.agent_done == "timeout"
        assert result.answer == ""

    def test_executable_not_found_returns_error_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = self._agent()

        def _raise(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("claude not found")

        monkeypatch.setattr(agent, "_run_subprocess", _raise)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert isinstance(result, AgentResult)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "not found" in (result.error or "").lower()

    def test_screenshots_included_in_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        b64 = _fake_b64()
        stream = [
            _line({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "tu_1", "name": "browser_take_screenshot", "input": {}},
            ]}}),
            _line({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "tu_1",
                 "content": [{"type": "image", "source": {"data": b64}}]},
            ]}}),
            _line({"type": "result", "result": "done", "num_turns": 1, "duration_ms": 100}),
        ]
        agent = self._agent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, stream, None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert len(result.screenshots) == 1
        assert result.screenshots[0] == "screenshot-1.png"

    def test_model_id_used_from_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured_cmd: list[list[str]] = []

        def _fake_run(cmd: list[str], **kw: Any) -> tuple:
            captured_cmd.append(cmd)
            return 0, self._make_stream(), None

        agent = self._agent()
        monkeypatch.setattr(agent, "_run_subprocess", _fake_run)
        agent.run_task({**TASK_INFO}, {**AGENT_CONFIG, "model_id": "claude-opus-4"}, tmp_path)
        cmd = captured_cmd[0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4"

    def test_is_error_result_maps_to_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        stream = [_line({"type": "result", "result": "API error occurred", "is_error": True, "num_turns": 0, "duration_ms": 0})]
        agent = self._agent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, stream, None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"


# ---------------------------------------------------------------------------
# Command-construction unit tests (no claude CLI needed)
# ---------------------------------------------------------------------------

class TestClaudeCodeAgentCmd:
    """Verify subprocess command flags without running the claude CLI."""

    def _capture_cmd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: dict[str, Any] | None = None
    ) -> list[str]:
        captured: list[list[str]] = []
        agent = ClaudeCodeAgent()
        stream = [_line({"type": "result", "result": "ok", "num_turns": 1, "duration_ms": 100})]

        def _fake(cmd: list[str], **kw: Any) -> tuple:
            captured.append(cmd)
            return 0, stream, None

        monkeypatch.setattr(agent, "_run_subprocess", _fake)
        agent.run_task(TASK_INFO, config or AGENT_CONFIG, tmp_path)
        return captured[0]

    def test_bare_flag_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cmd = self._capture_cmd(monkeypatch, tmp_path)
        assert "--bare" in cmd

    def test_tools_empty_string_absent(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # --tools "" was dropped; verify it does not appear in the command.
        cmd = self._capture_cmd(monkeypatch, tmp_path)
        assert "--tools" not in cmd

    def test_allowed_tools_set(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cmd = self._capture_cmd(monkeypatch, tmp_path)
        assert "--allowedTools" in cmd
        allowed = cmd[cmd.index("--allowedTools") + 1]
        assert "mcp__playwright" in allowed

    def test_strict_mcp_config_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cmd = self._capture_cmd(monkeypatch, tmp_path)
        assert "--strict-mcp-config" in cmd

    def test_playwright_mcp_timeout_action_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        cmd = self._capture_cmd(monkeypatch, tmp_path)
        mcp_cfg_json = cmd[cmd.index("--mcp-config") + 1]
        mcp_cfg = json.loads(mcp_cfg_json)
        args = mcp_cfg["mcpServers"]["playwright"]["args"]
        assert "--timeout-action" in args
        idx = args.index("--timeout-action")
        assert int(args[idx + 1]) > 5000  # must exceed the 5000ms default

    def test_allowed_tools_default_is_segment_wildcard(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The claude CLI rejects a trailing wildcard ("mcp__playwright*");
        # the default must be the accepted segment form.
        cmd = self._capture_cmd(monkeypatch, tmp_path)
        assert cmd[cmd.index("--allowedTools") + 1] == "mcp__playwright__*"

    def test_self_launch_has_no_cdp_endpoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Default config sets no browser_id -> self-launch, no --cdp-endpoint.
        cmd = self._capture_cmd(monkeypatch, tmp_path)
        mcp_cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])
        assert "--cdp-endpoint" not in mcp_cfg["mcpServers"]["playwright"]["args"]


# ---------------------------------------------------------------------------
# Browser backend wiring (open_browser_session) and root sandbox env
# ---------------------------------------------------------------------------

class TestClaudeCodeAgentBackend:
    def _stream(self) -> list[str]:
        return [_line({"type": "result", "result": "ok", "num_turns": 1, "duration_ms": 100})]

    def test_managed_backend_attaches_cdp_endpoint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import contextlib

        from browseruse_bench.agents import claude_code as cc_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        opened: dict[str, str] = {}

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            opened["browser_id"] = browser_id
            yield BrowserSessionContext(
                backend_id=browser_id, transport="cdp", cdp_url="ws://cdp.example/1"
            )

        monkeypatch.setattr(cc_module, "open_browser_session", fake_session)
        captured: list[list[str]] = []
        agent = ClaudeCodeAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda cmd, **kw: (captured.append(cmd), (0, self._stream(), None))[1],
        )
        result = agent.run_task(TASK_INFO, {**AGENT_CONFIG, "browser_id": "lexmount"}, tmp_path)
        mcp_cfg = json.loads(captured[0][captured[0].index("--mcp-config") + 1])
        args = mcp_cfg["mcpServers"]["playwright"]["args"]
        assert opened["browser_id"] == "lexmount"
        assert "--cdp-endpoint" in args
        assert args[args.index("--cdp-endpoint") + 1] == "ws://cdp.example/1"
        assert result.env_status == "success"

    def test_non_cdp_backend_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import contextlib

        from browseruse_bench.agents import claude_code as cc_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            yield BrowserSessionContext(backend_id=browser_id, transport="cloud_native")

        monkeypatch.setattr(cc_module, "open_browser_session", fake_session)
        agent = ClaudeCodeAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda *a, **kw: pytest.fail("subprocess must not be launched"),
        )
        result = agent.run_task(
            TASK_INFO, {**AGENT_CONFIG, "browser_id": "browser-use-cloud"}, tmp_path
        )
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "browser-use-cloud" in (result.error or "")
        assert "cloud_native" in (result.error or "")

    def test_is_sandbox_env_set_for_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # claude refuses --dangerously-skip-permissions under root without
        # IS_SANDBOX=1; the agent must inject it into the subprocess env.
        captured_env: dict[str, str] = {}
        agent = ClaudeCodeAgent()

        def _fake(cmd: list[str], **kw: Any) -> tuple:
            captured_env.update(kw.get("env") or {})
            return 0, self._stream(), None

        monkeypatch.setattr(agent, "_run_subprocess", _fake)
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert captured_env.get("IS_SANDBOX") == "1"


# ---------------------------------------------------------------------------
# Integration smoke test — requires claude CLI + ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestClaudeCodeAgentSmoke:
    """
    Smoke test: runs the real claude CLI to confirm Playwright MCP tools are
    accessible when --bare is active and --tools "" is absent.

    Skipped automatically when the claude CLI is not installed or
    ANTHROPIC_API_KEY is not set. Run explicitly with:
        pytest -m integration
    """

    def test_playwright_tools_accessible(self, tmp_path: Path) -> None:
        if not shutil.which("claude"):
            pytest.skip("claude CLI not installed")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set")

        agent = ClaudeCodeAgent()
        result = agent.run_task(
            task_info={
                "task_id": "smoke",
                "task_text": "Navigate to https://example.com and report the page title.",
                "url": "",
            },
            agent_config={
                "model_id": "claude-haiku-4-5-20251001",
                "max_turns": 5,
                "timeout": 60,
            },
            task_workspace=tmp_path,
        )

        # Must not fail with a hard error (tool-blocking would surface here)
        assert result.agent_done != "error", (
            f"agent exited with error — Playwright MCP tools may be blocked: {result.error}"
        )
        # At least one Playwright action must have been attempted
        assert len(result.action_history) >= 1, (
            "No actions recorded — Playwright MCP tools appear inaccessible"
        )
