"""Standalone failure-attribution pass over existing eval results."""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Any

from browseruse_bench.cli.eval import (
    refresh_summary_failure_stats,
    resolve_judge_settings,
    run_failure_classification,
)
from browseruse_bench.utils import (
    REPO_ROOT,
    normalize_agent_name,
    normalize_benchmark_name,
    resolve_dir_name_case_insensitive,
)

logger = logging.getLogger(__name__)

_TIMESTAMP_DIR_RE = re.compile(r"^\d{8}_\d{6}$")


def locate_results_file(experiments_root: Path, timestamp: str | None) -> Path:
    """Find the eval results JSONL under experiments_root/{timestamp}/tasks_eval_result.

    Covers every evaluator naming scheme (all end in ``_results.json``). When a
    directory holds several results files, the most recently written one wins.
    """
    if timestamp:
        run_dirs = [experiments_root / timestamp]
    else:
        run_dirs = sorted(
            d for d in experiments_root.glob("*") if _TIMESTAMP_DIR_RE.match(d.name)
        )
    candidates: list[Path] = []
    for run_dir in reversed(run_dirs):
        candidates = list((run_dir / "tasks_eval_result").glob("*_results.json"))
        if candidates:
            break
    if not candidates:
        raise SystemExit(
            f"[FAILED] No eval results found under {experiments_root}"
            f" (timestamp={timestamp or 'latest'})"
        )
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    if len(candidates) > 1:
        logger.info(
            "Multiple results files found, using most recent: %s", chosen.name
        )
    return chosen


def _resolve_experiments_root(args: argparse.Namespace, config: dict[str, Any]) -> Path:
    """Build the run directory root with the same normalization eval uses."""
    benchmark = normalize_benchmark_name(args.data)
    agent = normalize_agent_name(args.agent, config)
    root = REPO_ROOT / "experiments"
    for part in (benchmark, args.split, agent, args.model_id):
        root = root / resolve_dir_name_case_insensitive(part, root)
    return root


def configure_attribute_parser(parser: argparse.ArgumentParser, config: dict[str, Any]) -> None:
    """Configure arguments for the attribute command."""
    default = config.get("default", {})
    parser.add_argument("--agent", default=default.get("agent"))
    parser.add_argument("--data", default=default.get("data", "LexBench-Browser"))
    parser.add_argument("--split", default="All", help="Dataset split directory (default: All)")
    parser.add_argument("--model-id", required=True,
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
    if not args.agent:
        raise SystemExit("[FAILED] --agent is required (or set default.agent in config.yaml)")
    results_file = locate_results_file(_resolve_experiments_root(args, config), args.timestamp)
    logger.info("Attributing failures in %s", results_file)

    model, api_key, base_url, temperature = resolve_judge_settings(
        config, args.model, args.api_key, args.base_url
    )
    exit_code = run_failure_classification(
        results_file,
        results_file.parent.parent / "tasks",
        model,
        api_key,
        base_url,
        skip_existing=not args.force,
        num_workers=args.num_worker,
        temperature=temperature,
    )
    if exit_code == 0:
        refresh_summary_failure_stats(results_file)
    return exit_code
