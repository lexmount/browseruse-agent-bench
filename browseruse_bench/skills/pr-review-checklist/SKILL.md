---
name: pr-review-checklist
description: Generate and maintain PR review checklists from GitHub review threads for the current branch PR or a specified PR. Use when you need reviewer comments with exact code locations, nearby code snippets, and raw thread chat logs, and when you want continuously updated .pr/<pr_number>/review_checklist.md and .pr/<pr_number>/unresolved_checklist.md.
---

# PR Review Checklist

Generate PR review checklists into `.pr/<pr_number>/` and keep unresolved items current across reruns.

## Files

- Script: `scripts/generate_pr_review_checklists.py`
- Output:
  - `.pr/<pr_number>/review_checklist.md`
  - `.pr/<pr_number>/unresolved_checklist.md`

## Prerequisites

1. Ensure `gh` is installed and authenticated.
2. Run commands from this skill directory (the directory containing this `SKILL.md`).
3. Set `PYTHONPATH` to repo root.
4. If not authenticated, run `gh auth login` first.

## Commands

Current branch PR (auto-detect PR number):

```bash
export PYTHONPATH=/path/to/browseruse_bench
python3 scripts/generate_pr_review_checklists.py
```

Specified PR:

```bash
export PYTHONPATH=/path/to/browseruse_bench
python3 scripts/generate_pr_review_checklists.py --pr-number 122 --repo lexmount/browseruse-agent-bench
```

## Behavior

1. Resolve target PR:
   - Use `--pr-number` when provided.
   - Otherwise use current branch PR.
2. Pull review threads and comments from GitHub.
3. Render both Markdown files with:
   - File + line location
   - PR diff hunk snippet
   - Current workspace code context near the commented line
   - Raw thread chat log (full comment bodies + reply relation)
4. Rebuild unresolved checklist on every run.
5. Preserve manual checkbox states in `unresolved_checklist.md` for thread IDs that are still unresolved.
6. After each successful refresh, explicitly ask the user:
   - `Do you want me to generate/update .pr/<pr_number>/unresolved_solutions.md now?`
   - If yes, run `pr-unresolved-solutions` workflow immediately.

## Notes

- Thread IDs are stable (`TH-<root_comment_id>`), suitable for tracking work over time.
- For outdated threads, the current code context can differ from PR-time code; rely on diff hunk + thread link together.
