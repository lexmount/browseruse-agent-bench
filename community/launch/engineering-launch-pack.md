# Engineering Launch Pack

Positioning: run your agent, submit reproducible results, compare browser backends.

This launch pack is intentionally practical. The goal is to make browser-agent builders think:

> I can run this benchmark today, compare my agent against known baselines, and submit a result
> that maintainers can review and rerun.

## One-Liner

LexBench-Browser is a reproducible browser-agent benchmark: run your agent on 210 real-web tasks
across 107 websites, compare local/cloud browser backends, and submit reviewed leaderboard results.

## Short Pitch

LexBench-Browser is built for browser-agent developers who need more than a demo task. It provides
210 public tasks across 107 real websites, supports multiple agents and browser backends, records
cost/latency/token metrics, and defines a result submission workflow with maintainer review and
rerun.

Use it to:

- run your own browser agent
- compare local Chrome, Lexmount cloud browser, AgentBay, and other backends
- inspect trajectories and failure cases
- submit reproducible leaderboard results
- add new agent adapters or benchmark tasks

## Primary Links

- GitHub: https://github.com/lexmount/browseruse-agent-bench
- Landing page: https://lexmount.github.io/browseruse-agent-bench/
- Docs: https://docs.bubench.lexmount.io/
- Result submission policy: `docs/en/leaderboard/result-submissions.mdx`
- Evaluation protocol: `EVALUATION_PROTOCOL.md`
- Governance: `GOVERNANCE.md`

## Primary CTA

```bash
git clone https://github.com/lexmount/browseruse-agent-bench.git
cd browseruse-agent-bench
uv sync --extra browser-use
bubench run --agent browser-use --data LexBench-Browser --mode first_n --count 3
```

## Hacker News

Title:

```text
Show HN: LexBench-Browser, a real-world benchmark for browser agents
```

Post:

```text
We built LexBench-Browser as a reproducible benchmark for browser-agent builders.

It includes 210 public tasks across 107 real websites, supports multiple agents and browser
backends, and records success rate, steps, latency, token usage, cost, and trajectories.

The practical goal is simple:
- run your own browser agent
- compare browser backends such as local Chrome and cloud browsers
- submit a result that maintainers can review and rerun
- add an agent adapter or benchmark task through PR

The repo includes result submission metadata, governance, and an evaluation protocol so that
leaderboard entries are not just screenshots or self-reported numbers.

GitHub: https://github.com/lexmount/browseruse-agent-bench
```

## Reddit

Suggested subreddits:

- r/LocalLLaMA
- r/MachineLearning
- r/LLMDevs

Post:

```text
We open-sourced LexBench-Browser, a benchmark for browser agents that focuses on reproducible
engineering runs rather than one-off demos.

What it gives you:
- 210 public tasks across 107 real websites
- multi-agent runner: browser-use, Skyvern, Agent-TARS, etc.
- browser backend comparison: local Chrome, Lexmount cloud browser, AgentBay, CDP
- cost / latency / token metrics
- trajectory inspection
- result submission policy with maintainer review and rerun

The intended workflow is:
1. Run your agent on LexBench-Browser.
2. Compare model + browser backend settings.
3. Submit a reproducible result with metadata and artifacts.
4. Add an adapter if your agent is not supported yet.

GitHub: https://github.com/lexmount/browseruse-agent-bench

Would love feedback from people building browser agents: what result metadata would you require
before trusting a public leaderboard?
```

## X / Twitter

Single post:

```text
We open-sourced LexBench-Browser for browser-agent builders.

210 tasks across 107 real websites.
Run your agent, compare browser backends, inspect trajectories, and submit reproducible results.

GitHub: https://github.com/lexmount/browseruse-agent-bench
```

Thread:

```text
1/ Browser-agent evals need to be runnable, comparable, and reviewable.

LexBench-Browser is our attempt at that: 210 public tasks across 107 real websites, with multi-agent and multi-browser support.

2/ The workflow:

- run your agent
- choose model + browser backend
- evaluate with a declared judge strategy
- inspect trajectories and cost/latency/token metrics
- submit a result maintainers can review and rerun

3/ Supported paths include browser-use, Skyvern, Agent-TARS, local Chrome, cloud browsers, and CDP-style backends.

New agents can be added through a BaseAgent adapter.

4/ We also added governance, evaluation protocol, result submission metadata, and issue templates so leaderboard entries are not just self-reported screenshots.

5/ Try it:

git clone https://github.com/lexmount/browseruse-agent-bench.git
cd browseruse-agent-bench
uv sync --extra browser-use
bubench run --agent browser-use --data LexBench-Browser --mode first_n --count 3
```

## LinkedIn

```text
We open-sourced LexBench-Browser, a reproducible benchmark platform for browser-agent engineering.

The project is designed around a practical workflow:

1. Run your browser agent on real-web tasks.
2. Compare model and browser backend choices.
3. Inspect trajectories, latency, cost, token usage, and failure cases.
4. Submit leaderboard results with metadata and artifacts that maintainers can review and rerun.

Current snapshot:
- 210 public tasks
- 107 real websites
- multiple agent integrations
- local and cloud browser backends
- result submission and evaluation protocol docs

The goal is to make browser-agent results easier to reproduce, compare, and improve through PRs.

GitHub: https://github.com/lexmount/browseruse-agent-bench
```

## Discord / Slack Communities

```text
For anyone building browser agents: we open-sourced LexBench-Browser.

It is meant to be a practical benchmark workflow:
- run your agent
- compare browser backends
- inspect trajectories and cost/latency/token metrics
- submit reproducible results for review and rerun

Repo: https://github.com/lexmount/browseruse-agent-bench

We are especially looking for:
- new agent adapters
- official results from agent projects
- benchmark task proposals
- feedback on result metadata / rerun policy
```

## Upstream Agent Outreach

Use this when contacting maintainers of browser-use, Skyvern, Agent-TARS, or related projects.

```text
Hi! We added support for running <PROJECT> in LexBench-Browser and would like to invite an
official result submission from maintainers or power users.

LexBench-Browser is a reproducible browser-agent benchmark with 210 public tasks across 107
real websites. It records success rate, steps, latency, token/cost metrics, and task-level
artifacts.

The submission flow is:
1. Run <PROJECT> on LexBench-Browser.
2. Share redacted config + evaluation output + task-level artifacts.
3. Maintainers review and rerun the result.
4. Accepted results are listed as official leaderboard entries with credit.

Repo: https://github.com/lexmount/browseruse-agent-bench
Result policy: https://github.com/lexmount/browseruse-agent-bench/blob/main/EVALUATION_PROTOCOL.md

Would you be interested in submitting or reviewing an official <PROJECT> result?
```

## Messaging Rules

- Lead with what builders can run, compare, or submit.
- Avoid "we open-sourced a repo" as the headline.
- Avoid claiming state-of-the-art without a stable public leaderboard.
- Always mention reproducibility, artifacts, and maintainer rerun.
- Invite adapter PRs and result submissions as the main contribution path.

