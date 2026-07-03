"""Standalone failure-attribution pass over existing eval results."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from browseruse_bench.cli.eval import refresh_summary_failure_stats, run_failure_classification
from browseruse_bench.utils import REPO_ROOT, get_env_var

logger = logging.getLogger(__name__)


def locate_results_file(experiments_root: Path, timestamp: str | None) -> Path:
    """Find the eval results JSONL under experiments_root/{timestamp}/tasks_eval_result."""
    if timestamp:
        pattern_root = experiments_root / timestamp / "tasks_eval_result"
        candidates = sorted(pattern_root.glob("*_eval_results.json"))
    else:
        candidates = sorted(experiments_root.glob("*/tasks_eval_result/*_eval_results.json"))
    if not candidates:
        raise SystemExit(
            f"[FAILED] No eval results found under {experiments_root}"
            f" (timestamp={timestamp or 'latest'})"
        )
    return candidates[-1]


def clear_failure_labels(results_file: Path) -> int:
    """Reset failure labels on failed records so a --force pass relabels them."""
    records: list[dict[str, Any]] = []
    cleared = 0
    with open(results_file, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("predicted_label") == 0 and rec.get("failure_category"):
                rec["failure_category"] = None
                details = rec.get("evaluation_details")
                if isinstance(details, dict):
                    details.pop("failure_classification", None)
                cleared += 1
            records.append(rec)
    with open(results_file, "w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return cleared


def configure_attribute_parser(parser: argparse.ArgumentParser, config: dict[str, Any]) -> None:
    """Configure arguments for the attribute command."""
    default = config.get("default", {})
    parser.add_argument("--agent", default=default.get("agent"))
    parser.add_argument("--data", default=default.get("data", "LexBench-Browser"))
    parser.add_argument("--split", default="All", help="Dataset split directory (default: All)")
    parser.add_argument("--model-id", required=False, default=None,
                        help="Model id directory under the agent")
    parser.add_argument("--timestamp", default=None,
                        help="Timestamp directory to attribute (default: latest)")
    parser.add_argument("--force", action="store_true",
                        help="Re-label failures that already have a category")
    parser.add_argument("--num-worker", type=int, default=4)
    parser.add_argument("--model", default=None,
                        help="Judge model override (default: config.yaml eval.model)")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-url", default=None)


def attribute_command(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Label failure causes on an existing eval results file."""
    if not args.agent or not args.model_id:
        raise SystemExit(
            "[FAILED] --agent and --model-id are required"
            " (agent may come from config.yaml default.agent)"
        )
    experiments_root = (
        REPO_ROOT / "experiments" / args.data / args.split / args.agent / args.model_id
    )
    results_file = locate_results_file(experiments_root, args.timestamp)
    logger.info("Attributing failures in %s", results_file)
    if args.force:
        cleared = clear_failure_labels(results_file)
        logger.info("Cleared %s existing labels for re-attribution", cleared)

    eval_cfg = config.get("eval", {})
    exit_code = run_failure_classification(
        results_file,
        results_file.parent.parent / "tasks",
        args.model or eval_cfg.get("model") or "gpt-4.1",
        args.api_key or eval_cfg.get("api_key") or get_env_var("OPENAI_API_KEY", ""),
        args.base_url or eval_cfg.get("base_url") or "",
        skip_existing=not args.force,
        num_workers=args.num_worker,
        temperature=eval_cfg.get("temperature"),
    )
    if exit_code == 0:
        refresh_summary_failure_stats(results_file)
    return exit_code
