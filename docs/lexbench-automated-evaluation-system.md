# LexBench Automated Evaluation System

This branch packages the LexBench-Browser post-run workflow into one ordered
automation pipeline:

```text
run benchmark
→ hard artifact pre-check
→ eval non-hard tasks
→ failure attribution on non-hard failures
→ post-attribution rerun check
→ rerun selected tasks
→ re-eval
→ final failure attribution / visualization
```

The workflow intentionally runs deterministic hard-artifact rules before judge
calls. Hard-hit tasks go straight into the rerun set and can be excluded from
eval/failure-attribution to save judge tokens. Attribution is then used only to
catch non-hard failures whose primary cause is M3.2/M3.3.

## File Map

Core rerun rules:

```text
docs/result-rerun-check-rules.md
docs/rerun-rule-validation-12-models.md
scripts/collect_lexbench_rerun_candidates.py
scripts/audit_m3_3_api_log_failures.py
```

Failure attribution prompt and runner:

```text
browseruse_bench/eval/lexbench_browser/prompts/failure_taxonomy_system.txt
scripts/judge_lexbench_failure_taxonomy.py
```

Failure-attribution visualizations and reports:

```text
scripts/plot_failure_taxonomy_figure.py
scripts/plot_generation_failure_comparison.py
scripts/build_generation_comparison_docs.py
```

Standard benchmark/eval entrypoints:

```text
scripts/run.py
scripts/eval.py
```

## Stage 1: Run Benchmark

Run the benchmark normally. This produces task workspaces and agent execution
logs:

```text
experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/tasks/*/result.json
experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/tasks/*/api_logs/
output/logs/run/*.log
```

## Stage 2: Hard Artifact Pre-Check

Run deterministic hard rules before any judge call:

```zsh
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --model MODEL_DIR_NAME \
  --timestamp TIMESTAMP \
  --artifact-mode hard \
  --out-dir experiments/LexBench-Browser/All/browser-use/MODEL_DIR_NAME/TIMESTAMP/rerun_candidates_hard
```

This collects only:

```text
result_json_hard
∪ latest_agent_run_log_hard
```

These tasks are definite run/infrastructure failures and do not need eval or
failure attribution before rerun. Their ids are written to:

```text
experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/rerun_candidates_hard/rerun_task_ids.txt
```

## Stage 3: Evaluate Non-Hard Tasks

Run the normal LexBench-Browser evaluator, excluding hard-hit tasks:

```zsh
PYTHONPATH=. ./.venvs/browser_use/bin/python scripts/eval.py \
  --data LexBench-Browser \
  --split All \
  --agent browser-use \
  --model MODEL_CONFIG_KEY \
  --timestamp TIMESTAMP \
  --exclude-task-ids-file experiments/LexBench-Browser/All/browser-use/MODEL_DIR_NAME/TIMESTAMP/rerun_candidates_hard/rerun_task_ids.txt
```

The expected eval output is:

```text
experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/tasks_eval_result/
  task_gpt-4.1_per_task_threshold_stepwise_eval_results.json
```

## Stage 4: Failure Attribution on Non-Hard Failures

Run failure attribution after evaluation, again excluding hard-hit tasks. This
keeps judge tokens focused on failures that are not already deterministic
reruns.

Prompt:

```text
browseruse_bench/eval/lexbench_browser/prompts/failure_taxonomy_system.txt
```

Runner:

```zsh
PYTHONPATH=. python scripts/judge_lexbench_failure_taxonomy.py \
  --experiments-root /Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use \
  --models MODEL_DIR_NAME \
  --eval-filename task_gpt-4.1_per_task_threshold_stepwise_eval_results.json \
  --model gpt-5.5-judge \
  --include-judge-in-output \
  --exclude-task-ids-file experiments/LexBench-Browser/All/browser-use/MODEL_DIR_NAME/TIMESTAMP/rerun_candidates_hard/rerun_task_ids.txt \
  --num-workers 4
```

Default output:

```text
tasks_eval_result/
  task_gpt-4.1_per_task_threshold_stepwise_failure_taxonomy_gpt-5.5-judge.jsonl
  task_gpt-4.1_per_task_threshold_stepwise_failure_taxonomy_gpt-5.5-judge_summary.json
```

## Stage 5: Post-Attribution Rerun Check

This is the recommended final rerun pool for reducing M3.2/M3.3:

```zsh
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --model MODEL_DIR_NAME \
  --timestamp TIMESTAMP \
  --artifact-mode hard \
  --include-taxonomy-web-constraints
```

The final post-attribution set is:

```text
hard_artifact_rerun
∪ taxonomy_primary_M3.2_or_M3.3_on_non_hard_tasks
```

Where:

- `hard_artifact_rerun` is the Stage 2 set: result-json hard failures plus latest run-log hard failures.
- `taxonomy_primary_M3.2_or_M3.3_on_non_hard_tasks` catches attribution primary-code `M3.2 Access Barrier` or `M3.3 Site Limitation` among the remaining evaluated failures.

Outputs are written to:

```text
experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/rerun_candidates/
  rerun_candidates.json
  rerun_candidates.csv
  rerun_candidates_summary.md
  rerun_task_ids.txt
```

On the 12 current model runs, this reached:

```text
M3.2/M3.3 target: 171
hit: 171
recall: 100.0%
total candidates: 219
false positives vs primary M3.2/M3.3: 48
```

