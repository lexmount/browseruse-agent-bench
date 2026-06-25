#!/usr/bin/env python3
"""Audit M3.3 failures for explicit browser/network evidence in api_logs.

This is a high-recall log scanner for rerun candidates. It proves that
specific failure strings occurred in the trajectory; it does not prove the task
is semantically impossible or that rerunning will fix the result.
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


TAXONOMY_NAME = (
    "task_gpt-4.1_per_task_threshold_stepwise_failure_taxonomy_gpt-5.5-judge.jsonl"
)
EVAL_NAME = "task_gpt-4.1_per_task_threshold_stepwise_eval_results.json"


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
ACTION_ABOUT_BLANK_RE = re.compile(
    r"(?:Opened new tab with url|Navigated to)\s+about:blank", re.I
)
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
BOT_RE = re.compile(r"captcha|cloudflare|robot|human verification|403|429|blocked", re.I)
CONTENT_LIMIT_RE = re.compile(
    r"not available|not exposed|does not expose|does not provide|no active|not exist|"
    r"not found|missing requested|无法找到|没有提供|不提供|不存在|未公开|未暴露",
    re.I,
)


@dataclass
class SignalCounts:
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
    bot_signal: int = 0
    content_limitation_text: int = 0


@dataclass
class AuditRow:
    agent: str
    timestamp: str
    task_id: str
    task_type: str | None
    score: Any
    predicted_label: Any
    agent_done: Any
    agent_success: Any
    wall_clock_seconds: Any
    steps: Any
    primary_code: str
    recommendation: str
    category: str
    signal_counts: SignalCounts
    evidence: list[str] = field(default_factory=list)
    taxonomy_reasoning: str = ""
    final_answer_excerpt: str = ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def stringify(value: Any, max_len: int = 5000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_len]
    try:
        return json.dumps(value, ensure_ascii=False)[:max_len]
    except Exception:
        return str(value)[:max_len]


def count_re(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text or ""))


def collect_log_text_and_evidence(api_dir: Path) -> tuple[str, list[str]]:
    chunks: list[str] = []
    evidence: list[str] = []
    if not api_dir.exists():
        return "", evidence

    for step_path in sorted(api_dir.glob("step_*.json")):
        step = load_json(step_path)
        step_no = step.get("metadata", {}).get("step_number") or step_path.stem
        input_obj = step.get("input") or {}
        state_message = stringify(input_obj.get("state_message"), max_len=10000)
        url = stringify(input_obj.get("url"), max_len=500)
        output = stringify(step.get("output"), max_len=3000)
        action_results = step.get("action_results") or []

        chunks.extend([url, state_message, output])

        for action_result in action_results:
            error = stringify(action_result.get("error"), max_len=1000)
            content = stringify(action_result.get("extracted_content"), max_len=1000)
            chunks.extend([error, content])

            line = error or content
            if not line:
                continue
            if (
                ACCESS_ERROR_RE.search(line)
                or EMPTY_DOM_RE.search(line)
                or DETACHED_FOCUS_RE.search(line)
                or BROWSER_EVENT_TIMEOUT_RE.search(line)
                or LLM_TIMEOUT_RE.search(line)
                or PARSE_ERROR_RE.search(line)
                or ACTION_ABOUT_BLANK_RE.search(line)
            ):
                evidence.append(f"{step_no}: {line[:300]}")

    # Preserve order while deduplicating.
    unique_evidence = list(dict.fromkeys(evidence))
    return "\n".join(chunks), unique_evidence[:12]


def scan_signals(text: str, taxonomy_reasoning: str, final_answer: str) -> SignalCounts:
    combined = "\n".join([text, taxonomy_reasoning, final_answer])
    return SignalCounts(
        navigation_failed=count_re(NAVIGATION_FAILED_RE, combined),
        err_tunnel=count_re(ERR_TUNNEL_RE, combined),
        err_timed_out=count_re(ERR_TIMED_OUT_RE, combined),
        current_tab_about_blank=count_re(CURRENT_TAB_ABOUT_BLANK_RE, text),
        action_about_blank=count_re(ACTION_ABOUT_BLANK_RE, combined),
        empty_dom=count_re(EMPTY_DOM_RE, combined),
        detached_focus=count_re(DETACHED_FOCUS_RE, combined),
        browser_event_timeout=count_re(BROWSER_EVENT_TIMEOUT_RE, combined),
        llm_timeout=count_re(LLM_TIMEOUT_RE, combined),
        parse_error=count_re(PARSE_ERROR_RE, combined),
        bot_signal=count_re(BOT_RE, combined),
        content_limitation_text=count_re(CONTENT_LIMIT_RE, "\n".join([taxonomy_reasoning, final_answer])),
    )


def classify(counts: SignalCounts) -> tuple[str, str]:
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
        or counts.empty_dom >= 2
        or counts.browser_event_timeout > 0
    )
    model_protocol = counts.llm_timeout > 0 or counts.parse_error > 0
    content_limit = counts.content_limitation_text > 0

    if hard_access:
        if model_protocol or content_limit:
            return "rerun_candidate", "hard_access_error_mixed"
        return "rerun_candidate", "hard_access_error"
    if render_or_session:
        if model_protocol or content_limit:
            return "rerun_candidate", "render_or_session_error_mixed"
        return "rerun_candidate", "render_or_session_error"
    if content_limit and not model_protocol:
        return "keep_m3_3", "content_or_site_capability_missing"
    if model_protocol:
        return "rerun_candidate", "model_protocol_mixed"
    return "rerun_candidate", "unclear_m3_3"


def discover_runs(root: Path) -> list[tuple[str, str, Path]]:
    runs: list[tuple[str, str, Path]] = []
    for taxonomy_path in sorted(root.glob(f"*/*/tasks_eval_result/{TAXONOMY_NAME}")):
        run_dir = taxonomy_path.parents[1]
        timestamp = run_dir.name
        agent = run_dir.parent.name
        runs.append((agent, timestamp, run_dir))
    return runs


def audit_run(agent: str, timestamp: str, run_dir: Path) -> list[AuditRow]:
    taxonomy_path = run_dir / "tasks_eval_result" / TAXONOMY_NAME
    rows = read_jsonl(taxonomy_path)
    out: list[AuditRow] = []

    for row in rows:
        taxonomy = row.get("taxonomy") or {}
        if taxonomy.get("primary_code") != "M3.3":
            continue

        task_id = str(row.get("task_id"))
        result_path = run_dir / "tasks" / task_id / "result.json"
        result = load_json(result_path)
        api_dir = run_dir / "tasks" / task_id / "api_logs"
        log_text, evidence = collect_log_text_and_evidence(api_dir)
        taxonomy_reasoning = stringify(taxonomy.get("reasoning"), max_len=2000)
        final_answer = stringify(result.get("answer") or row.get("agent_response"), max_len=2000)
        counts = scan_signals(log_text, taxonomy_reasoning, final_answer)
        recommendation, category = classify(counts)
        metrics = result.get("metrics") or {}

        out.append(
            AuditRow(
                agent=agent,
                timestamp=timestamp,
                task_id=task_id,
                task_type=row.get("task_type"),
                score=row.get("score"),
                predicted_label=row.get("predicted_label"),
                agent_done=result.get("agent_done"),
                agent_success=result.get("agent_success"),
                wall_clock_seconds=result.get("wall_clock_seconds"),
                steps=metrics.get("steps"),
                primary_code="M3.3",
                recommendation=recommendation,
                category=category,
                signal_counts=counts,
                evidence=evidence,
                taxonomy_reasoning=taxonomy_reasoning,
                final_answer_excerpt=final_answer.replace("\n", " ")[:500],
            )
        )
    return out


def write_outputs(rows: list[AuditRow], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "m3_3_api_log_failure_scan.json"
    csv_path = out_dir / "m3_3_api_log_failure_scan.csv"
    md_path = out_dir / "m3_3_api_log_failure_scan_summary.md"

    payload = {
        "rule_notes": {
            "guarantee": "High-recall string evidence scan only. It guarantees captured patterns appeared in api_logs/result text; it does not guarantee semantic rerun correctness.",
            "rerun_candidate": "Includes explicit access/render/session errors and the former manual_review cases with mixed or unclear M3.3 evidence.",
            "manual_review": "Deprecated for rerun selection. Former manual_review cases are now emitted as rerun_candidate for higher recall.",
            "keep_m3_3": "No hard browser/access evidence and content/site capability limitation is present.",
        },
        "summary": summarize(rows),
        "rows": [serialize_row(row) for row in rows],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "agent",
                "timestamp",
                "task_id",
                "score",
                "agent_done",
                "wall_clock_seconds",
                "steps",
                "recommendation",
                "category",
                "navigation_failed",
                "err_tunnel",
                "err_timed_out",
                "current_tab_about_blank",
                "action_about_blank",
                "empty_dom",
                "detached_focus",
                "browser_event_timeout",
                "llm_timeout",
                "parse_error",
                "bot_signal",
                "content_limitation_text",
                "evidence",
            ],
        )
        writer.writeheader()
        for row in rows:
            data = serialize_row(row)
            flat = {
                **{k: data[k] for k in ["agent", "timestamp", "task_id", "score", "agent_done", "wall_clock_seconds", "steps", "recommendation", "category"]},
                **data["signal_counts"],
                "evidence": " | ".join(data["evidence"][:4]),
            }
            writer.writerow(flat)

    md_path.write_text(render_markdown(rows, payload["summary"]))
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "md": str(md_path)}, ensure_ascii=False, indent=2))


def serialize_row(row: AuditRow) -> dict[str, Any]:
    data = asdict(row)
    data["signal_counts"] = asdict(row.signal_counts)
    return data


def summarize(rows: list[AuditRow]) -> dict[str, Any]:
    by_agent: dict[str, Any] = {}
    for agent in sorted({row.agent for row in rows}):
        agent_rows = [row for row in rows if row.agent == agent]
        by_agent[agent] = {
            "total_m3_3": len(agent_rows),
            "recommendation_counts": dict(Counter(row.recommendation for row in agent_rows)),
            "category_counts": dict(Counter(row.category for row in agent_rows)),
            "rerun_candidate_task_ids": [row.task_id for row in agent_rows if row.recommendation == "rerun_candidate"],
            "manual_review_task_ids": [row.task_id for row in agent_rows if row.recommendation == "manual_review"],
            "keep_m3_3_task_ids": [row.task_id for row in agent_rows if row.recommendation == "keep_m3_3"],
        }
    return {
        "total_m3_3": len(rows),
        "overall_recommendation_counts": dict(Counter(row.recommendation for row in rows)),
        "overall_category_counts": dict(Counter(row.category for row in rows)),
        "by_agent": by_agent,
    }


def render_markdown(rows: list[AuditRow], summary: dict[str, Any]) -> str:
    lines = [
        "# M3.3 API Log Failure Scan",
        "",
        "This scan uses fixed string/threshold rules over `api_logs/step_*.json` and `result.json`.",
        "It is high recall for rerun candidates, but it is not a 100% semantic classifier.",
        "",
        "## Overall",
        "",
        f"- Total M3.3 rows scanned: {summary['total_m3_3']}",
        f"- Recommendation counts: `{json.dumps(summary['overall_recommendation_counts'], ensure_ascii=False)}`",
        f"- Category counts: `{json.dumps(summary['overall_category_counts'], ensure_ascii=False)}`",
        "",
        "## By Agent",
        "",
        "| Agent | M3.3 | Rerun | Manual | Keep | Rerun task ids |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for agent, info in summary["by_agent"].items():
        rec = info["recommendation_counts"]
        lines.append(
            "| {agent} | {total} | {rerun} | {manual} | {keep} | {ids} |".format(
                agent=agent,
                total=info["total_m3_3"],
                rerun=rec.get("rerun_candidate", 0),
                manual=rec.get("manual_review", 0),
                keep=rec.get("keep_m3_3", 0),
                ids=", ".join(info["rerun_candidate_task_ids"]),
            )
        )
    lines.extend(["", "## Rerun Candidates", ""])
    for row in rows:
        if row.recommendation != "rerun_candidate":
            continue
        evidence = " / ".join(row.evidence[:2])
        lines.append(f"- `{row.agent}` task `{row.task_id}`: {row.category}. Evidence: {evidence}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(
            "/Users/abc/Desktop/lexmount/browseruse-agent-bench/experiments/LexBench-Browser/All/browser-use"
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out_dir or args.root / "failure_taxonomy_review"
    all_rows: list[AuditRow] = []
    for agent, timestamp, run_dir in discover_runs(args.root):
        all_rows.extend(audit_run(agent, timestamp, run_dir))
    write_outputs(all_rows, out_dir)


if __name__ == "__main__":
    main()
