# Failure Taxonomy Fusion and Standalone Attribution Pass

Date: 2026-07-03
Status: approved

## Goal

Reimplement the core ideas of PR #68 (reverted by PR #72) at the right layer:
one unified failure taxonomy owned by `browseruse_bench/eval/failure.py`, and a
standalone attribution pass that labels failed tasks in an existing eval
results file. No parallel classification pipelines, no scripts/-layer forks.

## Unified taxonomy (canonical)

```
M1 Task Reasoning    M1.1 Requirement Following | M1.2 Target Selection | M1.3 Evidence Grounding
M2 Action Execution  M2.1 UI Misoperation | M2.2 Infinite Loop / long-horizon breakdown
                     M2.3 Format/Protocol Breakdown | M2.4 Model Service Error (new; ex-A3)
M3 Web Constraints   M3.1 Bot Defense | M3.2 Access Barrier (login/paywall/regional)
                     M3.3 Site Limitation (down/404/content absent)
OTHER (requires other_phrase) | U (attribution-pipeline failure; code-assigned only,
                                  never selectable by the judge)
```

- Multi-label with a primary code. Judge output schema:
  `{"reasoning": str, "codes": [code...], "primary_code": code, "other_phrase": str|null}`,
  enforced via JSON schema response_format with regex fallback for truncated output.
- `failure_category` on each eval record is set to `primary_code` so existing
  consumers (`utils/stats.calculate_failure_category_stats`, leaderboard) keep
  working unchanged (they treat categories as opaque strings).
- Full result stored in `evaluation_details.failure_classification`:
  `{category (=primary), codes, reasoning, other_phrase, legacy_category, raw_response}`.
- Deterministic legacy mapping M -> old codes for continuity:
  M1.* -> A1, M2.1 -> A2, M2.2 -> A4, M2.3 -> A2, M2.4 -> A3,
  M3.1 -> B1, M3.2 -> B2, M3.3 -> C2, OTHER -> OTHER, U -> U.
  Historical A-labeled data is not migrated in place; it is re-labeled by
  running the new pass with `--force` (cheap).
- System prompt: adapted from PR #68's `failure_taxonomy_system.txt`
  (kept as a module constant in `failure.py`, consistent with the current
  pattern): add M2.4, move "model service no-response" out of M2.3 into M2.4,
  keep the multi-label rules and OTHER discipline.
- Input construction reuses the fixed extraction: task description, last 10
  actions, agent answer + `[Agent runtime error]`, evaluator response
  (`grader_response` -> `response` fallback), last 3 screenshots, with
  `result.json` fallback loading.

## Standalone pass: `bubench attribute`

New `browseruse_bench/cli/attribute.py` subcommand:

- Locates the eval results JSONL exactly like `bubench eval` does
  (`--agent/--data/--split/--model-id/--timestamp`, default = latest).
- Classifies records with `predicted_label == 0`. Default skips records that
  already have `failure_category`; `--force` re-labels everything.
- `--num-worker` concurrency; judge model defaults to config.yaml `eval`
  section, overridable via `--model/--api-key/--base-url`.
- After labeling, refreshes `failure_category_statistics` in the paired
  summary JSON (this also fixes the pre-existing ordering quirk where the
  summary was generated before inline classification ran).

## eval inline path

`bubench eval` keeps running classification at the end by default, through the
same `classify_failures_batch`. Inline and standalone share all code. The
inline path also refreshes summary failure stats after classification.

## Error handling

- Judge call failure -> category `U` with `reasoning = "Classification error: ..."`.
- Truncated JSON -> regex fallback extracts `primary_code`/`codes`.
- Invalid/unknown codes from the judge are dropped; if none remain, category `U`.
- Missing results file -> warn and exit non-zero (attribute) / skip (inline).

## Testing

- Extend `tests/browseruse_bench/test_eval_failure.py`: multi-label parse,
  primary lands in `failure_category`, legacy mapping, OTHER requires phrase,
  U fallback, truncation recovery on `primary_code`, input-construction tests
  stay green.
- New tests for `attribute` CLI helpers: results-file location, skip/force
  semantics, summary refresh.
- End-to-end validation: re-label the three 2026-07-03 gpt-5.5 groups
  (140007/140009/140012, 87 failed cases) with `bubench attribute --force`,
  produce the M-based attribution comparison table.

## Rejected alternatives

- Two parallel systems (scripts/ judge + eval/failure.py): the exact defect of
  PR #68.
- Fully pluggable taxonomy registry: YAGNI now; constants are grouped so a
  future benchmark-specific taxonomy can be parameterized later.
