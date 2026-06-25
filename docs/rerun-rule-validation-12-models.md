# Rerun Rule Validation on 12 Model Runs

Validation date: 2026-06-25

Scope:

```text
experiments/LexBench-Browser/All/browser-use/*/*/
```

Runs with failure-taxonomy output: 12

Validation target:

```text
primary_code in {M3.2, M3.3}
```

M3.1 is excluded from the target because bot defense is usually an inherent
website/automation constraint rather than a rerun-fixable environment failure.

## Key Finding

Artifact-only rules cannot simultaneously achieve high M3.2/M3.3 recall and low
false positives.

Reason:

- Stable DOM/LLM failures are usually visible through `max_steps` or
  `Stopping due to 5 consecutive failures`.
- But many M3.3 rows are semantic site/content limitations. Their logs can look
  similar to M1/M2 failures or transient loading states.
- Broad `api_logs` render/session scans catch more M3.3, but also catch many
  M1/M2/M3.1 and evaluator-passed tasks.

## Rule Iterations

### Artifact-Only Strict Rule

Definition:

```text
result_json_hard
∪ latest_agent_run_log_hard
∪ constrained result/api DOM/access evidence
```

Constraints:

- Do not apply api-log soft rules to evaluator-passed tasks.
- Skip api-log soft rules when bot-defense signals are present.
- Do not use repeated parse/LLM-timeout-only api-log evidence by default.
- Use higher empty-DOM thresholds to avoid transient loading false positives.

Result:

```text
M3.2/M3.3 target: 171
hit: 92
recall: 53.8%
total candidates: 191
false positives vs M3.2/M3.3: 99
```

This is acceptable as a pre-attribution artifact scan, but not enough for the
final high-recall rerun pool.

### Final Token-Efficient Rule

Definition:

```text
hard_artifact_rerun
∪ taxonomy_primary_M3.2_or_M3.3_on_non_hard_tasks
```

Command:

```zsh
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --model MODEL \
  --timestamp TIMESTAMP \
  --artifact-mode hard \
  --include-taxonomy-web-constraints
```

Result:

```text
M3.2/M3.3 target: 171
hit: 171
recall: 100.0%
total candidates: 219
false positives vs M3.2/M3.3: 48
```

False-positive breakdown:

```text
PASS/none: 29
M2.3: 7
M2.2: 5
M3.1: 3
M1.1: 3
M1.3: 1
```

These 48 are not from broad api-log expansion; they are hard artifact signals
such as consecutive failures, early max-steps, tunnel errors, or LLM timeout 6/6.

## 12-Model Result Table

| Model | Candidates | M3.2/M3.3 Target | Hit | Recall | Non-M3.2/M3.3 |
|---|---:|---:|---:|---:|---:|
| MiniMax-M3 | 22 | 20 | 20 | 100.0% | 2 |
| bu-2-0 | 15 | 15 | 15 | 100.0% | 0 |
| dmx-claude-opus-4-8-thinking | 27 | 9 | 9 | 100.0% | 18 |
| doubao-seed-2-0-pro | 22 | 21 | 21 | 100.0% | 1 |
| doubao-seed-2-1-pro-260628 | 32 | 20 | 20 | 100.0% | 12 |
| gemini-3.1-pro-preview | 15 | 10 | 10 | 100.0% | 5 |
| gemini-3.5-flash | 6 | 5 | 5 | 100.0% | 1 |
| glm-5.1 | 22 | 20 | 20 | 100.0% | 2 |
| glm-5.2 | 14 | 12 | 12 | 100.0% | 2 |
| gpt-5.5 | 7 | 6 | 6 | 100.0% | 1 |
| kimi-k2.6 | 20 | 18 | 18 | 100.0% | 2 |
| qwen3.7-max | 17 | 15 | 15 | 100.0% | 2 |
| **Total** | **219** | **171** | **171** | **100.0%** | **48** |

## Final Recommendation

Use hard pre-check first, then attribution only for the remaining ambiguous
failures:

1. **Hard artifact pre-check**: catch deterministic infrastructure failures and
   send them directly to rerun without judge calls.
2. **Eval/failure attribution on non-hard tasks**: classify only the remaining
   failed tasks.
3. **Post-attribution rerun check**: add non-hard tasks whose attribution
   primary code is M3.2 or M3.3.

This is the rule that satisfies high M3.2/M3.3 recall while keeping false
positives bounded and avoiding unnecessary attribution tokens for deterministic
hard failures.

Do not use broad api-log render/session evidence as a default hard rerun rule.
It should remain constrained or optional because transient empty DOM/loading
states can recover inside a successful trajectory.
