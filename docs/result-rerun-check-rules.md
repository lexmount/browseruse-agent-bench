# Result Rerun Check Rules

This note is for deciding whether a benchmark task result should be rerun.
Do not judge by the final run summary alone. Inspect each task's `result.json`.

## Key Fields

Check these fields first:

```json
{
  "env_status": "...",
  "agent_done": "...",
  "agent_success": null,
  "error": null,
  "metrics": {
    "steps": 0,
    "usage": {
      "total_tokens": 0
    }
  },
  "action_history": [],
  "config": {
    "timeout_seconds": 1800,
    "max_steps": 40
  },
  "wall_clock_seconds": 0
}
```

## Definitely Rerun

These are run/environment failures, not valid model outcomes.

1. Missing, empty, or invalid `result.json`.
2. `agent_done == "error"`.
3. `env_status == "failed"`.
4. `agent_done == "max_steps"` but `metrics.steps < config.max_steps`.

Rule 4 matters because browser-use can stop early after internal consecutive failures, while the bench wrapper records it as `max_steps`. Example signs:

```text
agent_done=max_steps
config.max_steps=40
metrics.steps=6
answer="Waited for 7 seconds"
```

This is not a real max-steps run. It usually means browser-use stopped after internal failures such as:

```text
Stopping due to 5 consecutive failures
CDP request ... timed out
ScreenshotWatchdog ... timed out
Expected at least one handler to return a non-None result
```

5. `agent_done == "timeout"` but `wall_clock_seconds < config.timeout_seconds * 0.5`.

This is the key signal: a real timeout must run for (almost) the full budget. If the
run is labeled `timeout` but actually stopped at less than half the budget, the process
died early (browser startup / CDP crash / internal error) and was mislabeled as a task
timeout. This holds **regardless of `steps`, `total_tokens`, or `action_history`**.

There are two sub-cases, both covered by the single rule above:

```text
# 5a) zero-progress startup death
steps == 0, total_tokens == 0, action_history == 0
wall_clock_seconds = 66, timeout_seconds = 1800   # ratio 0.04

# 5b) mid-run crash with partial progress (do NOT skip these)
steps = 19, total_tokens = 58380, action_history = 9
wall_clock_seconds = 83, timeout_seconds = 600     # ratio 0.14
# or even wall_clock_seconds = 0 with real steps/tokens (process crashed)
```

Sub-case 5b is easy to miss if you only check `steps == 0`. Any timeout whose
`wall_clock_seconds` is far below the budget must be rerun even if it made progress.

## Definitely Rerun From Agent Run Logs

The `result.json` hard rules above are not enough by themselves. Some browser-use
internal failures are written back as `agent_done == "done"` or otherwise look
valid in `result.json`. For high recall, also inspect the corresponding **agent
execution run log** under:

```text
/Users/abc/Desktop/lexmount/browseruse-agent-bench/output/logs/run
```

Use the log that matches the target run's output directory, e.g. a log containing:

```text
Running browser-use on LexBench-Browser
Output: .../experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP
[RUNNING] Executing task with browser-use agent
[Agent] ...
[BrowserSession] ...
```

Do not use eval/judge-only logs for these checks. Eval logs may be newer, but they
do not contain the browser-use execution evidence.

Add any task with these log signals to the hard rerun set:

1. `Stopping due to 5 consecutive failures`.
2. `Result failed 6/6 times: LLM call timed out`.
3. `ERR_TUNNEL_CONNECTION_FAILED`.

Rationale:

```text
Stopping due to 5 consecutive failures
  => browser-use hit its internal failure threshold; not a normal task outcome

Result failed 6/6 times: LLM call timed out
  => the model service failed repeatedly until browser-use stopped/recovered badly

ERR_TUNNEL_CONNECTION_FAILED
  => network/tunnel failure, not a model capability failure
```

For high recall, treat these as hard rerun rules even if `result.json` says
`agent_done == "done"` or `agent_success == true`.

## Not Automatically Rerun

These may be valid failed model outcomes.

1. `agent_done == "timeout"` that actually used (almost) the full budget:

```text
wall_clock_seconds >= config.timeout_seconds * 0.5
(usually with steps > 0, total_tokens > 0, action_history non-empty)
```

This means the task genuinely ran out of time. The deciding factor is
`wall_clock_seconds`, not `steps`: if it is below half the budget, treat it as
Definitely Rerun rule 5 instead.

2. `agent_done == "max_steps"` with `metrics.steps >= config.max_steps`.

This means the task used its step budget. It can be model failure or task difficulty, not necessarily infrastructure failure.

3. `agent_done == "done"` and `agent_success == false`.

The agent explicitly ended and marked failure. This is usually a model/task outcome, not a broken run.

## Provisional `api_logs` Signals

The hard rules above only inspect `result.json`. They do **not** catch every
site/browser failure. Some runs end with `agent_done == "done"` and
`agent_success == false`, but the per-step `api_logs/step_*.json` still show
real access or rendering failures.

