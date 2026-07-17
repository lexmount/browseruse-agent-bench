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
import socket
import subprocess
import time
from datetime import UTC, datetime
from functools import cache
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
from browseruse_bench.utils.image_utils import decode_base64_to_file
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
    "- EXCEPTION: browser tool CONNECTION errors (gateway credentials/closed/not ready/"
    "timed out) are transient while the browser service finishes starting. Retry the "
    "same browser call up to 5 times before concluding the browser is unavailable; "
    "for open/navigate retries pass timeoutMs: 60000 to outlast startup.\n"
    "\n\nScreenshot rules:\n"
    "- Take a screenshot with the browser screenshot action after navigating to the main "
    "page and after finding the answer."
)

_MEDIA_PATH_RE = re.compile(r"MEDIA:(\S+)")

_MEDIA_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpeg", "image/jpg": ".jpg"}

# OpenClaw also resolves Anthropic credentials/endpoints from non-*_API_KEY
# vars (ANTHROPIC_OAUTH_TOKEN, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, ...),
# so the whole prefix is scrubbed.
_PROVIDER_ENV_PREFIX = "ANTHROPIC_"


def _subprocess_env(state_dir: Path) -> dict[str, str]:
    """Build the OpenClaw subprocess env without provider-autodetect vars.

    The bench provider's credentials are delivered via the written
    openclaw.json; any *_API_KEY (or ANTHROPIC_* credential/endpoint var)
    inherited from the parent env makes OpenClaw auto-detect an extra provider
    and route media understanding through it, which fails on the bench gateway.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.endswith("_API_KEY") and not key.startswith(_PROVIDER_ENV_PREFIX)
    }
    env["OPENCLAW_STATE_DIR"] = str(state_dir)
    env["OPENCLAW_CONFIG_PATH"] = str(state_dir / "openclaw.json")
    return env


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


# Local fake-IP proxy clients (Clash/Surge etc.) resolve proxied domains to the
# RFC 2544 benchmark range (198.18.0.0/15); OpenClaw's local SSRF preflight then
# blocks navigation ("browser navigation blocked by policy") even though the
# real navigation happens in the remote CDP browser, where local private-network
# concerns do not apply.
_BROWSER_SSRF_POLICY = {"dangerouslyAllowPrivateNetwork": True}

# Browser-tool connection failures caused by the service startup race; a run
# whose EVERY browser call died this way never had a browser and its answer is
# a blocked notice, not a result.
_BROWSER_OUTAGE_SIGNATURES = (
    "gateway node.list requires credentials",
    "browser endpoint blocked by policy",
    "gateway closed (1006",
    "timed out. Restart the OpenClaw gateway",
)


def _match_outage(text: str) -> str | None:
    for signature in _BROWSER_OUTAGE_SIGNATURES:
        if signature in text:
            return signature
    return None


def _detect_browser_outage(items: list[dict[str, Any]], answer: str) -> str | None:
    """Return the matched outage signature when the run never had a browser.

    Outage means every browser tool result carries a connection-failure
    signature (one successful call disproves it); with no browser items at
    all, fall back to scanning the final answer text.
    """
    browser_matches: list[str | None] = []
    for item in items:
        if not str(item.get("tool", "")).startswith("browser"):
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            # The toolResult never arrived (turn aborted, stop_predicate raced
            # the session write): unknown, not evidence of a working browser.
            continue
        content = result.get("content") or []
        text = " ".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict)
        )
        browser_matches.append(_match_outage(text))
    if browser_matches:
        return browser_matches[0] if all(browser_matches) else None
    return _match_outage(answer)


def _browser_config(cdp_url: str | None) -> dict[str, Any]:
    """Build the per-task browser section of openclaw.json."""
    if not cdp_url:
        # Locally launched browser: keep OpenClaw's SSRF preflight intact.
        return {"enabled": True}
    bench_profile = {"cdpUrl": cdp_url, "attachOnly": True, "color": "#00AA00"}
    return {
        "enabled": True,
        # The SSRF preflight resolves DNS locally, but navigation happens in
        # the REMOTE CDP browser where local private-network concerns do not
        # apply — and local fake-IP proxy clients resolve proxied domains to
        # the RFC 2544 range, which the guard would reject.
        "ssrfPolicy": _BROWSER_SSRF_POLICY,
        "defaultProfile": "bench",
        # OpenClaw auto-injects built-in `user`/`openclaw` profiles
        # (operator's local Chrome) unless the config defines those names;
        # models sometimes pass them explicitly and escape the bench
        # browser. Pin both to the bench CDP endpoint.
        "profiles": {
            "bench": bench_profile,
            "user": bench_profile,
            "openclaw": bench_profile,
        },
    }


def _allocate_free_port() -> int:
    """Reserve an ephemeral TCP port family for a task's private OpenClaw gateway.

    OpenClaw binds a small family around the gateway port (browser control
    service on port+2), so probe both before handing the base out. Best
    effort: the probe sockets are closed before OpenClaw binds, so a race
    remains possible but requires another process to grab the exact port in
    the spawn window.
    """
    base = 0
    for _ in range(20):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            base = sock.getsockname()[1]
            try:
                with socket.socket() as neighbor:
                    neighbor.bind(("127.0.0.1", base + 2))
            except OSError:
                continue
            return base
    return base


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
            continue
        inline = _inline_image_data(block)
        if inline:
            item.setdefault("inline_media", []).append(inline)
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


def _inline_image_data(block: dict[str, Any]) -> tuple[str, str] | None:
    """Return (mime_type, base64_data) from an inline image/media block."""
    if block.get("type") not in ("image", "media"):
        return None
    data = block.get("data")
    if not isinstance(data, str) or not data:
        return None
    return str(block.get("mimeType") or "image/png"), data


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
    """Save screenshots referenced by tool results into trajectory/.

    Handles both MEDIA:<path> file references and inline base64 image blocks
    (popped off the item so the raw data never reaches api_logs).
    """
    saved: list[str] = []
    for item in items:
        result = item.get("result")
        if isinstance(result, dict):
            for block in result.get("content", []):
                if isinstance(block, dict):
                    _copy_media_paths(str(block.get("text", "")), trajectory_dir, saved)
        for mime, data in item.pop("inline_media", []):
            _save_inline_media(mime, data, trajectory_dir, saved)
    return saved


def _save_inline_media(mime: str, data: str, trajectory_dir: Path, saved: list[str]) -> None:
    ext = _MEDIA_MIME_EXT.get(mime.lower())
    if ext is None:
        logger.debug("Skipping inline media with unsupported mime type: %s", mime)
        return
    fname = f"screenshot-{len(saved) + 1}{ext}"
    try:
        trajectory_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Failed to create trajectory dir for %s: %s", fname, exc)
        return
    if decode_base64_to_file(data, trajectory_dir / fname):
        saved.append(fname)


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


def _terminal_assistant_answer(raw_lines: list[str]) -> tuple[str, dict[str, Any]]:
    """Return (answer, usage) from the last text-only assistant message.

    Terminal means nothing but tool plumbing follows it: a tool call AFTER a
    text-only message marks that text as mid-turn commentary, not an answer
    (returning it would let the stop predicate kill a healthy run).
    """
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
        if has_tool_call:
            answer, usage = "", {}
            continue
        texts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        if texts:
            answer = "\n".join(texts).strip()
            raw_usage = message.get("usage")
            usage = raw_usage if isinstance(raw_usage, dict) else {}
    return answer, usage


def _openclaw_exe() -> str:
    """The one place that knows the CLI executable name per platform."""
    return "openclaw.cmd" if IS_WINDOWS else "openclaw"


def _session_file_path(task_workspace: Path, session_id: str) -> Path:
    """The one place that knows OpenClaw's per-task session JSONL layout."""
    return (
        task_workspace / ".openclaw-state" / "agents" / "main" / "sessions"
        / f"{session_id}.jsonl"
    )


