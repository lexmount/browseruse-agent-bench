"""
HermesAgent - Browser automation using the Hermes Agent CLI (Nous Research).

This agent executes tasks by invoking `hermes -z <prompt>` (oneshot mode: one
non-interactive agent conversation, no gateway, exits when done). Browsing
uses Hermes's built-in `browser` toolset attached to an external CDP endpoint
via the BROWSER_CDP_URL env var, or Hermes's own managed local browser for
self-launch backends. Each task gets an isolated HERMES_HOME state directory;
the provider API key is delivered via an env var and never written to disk.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

import yaml

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
from browseruse_bench.utils.parse_utils import safe_int

logger = logging.getLogger(__name__)

# Shared rule prefix for every run; the final reading rule differs by mode.
_RULES_PREFIX = (
    "You are a browser automation agent. "
    "You MUST use ONLY the browser_* tools for ALL browser interactions."
    "\n\nTask completion rules:\n"
    "- If you can see enough information to answer the task from the current page (e.g., "
    "ratings, names, prices visible in search results), provide your answer IMMEDIATELY "
    "without clicking into individual items to get more detail.\n"
    "- If you encounter a CAPTCHA, verification page, login wall, or access restriction: "
    "go back to the previous page and use the data already collected to answer.\n"
    "- Do NOT get stuck retrying the same blocked action. One retry max, then fall back.\n"
)

_DEFAULT_RULES = _RULES_PREFIX + (
    "- Read pages with browser_snapshot. Do NOT use browser_vision or browser_get_images: "
    "no vision model is available in this environment, image analysis always fails."
)

# Rules for use_vision runs: browser_vision routes to the main bench model
# (Hermes auxiliary auto mode resolves to the main provider first), so it works
# whenever that model is multimodal. Vision is evidence capture on top of
# snapshot-driven reading, not a replacement for it.
_VISION_RULES = _RULES_PREFIX + (
    "- Read pages with browser_snapshot as your primary tool. Additionally, call "
    "browser_vision once on each page that contains evidence for your final answer, so "
    "the run keeps a visual record. Trust browser_snapshot text over the vision summary "
    "when they disagree."
)

# gpt-5-family models on the gateway reject any temperature other than 1, while
# Hermes's browser_vision defaults to temperature=0.1; vision runs therefore
# pin auxiliary.vision.temperature (config key vision_temperature to override).
_DEFAULT_VISION_TEMPERATURE = 1.0


def _rules_for(agent_config: dict[str, Any]) -> str:
    """Pick the system rules for a run: explicit config wins, then vision mode."""
    explicit = agent_config.get("system_prompt")
    if explicit:
        return str(explicit)
    return _VISION_RULES if agent_config.get("use_vision") else _DEFAULT_RULES


# The name of the env var carrying the bench provider API key into the Hermes
# subprocess; referenced as key_env in the per-task config.yaml so the secret
# never touches disk.
_API_KEY_ENV = "HERMES_BENCH_API_KEY"

# Hermes auto-detects extra providers (auxiliary vision/web-extract models,
# fallback chains) from well-known env vars, which would route calls outside
# the bench provider. The bench provider is delivered via the per-task
# config.yaml + _API_KEY_ENV only, so scrub the whole families. HERMES_* is
# scrubbed to drop operator overrides (HERMES_INFERENCE_MODEL, ...); BROWSER_*
# to drop a stale BROWSER_CDP_URL before the per-task one is set.
_SCRUB_ENV_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_",
    "OPENROUTER_",
    "GOOGLE_",
    "GEMINI_",
    "NOUS_",
    "OLLAMA_",
    "HERMES_",
    "BROWSER_",
)

_SCREENSHOTS_SUBDIR = Path("cache") / "screenshots"


def _subprocess_env(state_dir: Path, cdp_url: str | None, api_key: str) -> dict[str, str]:
    """Build the Hermes subprocess env: scrubbed, isolated, CDP-attached."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.endswith("_API_KEY") and not key.startswith(_SCRUB_ENV_PREFIXES)
    }
    env["HERMES_HOME"] = str(state_dir)
    env[_API_KEY_ENV] = api_key
    if cdp_url:
        env["BROWSER_CDP_URL"] = cdp_url
    return env


