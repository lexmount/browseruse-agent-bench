---
name: custom-agent-creator
description: "Create and integrate new modular agents for browseruse-bench (BaseAgent in browseruse_bench/agents/, registry import in __init__.py, entry in configs/agent_registry.yaml, runtime config in root config.yaml agents section). Use when a user asks to add/register a new agent, scaffold a custom agent, or implement a new agent based on external docs."
---

# Custom Agent Creation and Integration

## Minimum Inputs
- Confirm Agent name (registry key used in configs/agent_registry.yaml and root config.yaml)
- Confirm Python module name (browseruse_bench/agents/<module>.py)
- Confirm supported benchmarks (e.g., Online-Mind2Web / BrowseComp / LexBench-Browser)
- Collect external docs URL(s); read and extract usable SDK/API details
- Define config keys (model_id, api_key, base_url, timeout, SDK-specific options)
- Agent runtime config lives in root config.yaml under agents:<agent_name>; model/provider
  parameters are read from agent_config and injected into SDK constructors directly

## Required References
- browseruse_bench/agents/__init__.py  (check existing imports before adding)
- configs/agent_registry.yaml          (check existing agent registrations)

## Load when needed (do NOT read upfront)
- references/custom-agent-integration.md  — SDK integration patterns
- browseruse_bench/agents/base.py         — BaseAgent method signatures
- browseruse_bench/agents/registry.py     — @register_agent internals
- browseruse_bench/runner/agent_runner.py — runner entrypoint conventions
- browseruse_bench/cli/run.py             — extra dependency mapping
- browseruse_bench/utils/venv.py          — install_agent_dependencies (supports requirements_file)
- browseruse_bench/agents/browser_use.py  — simple agent reference example
- browseruse_bench/agents/agent_tars.py   — subprocess-based agent example
- browseruse_bench/agents/skyvern.py      — async/CDP agent example
- docs/en/agents/custom-agent.mdx         — user-facing docs template
- docs/zh/agents/custom-agent.mdx         — Chinese docs template

## Standard Workflow
1) Create module:
   - browseruse_bench/agents/<agent_module>.py
2) Implement BaseAgent subclass:
   - Add from __future__ import annotations
   - PEP8 import order: stdlib → third-party → local
   - Use typing annotations (Dict, Any, Path)
   - Do not use print(); use logger
   - Do not use sys.path.insert
   - Read all configurable values from agent_config (no hardcoding)
   - Pass model/provider parameters from agent_config directly into SDK constructors (avoid relying solely on environment variables)
   - If the upstream SDK has a hardcoded/whitelist model check, do not expose that limitation directly to browseruse-bench users when it can be avoided:
     use a provider-native bootstrap/default model only for SDK initialization, and after initialization replace the SDK's runtime inference client/engine with your own wrapper that honors the configured `model_id`, `api_key`, and `base_url`
   - Resolve paths with REPO_ROOT when touching files
3) Register module import:
   - Add `from browseruse_bench.agents import <agent_module>  # noqa: F401` to browseruse_bench/agents/__init__.py
4) Register agent in configs/agent_registry.yaml:
   - path: browseruse_bench/agents
   - entrypoint: browseruse_bench/runner/agent_runner.py
   - venv: .venv  (or .venvs/<agent> if it needs an isolated environment)
   - supported_benchmarks: [...]
5) Add runtime config to root config.yaml under agents:<agent_name>:
   - active_model: <default_model_key>
   - models: { <model_key>: { model_id, api_key, base_url, ... } }
   - browser: { browser_id, ... }   (if the agent uses a browser)
   - defaults: { timeout, max_steps, ... }
