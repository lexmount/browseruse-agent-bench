"""
ClaudeCodeAgent - Browser automation using Claude Code CLI with Playwright MCP.

This agent executes tasks by invoking the `claude` CLI in non-interactive mode
(-p / --print) with Playwright MCP tools for browser control.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browseruse_bench.agents.cli_agent import CLIAgent
from browseruse_bench.agents.playwright_mcp import (
    SELF_LAUNCH_BROWSER_IDS,
    build_playwright_mcp_args,
)
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import open_browser_session
from browseruse_bench.browsers.providers.local import warn_if_local_proxy_unsupported
from browseruse_bench.schemas import AgentMetrics, AgentResult, AgentUsage
from browseruse_bench.utils import IS_WINDOWS

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a browser automation agent. "
    "You MUST use ONLY the Playwright MCP tools (mcp__playwright__*) for ALL browser interactions. "
    "Do NOT use Bash, WebFetch, WebSearch, Skill, Agent, or any other non-Playwright tools. "
    "\n\nTask completion rules:\n"
    "- If you can see enough information to answer the task from the current page (e.g., ratings, "
    "names, prices visible in search results), provide your answer IMMEDIATELY without clicking "
    "into individual items to get more detail.\n"
    "- If you encounter a CAPTCHA, verification page, login wall, or access restriction: "
    "close that tab, return to the previous page, and use the data already collected to answer.\n"
    "- Do NOT get stuck retrying the same blocked action. One retry max, then fall back.\n"
    "\n\nScreenshot rules:\n"
    "- When taking screenshots, do NOT specify a filename parameter. "
    "Call mcp__playwright__browser_take_screenshot with only {\"type\": \"png\"}.\n"
    "- Take a screenshot after navigating to the main page and after finding the answer."
)


def _parse_stream(
    stdout_lines: list[str],
    trajectory_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Parse stream-json output, save screenshots, return (result, assistant_messages, saved_files, turns).

    Each turn in `turns` is:
      {"assistant": <message dict>, "tool_results": [<tool_result obj>, ...]}
    """
    result_obj: dict[str, Any] = {}
    assistant_messages: list[dict[str, Any]] = []
    saved_screenshots: list[str] = []
    screenshot_counter = 0
    turns: list[dict[str, Any]] = []

    # Track tool_use_id → tool_name for screenshot tool calls
    screenshot_tool_ids: set[str] = set()

    current_turn: dict[str, Any] | None = None

    for raw_line in stdout_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")

        if msg_type == "result":
            result_obj = obj

        elif msg_type == "assistant":
            message = obj.get("message", {})
            if not isinstance(message, dict):
                continue
            assistant_messages.append(message)
            current_turn = {"assistant": message, "tool_results": []}
            turns.append(current_turn)
            for block in message.get("content", []):
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                if "screenshot" in block.get("name", "").lower():
                    if block_id := block.get("id"):
                        screenshot_tool_ids.add(block_id)

        elif msg_type == "tool_result":
            # Top-level tool_result (older Claude Code versions)
            if current_turn is not None:
                current_turn["tool_results"].append(obj)
            tool_use_id = obj.get("tool_use_id", "")
            if tool_use_id in screenshot_tool_ids:
                content = obj.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "image":
                            source = block.get("source", {})
                            b64_data = source.get("data", "") if isinstance(source, dict) else ""
                            before = len(saved_screenshots)
                            _save_screenshot(b64_data, screenshot_counter, trajectory_dir, saved_screenshots)
                            if len(saved_screenshots) > before:
                                if current_turn is not None:
                                    current_turn["screenshot"] = f"trajectory/screenshot-{screenshot_counter + 1}.png"
                                screenshot_counter += 1

        elif msg_type == "user":
            # Claude Code wraps tool results inside user messages:
            # {"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"...","content":[...]}]}}
            user_content = obj.get("message", {}).get("content", [])
            if not isinstance(user_content, list):
                continue
            for tr_block in user_content:
                if not isinstance(tr_block, dict) or tr_block.get("type") != "tool_result":
                    continue
                tool_use_id = tr_block.get("tool_use_id", "")
                if current_turn is not None:
                    current_turn["tool_results"].append(tr_block)
                if tool_use_id not in screenshot_tool_ids:
                    continue
                inner = tr_block.get("content", [])
                if not isinstance(inner, list):
                    continue
                for block in inner:
                    if not isinstance(block, dict) or block.get("type") != "image":
                        continue
                    source = block.get("source", {})
                    b64_data = source.get("data", "") if isinstance(source, dict) else ""
                    before = len(saved_screenshots)
                    _save_screenshot(b64_data, screenshot_counter, trajectory_dir, saved_screenshots)
                    if len(saved_screenshots) > before:
                        if current_turn is not None:
                            current_turn["screenshot"] = f"trajectory/screenshot-{screenshot_counter + 1}.png"
                        screenshot_counter += 1

    return result_obj, assistant_messages, saved_screenshots, turns


