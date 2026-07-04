# browseruse_bench Agent Instructions

Python benchmark framework for browser agents and provider integrations.

## Essentials

- Python version: `>=3.11` (use `uv`, not system Python).
- Install dev tools: `uv sync --extra dev`.
- Run tests: `uv run pytest tests/`.
- Run scripts: `uv run python ...`.
- Make the smallest correct change with clear control flow.
- Keep optional dependency isolation in registry/router lazy-load factories.
- Keep provider modules direct and fail-fast when selected.
- Use `logger` for logs; do not use `print()`.
- Catch specific exceptions only; do not use bare `except:` or `except Exception:`.
- Before committing agent-touching changes, run a real `bubench run --agent <agent> --data LexBench-Browser --mode single` (not `--dry-run`, not pytest-only). See [Smoke Testing Before Commit](docs_4_codeagent/error-handling-testing.md#smoke-testing-before-commit).
- Before opening or updating a PR, complete the [Mandatory Self-Review](CONTRIBUTING.md#mandatory-self-review-before-every-pr) and tick the self-review checklist in the PR template. This applies to humans and coding agents alike.

## Detailed Instructions

- [Coding Style and Control Flow](docs_4_codeagent/coding-style.md)
- [Architecture and Boundaries](docs_4_codeagent/architecture-boundaries.md)
- [Imports, Runtime, and Configuration](docs_4_codeagent/imports-runtime-config.md)
- [Error Handling and Testing](docs_4_codeagent/error-handling-testing.md)
- [PR Auto-Fix Loop (opt-in)](docs_4_codeagent/pr-auto-fix-loop.md) — watch your PR for new review threads, auto-fix, push. Opt-in per-PR, not a default behavior.
