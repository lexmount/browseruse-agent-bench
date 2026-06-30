"""Agent result schema — standardized output for all agents."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from browseruse_bench.schemas._types import (
    AgentDoneStatus,
    EnvironmentStatus,
    NormalizedAgentDoneStatus,
    NormalizedEnvironmentStatus,
    UTCDatetime,
)
from browseruse_bench.schemas.prompt import PromptSnapshot


class AgentUsage(BaseModel):
    """Token usage statistics from the agent's LLM calls."""

    model_config = ConfigDict(extra="allow")

    # Token counts
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_prompt_cached_tokens: int = 0
    total_tokens: int = 0

    # Cost breakdown
    total_prompt_cost: float = 0.0
    total_prompt_cached_cost: float = 0.0
    total_completion_cost: float = 0.0
    total_cost: float = 0.0

    # Invocation info
    entry_count: int = 0
    by_model: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _auto_fill_total_tokens(self) -> "AgentUsage":
        if self.total_tokens == 0 and (self.total_prompt_tokens + self.total_completion_tokens) > 0:
            self.total_tokens = self.total_prompt_tokens + self.total_completion_tokens
        return self


class AgentMetrics(BaseModel):
    """Performance metrics from agent execution."""

    model_config = ConfigDict(extra="allow")

    ttft_ms: int | None = None
    end_to_end_ms: int
    steps: int
    usage: AgentUsage | None = None


class AgentResult(BaseModel):
    """Standardized result from any agent execution.

    Top-level fields use ``extra="forbid"`` to catch typos and stale keys.
    Agent-specific opaque data should go into ``agent_metadata``.
    """

    model_config = ConfigDict(extra="forbid")

    # Version
    schema_version: Literal["1.0", "1.1", "2.0"] = "2.0"

    # Identity
    task_id: str
    task: str = ""

    # Timestamp
    timestamp: UTCDatetime

    # Execution result
    # env_status: whether the environment/browser is working properly
    env_status: NormalizedEnvironmentStatus
    # agent_done: how the agent finished the task
    agent_done: NormalizedAgentDoneStatus
    # agent_success: agent's self-reported success/failure from done(success=...)
    #   True  = agent called done(success=true)
    #   False = agent called done(success=false)
    #   None  = agent didn't call done (timeout, max_steps, error), and agent_done is not "done"
    agent_success: bool | None = None
    answer: str = ""
    error: str | None = None

    # Context
    model_id: str = ""
    browser_id: str = ""

    # Trajectory
    action_history: list[str] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)

    # Performance
    metrics: AgentMetrics

    # Agent-specific opaque data (eval scripts must not read this)
    agent_metadata: dict[str, Any] = Field(default_factory=dict)

    # Run-level metadata injected by the runner (machine id, orchestration info)
    run_metadata: dict[str, Any] = Field(default_factory=dict)

    # Prompt snapshot
    system_prompt: PromptSnapshot | None = None

    # Configuration snapshot
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_result_consistency(self) -> "AgentResult":
        if self.env_status == EnvironmentStatus.SUCCESS and self.agent_done == AgentDoneStatus.DONE and self.error is not None:
            raise ValueError("env_status is 'success' and agent_done is 'done' but error is not None")
        if self.agent_success is not None and self.agent_done != AgentDoneStatus.DONE:
            raise ValueError(
                f"agent_success must be None when agent_done is '{self.agent_done}', "
                f"got {self.agent_success}"
            )
        return self
