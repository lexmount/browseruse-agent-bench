"""Tests for OpenClawAgent: stdout JSON parsing, session normalization, run_task."""

from __future__ import annotations

import base64
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


class TestInlineBase64Screenshots:
    @staticmethod
    def _session_with_inline_image(path: Path, data: str, mime: str = "image/png") -> None:
        lines = [
            {"type": "message", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": "c1", "name": "browser", "arguments": {"action": "screenshot"}},
            ]}},
            {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1", "toolName": "browser",
                "content": [{"type": "image", "data": data, "mimeType": mime}]}},
        ]
        path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    def test_inline_base64_image_saved_to_trajectory(self, tmp_path: Path) -> None:
        # OpenClaw returns screenshots as inline base64 blocks (no path key);
        # they must be decoded into trajectory/ like path-based media.
        payload = b"png-bytes"
        session = tmp_path / "session.jsonl"
        self._session_with_inline_image(session, base64.b64encode(payload).decode())
        items = _normalize_session_items(session)
        saved = _collect_media_screenshots(items, tmp_path / "trajectory")
        assert saved == ["screenshot-1.png"]
        assert (tmp_path / "trajectory" / "screenshot-1.png").read_bytes() == payload
        # The raw base64 must not linger on items (it would bloat api_logs).
        assert all("inline_media" not in item for item in items)

    def test_invalid_base64_skipped(self, tmp_path: Path) -> None:
        session = tmp_path / "session.jsonl"
        self._session_with_inline_image(session, "not-valid-base64!!!")
        items = _normalize_session_items(session)
        assert _collect_media_screenshots(items, tmp_path / "trajectory") == []

    def test_non_image_mime_skipped(self, tmp_path: Path) -> None:
        session = tmp_path / "session.jsonl"
        self._session_with_inline_image(
            session, base64.b64encode(b"pdf").decode(), mime="application/pdf"
        )
        items = _normalize_session_items(session)
        assert _collect_media_screenshots(items, tmp_path / "trajectory") == []

    def test_run_task_reports_inline_screenshots(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        session = tmp_path / "session.jsonl"
        self._session_with_inline_image(session, base64.b64encode(b"shot").decode())
        agent = OpenClawAgent()
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (0, _result_stdout(session), None)
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.screenshots == ["screenshot-1.png"]
        assert (tmp_path / "trajectory" / "screenshot-1.png").read_bytes() == b"shot"


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
        # Each task process must get its own gateway port: concurrent tasks
        # sharing the default 18789 attach to each other's gateway and fail
        # browser auth ("gateway node.list requires credentials").
        gateway_port = int(captured_env["OPENCLAW_GATEWAY_PORT"])
        assert 1024 <= gateway_port <= 65535
        assert gateway_port != 18789
        # A configured gateway token makes OpenClaw treat the gateway as
        # external and skip its in-process browser service; must NOT be set.
        assert "OPENCLAW_GATEWAY_TOKEN" not in captured_env
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

    def test_media_understanding_disabled_in_state_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Screenshots would otherwise trigger OpenClaw's image understanding,
        # which auto-detects an image model and burns a failing LLM call per
        # image; the bench model gets no vision either way, so turn it off.
        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, _result_stdout(), None))
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        cfg = json.loads((tmp_path / ".openclaw-state" / "openclaw.json").read_text())
        assert cfg["tools"]["media"]["image"]["enabled"] is False

    def test_provider_autodetect_vars_scrubbed_from_subprocess_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # OpenClaw auto-detects providers from *_API_KEY / ANTHROPIC_* env vars
        # and routes media understanding through them; the bench provider gets
        # its credentials via the written openclaw.json, so none may leak.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leak")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://gateway.local")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-leak")
        monkeypatch.setenv("ANTHROPIC_OAUTH_TOKEN", "oauth-leak")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-leak")
        monkeypatch.setenv("BENCH_HARMLESS_VAR", "keep-me")
        agent = OpenClawAgent()
        captured_env: dict[str, str] = {}

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            captured_env.update(kw.get("env") or {})
            return 0, _result_stdout(), None

        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)

        assert "ANTHROPIC_API_KEY" not in captured_env
        assert "ANTHROPIC_BASE_URL" not in captured_env
        assert "ANTHROPIC_AUTH_TOKEN" not in captured_env
        assert "ANTHROPIC_OAUTH_TOKEN" not in captured_env
        assert "OPENAI_API_KEY" not in captured_env
        assert captured_env["BENCH_HARMLESS_VAR"] == "keep-me"
        assert captured_env["OPENCLAW_STATE_DIR"] == str(tmp_path / ".openclaw-state")

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
        # OpenClaw auto-injects built-in `user`/`openclaw` profiles (operator's
        # local Chrome) unless the config defines those names; models sometimes
        # pass them explicitly and escape the bench browser. Pin both to the
        # bench CDP endpoint.
        for alias in ("user", "openclaw"):
            assert cfg["browser"]["profiles"][alias]["cdpUrl"] == "wss://cdp.example/1"
            assert cfg["browser"]["profiles"][alias]["attachOnly"] is True
        # Local fake-IP proxy clients resolve proxied domains to the RFC 2544
        # benchmark range (198.18.0.0/15); OpenClaw's local SSRF preflight then
        # blocks navigation even though navigation happens in the remote CDP
        # browser. Only the CDP path disables it (see the non-CDP test).
        assert cfg["browser"]["ssrfPolicy"]["dangerouslyAllowPrivateNetwork"] is True
        assert result.env_status == "success"

    def test_gateway_browser_node_dispatch_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Default gateway.nodes.browser.mode="auto" consults gateway node.list
        # before the in-process service; without gateway credentials every
        # browser call fails ("gateway node.list requires credentials").
        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, _result_stdout(), None))
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        cfg = json.loads((tmp_path / ".openclaw-state" / "openclaw.json").read_text())
        assert cfg["gateway"]["nodes"]["browser"]["mode"] == "off"

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


