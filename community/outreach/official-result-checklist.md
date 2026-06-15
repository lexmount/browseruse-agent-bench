# Official Result Outreach Checklist

Use this checklist when asking browser-agent projects to submit official LexBench-Browser results.

## Before Outreach

- Confirm the project has an existing adapter or a clear integration path.
- Run a small smoke test if credentials and dependencies are available.
- Identify the maintainers or release channel most likely to review benchmark requests.
- Link to the exact result submission policy and evaluation protocol.
- Be clear that closed-source agents are allowed when reproducibility limits are disclosed.
- Ask for metadata and artifacts, not just a score.

## Information to Request

- agent name, version, and source or reproducibility status
- model provider and model ID
- browser backend
- benchmark split and version
- run command
- evaluation command
- redacted config
- run timestamp
- success rate, average steps, latency, token usage, and cost where available
- task-level artifacts
- known skips, retries, provider incidents, or browser constraints

## Maintainer Review

- Validate the submitted metadata with `uv run python scripts/validate_result_submissions.py`.
- Check that every task in the selected split is accounted for.
- Confirm secrets are redacted.
- Decide whether to do a full or sampled rerun.
- Record reviewer, rerun status, and any caveats in the accepted result metadata.
- Credit the contributor or upstream project in `CONTRIBUTORS.md` when appropriate.

## Outreach Template

```text
Hi <PROJECT> maintainers,

We support running <PROJECT> in browseruse-agent-bench on the LexBench-Browser dataset and would
like to invite an official result submission from your team or community.

browseruse-agent-bench is a reproducible browser-agent benchmark framework. LexBench-Browser is
its default public dataset, with 208 no-login tasks across 107 websites. The framework records
success rate, steps, latency, token/cost metrics, browser backend, and task-level artifacts.

Official results can be open-source or closed-source, but they need enough metadata for maintainer
review and rerun:
- agent version
- model provider and model ID
- browser backend
- run and evaluation commands
- redacted config
- task-level artifacts
- known skips/retries/provider incidents

Repo: https://github.com/lexmount/browseruse-agent-bench
Protocol: https://github.com/lexmount/browseruse-agent-bench/blob/main/EVALUATION_PROTOCOL.md
Submission docs: https://github.com/lexmount/browseruse-agent-bench/tree/main/community/results

Would you be interested in submitting or reviewing an official <PROJECT> result?
```
