"""WebwrightAgent - Browser automation using the Webwright SDK.

This agent embeds Webwright's Python runner directly, following the same SDK
integration style as ``browser_use.py`` while keeping Webwright dependencies
isolated in the agent venv.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from httpx import HTTPError

try:
    from playwright.async_api import Error as PlaywrightError
except ImportError:
    PlaywrightError = None

from browseruse_bench.agents.base import BaseAgent
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import open_browser_session
from browseruse_bench.browsers.providers.local import warn_if_local_proxy_unsupported
from browseruse_bench.schemas import AgentMetrics, AgentResult, AgentUsage

logger = logging.getLogger(__name__)

RunOne = Callable[..., dict[str, Any]]

_run_one: RunOne | None = None
_WEBWRIGHT_IMPORT_ERROR: str | None = None
_WEBWRIGHT_LIMITS_EXCEEDED: type[BaseException] | None = None


def _load_webwright_dependencies() -> None:
    """Lazy-load Webwright only when the webwright agent is selected."""
    global _run_one, _WEBWRIGHT_IMPORT_ERROR, _WEBWRIGHT_LIMITS_EXCEEDED

    if _run_one is not None or _WEBWRIGHT_IMPORT_ERROR is not None:
        return
    try:
        from webwright.exceptions import LimitsExceeded
        from webwright.run.cli import run_one
    except ImportError as exc:
        _WEBWRIGHT_IMPORT_ERROR = str(exc)
        logger.error("webwright dependency is not available: %s", exc)
        return
    _run_one = run_one
    _WEBWRIGHT_LIMITS_EXCEEDED = LimitsExceeded


def _get_config_value(agent_config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in agent_config and agent_config[key] is not None:
            return agent_config[key]
    return default


def _latest_run_dir(task_workspace: Path) -> Path | None:
    final_runs_dir = task_workspace / "final_runs"
    if not final_runs_dir.is_dir():
        return None

    run_dirs: list[tuple[int, Path]] = []
    for entry in final_runs_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith("run_"):
            continue
        try:
            run_id = int(entry.name.removeprefix("run_"))
        except ValueError:
            continue
        run_dirs.append((run_id, entry))
    if not run_dirs:
        return None
    return max(run_dirs, key=lambda item: item[0])[1]


def _read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return ""


def _extract_actions_from_log(log_text: str) -> list[str]:
    actions: list[str] = []
    for line in log_text.splitlines():
        clean = line.strip()
        if clean.lower().startswith("step "):
            actions.append(clean)
    return actions


def _extract_answer_from_log(log_text: str) -> str:
    for line in reversed(log_text.splitlines()):
        clean = line.strip()
        if clean and not clean.lower().startswith("step "):
            return clean
    return ""


def _collect_screenshots(task_workspace: Path, run_dir: Path | None) -> list[str]:
    screenshots_dir = (run_dir / "screenshots") if run_dir is not None else (task_workspace / "screenshots")
    if not screenshots_dir.is_dir():
        return []

    screenshots: list[str] = []
    for image_path in sorted(screenshots_dir.glob("*.png")):
        try:
            screenshots.append(str(image_path.relative_to(task_workspace)))
        except ValueError:
            screenshots.append(str(image_path))
    return screenshots


def _extract_actions_from_step_files(task_workspace: Path) -> list[str]:
    steps_dir = task_workspace / "steps"
    if not steps_dir.is_dir():
        return []
    actions: list[str] = []
    for step_path in sorted(steps_dir.glob("step_*.*")):
        try:
            preview = step_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            logger.warning("Failed to read %s: %s", step_path, exc)
            continue
        first_line = preview.splitlines()[0] if preview else ""
        actions.append(f"{step_path.stem}: {first_line[:120]}")
    return actions


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_trajectory(task_workspace: Path) -> dict[str, Any] | None:
    try:
        trajectory = json.loads((task_workspace / "trajectory.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read Webwright trajectory from %s: %s", task_workspace, exc)
        return None
    return trajectory if isinstance(trajectory, dict) else None


def _read_api_calls_from_trajectory(task_workspace: Path) -> int | None:
    trajectory = _read_trajectory(task_workspace)
    if trajectory is None:
        return None

    info = trajectory.get("info")
    if not isinstance(info, dict) or info.get("api_calls") is None:
        return None
    steps = _safe_int(info.get("api_calls"), -1)
    return steps if steps >= 0 else None


def _read_usage_from_trajectory(task_workspace: Path) -> dict[str, Any]:
    trajectory = _read_trajectory(task_workspace)
    if trajectory is None:
        return {}

    model = trajectory.get("model")
    if not isinstance(model, dict):
        return {}
    usage = model.get("usage")
    if not isinstance(usage, dict):
        return {}

    cumulative = usage.get("cumulative_response")
    if isinstance(cumulative, dict):
        return cumulative
    last_response = usage.get("last_response")
    return last_response if isinstance(last_response, dict) else {}


def _normalize_model_type(raw_value: Any) -> str:
    value = str(raw_value or "OPENAI").strip().lower().replace("-", "_")
    if value in {"anthropic", "claude"}:
        return "anthropic"
    if value in {"openrouter", "open_router"}:
        return "openrouter"
    if value in {"openai", "azure", "openai_compatible"}:
        return "openai"
    return value


def _default_model_config(model_type: str) -> str:
    if model_type == "anthropic":
        return "model_claude.yaml"
    if model_type == "openrouter":
        return "model_openrouter.yaml"
    return "model_openai.yaml"


def _normalize_openai_responses_endpoint(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/responses"):
        return endpoint
    if endpoint.endswith("/v1"):
        return f"{endpoint}/responses"
    return endpoint


def _normalize_chat_completions_endpoint(base_url: str) -> str:
    endpoint = base_url.rstrip("/")
    if endpoint.endswith("/chat/completions"):
        return endpoint
    if endpoint.endswith("/v1"):
        return f"{endpoint}/chat/completions"
    return endpoint


def _is_official_openai_endpoint(base_url: str | None) -> bool:
    if not base_url:
        return True
    hostname = (urlparse(base_url).hostname or "").lower()
    return hostname == "api.openai.com"


def _is_chat_completions_endpoint(base_url: str | None) -> bool:
    if not base_url:
        return False
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/").lower()
    return hostname == "openrouter.ai" or path.endswith("/chat/completions")


def _model_api_key_env_var(model_type: str) -> str:
    if model_type == "anthropic":
        return "ANTHROPIC_API_KEY"
    if model_type == "openrouter":
        return "OPENROUTER_API_KEY"
    return "OPENAI_API_KEY"


def _fallback_model_api_key_env_var(model_type: str) -> str | None:
    if model_type == "openrouter":
        return "OPENAI_API_KEY"
    return None


@contextmanager
def _temporary_model_env(agent_config: dict[str, Any], model_type: str) -> Iterator[None]:
    env_var = _model_api_key_env_var(model_type)
    api_key = agent_config.get("api_key")
    if not api_key:
        fallback_env_var = _fallback_model_api_key_env_var(model_type)
        if fallback_env_var and env_var not in os.environ:
            api_key = os.environ.get(fallback_env_var)
    if not api_key:
        yield
        return

    previous = os.environ.get(env_var)
    had_previous = env_var in os.environ
    os.environ[env_var] = str(api_key)
    try:
        yield
    finally:
        if had_previous and previous is not None:
            os.environ[env_var] = previous
        else:
            os.environ.pop(env_var, None)


@contextmanager
def _wall_clock_timeout(timeout_seconds: int) -> Iterator[None]:
    if timeout_seconds <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _handle_timeout(signum: int, frame: Any) -> None:
        del signum, frame
        raise TimeoutError(f"Timeout after {timeout_seconds} seconds")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    signal.signal(signal.SIGALRM, _handle_timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


@register_agent
class WebwrightAgent(BaseAgent):
    """Browser automation agent using Webwright's Python runner."""

    name = "webwright"

    def prepare(self, agent_config: dict[str, Any]) -> None:
        del agent_config
        _load_webwright_dependencies()
        if _WEBWRIGHT_IMPORT_ERROR:
            raise ImportError(
                "webwright dependencies are missing. Install the webwright agent "
                "environment via `uv sync --extra webwright` or run bubench with "
                "the webwright registry entry."
            )

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult:
        """Execute a browser automation task using the embedded Webwright runner."""
        self.prepare(agent_config)
        if _run_one is None:
            raise ImportError("webwright.run.cli.run_one was not loaded")

        task_workspace.mkdir(parents=True, exist_ok=True)

        task_id = task_info["task_id"]
        task_prompt = self.build_task_prompt(task_info)
        start_url = str(task_info.get("url") or task_info.get("task_start_url") or "")
        timeout = self.get_timeout(agent_config, 600)
        max_steps = self.get_max_steps(agent_config, 100)
        model_id = self.get_model_id(agent_config) or ""
        model_type = self._resolve_model_type(agent_config)
        browser_id = str(_get_config_value(agent_config, "browser_id", "BROWSER_ID", default="lexmount"))
        debug = bool(agent_config.get("debug", False))

        started = time.monotonic()
        result_payload: dict[str, Any] = {}
        error_msg: str | None = None
        forced_exit_status: str | None = None

        logger.info(
            "Executing Webwright for task %s (model_type=%s, model=%s, max_steps=%d)",
            task_id,
            model_type,
            model_id or "<default>",
            max_steps,
        )

        warn_if_local_proxy_unsupported(agent_config, self.name)
        try:
            with open_browser_session(
                browser_id=browser_id,
                agent_name=self.name,
                agent_config=agent_config,
            ) as session_context:
                cdp_url = session_context.cdp_url if session_context.transport == "cdp" else None
                config_spec = self._build_config_spec(
                    agent_config=agent_config,
                    model_type=model_type,
                    model_id=model_id,
                    timeout=timeout,
                    max_steps=max_steps,
                    session_transport=session_context.transport,
                    cdp_url=cdp_url,
                )
                with _temporary_model_env(agent_config, model_type), _wall_clock_timeout(timeout):
                    result_payload = _run_one(
                        task=task_prompt,
                        task_id=task_id,
                        start_url=start_url or None,
                        config_spec=config_spec,
                        resolved_output_dir=task_workspace,
                        debug=debug,
                        snapshot_config=True,
                    )
                browser_id = session_context.backend_id
        except TimeoutError:
            error_msg = f"Timeout after {timeout} seconds"
            logger.error("Task %s timed out after %d seconds", task_id, timeout)
        except self._runtime_error_types() as exc:
            if self._is_limits_exceeded(exc):
                forced_exit_status = "LimitsExceeded"
                logger.info("Task %s reached Webwright step limit", task_id)
            else:
                error_msg = str(exc)
                logger.error("Task %s Webwright execution error: %s", task_id, exc)

        end_to_end_ms = int((time.monotonic() - started) * 1000)
        latest_run = _latest_run_dir(task_workspace)
        final_log = _read_text_if_exists(latest_run / "final_script_log.txt") if latest_run else ""
        action_history = _extract_actions_from_log(final_log)
        if not action_history:
            action_history = _extract_actions_from_step_files(task_workspace)
        screenshots = _collect_screenshots(task_workspace, latest_run)

        final_answer = str(
            result_payload.get("final_response")
            or result_payload.get("submission")
            or _extract_answer_from_log(final_log)
            or ""
        )
        exit_status = forced_exit_status or str(result_payload.get("exit_status") or "")
        run_exception = str(result_payload.get("run_exception") or "")
        if run_exception and not error_msg:
            error_msg = run_exception

        if error_msg and "Timeout" in error_msg:
            env_status = "success"
            agent_done = "timeout"
        elif error_msg:
            env_status = "failed"
            agent_done = "error"
            if not final_answer:
                final_answer = f"[Task Failed: {error_msg}]"
        elif exit_status == "LimitsExceeded":
            env_status = "success"
            agent_done = "max_steps"
        else:
            env_status = "success"
            agent_done = "done"

        usage_payload = self._extract_usage(result_payload)
        if not usage_payload:
            usage_payload = self._extract_usage(
                {"usage": _read_usage_from_trajectory(task_workspace)}
            )
        steps = _safe_int(result_payload.get("api_calls"), -1)
        if steps < 0:
            trajectory_steps = _read_api_calls_from_trajectory(task_workspace)
            steps = trajectory_steps if trajectory_steps is not None else len(action_history)
        agent_success = None
        if agent_done == "done":
            agent_success = bool(final_answer)

        metadata: dict[str, Any] = {
            "exit_status": exit_status,
        }
        if latest_run is not None:
            try:
                metadata["latest_run"] = str(latest_run.relative_to(task_workspace))
            except ValueError:
                metadata["latest_run"] = str(latest_run)
        if result_payload.get("_output_dir"):
            metadata["output_dir"] = result_payload["_output_dir"]

        return AgentResult(
            task_id=task_id,
            task=task_prompt,
            timestamp=datetime.now(UTC),
            env_status=env_status,  # type: ignore[arg-type]
            agent_done=agent_done,  # type: ignore[arg-type]
            agent_success=agent_success,
            answer=final_answer,
            error=error_msg if env_status == "failed" else None,
            model_id=model_id,
            browser_id=browser_id,
            action_history=action_history,
            screenshots=screenshots,
            metrics=AgentMetrics(
                end_to_end_ms=end_to_end_ms,
                steps=steps,
                usage=AgentUsage(**usage_payload) if usage_payload else None,
            ),
            agent_metadata=metadata,
            config={
                "timeout_seconds": timeout,
                "max_steps": max_steps,
                "model_type": model_type,
                "browser_id": browser_id,
            },
        )

    @staticmethod
    def _build_config_spec(
        *,
        agent_config: dict[str, Any],
        model_type: str,
        model_id: str,
        timeout: int,
        max_steps: int,
        session_transport: str,
        cdp_url: str | None = None,
    ) -> list[str]:
        if session_transport not in {"cdp", "local"}:
            raise ValueError(
                f"Webwright does not support browser session transport: {session_transport}"
            )

        uses_live_browser = session_transport == "cdp"
        config_spec = ["base.yaml"]
        if uses_live_browser:
            config_spec.append("local_browser.yaml")
        config_spec.append(_default_model_config(model_type))

        if model_id:
            config_spec.append(f"model.model_name={model_id}")
        config_spec.append(f"agent.step_limit={max_steps}")
        if uses_live_browser:
            config_spec.append(
                "environment.environment_class="
                "browseruse_bench.agents.webwright_remote_cdp.RemoteCDPEnvironment"
            )
            config_spec.append("environment.browser_mode=local_cdp")
            config_spec.append("environment.remote_cdp_new_page=true")
            if cdp_url:
                config_spec.append(f"environment.remote_cdp_url={cdp_url}")
        else:
            config_spec.append(f"environment.command_timeout_seconds={timeout}")
            config_spec.append("environment.browser_mode=local")

        max_output_tokens = agent_config.get("max_output_tokens") or agent_config.get("max_tokens")
        if max_output_tokens is not None:
            config_spec.append(f"model.max_output_tokens={int(max_output_tokens)}")

        request_timeout = agent_config.get("request_timeout_seconds") or agent_config.get("request_timeout")
        if request_timeout is not None:
            config_spec.append(f"model.request_timeout_seconds={int(request_timeout)}")

        base_url = agent_config.get("base_url")
        if base_url:
            if model_type == "anthropic":
                config_spec.append(f"model.anthropic_endpoint={base_url}")
            elif model_type == "openrouter":
                config_spec.append(
                    f"model.openrouter_endpoint={_normalize_chat_completions_endpoint(base_url)}"
                )
            else:
                config_spec.append(
                    f"model.openai_endpoint={_normalize_openai_responses_endpoint(base_url)}"
                )

        extra_config_spec = agent_config.get("config_spec")
        if isinstance(extra_config_spec, list):
            config_spec.extend(str(item) for item in extra_config_spec)
        elif isinstance(extra_config_spec, str) and extra_config_spec.strip():
            config_spec.append(extra_config_spec.strip())

        return config_spec

    @staticmethod
    def _resolve_model_type(agent_config: dict[str, Any]) -> str:
        model_type = _normalize_model_type(
            _get_config_value(agent_config, "model_type", "MODEL_TYPE", default="OPENAI")
        )
        base_url = agent_config.get("base_url")
        api_style = str(agent_config.get("model_api_style") or "").strip().lower()
        if model_type == "openai" and api_style in {"chat", "chat_completions", "chat-completions"}:
            return "openrouter"
        if (
            model_type == "openai"
            and base_url
            and not _is_official_openai_endpoint(str(base_url))
            and _is_chat_completions_endpoint(str(base_url))
        ):
            return "openrouter"
        return model_type

    @staticmethod
    def _runtime_error_types() -> tuple[type[BaseException], ...]:
        error_types: tuple[type[BaseException], ...] = (
            HTTPError,
            ImportError,
            RuntimeError,
            OSError,
            TypeError,
            ValueError,
        )
        if PlaywrightError is not None:
            error_types = error_types + (PlaywrightError,)
        if _WEBWRIGHT_LIMITS_EXCEEDED is not None:
            error_types = error_types + (_WEBWRIGHT_LIMITS_EXCEEDED,)
        return error_types

    @staticmethod
    def _is_limits_exceeded(exc: BaseException) -> bool:
        return _WEBWRIGHT_LIMITS_EXCEEDED is not None and isinstance(exc, _WEBWRIGHT_LIMITS_EXCEEDED)

    @staticmethod
    def _extract_usage(result_payload: dict[str, Any]) -> dict[str, Any]:
        usage = result_payload.get("usage")
        if not isinstance(usage, dict):
            usage = result_payload.get("model_usage")
        if not isinstance(usage, dict):
            return {}

        total_prompt_tokens = _safe_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
        total_completion_tokens = _safe_int(
            usage.get("output_tokens") or usage.get("completion_tokens")
        )
        total_prompt_cached_tokens = _safe_int(
            usage.get("cached_input_tokens") or usage.get("cached_tokens")
        )
        total_tokens = _safe_int(usage.get("total_tokens"), total_prompt_tokens + total_completion_tokens)
        total_cost = _safe_float(usage.get("cost") or usage.get("total_cost"))

        usage_data: dict[str, Any] = {
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_prompt_cached_tokens": total_prompt_cached_tokens,
            "total_tokens": total_tokens,
        }
        if total_cost is not None:
            usage_data["total_cost"] = total_cost
        return usage_data