These signals are useful before failure attribution exists and for validating
M3.3 coverage, but broad `api_logs` render/session matching is **not** the final
default rerun rule. It creates too many false positives because transient empty
DOM/loading states can recover inside a successful trajectory.

Useful access/rendering/model-service signals:

```text
Navigation failed - site unavailable
ERR_TUNNEL_CONNECTION_FAILED
ERR_TIMED_OUT / net::ERR_TIMED_OUT
ERR_SOCKET_NOT_CONNECTED
ERR_CONNECTION_RESET / ERR_CONNECTION_CLOSED / ERR_CONNECTION_REFUSED
This site can’t be reached
Current tab/URL is about:blank repeatedly
0 links, 0 interactive / 0 total elements / Empty DOM / empty content
No valid agent focus available - target may have detached
Target closed / Cannot find context / SessionManager not initialized
Event handler ... timed out after ... / CDP request ... timed out
LLM call timed out / model service no-response
Failed to parse structured output / Invalid JSON / malformed JSON
```

Pre-attribution interpretation:

```text
unsuccessful task + repeated hard access errors
  => provisional rerun_candidate

unsuccessful task + repeated empty DOM/about:blank/detached focus
  => provisional rerun_candidate

otherwise successful task + transient empty DOM/loading evidence
  => do not rerun by api_logs alone

bot-defense evidence such as CAPTCHA/403/Cloudflare
  => do not use api_logs alone; this is usually M3.1
```

Older M3.3 audit reports may contain `manual_review`. For validation-only
M3.3 recall studies, merge those rows into `rerun_candidate`. For the final
rerun set, use the post-attribution rule below instead.

Use the rerun scanner in hard mode first to collect deterministic rerun ids for
a target run:

```zsh
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --root /Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use \
  --model MODEL \
  --timestamp TIMESTAMP \
  --artifact-mode hard \
  --out-dir experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/rerun_candidates_hard
```

This hard pre-check does not require failure-attribution results. Outputs are
written to:

```text
experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/rerun_candidates_hard/
  rerun_candidates.json
  rerun_candidates.csv
  rerun_candidates_summary.md
  rerun_task_ids.txt
```

These hard-hit tasks should go directly into the rerun set and can be excluded
from eval and failure attribution.

There is also an optional strict artifact diagnostic mode:

```zsh
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --root /Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use \
  --model MODEL \
  --timestamp TIMESTAMP
```

The strict mode includes result hard rules, latest run-log hard rules, and
constrained api-log access/render/session evidence. It is useful for debugging
before attribution exists, but it is not the final default rerun rule. Repeated
parse or LLM-timeout-only api-log evidence can be added to an even broader
debugging pool with:

```zsh
--include-protocol-only
```

In strict mode, the `api_logs` part is applied only to unsuccessful task results
by default. This avoids rerunning tasks that recovered from transient
loading/empty-DOM states and finished successfully. `result.json` hard rules and
latest run-log hard rules still take precedence even if `agent_success == true`.

After failure attribution is available, use the final high-recall mode. In the
token-efficient workflow, run this after the hard artifact pre-check and
attribution on the remaining non-hard failures:

```zsh
PYTHONPATH=. python scripts/collect_lexbench_rerun_candidates.py \
  --root /Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use \
  --model MODEL \
  --timestamp TIMESTAMP \
  --artifact-mode hard \
  --include-taxonomy-web-constraints
```

This mode uses:

```text
hard_artifact_rerun
∪ taxonomy_primary_M3.2_or_M3.3_on_non_hard_tasks
```

On the 12 current model runs, this rule covered `171/171` primary M3.2/M3.3
failures with `219` total candidates and `48` non-M3.2/M3.3 candidates. This is
the recommended rule when the goal is to reduce M3.2/M3.3 while keeping false
positives bounded.

Use the M3.3 taxonomy audit script only to validate rule recall against failure
attribution:

```zsh
PYTHONPATH=. python scripts/audit_m3_3_api_log_failures.py \
  --root /Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use
```

Both scanners are string-evidence detectors, not semantic oracles. They can
prove that logs contain these errors, but they cannot guarantee that rerunning
will fix the task or that the original attribution is wrong. This rule
intentionally prioritizes recall over precision.

## Final Token-Efficient Rerun Set

For each `MODEL/TIMESTAMP`, the final rerun candidate set is:

```text
hard_artifact_rerun
∪ taxonomy_primary_M3.2_or_M3.3_on_non_hard_tasks
```

Recommended order:

```text
1. Run hard artifact pre-check.
2. Put hard-hit tasks directly into the rerun set.
3. Exclude hard-hit tasks from eval/failure attribution when possible.
4. Run eval and failure attribution on the remaining tasks.
5. Add remaining tasks whose attribution primary_code is M3.2 or M3.3.
```

This avoids spending judge tokens on deterministic run/infrastructure failures.
When failure attribution is not available yet, use the pre-attribution scanner
output as a provisional artifact-only rerun set.