@cache
def _openclaw_cli_version() -> str:
    """One `openclaw --version` per process; compat failures (e.g. rejected
    CLI flags or config fields) are version-dependent, so every result must
    record which CLI it ran against."""
    exe = _openclaw_exe()
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not determine OpenClaw CLI version: %s", exc)
        return "unknown"
    lines = (proc.stdout or proc.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else "unknown"


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

        Retries once on a fresh browser session when the run lost the
        browser-service startup race (every browser call failed with a
        connection error and the "answer" is just a blocked notice).
        """
        cli_version = self._cli_version()
        logger.info("OpenClaw CLI version: %s", cli_version)
        retries = max(0, safe_int(agent_config.get("outage_retries", 1), 1))
        result = self._attempt_task(task_info, agent_config, task_workspace, attempt=0)
        for attempt in range(1, retries + 1):
            outage = (
                result.agent_metadata.get("browser_outage")
                if isinstance(result, AgentResult)
                else None
            )
            if not outage:
                break
            logger.warning(
                "OpenClaw browser outage on task %s (%s); retrying on a fresh browser session",
                task_info.get("task_id"),
                outage,
            )
            result = self._attempt_task(task_info, agent_config, task_workspace, attempt=attempt)
            if isinstance(result, AgentResult) and not result.agent_metadata.get("browser_outage"):
                result.agent_metadata["outage_retried"] = attempt
        if isinstance(result, AgentResult):
            result.agent_metadata["openclaw_cli_version"] = cli_version
        return result

    # Class attribute so tests can patch/clear the process-wide cache.
    _cli_version = staticmethod(_openclaw_cli_version)

    def _attempt_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
        attempt: int,
    ) -> AgentResult | dict[str, Any]:
        """Run one OpenClaw attempt against a freshly opened browser session."""
        browser_id = str(agent_config.get("browser_id") or "")
        if browser_id in SELF_LAUNCH_BROWSER_IDS:
            warn_if_local_proxy_unsupported(agent_config, self.name)
            return self._execute(task_info, agent_config, task_workspace, cdp_url=None, attempt=attempt)
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
            return self._execute(
                task_info, agent_config, task_workspace, cdp_url=cdp_url, attempt=attempt
            )

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
        attempt: int = 0,
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
        cmd = self._build_command(f"{rules}\n\n{prompt}", task_id, timeout, agent_config, attempt)

        env = _subprocess_env(state_dir)
        # Isolate the per-process gateway: concurrent tasks sharing the
        # default port 18789 attach to each other's gateway and fail browser
        # auth ("gateway node.list requires credentials"). Do NOT pre-set
        # OPENCLAW_GATEWAY_TOKEN — configured credentials make OpenClaw treat
        # the gateway as external and skip starting its in-process browser
        # control service entirely (calls then die with 1006 closures).
        env["OPENCLAW_GATEWAY_PORT"] = str(_allocate_free_port())

        logger.info(
            "Executing OpenClaw for task %s (model=%s, timeout=%ds)", task_id, model, timeout
        )
        t_start = time.monotonic()

        session_id = self._session_id(task_id, attempt)
        # The predicate runs per output line; the filesystem fallbacks
        # (re-reading stdout.txt/stderr.txt and parsing the session JSONL)
        # are too expensive for that cadence, so probe them at most once a
        # second — the wait loop re-evaluates every 0.25s regardless.
        next_probe_at = [0.0]

        def _has_result(lines: list[str]) -> bool:
            if _stdout_json(lines) is not None:
                return True
            now = time.monotonic()
            if now < next_probe_at[0]:
                return False
            next_probe_at[0] = now + 1.0
            return (
                self._workspace_json(task_workspace) is not None
                or self._session_result(task_workspace, session_id) is not None
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
            session_id=session_id,
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
        if agent_config.get("llm_timeout"):
            logger.warning(
                "llm_timeout is ignored: provider timeoutSeconds was dropped for "
                "OpenClaw CLI config-schema compatibility"
            )
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
            # When use_vision is on, declare the primary model natively
            # multimodal so OpenClaw passes browser screenshots straight to it
            # (docs/nodes/images: "if the active primary image model already
            # supports vision natively, OpenClaw ... passes the original image
            # to the model") instead of auto-detecting a separate image model
            # that the bench gateway does not serve.
            "input": ["text", "image"] if agent_config.get("use_vision") else ["text"],
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
            "browser": _browser_config(cdp_url),
            # Media image understanding is off by default: without use_vision
            # the primary model is declared text-only, so a screenshot would
            # make OpenClaw auto-detect a separate image model and burn a
            # failing LLM call per image on the bench gateway. With use_vision
            # the primary model is declared multimodal (see input above), so
            # OpenClaw routes screenshots straight to it.
            "tools": {"media": {"image": {"enabled": bool(agent_config.get("use_vision"))}}},
            # Default "auto" consults gateway node.list before the in-process
            # browser service; without gateway credentials every browser call
            # fails ("gateway node.list requires credentials before opening a
            # websocket"). Force local dispatch.
            "gateway": {"nodes": {"browser": {"mode": "off"}}},
        }
        (state_dir / "openclaw.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _session_id(task_id: str, attempt: int = 0) -> str:
        """Per-attempt session id: reusing one across retries resumes the
        failed attempt's transcript, so the model sees its own browser
        failures and gives up — and metrics double-count both attempts."""
        session_id = f"bench-{task_id}"
        return f"{session_id}-r{attempt}" if attempt else session_id

    @staticmethod
    def _build_command(
        full_prompt: str,
        task_id: str,
        timeout: int,
        agent_config: dict[str, Any],
        attempt: int = 0,
    ) -> list[str]:
        exe = _openclaw_exe()
        cmd = [
            # --dev keeps the embedded gateway/browser-control ports off the
            # defaults, so bench runs never collide with an operator's own
            # OpenClaw app; state still goes to OPENCLAW_STATE_DIR and the
            # gateway port to OPENCLAW_GATEWAY_PORT.
            exe, "--dev", "agent",
            "--local",   # embedded agent turn, no Gateway service required
            "--json",
            "--agent", "main",
            "--session-id", OpenClawAgent._session_id(task_id, attempt),
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
        session_id: str,
    ) -> AgentResult:
        result_obj = (
            _stdout_json(stdout_lines)
            or self._workspace_json(task_workspace)
            or self._session_result(task_workspace, session_id)
            or {}
        )
        payloads = result_obj.get("payloads")
        answer = ""
        if isinstance(payloads, list):
            answer = "\n".join(
                str(p.get("text", "")) for p in payloads if isinstance(p, dict) and p.get("text")
            ).strip()
        if result_obj and not answer and returncode in (0, None) and execution_error is None:
            # Aborted runs can emit the --json payload with text="" while the
            # real final answer only exists in the session JSONL. Recover it
            # only for clean exits: on failed runs the "[Task Failed: ...]"
            # sentinel must survive, and a recovered answer must never flip
            # has_result for a crashed CLI.
            recovered = self._session_result(task_workspace, session_id)
            if recovered:
                answer = str(recovered["payloads"][0].get("text") or "")

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

        items = self._session_items(result_obj, task_workspace, session_id)
        # Must run before write_api_logs: it pops inline base64 blobs off the
        # items so they never reach the api_logs artifacts.
        saved_screenshots = _collect_media_screenshots(items, trajectory_dir)
        steps = sum(1 for item in items if item.get("type") in STEP_ITEM_TYPES)
        if items:
            try:
                write_api_logs(task_id, model, rules, items, task_workspace / "api_logs")
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("Failed to generate api_logs for task %s: %s", task_id, exc)

        # A "successful" run whose browser never worked is a false success:
        # the answer is a blocked notice, not a task result. Timeouts are
        # excluded — flipping them would corrupt timeout stats and burn a
        # second full timeout budget on retry.
        agent_metadata: dict[str, Any] = {}
        outage = (
            _detect_browser_outage(items, answer)
            if env_status == "success" and agent_done == "done"
            else None
        )
        if outage:
            env_status, agent_done = "failed", "error"
            error_message = f"OpenClaw browser tool unavailable ({outage})"
            agent_metadata["browser_outage"] = outage
            if not answer:
                answer = f"[Task Failed: {error_message}]"

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
            agent_metadata=agent_metadata,
            metrics=AgentMetrics(
                end_to_end_ms=duration_ms,
                steps=steps,
                usage=self._usage_from(result_obj, task_workspace, session_id),
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
    def _session_result(task_workspace: Path, session_id: str) -> dict[str, Any] | None:
        """Recover a completed answer from OpenClaw's session JSONL.

        Some OpenClaw CLI runs finish the agent turn and write the final
        assistant text to the session file, but never emit the ``--json``
        payload to stdout or exit. Treat the last assistant text-only message
        as the terminal answer so the benchmark can terminate the lingering
        process group and continue.
        """
        session_file = _session_file_path(task_workspace, session_id)
        if not session_file.is_file():
            return None
        try:
            raw_lines = session_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        answer, usage = _terminal_assistant_answer(raw_lines)
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
    def _resolve_session_file(
        result_obj: dict[str, Any], task_workspace: Path, session_id: str
    ) -> Path | None:
        """Locate the session JSONL for a result.

        Prefer the paths the CLI attested in agentMeta, then fall back to the
        deterministic per-task layout (timed-out runs killed mid tool-loop
        leave no result_obj at all). Candidates that do not exist on disk are
        skipped rather than short-circuiting the fallback chain.
        """
        agent_meta = OpenClawAgent._agent_meta(result_obj) or {}
        candidates: list[Path] = []
        if agent_meta.get("sessionFile"):
            candidates.append(Path(str(agent_meta["sessionFile"])))
        if agent_meta.get("sessionId"):
            candidates.append(_session_file_path(task_workspace, str(agent_meta["sessionId"])))
        if session_id:
            candidates.append(_session_file_path(task_workspace, session_id))
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _session_items(
        result_obj: dict[str, Any], task_workspace: Path, session_id: str
    ) -> list[dict[str, Any]]:
        session_file = OpenClawAgent._resolve_session_file(result_obj, task_workspace, session_id)
        return _normalize_session_items(session_file) if session_file else []

    @staticmethod
    def _usage_from(
        result_obj: dict[str, Any], task_workspace: Path, session_id: str
    ) -> AgentUsage | None:
        session_file = OpenClawAgent._resolve_session_file(result_obj, task_workspace, session_id)
        totals = _collect_session_usage(session_file)
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