def _state_config(model: str, base_url: str, agent_config: dict[str, Any]) -> dict[str, Any]:
    """Build the per-task HERMES_HOME/config.yaml content."""
    agent_section: dict[str, Any] = {"verbose": False}
    reasoning_effort = agent_config.get("reasoning_effort")
    if reasoning_effort:
        agent_section["reasoning_effort"] = str(reasoning_effort)
    config: dict[str, Any] = {
        "model": {"default": model, "provider": "bench"},
        "providers": {"bench": {"base_url": base_url, "key_env": _API_KEY_ENV}},
        "agent": agent_section,
    }
    if agent_config.get("use_vision"):
        # `or` (not the .get default) so an explicit null/empty in config falls
        # back instead of crashing float(None).
        raw_temperature = agent_config.get("vision_temperature") or _DEFAULT_VISION_TEMPERATURE
        config["auxiliary"] = {"vision": {"temperature": float(raw_temperature)}}
    return config


def _write_state_config(
    state_dir: Path, model: str, base_url: str, agent_config: dict[str, Any]
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    config = _state_config(model, base_url, agent_config)
    (state_dir / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )


def _read_usage_report(path: Path) -> dict[str, Any] | None:
    """Read the JSON usage report written by `hermes -z --usage-file`."""
    if not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read Hermes usage report %s: %s", path, exc)
        return None
    return report if isinstance(report, dict) else None


def _usage_from_report(report: dict[str, Any] | None) -> AgentUsage | None:
    """Map the oneshot usage report to AgentUsage.

    Hermes normalizes every wire format to DISJOINT buckets: its
    ``input_tokens`` EXCLUDES ``cache_read_tokens``/``cache_write_tokens``
    (see hermes-agent ``agent/usage_pricing.py``). Fold the cache counters
    into the prompt count to match the AgentUsage convention.
    """
    if not report:
        return None
    input_tokens = safe_int(report.get("input_tokens"))
    cache_read = safe_int(report.get("cache_read_tokens"))
    cache_write = safe_int(report.get("cache_write_tokens"))
    completion = safe_int(report.get("output_tokens"))
    if input_tokens + cache_read + cache_write + completion == 0:
        return None
    return AgentUsage(
        total_prompt_tokens=input_tokens + cache_read + cache_write,
        total_prompt_cached_tokens=cache_read,
        total_prompt_cache_creation_tokens=cache_write,
        total_completion_tokens=completion,
        entry_count=safe_int(report.get("api_calls")),
    )


def _fold_tool_calls(
    raw: str, items: list[dict[str, Any]], by_call_id: dict[str, dict[str, Any]]
) -> None:
    """Append one assistant message's tool calls to *items* in the shared shape."""
    try:
        calls = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(calls, list):
        return
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        item = {
            "type": "mcp_tool_call",
            "tool": str(function.get("name") or ""),
            "arguments": _parse_arguments(function.get("arguments")),
            "status": "in_progress",
        }
        items.append(item)
        by_call_id[str(call.get("call_id") or call.get("id") or "")] = item


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _fold_rows(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """Normalize (role, content, tool_call_id, tool_calls) rows to step items."""
    items: list[dict[str, Any]] = []
    by_call_id: dict[str, dict[str, Any]] = {}
    for role, content, tool_call_id, tool_calls in rows:
        if role == "assistant" and tool_calls:
            _fold_tool_calls(str(tool_calls), items, by_call_id)
            continue
        if role != "tool":
            continue
        item = by_call_id.get(str(tool_call_id or ""))
        if item is None:
            continue
        item["status"] = "completed"
        item["result"] = {"content": [{"type": "text", "text": str(content or "")}]}
    return items


def _session_items(state_dir: Path, session_id: str | None) -> list[dict[str, Any]]:
    """Read tool calls for *session_id* from the per-task SQLite session store.

    Falls back to the newest session in the store when *session_id* is unknown
    (e.g. the run timed out before the usage report was written) — the store
    is per-task, so at most one oneshot session exists.
    """
    db_path = state_dir / "state.db"
    if not db_path.is_file():
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            resolved = session_id or _latest_session_id(conn)
            if not resolved:
                return []
            rows = conn.execute(
                "SELECT role, content, tool_call_id, tool_calls FROM messages "
                "WHERE session_id = ? ORDER BY id",
                (resolved,),
            ).fetchall()
    except sqlite3.Error as exc:
        logger.warning("Failed to read Hermes session store %s: %s", db_path, exc)
        return []
    return _fold_rows(rows)


def _latest_session_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1").fetchone()
    return str(row[0]) if row else None


def _screenshots_by_mtime(source_dir: Path) -> list[Path]:
    """Stat once per file; a file vanishing between glob and stat is skipped."""
    stamped: list[tuple[float, Path]] = []
    for path in source_dir.glob("*.png"):
        try:
            stamped.append((path.stat().st_mtime, path))
        except OSError as exc:
            logger.warning("Skipping unreadable screenshot %s: %s", path, exc)
    return [path for _, path in sorted(stamped)]


def _collect_screenshots(state_dir: Path, trajectory_dir: Path) -> list[str]:
    """Copy browser screenshots from the Hermes cache into trajectory/."""
    source_dir = state_dir / _SCREENSHOTS_SUBDIR
    if not source_dir.is_dir():
        return []
    sources = _screenshots_by_mtime(source_dir)
    if not sources:
        return []
    try:
        trajectory_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Failed to create trajectory dir %s: %s", trajectory_dir, exc)
        return []
    saved: list[str] = []
    for source in sources:
        fname = f"screenshot-{len(saved) + 1}.png"
        try:
            shutil.copyfile(source, trajectory_dir / fname)
            saved.append(fname)
        except OSError as exc:
            logger.warning("Failed to copy screenshot %s: %s", source, exc)
    return saved


@cache
def _hermes_cli_version() -> str:
    """One `hermes --version` per process; behavior is version-dependent, so
    every result must record which CLI it ran against."""
    try:
        proc = subprocess.run(
            ["hermes", "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not determine Hermes CLI version: %s", exc)
        return "unknown"
    lines = (proc.stdout or proc.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else "unknown"


@register_agent
class HermesAgent(CLIAgent):
    """
    Browser automation agent using the Hermes Agent CLI.

    Hermes is invoked as an external process via `hermes -z` (oneshot mode)
    with HERMES_HOME pointed at a per-task directory (the operator's ~/.hermes
    is never touched). The `browser` toolset drives either an external CDP
    endpoint (BROWSER_CDP_URL) or Hermes's managed local browser.
    Install first: curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
    """

    name = "hermes"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a browser automation task using the Hermes CLI."""
        cli_version = self._cli_version()
        logger.info("Hermes CLI version: %s", cli_version)
        browser_id = str(agent_config.get("browser_id") or "")
        if browser_id in SELF_LAUNCH_BROWSER_IDS:
            warn_if_local_proxy_unsupported(agent_config, self.name)
            result = self._execute(task_info, agent_config, task_workspace, cdp_url=None)
        else:
            result = self._run_with_browser_session(task_info, agent_config, task_workspace)
        if isinstance(result, AgentResult):
            result.agent_metadata["hermes_cli_version"] = cli_version
        return result

    # Class attribute so tests can patch/clear the process-wide cache.
    _cli_version = staticmethod(_hermes_cli_version)

    def _run_with_browser_session(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult:
        browser_id = str(agent_config.get("browser_id") or "")
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
                "endpoint, so the hermes agent cannot attach its browser toolset to it. "
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
        rules = _rules_for(agent_config)
        model = str(agent_config.get("model_id") or agent_config.get("model", ""))
        timeout = self.get_timeout(agent_config, 600)

        state_dir = task_workspace / ".hermes-state"
        _write_state_config(state_dir, model, str(agent_config.get("base_url", "")), agent_config)
        usage_file = task_workspace / "hermes_usage.json"
        cmd = self._build_command(f"{rules}\n\n{prompt}", usage_file, agent_config)
        env = _subprocess_env(state_dir, cdp_url, str(agent_config.get("api_key", "")))

        logger.info("Executing Hermes for task %s (model=%s, timeout=%ds)", task_id, model, timeout)
        t_start = time.monotonic()
        try:
            returncode, stdout_lines, execution_error = self._run_subprocess(
                cmd,
                timeout=timeout,
                task_workspace=task_workspace,
                cwd=task_workspace,
                env=env,
                # Hermes spawns helper processes (browser supervisor, node);
                # kill the whole group on timeout so they cannot keep the
                # benchmark runner alive.
                terminate_process_group=True,
            )
        except FileNotFoundError:
            return self._missing_cli_result(task_id)
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
            state_dir=state_dir,
            usage_file=usage_file,
        )

    @staticmethod
    def _missing_cli_result(task_id: str) -> AgentResult:
        return AgentResult(
            task_id=task_id,
            timestamp=datetime.now(UTC),
            env_status="failed",  # type: ignore[arg-type]
            agent_done="error",  # type: ignore[arg-type]
            error=(
                "Executable 'hermes' not found. Please install Hermes Agent: "
                "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
            ),
            metrics=AgentMetrics(end_to_end_ms=0, steps=0),
        )

    @staticmethod
    def _build_command(
        full_prompt: str, usage_file: Path, agent_config: dict[str, Any]
    ) -> list[str]:
        toolsets = str(agent_config.get("toolsets") or "browser")
        return [
            "hermes",
            "-z", full_prompt,
            "--usage-file", str(usage_file),
            "-t", toolsets,
        ]

    def _resolve_status(
        self,
        returncode: int,
        execution_error: str | None,
        report: dict[str, Any] | None,
        answer: str,
    ) -> tuple[str, str, str | None]:
        """Map exit conditions plus the usage report to (env_status, agent_done, error)."""
        env_status, agent_done = self._map_exit_status(
            returncode, execution_error, has_result=bool(answer)
        )
        error_message = execution_error
        if agent_done != "timeout" and report is not None and report.get("failed"):
            env_status, agent_done = "failed", "error"
            error_message = error_message or str(
                report.get("failure") or "Hermes reported a failed run"
            )
        if env_status == "failed" and not error_message:
            # A crashed CLI (non-zero exit, nothing on stdout, no report) must
            # still leave a diagnostic on the result.
            error_message = f"Hermes exited with code {returncode} and produced no output"
        return env_status, agent_done, error_message

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
        state_dir: Path,
        usage_file: Path,
    ) -> AgentResult:
        answer = "".join(stdout_lines).strip()
        report = _read_usage_report(usage_file)
        if execution_error and "Timeout" in execution_error:
            logger.error("Hermes task %s timed out", task_id)
        env_status, agent_done, error_message = self._resolve_status(
            returncode, execution_error, report, answer
        )
        if env_status == "failed" and not answer:
            answer = f"[Task Failed: {error_message}]"

        session_id = str(report.get("session_id") or "") if report else None
        items = _session_items(state_dir, session_id)
        trajectory_dir = task_workspace / "trajectory"
        saved_screenshots = _collect_screenshots(state_dir, trajectory_dir)
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
            error=error_message if env_status == "failed" else None,
            action_history=extract_actions(items),
            screenshots=saved_screenshots,
            model_id=model,
            agent_metadata={},
            metrics=AgentMetrics(
                end_to_end_ms=duration_ms,
                steps=steps,
                usage=_usage_from_report(report),
            ),
        )
