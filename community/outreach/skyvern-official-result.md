# Skyvern Official Result Request

Target project: `Skyvern`

Primary ask: invite maintainers or power users to submit a reviewed LexBench-Browser result for
the `skyvern` adapter.

## Suggested Message

```text
Hi Skyvern maintainers,

We support running Skyvern in LexBench-Browser and would like to invite an official result
submission from your team or community.

browseruse-agent-bench is a reproducible browser-agent benchmark framework. LexBench-Browser is
its default public dataset, with 208 no-login tasks across 107 websites. The framework records
success rate, task-level artifacts, steps, latency, token/cost metrics, model settings, and
browser backend.

For Skyvern, the most useful official result would include:
- Skyvern version or commit
- model provider and model ID
- browser backend and launch mode
- proxy or region notes if used
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

Would you be interested in submitting or reviewing an official Skyvern result?
```

## Local Prep

- Verify current Skyvern config requirements before outreach.
- Include proxy guidance if suggesting local Chrome from mainland China.
- Ask whether maintainers prefer local launch or a cloud browser session.
- Record any Skyvern-specific retry behavior in the result metadata.
