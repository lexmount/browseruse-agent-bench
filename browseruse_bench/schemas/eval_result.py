"""Evaluation result schema — standardized output for all benchmark evaluations."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from browseruse_bench.schemas._types import UTCDatetime
from browseruse_bench.schemas.agent_result import AgentMetrics
from browseruse_bench.schemas.prompt import PromptSnapshot


class EvalUsage(BaseModel):
    """Token usage from the evaluation LLM call."""

    model_config = ConfigDict(extra="allow")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    non_cached_prompt: int = 0
    costs: dict[str, float] | None = None


class EvalDetails(BaseModel):
    """Evaluation details.

    ``agent_metrics`` is stored as a nested object — never flat-merged — which
    fixes the ``usage`` key-override bug that occurred with ``dict.update()``.
    """

    model_config = ConfigDict(extra="allow")

    # Evaluator prompt snapshots (v1.1 — typed discriminated union)
    system_prompt: PromptSnapshot | None = None
    user_prompt: PromptSnapshot | None = None
    additional_prompts: dict[str, PromptSnapshot] | None = None

    # Evaluator output
    response: str = ""
    score: int | None = None
    is_correct: bool | None = None
    reasoning: str | None = None
    eval_usage: EvalUsage | None = None

    # Agent metrics (nested, NOT flat-merged)
    agent_metrics: AgentMetrics | None = None

    # Benchmark-specific fields
    benchmark_details: dict[str, Any] = Field(default_factory=dict)


class FailureClassification(BaseModel):
    """Failure analysis classification."""

    category: str
    codes: list[str] = Field(default_factory=list)
    reasoning: str = ""
    other_phrase: str | None = None
    legacy_category: str | None = None
    raw_response: str | None = None


class AgentResultRef(BaseModel):
    """Reference back to the original AgentResult for traceability."""

    task_id: str
    timestamp: UTCDatetime
    result_dir: str
    model_id: str = ""
    browser_id: str = ""


class EvalResult(BaseModel):
    """Standardized evaluation result.

    Top-level fields use ``extra="forbid"`` to catch unexpected keys.
    """

    model_config = ConfigDict(extra="forbid")

    # Version
    schema_version: Literal["1.0", "1.1"] = "1.1"

    # Identity
    task_id: str
    task: str = ""

    # Timestamp
    timestamp: UTCDatetime

    # Agent result traceability
    agent_result_ref: AgentResultRef

    # Verdict
    predicted_label: int  # 1 = success, 0 = failure

    # Shortcut fields
    model_id: str = ""
    browser_id: str = ""

    # Evaluation details
    evaluation_details: EvalDetails

    # Failure analysis
    failure_category: str | None = None
    failure_classification: FailureClassification | None = None

    # Benchmark-specific top-level fields
    task_type: str | None = None
    correct_answer: str | None = None
    agent_response: str | None = None
