#!/usr/bin/env python3
"""
Unified Agent Runner - Entry point for subprocess-based agent execution.

This script is designed to be called via subprocess with proper dependency isolation:
    uv run --extra <agent> browseruse_bench/runner/agent_runner.py --agent <agent> ...

It uses the modular browseruse_bench.agents package for clean code organization
while maintaining process isolation between different agent runs.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import traceback
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Any


class ExitCode(IntEnum):
    """Subprocess exit codes used by the agent runner.

    Values mirror POSIX conventions so callers that only inspect the raw
    integer (``sys.exit``, shell ``$?``) keep working unchanged:

    - ``SUCCESS`` (0): task finished without env-level failure
    - ``FAILURE`` (1): generic failure (bad args, missing deps, task error)
    - ``INTERRUPTED`` (130): 128 + SIGINT(2), terminated by Ctrl-C / SIGTERM
    """

    SUCCESS = 0
    FAILURE = 1
    INTERRUPTED = 130

from browseruse_bench.agents import get_agent
from browseruse_bench.schemas import AgentResult
from browseruse_bench.utils import (
    REPO_ROOT,
    load_agent_config_from_path,
    load_env_file,
    resolve_timeout_value,
    setup_logger,
)
from browseruse_bench.utils.llm_cost import enrich_result_usage_cost_if_needed
from browseruse_bench.utils.run_identity import load_machine_identity_from_env

# Load environment variables from root .env
load_env_file(REPO_ROOT / ".env")

logger = setup_logger("agent_runner")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unified Agent Runner")
    parser.add_argument("--agent", required=True, help="Agent name (e.g., browser-use, skyvern)")
    parser.add_argument("--task-info", required=True, type=Path, help="Path to task info JSON file")
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=None,
        help=(
            "Internal: path to a JSON snapshot of the inline agent config, "
            "written by the parent bubench CLI. Not intended to be set by users."
        ),
    )
    parser.add_argument("--workspace", required=True, type=Path, help="Task workspace directory")
    parser.add_argument("--timeout", type=int, default=None, help="Task timeout in seconds")
    return parser.parse_args()


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle termination signals to allow cleanup."""
    logger.warning(f"Received signal {signum}, shutting down...")
    raise KeyboardInterrupt("Process interrupted")


def _build_error_result(
    task_info: dict[str, Any],
    error: str,
    trace: str,
    *,
    env_status: str = "failed",
    agent_done: str = "error",
) -> dict[str, Any]:
    """Build a minimal structured error result compatible with evaluators."""
    task_text = (
        task_info.get("prompt_base")
        or task_info.get("prompt")
        or task_info.get("task_text")
        or task_info.get("task")
        or ""
    )
    return {
        "task_id": task_info.get("task_id", "unknown"),
        "task": task_text,
        "timestamp": datetime.now(UTC).isoformat(),
        "status": env_status,
        "env_status": env_status,
        "agent_done": agent_done,
        "agent_success": None,
        "answer": "",
        "error": error,
        "traceback": trace,
        "model_id": "",
        "browser_id": "",
        "action_history": [],
        "screenshots": [],
        "metrics": {
            "end_to_end_ms": 0,
            "steps": 0,
            "usage": None,
        },
        "config": {},
    }


def _save_result(workspace: Path, result_data: dict[str, Any]) -> None:
    """Persist result.json into the task workspace."""
    result_path = workspace / "result.json"
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.error("Failed to write result.json at %s: %s", result_path, exc)


def _attach_run_metadata(result_data: dict[str, Any], run_metadata: dict[str, Any]) -> dict[str, Any]:
    existing = result_data.get("run_metadata")
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(run_metadata)
    result_data["run_metadata"] = merged
    return result_data


