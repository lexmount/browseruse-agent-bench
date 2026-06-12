"""Evaluation model client + image encoding helper."""

from __future__ import annotations

import base64
import contextvars
import io
import logging
import os
from typing import Any

try:
    import backoff
except ImportError:
    backoff = None

try:
    from openai import APIConnectionError, APIError, OpenAI, RateLimitError
except ImportError:
    APIConnectionError = None
    APIError = None
    OpenAI = None
    RateLimitError = None

try:
    from PIL import Image
except ImportError:
    Image = None

logger = logging.getLogger(__name__)

# Per-task context for log records. Set by BaseEvaluator at the top of each
# task iteration; read by ``_TaskIdFilter`` to inject ``task_id`` into log
# records so eval.log lines can be attributed to a specific task.
current_task_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "eval_current_task_id", default="-"
)


def default_temperature_for_model(model: str) -> float:
    """Return a safe default eval temperature for models with strict schemas."""
    if model.lower().startswith("gpt-5"):
        return 1.0
    return 0.3


def default_max_tokens_for_model(model: str, fallback: int = 2048) -> int:
    """Return a safe default eval output budget."""
    if model.lower().startswith("gpt-5"):
        return 8192
    return fallback


class TaskIdLogFilter(logging.Filter):
    """Logging filter that injects the current task_id into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = current_task_id.get()
        return True


def _log_backoff(details: dict[str, Any]) -> None:
    """Log retry details for backoff callbacks."""
    wait = float(details.get("wait", 0.0))
    exception = details.get("exception")
    logger.warning("[WARNING] Retrying in %.1fs due to %s", wait, exception)


def _is_non_retryable_api_error(exc: Exception) -> bool:
    """Return True for request errors that another retry will not fix."""
    return getattr(exc, "status_code", None) == 400


def encode_image(image, scale_factor: float = 1.0) -> str:
    """Convert a PIL image to base64 string.

    Args:
        image: PIL Image object
        scale_factor: Image scale factor (0.0 < scale_factor <= 1.0)
                     e.g., 0.5 means 50% of original size

    Returns:
        Base64 encoded string
    """
    if Image is None:
        raise ImportError("PIL is required. Install with: pip install Pillow")

    if image.mode == "RGBA":
        image = image.convert("RGB")

    # Apply scaling if scale_factor is not 1.0
    if scale_factor < 1.0 and scale_factor > 0:
        new_width = int(image.width * scale_factor)
        new_height = int(image.height * scale_factor)
        image = image.resize((new_width, new_height), resample=Image.Resampling.LANCZOS)

    buffered = io.BytesIO()
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


if OpenAI and backoff:

    class EvaluationModel:
        """OpenAI model wrapper for evaluation"""

        def __init__(
            self,
            model: str = "gpt-4.1",
            api_key: str = None,
            base_url: str = None,
            temperature: float | None = None,
        ):
            kwargs = {}
            if api_key:
                kwargs["api_key"] = api_key
            else:
                kwargs["api_key"] = os.getenv("OPENAI_API_KEY")

            if base_url:
                kwargs["base_url"] = base_url
            elif os.getenv("OPENAI_BASE_URL"):
                kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")

            if not kwargs.get("api_key"):
                raise ValueError("API key required: set OPENAI_API_KEY or pass api_key parameter")

            self.client = OpenAI(**kwargs)
            self.model = model
            self.temperature = temperature
            self.last_usage = None

        @backoff.on_exception(
            backoff.expo,
            (APIError, RateLimitError, APIConnectionError),
            max_tries=5,
            giveup=_is_non_retryable_api_error,
            on_backoff=_log_backoff,
        )
        def generate(
            self,
            messages: list[dict],
            max_tokens: int = 2048,
            temperature: float | None = None,
            **kwargs,
        ) -> str:
            """Generate response from OpenAI API with retry"""
            if temperature is None:
                temperature = (
                    self.temperature
                    if self.temperature is not None
                    else default_temperature_for_model(self.model)
                )
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            self.last_usage = getattr(response, "usage", None)
            choice = response.choices[0]
            content = choice.message.content
            if not content:
                refusal = getattr(choice.message, "refusal", None)
                tool_calls = getattr(choice.message, "tool_calls", None)
                logger.warning(
                    "Eval LLM returned empty content: model=%s finish_reason=%s refusal=%s tool_calls=%s",
                    self.model,
                    choice.finish_reason,
                    refusal,
                    bool(tool_calls),
                )
            return content or ""
else:
    # Fallback when dependencies are not installed
    class EvaluationModel:
        """OpenAI model wrapper for evaluation (stub)"""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "openai and backoff are required. Install with: pip install openai backoff"
            )


def load_evaluation_model(
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    temperature: float | None = None,
) -> EvaluationModel:
    """Load evaluation model with environment variable fallback

    Args:
        model: Model name (default: gpt-4.1)
        api_key: API key (default: EVAL_MODEL_API_KEY or OPENAI_API_KEY env var)
        base_url: API base URL (default: OPENAI_BASE_URL env var)

    Returns:
        EvaluationModel instance
    """
    model = model or "gpt-4.1"
    api_key = api_key or os.getenv("EVAL_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = base_url or os.getenv("OPENAI_BASE_URL")

    if not api_key:
        raise ValueError("API key required: set EVAL_MODEL_API_KEY or OPENAI_API_KEY")

    # Debug info
    logger.info(
        f"   [CONFIG] Eval config: model={model}, base_url={base_url[:50] + '...' if base_url and len(base_url) > 50 else base_url}"
    )

    return EvaluationModel(model, api_key, base_url, temperature=temperature)
