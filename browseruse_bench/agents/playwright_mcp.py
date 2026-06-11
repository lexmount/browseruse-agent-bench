"""
Shared helpers for CLI agents that drive the browser via Playwright MCP.

Used by the codex and cursor agents: browser rules, MCP server argument
construction, screenshot collection, action descriptions, and api_logs output.
The normalized step-item shape is::

    {"type": "mcp_tool_call", "tool": ..., "arguments": {...},
     "status": ..., "result": {"content": [{"type": "text", "text": ...}]},
     "error": {"message": ...}}
    {"type": "command_execution", "command": ..., "status": ...}
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BROWSER_RULES = (
    "You are a browser automation agent. "
    "You MUST use ONLY the Playwright MCP tools (server 'playwright') for ALL browser "
    "interactions. Do NOT run shell commands, do NOT read or write files, and do NOT "
    "use skills or any non-Playwright tools."
    "\n\nTask completion rules:\n"
    "- If you can see enough information to answer the task from the current page (e.g., "
    "ratings, names, prices visible in search results), provide your answer IMMEDIATELY "
    "without clicking into individual items to get more detail.\n"
    "- If you encounter a CAPTCHA, verification page, login wall, or access restriction: "
    "close that tab, return to the previous page, and use the data already collected to answer.\n"
    "- Do NOT get stuck retrying the same blocked action. One retry max, then fall back.\n"
    "\n\nScreenshot rules:\n"
    "- When taking screenshots, do NOT specify a filename parameter.\n"
    "- Take a screenshot with browser_take_screenshot after navigating to the main page "
    "and after finding the answer."
)

# JSONL item types that represent an agent step (tool usage).
STEP_ITEM_TYPES = {"mcp_tool_call", "command_execution"}

# Browser ids where Playwright MCP launches its own local browser instead of
# connecting to a managed backend session over CDP.
SELF_LAUNCH_BROWSER_IDS = {"", "local", "Chrome-Local"}


def build_playwright_mcp_args(agent_config: dict[str, Any], cdp_url: str | None) -> list[str]:
    """Build the Playwright MCP server argument list from agent config."""
    mcp_args = list(agent_config.get("playwright_mcp_args", ["@playwright/mcp@latest"]))
    # --timeout-action 30000: raise from the 5000ms default (screenshots time
    # out on pages with slow external font CDNs).
    mcp_args += ["--timeout-action", "30000"]
    if cdp_url:
        mcp_args += ["--cdp-endpoint", cdp_url]
    return mcp_args


def collect_screenshots(task_workspace: Path, trajectory_dir: Path) -> list[str]:
    """Copy Playwright MCP screenshots into trajectory/.

    Screenshots land in .playwright-mcp/ by default, or in the workspace root
    when the model passes an explicit filename despite the rules.
    """
    candidates: list[Path] = []
    for parent in (task_workspace / ".playwright-mcp", task_workspace):
        if parent.is_dir():
            candidates += [
                p for p in parent.iterdir() if p.suffix.lower() in (".png", ".jpeg", ".jpg")
            ]
    images = sorted(candidates, key=lambda p: p.stat().st_mtime)
    saved: list[str] = []
    for index, image in enumerate(images, 1):
        fname = f"screenshot-{index}{image.suffix.lower()}"
        try:
            trajectory_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(image, trajectory_dir / fname)
            saved.append(fname)
        except OSError as exc:
            logger.warning("Failed to copy screenshot %s: %s", image.name, exc)
    return saved


def describe_item(item: dict[str, Any]) -> str | None:
    """Map a completed step item to a short action description."""
    item_type = item.get("type")
    if item_type == "command_execution":
        return f"Shell: {str(item.get('command', ''))[:80]}"
    if item_type != "mcp_tool_call":
        return None
    tool = str(item.get("tool", ""))
    arguments = item.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    if "navigate" in tool:
        return f"Navigate to {arguments.get('url', '')}"
    if "click" in tool:
        return f"Click: {arguments.get('element', arguments.get('ref', ''))}"
    if "type" in tool or "fill" in tool:
        return f"Type: {str(arguments.get('text', arguments.get('value', '')))[:60]}"
    if "screenshot" in tool:
        return "Take screenshot"
    if "snapshot" in tool:
        return "Take snapshot"
    if "press" in tool:
        return f"Press key: {arguments.get('key', '')}"
    return tool or None


def extract_actions(items: list[dict[str, Any]]) -> list[str]:
    actions: list[str] = []
    for item in items:
        description = describe_item(item)
        if description:
            actions.append(description)
    return actions


def write_api_logs(
    task_id: str,
    model_id: str,
    rules: str,
    items: list[dict[str, Any]],
    api_logs_dir: Path,
) -> None:
    """Write api_logs/step_NNN.json + system_prompt.txt for step items."""
    api_logs_dir.mkdir(parents=True, exist_ok=True)
    try:
        (api_logs_dir / "system_prompt.txt").write_text(rules, encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to write system_prompt.txt: %s", exc)

    step_items = [item for item in items if item.get("type") in STEP_ITEM_TYPES]
    for step_number, item in enumerate(step_items, 1):
        step_data = {
            "metadata": {
                "task_id": task_id,
                "step_number": step_number,
                "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
                "model_id": model_id,
            },
            "output": {"actions": [{"tool": item.get("tool") or item.get("command"), "input": item.get("arguments")}]},
            "action_results": [item_result_summary(item)],
        }
        try:
            (api_logs_dir / f"step_{step_number:03d}.json").write_text(
                json.dumps(step_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (OSError, TypeError) as exc:
            logger.warning("Failed to write step %d log: %s", step_number, exc)


def item_result_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": item.get("status")}
    result = item.get("result")
    if isinstance(result, dict):
        texts = [
            block.get("text", "")
            for block in result.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if texts:
            summary["extracted_content"] = "\n".join(texts)
    error = item.get("error")
    if isinstance(error, dict) and error.get("message"):
        summary["error"] = error["message"]
    if item.get("aggregated_output"):
        summary["extracted_content"] = str(item["aggregated_output"])[:2000]
    return summary
