"""
CodexAgent - Browser automation using OpenAI Codex CLI with Playwright MCP.

This agent executes tasks by invoking `codex exec` in non-interactive mode
(--json JSONL event stream) with a Playwright MCP server injected via -c
config overrides for browser control.
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

# codex config id for the proxy model provider. Must not collide with codex's
# reserved built-ins (openai/azure/gemini/...), which it refuses to override.
_CODEX_PROXY_PROVIDER_ID = "bench"


def _parse_events(stdout_lines: list[str]) -> tuple[str, list[dict[str, Any]], dict[str, int], str | None]:
    """Parse `codex exec --json` JSONL output.

    Returns ``(answer, items, usage_totals, error_message)``:
    - *answer*: text of the last completed ``agent_message`` item.
    - *items*: all completed items (agent_message / mcp_tool_call / ...).
    - *usage_totals*: token usage accumulated across ``turn.completed`` events.
    - *error_message*: message from an ``error`` / ``turn.failed`` event that was
      not followed by a ``turn.completed`` (a later completed turn means the
      error was transient and the run recovered).
    """
    answer = ""
    items: list[dict[str, Any]] = []
    usage_totals = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    error_message: str | None = None

    for raw_line in stdout_lines:
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = obj.get("type")
        if event_type == "item.completed":
            item = obj.get("item")
            if isinstance(item, dict):
                items.append(item)
                if item.get("type") == "agent_message" and item.get("text"):
                    answer = str(item["text"])
        elif event_type == "turn.completed":
            _accumulate_usage(usage_totals, obj.get("usage"))
            error_message = None
        elif event_type in ("turn.failed", "error"):
            error_message = _extract_error_message(obj) or error_message

    return answer, items, usage_totals, error_message


def _accumulate_usage(totals: dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key in totals:
        value = usage.get(key)
        if isinstance(value, int | float):
            totals[key] += int(value)


def _extract_error_message(obj: dict[str, Any]) -> str | None:
    error = obj.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if obj.get("message"):
        return str(obj["message"])
    return None


def _toml_value(value: Any) -> str:
    """Render a -c override value as TOML (JSON syntax is TOML-compatible here)."""
    return json.dumps(value, ensure_ascii=False)


@register_agent
class CodexAgent(CLIAgent):
    """
    Browser automation agent using OpenAI Codex CLI with Playwright MCP.

    Codex is invoked as an external process via `codex exec --json`. The
    Playwright MCP server is injected with -c overrides; the user-level
    ~/.codex/config.toml is ignored (auth.json is still used), so install
    Codex and log in first:
      npm install -g @openai/codex && codex login
    """

    name = "codex"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a browser automation task using Codex CLI.

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
                "endpoint, so the codex agent cannot attach Playwright MCP to it. "
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
        model = agent_config.get("model_id") or agent_config.get("model", "gpt-5.5")
        timeout = self._resolve_timeout(task_id, agent_config)

        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        last_message_file = task_workspace / "last_message.txt"
        cmd = self._build_command(
            full_prompt=f"{rules}\n\n{prompt}",
            model=model,
            agent_config=agent_config,
            task_workspace=task_workspace,
            last_message_file=last_message_file,
            cdp_url=cdp_url,
        )

        env = {**os.environ}
        # The model provider's env_key is OPENAI_API_KEY (see _build_command),
        # so codex reads the key from here whether it targets the default
        # endpoint or a configured base_url provider. OPENAI_BASE_URL is not
        # set: codex ignores it; base_url is wired via the -c model_provider.
        if agent_config.get("api_key"):
            env["OPENAI_API_KEY"] = str(agent_config["api_key"])

        logger.info("Executing Codex for task %s (model=%s, timeout=%ds)", task_id, model, timeout)
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
                    "Executable 'codex' not found. "
                    "Please install Codex CLI: npm install -g @openai/codex"
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
            last_message_file=last_message_file,
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
    def _build_command(
        full_prompt: str,
        model: str,
        agent_config: dict[str, Any],
        task_workspace: Path,
        last_message_file: Path,
        cdp_url: str | None = None,
    ) -> list[str]:
        sandbox = agent_config.get("sandbox_mode", "read-only")
        mcp_command = agent_config.get("playwright_mcp_command", "npx")
        mcp_args = build_playwright_mcp_args(agent_config, cdp_url)
        mcp_startup_timeout = int(agent_config.get("mcp_startup_timeout", 120))
        mcp_tool_timeout = int(agent_config.get("mcp_tool_timeout", 120))

        exe = "codex.cmd" if IS_WINDOWS else "codex"
        cmd = [
            exe, "exec", full_prompt,
            "--json",
            "--model", model,
            "--sandbox", sandbox,
            "--cd", str(task_workspace),
            "--skip-git-repo-check",   # task workspaces are not git repositories
            "--ephemeral",             # do not persist session files
            "--ignore-user-config",    # hermetic: skip ~/.codex/config.toml (auth still works)
            "--output-last-message", str(last_message_file),
            "-c", f"mcp_servers.playwright.command={_toml_value(mcp_command)}",
            "-c", f"mcp_servers.playwright.args={_toml_value(mcp_args)}",
            # "approve": auto-approve MCP tool calls; exec mode has no
            # interactive reviewer, so "prompt"/"auto" cancel every call.
            "-c", 'mcp_servers.playwright.default_tools_approval_mode="approve"',
            "-c", f"mcp_servers.playwright.startup_timeout_sec={mcp_startup_timeout}",
            "-c", f"mcp_servers.playwright.tool_timeout_sec={mcp_tool_timeout}",
        ]
        # codex ignores OPENAI_BASE_URL; to route it at a custom (proxy)
        # endpoint, register a model provider under a fixed, non-reserved id
        # (codex rejects overriding built-ins like "openai"/"azure", so the
        # config's model_provider — often "openai" — is NOT used as the id).
        # wire_api must be "responses" (codex >=0.139 dropped "chat"), so the
        # endpoint has to serve the OpenAI Responses API. Without base_url,
        # codex uses its default provider (api.openai.com / ChatGPT auth.json).
        base_url = agent_config.get("base_url")
        if base_url:
            provider = _CODEX_PROXY_PROVIDER_ID
            cmd += [
                "-c", f"model_providers.{provider}.name={_toml_value(provider)}",
                "-c", f"model_providers.{provider}.base_url={_toml_value(str(base_url))}",
                "-c", f'model_providers.{provider}.wire_api="responses"',
                "-c", f'model_providers.{provider}.env_key="OPENAI_API_KEY"',
                "-c", f"model_provider={_toml_value(provider)}",
            ]
        return cmd

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
        last_message_file: Path,
    ) -> AgentResult:
        answer, items, usage_totals, error_message = _parse_events(stdout_lines)
        if not answer and last_message_file.is_file():
            answer = last_message_file.read_text(encoding="utf-8").strip()

        if execution_error and "Timeout" in execution_error:
            logger.error("Codex task %s timed out", task_id)
        env_status, agent_done = self._map_exit_status(
            returncode, execution_error, has_result=bool(answer)
        )
        # An unrecovered error/turn.failed event marks the run failed even when
        # an earlier partial agent_message produced answer text.
        if agent_done != "timeout" and error_message:
            env_status, agent_done = "failed", "error"
        if env_status == "failed" and not answer:
            answer = f"[Task Failed: {execution_error or error_message or 'No output from Codex'}]"

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


def _stdout_hook(line: str) -> None:
    clean = line.strip()
    if not clean.startswith("{"):
        return
    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        return
    if obj.get("type") == "item.completed":
        item = obj.get("item", {})
        if isinstance(item, dict) and item.get("type") == "mcp_tool_call":
            logger.info("[Codex] Tool: %s", item.get("tool", ""))
    elif obj.get("type") == "turn.completed":
        usage = obj.get("usage", {})
        if isinstance(usage, dict):
            logger.info(
                "[Codex] Turn done: in=%s out=%s tokens",
                usage.get("input_tokens"), usage.get("output_tokens"),
            )


def _stderr_hook(line: str) -> None:
    clean = line.strip()
    if clean and "error" in clean.lower():
        logger.warning("[Codex] %s", clean)
