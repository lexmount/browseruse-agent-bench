# Agent-TARS Official Result Request

Target project: `Agent-TARS`

Primary ask: invite maintainers or power users to submit a reviewed LexBench-Browser result for
the `agent-tars` adapter.

## Suggested Message

```text
Hi Agent-TARS maintainers,

We support running Agent-TARS in LexBench-Browser and would like to invite an official result
submission from your team or community.

browseruse-agent-bench is a reproducible browser-agent benchmark framework. LexBench-Browser is
its default public dataset, with 208 no-login tasks across 107 websites. It is intended for
engineering comparison: run your agent, submit artifacts, compare browser backends where
supported, and inspect task-level failures.

For Agent-TARS, the most useful official result would include:
- Agent-TARS version or commit
- CLI/runtime configuration
- model provider and model ID
- browser backend or browser launch constraints
- benchmark split and version
- run command and evaluation command
- redacted config
- task-level artifacts
- known skips, retries, provider incidents, or browser constraints

Closed-source model/provider settings are acceptable if reproducibility limits are disclosed.
Maintainers review and rerun results before they become official leaderboard entries.

Repo: https://github.com/lexmount/browseruse-agent-bench
Protocol: https://github.com/lexmount/browseruse-agent-bench/blob/main/EVALUATION_PROTOCOL.md
Submission docs: https://github.com/lexmount/browseruse-agent-bench/tree/main/community/results

Would you be interested in submitting or reviewing an official Agent-TARS result?
```

## Local Prep

- Confirm the current CLI invocation documented by Agent-TARS.
- Be explicit if a browser backend cannot be swapped because the CLI owns launch behavior.
- Ask maintainers to include environment and runtime notes in the submission.
- Record any unsupported browser-backend comparisons as limitations, not failures.
