"""Tests for HermesAgent: session-store normalization, usage mapping, run_task."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from browseruse_bench.agents.hermes import (
    _API_KEY_ENV,
    HermesAgent,
    _collect_screenshots,
    _rules_for,
    _session_items,
    _state_config,
    _subprocess_env,
    _usage_from_report,
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

USAGE_REPORT: dict[str, Any] = {
    "input_tokens": 1000,
    "output_tokens": 50,
    "cache_read_tokens": 400,
    "cache_write_tokens": 100,
    "total_tokens": 1050,
    "api_calls": 3,
    "model": "gpt-test",
    "session_id": "sess-1",
    "completed": True,
    "failed": False,
}


def _write_state_db(state_dir: Path, session_id: str = "sess-1") -> None:
    """Create a minimal Hermes state.db with one browsing session."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(state_dir / "state.db") as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_calls TEXT)"
        )
        conn.execute("INSERT INTO sessions (id) VALUES (?)", (session_id,))
        tool_calls = json.dumps([
            {
                "id": "c1",
                "call_id": "c1",
                "type": "function",
                "function": {
                    "name": "browser_navigate",
                    "arguments": json.dumps({"url": "https://example.com"}),
                },
            }
        ])
        rows = [
            (session_id, "user", "go", None, None),
            (session_id, "assistant", None, None, tool_calls),
            (session_id, "tool", "opened example.com", "c1", None),
            (session_id, "assistant", "done", None, None),
        ]
        conn.executemany(
            "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def _prepare_success_artifacts(task_workspace: Path) -> None:
    """Simulate the artifacts a successful `hermes -z` run leaves behind."""
    (task_workspace / "hermes_usage.json").write_text(json.dumps(USAGE_REPORT), encoding="utf-8")
    _write_state_db(task_workspace / ".hermes-state")


class TestSessionItems:
    def test_tool_calls_folded_to_items(self, tmp_path: Path) -> None:
        _write_state_db(tmp_path)
        items = _session_items(tmp_path, "sess-1")
        assert len(items) == 1
        assert items[0]["type"] == "mcp_tool_call"
        assert items[0]["tool"] == "browser_navigate"
        assert items[0]["arguments"] == {"url": "https://example.com"}
        assert items[0]["status"] == "completed"
        assert items[0]["result"]["content"][0]["text"] == "opened example.com"

    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        assert _session_items(tmp_path, "sess-1") == []

    def test_unknown_session_falls_back_to_latest(self, tmp_path: Path) -> None:
        # Timed-out runs never produce a usage report; the store is per-task,
        # so the newest (only) session is the right fallback.
        _write_state_db(tmp_path)
        items = _session_items(tmp_path, None)
        assert len(items) == 1
        assert items[0]["tool"] == "browser_navigate"

    def test_malformed_tool_calls_skipped(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        with sqlite3.connect(tmp_path / "state.db") as conn:
            conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id TEXT, role TEXT, content TEXT, tool_call_id TEXT, tool_calls TEXT)"
            )
            conn.execute("INSERT INTO sessions (id) VALUES ('s')")
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, tool_calls) "
                "VALUES ('s', 'assistant', NULL, NULL, 'not-json')"
            )
        assert _session_items(tmp_path, "s") == []


class TestUsageFromReport:
    def test_disjoint_buckets_folded_into_prompt(self) -> None:
        # Hermes reports DISJOINT buckets (input excludes cache reads/writes);
        # AgentUsage wants prompt INCLUDING cached.
        usage = _usage_from_report(USAGE_REPORT)
        assert usage is not None
        assert usage.total_prompt_tokens == 1500
        assert usage.total_prompt_cached_tokens == 400
        assert usage.total_prompt_cache_creation_tokens == 100
        assert usage.total_completion_tokens == 50
        assert usage.entry_count == 3

    def test_empty_report_returns_none(self) -> None:
        assert _usage_from_report(None) is None
        assert _usage_from_report({"input_tokens": 0, "output_tokens": 0}) is None


