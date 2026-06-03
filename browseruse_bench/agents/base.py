"""
BaseAgent - Abstract base class for all agent implementations.

All custom agents should inherit from this class and implement the run_task() method.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from browseruse_bench.schemas import AgentResult

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base class for browser automation agents.

    All agent implementations must inherit from this class and implement
    the run_task() method to execute a single task.

    Attributes:
        name: Unique identifier for the agent (e.g., "browser-use", "skyvern").
        default_system_prompt: Optional default system prompt for this agent type.
            Users can override per-run via agent_config["system_prompt"].
    """

    name: str = "base"

    # Subclasses declare their default here; users override via config.
    default_system_prompt: str | None = None

    # ------------------------------------------------------------------ #
    # Task helpers                                                         #
    # ------------------------------------------------------------------ #

    def build_task_prompt(
        self,
        task_info: dict[str, Any],
        template: str | None = None,
    ) -> str:
        """Build the task prompt string from task_info.

        Eliminates the duplicated prompt construction pattern across agents.

        Single-site tasks keep a "use only this site" constraint, but explicitly
        treat regional/country variants of the same site (a different subdomain
        or domain ending, e.g. ``zalando.com`` → ``zalando.co.uk``) as the same
        site, so a region redirect is not mistaken for an off-site jump.

        Multi-site tasks (``len(task_info["urls"]) > 1``) skip the single-site
        constraint entirely, since it would contradict the requirement to use
        multiple sites.

        Args:
            task_info: Dict with "task_text" (or "prompt"), "url", and
                optional "urls" (list[str]) keys. ``urls`` is populated by
                ``load_tasks`` for ``target_website`` values containing ``+``.
            template: Optional format string accepting {task_text} and {url}.
                      Defaults to the standard single-site constraint format.

        Returns:
            Formatted prompt ready to pass to the agent.
        """
        task_text = task_info.get("task_text") or task_info.get("prompt", "")
        url = task_info.get("url", "")
        if template:
            return template.format(task_text=task_text, url=url)
        if task_info.get("benchmark_name") == "Odysseys" and url:
            return (
                f"{task_text}\n"
                f"Start from {url}. You may visit any websites needed to complete the task, "
                "and keep requested proof pages open when the task asks for visual evidence."
            )
        urls = task_info.get("urls") or ([url] if url else [])
        if len(urls) > 1:
            return (
                f"{task_text}\n"
                f"You may use the following websites to complete the task: {', '.join(urls)}\n"
                f"Start with {urls[0]}"
            )
        if url:
            return (
                f"{task_text}\n"
                f"Use only {url} to achieve the task. Regional or country versions of "
                f"the same site (a different subdomain or domain ending) count as the "
                f"same site and are allowed; do not navigate to unrelated third-party "
                f"sites. Starting URL: {url}"
            )
        return task_text

    def get_system_prompt(self, agent_config: dict[str, Any]) -> str | None:
        """Return the active system prompt.

        Priority: agent_config["system_prompt"] → self.default_system_prompt → None.
        An explicit empty string in config is preserved and returned as-is (not treated as absent).
        To disable the default entirely, set default_system_prompt = None on the subclass.
        """
        val = agent_config.get("system_prompt")
        return val if val is not None else self.default_system_prompt

    # ------------------------------------------------------------------ #
    # Config normalisation helpers                                         #
    # ------------------------------------------------------------------ #

    def get_model_id(self, agent_config: dict[str, Any]) -> str | None:
        """Normalise model identifier (model_id / model).

        Returns None when absent, matching the None contract of get_api_key / get_base_url.
        Callers that need a str can coerce: ``model = self.get_model_id(cfg) or ""``.
        """
        return agent_config.get("model_id") or agent_config.get("model") or None

    def get_timeout(self, agent_config: dict[str, Any], default: int = 300) -> int:
        """Normalise execution timeout (timeout_seconds / timeout / TIMEOUT).

        Returns *default* when the key is absent or the value is not coercible to int.
        Uses explicit None checks so that an explicit ``0`` is preserved, not discarded.
        """
        raw = None
        for key in ("timeout_seconds", "timeout", "TIMEOUT"):
            val = agent_config.get(key)
            if val is not None:
                raw = val
                break
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("Invalid timeout value %r, using default %d", raw, default)
            return default

    def get_max_steps(self, agent_config: dict[str, Any], default: int = 40) -> int:
        """Normalise step limit (max_steps / max_turns / max_iterations / MAX_STEPS).

        Uses explicit None checks so that an explicit ``0`` is preserved, not discarded.
        """
        raw = None
        for key in ("max_steps", "max_turns", "max_iterations", "MAX_STEPS"):
            val = agent_config.get(key)
            if val is not None:
                raw = val
                break
        if raw is None:
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("Invalid max_steps value %r, using default %d", raw, default)
            return default

    def get_api_key(
        self,
        agent_config: dict[str, Any],
        env_var: str | None = None,
    ) -> str | None:
        """Return API key from config, falling back to *env_var*.

        Args:
            agent_config: Agent configuration dict.
            env_var: Environment variable name to fall back to. No default is
                provided intentionally — callers must pass the provider-specific
                variable (e.g. ``"ANTHROPIC_API_KEY"``) to avoid silently picking
                up an unrelated key.
        """
        return agent_config.get("api_key") or (os.getenv(env_var) if env_var else None) or None

    def get_base_url(
        self,
        agent_config: dict[str, Any],
        env_var: str | None = None,
    ) -> str | None:
        """Return base URL from config, falling back to *env_var*.

        Args:
            agent_config: Agent configuration dict.
            env_var: Environment variable name to fall back to.
        """
        return agent_config.get("base_url") or (os.getenv(env_var) if env_var else None) or None

    # ------------------------------------------------------------------ #
    # Output helpers                                                       #
    # ------------------------------------------------------------------ #

    def save_screenshot(
        self,
        b64_data: str,
        index: int,
        trajectory_dir: Path,
    ) -> bool:
        """Decode a base64-encoded PNG and save it to *trajectory_dir*.

        Args:
            b64_data: Raw base64 image data (no data-URI prefix).
            index: 1-based screenshot counter; saved as ``screenshot-{index}.png``.
            trajectory_dir: Target directory (created if absent).

        Returns:
            True on success, False otherwise.
        """
        if not b64_data:
            return False
        try:
            img_bytes = base64.b64decode(b64_data)
        except (binascii.Error, ValueError) as exc:
            logger.warning("Failed to decode screenshot %d: %s", index, exc)
            return False
        try:
            trajectory_dir.mkdir(parents=True, exist_ok=True)
            (trajectory_dir / f"screenshot-{index}.png").write_bytes(img_bytes)
            return True
        except OSError as exc:
            logger.warning("Failed to write screenshot %d: %s", index, exc)
            return False

    # ------------------------------------------------------------------ #
    # Lifecycle hooks                                                      #
    # ------------------------------------------------------------------ #

    def prepare(self, agent_config: dict[str, Any]) -> None:
        """Prepare agent runtime dependencies before task execution.

        Called once before run_task(). Override for lazy imports, env setup,
        or dependency checks.
        """
        return None

    @abstractmethod
    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a single browser automation task.

        Args:
            task_info: Task information dict containing:
                - task_id: Unique task identifier
                - task_text: Natural language task description
                - url: Starting URL for the task
            agent_config: Agent configuration from config.yaml, merged with env vars.
            task_workspace: Directory path for storing task outputs.

        Returns:
            AgentResult instance (preferred) or legacy dict.
        """
        pass

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}')>"