def main() -> int:
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    args = parse_args()
    start_time = time.monotonic()

    # Load task info
    if not args.task_info.exists():
        logger.error(f"Task info file not found: {args.task_info}")
        return ExitCode.FAILURE

    with open(args.task_info, "r", encoding="utf-8") as f:
        task_info = json.load(f)

    # Load agent config
    if args.agent_config is not None and not args.agent_config.exists():
        logger.error(f"Agent config file not found: {args.agent_config}")
        return ExitCode.FAILURE
    agent_config: dict[str, Any] = load_agent_config_from_path(args.agent_config)

    # Resolve timeout
    timeout = resolve_timeout_value(args.timeout, agent_config)
    agent_config["timeout_seconds"] = timeout
    run_metadata = {"machine": load_machine_identity_from_env()}

    # Ensure workspace exists
    workspace = args.workspace
    workspace.mkdir(parents=True, exist_ok=True)

    # NOTE: runtime.log is written by the parent process (cli/run.py) which
    # reads this subprocess's stdout and tees it into the task workspace.
    # No file handler or tee needed here — all output goes to stdout/stderr
    # and gets captured by the parent.

    # Import and get agent (dependency loading happens here)
    try:
        agent = get_agent(args.agent)
    except ValueError as e:
        logger.error(f"Failed to get agent: {e}")
        return ExitCode.FAILURE
    except ImportError as e:
        logger.error(f"Missing dependencies for {args.agent}: {e}")
        return ExitCode.FAILURE

    # Execute task
    try:
        logger.info(f"[RUNNING] Executing task with {args.agent} agent...")
        agent.prepare(agent_config)
        result = agent.run_task(task_info, agent_config, workspace)

        # Normalize to AgentResult
        if isinstance(result, AgentResult):
            result_data = result.model_dump(mode="json")
            status = result.env_status.value
            error = result.error
        elif isinstance(result, dict):
            result_data = result
            result_data.setdefault("task_id", task_info.get("task_id", "unknown"))
            result_data.setdefault("env_status", "success")
            result_data.setdefault("agent_done", "done")
            result_data.setdefault("agent_success", None)
            status = result_data.get("env_status", "success")
            error = result_data.get("error")
        else:
            raise ValueError("Agent must return an AgentResult or dictionary")

        # Ensure task text is always populated in result.json (used by evaluator).
        # With site-skills injection, "task" records the clean pre-injection
        # prompt; the injected knowledge is recorded separately (site_skills).
        if task_info.get("prompt_base"):
            result_data["task"] = task_info["prompt_base"]
        elif not result_data.get("task"):
            result_data["task"] = task_info.get("prompt") or task_info.get("task_text") or task_info.get("task", "")
        if task_info.get("site_skills"):
            result_data["site_skills"] = task_info["site_skills"]

        # Enrich usage cost if SDK didn't provide complete cost data
        result_data = enrich_result_usage_cost_if_needed(
            result_data,
            model_name=result_data.get("model_id"),
        )
        result_data = _attach_run_metadata(result_data, run_metadata)

        _save_result(workspace, result_data)

        if status == "failed":
            logger.error(f"[FAILED] {error or 'Unknown error'}")
            return ExitCode.FAILURE

        logger.info("[SUCCESS] Task completed successfully")
        return ExitCode.SUCCESS

    except KeyboardInterrupt as exc:
        logger.warning("Task interrupted: %s", exc)
        error_result = _build_error_result(
            task_info,
            str(exc),
            traceback.format_exc(),
            env_status="interrupted",
            agent_done="interrupted",
        )
        error_result = _attach_run_metadata(error_result, run_metadata)
        _save_result(workspace, error_result)
        return ExitCode.INTERRUPTED

    except (ImportError, OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        logger.exception("[FAILED] Task execution error: %s", exc)
        error_result = _build_error_result(task_info, str(exc), traceback.format_exc())
        error_result = _attach_run_metadata(error_result, run_metadata)
        _save_result(workspace, error_result)
        return ExitCode.FAILURE

    finally:
        elapsed_s = time.monotonic() - start_time
        logger.info("[TIMING] Task wall-clock time: %.1fs", elapsed_s)
        result_path = workspace / "result.json"
        try:
            if result_path.is_file():
                with open(result_path, "r", encoding="utf-8") as fh:
                    rd = json.load(fh)
                rd["wall_clock_seconds"] = round(elapsed_s, 1)
                with open(result_path, "w", encoding="utf-8") as fh:
                    json.dump(rd, fh, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, OSError):
            pass


if __name__ == "__main__":
    sys.exit(main())