def _save_screenshot(
    b64_data: str,
    index: int,
    trajectory_dir: Path,
    saved_list: list[str],
) -> None:
    if not b64_data:
        return
    try:
        img_bytes = base64.b64decode(b64_data)
    except (binascii.Error, ValueError) as exc:
        logger.warning("Failed to decode screenshot %d: %s", index + 1, exc)
        return
    fname = f"screenshot-{index + 1}.png"
    try:
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        (trajectory_dir / fname).write_bytes(img_bytes)
        saved_list.append(fname)
        logger.info("[Claude Code] Saved %s", fname)
    except OSError as exc:
        logger.warning("Failed to write %s: %s", fname, exc)


def _write_api_logs(
    task_id: str,
    model_id: str,
    system_prompt: str,
    turns: list[dict[str, Any]],
    total_cost_usd: float | None,
    api_logs_dir: Path,
) -> None:
    """Write api_logs/step_NNN.json + system_prompt.txt + summary.md."""
    api_logs_dir.mkdir(parents=True, exist_ok=True)

    # system_prompt.txt
    if system_prompt:
        try:
            (api_logs_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to write system_prompt.txt: %s", exc)

    steps_data: list[dict[str, Any]] = []

    for step_number, turn in enumerate(turns, 1):
        message = turn["assistant"]
        tool_results = turn["tool_results"]
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")

        # Extract URL from navigate tool call
        url: str | None = None
        tool_uses: list[dict[str, Any]] = []
        text_output: str = ""

        for block in message.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_output += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_name = block.get("name", "")
                inp = block.get("input", {})
                tool_uses.append({"tool": tool_name, "input": inp})
                if "navigate" in tool_name and isinstance(inp, dict):
                    url = inp.get("url")

        # Collect tool result summaries (strip image data to keep files small)
        results_data: list[dict[str, Any]] = []
        for tr in tool_results:
            # tr may be a top-level tool_result obj or a tool_result block inside a user message
            tool_use_id = tr.get("tool_use_id", "")
            content = tr.get("content", [])
            result_entry: dict[str, Any] = {"tool_use_id": tool_use_id}
            text_parts: list[str] = []
            has_image = False
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        has_image = True
            elif isinstance(content, str):
                text_parts.append(content)
            if text_parts:
                result_entry["extracted_content"] = "\n".join(text_parts)
            if has_image:
                result_entry["has_screenshot"] = True
            results_data.append(result_entry)

        screenshot_ref: str | None = turn.get("screenshot")
        step_data: dict[str, Any] = {
            "metadata": {
                "task_id": task_id,
                "step_number": step_number,
                "timestamp": timestamp,
                "model_id": model_id,
            },
            "input": {
                "url": url,
                "state_message": None,
                "screenshot_ref": screenshot_ref,
            },
            "output": {
                "thinking": text_output.strip() or None,
                "actions": tool_uses,
            },
            "action_results": results_data,
        }
        steps_data.append(step_data)

        step_file = api_logs_dir / f"step_{step_number:03d}.json"
        try:
            step_file.write_text(
                json.dumps(step_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, TypeError) as exc:
            logger.warning("Failed to write step %d log: %s", step_number, exc)

    # summary.md
    _write_summary_md(
        api_logs_dir=api_logs_dir,
        task_id=task_id,
        model_id=model_id,
        system_prompt=system_prompt,
        steps_data=steps_data,
        total_cost_usd=total_cost_usd,
    )


def _write_summary_md(
    api_logs_dir: Path,
    task_id: str,
    model_id: str,
    system_prompt: str,
    steps_data: list[dict[str, Any]],
    total_cost_usd: float | None,
) -> None:
    lines: list[str] = ["# LLM API Call Log\n", "## Task Info"]
    lines += [
        f"- **Task ID**: {task_id}",
        f"- **Model**: {model_id}",
        f"- **Total Steps**: {len(steps_data)}",
    ]
    if total_cost_usd is not None:
        lines.append(f"- **Total Cost**: ${total_cost_usd:.4f}")
    lines.append("")

    if system_prompt:
        lines += [
            "## System Prompt", "",
            "See [system_prompt.txt](./system_prompt.txt) for the complete system prompt.", "",
            "<details>", "<summary>Click to expand system prompt</summary>", "",
            "```", system_prompt, "```", "", "</details>", "",
        ]

    lines.append("---\n")

    for step_data in steps_data:
        meta = step_data.get("metadata", {})
        inp = step_data.get("input", {})
        out = step_data.get("output", {})
        results = step_data.get("action_results", [])

        step_num = meta.get("step_number", "?")
        ts = meta.get("timestamp", "")
        lines.append(f"## Step {step_num} ({ts})\n")

        url = inp.get("url")
        if url:
            lines += [f"**URL**: {url}", ""]

        screenshot_ref = inp.get("screenshot_ref")
        if screenshot_ref:
            lines += [f"**Screenshot**: [{screenshot_ref}](../{screenshot_ref})", ""]

        lines += ["### Output (Model Response)", ""]
        thinking = out.get("thinking")
        if thinking:
            lines += [f"**Thinking**: {thinking}", ""]

        actions = out.get("actions", [])
        if actions:
            lines.append("**Actions**:")
            for i, action in enumerate(actions, 1):
                try:
                    action_str = json.dumps(action, ensure_ascii=False)
                except TypeError:
                    action_str = str(action)
                lines.append(f"{i}. `{action_str}`")
            lines.append("")

        if results:
            lines += ["### Action Results", ""]
            for result in results:
                content = result.get("extracted_content")
                if content:
                    lines.append(f"- {content[:200]}")
                if result.get("has_screenshot"):
                    lines.append("- *(screenshot captured)*")
            lines.append("")

        lines.append("---\n")

    md_file = api_logs_dir / "summary.md"
    try:
        md_file.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write summary.md: %s", exc)


def _extract_actions(assistant_messages: list[dict[str, Any]]) -> list[str]:
    """Extract tool call actions from assistant messages."""
    actions: list[str] = []
    for message in assistant_messages:
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = block.get("name", "")
            inp = block.get("input", {})
            if not isinstance(inp, dict):
                inp = {}

            if "navigate" in tool_name:
                action = f"Navigate to {inp.get('url', '')}"
            elif "click" in tool_name:
                action = f"Click: {inp.get('selector', inp.get('element', ''))}"
            elif "fill" in tool_name or "type" in tool_name:
                value = str(inp.get("value", inp.get("text", "")))[:60]
                action = f"Type: {value}"
            elif "screenshot" in tool_name:
                action = "Take screenshot"
            elif "snapshot" in tool_name:
                action = "Take snapshot"
            elif "select" in tool_name:
                action = f"Select: {inp.get('value', '')}"
            elif "press" in tool_name:
                action = f"Press key: {inp.get('key', '')}"
            elif "hover" in tool_name:
                action = f"Hover: {inp.get('selector', '')}"
            elif "wait" in tool_name:
                action = f"Wait for: {inp.get('selector', inp.get('text', ''))}"
            else:
                short_name = tool_name.split("__")[-1] if "__" in tool_name else tool_name
                action = short_name
            actions.append(action)

    return actions


@register_agent
class ClaudeCodeAgent(CLIAgent):
    """
    Browser automation agent using Claude Code CLI with Playwright MCP.

    Claude Code is invoked as an external process via `claude -p` (non-interactive mode).
    Playwright MCP must be configured in Claude Code's user-scope MCP settings:
      claude mcp add playwright --scope user -- npx @playwright/mcp@latest
    """

    name = "claude-code"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a browser automation task using Claude Code CLI.

        With a managed browser backend (lexmount, cdp), the backend session is
        opened first and its CDP endpoint is handed to Playwright MCP; with
        local/unset browser_id, MCP launches its own browser.
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
                "endpoint, so claude-code cannot attach Playwright MCP to it. "
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
    ) -> AgentResult | dict[str, Any]:
        task_id = task_info["task_id"]
        # Reuse the prompt already formatted by cli/run.py (it carries the
        # single-site constraint, the same-site region allowance, and the
        # avoid-loops reminder). Fall back to BaseAgent.build_task_prompt for
        # direct (non-CLI) callers so the constraint is still applied once and
        # we never wrap it twice.
        prompt = task_info.get("prompt") or self.build_task_prompt(task_info)

        model = agent_config.get("model_id") or agent_config.get("model", "claude-sonnet-4-6")
        max_turns = int(agent_config.get("max_turns", 50))
        timeout_val = agent_config.get("timeout_seconds") or agent_config.get("timeout", 300)
        try:
            timeout = int(timeout_val)
        except (TypeError, ValueError) as exc:
            logger.warning("Invalid timeout for task %s (%r): %s", task_id, timeout_val, exc)
            timeout = 300

        # Current claude CLI rejects a trailing wildcard ("mcp__playwright*") in
        # allow rules; the segment wildcard "mcp__playwright__*" is accepted.
        allowed_tools = agent_config.get("allowed_tools", "mcp__playwright__*")
        system_prompt = agent_config.get("system_prompt") or _DEFAULT_SYSTEM_PROMPT

        api_key = agent_config.get("api_key")
        base_url = agent_config.get("base_url")

        # Build a Playwright-only MCP config so the subprocess ignores all other
        # user-scope MCP servers (e.g. chrome-devtools-mcp) that would otherwise
        # be loaded and cause spurious socket errors. With a managed backend,
        # build_playwright_mcp_args appends --cdp-endpoint <cdp_url> so MCP
        # attaches to the cloud browser instead of launching a local one.
        playwright_mcp_cmd = agent_config.get("playwright_mcp_command", "npx")
        playwright_mcp_args = build_playwright_mcp_args(agent_config, cdp_url)
        mcp_config_json = json.dumps({
            "mcpServers": {
                "playwright": {
                    "command": playwright_mcp_cmd,
                    "args": playwright_mcp_args,
                }
            }
        })

        exe = "claude.cmd" if IS_WINDOWS else "claude"
        cmd = [
            exe,
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--model", model,
            "--max-turns", str(max_turns),
            "--allowedTools", allowed_tools,
            "--bare",               # skip hooks/CLAUDE.md so user SessionStart hooks
                                    # don't inject conflicting tool-use instructions
            "--dangerously-skip-permissions",  # required for non-interactive mode
            "--system-prompt", system_prompt,
            "--mcp-config", mcp_config_json,
            "--strict-mcp-config",
        ]

        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Executing Claude Code for task %s (model=%s, max_turns=%d)", task_id, model, max_turns
        )

        env = {**os.environ}
        # claude refuses --dangerously-skip-permissions under root/sudo unless
        # IS_SANDBOX=1 is set; server/container deployments commonly run as root.
        env["IS_SANDBOX"] = "1"
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url

        def _stdout_hook(line: str) -> None:
            clean = line.strip()
            if not clean:
                return
            try:
                obj = json.loads(clean)
                msg_type = obj.get("type")
                if msg_type == "assistant":
                    for block in obj.get("message", {}).get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            logger.info("[Claude Code] Tool: %s", block.get("name", ""))
                elif msg_type == "result":
                    num = obj.get("num_turns", "?")
                    cost = obj.get("total_cost_usd")
                    cost_str = f" cost=${cost:.4f}" if cost else ""
                    logger.info("[Claude Code] Done: %s turns%s", num, cost_str)
            except (json.JSONDecodeError, AttributeError):
                pass

        def _stderr_hook(line: str) -> None:
            clean = line.strip()
            if clean and "error" in clean.lower():
                logger.warning("[Claude Code] %s", clean)

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
                    f"Executable '{exe}' not found. "
                    "Please install Claude Code: npm install -g @anthropic-ai/claude-code"
                ),
                metrics=AgentMetrics(end_to_end_ms=0, steps=0),
            )
        if execution_error and "Timeout" in execution_error:
            logger.error("Claude Code task %s timed out after %d seconds", task_id, timeout)

        # Parse stream, extract screenshots and per-turn data
        result_obj, assistant_messages, saved_screenshots, turns = _parse_stream(
            stdout_lines, trajectory_dir
        )

        answer = result_obj.get("result", "")
        num_turns = int(result_obj.get("num_turns", 0))
        duration_ms = int(result_obj.get("duration_ms", 0))
        is_error = result_obj.get("is_error", False)
        total_cost_usd = result_obj.get("total_cost_usd")

        # Fallback values when result event never arrived (e.g. timeout)
        if not answer and turns:
            for turn in reversed(turns):
                for block in reversed(turn["assistant"].get("content", [])):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            answer = text
                            break
                if answer:
                    break
        if not num_turns and turns:
            num_turns = len(turns)
        if not duration_ms:
            duration_ms = int((time.monotonic() - t_start) * 1000)

        action_history = _extract_actions(assistant_messages)

        # Determine final error message (prefer is_error result over generic fallback)
        api_error: str | None = answer if is_error and answer else None

        if execution_error and "Timeout" in execution_error:
            env_status = "success"
            agent_done = "timeout"
        elif execution_error or is_error or (returncode not in (0, None) and not result_obj):
            env_status = "failed"
            agent_done = "error"
            if not answer:
                answer = f"[Task Failed: {execution_error or 'Claude Code exited with error'}]"
        elif result_obj:
            env_status = "success"
            agent_done = "done"
        else:
            env_status = "failed"
            agent_done = "error"
            answer = "[Task Failed: No output from Claude Code]"

        # Write api_logs
        if turns:
            api_logs_dir = task_workspace / "api_logs"
            try:
                _write_api_logs(
                    task_id=task_id,
                    model_id=model,
                    system_prompt=system_prompt,
                    turns=turns,
                    total_cost_usd=total_cost_usd,
                    api_logs_dir=api_logs_dir,
                )
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("Failed to generate api_logs for task %s: %s", task_id, exc)

        agent_metadata: dict[str, Any] = {}
        if total_cost_usd is not None:
            agent_metadata["total_cost_usd"] = total_cost_usd

        return AgentResult(
            task_id=task_id,
            timestamp=datetime.now(UTC),
            env_status=env_status,  # type: ignore[arg-type]
            agent_done=agent_done,  # type: ignore[arg-type]
            answer=answer,
            error=(execution_error or api_error) if env_status == "failed" else None,
            action_history=action_history,
            screenshots=saved_screenshots,
            model_id=model,
            metrics=AgentMetrics(
                end_to_end_ms=duration_ms,
                steps=num_turns,
                usage=AgentUsage(total_cost=total_cost_usd) if total_cost_usd is not None else None,
            ),
            agent_metadata=agent_metadata,
        )
