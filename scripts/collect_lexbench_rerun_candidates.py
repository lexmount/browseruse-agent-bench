#!/usr/bin/env python3
"""Collect high-recall LexBench-Browser rerun task candidates.

By default, this scanner uses only run artifacts:

- tasks/<task_id>/result.json
- tasks/<task_id>/api_logs/step_*.json
- output/logs/run/*.log matching the target MODEL/TIMESTAMP output directory

When ``--include-taxonomy-web-constraints`` is set, it also merges tasks whose
failure taxonomy primary code is M3.2 or M3.3. The recommended workflow is to
run hard artifact mode first, exclude those hard-hit tasks from judge calls,
then run this script again to union hard artifacts with non-hard taxonomy hits.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "LexBench-Browser" / "All" / "browser-use"
DEFAULT_RUN_LOG_DIR = REPO_ROOT / "output" / "logs" / "run"

RUN_LOG_LINE_RE = re.compile(r"\[run\]\s+\[(?P<task_id>[^\]]+)\]\s+(?P<message>.*)")

ACCESS_ERROR_RE = re.compile(
    r"Navigation failed(?: - site unavailable)?|site unavailable|This site can.?t be reached|"
    r"ERR_TUNNEL_CONNECTION_FAILED|ERR_TIMED_OUT|net::ERR_TIMED_OUT|ERR_SOCKET_NOT_CONNECTED|"
    r"ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_(?:RESET|CLOSED|REFUSED)|ERR_HTTP2_PROTOCOL_ERROR",
    re.I,
)
NAVIGATION_FAILED_RE = re.compile(r"Navigation failed|site unavailable", re.I)
ERR_TUNNEL_RE = re.compile(r"ERR_TUNNEL_CONNECTION_FAILED|ERR_SOCKET_NOT_CONNECTED", re.I)
ERR_TIMED_OUT_RE = re.compile(r"ERR_TIMED_OUT|net::ERR_TIMED_OUT", re.I)
CURRENT_TAB_ABOUT_BLANK_RE = re.compile(
    r"(?:^|\n)(?:Tab\s+[^:\n]+:\s+about:blank|Current URL:\s+about:blank|URL:\s+about:blank)",
    re.I,
)
ACTION_ABOUT_BLANK_RE = re.compile(r"(?:Opened new tab with url|Navigated to)\s+about:blank", re.I)
EMPTY_DOM_RE = re.compile(
    r"0 links,\s*0 interactive|0 total elements|Empty DOM|empty DOM|empty content|"
    r"no DOM elements|Page loaded but returned empty content",
    re.I,
)
DETACHED_FOCUS_RE = re.compile(
    r"No valid agent focus|target may have detached|Target closed|Cannot find context|"
    r"SessionManager not initialized|browser is in an unstable state|detached target",
    re.I,
)
BROWSER_EVENT_TIMEOUT_RE = re.compile(
    r"Event handler .* timed out after|CDP request .* timed out|ScreenshotWatchdog .* timed out|"
    r"Navigation failed: .*timed out after",
    re.I | re.S,
)
LLM_TIMEOUT_RE = re.compile(r"LLM call timed out|model service no-response", re.I)
PARSE_ERROR_RE = re.compile(
    r"validation error for AgentOutput|Failed to parse structured output|Invalid JSON|"
    r"malformed JSON|parser failure",
    re.I,
)
BOT_RE = re.compile(
    r"captcha|cloudflare|robot|human verification|403|429|blocked|Too Many Requests|"
    r"abnormal traffic|security check|验证码|人机|验证",
    re.I,
)
RESULT_DOM_ACCESS_RE = re.compile(
    r"0 links,\s*0 interactive|0 total elements|Empty DOM|empty DOM|empty content|"
    r"Page appears empty|No valid agent focus|target may have detached|"
    r"browser is in an unstable state|Cannot find context|Target closed|"
    r"SessionManager not initialized|Navigation failed|site unavailable|"
    r"ERR_TUNNEL_CONNECTION_FAILED|ERR_TIMED_OUT|This site can.?t be reached",
    re.I,
)


@dataclass
class Reason:
    source: str
    rule: str
    detail: str


@dataclass
class Candidate:
    model: str
    timestamp: str
    task_id: str
    reasons: list[Reason] = field(default_factory=list)


@dataclass
class ApiSignalCounts:
    navigation_failed: int = 0
    err_tunnel: int = 0
    err_timed_out: int = 0
    current_tab_about_blank: int = 0
    action_about_blank: int = 0
    empty_dom: int = 0
    detached_focus: int = 0
    browser_event_timeout: int = 0
    llm_timeout: int = 0
    parse_error: int = 0


def natural_key(value: str) -> tuple[int, str]:
    return (0, f"{int(value):012d}") if value.isdigit() else (1, value)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def count_re(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text or ""))


def stringify(value: Any, max_len: int = 10000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_len]
    try:
        return json.dumps(value, ensure_ascii=False)[:max_len]
    except Exception:
        return str(value)[:max_len]


def extract_xmlish_block(text: str, tag: str) -> str:
    match = re.search(fr"<{tag}>(.*?)</{tag}>", text or "", re.S)
    return match.group(1) if match else ""


def result_json_reasons(task_dir: Path) -> list[Reason]:
    result_path = task_dir / "result.json"
    if not result_path.exists() or result_path.stat().st_size == 0:
        return [Reason("result_json", "missing_or_empty_result_json", str(result_path))]

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [Reason("result_json", "invalid_result_json", str(exc))]

    reasons: list[Reason] = []
    done = result.get("agent_done")
    metrics = result.get("metrics") or {}
    config = result.get("config") or {}
    steps = metrics.get("steps") or 0
    max_steps = config.get("max_steps") or 40
    timeout_seconds = config.get("timeout_seconds") or 0
    wall_clock_seconds = result.get("wall_clock_seconds") or 0

    if result.get("env_status") == "failed":
        reasons.append(Reason("result_json", "env_status_failed", "env_status == failed"))
    if done == "error":
        reasons.append(Reason("result_json", "agent_done_error", "agent_done == error"))
    if done == "max_steps" and steps < max_steps:
        reasons.append(
            Reason(
                "result_json",
                "early_max_steps",
                f"agent_done=max_steps but steps={steps} < max_steps={max_steps}",
            )
        )
    if done == "timeout" and timeout_seconds and wall_clock_seconds < timeout_seconds * 0.5:
        reasons.append(
            Reason(
                "result_json",
                "suspicious_early_timeout",
                f"wall_clock_seconds={wall_clock_seconds} < 0.5*timeout_seconds={timeout_seconds}",
            )
        )
    return reasons


def result_was_unsuccessful(task_dir: Path) -> bool:
    result = read_json(task_dir / "result.json")
    if not result:
        return True
    if result.get("agent_success") is False:
        return True
    if result.get("agent_done") in {"error", "timeout", "max_steps"}:
        return True
    if result.get("env_status") == "failed":
        return True
    return False


def result_text(result: dict[str, Any]) -> str:
    chunks: list[str] = []
    for key in ("answer", "error", "exception", "traceback"):
        value = result.get(key)
        if value:
            chunks.append(stringify(value, 5000))
    for action in result.get("action_history") or []:
        chunks.append(stringify(action, 2000))
    return "\n".join(chunks)


def result_done_false_dom_reason(task_dir: Path) -> list[Reason]:
    result = read_json(task_dir / "result.json")
    if result.get("agent_success") is not False:
        return []
    text = result_text(result)
    if not RESULT_DOM_ACCESS_RE.search(text):
        return []
    return [
        Reason(
            "result_json",
            "done_false_with_dom_or_access_evidence",
            "agent_success=false and final result/action history contains DOM/access failure evidence",
        )
    ]


def load_eval_pass_task_ids(run_dir: Path) -> set[str]:
    eval_dir = run_dir / "tasks_eval_result"
    candidates = sorted(eval_dir.glob("*_eval_results.json"))
    pass_ids: set[str] = set()
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            if text.startswith("["):
                rows = json.loads(text)
            else:
                rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        except Exception:
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("predicted_label") == 1 and row.get("task_id") is not None:
                pass_ids.add(str(row["task_id"]))
    return pass_ids


def load_taxonomy_web_constraint_ids(run_dir: Path) -> set[str]:
    eval_dir = run_dir / "tasks_eval_result"
    ids: set[str] = set()
    for path in sorted(eval_dir.glob("*_failure_taxonomy*.jsonl")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            taxonomy = row.get("taxonomy") or {}
            if taxonomy.get("primary_code") in {"M3.2", "M3.3"} and row.get("task_id") is not None:
                ids.add(str(row["task_id"]))
    return ids


def log_matches_run(text: str, run_dir: Path) -> bool:
    normalized_text = text.replace("\\", "/")
    normalized_run = str(run_dir).replace("\\", "/")
    if normalized_run in normalized_text:
        return True
    try:
        model = run_dir.parent.name
        timestamp = run_dir.name
    except Exception:
        return False
    relative = f"experiments/LexBench-Browser/All/browser-use/{model}/{timestamp}"
    return relative in normalized_text


def run_log_reasons_for_message(message: str) -> list[Reason]:
    reasons: list[Reason] = []
    if "Stopping due to 5 consecutive failures" in message:
        reasons.append(
            Reason(
                "latest_agent_run_log",
                "stopping_due_to_5_consecutive_failures",
                "Stopping due to 5 consecutive failures",
            )
        )
    if re.search(r"Result failed\s+6/6\s+times?:.*LLM call timed out", message, re.I):
        reasons.append(
            Reason(
                "latest_agent_run_log",
                "llm_timeout_6_of_6",
                "Result failed 6/6 times: LLM call timed out",
            )
        )
    if "ERR_TUNNEL_CONNECTION_FAILED" in message:
        reasons.append(
            Reason(
                "latest_agent_run_log",
                "err_tunnel_connection_failed",
                "ERR_TUNNEL_CONNECTION_FAILED",
            )
        )
    return reasons


def latest_run_log_reasons(run_dir: Path, run_log_dir: Path) -> tuple[dict[str, list[Reason]], list[str]]:
    matched_logs: list[Path] = []
    latest_task_reasons: dict[str, list[Reason]] = {}

    for log_path in sorted(run_log_dir.glob("*.log"), key=lambda p: (p.stat().st_mtime, p.name)):
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not log_matches_run(text, run_dir):
            continue
        matched_logs.append(log_path)
        reasons_by_task: dict[str, list[Reason]] = defaultdict(list)
        seen_task_ids: set[str] = set()
        for line in text.splitlines():
            match = RUN_LOG_LINE_RE.search(line)
            if not match:
                continue
            task_id = match.group("task_id")
            message = match.group("message")
            seen_task_ids.add(task_id)
            reasons_by_task[task_id].extend(run_log_reasons_for_message(message))

        # A later execution log for the same task supersedes older task-level
        # run-log evidence, even if the later log has no hard signal.
        for task_id in seen_task_ids:
            latest_task_reasons[task_id] = list({r.rule: r for r in reasons_by_task.get(task_id, [])}.values())

    return latest_task_reasons, [str(path) for path in matched_logs]


def collect_api_log_text(api_dir: Path) -> str:
    chunks: list[str] = []
    if not api_dir.exists():
        return ""
    for step_path in sorted(api_dir.glob("step_*.json")):
        step = read_json(step_path)
        input_obj = step.get("input") or {}
        state_message = stringify(input_obj.get("state_message"), 12000)
        browser_state = extract_xmlish_block(state_message, "browser_state")
        chunks.extend(
            [
                stringify(input_obj.get("url"), 1000),
                browser_state,
                stringify(step.get("output"), 5000),
            ]
        )
        for action_result in step.get("action_results") or []:
            chunks.extend(
                [
                    stringify(action_result.get("error"), 2000),
                    stringify(action_result.get("extracted_content"), 2000),
                ]
            )
    return "\n".join(chunks)


def scan_api_signals(text: str) -> ApiSignalCounts:
    return ApiSignalCounts(
        navigation_failed=count_re(NAVIGATION_FAILED_RE, text),
        err_tunnel=count_re(ERR_TUNNEL_RE, text),
        err_timed_out=count_re(ERR_TIMED_OUT_RE, text),
        current_tab_about_blank=count_re(CURRENT_TAB_ABOUT_BLANK_RE, text),
        action_about_blank=count_re(ACTION_ABOUT_BLANK_RE, text),
        empty_dom=count_re(EMPTY_DOM_RE, text),
        detached_focus=count_re(DETACHED_FOCUS_RE, text),
        browser_event_timeout=count_re(BROWSER_EVENT_TIMEOUT_RE, text),
        llm_timeout=count_re(LLM_TIMEOUT_RE, text),
        parse_error=count_re(PARSE_ERROR_RE, text),
    )


def api_log_reasons(task_dir: Path, include_protocol_only: bool, skip_bot: bool) -> list[Reason]:
    text = collect_api_log_text(task_dir / "api_logs")
    if not text:
        return []
    if skip_bot and BOT_RE.search(text):
        return []
    counts = scan_api_signals(text)
    reasons: list[Reason] = []

    hard_access = (
        counts.err_tunnel > 0
        or counts.err_timed_out > 0
        or counts.navigation_failed >= 2
        or counts.browser_event_timeout >= 2
    )
    render_or_session = (
        counts.detached_focus > 0
        or counts.current_tab_about_blank >= 2
        or counts.action_about_blank >= 2
        or counts.empty_dom >= 10
    )
    repeated_protocol = counts.llm_timeout >= 5 or counts.parse_error >= 3

    if hard_access:
        reasons.append(
            Reason(
                "api_logs",
                "api_hard_access_error",
                json.dumps(asdict(counts), ensure_ascii=False, sort_keys=True),
            )
        )
    if render_or_session:
        reasons.append(
            Reason(
                "api_logs",
                "api_render_or_session_error",
                json.dumps(asdict(counts), ensure_ascii=False, sort_keys=True),
            )
        )
    if include_protocol_only and repeated_protocol:
        reasons.append(
            Reason(
                "api_logs",
                "api_repeated_model_protocol_error",
                json.dumps(asdict(counts), ensure_ascii=False, sort_keys=True),
            )
        )
    return reasons


def discover_runs(root: Path) -> list[Path]:
    runs: list[Path] = []
    for tasks_dir in sorted(root.glob("*/*/tasks")):
        run_dir = tasks_dir.parent
        if (run_dir / "tasks").is_dir():
            runs.append(run_dir)
    return runs


def collect_run(
    run_dir: Path,
    run_log_dir: Path,
    include_protocol_only: bool,
    artifact_mode: str = "strict",
    include_taxonomy_web_constraints: bool = False,
) -> tuple[list[Candidate], dict[str, Any]]:
    tasks_dir = run_dir / "tasks"
    model = run_dir.parent.name
    timestamp = run_dir.name
    candidates: dict[str, Candidate] = {}

    def add(task_id: str, reasons: list[Reason]) -> None:
        if not reasons:
            return
        candidate = candidates.setdefault(task_id, Candidate(model=model, timestamp=timestamp, task_id=task_id))
        existing = {(reason.source, reason.rule, reason.detail) for reason in candidate.reasons}
        for reason in reasons:
            key = (reason.source, reason.rule, reason.detail)
            if key not in existing:
                candidate.reasons.append(reason)
                existing.add(key)

    latest_log_reasons, matched_logs = latest_run_log_reasons(run_dir, run_log_dir)
    eval_pass_task_ids = load_eval_pass_task_ids(run_dir)
    taxonomy_web_ids = load_taxonomy_web_constraint_ids(run_dir) if include_taxonomy_web_constraints else set()

    task_dirs = [path for path in tasks_dir.iterdir() if path.is_dir()] if tasks_dir.exists() else []
    for task_dir in sorted(task_dirs, key=lambda p: natural_key(p.name)):
        task_id = task_dir.name
        add(task_id, result_json_reasons(task_dir))
        add(task_id, latest_log_reasons.get(task_id, []))
        if task_id in taxonomy_web_ids:
            add(
                task_id,
                [
                    Reason(
                        "failure_taxonomy",
                        "primary_m3_2_or_m3_3",
                        "failure taxonomy primary_code is M3.2 or M3.3",
                    )
                ],
            )
        if artifact_mode == "strict":
            if task_id not in eval_pass_task_ids:
                add(task_id, result_done_false_dom_reason(task_dir))
            if result_was_unsuccessful(task_dir) and task_id not in eval_pass_task_ids:
                add(task_id, api_log_reasons(task_dir, include_protocol_only=include_protocol_only, skip_bot=True))

    for task_id, reasons in latest_log_reasons.items():
        add(task_id, reasons)

    rows = sorted(candidates.values(), key=lambda row: natural_key(row.task_id))
    metadata = {
        "model": model,
        "timestamp": timestamp,
        "run_dir": str(run_dir),
        "matched_run_logs": matched_logs,
        "eval_pass_filter_count": len(eval_pass_task_ids),
        "taxonomy_web_constraint_count": len(taxonomy_web_ids),
        "artifact_mode": artifact_mode,
        "include_taxonomy_web_constraints": include_taxonomy_web_constraints,
        "task_count": len(task_dirs),
        "rerun_count": len(rows),
        "source_counts": dict(Counter(reason.source for row in rows for reason in row.reasons)),
        "rule_counts": dict(Counter(reason.rule for row in rows for reason in row.reasons)),
    }
    return rows, metadata


def write_outputs(rows: list[Candidate], metadata: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "lexbench_rerun_candidates_v1",
        "metadata": metadata,
        "rerun_ids": [row.task_id for row in rows],
        "rows": [
            {
                "model": row.model,
                "timestamp": row.timestamp,
                "task_id": row.task_id,
                "reasons": [asdict(reason) for reason in row.reasons],
            }
            for row in rows
        ],
    }

    json_path = out_dir / "rerun_candidates.json"
    csv_path = out_dir / "rerun_candidates.csv"
    ids_path = out_dir / "rerun_task_ids.txt"
    md_path = out_dir / "rerun_candidates_summary.md"

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ids_path.write_text(" ".join(payload["rerun_ids"]) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["model", "timestamp", "task_id", "rules", "sources", "details"])
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "model": row.model,
                    "timestamp": row.timestamp,
                    "task_id": row.task_id,
                    "rules": ";".join(reason.rule for reason in row.reasons),
                    "sources": ";".join(sorted({reason.source for reason in row.reasons})),
                    "details": " | ".join(f"{reason.rule}: {reason.detail}" for reason in row.reasons),
                }
            )

    lines = [
        "# LexBench Rerun Candidates",
        "",
        f"- Run: `{metadata['model']}/{metadata['timestamp']}`",
        f"- Tasks scanned: {metadata['task_count']}",
        f"- Rerun candidates: {metadata['rerun_count']}",
        f"- Source counts: `{json.dumps(metadata['source_counts'], ensure_ascii=False)}`",
        f"- Rule counts: `{json.dumps(metadata['rule_counts'], ensure_ascii=False)}`",
        "",
        "## Task IDs",
        "",
        "```text",
        " ".join(payload["rerun_ids"]),
        "```",
        "",
        "## Reasons",
        "",
    ]
    for row in rows:
        rules = ", ".join(reason.rule for reason in row.reasons)
        lines.append(f"- `{row.task_id}`: {rules}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "ids": str(ids_path), "md": str(md_path)}, indent=2))


def resolve_run_dir(args: argparse.Namespace) -> Path:
    if args.run_dir:
        return Path(args.run_dir)
    if not args.model or not args.timestamp:
        raise SystemExit("Provide --run-dir or both --model and --timestamp.")
    return Path(args.root) / args.model / args.timestamp


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect LexBench-Browser rerun task candidates from run artifacts.")
    parser.add_argument("--root", type=Path, default=DEFAULT_EXPERIMENT_ROOT)
    parser.add_argument("--run-dir", type=Path, default=None, help="Specific MODEL/TIMESTAMP run directory.")
    parser.add_argument("--model", default=None, help="Model directory name under --root.")
    parser.add_argument("--timestamp", default=None, help="Timestamp directory under --root/MODEL.")
    parser.add_argument("--run-log-dir", type=Path, default=DEFAULT_RUN_LOG_DIR)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--artifact-mode",
        choices=["hard", "strict"],
        default="strict",
        help="hard = result/log hard rules only; strict = hard plus constrained result/api evidence.",
    )
    parser.add_argument(
        "--include-taxonomy-web-constraints",
        action="store_true",
        help="Also include tasks whose failure taxonomy primary_code is M3.2 or M3.3.",
    )
    parser.add_argument(
        "--include-protocol-only",
        action="store_true",
        help="Also include repeated api_logs parse/LLM-timeout signals without access/render evidence.",
    )
    args = parser.parse_args()

    run_dir = resolve_run_dir(args)
    out_dir = args.out_dir or run_dir / "rerun_candidates"
    rows, metadata = collect_run(
        run_dir,
        args.run_log_dir,
        include_protocol_only=args.include_protocol_only,
        artifact_mode=args.artifact_mode,
        include_taxonomy_web_constraints=args.include_taxonomy_web_constraints,
    )
    write_outputs(rows, metadata, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