6) Add dependencies — two paths depending on whether the agent's deps conflict with core:

   **Path A — no conflict (happy path):**
   - Add extra group to pyproject.toml `[project.optional-dependencies]`: `<agent> = ["sdk>=x.y"]`
   - Map agent name → extra in browseruse_bench/cli/run.py (the `if/elif extra_name` block)
   - Registry entry uses only `venv: .venvs/<agent>` (no `requirements_file`)
   - Install: `uv venv .venvs/<agent> && uv pip install --python .venvs/<agent> -e ".[<agent>]"`

   **Path B — deps conflict with core project (e.g. older openai, older pydantic):**
   - Do NOT add to pyproject.toml extras (installing `.[extra]` would pull conflicting core deps)
   - Create `configs/requirements/<agent>.txt` with the SDK and minimum runtime deps:
     ```
     <sdk-package>
     pydantic>=2.0
     PyYAML>=6.0
     python-dotenv>=1.0
     ```
   - Add `requirements_file: configs/requirements/<agent>.txt` to the registry entry
   - browseruse_bench itself is available via PYTHONPATH — do not add it to the requirements file
   - install_agent_dependencies() in venv.py auto-detects `requirements_file` and installs from it
   - No change needed in cli/run.py (extra_name falls through to None; requirements_file takes over)
   - Install: `uv venv .venvs/<agent> && uv pip install --python .venvs/<agent> -r configs/requirements/<agent>.txt`

   **How to detect a conflict before committing:**
   Run `uv pip install --python .venvs/<agent> -e ".[<agent>]"` in a scratch venv first.
   If uv says "X and Y are incompatible", use Path B.

7) Optional sanity run:
   - uv run bubench run --agent <agent> --data <benchmark> --mode first_n --count 1

## Minimal Viable Variant (lean BaseAgent.run_task)
- Config keys from the root config.yaml `models`/`browser`/`defaults` sections are **not normalized**
  at load time — write lowercase in both the YAML and in Python (`model_id`, `base_url`, `browser_id`,
  `max_steps`, `timeout`, and agent-specific flags like `use_vision`, `flash_mode`).
- For common config keys use BaseAgent helpers (defined in `browseruse_bench/agents/base.py`,
  load when needed): `self.get_model_id(agent_config)`, `self.get_timeout(agent_config)`,
  `self.get_max_steps(agent_config)`, `self.get_api_key(agent_config, env_var="PROVIDER_API_KEY")`,
  `self.get_base_url(agent_config, env_var=...)`. These handle key aliases (`timeout`/`timeout_seconds`,
  `max_steps`/`max_turns`, etc.) and provide safe defaults. For agent-specific flags use
  `agent_config.get("flag_name")` directly.
- LLM branch only on `model_type` → ChatBrowserUse / ChatOpenAI / ChatGoogle; avoid large parameter maps.
- Browser session lifecycle must go through `open_browser_session(...)` from `browseruse_bench.browsers`.
- Keep provider-specific session create/delete logic in `browseruse_bench/browsers/providers/*.py`; do not duplicate this in agent modules.
- In agent code, branch by `session_context.transport` (`local` / `cdp` / `cloud_native`) instead of hardcoding provider SDK logic.
- History handling: decode base64 screenshots to `trajectory/`, collect basic actions, steps, end-to-end ms; keep result dict small.
- Trajectory screenshots are an expected artifact, not an optional extra: `AgentResult.screenshots`
  must list the files saved under `trajectory/`. If the agent framework can capture screenshots,
  make the default rules ask for at least one after reaching the main page and one after finding
  the answer (see the openclaw/hermes default rules). If capturing is deliberately disabled — for
  example the only screenshot path also triggers a vision-model call the bench model cannot serve
  (hermes `browser_vision`) — record that trade-off in the agent module's default rules or a module
  comment so a run with zero screenshots is explainable, and still keep the collection code path
  working for the day it is re-enabled.
- For provider semantics, routing, workspace, and missing-file edge cases, follow the guidance in
  `SDK Integration Pitfalls` below instead of repeating those rules in the generated agent.
- Error handling: catch specific expected failures (`asyncio.TimeoutError`, `TimeoutError`, `ValueError`, `RuntimeError`), set `status`/`error`; no bare `except`.
- Still obey repo rules: no hardcoded secrets/URLs, use `logger`, no `sys.path.insert`, type hints, imports at top, no emojis, resolve paths via `REPO_ROOT` when touching files.

## SDK Integration Pitfalls (learned from SeeAct-style integrations)
- Do not assume an SDK's documented provider support is sufficient for browseruse-bench needs.
  Verify whether it also supports:
  - custom `base_url`
  - non-default model IDs
  - OpenAI-compatible gateways
  - provider-specific API key env vars
- If the SDK reads credentials only from environment variables, isolate that env injection in a narrow setup/restore block.
  Restore the previous environment values in `finally` so one task run does not leak credentials into another.
- Prefer wrapping the SDK's inference engine/client instead of forking or patching the third-party package in-place.
  Keep browseruse-bench-specific provider normalization in our agent module.
