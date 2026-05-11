# Result Submissions

This directory documents the minimum shape for community leaderboard result submissions.

Official leaderboard entries require maintainer review and maintainer rerun. Closed-source
agents may be submitted when the metadata clearly states the reproducibility limitations.

## Required Metadata

Each result submission should include a metadata file with:

- benchmark, split, and benchmark version
- agent name and version
- whether the agent is open-source or closed-source
- model provider and model ID
- browser backend
- judge model and strategy
- run command and eval command
- run timestamp
- success rate
- average steps
- average end-to-end latency
- artifact location
- known skips, retries, provider incidents, or browser constraints

## Recommended Layout

```text
community/results/
`-- <benchmark>/
    `-- <split>/
        `-- <agent>/
            `-- <model-id>/
                `-- <timestamp>/
                    |-- submission.json
                    `-- README.md
```

Large run artifacts should be linked from the submission metadata instead of committed to git.
Do not commit secrets, cookies, account credentials, or provider logs containing API keys.

## Review Policy

Maintainers review metadata first, then request or run reproduction artifacts. A result is not
official until a maintainer marks it accepted or merges the corresponding leaderboard update.

## Validation

Run:

```bash
uv run python scripts/validate_result_submissions.py
```

The validator checks required metadata, task accounting, basic metric ranges, and obvious
secret-like values.
