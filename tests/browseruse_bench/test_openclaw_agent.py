"""Tests for OpenClawAgent: stdout JSON parsing, session normalization, run_task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.agents.openclaw import (
    OpenClawAgent,
    _collect_media_screenshots,
    _normalize_session_items,
    _stdout_json,
)
from browseruse_bench.schemas import AgentResult

TASK_INFO: dict[str, Any] = {
    "task_id": "t1",
    "task_text": "Go to example.com",
    "url": "https://example.com",
}

AGENT_CONFIG: dict[str, Any] = {
    "model_id": "gpt-test",
    "api_key": "sk-test",
    "base_url": "https://llm.example/v1",
    "timeout": 10,
}


def _result_stdout(session_file: Path | None = None, answer: str = "The price is $42") -> list[str]:
    obj = {
        "payloads": [{"text": answer, "mediaUrl": None}],
        "meta": {
            "durationMs": 2000,
            "agentMeta": {
                "sessionId": "s1",
                "sessionFile": str(session_file) if session_file else None,
                "provider": "bench",
                "model": "gpt-test",
                "lastCallUsage": {"input": 100, "output": 20, "cacheRead": 40, "cacheWrite": 0, "total": 120},
            },
        },
    }
    text = json.dumps(obj, indent=2)
    return [line + "\n" for line in text.split("\n")]


def _write_session(path: Path) -> None:
    lines = [
        {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "go"}]}},
        {"type": "message", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "c1", "name": "browser",
             "arguments": {"action": "open", "url": "https://example.com"}},
        ]}},
        {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "browser",
            "content": [{"type": "text", "text": "opened"}]}},
        {"type": "message", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "c2", "name": "browser", "arguments": {"action": "screenshot"}},
        ]}},
        {"type": "message", "message": {"role": "toolResult", "toolCallId": "c2", "toolName": "browser",
            "content": [{"type": "text", "text": "MEDIA:" + str(path.parent / "shot.png")}]}},
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


class TestStdoutJson:
    def test_incomplete_json_returns_none(self) -> None:
        assert _stdout_json(["{\n", '  "payloads": [\n']) is None

    def test_complete_json_parsed(self) -> None:
        assert _stdout_json(_result_stdout())["payloads"][0]["text"] == "The price is $42"

    def test_non_json_returns_none(self) -> None:
        assert _stdout_json(["warning: something\n"]) is None
        assert _stdout_json([]) is None


class TestNormalizeSessionItems:
    def test_tool_calls_and_results_joined(self, tmp_path: Path) -> None:
        session = tmp_path / "session.jsonl"
        _write_session(session)
        items = _normalize_session_items(session)
        assert len(items) == 2
        assert items[0]["type"] == "mcp_tool_call"
        assert items[0]["tool"] == "browser_open"
        assert items[0]["arguments"]["url"] == "https://example.com"
        assert items[0]["status"] == "completed"
        assert items[0]["result"]["content"][0]["text"] == "opened"
        assert items[1]["tool"] == "browser_screenshot"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _normalize_session_items(tmp_path / "absent.jsonl") == []


class TestCollectMediaScreenshots:
    def test_media_paths_copied(self, tmp_path: Path) -> None:
        (tmp_path / "shot.png").write_bytes(b"png")
        items = [{
            "type": "mcp_tool_call", "tool": "browser_screenshot", "status": "completed",
            "result": {"content": [{"type": "text", "text": f"MEDIA:{tmp_path / 'shot.png'}"}]},
        }]
        trajectory = tmp_path / "trajectory"
        saved = _collect_media_screenshots(items, trajectory)
        assert saved == ["screenshot-1.png"]
        assert (trajectory / "screenshot-1.png").read_bytes() == b"png"

    def test_missing_media_file_skipped(self, tmp_path: Path) -> None:
        items = [{
            "type": "mcp_tool_call", "tool": "browser_screenshot", "status": "completed",
            "result": {"content": [{"type": "text", "text": "MEDIA:/nonexistent/x.png"}]},
        }]
        assert _collect_media_screenshots(items, tmp_path / "trajectory") == []


class TestOpenClawAgentRunTask:
    def test_successful_run_returns_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        session = tmp_path / "session.jsonl"
        _write_session(session)
        (tmp_path / "shot.png").write_bytes(b"png")
        agent = OpenClawAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (0, _result_stdout(session), None)
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert isinstance(result, AgentResult)
        assert result.answer == "The price is $42"
        assert result.env_status == "success"
        assert result.agent_done == "done"
        assert result.metrics.steps == 2
        assert result.metrics.usage is not None
        # lastCallUsage fallback; pi-ai `input` excludes cacheRead/cacheWrite,
        # so the normalized prompt count folds them back in (100 + 40 + 0).
        assert result.metrics.usage.total_prompt_tokens == 140
        assert result.metrics.usage.total_prompt_cached_tokens == 40
        assert result.screenshots == ["screenshot-1.png"]
        assert result.action_history[0] == "browser_open"

    def test_usage_aggregates_across_session_messages(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Session log carries per-call usage: aggregate it instead of trusting
        # lastCallUsage (which covers only the final LLM call).
        session = tmp_path / "session.jsonl"
        lines = [
            {"type": "message", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "step 1"}],
                "usage": {"input": 10, "output": 5, "cacheRead": 100, "cacheWrite": 30},
            }},
            {"type": "message", "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "step 2"}],
                "usage": {"input": 20, "output": 7, "cacheRead": 200, "cacheWrite": 0},
            }},
        ]
        session.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        agent = OpenClawAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (0, _result_stdout(session), None)
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        usage = result.metrics.usage
        assert usage is not None
        assert usage.total_prompt_tokens == 360  # 10+100+30 + 20+200+0
        assert usage.total_prompt_cached_tokens == 300
        assert usage.total_prompt_cache_creation_tokens == 30
        assert usage.total_completion_tokens == 12
        assert usage.entry_count == 2

    def test_state_config_written(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenClawAgent()
        captured_env: dict[str, str] = {}

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            captured_env.update(kw.get("env") or {})
            return 0, _result_stdout(), None

        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)

        state_dir = tmp_path / ".openclaw-state"
        assert captured_env["OPENCLAW_STATE_DIR"] == str(state_dir)
        cfg = json.loads((state_dir / "openclaw.json").read_text())
        provider = cfg["models"]["providers"]["bench"]
        assert provider["baseUrl"] == "https://llm.example/v1"
        assert provider["models"][0]["id"] == "gpt-test"
        assert cfg["agents"]["defaults"]["model"]["primary"] == "bench/gpt-test"
        assert cfg["agents"]["defaults"]["workspace"] == str(tmp_path / ".openclaw-workspace")
        assert cfg["agents"]["list"][0]["tools"]["allow"] == ["browser", "read"]
        assert cfg["models"]["providers"]["bench"]["timeoutSeconds"] == 300
        # The api key written for the run must be scrubbed from the artifact.
        assert provider["apiKey"] == "***"
        # Without this compat flag OpenClaw never sends stream_options
        # include_usage to custom providers, so token usage is all zeros.
        assert provider["models"][0]["compat"] == {"supportsUsageInStreaming": True}

    def test_cdp_url_written_as_attach_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import contextlib

        from browseruse_bench.agents import openclaw as openclaw_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            yield BrowserSessionContext(
                backend_id=browser_id, transport="cdp", cdp_url="wss://cdp.example/1"
            )

        monkeypatch.setattr(openclaw_module, "open_browser_session", fake_session)
        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, _result_stdout(), None))
        config = {**AGENT_CONFIG, "browser_id": "lexmount"}
        result = agent.run_task(TASK_INFO, config, tmp_path)

        cfg = json.loads((tmp_path / ".openclaw-state" / "openclaw.json").read_text())
        profile = cfg["browser"]["profiles"]["bench"]
        assert profile["cdpUrl"] == "wss://cdp.example/1"
        assert profile["attachOnly"] is True
        assert cfg["browser"]["defaultProfile"] == "bench"
        assert result.env_status == "success"

    def test_non_cdp_backend_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import contextlib

        from browseruse_bench.agents import openclaw as openclaw_module
        from browseruse_bench.browsers.types import BrowserSessionContext

        @contextlib.contextmanager
        def fake_session(browser_id: str, agent_name: str, agent_config: dict[str, Any]):
            yield BrowserSessionContext(backend_id=browser_id, transport="cloud_native")

        monkeypatch.setattr(openclaw_module, "open_browser_session", fake_session)
        agent = OpenClawAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess",
            lambda *a, **kw: pytest.fail("subprocess must not be launched"),
        )
        config = {**AGENT_CONFIG, "browser_id": "browser-use-cloud"}
        result = agent.run_task(TASK_INFO, config, tmp_path)
        assert result.env_status == "failed"
        assert "browser-use-cloud" in (result.error or "")

    def test_no_result_json_is_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenClawAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (0, ["FailoverError: 401\n"], None)
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "FailoverError: 401" in (result.error or "")

    def test_api_key_scrubbed_even_when_executable_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenClawAgent()

        def _raise(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("openclaw not found")

        monkeypatch.setattr(agent, "_run_subprocess", _raise)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        cfg = json.loads((tmp_path / ".openclaw-state" / "openclaw.json").read_text())
        assert cfg["models"]["providers"]["bench"]["apiKey"] == "***"

    def test_image_block_screenshot_preserved(self, tmp_path: Path) -> None:
        (tmp_path / "img.png").write_bytes(b"png")
        session = tmp_path / "session.jsonl"
        lines = [
            {"type": "message", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": "c1", "name": "browser", "arguments": {"action": "screenshot"}},
            ]}},
            {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "browser",
                "content": [{"type": "image", "path": str(tmp_path / "img.png")}]}},
        ]
        session.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        items = _normalize_session_items(session)
        saved = _collect_media_screenshots(items, tmp_path / "trajectory")
        assert saved == ["screenshot-1.png"]

    def test_timeout_maps_to_timeout_status(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenClawAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (-1, [], "Timeout after 10 seconds")
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "success"
        assert result.agent_done == "timeout"

    def test_executable_not_found_returns_error_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = OpenClawAgent()

        def _raise(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("openclaw not found")

        monkeypatch.setattr(agent, "_run_subprocess", _raise)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert "not found" in (result.error or "").lower()


class TestStopPredicate:
    def test_run_subprocess_stops_early_on_predicate(self, tmp_path: Path) -> None:
        # A process that prints JSON then sleeps forever must be terminated as
        # soon as the predicate matches, with a zero exit status.
        import time as time_module

        agent = OpenClawAgent()
        cmd = [
            "python3", "-u", "-c",
            "import time, sys; print('{\"done\": true}'); sys.stdout.flush(); time.sleep(60)",
        ]
        t0 = time_module.monotonic()
        returncode, lines, error = agent._run_subprocess(
            cmd,
            timeout=30,
            task_workspace=tmp_path,
            stop_predicate=lambda ls: _stdout_json(ls) is not None,
        )
        elapsed = time_module.monotonic() - t0
        assert returncode == 0
        assert error is None
        assert _stdout_json(lines) == {"done": True}
        assert elapsed < 15


class TestUsageFromTotalOnly:
    def test_total_only_last_call_usage_preserved(self) -> None:
        from browseruse_bench.agents.openclaw import OpenClawAgent

        result_obj = {
            "meta": {"agentMeta": {"lastCallUsage": {"total": 5000}}},
        }
        usage = OpenClawAgent._usage_from(result_obj)
        assert usage is not None
        assert usage.total_tokens == 5000
