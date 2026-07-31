"""
OpenHandsAgent - Browser automation using OpenHands CLI with Playwright MCP.

This agent invokes `openhands --headless --json` and supplies model credentials
through OpenHands' environment override path. A task-local home directory holds
the Playwright MCP server config so benchmark runs do not mutate user config.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browseruse_bench.agents.cli_agent import CLIAgent
from browseruse_bench.agents.playwright_mcp import (
    DEFAULT_BROWSER_RULES,
    SELF_LAUNCH_BROWSER_IDS,
    STEP_ITEM_TYPES,
    build_playwright_mcp_args,
    collect_screenshots,
    extract_actions,
    write_api_logs,
)
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import open_browser_session
from browseruse_bench.browsers.providers.local import warn_if_local_proxy_unsupported
from browseruse_bench.schemas import AgentMetrics, AgentResult
from browseruse_bench.utils import IS_WINDOWS

logger = logging.getLogger(__name__)

OPENHANDS_BROWSER_RULES = DEFAULT_BROWSER_RULES.replace(
    "browser automation agent", "browser task agent"
)


def _command_prefix(value: Any, default: list[str]) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [value]
    return default


def _iter_json(stdout_lines: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_line in stdout_lines:
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if not isinstance(value, dict):
        return ""
    for key in ("final_answer", "answer", "result", "message", "content", "text"):
        text = _extract_text(value.get(key))
        if text:
            return text
    return ""


def _normalize_action(obj: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(obj.get("type") or "")
    kind = str(obj.get("kind") or "")
    action = obj.get("action")
    if event_type != "action" and kind != "ActionEvent" and action is None:
        return None
    tool_name = obj.get("tool_name") or obj.get("tool")
    if isinstance(action, dict):
        name = tool_name or action.get("tool") or action.get("tool_name") or action.get("action") or action.get("name")
        args = action.get("data") or action.get("args") or action.get("arguments") or {}
        command = action.get("command")
    else:
        name = action or tool_name or obj.get("name")
        args = obj.get("args") or obj.get("arguments") or {}
        command = obj.get("command")
    if action is None and kind == "ActionEvent":
        return None
    name_text = str(name or "")
    if command:
        return {"type": "command_execution", "command": str(command), "status": "completed"}
    if "mcp" in name_text.lower() or "browser" in name_text.lower() or "playwright" in name_text.lower():
        return {
            "type": "mcp_tool_call",
            "tool": name_text,
            "arguments": args if isinstance(args, dict) else {},
            "status": "completed",
        }
    return {"type": "command_execution", "command": name_text, "status": "completed"}


def _extract_error_message(obj: dict[str, Any]) -> str | None:
    if obj.get("kind") == "AgentErrorEvent":
        return None
    error = obj.get("error")
    if isinstance(error, dict):
        return _extract_text(error) or str(error)
    if isinstance(error, str):
        return error
    if obj.get("code") or obj.get("detail") or obj.get("kind") == "ConversationErrorEvent":
        detail = obj.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        code = obj.get("code")
        if isinstance(code, str) and code:
            return code
    if obj.get("type") == "error":
        return _extract_text(obj) or "OpenHands reported an error"
    return None


def _parse_jsonl(stdout_lines: list[str]) -> tuple[str, list[dict[str, Any]], str | None]:
    """Parse OpenHands `--json` JSONL events into shared bench fields."""
    answer = ""
    items: list[dict[str, Any]] = []
    error_message: str | None = None

    for obj in _iter_json(stdout_lines):
        error_message = _extract_error_message(obj) or error_message
        item = _normalize_action(obj)
        if item:
            items.append(item)
        event_type = str(obj.get("type") or "").lower()
        kind = str(obj.get("kind") or "")
        if event_type in {"finish", "finished", "final", "result", "message"}:
            text = _extract_text(obj)
            if text:
                answer = text
        elif kind == "MessageEvent" and obj.get("source") == "agent":
            text = _extract_text(obj.get("llm_message"))
            if text:
                answer = text
        elif obj.get("final_answer") or obj.get("answer"):
            text = _extract_text(obj.get("final_answer") or obj.get("answer"))
            if text:
                answer = text

    return answer, items, error_message


@register_agent
class OpenHandsAgent(CLIAgent):
    """Browser automation agent using OpenHands CLI with Playwright MCP."""

    name = "openhands"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        browser_id = str(agent_config.get("browser_id") or "")
        if browser_id in SELF_LAUNCH_BROWSER_IDS:
            warn_if_local_proxy_unsupported(agent_config, self.name)
            return self._execute(task_info, agent_config, task_workspace, cdp_url=None)
        with open_browser_session(
            browser_id=browser_id,
            agent_name=self.name,
            agent_config=agent_config,
        ) as session_context:
            cdp_url = session_context.cdp_url if session_context.transport == "cdp" else None
            if not cdp_url:
                return self._unsupported_backend_result(
                    task_info["task_id"], browser_id, session_context.transport
                )
            return self._execute(task_info, agent_config, task_workspace, cdp_url=cdp_url)

    def _unsupported_backend_result(
        self, task_id: str, browser_id: str, transport: str
    ) -> AgentResult:
        return AgentResult(
            task_id=task_id,
            timestamp=datetime.now(UTC),
            env_status="failed",  # type: ignore[arg-type]
            agent_done="error",  # type: ignore[arg-type]
            error=(
                f"Browser backend '{browser_id}' (transport={transport}) provides no CDP "
                "endpoint, so the openhands agent cannot attach Playwright MCP to it. "
                "Use a CDP-capable backend (e.g. lexmount, cdp) or browser_id=local."
            ),
            metrics=AgentMetrics(end_to_end_ms=0, steps=0),
        )

    def _execute(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
        cdp_url: str | None,
    ) -> AgentResult:
        task_id = task_info["task_id"]
        prompt = task_info.get("prompt") or self.build_task_prompt(task_info)
        rules = agent_config.get("system_prompt") or OPENHANDS_BROWSER_RULES
        model = agent_config.get("model_id") or agent_config.get("model", "gpt-5.4")
        timeout = self._resolve_timeout(task_id, agent_config)
        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        home_dir = self._write_workspace_config(agent_config, task_workspace, cdp_url)
        cmd = self._build_command(f"{rules}\n\n{prompt}", agent_config)

        env = {**os.environ, "HOME": str(home_dir), "OH_PERSISTENCE_DIR": str(home_dir / ".openhands")}
        env.setdefault("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv"))
        env["OPENHANDS_SUPPRESS_BANNER"] = "1"
        env["LLM_MODEL"] = str(model)
        if agent_config.get("api_key"):
            env["LLM_API_KEY"] = str(agent_config["api_key"])
        if agent_config.get("base_url"):
            env["LLM_BASE_URL"] = str(agent_config["base_url"])

        logger.info("Executing OpenHands for task %s (model=%s, timeout=%ds)", task_id, model, timeout)
        t_start = time.monotonic()
        try:
            returncode, stdout_lines, execution_error = self._run_subprocess(
                cmd,
                timeout=timeout,
                task_workspace=task_workspace,
                cwd=task_workspace,
                env=env,
                collect_stdout=True,
                stdout_line_hook=_stdout_hook,
                stderr_line_hook=_stderr_hook,
                terminate_process_group=True,
            )
        except FileNotFoundError:
            return AgentResult(
                task_id=task_id,
                timestamp=datetime.now(UTC),
                env_status="failed",  # type: ignore[arg-type]
                agent_done="error",  # type: ignore[arg-type]
                error="Executable 'openhands' not found. Please install OpenHands CLI.",
                metrics=AgentMetrics(end_to_end_ms=0, steps=0),
            )
        duration_ms = int((time.monotonic() - t_start) * 1000)
        return self._finalize_result(
            task_id=task_id,
            model=str(model),
            rules=rules,
            stdout_lines=stdout_lines,
            returncode=returncode,
            execution_error=execution_error,
            duration_ms=duration_ms,
            task_workspace=task_workspace,
            trajectory_dir=trajectory_dir,
        )

    @staticmethod
    def _resolve_timeout(task_id: str, agent_config: dict[str, Any]) -> int:
        timeout_val = agent_config.get("timeout_seconds") or agent_config.get("timeout", 600)
        try:
            return int(timeout_val)
        except (TypeError, ValueError) as exc:
            logger.warning("Invalid timeout for task %s (%r): %s", task_id, timeout_val, exc)
            return 600

    @staticmethod
    def _write_workspace_config(
        agent_config: dict[str, Any],
        task_workspace: Path,
        cdp_url: str | None,
    ) -> Path:
        home_dir = task_workspace / ".openhands-home"
        config_dir = home_dir / ".openhands"
        config_dir.mkdir(parents=True, exist_ok=True)
        mcp_config = {
            "mcpServers": {
                "playwright": {
                    "command": agent_config.get("playwright_mcp_command", "npx"),
                    "args": build_playwright_mcp_args(agent_config, cdp_url),
                    "env": {},
                }
            }
        }
        (config_dir / "mcp.json").write_text(
            json.dumps(mcp_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return home_dir

    @staticmethod
    def _build_command(
        full_prompt: str,
        agent_config: dict[str, Any] | None = None,
    ) -> list[str]:
        default_cmd = ["openhands.cmd"] if IS_WINDOWS else ["openhands"]
        prefix = _command_prefix((agent_config or {}).get("openhands_command"), default_cmd)
        return [
            *prefix,
            "--headless",
            "--json",
            "--override-with-envs",
            "--task", full_prompt,
        ]

    def _finalize_result(
        self,
        task_id: str,
        model: str,
        rules: str,
        stdout_lines: list[str],
        returncode: int,
        execution_error: str | None,
        duration_ms: int,
        task_workspace: Path,
        trajectory_dir: Path,
    ) -> AgentResult:
        answer, items, error_message = _parse_jsonl(stdout_lines)
        if execution_error and "Timeout" in execution_error:
            logger.error("OpenHands task %s timed out", task_id)
        env_status, agent_done = self._map_exit_status(
            returncode, execution_error, has_result=bool(answer)
        )
        if agent_done != "timeout" and error_message:
            env_status, agent_done = "failed", "error"
        if agent_done != "timeout" and env_status == "success" and not answer:
            env_status, agent_done = "failed", "error"
            error_message = self._stderr_error(task_workspace) or "OpenHands exited without an answer"
        if env_status == "failed" and not answer:
            answer = f"[Task Failed: {execution_error or error_message or 'No output from OpenHands'}]"

        saved_screenshots = collect_screenshots(task_workspace, trajectory_dir)
        steps = sum(1 for item in items if item.get("type") in STEP_ITEM_TYPES)
        if items:
            try:
                write_api_logs(task_id, model, rules, items, task_workspace / "api_logs")
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("Failed to generate api_logs for task %s: %s", task_id, exc)

        return AgentResult(
            task_id=task_id,
            timestamp=datetime.now(UTC),
            env_status=env_status,  # type: ignore[arg-type]
            agent_done=agent_done,  # type: ignore[arg-type]
            answer=answer,
            error=(execution_error or error_message) if env_status == "failed" else None,
            action_history=extract_actions(items),
            screenshots=saved_screenshots,
            model_id=model,
            metrics=AgentMetrics(end_to_end_ms=duration_ms, steps=steps),
        )

    @staticmethod
    def _stderr_error(task_workspace: Path) -> str | None:
        stderr_file = task_workspace / "stderr.txt"
        if not stderr_file.is_file():
            return None
        lines = [line.strip() for line in stderr_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        for line in lines:
            if "error" in line.lower() or "failed" in line.lower():
                return line[:500]
        return None


def _stdout_hook(line: str) -> None:
    clean = line.strip()
    if not clean.startswith("{"):
        return
    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        return
    item = _normalize_action(obj)
    if item:
        logger.info("[OpenHands] Action: %s", item.get("tool") or item.get("command"))


def _stderr_hook(line: str) -> None:
    clean = line.strip()
    if clean and "error" in clean.lower():
        logger.warning("[OpenHands] %s", clean)
