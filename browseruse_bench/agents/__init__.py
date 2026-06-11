# Agent Implementations
# =====================
#
# This package contains modular agent implementations for browseruse-bench.
# All agents inherit from BaseAgent and implement the run_task() method.
#
# Usage:
#   from browseruse_bench.agents import get_agent, list_agents
#   agent = get_agent("browser-use")
#   result = agent.run_task(task_info, agent_config, task_workspace)
from __future__ import annotations

import logging

from browseruse_bench.agents.base import BaseAgent
from browseruse_bench.agents.cli_agent import CLIAgent
from browseruse_bench.agents.registry import get_agent, list_agents, register_agent

logger = logging.getLogger(__name__)

try:
    from browseruse_bench.agents import agent_tars  # noqa: F401
except ImportError as exc:
    logger.warning("Skipping optional agent module browseruse_bench.agents.agent_tars: %s", exc)

try:
    from browseruse_bench.agents import claude_code  # noqa: F401
except ImportError as exc:
    logger.warning("Skipping optional agent module browseruse_bench.agents.claude_code: %s", exc)

try:
    from browseruse_bench.agents import codex  # noqa: F401
except ImportError as exc:
    logger.warning("Skipping optional agent module browseruse_bench.agents.codex: %s", exc)

try:
    from browseruse_bench.agents import browser_use  # noqa: F401
except ImportError as exc:
    logger.warning("Skipping optional agent module browseruse_bench.agents.browser_use: %s", exc)

try:
    from browseruse_bench.agents import skyvern  # noqa: F401
except ImportError as exc:
    logger.warning("Skipping optional agent module browseruse_bench.agents.skyvern: %s", exc)

try:
    from browseruse_bench.agents import deepbrowse  # noqa: F401
except ImportError as exc:
    logger.warning("Skipping optional agent module browseruse_bench.agents.deepbrowse: %s", exc)

try:
    from browseruse_bench.agents import webwright  # noqa: F401
except ImportError as exc:
    logger.warning("Skipping optional agent module browseruse_bench.agents.webwright: %s", exc)

try:
    from browseruse_bench.agents import openai_cua  # noqa: F401
except (ModuleNotFoundError, ImportError) as exc:
    # Catch both: ModuleNotFoundError (missing package) and ImportError
    # (broken submodule / version mismatch in a dep like playwright).
    # This matches the guidance in custom-agent-creator/references.
    logger.warning("Skipping optional agent module browseruse_bench.agents.openai_cua: %s", exc)

__all__ = [
    "BaseAgent",
    "CLIAgent",
    "get_agent",
    "list_agents",
    "register_agent",
]