- Some SDKs assume task-local artifact subdirectories already exist (`screenshots/`, `dom/`, traces, etc.).
  If the SDK writes files under the task workspace, proactively create the expected subdirectories after
  initialization and re-check them before repeated callbacks if the SDK is fragile.
- If you use LiteLLM (or a similar router), remember that it may infer the provider from the model name.
  Example: a model name like `gemini-2.5-pro` may be auto-routed to Vertex/Google even when the user explicitly
  configured an OpenAI-compatible gateway. In those cases, set the provider explicitly (for example
  `custom_llm_provider="openai"`) instead of relying on model-name inference.
- When wiring official Gemini/Vertex-style multimodal paths, audit runtime-only dependencies required by the
  router stack (for example `Pillow`, `vertexai`, Google SDK packages). Add them to the isolated requirements
  file when they are genuinely needed by the selected provider path.
- Conversely, do not install provider-specific dependencies just because the model name looks like that provider.
  If the request is intentionally routed through an OpenAI-compatible gateway, force the router down the
  OpenAI-compatible path instead of allowing it to import unrelated provider SDKs.
- Audit third-party SDK loggers for duplicate terminal output. Some SDKs attach their own `StreamHandler` and
  also propagate to the root logger, causing every line to appear twice. In our wrapper layer, disable
  propagation or remove duplicate console handlers while keeping file handlers when needed.
- After provider routing changes, run a real smoke task per provider path you claim to support
  (for example one OPENAI run, one OPENAI_COMPATIBLE run, one GEMINI run if direct Gemini is supported).
  Unit tests are necessary but not sufficient for SDK/router integrations.
- Add focused tests for:
  - runtime config resolution (`model_type`, `model_id`, api/base selection)
  - bootstrap model fallback for SDKs with model whitelists
  - OpenAI-compatible/custom gateway routing
  - explicit provider override when the model name would otherwise trigger router auto-detection
  - task-local artifact directory creation
  - missing screenshot/file fallback behavior
  - duplicate logger handler deduplication

## Adding a New Browser Backend (required checklist)
1) Add provider implementation:
   - `browseruse_bench/browsers/providers/<provider>.py`
   - implement `open(agent_name, agent_config)` and `close(session_context)`
2) Register backend id:
   - update `browseruse_bench/browsers/registry.py`
3) Dependency and runtime:
   - optional provider SDK imports must be `ImportError`-safe
   - fail only when selected backend is used
4) Tests:
   - add/extend `tests/browseruse_bench/test_browsers.py`
   - include open-success, missing-credential/dependency, cleanup-failure-tolerated
5) Docs/config updates:
   - update `docs/en/agents/*.mdx`, `docs/zh/agents/*.mdx`
   - update quickstart env docs if new env vars are required

## BaseAgent Template (replace <agent_name> and <agent_module>)
```python
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict

from browseruse_bench.agents.base import BaseAgent
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.schemas import AgentMetrics, AgentResult

logger = logging.getLogger(__name__)

@register_agent
class <AgentName>(BaseAgent):
    name = "<agent_name>"

    def run_task(
        self,
        task_info: Dict[str, Any],
        agent_config: Dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult:
        task_id     = task_info.get("task_id", "unknown")
        task_prompt = self.build_task_prompt(task_info)
        timeout     = self.get_timeout(agent_config)

        logger.info("Starting task %s", task_id)

        t0 = time.monotonic()
        # Implement logic using external SDK/docs.
        # Save artifacts under task_workspace.

        return AgentResult(
            task_id=task_id,
            timestamp=datetime.now(UTC),
            env_status="success",   # "success" | "failed"
            agent_done="done",      # "done" | "timeout" | "max_steps" | "error"
            agent_success=True,     # True/False only when agent_done=="done", else None
            answer="",
            metrics=AgentMetrics(end_to_end_ms=int((time.monotonic() - t0) * 1000), steps=0),
        )
```

## Choose Your Pattern

The `BaseAgent Template` above covers the sync/SDK case. Two more integration patterns are common; pick one before coding:

| Pattern | Base class | When to use | Example agents |
|---------|-----------|-------------|---------------|
| **SDK embedding (sync)** | `BaseAgent` | Agent is a Python SDK you call directly, synchronous API | — |
| **SDK embedding (async)** | `BaseAgent` + `_run_task_async` | Agent SDK is async (Playwright, httpx-based) | `browser_use.py`, `openai_cua.py`, `skyvern.py` |
| **CLI subprocess** | `CLIAgent` | Agent is an external executable (npm, binary) | `agent_tars.py`, `claude_code.py` |

