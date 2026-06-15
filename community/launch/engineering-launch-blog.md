# Launch Blog Draft: browseruse-agent-bench for Browser-Agent Builders

Working title:

```text
browseruse-agent-bench: Run Your Agent, Submit Results, Compare Browser Backends
```

## Summary

Browser agents are getting good enough that demos are no longer the hard part. The harder
engineering question is whether an agent can complete real web workflows across sites, languages,
popups, dynamic content, flaky pages, and different browser environments.

browseruse-agent-bench is our attempt to make that question runnable. It is a multi-agent browser
evaluation framework with local and cloud browser backend support, trajectory artifacts, cost and
latency accounting, and a reviewable result submission workflow. Its default public dataset,
LexBench-Browser, contains 208 no-login tasks across 107 websites.

## Why We Built It

Most browser-agent evaluation breaks down in three places:

1. The task is too demo-like, so it misses practical failure modes.
2. The browser environment is underspecified, so a result is hard to reproduce.
3. The leaderboard is just a number, without artifacts or rerun policy.

browseruse-agent-bench treats evaluation as an engineering workflow instead of a static score. You
can run an agent on LexBench-Browser, inspect task-level outputs, compare browser backends, and
submit enough metadata for maintainers to review and rerun the result.

## What Is in v1.0

The LexBench-Browser v1.0 public dataset snapshot includes:

- 208 tasks
- 107 distinct websites
- 135 Chinese-language tasks and 73 English-language tasks
- 90 global split tasks and 118 Lexmount-region split tasks
- no-login tasks only
- task-level reference steps, key points, common mistakes, scoring items, and robustness tags

The tasks cover commerce, media and entertainment, education and research, finance, gaming,
local lifestyle, productivity tools, safety, and social/community workflows.

## What the Benchmark Tries to Expose

The dataset labels practical web-agent stressors:

- popup interference: login popups, cookie consent, ad overlays
- sequence complexity: long sequences, deep navigation, multi-site workflows
- content dynamics: real-time data, lazy loading, iframes
- anti-crawl behavior: captcha, anti-bot, rate limiting
- localization: Chinese rendering and cross-language workflows
- complex interaction: filtering, sorting, and data extraction

These labels are not just metadata. They help contributors explain why a task belongs in the
dataset and help agent builders understand failure clusters.

## The Workflow

Run a small sample:

```bash
git clone https://github.com/lexmount/browseruse-agent-bench.git
cd browseruse-agent-bench
uv sync --extra browser-use
uv run bubench run --agent browser-use --data LexBench-Browser --mode first_n --count 3
```

Then scale up:

1. Pick an agent adapter.
2. Pick a browser backend: local Chrome, Lexmount, AgentBay, CDP, or another provider.
3. Run the LexBench-Browser dataset.
4. Evaluate with the declared judge model and strategy.
5. Inspect trajectories, failures, cost, latency, and token usage.
6. Submit result metadata and artifacts through a GitHub PR.

## Why Browser Backends Matter

A browser agent is not only the model and prompt. The browser backend affects page reachability,
session persistence, login state, latency, stability, and provider-specific constraints.

browseruse-agent-bench keeps browser selection explicit through `browser_id`. That makes
comparisons more honest:

- local Chrome for development and low-volume runs
- cloud browser sessions for isolated and repeatable runs
- CDP-compatible backends for provider integrations
- login contexts where supported by the backend

If a result uses a different browser backend, the leaderboard metadata should say so.

## Result Submissions Are Reviewable by Design

Official leaderboard entries require:

- benchmark name, split, and version
- agent name, version, and source or reproducibility status
- model provider and model ID
- browser backend
- run and evaluation commands
- redacted config
- run timestamp
- success rate, steps, latency, token usage, and cost where available
- task-level artifacts
- known skips, retries, provider incidents, or browser constraints

Closed-source agents can submit results, but the reproducibility limits must be disclosed.
Maintainers review and rerun results before listing them as official.

## How to Contribute

Useful contributions include:

- adding an agent adapter
- adding a browser backend
- submitting official results
- proposing dataset tasks with evaluation criteria
- improving docs and examples
- sharing failure analyses from real runs

Start with:

- GitHub: https://github.com/lexmount/browseruse-agent-bench
- Dataset: https://huggingface.co/datasets/Lexmount/LexBench-Browser
- Docs: https://docs.bubench.lexmount.io/
- Result policy: `EVALUATION_PROTOCOL.md`
- Contribution guide: `CONTRIBUTING.md`

## Closing

The goal is not to declare a final winner. The goal is to make browser-agent evaluation easier to
run, inspect, compare, and improve in public.

If you build browser agents, try browseruse-agent-bench with the LexBench-Browser dataset, compare
browser backends, and submit a reproducible result.