If asking Codex or another agent to collect ids, give this instruction:

```text
For the target MODEL/TIMESTAMP under
/Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use,
run scripts/collect_lexbench_rerun_candidates.py with
`--artifact-mode hard --include-taxonomy-web-constraints` and return the union of:
1. hard artifact rerun ids from result.json and latest matching run logs,
2. failure-taxonomy ids whose primary_code is M3.2 or M3.3 among non-hard tasks.

Return sorted unique task ids and the reason for each id.
```

## Quick Result Check Command

Set `TASKS_DIR` to a run's `tasks` directory:

```zsh
TASKS_DIR=/Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use/MODEL/TIMESTAMP/tasks

PYTHONPATH=. ./.venvs/browser_use/bin/python - <<'PY'
import json
import os
from pathlib import Path
from collections import Counter

root = Path(os.environ["TASKS_DIR"])
hard = []
timeout_suspicious = []
load_bad = []
counts = Counter()

for d in sorted(root.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 999999):
    if not d.is_dir():
        continue
    result_path = d / "result.json"
    if not result_path.exists() or result_path.stat().st_size == 0:
        load_bad.append((d.name, "missing_or_empty_result_json"))
        continue
    try:
        result = json.loads(result_path.read_text())
    except Exception as exc:
        load_bad.append((d.name, f"invalid_json:{exc}"))
        continue

    done = result.get("agent_done")
    counts[done] += 1
    metrics = result.get("metrics") or {}
    usage = metrics.get("usage") or {}
    config = result.get("config") or {}

    steps = metrics.get("steps") or 0
    total_tokens = usage.get("total_tokens") or 0
    actions = len(result.get("action_history") or [])
    max_steps = config.get("max_steps") or 40
    timeout_seconds = config.get("timeout_seconds") or 0
    wall_clock_seconds = result.get("wall_clock_seconds") or 0

    reasons = []
    if result.get("env_status") == "failed":
        reasons.append("env_status=failed")
    if done == "error":
        reasons.append("agent_done=error")
    if done == "max_steps" and steps < max_steps:
        reasons.append(f"early max_steps: steps={steps} < max_steps={max_steps}")
    if done == "timeout" and timeout_seconds and wall_clock_seconds < timeout_seconds * 0.5:
        reasons.append(
            f"suspicious timeout: wall={wall_clock_seconds} < 0.5*timeout={timeout_seconds} "
            f"(steps={steps} tokens={total_tokens} actions={actions})"
        )

    if reasons:
        hard.append((d.name, reasons))

print("agent_done_counts:", dict(counts))
print("load_bad:", load_bad)
print("rerun_count:", len(hard))
print("rerun_ids:", " ".join(tid for tid, _ in hard))
print()
for tid, reasons in hard:
    print(tid, "|", "; ".join(reasons))
PY
```

## Quick Agent Log Check Command

Set `RUN_LOG` to the matching agent execution log for the same `MODEL/TIMESTAMP`:

```zsh
RUN_LOG=/Users/abc/Desktop/lexmount/browseruse-agent-bench/output/logs/run/RUN_LOG_FILE.log

PYTHONPATH=. ./.venvs/browser_use/bin/python - <<'PY'
import os
import re
from collections import defaultdict
from pathlib import Path

log_path = Path(os.environ["RUN_LOG"])
line_re = re.compile(r"\[run\] \[(\d+)\] (.*)")

reasons_by_task = defaultdict(set)

for line in log_path.read_text(errors="replace").splitlines():
    match = line_re.search(line)
    if not match:
        continue
    task_id, message = match.groups()
    if "Stopping due to 5 consecutive failures" in message:
        reasons_by_task[task_id].add("stopping_due_to_5_consecutive_failures")
    if "Result failed 6/6 times" in message and "LLM call timed out" in message:
        reasons_by_task[task_id].add("llm_timeout_6_of_6")
    if "ERR_TUNNEL_CONNECTION_FAILED" in message:
        reasons_by_task[task_id].add("err_tunnel_connection_failed")

print("log_rerun_count:", len(reasons_by_task))
print("log_rerun_ids:", " ".join(sorted(reasons_by_task, key=lambda x: int(x) if x.isdigit() else x)))
print()
for task_id in sorted(reasons_by_task, key=lambda x: int(x) if x.isdigit() else x):
    print(task_id, "|", "; ".join(sorted(reasons_by_task[task_id])))
PY
```

## Rerun Command Pattern

Do not use `--skip-completed` when rerunning failed tasks in the same timestamp.

```zsh
PYTHONPATH=. ./.venvs/browser_use/bin/python scripts/run.py \
  --agent browser-use \
  --data LexBench-Browser \
  --split All \
  --model MODEL_CONFIG_KEY \
  --timestamp TIMESTAMP \
  --mode specific \
  --task-ids IDS_HERE \
  --concurrency 3 \
  --timeout 1800 \
  --no-group-by-site
```