class TestStateConfig:
    def test_provider_uses_key_env_not_secret(self) -> None:
        config = _state_config("gpt-test", "https://llm.example/v1", AGENT_CONFIG)
        assert config["model"] == {"default": "gpt-test", "provider": "bench"}
        assert config["providers"]["bench"]["base_url"] == "https://llm.example/v1"
        assert config["providers"]["bench"]["key_env"] == _API_KEY_ENV
        assert "sk-test" not in yaml.safe_dump(config)

    def test_reasoning_effort_forwarded(self) -> None:
        config = _state_config("m", "u", {"reasoning_effort": "high"})
        assert config["agent"]["reasoning_effort"] == "high"

    def test_no_auxiliary_section_without_use_vision(self) -> None:
        config = _state_config("m", "u", AGENT_CONFIG)
        assert "auxiliary" not in config

    def test_use_vision_pins_vision_temperature(self) -> None:
        config = _state_config("m", "u", {"use_vision": True})
        assert config["auxiliary"]["vision"]["temperature"] == 1.0

    def test_vision_temperature_override(self) -> None:
        config = _state_config("m", "u", {"use_vision": True, "vision_temperature": 0.3})
        assert config["auxiliary"]["vision"]["temperature"] == 0.3

    def test_null_vision_temperature_falls_back(self) -> None:
        # An explicit null in config.yaml (key present, value None) must fall
        # back to the default, not crash float(None).
        config = _state_config("m", "u", {"use_vision": True, "vision_temperature": None})
        assert config["auxiliary"]["vision"]["temperature"] == 1.0


class TestRulesSelection:
    def test_default_rules_forbid_vision(self) -> None:
        rules = _rules_for({})
        assert "Do NOT use browser_vision" in rules

    def test_use_vision_switches_to_vision_rules(self) -> None:
        rules = _rules_for({"use_vision": True})
        assert "browser_vision" in rules
        assert "Do NOT use browser_vision" not in rules

    def test_explicit_system_prompt_wins(self) -> None:
        assert _rules_for({"use_vision": True, "system_prompt": "custom"}) == "custom"


