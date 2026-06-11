"""
CursorAgent - Browser automation using Cursor CLI with Playwright MCP.

This agent executes tasks by invoking `cursor-agent -p` in non-interactive mode
(--output-format stream-json) with a Playwright MCP server injected via a
workspace-level .cursor/mcp.json for browser control.
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
from browseruse_bench.schemas import AgentMetrics, AgentResult, AgentUsage
from browseruse_bench.utils import IS_WINDOWS

logger = logging.getLogger(__name__)

# Maps stream-json usage keys to the shared usage-total keys.
_USAGE_KEY_MAP = {
    "inputTokens": "input_tokens",
    "cacheReadTokens": "cached_input_tokens",
    "outputTokens": "output_tokens",
}


def _text_of_message(message: dict[str, Any]) -> str:
    content = message.get("content", [])
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "".join(parts).strip()


def _normalize_mcp_call(started_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize a started mcpToolCall args block to the shared item shape."""
    arguments = started_args.get("args")
    return {
        "type": "mcp_tool_call",
        "tool": str(started_args.get("toolName", "")),
        "arguments": arguments if isinstance(arguments, dict) else {},
        "status": "in_progress",
    }


def _apply_mcp_result(item: dict[str, Any], result: dict[str, Any]) -> None:
    """Fold a completed mcpToolCall result into the normalized item."""
    success = result.get("success")
    if isinstance(success, dict):
        item["status"] = "completed"
        texts = [
            block["text"].get("text", "")
            for block in success.get("content", [])
            if isinstance(block, dict) and isinstance(block.get("text"), dict)
        ]
        item["result"] = {"content": [{"type": "text", "text": "\n".join(texts)}]}
        return
    item["status"] = "failed"
    rejected = result.get("rejected")
    reason = rejected.get("reason") if isinstance(rejected, dict) else None
    item["error"] = {"message": str(reason or "MCP tool call failed")}