_OUTAGE_TEXT = (
    "browser failed: gateway node.list requires credentials before opening a websocket"
)


def _write_blocked_session(path: Path) -> None:
    lines = [
        {"type": "message", "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "c1", "name": "browser",
             "arguments": {"action": "open", "url": "https://example.com"}},
        ]}},
        {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1",
            "toolName": "browser", "content": [{"type": "text", "text": _OUTAGE_TEXT}]}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


class TestBrowserOutageRetry:
    """A run whose every browser call lost the service-startup race is a false
    success: detect it, retry once on a fresh session, else mark failed."""

    def test_outage_retried_once_to_success(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        blocked = tmp_path / "blocked.jsonl"
        _write_blocked_session(blocked)
        good = tmp_path / "good.jsonl"
        _write_session(good)
        calls: list[int] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            calls.append(1)
            if len(calls) == 1:
                return 0, _result_stdout(blocked, answer="[blocked] browser unavailable"), None
            return 0, _result_stdout(good), None

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert len(calls) == 2
        assert result.env_status == "success"
        assert result.answer == "The price is $42"

    def test_outage_on_both_attempts_marks_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        blocked = tmp_path / "blocked.jsonl"
        _write_blocked_session(blocked)
        calls: list[int] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            calls.append(1)
            return 0, _result_stdout(blocked, answer="[blocked] browser unavailable"), None

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert len(calls) == 2
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "browser" in (result.error or "").lower()

    def test_successful_browser_calls_are_not_outage(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A session with a real successful browser call must not be retried
        # even if one call failed with an outage-looking error.
        session = tmp_path / "mixed.jsonl"
        lines = [
            {"type": "message", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": "c1", "name": "browser",
                 "arguments": {"action": "open", "url": "https://example.com"}},
                {"type": "toolCall", "id": "c2", "name": "browser",
                 "arguments": {"action": "snapshot"}},
            ]}},
            {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1",
                "toolName": "browser", "content": [{"type": "text", "text": _OUTAGE_TEXT}]}},
            {"type": "message", "message": {"role": "toolResult", "toolCallId": "c2",
                "toolName": "browser", "content": [{"type": "text", "text": "page snapshot ok"}]}},
        ]
        session.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        calls: list[int] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            calls.append(1)
            return 0, _result_stdout(session), None

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert len(calls) == 1
        assert result.env_status == "success"


