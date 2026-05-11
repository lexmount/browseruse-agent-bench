# Public Leaderboard Data

The public leaderboard starts as a static, reviewable data file. Submissions arrive through
GitHub PRs, are validated by metadata checks, then become official only after maintainer review
and rerun.

## Files

- `accepted-results.schema.json` documents the accepted result fields.
- `accepted-results.json` stores accepted public leaderboard entries.

The existing leaderboard server can later read this data as a read-only source. Uploads should
continue through GitHub PRs until submission volume justifies a dedicated review UI.

## Acceptance Flow

1. Contributor submits `community/results/**/submission.json`.
2. `scripts/validate_result_submissions.py` validates required metadata.
3. Maintainers review artifacts and rerun the result.
4. Maintainers add or update an entry in `accepted-results.json`.
5. Static pages or the read-only server display accepted entries.