There is also a broader artifact-only mode for debugging before attribution
exists:

```zsh
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --model MODEL_DIR_NAME \
  --timestamp TIMESTAMP
```

This provisional mode reads only `result.json`, `api_logs`, and
`output/logs/run`. It is not the final high-recall rule.

Detailed rule definitions live in:

```text
docs/result-rerun-check-rules.md
```

By default, the provisional artifact-only scanner does not include repeated
parse/LLM-timeout-only api-log evidence unless there is also
access/render/session evidence. To include those protocol-only candidates as an
even broader debugging pool, add:

```zsh
--include-protocol-only
```

## Stage 6: Rerun Candidates

Read task ids from:

```zsh
IDS="$(cat experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/rerun_candidates/rerun_task_ids.txt)"
```

Then rerun those tasks in the same timestamp:

```zsh
PYTHONPATH=. ./.venvs/browser_use/bin/python scripts/run.py \
  --agent browser-use \
  --data LexBench-Browser \
  --split All \
  --model MODEL_CONFIG_KEY \
  --timestamp TIMESTAMP \
  --mode specific \
  --task-ids $IDS \
  --concurrency 3 \
  --timeout 1800 \
  --no-group-by-site
```

Do not use `--skip-completed` for this rerun. These tasks are intentionally being overwritten/retested.

## Stage 7: Re-Evaluate Rerun Results

After rerun, run the evaluator again:

```zsh
PYTHONPATH=. ./.venvs/browser_use/bin/python scripts/eval.py \
  --data LexBench-Browser \
  --split All \
  --agent browser-use \
  --model MODEL_CONFIG_KEY \
  --timestamp TIMESTAMP
```

The expected eval output is still under:

```text
experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/tasks_eval_result/
  task_gpt-4.1_per_task_threshold_stepwise_eval_results.json
```

## Stage 8: Re-Run Failure Attribution

After rerun and re-eval, run failure attribution again so final analysis uses the
latest task outcomes.

Prompt:

```text
browseruse_bench/eval/lexbench_browser/prompts/failure_taxonomy_system.txt
```

Runner:

```zsh
PYTHONPATH=. python scripts/judge_lexbench_failure_taxonomy.py \
  --experiments-root /Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use \
  --models MODEL_DIR_NAME \
  --eval-filename task_gpt-4.1_per_task_threshold_stepwise_eval_results.json \
  --model gpt-5.5-judge \
  --include-judge-in-output \
  --num-workers 4
```

Default output:

```text
tasks_eval_result/
  task_gpt-4.1_per_task_threshold_stepwise_failure_taxonomy_gpt-5.5-judge.jsonl
  task_gpt-4.1_per_task_threshold_stepwise_failure_taxonomy_gpt-5.5-judge_summary.json
```

The taxonomy output is both part of the post-attribution rerun check and the
input to model capability analysis.

## Stage 9: Validate Rerun Rule Recall

Use taxonomy output to measure whether the post-attribution rerun rule covers
M3.2/M3.3 while keeping false positives bounded. The current validation record is:

```text
docs/rerun-rule-validation-12-models.md
```

Current 12-model result:

```text
M3.2/M3.3 target: 171
hit: 171
recall: 100.0%
total candidates: 219
false positives vs primary M3.2/M3.3: 48
```

For auxiliary M3.3-specific api-log audits, use:

```zsh
PYTHONPATH=. python scripts/audit_m3_3_api_log_failures.py \
  --root /Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use
```

This writes:

```text
experiments/LexBench-Browser/All/browser-use/failure_taxonomy_review/
  m3_3_api_log_failure_scan.json
  m3_3_api_log_failure_scan.csv
  m3_3_api_log_failure_scan_summary.md
```

This audit is for diagnosis and rule validation. It is not the final rerun
selection rule.

## Stage 10: Visualize Failure Attribution

Main failure taxonomy figure:

```zsh
PYTHONPATH=. python scripts/plot_failure_taxonomy_figure.py
```

Generation comparison figure:

```zsh
PYTHONPATH=. python scripts/plot_generation_failure_comparison.py
```

Generation comparison document:

```zsh
PYTHONPATH=. python scripts/build_generation_comparison_docs.py
```

Outputs are written under:

```text
reports/
reports/assets/
```

`plot_failure_taxonomy_figure.py` also writes paper figures to the configured paper figure directory:

```text
/Users/abc/Desktop/lexmount/lexbench_arxiv_paper/lexmount_tech_report/fig
```

## Recommended End-to-End Order

```text
1. Run benchmark tasks.
2. Run hard artifact pre-check.
3. Evaluate non-hard task results.
4. Run failure attribution on non-hard evaluator-failed tasks.
5. Run post-attribution rerun check.
6. Rerun selected candidates.
7. Re-evaluate rerun results.
8. Re-run failure attribution on final failures.
9. Generate taxonomy figures/reports.
10. Optionally cross-check rerun-rule recall against M3.2/M3.3.
```

Keep these two concepts separate:

- **Hard artifact pre-check** answers: "Which tasks are deterministic infrastructure/run failures and can skip judge calls?"
- **Post-attribution rerun check** answers: "Which additional non-hard tasks should be rerun to reduce M3.2/M3.3?"
- **Failure attribution** answers: "For the evaluated failed trajectory, what capability or web-constraint category best explains the failure?"
