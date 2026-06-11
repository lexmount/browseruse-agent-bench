# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Team-wide agent rules

These are the single source of truth for coding conventions, layer boundaries, import/runtime rules, error handling, and testing in this repo. They apply to every coding agent. Read them first.

@AGENTS.md
@docs_4_codeagent/coding-style.md
@docs_4_codeagent/architecture-boundaries.md
@docs_4_codeagent/imports-runtime-config.md
@docs_4_codeagent/error-handling-testing.md

The sections below are bootstrap context specific to Claude Code sessions — architecture orientation and command cheatsheet. They do not override the imports above.

## Commands

- Run a benchmark: `uv run scripts/run.py --agent <agent> --data <bench> --split <split> --mode first_n --count <n>` (equivalent: `bubench run ...`).
- Evaluate results: `uv run scripts/eval.py --agent <agent> --data <bench> --split <split> --model-id <id>` (equivalent: `bubench eval ...`).
- Submit a LexBench job (not local run): `bubench submit ...`.
- Leaderboard / viz: `bubench leaderboard`, `bubench server`, `bubench viz --watch`.
- All tests: `uv run pytest tests/`.
- Single test: `uv run pytest tests/browseruse_bench/test_task.py -v` or `... -k <pattern>`.
- Integration tests (marked `integration`, require external tools / API keys) live under `tests/integration/`.

## Install gotcha

Agent SDKs ship as **mutually exclusive extras** (declared in `pyproject.toml` under `[tool.uv] conflicts`): `browser-use`, `skyvern`, `openai-cua` pin incompatible Playwright versions. Install each into a separate venv when running multiple agents.

- Dev: `uv sync --extra dev`
- Agent: `uv sync --extra browser-use` (or `skyvern` / `openai-cua`)

## High-level architecture

The repo orchestrates browser agents (`browser-use`, `skyvern`, `agent-tars`, `claude-code`, `codex`, `openai-cua`, `deepbrowse`) against benchmarks (`LexBench-Browser`, `Online-Mind2Web`, `BrowseComp`) on browser backends (`Chrome-Local`, `lexmount`, `browser-use-cloud`, `agentbay`, `cdp`).

- **CLI** ([browseruse_bench/cli/](browseruse_bench/cli/)) — argparse subcommands (`run`, `eval`, `submit`, `leaderboard`, `server`, `service`, `skills`, `viz`, `login`). [__init__.py](browseruse_bench/cli/__init__.py) preloads `.env` from `REPO_ROOT` and reads `config.yaml`. The top-level `scripts/*.py` files are thin shims — edit the CLI module, not the script.
- **Agents** ([browseruse_bench/agents/](browseruse_bench/agents/)) — [base.py](browseruse_bench/agents/base.py) (`BaseAgent` ABC), one file per agent, plus [registry.py](browseruse_bench/agents/registry.py). Agents self-register via `@register_agent` on a unique `name`. Do not import agent SDKs at registry level.
- **Runner** ([browseruse_bench/runner/agent_runner.py](browseruse_bench/runner/agent_runner.py)) — subprocess entry point invoked as `uv run --extra <agent> browseruse_bench/runner/agent_runner.py ...`. This is how per-agent venv isolation is enforced at runtime.
- **Browsers** ([browseruse_bench/browsers/](browseruse_bench/browsers/)) — backend abstraction parallel to agents. [registry.py](browseruse_bench/browsers/registry.py) uses **function-local imports in factories** to lazy-load providers — intentional, and the one place the "no imports inside functions" rule is relaxed (see [imports-runtime-config.md](docs_4_codeagent/imports-runtime-config.md)).
- **Eval** ([browseruse_bench/eval/](browseruse_bench/eval/)) — `BaseEvaluator` ABC + per-benchmark subpackages (`online_mind2web/`, `browse_comp/`, `lexbench_browser/`). [registry.py](browseruse_bench/eval/registry.py) lazy-binds factories like `browsers/registry.py`. Datasets live under [browseruse_bench/data/](browseruse_bench/data/) (one directory per benchmark, with `data_info.json` + tasks files). Controlled via `--data-source {local|huggingface}` and `HF_ENDPOINT`.
- **Visualization & Leaderboard** ([browseruse_bench/visualization/](browseruse_bench/visualization/), [browseruse_bench/leaderboard/](browseruse_bench/leaderboard/)) — static HTML generators plus a FastAPI server.

Experiment outputs land in `experiments/{benchmark}/{split}/{agent}/{model_id}/{timestamp}/`. Each run directory is self-contained (`run.log`, `runtime.log`, `task_brief.log`, `run_manifest.json`). Script-level logs go to `output/logs/{run,eval,leaderboard}/`.

## Configuration

- `.env` holds **only** secrets (`OPENAI_API_KEY`, `LEXMOUNT_API_KEY`, etc.) and is preloaded from `REPO_ROOT`.
- `config.yaml` (copied from `config.example.yaml`, git-ignored) holds everything else: agents, models (with `$ENV_VAR` expansion), browser selection, defaults, eval model. Each agent section uses `active_model` to pick which `models.<name>` entry is in force.
- Use `from browseruse_bench.utils import REPO_ROOT`; do not compute paths via `Path(__file__).parents[N]` or mutate `sys.path`.