class TestOutageRetryHardening:
    """Review findings: the retry must be genuinely fresh and well-scoped."""

    def _blocked_and_good(self, tmp_path: Path) -> tuple[Path, Path]:
        blocked = tmp_path / "blocked.jsonl"
        _write_blocked_session(blocked)
        good = tmp_path / "good.jsonl"
        _write_session(good)
        return blocked, good

    def test_retry_uses_a_fresh_session_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Reusing the session key resumes attempt 1's transcript: the model
        # sees its own failures and gives up without touching the browser,
        # and usage/steps double-count across attempts.
        blocked, good = self._blocked_and_good(tmp_path)
        cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            cmds.append(cmd)
            session = blocked if len(cmds) == 1 else good
            return 0, _result_stdout(session, answer="[blocked]" if len(cmds) == 1 else "ok"), None

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        key1 = cmds[0][cmds[0].index("--session-key") + 1]
        key2 = cmds[1][cmds[1].index("--session-key") + 1]
        assert key1 != key2

    def test_dangling_tool_call_does_not_mask_outage(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A browser call whose toolResult never arrived is unknown, not
        # evidence of a working browser.
        session = tmp_path / "dangling.jsonl"
        lines = [
            {"type": "message", "message": {"role": "assistant", "content": [
                {"type": "toolCall", "id": "c1", "name": "browser",
                 "arguments": {"action": "open", "url": "https://example.com"}},
                {"type": "toolCall", "id": "c2", "name": "browser",
                 "arguments": {"action": "open", "url": "https://example.com"}},
            ]}},
            {"type": "message", "message": {"role": "toolResult", "toolCallId": "c1",
                "toolName": "browser", "content": [{"type": "text", "text": _OUTAGE_TEXT}]}},
            # c2 never gets a toolResult (stop_predicate raced the write)
        ]
        session.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
        calls: list[int] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            calls.append(1)
            return 0, _result_stdout(session, answer="[blocked] browser down"), None

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert len(calls) == 2  # outage detected despite the dangling call
        assert result.env_status == "failed"

    def test_timeout_results_are_not_flipped_or_retried(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        blocked = tmp_path / "blocked.jsonl"
        _write_blocked_session(blocked)
        calls: list[int] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], str]:
            calls.append(1)
            return -1, _result_stdout(blocked, answer="partial"), "Timeout after 10 seconds"

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert len(calls) == 1
        assert result.agent_done == "timeout"

    def test_outage_retries_zero_disables_retry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        blocked = tmp_path / "blocked.jsonl"
        _write_blocked_session(blocked)
        calls: list[int] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            calls.append(1)
            return 0, _result_stdout(blocked, answer="[blocked]"), None

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, {**AGENT_CONFIG, "outage_retries": 0}, tmp_path)
        assert len(calls) == 1
        assert result.env_status == "failed"

    def test_successful_retry_is_recorded_in_metadata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        blocked, good = self._blocked_and_good(tmp_path)
        calls: list[int] = []

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            calls.append(1)
            session = blocked if len(calls) == 1 else good
            return 0, _result_stdout(session, answer="[blocked]" if len(calls) == 1 else "ok"), None

        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "success"
        assert result.agent_metadata.get("outage_retried") == 1

    def test_ssrf_policy_only_for_cdp_browsers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The SSRF preflight is meaningless for a REMOTE CDP browser, but for
        # a locally launched browser it is a real guard and must stay on.
        agent = OpenClawAgent()
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, _result_stdout(), None))
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        cfg = json.loads((tmp_path / ".openclaw-state" / "openclaw.json").read_text())
        assert "ssrfPolicy" not in cfg["browser"]
