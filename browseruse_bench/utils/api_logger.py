"""
API Logger - Records complete LLM API calls for each task execution.

This module provides functionality to log complete input/output data
from LLM API calls in a structured and human-readable format.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class APICallLogger:
    """
    Manages API call logging for a single task execution.

    Logs each LLM API call to individual JSON files and generates
    a human-readable summary.md file.
    """

    def __init__(self, api_logs_dir: Path, task_id: str, model_id: str, system_prompt: str | None = None):
        """
        Initialize the API call logger.

        Args:
            api_logs_dir: Directory where API logs will be saved.
            task_id: Unique identifier for the task.
            model_id: Identifier of the LLM model being used.
            system_prompt: The system prompt sent to LLM.
        """
        self.api_logs_dir = api_logs_dir
        self.task_id = task_id
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.steps: list[dict[str, Any]] = []

        # Save system prompt immediately
        if system_prompt:
            self._save_system_prompt()

    def _save_system_prompt(self) -> None:
        """Save system prompt to a separate file."""
        if not self.system_prompt:
            return

        prompt_file = self.api_logs_dir / "system_prompt.txt"
        try:
            prompt_file.write_text(self.system_prompt, encoding="utf-8")
        except OSError as exc:
            logger.warning(f"Failed to write system_prompt.txt: {exc}")

    def log_step(
        self,
        step_number: int,
        model_output: Any,
        action_results: list[Any] | None,
        state: Any | None,
        state_message: str | None,
        llm_failures: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Log a single LLM API call step.

        Args:
            step_number: The step number (1-indexed).
            model_output: The model's output for this step.
            action_results: Results from executing the actions.
            state: Browser state information.
            state_message: Complete state message sent to LLM (contains browser_state, agent_history, etc.).
            llm_failures: Failed LLM call records for this step (raw response text,
                token usage, error) captured before the SDK discarded them.
        """
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")

        # Extract URL from state if available
        url = None
        if state:
            url = getattr(state, "url", None)

        # Parse model output
        output_data = self._parse_model_output(model_output)

        # Parse action results
        results_data = self._parse_action_results(action_results)

        step_data = {
            "metadata": {
                "task_id": self.task_id,
                "step_number": step_number,
                "timestamp": timestamp,
                "model_id": self.model_id,
            },
            "input": {
                "url": url,
                "state_message": state_message,  # Complete input to LLM
                "screenshot_ref": f"trajectory/screenshot-{step_number}.png",
            },
            "output": output_data,
            "action_results": results_data,
            "llm_failures": llm_failures or [],
        }

        self.steps.append(step_data)

        # Write individual step file immediately
        step_file = self.api_logs_dir / f"step_{step_number:03d}.json"
        try:
            step_file.write_text(json.dumps(step_data, ensure_ascii=False, indent=2))
        except (OSError, TypeError) as exc:
            logger.warning(f"Failed to write step {step_number} log: {exc}")

    def _parse_model_output(self, model_output: Any) -> dict[str, Any]:
        """Extract relevant fields from model output."""
        if model_output is None:
            return {}

        output_data: dict[str, Any] = {}

        # Try to extract common fields from browser-use AgentOutput
        if hasattr(model_output, "current_state"):
            current_state = model_output.current_state
            if current_state:
                if hasattr(current_state, "evaluation_previous_goal"):
                    output_data["thinking"] = current_state.evaluation_previous_goal
                if hasattr(current_state, "memory"):
                    output_data["memory"] = current_state.memory
                if hasattr(current_state, "next_goal"):
                    output_data["next_goal"] = current_state.next_goal

        if hasattr(model_output, "action"):
            actions = model_output.action
            if actions:
                output_data["actions"] = self._serialize_actions(actions)

        return output_data

    def _serialize_actions(self, actions: Any) -> list[dict[str, Any]]:
        """Serialize action objects to dictionaries."""
        if actions is None:
            return []

        action_list = actions if isinstance(actions, list) else [actions]
        serialized = []

        for action in action_list:
            if action is None:
                continue

            # Try to convert to dict using model_dump or __dict__
            if hasattr(action, "model_dump"):
                action_dict = action.model_dump(exclude_none=True)
            elif hasattr(action, "__dict__"):
                action_dict = {k: v for k, v in action.__dict__.items() if v is not None}
            else:
                action_dict = {"raw": str(action)}

            serialized.append(action_dict)

        return serialized

    def _parse_action_results(self, action_results: list[Any] | None) -> list[dict[str, Any]]:
        """Parse action results into serializable format."""
        if not action_results:
            return []

        results = []
        for result in action_results:
            if result is None:
                continue

            result_data: dict[str, Any] = {}

            if hasattr(result, "extracted_content"):
                result_data["extracted_content"] = result.extracted_content
            if hasattr(result, "error"):
                result_data["error"] = str(result.error) if result.error else None
            if hasattr(result, "is_done"):
                result_data["is_done"] = result.is_done

            # Fallback: try to serialize the whole object
            if not result_data:
                if hasattr(result, "model_dump"):
                    result_data = result.model_dump(exclude_none=True)
                elif hasattr(result, "__dict__"):
                    result_data = {k: v for k, v in result.__dict__.items() if v is not None}
                else:
                    result_data = {"raw": str(result)}

            results.append(result_data)

        return results

    def log_unmatched_llm_failures(self, failures: list[dict[str, Any]]) -> None:
        """
        Write failed LLM call records that could not be attributed to a step.

        Args:
            failures: Failure records (raw response text, usage, error) whose
                timestamps fell outside every step window.
        """
        if not failures:
            return

        failures_file = self.api_logs_dir / "llm_failures_unmatched.json"
        try:
            failures_file.write_text(json.dumps(failures, ensure_ascii=False, indent=2))
        except (OSError, TypeError) as exc:
            logger.warning(f"Failed to write llm_failures_unmatched.json: {exc}")

    @staticmethod
    def _render_llm_failures(llm_failures: list[dict[str, Any]]) -> list[str]:
        """Render failed LLM call records as summary.md lines."""
        lines = ["### LLM Failures", ""]
        for failure in llm_failures:
            lines.append(f"- **Error**: {failure.get('error')}")
            raw_response = failure.get("raw_response")
            if not raw_response:
                continue
            lines.extend(
                [
                    "",
                    "<details>",
                    "<summary>Raw LLM response</summary>",
                    "",
                    "```",
                    raw_response,
                    "```",
                    "",
                    "</details>",
                ]
            )
        lines.append("")
        return lines

    def finalize(self, usage_data: dict[str, Any] | None = None) -> None:
        """
        Generate the summary.md file with all steps.

        Args:
            usage_data: Token usage and cost data from the agent.
        """
        if not self.steps:
            return

        lines = ["# LLM API Call Log\n"]
        lines.append("## Task Info")
        lines.append(f"- **Task ID**: {self.task_id}")
        lines.append(f"- **Model**: {self.model_id}")
        lines.append(f"- **Total Steps**: {len(self.steps)}")

        # Add cost info if available
        if usage_data:
            total_cost = usage_data.get("total_cost", 0)
            if total_cost:
                lines.append(f"- **Total Cost**: ${total_cost:.4f}")

        lines.append("")

        # Reference to system prompt
        if self.system_prompt:
            lines.append("## System Prompt")
            lines.append("")
            lines.append("See [system_prompt.txt](./system_prompt.txt) for the complete system prompt.")
            lines.append("")
            lines.append("<details>")
            lines.append("<summary>Click to expand system prompt</summary>")
            lines.append("")
            lines.append("```")
            lines.append(self.system_prompt)
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        lines.append("---\n")

        # Generate step details
        for step_data in self.steps:
            metadata = step_data.get("metadata", {})
            input_data = step_data.get("input", {})
            output_data = step_data.get("output", {})
            results = step_data.get("action_results", [])

            step_num = metadata.get("step_number", "?")
            timestamp = metadata.get("timestamp", "")

            lines.append(f"## Step {step_num} ({timestamp})")
            lines.append("")

            # URL
            url = input_data.get("url")
            if url:
                lines.append(f"**URL**: {url}")
                lines.append("")

            # Screenshot reference
            screenshot_ref = input_data.get("screenshot_ref")
            if screenshot_ref:
                lines.append(f"**Screenshot**: [{screenshot_ref}](../{screenshot_ref})")
                lines.append("")

            # === INPUT SECTION ===
            lines.append("### Input (State Message)")
            lines.append("")
            state_message = input_data.get("state_message")
            if state_message:
                lines.append("<details>")
                lines.append("<summary>Click to expand full state message</summary>")
                lines.append("")
                lines.append("```")
                lines.append(state_message)
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")
            else:
                lines.append("*No state message available*")
                lines.append("")

            # === OUTPUT SECTION ===
            lines.append("### Output (Model Response)")
            lines.append("")

            # Thinking
            thinking = output_data.get("thinking")
            if thinking:
                lines.append(f"**Thinking**: {thinking}")
                lines.append("")

            # Memory
            memory = output_data.get("memory")
            if memory:
                lines.append(f"**Memory**: {memory}")
                lines.append("")

            # Next Goal
            next_goal = output_data.get("next_goal")
            if next_goal:
                lines.append(f"**Next Goal**: {next_goal}")
                lines.append("")

            # Actions
            actions = output_data.get("actions", [])
            if actions:
                lines.append("**Actions**:")
                for i, action in enumerate(actions, 1):
                    try:
                        action_str = json.dumps(action, ensure_ascii=False)
                    except TypeError:
                        action_str = str(action)
                    lines.append(f"{i}. `{action_str}`")
                lines.append("")

            # === RESULTS SECTION ===
            if results:
                lines.append("### Action Results")
                lines.append("")
                for result in results:
                    content = result.get("extracted_content")
                    error = result.get("error")
                    is_done = result.get("is_done")
                    if content:
                        lines.append(f"- {content}")
                    if error:
                        lines.append(f"- **Error**: {error}")
                    if is_done:
                        lines.append(f"- **Done**: {is_done}")
                lines.append("")

            # === LLM FAILURES SECTION ===
            llm_failures = step_data.get("llm_failures") or []
            if llm_failures:
                lines.extend(self._render_llm_failures(llm_failures))

            lines.append("---\n")

        # Write summary.md
        md_file = self.api_logs_dir / "summary.md"
        try:
            md_file.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            logger.warning(f"Failed to write summary.md: {exc}")