def _normalize_tool_call(obj: dict[str, Any], pending: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Track started/completed tool_call events; return the item when completed.

    Cursor splits a tool call across two events: `started` carries the args,
    `completed` carries only the result, joined by call_id.
    """
    call_id = str(obj.get("call_id", ""))
    tool_call = obj.get("tool_call")
    if not isinstance(tool_call, dict):
        return None
    mcp = tool_call.get("mcpToolCall")
    shell = tool_call.get("shellToolCall")
    if obj.get("subtype") == "started":
        if isinstance(mcp, dict) and isinstance(mcp.get("args"), dict):
            pending[call_id] = _normalize_mcp_call(mcp["args"])
        elif isinstance(shell, dict) and isinstance(shell.get("args"), dict):
            pending[call_id] = {
                "type": "command_execution",
                "command": str(shell["args"].get("command", "")),
                "status": "in_progress",
            }
        return None
    if obj.get("subtype") != "completed":
        return None
    item = pending.pop(call_id, None) or {"type": "mcp_tool_call", "tool": "", "arguments": {}}
    if isinstance(mcp, dict) and isinstance(mcp.get("result"), dict):
        _apply_mcp_result(item, mcp["result"])
    elif isinstance(shell, dict):
        item["status"] = "completed"
    return item


def _parse_stream(stdout_lines: list[str]) -> tuple[str, list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """Parse `cursor-agent --output-format stream-json` JSONL output.

    Returns ``(answer, items, usage_totals, result_obj)``:
    - *answer*: text of the last ``assistant`` message.
    - *items*: completed tool calls normalized to the shared step-item shape.
    - *usage_totals*: token usage from the final ``result`` event.
    - *result_obj*: the final ``result`` event (empty when it never arrived).
    """
    answer = ""
    items: list[dict[str, Any]] = []
    usage_totals = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    result_obj: dict[str, Any] = {}
    pending: dict[str, dict[str, Any]] = {}

    for raw_line in stdout_lines:
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = obj.get("type")
        if event_type == "assistant":
            text = _text_of_message(obj.get("message", {}))
            if text:
                answer = text
        elif event_type == "tool_call":
            item = _normalize_tool_call(obj, pending)
            if item:
                items.append(item)
        elif event_type == "result":
            result_obj = obj
            usage = obj.get("usage")
            if isinstance(usage, dict):
                for src_key, dst_key in _USAGE_KEY_MAP.items():
                    value = usage.get(src_key)
                    if isinstance(value, int | float):
                        usage_totals[dst_key] += int(value)

    return answer, items, usage_totals, result_obj


@register_agent
class CursorAgent(CLIAgent):
    """
    Browser automation agent using Cursor CLI with Playwright MCP.

    Cursor is invoked as an external process via `cursor-agent -p`. The
    Playwright MCP server and the permission policy (allow MCP, deny shell)
    are written to workspace-level .cursor/ config files; --force is required
    because non-interactive mode auto-rejects MCP tool approvals otherwise.
    Install and authenticate first:
      curl https://cursor.com/install -fsS | bash
      cursor-agent login   # or CURSOR_API_KEY
    """

    name = "cursor"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a browser automation task using Cursor CLI.

        With a managed browser backend configured (e.g. lexmount, cdp), the
        backend session is opened first and its CDP endpoint is handed to
        Playwright MCP, so login contexts/proxies injected by cli/run.py apply.
        """
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
        """Fail fast instead of silently self-launching a local browser."""
        return AgentResult(
            task_id=task_id,
            timestamp=datetime.now(UTC),
            env_status="failed",  # type: ignore[arg-type]
            agent_done="error",  # type: ignore[arg-type]
            error=(
                f"Browser backend '{browser_id}' (transport={transport}) provides no CDP "
                "endpoint, so the cursor agent cannot attach Playwright MCP to it. "
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
        rules = agent_config.get("system_prompt") or DEFAULT_BROWSER_RULES
        model = agent_config.get("model_id") or agent_config.get("model", "gpt-5.2")
        timeout = self._resolve_timeout(task_id, agent_config)

        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        self._write_workspace_config(agent_config, task_workspace, cdp_url)
        cmd = self._build_command(f"{rules}\n\n{prompt}", model, task_workspace, agent_config)

        env = {**os.environ}
        if agent_config.get("api_key"):
            env["CURSOR_API_KEY"] = str(agent_config["api_key"])
        # Isolate from the operator's global ~/.cursor (MCP servers, rules):
        # only the workspace-injected Playwright MCP server may be loaded.
        # Requires API-key auth; set isolate_user_config: false to keep the
        # global config dir (and OAuth login state) instead.
        if agent_config.get("isolate_user_config", True):
            config_dir = task_workspace / ".cursor-config"
            config_dir.mkdir(parents=True, exist_ok=True)
            env["CURSOR_CONFIG_DIR"] = str(config_dir)

        logger.info("Executing Cursor for task %s (model=%s, timeout=%ds)", task_id, model, timeout)
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
            )
        except FileNotFoundError:
            return AgentResult(
                task_id=task_id,
                timestamp=datetime.now(UTC),
                env_status="failed",  # type: ignore[arg-type]
                agent_done="error",  # type: ignore[arg-type]
                error=(
                    "Executable 'cursor-agent' not found. "
                    "Please install Cursor CLI: curl https://cursor.com/install -fsS | bash"
                ),
                metrics=AgentMetrics(end_to_end_ms=0, steps=0),
            )
        duration_ms = int((time.monotonic() - t_start) * 1000)

        return self._finalize_result(
            task_id=task_id,
            model=model,
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
    ) -> None:
        """Write .cursor/mcp.json (Playwright server) and .cursor/cli.json (permissions)."""
        cursor_dir = task_workspace / ".cursor"
        cursor_dir.mkdir(parents=True, exist_ok=True)
        mcp_config = {
            "mcpServers": {
                "playwright": {
                    "command": agent_config.get("playwright_mcp_command", "npx"),
                    "args": build_playwright_mcp_args(agent_config, cdp_url),
                }
            }
        }
        (cursor_dir / "mcp.json").write_text(
            json.dumps(mcp_config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # --force auto-approves tool calls unless explicitly denied, so deny
        # shell, web-fetch, and file tools here; browsing happens exclusively
        # through the MCP server.
        permissions = {
            "permissions": {
                "allow": ["Mcp(playwright)"],
                "deny": ["Shell(**)", "WebFetch(**)", "Read(**)", "Write(**)"],
            }
        }
        (cursor_dir / "cli.json").write_text(
            json.dumps(permissions, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _build_command(
        full_prompt: str,
        model: str,
        task_workspace: Path,
        agent_config: dict[str, Any] | None = None,
    ) -> list[str]:
        default_exe = "cursor-agent.cmd" if IS_WINDOWS else "cursor-agent"
        exe = str((agent_config or {}).get("cursor_agent_command") or default_exe)
        return [
            exe,
            "-p", full_prompt,
            "--output-format", "stream-json",
            "--model", model,
            "--workspace", str(task_workspace),
            "--trust",          # headless mode: trust the task workspace
            "--approve-mcps",   # auto-approve the injected MCP server
            # Required: non-interactive runs auto-reject MCP tool approvals
            # otherwise ("User rejected MCP: ..."). Shell stays blocked via the
            # deny rule in .cursor/cli.json.
            "--force",
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
        answer, items, usage_totals, result_obj = _parse_stream(stdout_lines)
        if not answer and isinstance(result_obj.get("result"), str):
            answer = result_obj["result"].strip()

        if execution_error and "Timeout" in execution_error:
            logger.error("Cursor task %s timed out", task_id)
        env_status, agent_done = self._map_exit_status(
            returncode, execution_error, has_result=bool(answer)
        )
        error_message = self._result_error(result_obj)
        # A normal run always ends with a terminal result event; exiting
        # without one (e.g. MCP startup failure after a partial preamble)
        # is a failure even when some assistant text was emitted.
        if not result_obj and not error_message:
            error_message = "Cursor exited without a terminal result event"
        if agent_done != "timeout" and error_message:
            env_status, agent_done = "failed", "error"
        if env_status == "failed" and not answer:
            answer = f"[Task Failed: {execution_error or error_message or 'No output from Cursor'}]"

        saved_screenshots = collect_screenshots(task_workspace, trajectory_dir)
        steps = sum(1 for item in items if item.get("type") in STEP_ITEM_TYPES)
        if items:
            try:
                write_api_logs(task_id, model, rules, items, task_workspace / "api_logs")
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("Failed to generate api_logs for task %s: %s", task_id, exc)

        total_tokens = usage_totals["input_tokens"] + usage_totals["output_tokens"]
        usage = AgentUsage(
            total_prompt_tokens=usage_totals["input_tokens"],
            total_prompt_cached_tokens=usage_totals["cached_input_tokens"],
            total_completion_tokens=usage_totals["output_tokens"],
            total_tokens=total_tokens,
        ) if total_tokens else None

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
            metrics=AgentMetrics(end_to_end_ms=duration_ms, steps=steps, usage=usage),
        )

    @staticmethod
    def _result_error(result_obj: dict[str, Any]) -> str | None:
        """Extract an error message from the final result event, if any."""
        if not result_obj:
            return None
        if result_obj.get("is_error") or result_obj.get("subtype") != "success":
            return str(result_obj.get("result") or "Cursor reported an error result")
        return None


def _stdout_hook(line: str) -> None:
    clean = line.strip()
    if not clean.startswith("{"):
        return
    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        return
    if obj.get("type") == "tool_call" and obj.get("subtype") == "started":
        mcp = obj.get("tool_call", {}).get("mcpToolCall", {})
        if isinstance(mcp, dict) and isinstance(mcp.get("args"), dict):
            logger.info("[Cursor] Tool: %s", mcp["args"].get("toolName", ""))
    elif obj.get("type") == "result":
        usage = obj.get("usage", {})
        if isinstance(usage, dict):
            logger.info(
                "[Cursor] Done: in=%s out=%s tokens",
                usage.get("inputTokens"), usage.get("outputTokens"),
            )


def _stderr_hook(line: str) -> None:
    clean = line.strip()
    if clean and "error" in clean.lower():
        logger.warning("[Cursor] %s", clean)
