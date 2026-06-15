# browser-use Official Result Request

Target project: `browser-use`

Primary ask: invite maintainers or power users to submit a reviewed LexBench-Browser result for
the `browser-use` adapter.

## Suggested Message

```text
Hi browser-use maintainers,

We support running browser-use in LexBench-Browser and would like to invite an official result
submission from your team or community.

browseruse-agent-bench is a reproducible browser-agent benchmark framework. LexBench-Browser is
its default public dataset, with 208 no-login tasks across 107 websites. The workflow is
engineering-oriented: run your agent, compare browser backends, inspect trajectories, and submit
reviewed results with enough metadata for rerun.

For browser-use, the most useful official result would include:
- browser-use version or commit
- model provider and model ID
- browser backend: local Chrome, Lexmount, AgentBay, CDP, or another backend
- benchmark split and version
- run command and evaluation command
- redacted config
- task-level artifacts
- success rate, steps, latency, token usage, and cost where available
- known skips, retries, provider incidents, or browser constraints

Closed-source model/provider settings are acceptable if reproducibility limits are disclosed.
Maintainers review and rerun results before they become official leaderboard entries.

Repo: https://github.com/lexmount/browseruse-agent-bench
Protocol: https://github.com/lexmount/browseruse-agent-bench/blob/main/EVALUATION_PROTOCOL.md
Submission docs: https://github.com/lexmount/browseruse-agent-bench/tree/main/community/results

Would you be interested in submitting or reviewing an official browser-use result?
```

## Local Prep

- Verify the quickstart still runs with `uv sync --extra browser-use`.
- Prefer a small sample first: `uv run bubench run --agent browser-use --data LexBench-Browser --mode first_n --count 3`.
- Ask whether maintainers want to compare local Chrome and one cloud backend.
- If the project has a preferred model/provider config, record it in the submission metadata.