Templates B and C below cover the non-sync variants.

## AgentResult Validation Rules

`AgentResult` uses `extra="forbid"` — unknown fields cause validation errors.

Constraints enforced by `model_validator`:
- `agent_success` MUST be `None` when `agent_done != "done"`
- `error` MUST be `None` when `env_status == "success"` AND `agent_done == "done"`
- `env_status`: `"success"` | `"failed"`
- `agent_done`: `"done"` | `"timeout"` | `"max_steps"` | `"error"`

Status-setting pattern (use in all templates):
```python
if error_msg and "Timeout" in error_msg:
    env_status, agent_done = "success", "timeout"
elif error_msg:
    env_status, agent_done = "failed", "error"
elif steps >= max_steps:
    env_status, agent_done = "success", "max_steps"
else:
    env_status, agent_done = "success", "done"

agent_success = None
if agent_done == "done":
    agent_success = bool(final_answer)
```

## Error Handling — SDK Exception Catch (critical)

Templates only catch generic exceptions (`RuntimeError`, `OSError`, `ValueError`, `TimeoutError`). You **MUST** also catch the agent SDK's base API exception class, or a 401 / rate-limit / bad-request will crash the task runner and skip `result.json`.

```python
import <sdk_module>   # e.g. import openai as openai_mod

try:
    # ... SDK calls ...
except <sdk_module>.APIError as exc:        # openai.APIError, stagehand.APIError, etc.
    error_msg = f"<SDK> API error: {exc}"
    logger.error("Task %s <SDK> API error: %s", task_id, exc)
except (RuntimeError, OSError, ValueError) as exc:
    error_msg = str(exc)
    logger.error("Task %s error: %s", task_id, exc)
```

Check the SDK's exception hierarchy (usually `<sdk>.APIError` or `<sdk>.Error` as base) and catch the broadest SDK-specific class.

## Template B — Async BaseAgent

For SDKs that are async (Playwright, httpx, modern browser-use SDKs). Wrap `asyncio.run()` in `run_task()`, put the real logic in `_run_task_async()`.

```python
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browseruse_bench.agents.base import BaseAgent
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import BrowserSessionContext, open_browser_session
from browseruse_bench.schemas import AgentMetrics, AgentResult, AgentUsage

logger = logging.getLogger(__name__)

@register_agent
class <AgentName>(BaseAgent):
    name = "<agent-name>"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult:
        timeout = self.get_timeout(agent_config)
        browser_id = agent_config.get("browser_id", "Chrome-Local")

        with open_browser_session(
            browser_id=browser_id,
            agent_name=self.name,
            agent_config=agent_config,
        ) as session_context:
            return asyncio.run(
                self._run_task_async(
                    task_info, agent_config, task_workspace,
                    timeout, session_context,
                )
            )

    async def _run_task_async(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
        timeout: int,
        session_context: BrowserSessionContext,
    ) -> AgentResult:
        # import <sdk> here (lazy import)

        task_id = task_info["task_id"]
        task_prompt = self.build_task_prompt(task_info)
        model_id = self.get_model_id(agent_config) or ""
        max_steps = self.get_max_steps(agent_config)

        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)

        t0 = time.monotonic()
        error_msg: str | None = None
        final_answer = ""
        steps = 0
        total_input_tokens = 0
        total_output_tokens = 0

        try:
            # Async SDK calls here.
            # Branch on session_context.transport:
            #   "cdp"   -> connect via session_context.cdp_url
            #   "local" -> launch local browser
            pass
        except (asyncio.TimeoutError, TimeoutError):
            error_msg = f"Timeout after {timeout} seconds"
            logger.error("Task %s timed out after %d seconds", task_id, timeout)
        # except <sdk>.APIError as exc:         # <-- ADD SDK EXCEPTION (see Error Handling)
        #     error_msg = f"SDK error: {exc}"
        #     logger.error("Task %s SDK error: %s", task_id, exc)
        except (RuntimeError, OSError, ValueError) as exc:
            error_msg = str(exc)
            logger.error("Task %s error: %s", task_id, exc)

        end_to_end_ms = int((time.monotonic() - t0) * 1000)

        if error_msg and "Timeout" in error_msg:
            env_status, agent_done = "success", "timeout"
        elif error_msg:
            env_status, agent_done = "failed", "error"
        elif steps >= max_steps:
            env_status, agent_done = "success", "max_steps"
        else:
            env_status, agent_done = "success", "done"

        agent_success = None
        if agent_done == "done":
            agent_success = bool(final_answer)

        usage_obj = None
        if total_input_tokens + total_output_tokens > 0:
            usage_obj = AgentUsage(
                total_prompt_tokens=total_input_tokens,
                total_completion_tokens=total_output_tokens,
            )

        return AgentResult(
            task_id=task_id,
            task=task_prompt,
            timestamp=datetime.now(UTC),
            env_status=env_status,   # type: ignore[arg-type]
            agent_done=agent_done,   # type: ignore[arg-type]
            agent_success=agent_success,
            answer=final_answer if not error_msg else f"[Task Failed: {error_msg}]",
            model_id=model_id,
            browser_id=session_context.backend_id,
            metrics=AgentMetrics(
                end_to_end_ms=end_to_end_ms,
                steps=steps,
                usage=usage_obj,
            ),
            error=error_msg if env_status == "failed" else None,
        )
```

