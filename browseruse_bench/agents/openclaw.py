"""
OpenClawAgent - Browser automation using the OpenClaw CLI's built-in browser tool.

This agent executes tasks by invoking `openclaw agent --local --json` (one
embedded agent turn, no Gateway required) with a per-task isolated state
directory. Browsing uses OpenClaw's own `browser` tool: either its managed
local Chrome, or an external CDP endpoint (e.g. lexmount) attached via a
browser profile with `cdpUrl` + `attachOnly`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browseruse_bench.agents.cli_agent import CLIAgent
from browseruse_bench.agents.playwright_mcp import (
    SELF_LAUNCH_BROWSER_IDS,
    STEP_ITEM_TYPES,
    extract_actions,
    write_api_logs,
)
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import open_browser_session
from browseruse_bench.browsers.providers.local import warn_if_local_proxy_unsupported
from browseruse_bench.schemas import AgentMetrics, AgentResult, AgentUsage
from browseruse_bench.utils import IS_WINDOWS
from browseruse_bench.utils.parse_utils import safe_int

logger = logging.getLogger(__name__)

_DEFAULT_RULES = (
    "You are a browser automation agent. "
    "You MUST use ONLY the 'browser' tool for ALL browser interactions (and 'read' "
    "for skill files). Do NOT run shell commands and do NOT write files."
    "\n\nTask completion rules:\n"
    "- If you can see enough information to answer the task from the current page (e.g., "
    "ratings, names, prices visible in search results), provide your answer IMMEDIATELY "
    "without clicking into individual items to get more detail.\n"
    "- If you encounter a CAPTCHA, verification page, login wall, or access restriction: "
    "close that tab, return to the previous page, and use the data already collected to answer.\n"
    "- Do NOT get stuck retrying the same blocked action. One retry max, then fall back.\n"
    "\n\nScreenshot rules:\n"
    "- Take a screenshot with the browser screenshot action after navigating to the main "
    "page and after finding the answer."
)

_MEDIA_PATH_RE = re.compile(r"MEDIA:(\S+)")

# Failure signature of a transient OpenClaw browser-control outage: the very
# first browser tool call errors with this text after ~30s and the agent gives
# up. Adjacent tasks succeed, so one retry on a fresh browser session recovers.
_GATEWAY_TIMEOUT_SNIPPET = "Restart the OpenClaw gateway"
_GATEWAY_TIMEOUT_ERROR = "OpenClaw gateway timed out on the first browser tool call"
_GATEWAY_TIMEOUT_MAX_STEPS = 2


def _first_browser_call_gateway_timeout(items: list[dict[str, Any]]) -> bool:
    """True when the first browser tool call failed with the gateway-timeout signature."""
    for item in items:
        if item.get("type") != "mcp_tool_call":
            continue
        if not str(item.get("tool", "")).startswith("browser"):
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            return False
        texts = [
            str(block.get("text", ""))
            for block in result.get("content", [])
            if isinstance(block, dict)
        ]
        return _GATEWAY_TIMEOUT_SNIPPET in "\n".join(texts)
    return False


def _stdout_json(stdout_lines: list[str]) -> dict[str, Any] | None:
    """Find the CLI result JSON object in the accumulated output, or None.

    OpenClaw interleaves log lines with the result object and sometimes emits
    it on stderr, so scan the combined text for the first JSON object that
    looks like a result payload instead of requiring clean stdout.
    """
    text = "".join(stdout_lines).strip()
    decoder = json.JSONDecoder()
    for match in re.finditer(r"{", text):
        try:
            parsed, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and ("payloads" in parsed or "meta" in parsed):
            return parsed
    return None


def _normalize_session_items(session_file: Path) -> list[dict[str, Any]]:
    """Read the session JSONL and normalize tool calls to the shared item shape."""
    if not session_file.is_file():
        return []
    items: list[dict[str, Any]] = []
    by_call_id: dict[str, dict[str, Any]] = {}
    for raw_line in session_file.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        message = obj.get("message")
        if not isinstance(message, dict):
            continue
        _fold_message(message, items, by_call_id)
    return items


def _fold_openclaw_usage(raw: Any, totals: dict[str, int]) -> bool:
    """Accumulate one OpenClaw (pi-ai) usage block into *totals*.

    OpenClaw reports Anthropic-style disjoint components: ``input`` EXCLUDES
    ``cacheRead``/``cacheWrite``. Fold them into the prompt count to match the
    AgentUsage convention (prompt includes cached). Returns True when the
    block carried any tokens.
    """
    if not isinstance(raw, dict):
        return False
    input_tokens = safe_int(raw.get("input"))
    cache_read = safe_int(raw.get("cacheRead"))
    cache_write = safe_int(raw.get("cacheWrite"))
    output_tokens = safe_int(raw.get("output"))
    if input_tokens + cache_read + cache_write + output_tokens == 0:
        return False
    totals["prompt"] += input_tokens + cache_read + cache_write
    totals["cached"] += cache_read
    totals["cache_creation"] += cache_write
    totals["completion"] += output_tokens
    totals["entries"] += 1
    return True


def _collect_session_usage(session_file: Path | None) -> dict[str, int]:
    """Sum per-call usage blocks across all assistant messages in the session log."""
    totals = {"prompt": 0, "cached": 0, "cache_creation": 0, "completion": 0, "entries": 0}
    if session_file is None or not session_file.is_file():
        return totals
    for raw_line in session_file.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            logger.debug("OpenClaw usage: skipping unparsable session line: %s", exc)
            continue
        message = obj.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            _fold_openclaw_usage(message.get("usage"), totals)
    return totals


def _fold_message(
    message: dict[str, Any],
    items: list[dict[str, Any]],
    by_call_id: dict[str, dict[str, Any]],
) -> None:
    role = message.get("role")
    if role == "assistant":
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "toolCall":
                item = _normalize_tool_call(block)
                items.append(item)
                by_call_id[str(block.get("id", ""))] = item
        return
    if role != "toolResult":
        return
    item = by_call_id.get(str(message.get("toolCallId", "")))
    if item is None:
        return
    texts: list[str] = []
    for block in message.get("content", []):
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            texts.append(block.get("text", ""))
            continue
        media_path = _image_block_path(block)
        if media_path:
            texts.append(f"MEDIA:{media_path}")
    details = message.get("details")
    if isinstance(details, dict):
        media = details.get("media")
        if isinstance(media, dict) and isinstance(media.get("mediaUrl"), str):
            texts.append(f"MEDIA:{media['mediaUrl']}")
        elif isinstance(details.get("path"), str):
            texts.append(f"MEDIA:{details['path']}")
    item["status"] = "completed"
    item["result"] = {"content": [{"type": "text", "text": "\n".join(texts)}]}


def _image_block_path(block: dict[str, Any]) -> str | None:
    """Extract a file path from an image/media result block, if present."""
    if block.get("type") not in ("image", "media"):
        return None
    for key in ("path", "url", "mediaUrl", "file"):
        value = block.get(key)
        if isinstance(value, str) and value.startswith("/"):
            return value
    source = block.get("source")
    if isinstance(source, dict) and isinstance(source.get("path"), str):
        return source["path"]
    return None


def _normalize_tool_call(block: dict[str, Any]) -> dict[str, Any]:
    name = str(block.get("name", ""))
    arguments = block.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    if name == "browser":
        action = str(arguments.get("action", ""))
        return {
            "type": "mcp_tool_call",
            "tool": f"browser_{action}" if action else "browser",
            "arguments": arguments,
            "status": "in_progress",
        }
    if name == "exec":
        return {
            "type": "command_execution",
            "command": str(arguments.get("command", ""))[:200],
            "status": "in_progress",
        }
    return {"type": "mcp_tool_call", "tool": name, "arguments": arguments, "status": "in_progress"}


def _collect_media_screenshots(items: list[dict[str, Any]], trajectory_dir: Path) -> list[str]:
    """Copy MEDIA:<path> screenshot files referenced by tool results into trajectory/."""
    saved: list[str] = []
    for item in items:
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        for block in result.get("content", []):
            if isinstance(block, dict):
                _copy_media_paths(str(block.get("text", "")), trajectory_dir, saved)
    return saved


def _copy_media_paths(text: str, trajectory_dir: Path, saved: list[str]) -> None:
    for match in _MEDIA_PATH_RE.finditer(text):
        source = Path(match.group(1))
        if not source.is_file() or source.suffix.lower() not in (".png", ".jpeg", ".jpg"):
            continue
        fname = f"screenshot-{len(saved) + 1}{source.suffix.lower()}"
        try:
            trajectory_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, trajectory_dir / fname)
            saved.append(fname)
        except OSError as exc:
            logger.warning("Failed to copy screenshot %s: %s", source, exc)


def _normalize_thinking_level(value: Any) -> str | None:
    """Map config reasoning-effort spellings to OpenClaw --thinking levels."""
    if not value:
        return None
    level = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if level in ("extra_high", "extrahigh"):
        return "xhigh"
    return level


@register_agent
class OpenClawAgent(CLIAgent):
    """
    Browser automation agent using the OpenClaw CLI.

    OpenClaw is invoked as an external process via `openclaw agent --local
    --json` with OPENCLAW_STATE_DIR/OPENCLAW_CONFIG_PATH pointed at a per-task
    directory (the operator's ~/.openclaw is never touched). The embedded
    agent's `browser` tool drives either OpenClaw's managed Chrome or an
    external CDP endpoint. The CLI process stays alive after the turn (its
    browser service keeps running), so stdout is parsed incrementally and the
    process is terminated as soon as the result JSON is complete.
    Install first: npm install -g openclaw
    """

    name = "openclaw"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a browser automation task using OpenClaw CLI.

        A first-browser-call gateway timeout is a transient infrastructure
        failure, not an agent outcome: retry once on a fresh browser session
        with a clean per-task OpenClaw state.
        """
        result = self._run_once(task_info, agent_config, task_workspace)
        if not self._is_first_call_gateway_timeout(result):
            return result
        logger.warning(
            "OpenClaw gateway timed out on the first browser call for task %s; "
            "retrying once with a fresh browser session",
            task_info["task_id"],
        )
        shutil.rmtree(task_workspace / ".openclaw-state", ignore_errors=True)
        return self._run_once(task_info, agent_config, task_workspace)

    @staticmethod
    def _is_first_call_gateway_timeout(result: AgentResult | dict[str, Any]) -> bool:
        if not isinstance(result, AgentResult):
            return False
        return bool(result.error and _GATEWAY_TIMEOUT_ERROR in result.error)

    def _run_once(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """One OpenClaw attempt on its own browser session."""
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
        """Fail fast instead of silently launching a managed local browser."""
        return AgentResult(
            task_id=task_id,
            timestamp=datetime.now(UTC),
            env_status="failed",  # type: ignore[arg-type]
            agent_done="error",  # type: ignore[arg-type]
            error=(
                f"Browser backend '{browser_id}' (transport={transport}) provides no CDP "
                "endpoint, so the openclaw agent cannot attach its browser tool to it. "
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
        rules = agent_config.get("system_prompt") or _DEFAULT_RULES
        model = agent_config.get("model_id") or agent_config.get("model", "gpt-5.4")
        timeout = self._resolve_timeout(task_id, agent_config)

        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        state_dir = task_workspace / ".openclaw-state"
        self._write_state_config(agent_config, task_workspace, state_dir, model, cdp_url)
        cmd = self._build_command(f"{rules}\n\n{prompt}", task_id, timeout, agent_config)

        env = {**os.environ}
        env["OPENCLAW_STATE_DIR"] = str(state_dir)
        env["OPENCLAW_CONFIG_PATH"] = str(state_dir / "openclaw.json")

        logger.info(
            "Executing OpenClaw for task %s (model=%s, timeout=%ds)", task_id, model, timeout
        )
        t_start = time.monotonic()

        def _has_result(lines: list[str]) -> bool:
            return (
                _stdout_json(lines) is not None
                or self._workspace_json(task_workspace) is not None
                or self._session_result(task_workspace, task_id) is not None
            )

        try:
            returncode, stdout_lines, execution_error = self._run_subprocess(
                cmd,
                timeout=timeout,
                task_workspace=task_workspace,
                cwd=task_workspace,
                env=env,
                collect_stdout=True,
                # The result JSON is sometimes emitted on stderr only.
                collect_stderr_as_stdout=True,
                stderr_line_hook=_stderr_hook,
                # The CLI keeps running after the turn (embedded browser
                # service); terminate as soon as the result JSON is complete.
                stop_predicate=_has_result,
                # OpenClaw spawns openclaw-agent and browser/gateway helpers.
                # Kill the whole group once the result exists, otherwise those
                # helpers can keep the benchmark runner alive.
                terminate_process_group=True,
                early_stop_grace_seconds=float(agent_config.get("early_stop_grace_seconds", 2)),
                kill_grace_seconds=float(agent_config.get("kill_grace_seconds", 5)),
            )
        except FileNotFoundError:
            return AgentResult(
                task_id=task_id,
                timestamp=datetime.now(UTC),
                env_status="failed",  # type: ignore[arg-type]
                agent_done="error",  # type: ignore[arg-type]
                error=(
                    "Executable 'openclaw' not found. "
                    "Please install OpenClaw: npm install -g openclaw"
                ),
                metrics=AgentMetrics(end_to_end_ms=0, steps=0),
            )
        finally:
            # Every exit path must remove the provider apiKey from the
            # per-task config so secrets never persist in task artifacts.
            self._scrub_state_secrets(task_workspace)
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
    def _write_state_config(
        agent_config: dict[str, Any],
        task_workspace: Path,
        state_dir: Path,
        model: str,
        cdp_url: str | None,
    ) -> None:
        """Write the per-task openclaw.json (provider, workspace, tools, browser)."""
        state_dir.mkdir(parents=True, exist_ok=True)
        provider_api = str(agent_config.get("api") or "openai-completions")
        # OpenClaw's auto-detection disables streaming usage for custom
        # providers, which zeroes all token accounting; the bench gateway
        # supports stream_options.include_usage, so opt in.
        compat: dict[str, Any] = {"supportsUsageInStreaming": True}
        if agent_config.get("supports_reasoning_effort") is not None:
            compat["supportsReasoningEffort"] = bool(agent_config.get("supports_reasoning_effort"))
        model_def: dict[str, Any] = {
            "id": model,
            "name": model,
            "api": provider_api,
            "reasoning": bool(agent_config.get("reasoning", False)),
            "input": ["text"],
            "contextWindow": int(agent_config.get("context_window", 195000)),
            "maxTokens": int(agent_config.get("max_tokens", 16000)),
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "compat": compat,
        }
        config: dict[str, Any] = {
            "models": {
                "mode": "merge",
                "providers": {
                    "bench": {
                        "baseUrl": agent_config.get("base_url", ""),
                        "apiKey": agent_config.get("api_key", ""),
                        # No timeoutSeconds: the current OpenClaw CLI rejects
                        # it in the provider config schema.
                        "api": provider_api,
                        "models": [model_def],
                    }
                },
            },
            "agents": {
                "defaults": {
                    "model": {"primary": f"bench/{model}"},
                    # Subdirectory, not the task workspace itself: OpenClaw
                    # bootstraps template files (SOUL.md, AGENTS.md, ...) into
                    # its workspace, which must not pollute task artifacts.
                    "workspace": str(task_workspace / ".openclaw-workspace"),
                },
                # Tool whitelist: browsing plus reading bundled skill files.
                "list": [{"id": "main", "tools": {"allow": ["browser", "read"]}}],
            },
            "browser": {"enabled": True},
            # Never route browser calls through gateway node proxies: a call
            # with no explicit target otherwise consults gateway node.list,
            # which fails on machines without gateway credentials.
            "gateway": {"nodes": {"browser": {"mode": "off"}}},
        }
        if cdp_url:
            bench_profile = {"cdpUrl": cdp_url, "attachOnly": True, "color": "#00AA00"}
            config["browser"] = {
                "enabled": True,
                "defaultProfile": "bench",
                # Also pin the built-in profile names: OpenClaw otherwise
                # injects "user" (attach the operator's local Chrome) and
                # "openclaw" (managed local Chrome), and the model sometimes
                # requests them explicitly, escaping the bench browser.
                "profiles": {
                    "bench": bench_profile,
                    "user": dict(bench_profile),
                    "openclaw": dict(bench_profile),
                },
            }
        (state_dir / "openclaw.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _build_command(
        full_prompt: str,
        task_id: str,
        timeout: int,
        agent_config: dict[str, Any],
    ) -> list[str]:
        exe = "openclaw.cmd" if IS_WINDOWS else "openclaw"
        cmd = [
            # --dev keeps the embedded gateway/browser-control ports off the
            # defaults, so bench runs never collide with an operator's own
            # OpenClaw app; state still goes to OPENCLAW_STATE_DIR.
            exe, "--dev", "agent",
            "--local",   # embedded agent turn, no Gateway service required
            "--json",
            "--agent", "main",
            "--session-id", f"bench-{task_id}",
            "-m", full_prompt,
            "--timeout", str(timeout),
        ]
        thinking = _normalize_thinking_level(
            agent_config.get("thinking")
            or agent_config.get("reasoning_effort")
            or agent_config.get("thinking_effort")
        )
        if thinking:
            cmd += ["--thinking", thinking]
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
    ) -> AgentResult:
        result_obj = (
            _stdout_json(stdout_lines)
            or self._workspace_json(task_workspace)
            or self._session_result(task_workspace, task_id)
            or {}
        )
        payloads = result_obj.get("payloads")
        answer = ""
        if isinstance(payloads, list):
            answer = "\n".join(
                str(p.get("text", "")) for p in payloads if isinstance(p, dict) and p.get("text")
            ).strip()

        if execution_error and "Timeout" in execution_error:
            logger.error("OpenClaw task %s timed out", task_id)
        env_status, agent_done = self._map_exit_status(
            returncode, execution_error, has_result=bool(answer)
        )
        error_message = execution_error
        if agent_done != "timeout" and not result_obj:
            env_status, agent_done = "failed", "error"
            error_message = error_message or (
                "No result JSON from OpenClaw: " + "".join(stdout_lines)[-500:].strip()
            ).strip(": ")
        if env_status == "failed" and not answer:
            answer = f"[Task Failed: {error_message or 'No result JSON from OpenClaw'}]"

        items = self._session_items(result_obj, task_workspace)
        saved_screenshots = _collect_media_screenshots(items, trajectory_dir)
        steps = sum(1 for item in items if item.get("type") in STEP_ITEM_TYPES)
        if steps <= _GATEWAY_TIMEOUT_MAX_STEPS and _first_browser_call_gateway_timeout(items):
            # The agent only wrapped the outage into a text answer; this is an
            # environment failure, never env_status=success.
            env_status, agent_done = "failed", "error"
            error_message = _GATEWAY_TIMEOUT_ERROR
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
            error=error_message if env_status == "failed" else None,
            action_history=extract_actions(items),
            screenshots=saved_screenshots,
            model_id=model,
            metrics=AgentMetrics(
                end_to_end_ms=duration_ms, steps=steps, usage=self._usage_from(result_obj)
            ),
        )

    @staticmethod
    def _scrub_state_secrets(task_workspace: Path) -> None:
        """Redact the provider apiKey from the per-task config left in artifacts."""
        config_path = task_workspace / ".openclaw-state" / "openclaw.json"
        if not config_path.is_file():
            return
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            for provider in config.get("models", {}).get("providers", {}).values():
                if isinstance(provider, dict) and provider.get("apiKey"):
                    provider["apiKey"] = "***"
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to scrub state config secrets: %s", exc)

    @staticmethod
    def _workspace_json(task_workspace: Path) -> dict[str, Any] | None:
        """Recover the result JSON from the drained stdout.txt / stderr.txt."""
        lines: list[str] = []
        for name in ("stdout.txt", "stderr.txt"):
            path = task_workspace / name
            if not path.is_file():
                continue
            try:
                lines.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
        return _stdout_json(lines)

    @staticmethod
    def _session_result(task_workspace: Path, task_id: str) -> dict[str, Any] | None:
        """Recover a completed answer from OpenClaw's session JSONL.

        Some OpenClaw CLI runs finish the agent turn and write the final
        assistant text to the session file, but never emit the ``--json``
        payload to stdout or exit. Treat the last assistant text-only message
        as the terminal answer so the benchmark can terminate the lingering
        process group and continue.
        """
        session_id = f"bench-{task_id}"
        session_file = (
            task_workspace / ".openclaw-state" / "agents" / "main" / "sessions"
            / f"{session_id}.jsonl"
        )
        if not session_file.is_file():
            return None
        try:
            raw_lines = session_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

        answer = ""
        usage: dict[str, Any] = {}
        for raw_line in raw_lines:
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            message = obj.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            has_tool_call = any(
                isinstance(block, dict) and block.get("type") == "toolCall" for block in content
            )
            texts = [
                str(block.get("text", ""))
                for block in content
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
            ]
            if texts and not has_tool_call:
                answer = "\n".join(texts).strip()
                raw_usage = message.get("usage")
                usage = raw_usage if isinstance(raw_usage, dict) else {}
        if not answer:
            return None

        agent_meta: dict[str, Any] = {
            "sessionId": session_id,
            "sessionFile": str(session_file),
        }
        total_tokens = safe_int(usage.get("total") or usage.get("totalTokens"))
        if total_tokens:
            agent_meta["lastCallUsage"] = {
                "input": safe_int(usage.get("input")),
                "output": safe_int(usage.get("output")),
                "cacheRead": safe_int(usage.get("cacheRead")),
                "cacheWrite": safe_int(usage.get("cacheWrite")),
                "total": total_tokens,
            }
        return {"payloads": [{"text": answer}], "meta": {"agentMeta": agent_meta}}

    @staticmethod
    def _agent_meta(result_obj: dict[str, Any]) -> dict[str, Any] | None:
        meta = result_obj.get("meta")
        agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
        return agent_meta if isinstance(agent_meta, dict) else None

    @staticmethod
    def _session_file_from(result_obj: dict[str, Any]) -> Path | None:
        agent_meta = OpenClawAgent._agent_meta(result_obj)
        session_file = agent_meta.get("sessionFile") if agent_meta else None
        return Path(str(session_file)) if session_file else None

    @staticmethod
    def _session_items(result_obj: dict[str, Any], task_workspace: Path) -> list[dict[str, Any]]:
        session_file = OpenClawAgent._session_file_from(result_obj)
        if session_file is not None:
            return _normalize_session_items(session_file)
        agent_meta = OpenClawAgent._agent_meta(result_obj)
        session_id = agent_meta.get("sessionId") if agent_meta else None
        if not session_id:
            return []
        fallback = (
            task_workspace / ".openclaw-state" / "agents" / "main" / "sessions"
            / f"{session_id}.jsonl"
        )
        return _normalize_session_items(fallback)

    @staticmethod
    def _usage_from(result_obj: dict[str, Any]) -> AgentUsage | None:
        totals = _collect_session_usage(OpenClawAgent._session_file_from(result_obj))
        last_call: Any = None
        if not totals["entries"]:
            # lastCallUsage covers only the final LLM call; use it only when
            # the session log carries no per-message usage at all.
            agent_meta = OpenClawAgent._agent_meta(result_obj)
            last_call = agent_meta.get("lastCallUsage") if agent_meta else None
            _fold_openclaw_usage(last_call, totals)
        if not totals["entries"]:
            # Degenerate lastCallUsage with only an aggregate total: keep the
            # total token count rather than dropping usage entirely.
            total_tokens = safe_int(last_call.get("total")) if isinstance(last_call, dict) else 0
            if not total_tokens:
                return None
            return AgentUsage(total_tokens=total_tokens, entry_count=1)
        return AgentUsage(
            total_prompt_tokens=totals["prompt"],
            total_prompt_cached_tokens=totals["cached"],
            total_prompt_cache_creation_tokens=totals["cache_creation"],
            total_completion_tokens=totals["completion"],
            entry_count=totals["entries"],
        )


def _stderr_hook(line: str) -> None:
    clean = line.strip()
    if clean and ("error" in clean.lower() or "FailoverError" in clean):
        logger.warning("[OpenClaw] %s", clean)