class TestSubprocessEnv:
    def test_scrubs_provider_and_hermes_vars(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://leak.local")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-leak")
        monkeypatch.setenv("HERMES_INFERENCE_MODEL", "operator-model")
        monkeypatch.setenv("HERMES_HOME", "/operator/.hermes")
        monkeypatch.setenv("BROWSER_CDP_URL", "http://stale:9222")
        monkeypatch.setenv("BENCH_HARMLESS_VAR", "keep-me")
        env = _subprocess_env(tmp_path, "http://127.0.0.1:9222", "sk-bench")
        assert "OPENAI_API_KEY" not in env
        assert "OPENAI_BASE_URL" not in env
        assert "OPENROUTER_API_KEY" not in env
        assert "HERMES_INFERENCE_MODEL" not in env
        assert env["BENCH_HARMLESS_VAR"] == "keep-me"
        assert env["HERMES_HOME"] == str(tmp_path)
        assert env[_API_KEY_ENV] == "sk-bench"
        assert env["BROWSER_CDP_URL"] == "http://127.0.0.1:9222"

    def test_no_cdp_env_for_self_launch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("BROWSER_CDP_URL", "http://stale:9222")
        env = _subprocess_env(tmp_path, None, "sk-bench")
        assert "BROWSER_CDP_URL" not in env


class TestCollectScreenshots:
    def test_copies_pngs_in_mtime_order(self, tmp_path: Path) -> None:
        shots_dir = tmp_path / "state" / "cache" / "screenshots"
        shots_dir.mkdir(parents=True)
        (shots_dir / "browser_screenshot_b.png").write_bytes(b"one")
        (shots_dir / "browser_screenshot_a.png").write_bytes(b"two")
        os.utime(shots_dir / "browser_screenshot_b.png", (1, 1))
        os.utime(shots_dir / "browser_screenshot_a.png", (2, 2))
        trajectory = tmp_path / "trajectory"
        assert _collect_screenshots(tmp_path / "state", trajectory) == [
            "screenshot-1.png",
            "screenshot-2.png",
        ]
        assert (trajectory / "screenshot-1.png").read_bytes() == b"one"

    def test_no_screenshots_dir(self, tmp_path: Path) -> None:
        assert _collect_screenshots(tmp_path, tmp_path / "trajectory") == []


class TestHermesAgentRunTask:
    def _agent(self, monkeypatch: pytest.MonkeyPatch) -> HermesAgent:
        agent = HermesAgent()
        monkeypatch.setattr(agent, "_cli_version", lambda: "Hermes Agent v0.0-test")
        return agent

    def test_successful_run_returns_answer(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = self._agent(monkeypatch)

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            _prepare_success_artifacts(tmp_path)
            return 0, ["The price is $42\n"], None

        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert isinstance(result, AgentResult)
        assert result.env_status == "success"
        assert result.agent_done == "done"
        assert result.answer == "The price is $42"
        assert result.action_history == ["Navigate to https://example.com"]
        assert result.metrics.steps == 1
        assert result.metrics.usage.total_prompt_tokens == 1500
        assert result.agent_metadata["hermes_cli_version"] == "Hermes Agent v0.0-test"
        assert (tmp_path / "api_logs" / "system_prompt.txt").is_file()

    def test_state_config_written_without_secret(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = self._agent(monkeypatch)
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (0, ["ok\n"], None))
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        written = (tmp_path / ".hermes-state" / "config.yaml").read_text(encoding="utf-8")
        assert "sk-test" not in written
        assert _API_KEY_ENV in written

    def test_command_shape(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent = self._agent(monkeypatch)
        captured: dict[str, Any] = {}

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            captured["cmd"] = cmd
            captured["env"] = kw.get("env")
            return 0, ["ok\n"], None

        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        cmd = captured["cmd"]
        assert cmd[0] == "hermes"
        assert cmd[1] == "-z"
        assert "Go to example.com" in cmd[2]
        assert cmd[cmd.index("-t") + 1] == "browser"
        assert captured["env"][_API_KEY_ENV] == "sk-test"

    def test_failed_report_maps_to_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = self._agent(monkeypatch)

        def fake_run(cmd: list[str], **kw: Any) -> tuple[int, list[str], None]:
            report = dict(USAGE_REPORT, completed=False, failed=True, failure="boom")
            (tmp_path / "hermes_usage.json").write_text(json.dumps(report), encoding="utf-8")
            return 0, [], None

        monkeypatch.setattr(agent, "_run_subprocess", fake_run)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert result.error == "boom"
        assert result.answer.startswith("[Task Failed:")

    def test_crash_without_output_keeps_diagnostic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Non-zero exit, nothing on stdout, no usage report: the result must
        # still carry an error message, not error=None.
        agent = self._agent(monkeypatch)
        monkeypatch.setattr(agent, "_run_subprocess", lambda *a, **kw: (1, [], None))
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert result.agent_done == "error"
        assert "exited with code 1" in result.error
        assert result.answer.startswith("[Task Failed:")

    def test_timeout_preserved(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent = self._agent(monkeypatch)
        monkeypatch.setattr(
            agent, "_run_subprocess", lambda *a, **kw: (-1, [], "Timeout after 10 seconds")
        )
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "success"
        assert result.agent_done == "timeout"

    def test_missing_executable(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        agent = self._agent(monkeypatch)

        def raise_not_found(*a: Any, **kw: Any) -> None:
            raise FileNotFoundError("hermes")

        monkeypatch.setattr(agent, "_run_subprocess", raise_not_found)
        result = agent.run_task(TASK_INFO, AGENT_CONFIG, tmp_path)
        assert result.env_status == "failed"
        assert "install" in result.error.lower()

    def test_non_cdp_backend_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        agent = self._agent(monkeypatch)

        class FakeSession:
            transport = "playwright"
            cdp_url = None

            def __enter__(self) -> FakeSession:
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

        monkeypatch.setattr(
            "browseruse_bench.agents.hermes.open_browser_session",
            lambda **kw: FakeSession(),
        )
        config = dict(AGENT_CONFIG, browser_id="browser-use-cloud")
        result = agent.run_task(TASK_INFO, config, tmp_path)
        assert result.env_status == "failed"
        assert "no CDP" in result.error
