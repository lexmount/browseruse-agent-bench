"""
BrowserUseAgent - Browser automation using the browser-use library.

This agent uses the browser-use SDK to execute browser automation tasks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browser_use import Agent as BrowserUseSDKAgent
from browser_use import Browser as BrowserUseSDKBrowser
from browser_use import ChatAnthropic as BrowserUseSDKChatAnthropic
from browser_use import ChatAzureOpenAI as BrowserUseSDKChatAzureOpenAI
from browser_use import ChatBrowserUse as BrowserUseSDKChatBrowserUse
from browser_use import ChatGoogle as BrowserUseSDKChatGoogle
from browser_use import ChatOpenAI as BrowserUseSDKChatOpenAI
from browser_use.browser import session as browser_use_session_module
from browser_use.browser.profile import ProxySettings as BrowserUseProxySettings
from browser_use.llm.exceptions import ModelError as BrowserUseModelError
from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
from browser_use.llm.openai.responses_serializer import ResponsesAPIMessageSerializer
from browser_use.llm.schema import SchemaOptimizer
from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from pydantic import BaseModel as _PydanticBaseModel

from browseruse_bench.agents.base import BaseAgent
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import BrowserSessionContext, open_browser_session
from browseruse_bench.schemas import AgentMetrics, AgentResult, AgentUsage
from browseruse_bench.utils.api_logger import APICallLogger

Agent: type[Any] = BrowserUseSDKAgent
ChatBrowserUse: type[Any] = BrowserUseSDKChatBrowserUse
ChatAnthropic: type[Any] = BrowserUseSDKChatAnthropic
ChatGoogle: type[Any] = BrowserUseSDKChatGoogle
ChatAzureOpenAI: type[Any] = BrowserUseSDKChatAzureOpenAI
ChatOpenAI: type[Any] = BrowserUseSDKChatOpenAI

logger = logging.getLogger(__name__)

BROWSER_USE_CDP_CONNECT_TIMEOUT_SECONDS = 30.0
_BROWSER_USE_SDK_CDP_CONNECT_TIMEOUT_SECONDS = 15.0
_BROWSER_USE_CDP_CONNECT_TIMEOUT_LABEL = f"{BROWSER_USE_CDP_CONNECT_TIMEOUT_SECONDS:g}s"


_MAX_STEPS_ERROR = "Failed to complete task in maximum steps"
_JSON_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_PATCHED_MODEL_VALIDATE_JSON_ATTR = "_browseruse_bench_robust_json_patched"


def _iter_json_candidates(text: str) -> Iterator[Any]:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    if not stripped:
        return

    try:
        yield json.loads(stripped)
        return
    except json.JSONDecodeError:
        pass

    for match in _JSON_FENCE_RE.finditer(stripped):
        fenced = match.group(1).strip()
        try:
            yield json.loads(fenced)
        except json.JSONDecodeError:
            yield from _iter_json_candidates(fenced)

    for index, char in enumerate(stripped):
        if char not in "{[":
            continue
        try:
            candidate, _ = decoder.raw_decode(stripped[index:])
            yield candidate
        except json.JSONDecodeError:
            continue


def _raw_decode_json_candidate(text: str) -> Any | None:
    return next(_iter_json_candidates(text), None)


def _validate_json_candidates(
    output_model: type[Any],
    candidates: Iterator[Any],
    *validation_args: Any,
    **validation_kwargs: Any,
) -> Any:
    validation_error: Exception | None = None
    for candidate in candidates:
        try:
            return _validate_extracted_json(
                output_model,
                candidate,
                *validation_args,
                **validation_kwargs,
            )
        except Exception as exc:
            validation_error = exc
            continue
    if validation_error is not None:
        raise validation_error
    return None


def _validate_extracted_json(
    output_model: type[Any],
    parsed: Any,
    *validation_args: Any,
    **validation_kwargs: Any,
) -> Any:
    try:
        return output_model.model_validate(parsed, *validation_args, **validation_kwargs)
    except Exception:
        if isinstance(parsed, dict):
            for key in ("AgentOutput", "agent_output", "arguments", "input"):
                value = parsed.get(key)
                if isinstance(value, str):
                    value = _raw_decode_json_candidate(value)
                if isinstance(value, dict):
                    try:
                        return output_model.model_validate(
                            value,
                            *validation_args,
                            **validation_kwargs,
                        )
                    except Exception:
                        continue
        raise


def _patch_output_model_json_parser(output_model: type[Any] | None) -> None:
    if output_model is None or _PATCHED_MODEL_VALIDATE_JSON_ATTR in output_model.__dict__:
        return

    original_validate_json = output_model.model_validate_json.__func__

    @classmethod
    def robust_model_validate_json(cls: type[Any], json_data: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return original_validate_json(cls, json_data, *args, **kwargs)
        except Exception:
            if not isinstance(json_data, str):
                raise
            parsed = _validate_json_candidates(
                cls,
                _iter_json_candidates(json_data),
                *args,
                **kwargs,
            )
            if parsed is None:
                raise
            return parsed

    output_model.model_validate_json = robust_model_validate_json  # type: ignore[method-assign]
    setattr(output_model, _PATCHED_MODEL_VALIDATE_JSON_ATTR, True)


def _iter_output_model_bases(model: type[Any] | None) -> Iterator[type[Any]]:
    if model is None:
        return
    for klass in getattr(model, "__mro__", ()):
        if klass is _PydanticBaseModel or klass is object:
            break
        yield klass


def _patch_agent_output_json_parsers(agent: Any) -> None:
    for attr in ("AgentOutput", "DoneAgentOutput"):
        for model in _iter_output_model_bases(getattr(agent, attr, None)):
            _patch_output_model_json_parser(model)


_PATCHED_SCHEMA_OPTIMIZER_ATTR = "_browseruse_bench_strip_numeric_bounds_patched"
_VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high")
_NON_IDENTIFIER_CHARS_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _is_claude_model(model_id: str | None) -> bool:
    return "claude" in (model_id or "").lower()


def _strip_numeric_bounds(obj: Any) -> None:
    """Recursively drop numeric-range keywords from a JSON schema in place."""
    if isinstance(obj, dict):
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            obj.pop(key, None)
        for value in obj.values():
            _strip_numeric_bounds(value)
    elif isinstance(obj, list):
        for item in obj:
            _strip_numeric_bounds(item)


def _patch_schema_optimizer_for_claude() -> None:
    """Make browser-use structured-output schemas compatible with Claude validators."""
    if getattr(SchemaOptimizer, _PATCHED_SCHEMA_OPTIMIZER_ATTR, False):
        return

    original = SchemaOptimizer.create_optimized_json_schema

    def patched(model: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = original(model, *args, **kwargs)
        _strip_numeric_bounds(schema)
        return schema

    SchemaOptimizer.create_optimized_json_schema = staticmethod(patched)  # type: ignore[method-assign]
    setattr(SchemaOptimizer, _PATCHED_SCHEMA_OPTIMIZER_ATTR, True)


def _enable_claude_thinking(llm: Any, reasoning_effort: str) -> None:
    """Inject Claude reasoning params for OpenAI-compatible gateways."""
    original_get_client = llm.get_client

    def get_client_with_thinking() -> Any:
        client = original_get_client()
        original_create = client.chat.completions.create

        async def create_with_thinking(*args: Any, **kwargs: Any) -> Any:
            extra_body = dict(kwargs.get("extra_body") or {})
            extra_body.setdefault("reasoning_effort", reasoning_effort)
            allowed = list(extra_body.get("allowed_openai_params") or [])
            if "reasoning_effort" not in allowed:
                allowed.append("reasoning_effort")
            extra_body["allowed_openai_params"] = allowed
            kwargs["extra_body"] = extra_body
            return await original_create(*args, **kwargs)

        client.chat.completions.create = create_with_thinking  # type: ignore[method-assign]
        return client

    llm.get_client = get_client_with_thinking  # type: ignore[method-assign]


class _LLMFailureRecorder:
    """
    Keeps raw LLM completions for calls that fail browser-use parsing.

    browser-use converts unparseable completions into ModelProviderError before
    the agent sees the content, so the raw payload must be stashed at the HTTP
    client layer and attached to the failure record here.
    """

    def __init__(self) -> None:
        self._last_response: dict[str, Any] | None = None
        self.failures: list[dict[str, Any]] = []

    def begin_call(self) -> None:
        self._last_response = None

    def record_response(self, response: Any) -> None:
        choices = getattr(response, "choices", None) or []
        message = getattr(choices[0], "message", None) if choices else None
        usage = getattr(response, "usage", None)
        self._last_response = {
            "raw_response": getattr(message, "content", None),
            "usage": usage.model_dump() if hasattr(usage, "model_dump") else None,
        }

    def record_failure(self, error: Exception) -> None:
        record: dict[str, Any] = {
            "timestamp": time.time(),
            "error": str(error),
            "status_code": getattr(error, "status_code", None),
            "raw_response": None,
            "usage": None,
        }
        if self._last_response is not None:
            record.update(self._last_response)
        self.failures.append(record)


def _record_raw_llm_responses(llm: Any, recorder: _LLMFailureRecorder) -> None:
    """
    Stash each raw chat completion so failed parses keep their payload.

    Only OpenAI-compatible clients exposing chat.completions.create are covered
    (the bench's primary gateway path). Azure responses-API mode, Anthropic, and
    Google clients bypass this wrap; their failure records carry error and
    timestamp but raw_response=None.
    """
    original_get_client = llm.get_client

    def get_client_with_response_recording() -> Any:
        client = original_get_client()
        completions = getattr(getattr(client, "chat", None), "completions", None)
        if completions is None:
            return client
        original_create = completions.create

        async def create_with_response_recording(*args: Any, **kwargs: Any) -> Any:
            response = await original_create(*args, **kwargs)
            recorder.record_response(response)
            return response

        completions.create = create_with_response_recording
        return client

    llm.get_client = get_client_with_response_recording


def _capture_llm_failures(llm: Any, recorder: _LLMFailureRecorder) -> None:
    """Wrap llm.ainvoke so provider/parse failures keep raw response context."""
    if hasattr(llm, "get_client"):
        _record_raw_llm_responses(llm, recorder)

    original_ainvoke = llm.ainvoke

    async def ainvoke_with_failure_capture(*args: Any, **kwargs: Any) -> Any:
        recorder.begin_call()
        try:
            return await original_ainvoke(*args, **kwargs)
        except BrowserUseModelError as exc:
            recorder.record_failure(exc)
            raise

    llm.ainvoke = ainvoke_with_failure_capture


@dataclass
class _BrowserUseResponsesLLM:
    """browser-use LLM adapter for OpenAI-compatible Responses API endpoints."""

    model: str
    api_key: str | None = None
    base_url: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    add_schema_to_system_prompt: bool = True

    @property
    def provider(self) -> str:
        return "openai"

    @property
    def name(self) -> str:
        return self.model

    @property
    def model_name(self) -> str:
        return self.model

    def get_client(self) -> Any:
        from openai import AsyncOpenAI

        client_kwargs: dict[str, Any] = {}
        if self.api_key:
            client_kwargs["api_key"] = self.api_key
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        return AsyncOpenAI(**client_kwargs)

    def _model_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.max_output_tokens is not None:
            params["max_output_tokens"] = self.max_output_tokens
        return params

    @staticmethod
    def _schema_name(output_format: type[Any]) -> str:
        raw_name = getattr(output_format, "__name__", "agent_output")
        name = _NON_IDENTIFIER_CHARS_RE.sub("_", raw_name).strip("_")
        return name[:64] or "agent_output"

    @classmethod
    def _text_format(cls, output_format: type[Any]) -> dict[str, Any]:
        return {
            "format": {
                "type": "json_schema",
                "name": cls._schema_name(output_format),
                "schema": SchemaOptimizer.create_optimized_json_schema(output_format),
                "strict": True,
            }
        }

    @staticmethod
    def _with_schema_prompt(messages: list[Any], output_format: type[Any]) -> list[Any]:
        schema = SchemaOptimizer.create_optimized_json_schema(output_format)
        instruction = (
            "Return only valid JSON matching this schema. Do not wrap it in markdown.\n"
            f"<json_schema>\n{json.dumps(schema, ensure_ascii=False)}\n</json_schema>"
        )
        serialized = ResponsesAPIMessageSerializer.serialize_messages(messages)
        if serialized and serialized[0].get("role") == "system":
            content = serialized[0].get("content")
            if isinstance(content, str):
                serialized[0]["content"] = f"{content}\n\n{instruction}"
            elif isinstance(content, list):
                serialized[0]["content"] = [
                    *content,
                    {"type": "input_text", "text": f"\n\n{instruction}"},
                ]
            else:
                serialized.insert(0, {"role": "system", "content": instruction})
            return serialized
        return [{"role": "system", "content": instruction}, *serialized]

    @staticmethod
    def _usage(response: Any) -> ChatInvokeUsage | None:
        raw_usage = getattr(response, "usage", None)
        if raw_usage is None:
            return None
        input_tokens = int(getattr(raw_usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(raw_usage, "output_tokens", 0) or 0)
        cached_tokens = None
        input_details = getattr(raw_usage, "input_tokens_details", None)
        if input_details is not None:
            cached = getattr(input_details, "cached_tokens", None)
            cached_tokens = int(cached) if cached is not None else None
        return ChatInvokeUsage(
            prompt_tokens=input_tokens,
            prompt_cached_tokens=cached_tokens,
            prompt_cache_creation_tokens=None,
            prompt_image_tokens=None,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )

    async def ainvoke(
        self,
        messages: list[Any],
        output_format: type[Any] | None = None,
        **kwargs: Any,
    ) -> ChatInvokeCompletion[Any]:
        from openai import APIConnectionError, APIStatusError, RateLimitError

        del kwargs
        if output_format is None:
            responses_input = ResponsesAPIMessageSerializer.serialize_messages(messages)
            model_params = self._model_params()
        else:
            responses_input = self._with_schema_prompt(messages, output_format)
            model_params = {
                **self._model_params(),
                "text": self._text_format(output_format),
            }

        try:
            response = await self.get_client().responses.create(
                model=self.model,
                input=responses_input,
                **model_params,
            )
        except RateLimitError as exc:
            raise ModelRateLimitError(message=exc.message, model=self.name) from exc
        except APIConnectionError as exc:
            raise ModelProviderError(message=str(exc), model=self.name) from exc
        except APIStatusError as exc:
            raise ModelProviderError(
                message=exc.message,
                status_code=exc.status_code,
                model=self.name,
            ) from exc

        text = getattr(response, "output_text", "") or ""
        usage = self._usage(response)
        if output_format is None:
            return ChatInvokeCompletion(completion=text, usage=usage, stop_reason=None)

        parsed = _validate_json_candidates(output_format, _iter_json_candidates(text))
        if parsed is None:
            raise ModelProviderError(
                message="Responses API model returned no valid structured output",
                status_code=500,
                model=self.name,
            )
        return ChatInvokeCompletion(completion=parsed, usage=usage, stop_reason=None)


def _match_step_llm_failures(
    pending_failures: list[dict[str, Any]],
    hist_item: Any,
) -> list[dict[str, Any]]:
    """Pop failures whose timestamps fall inside this history item's step window."""
    metadata = getattr(hist_item, "metadata", None)
    start = getattr(metadata, "step_start_time", None)
    end = getattr(metadata, "step_end_time", None)
    if start is None or end is None:
        return []
    matched = [failure for failure in pending_failures if start <= failure["timestamp"] <= end]
    for failure in matched:
        pending_failures.remove(failure)
    return matched


def _get_config_value(agent_config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in agent_config and agent_config[key] is not None:
            return agent_config[key]
    return default


@contextmanager
def _browser_use_cdp_connect_timeout(timeout_seconds: float) -> Iterator[None]:
    """Override browser-use SDK's hard-coded 15s CDP connect guard."""
    original_wait_for = browser_use_session_module.asyncio.wait_for

    async def wait_for_with_cdp_timeout(
        fut: Any,
        timeout: float | None = None,
    ) -> Any:
        if timeout == _BROWSER_USE_SDK_CDP_CONNECT_TIMEOUT_SECONDS:
            timeout = timeout_seconds
        return await original_wait_for(fut, timeout=timeout)

    browser_use_session_module.asyncio.wait_for = wait_for_with_cdp_timeout
    try:
        yield
    finally:
        browser_use_session_module.asyncio.wait_for = original_wait_for


def _browser_use_cdp_timeout_message(message: str) -> str:
    if "timed out after 15s" in message and "CDP connection" in message:
        return message.replace("15s", _BROWSER_USE_CDP_CONNECT_TIMEOUT_LABEL)
    return message


class BrowserUseBenchBrowser(BrowserUseSDKBrowser):
    """browser-use BrowserSession with benchmark-specific CDP connect timeout."""

    async def start(self, *args: Any, **kwargs: Any) -> Any:
        try:
            with _browser_use_cdp_connect_timeout(BROWSER_USE_CDP_CONNECT_TIMEOUT_SECONDS):
                return await super().start(*args, **kwargs)
        except RuntimeError as exc:
            message = _browser_use_cdp_timeout_message(str(exc))
            if message != str(exc):
                raise RuntimeError(message) from exc
            raise

    async def _auto_reconnect(self, *args: Any, **kwargs: Any) -> Any:
        try:
            with _browser_use_cdp_connect_timeout(BROWSER_USE_CDP_CONNECT_TIMEOUT_SECONDS):
                return await super()._auto_reconnect(*args, **kwargs)
        except RuntimeError as exc:
            message = _browser_use_cdp_timeout_message(str(exc))
            if message != str(exc):
                raise RuntimeError(message) from exc
            raise


Browser: type[Any] = BrowserUseBenchBrowser


def _history_errors(history: Any) -> list[str]:
    errors = getattr(history, "errors", None)
    if not callable(errors):
        return []
    try:
        raw_errors = errors() or []
    except (AttributeError, TypeError, ValueError):
        return []
    return [error for error in raw_errors if isinstance(error, str) and error]


def _history_reached_max_steps(
    *,
    history_errors: list[str],
    steps_count: int,
    max_steps: int,
) -> bool:
    if steps_count >= max_steps:
        return True
    return any("maximum steps" in error.lower() or "max steps" in error.lower() for error in history_errors)


def _unfinished_history_error(
    *,
    history_errors: list[str],
    steps_count: int,
) -> str:
    if history_errors:
        return history_errors[-1]
    return f"Agent stopped before completion after {steps_count} steps without reporting done"


@register_agent
class BrowserUseAgent(BaseAgent):
    """
    Browser automation agent using the browser-use library.

    Supports multiple LLM providers (OpenAI, Gemini, Browser-Use)
    and browser backends (local Chrome, Lexmount cloud, browser-use cloud, AgentBay cloud).
    """

    name = "browser-use"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a browser automation task using browser-use."""
        timeout = self.get_timeout(agent_config, 300)
        flash_mode = _get_config_value(agent_config, "flash_mode", "FLASH_MODE", default=True)
        browser_id = _get_config_value(agent_config, "browser_id", "BROWSER_ID", default="Chrome-Local")

        with open_browser_session(
            browser_id=browser_id,
            agent_name=self.name,
            agent_config=agent_config,
        ) as session_context:
            return asyncio.run(
                self._run_task_async(
                    task_info=task_info,
                    task_workspace=task_workspace,
                    timeout=timeout,
                    flash_mode=flash_mode,
                    agent_config=agent_config,
                    session_context=session_context,
                )
            )

    @staticmethod
    def _create_browser_instance(
        session_context: BrowserSessionContext,
    ) -> tuple[Any, tempfile.TemporaryDirectory[str] | None]:
        """
        Build browser runtime from unified backend session context.

        Browser backend ownership/lifecycle stays outside agent business logic.
        """
        browser_id = session_context.backend_id
        transport = session_context.transport
        cdp_url = session_context.cdp_url
        temp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
        if transport == "cdp":
            if not cdp_url:
                raise ValueError(f"CDP URL is required for browser id: {browser_id}")
            browser = Browser(
                viewport={"width": 1920, "height": 1080},
                headless=False,
                cdp_url=cdp_url,
            )
        elif transport == "cloud_native" and browser_id == "browser-use-cloud":
            browser = Browser(use_cloud=True)
        elif transport == "local":
            temp_dir_obj = tempfile.TemporaryDirectory(prefix="browseruse-tmp-user-data-")
            user_data_dir = Path(temp_dir_obj.name)
            local_browser_kwargs: dict[str, Any] = {
                "headless": bool(session_context.metadata.get("headless", False)),
                "user_data_dir": user_data_dir,
            }
            executable_path = str(session_context.metadata.get("executable_path") or "").strip()
            if executable_path:
                local_browser_kwargs["executable_path"] = executable_path
            proxy_meta = session_context.metadata.get("local_proxy")
            if proxy_meta:
                local_browser_kwargs["proxy"] = BrowserUseProxySettings(
                    server=proxy_meta.get("server"),
                    username=proxy_meta.get("username"),
                    password=proxy_meta.get("password"),
                    bypass=proxy_meta.get("bypass"),
                )
            browser = Browser(**local_browser_kwargs)
        else:
            raise ValueError(
                f"Unsupported browser backend for browser-use agent: "
                f"backend_id={browser_id}, transport={transport}"
            )
        return browser, temp_dir_obj

    @staticmethod
    async def _close_browser_runtime(browser: Any, task_id: str) -> None:
        """Close browser-use runtime object; backend session is closed by manager."""
        try:
            await browser.stop()
        except (
            OSError,
            RuntimeError,
            TimeoutError,
        ) as exc:
            logger.error(f"Failed to close browser runtime for task {task_id}: {exc}")

    async def _run_task_async(
        self,
        task_info: dict[str, Any],
        task_workspace: Path,
        timeout: int,
        flash_mode: bool,
        agent_config: dict[str, Any],
        session_context: BrowserSessionContext,
    ) -> AgentResult:
        """Async implementation of task execution."""
        task_id = task_info["task_id"]

        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)
        # TODO No action list
        task_prompt = self.build_task_prompt(task_info)

        # Read parameters from configuration dictionary
        model_type: str = _get_config_value(agent_config, "model_type", "MODEL_TYPE", default="")
        model_id: str = _get_config_value(agent_config, "model_id", "MODEL_ID", default="")
        browser_id = _get_config_value(agent_config, "browser_id", "BROWSER_ID", default="Chrome-Local")
        use_vision = _get_config_value(agent_config, "use_vision", "USE_VISION", default=False)
        max_steps = self.get_max_steps(agent_config, 40)
        save_api_logs = _get_config_value(agent_config, "save_api_logs", "SAVE_API_LOGS", default=True)

        config_info = {
            "timeout_seconds": timeout,
            "flash_mode": flash_mode,
            "use_vision": use_vision,
            "max_steps": max_steps,
            "save_api_logs": save_api_logs,
        }

        # Initialize LLM based on model type, this is a BU specific implementation, and different
        # models have different SDK preferences for utilizing the inference feature in agent scene.
        llm = self._create_llm(model_type, model_id, agent_config, config_info)
        llm_recorder = _LLMFailureRecorder()
        _capture_llm_failures(llm, llm_recorder)

        # Initialize Browser
        agent = None
        history = None
        browser, temp_dir_obj = self._create_browser_instance(session_context=session_context)

        start_time = time.time()
        error_msg = None

        try:
            agent = Agent(
                browser=browser,
                task=task_prompt,
                llm=llm,
                calculate_cost=True,
                flash_mode=flash_mode,
                use_vision=use_vision,
                use_judge=_get_config_value(agent_config, "use_judge", "USE_JUDGE", default=False),
            )
            _patch_agent_output_json_parsers(agent)

            history = await asyncio.wait_for(agent.run(max_steps=max_steps), timeout=timeout)
        except TimeoutError:
            error_msg = f"Timeout after {timeout} seconds"
            logger.error(f"Task {task_id} timed out after {timeout} seconds")
        except (RuntimeError, OSError, TypeError, ValueError) as e:
            error_msg = str(e)
            logger.error(f"Task {task_id} execution error: {e}")
        finally:
            # Backend session cleanup is handled by open_browser_session(...).
            await self._close_browser_runtime(browser=browser, task_id=task_id)

            if temp_dir_obj:
                try:
                    temp_dir_obj.cleanup()
                except (OSError, RuntimeError) as exc:
                    logger.warning(
                        "Failed to cleanup temporary directory for task %s: %s",
                        task_id,
                        exc,
                    )

        end_time = time.time()
        end_to_end_ms = int((end_time - start_time) * 1000)

        # Process History and return result
        screenshot_count = 0
        usage_data = {}
        action_history = []
        steps_count = 0

        if agent:
            # Attempt to get usage data even if history is partial
            try:
                if hasattr(agent, "token_cost_service"):
                    usage_summary = await agent.token_cost_service.get_usage_summary()
                    if usage_summary:
                        usage_data = json.loads(usage_summary.model_dump_json())
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.error(f"Failed to parse usage summary for task {task_id}: {exc}")

            if hasattr(agent, "history"):
                history = agent.history
                action_history = history.extracted_content() or []
                steps_count = history.number_of_steps()

                # Save screenshots
                for i, b64_data in enumerate(history.screenshots() or [], 1):
                    if self.save_screenshot(b64_data, i, trajectory_dir):
                        screenshot_count += 1
                    elif b64_data:
                        logger.error(f"Failed to save screenshot {i} for task {task_id}")

                # Generate API logs (system_prompt.txt + step_XXX.json + summary.md)
                if save_api_logs:
                    api_logs_dir = task_workspace / "api_logs"
                    api_logs_dir.mkdir(parents=True, exist_ok=True)

                    # Extract system prompt from agent
                    system_prompt = None
                    try:
                        if hasattr(agent, "message_manager") and hasattr(
                            agent.message_manager, "system_prompt"
                        ):
                            sp = agent.message_manager.system_prompt
                            system_prompt = (
                                sp.text if hasattr(sp, "text") else (str(sp) if sp else None)
                            )
                    except (AttributeError, TypeError, ValueError) as exc:
                        logger.debug("Could not extract system_prompt: %s", exc)

                    try:
                        api_logger = APICallLogger(api_logs_dir, task_id, model_id, system_prompt)
                        pending_llm_failures = list(llm_recorder.failures)
                        for i, hist_item in enumerate(history.history, 1):
                            api_logger.log_step(
                                step_number=i,
                                model_output=hist_item.model_output,
                                action_results=hist_item.result,
                                state=hist_item.state,
                                state_message=getattr(hist_item, "state_message", None),
                                llm_failures=_match_step_llm_failures(pending_llm_failures, hist_item),
                            )
                        api_logger.log_unmatched_llm_failures(pending_llm_failures)
                        api_logger.finalize(usage_data)
                    except Exception as exc:
                        logger.warning(f"Failed to generate API logs for task {task_id}: {exc}")

        # Determine env_status, agent_done, and agent_success
        agent_success: bool | None = None
        history_errors = _history_errors(history) if history else []
        max_steps_reached = (
            _history_reached_max_steps(
                history_errors=history_errors,
                steps_count=steps_count,
                max_steps=max_steps,
            )
            if history
            else False
        )
        unfinished_error = None
        if not error_msg and history and not history.is_done():
            unfinished_error = (
                _MAX_STEPS_ERROR
                if max_steps_reached
                else _unfinished_history_error(
                    history_errors=history_errors,
                    steps_count=steps_count,
                )
            )
        elif not error_msg and not history:
            unfinished_error = "Agent returned no history before completion"

        final_answer = ""
        if error_msg:
            final_answer = f"[Task Failed: {error_msg}]"
        elif history and history.is_done():
            final_answer = history.final_result() or ""
        elif unfinished_error:
            final_answer = f"[Task Failed: {unfinished_error}]"

        if error_msg and "Timeout" in error_msg:
            # Timeout: environment is fine, agent was killed by timeout
            env_status = "success"
            agent_done = "timeout"
        elif error_msg:
            # Other errors: environment failed
            env_status = "failed"
            agent_done = "error"
        elif history and history.is_done():
            # Agent self-reported completion — check success parameter
            env_status = "success"
            agent_done = "done"
            agent_success = history.is_successful()
        elif max_steps_reached:
            env_status = "success"
            agent_done = "max_steps"
        else:
            env_status = "failed"
            agent_done = "error"

        return AgentResult(
            task_id=task_id,
            task=task_prompt,
            timestamp=datetime.now(UTC),
            env_status=env_status,  # type: ignore[arg-type]
            agent_done=agent_done,  # type: ignore[arg-type]
            agent_success=agent_success,
            answer=final_answer,
            model_id=model_id or "",
            browser_id=browser_id,
            action_history=action_history,
            metrics=AgentMetrics(
                ttft_ms=int(end_to_end_ms * 0.1) if steps_count > 0 else 0,
                end_to_end_ms=end_to_end_ms,
                steps=steps_count,
                usage=AgentUsage(**usage_data) if usage_data else None,
            ),
            config=config_info,
            error=error_msg or unfinished_error,
        )

    def _create_llm(
        self,
        model_type: str,
        model_id: str,
        agent_config: dict[str, Any],
        config_info: dict[str, Any],
    ) -> Any:
        # TODO Why not Claude?
        """Create LLM instance based on model type."""
        provider_builders: dict[str, Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
            "BROWSER_USE": self._build_browser_use_kwargs,
            "OPENAI": self._build_openai_kwargs,
            "AZURE": self._build_azure_kwargs,
            "GEMINI": self._build_gemini_kwargs,
            "ANTHROPIC": self._build_anthropic_kwargs,
        }
        provider_classes: dict[str, type[Any]] = {
            "BROWSER_USE": ChatBrowserUse,
            "OPENAI": ChatOpenAI,
            "AZURE": ChatAzureOpenAI,
            "GEMINI": ChatGoogle,
            "ANTHROPIC": ChatAnthropic,
        }

        if model_type not in provider_classes:
            raise ValueError(f"Invalid model type: {model_type}")

        is_claude = _is_claude_model(model_id)
        if is_claude:
            _patch_schema_optimizer_for_claude()
            config_info["claude_schema_numeric_bounds_stripped"] = True

        llm_class = provider_classes[model_type]
        if model_type == "OPENAI" and agent_config.get("model_api_style") == "responses":
            kwargs = self._build_openai_responses_kwargs(model_id, agent_config, config_info)
            llm = _BrowserUseResponsesLLM(**kwargs)
        else:
            kwargs = provider_builders[model_type](model_id, agent_config, config_info)
            llm = llm_class(**kwargs)

        if is_claude and model_type in ("OPENAI", "AZURE"):
            thinking_enabled = _get_config_value(
                agent_config,
                "claude_thinking",
                "CLAUDE_THINKING",
                default=True,
            )
            if thinking_enabled:
                reasoning_effort = _get_config_value(
                    agent_config,
                    "claude_reasoning_effort",
                    "CLAUDE_REASONING_EFFORT",
                    default="high",
                )
                if reasoning_effort not in _VALID_REASONING_EFFORTS:
                    logger.warning(
                        "Invalid claude_reasoning_effort=%r; falling back to 'high'",
                        reasoning_effort,
                    )
                    reasoning_effort = "high"
                _enable_claude_thinking(llm, reasoning_effort)
                config_info["claude_reasoning_effort"] = reasoning_effort

        return llm

    @staticmethod
    def _build_browser_use_kwargs(
        model_id: str,
        agent_config: dict[str, Any],
        config_info: dict[str, Any],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": model_id}
        if "timeout" in agent_config:
            kwargs["timeout"] = agent_config["timeout"]
        if "max_retries" in agent_config:
            kwargs["max_retries"] = agent_config["max_retries"]
        if agent_config.get("api_key"):
            kwargs["api_key"] = agent_config["api_key"]
        return kwargs

    @staticmethod
    def _build_openai_kwargs(
        model_id: str,
        agent_config: dict[str, Any],
        config_info: dict[str, Any],
    ) -> dict[str, Any]:
        dont_force_structured_output = agent_config.get("dont_force_structured_output", False)
        add_schema_to_system_prompt = agent_config.get("add_schema_to_system_prompt", False)
        config_info["dont_force_structured_output"] = dont_force_structured_output
        config_info["add_schema_prompt"] = add_schema_to_system_prompt

        openai_kwargs: dict[str, Any] = {
            "model": model_id,
            "api_key": agent_config.get("api_key") or os.getenv("OPENAI_API_KEY"),
            "base_url": agent_config.get("base_url") or os.getenv("OPENAI_BASE_URL"),
            "dont_force_structured_output": dont_force_structured_output,
            "add_schema_to_system_prompt": add_schema_to_system_prompt,
        }
        if "temperature" in agent_config:
            temperature = agent_config["temperature"]
            openai_kwargs["temperature"] = temperature
            config_info["temperature"] = temperature
        if "frequency_penalty" in agent_config:
            frequency_penalty = agent_config["frequency_penalty"]
            openai_kwargs["frequency_penalty"] = frequency_penalty
            if frequency_penalty is not None:
                config_info["frequency_penalty"] = frequency_penalty
        if agent_config.get("max_tokens") is not None:
            openai_kwargs["max_completion_tokens"] = agent_config["max_tokens"]
            config_info["max_tokens"] = agent_config["max_tokens"]
        elif agent_config.get("max_completion_tokens") is not None:
            openai_kwargs["max_completion_tokens"] = agent_config["max_completion_tokens"]
            config_info["max_completion_tokens"] = agent_config["max_completion_tokens"]
        if agent_config.get("remove_min_items_from_schema"):
            openai_kwargs["remove_min_items_from_schema"] = True
        if agent_config.get("remove_defaults_from_schema"):
            openai_kwargs["remove_defaults_from_schema"] = True
        return openai_kwargs

    @staticmethod
    def _build_openai_responses_kwargs(
        model_id: str,
        agent_config: dict[str, Any],
        config_info: dict[str, Any],
    ) -> dict[str, Any]:
        responses_kwargs: dict[str, Any] = {
            "model": model_id,
            "api_key": (
                agent_config.get("api_key")
                or os.getenv("XAI_API_KEY")
                or os.getenv("GROK_API_KEY")
                or os.getenv("OPENAI_API_KEY")
            ),
            "base_url": agent_config.get("base_url") or os.getenv("OPENAI_BASE_URL"),
            "add_schema_to_system_prompt": True,
        }
        config_info["model_api_style"] = "responses"
        config_info["add_schema_prompt"] = True
        if "temperature" in agent_config:
            responses_kwargs["temperature"] = agent_config["temperature"]
            config_info["temperature"] = agent_config["temperature"]
        if agent_config.get("max_tokens") is not None:
            responses_kwargs["max_output_tokens"] = agent_config["max_tokens"]
            config_info["max_tokens"] = agent_config["max_tokens"]
        elif agent_config.get("max_completion_tokens") is not None:
            responses_kwargs["max_output_tokens"] = agent_config["max_completion_tokens"]
            config_info["max_completion_tokens"] = agent_config["max_completion_tokens"]
        return responses_kwargs

    @staticmethod
    def _build_azure_kwargs(
        model_id: str,
        agent_config: dict[str, Any],
        config_info: dict[str, Any],
    ) -> dict[str, Any]:
        dont_force_structured_output = agent_config.get("dont_force_structured_output", False)
        add_schema_to_system_prompt = agent_config.get("add_schema_to_system_prompt", False)
        use_responses_api = agent_config.get("use_responses_api", True)
        config_info["dont_force_structured_output"] = dont_force_structured_output
        config_info["add_schema_prompt"] = add_schema_to_system_prompt

        azure_kwargs: dict[str, Any] = {
            "model": model_id,
            "api_key": agent_config.get("api_key") or os.getenv("OPENAI_API_KEY"),
            "base_url": agent_config.get("base_url") or os.getenv("OPENAI_BASE_URL"),
            "use_responses_api": use_responses_api,
            "dont_force_structured_output": dont_force_structured_output,
            "add_schema_to_system_prompt": add_schema_to_system_prompt,
        }
        if "temperature" in agent_config:
            temperature = agent_config["temperature"]
            azure_kwargs["temperature"] = temperature
            config_info["temperature"] = temperature
        if "frequency_penalty" in agent_config:
            frequency_penalty = agent_config["frequency_penalty"]
            azure_kwargs["frequency_penalty"] = frequency_penalty
            if frequency_penalty is not None:
                config_info["frequency_penalty"] = frequency_penalty
        if agent_config.get("max_tokens") is not None:
            azure_kwargs["max_completion_tokens"] = agent_config["max_tokens"]
            config_info["max_tokens"] = agent_config["max_tokens"]
        elif agent_config.get("max_completion_tokens") is not None:
            azure_kwargs["max_completion_tokens"] = agent_config["max_completion_tokens"]
            config_info["max_completion_tokens"] = agent_config["max_completion_tokens"]
        if agent_config.get("remove_min_items_from_schema"):
            azure_kwargs["remove_min_items_from_schema"] = True
        if agent_config.get("remove_defaults_from_schema"):
            azure_kwargs["remove_defaults_from_schema"] = True
        return azure_kwargs

    @staticmethod
    def _build_gemini_kwargs(
        model_id: str,
        agent_config: dict[str, Any],
        config_info: dict[str, Any],
    ) -> dict[str, Any]:
        google_config: dict[str, Any] = {}
        gemini_thinking_models = {"gemini-3-flash-preview", "gemini-3-pro-preview", "gemini-3.1-pro-preview"}

        if model_id in gemini_thinking_models:
            thinking_level = agent_config.get("gemini3_thinking_level")
            if thinking_level:
                google_config["thinking_config"] = {"thinking_level": thinking_level}
                config_info["thinking_level"] = thinking_level

        gemini_base_url = agent_config.get("base_url") or os.getenv("GEMINI_BASE_URL")
        http_opts: dict[str, Any] = {}
        if gemini_base_url:
            http_opts["base_url"] = gemini_base_url.rstrip("/")
            http_opts["api_version"] = ""

        return {
            "model": model_id,
            "api_key": agent_config.get("api_key") or os.getenv("GEMINI_API_KEY"),
            "http_options": http_opts,
            "config": google_config,
        }

    @staticmethod
    def _build_anthropic_kwargs(
        model_id: str,
        agent_config: dict[str, Any],
        config_info: dict[str, Any],
    ) -> dict[str, Any]:
        max_tokens = agent_config.get("anthropic_max_tokens", 8192)
        max_retries = agent_config.get("anthropic_max_retries", 10)
        config_info["max_tokens"] = max_tokens
        config_info["max_retries"] = max_retries
        return {
            "model": model_id,
            "api_key": agent_config.get("api_key") or os.getenv("ANTHROPIC_API_KEY"),
            "base_url": agent_config.get("base_url") or os.getenv("ANTHROPIC_BASE_URL"),
            "max_tokens": max_tokens,
            "max_retries": max_retries,
        }