## Template C — CLIAgent Subprocess

For agents that ship as an external executable (npm package, binary). Inherit `CLIAgent` instead of `BaseAgent` to get `_run_subprocess` and `_map_exit_status` helpers.

```python
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browseruse_bench.agents.cli_agent import CLIAgent
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import open_browser_session
from browseruse_bench.schemas import AgentMetrics, AgentResult

logger = logging.getLogger(__name__)

@register_agent
class <AgentName>(CLIAgent):
    name = "<agent-name>"

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult:
        task_id = task_info["task_id"]
        task_prompt = self.build_task_prompt(task_info)
        timeout = self.get_timeout(agent_config)
        model_id = self.get_model_id(agent_config) or ""
        browser_id = agent_config.get("browser_id", "Chrome-Local")

        cmd = ["<executable>", "<subcommand>"]

        with open_browser_session(
            browser_id=browser_id,
            agent_name=self.name,
            agent_config=agent_config,
        ) as session_context:
            cmd_args: list[str] = []
            # Build CLI flags from agent_config ...
            # If session_context.transport == "cdp":
            #     cmd_args.extend(["--cdp-url", session_context.cdp_url])

            cmd_args.extend(["--input", task_prompt])
            full_cmd = cmd + cmd_args

            try:
                returncode, stdout_lines, execution_error = self._run_subprocess(
                    full_cmd,
                    timeout=timeout,
                    task_workspace=task_workspace,
                )
            except FileNotFoundError:
                return AgentResult(
                    task_id=task_id,
                    timestamp=datetime.now(UTC),
                    env_status="failed",   # type: ignore[arg-type]
                    agent_done="error",    # type: ignore[arg-type]
                    error=f"Executable '{cmd[0]}' not found.",
                    metrics=AgentMetrics(end_to_end_ms=0, steps=0),
                )

            env_status, agent_done = self._map_exit_status(returncode, execution_error)

            # Parse agent output (stdout, result files, etc.)
            final_answer = ""
            steps = 0
            # ... parse stdout_lines or task_workspace files ...

            agent_success = None
            if agent_done == "done":
                agent_success = bool(final_answer)

            return AgentResult(
                task_id=task_id,
                task=task_prompt,
                timestamp=datetime.now(UTC),
                env_status=env_status,   # type: ignore[arg-type]
                agent_done=agent_done,   # type: ignore[arg-type]
                agent_success=agent_success,
                answer=final_answer if not execution_error else f"[Task Failed: {execution_error}]",
                model_id=model_id,
                browser_id=session_context.backend_id,
                metrics=AgentMetrics(end_to_end_ms=0, steps=steps),
                error=execution_error if env_status == "failed" else None,
            )
```

`CLIAgent` provides:
- `_run_subprocess(cmd, timeout, task_workspace, ...)` → `(returncode, stdout_lines, execution_error)`; drains stdout/stderr to files, handles timeout (SIGTERM → SIGKILL), raises `FileNotFoundError` if executable not found
- `_map_exit_status(returncode, execution_error)` → `(env_status, agent_done)`

## Constraints
- No print(); use logger
- No bare except or except Exception; catch specific exceptions and log ERROR
- No hardcoded timeout, URL, API key, or model values
