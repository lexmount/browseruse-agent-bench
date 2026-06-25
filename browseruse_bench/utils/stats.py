"""Statistical utility functions."""
from __future__ import annotations

from math import fsum
from typing import Any, Dict, List, Optional


def _calc_stats(values: List[float]) -> Dict[str, float]:
    """Calculate statistics for a list of values.

    Args:
        values: List of values.

    Returns:
        Dict[str, float]: Dictionary containing count, mean, min, max, median.
    """
    if not values:
        return {"count": 0, "mean": 0, "min": 0, "max": 0, "median": 0}
    s = sorted(values)
    return {
        "count": len(s),
        "mean": round(sum(s) / len(s), 2),
        "min": min(s),
        "max": max(s),
        "median": round(s[len(s) // 2], 2)
    }


def _get_agent_metrics(task: Dict[str, Any], path: str = "evaluation_details") -> Optional[Dict[str, Any]]:
    """Extract agent_metrics from a task dict.

    Supports two storage layouts:
      1) New schema: evaluation_details.agent_metrics.{metric}
      2) Legacy flat: evaluation_details.{metric}
    """
    container = task.get(path)
    if not isinstance(container, dict):
        return None

    # New schema: nested agent_metrics
    agent_metrics = container.get("agent_metrics")
    if isinstance(agent_metrics, dict):
        return agent_metrics

    # Legacy fallback: metrics were flat-merged into evaluation_details
    return container


def calculate_metric_stats(tasks: List[Dict[str, Any]], metric: str, path: str = "evaluation_details") -> Dict[str, float]:
    """Calculate statistics for a specific metric.

    Args:
        tasks: List of task results.
        metric: Metric name (e.g., "ttft_ms", "end_to_end_ms", "steps").
        path: Path to metric in task dictionary, default is "evaluation_details".

    Returns:
        Dict[str, float]: Statistics dictionary for the metric.
    """
    vals = []
    for t in tasks:
        am = _get_agent_metrics(t, path)
        if am is not None and metric in am and isinstance(am[metric], (int, float)):
            vals.append(float(am[metric]))
    return _calc_stats(vals)


def calculate_usage_stats(tasks: List[Dict[str, Any]], path: str = "evaluation_details") -> Dict[str, Dict[str, float]]:
    """Calculate usage statistics.

    Args:
        tasks: List of task results.
        path: Path to metric in task dictionary, default is "evaluation_details".

    Returns:
        Dict[str, Dict[str, float]]: Usage statistics for each field.
    """
    # Define usage fields to aggregate (exclude by_model)
    usage_fields = [
        "total_prompt_tokens",
        "total_prompt_cost",
        "total_prompt_cached_tokens",
        "total_prompt_cached_cost",
        "total_completion_tokens",
        "total_completion_cost",
        "total_tokens",
        "total_cost",
        "entry_count"
    ]

    usage_stats: Dict[str, Dict[str, float]] = {}
    for field in usage_fields:
        vals: List[float] = []
        for t in tasks:
            am = _get_agent_metrics(t, path)
            if am is not None:
                usage = am.get("usage")
                if isinstance(usage, dict) and field in usage and isinstance(usage[field], (int, float)):
                    vals.append(float(usage[field]))

        if vals:
            field_stats = _calc_stats(vals)
            field_stats["sum"] = fsum(vals)
            usage_stats[field] = field_stats

    return usage_stats


def calculate_all_metrics_stats(
    tasks: List[Dict[str, Any]],
    metrics: Optional[List[str]] = None,
    path: str = "evaluation_details",
) -> Dict[str, Dict[str, float]]:
    """Calculate statistics for multiple metrics.

    Args:
        tasks: List of task results.
        metrics: List of metric names, default is ["ttft_ms", "end_to_end_ms", "steps"].
        path: Path to metric in task dictionary, default is "evaluation_details".

    Returns:
        Dict[str, Dict[str, float]]: Statistics for each metric, including basic metrics and usage.
    """
    if metrics is None:
        metrics = ["ttft_ms", "end_to_end_ms", "steps"]

    # Calculate basic metrics
    stats = {m: calculate_metric_stats(tasks, m, path) for m in metrics}

    # Calculate usage statistics
    usage_stats = calculate_usage_stats(tasks, path)
    if usage_stats:
        stats["usage"] = usage_stats

    return stats

def filter_tasks_by_label(
    tasks: List[Dict[str, Any]],
    key: str = "predicted_label",
    val: int = 1,
) -> List[Dict[str, Any]]:
    """Filter tasks by label.

    Args:
        tasks: List of task results.
        key: Label key name, default is "predicted_label".
        val: Label value, default is 1 (success).

    Returns:
        List[Dict[str, Any]]: Filtered task list.
    """
    return [t for t in tasks if t.get(key) == val]


def calculate_failure_category_stats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate failure category statistics for failed tasks.

    Compatible with two storage structures:
      1) Top-level field: result["failure_category"]
      2) Nested in evaluation_details: result["evaluation_details"]["failure_category"]

    Args:
        results: List of evaluation results.

    Returns:
        Dict[str, Any]: Dictionary containing failure category statistics:
        {
            "total_failed_tasks": int,
            "by_category": {
                "A1": {"count": int, "rate": float},
                ...
            }
        }
    """
    failed_tasks = filter_tasks_by_label(results, "predicted_label", 0)
    total_failed = len(failed_tasks)

    category_counts: Dict[str, int] = {}
    for r in failed_tasks:
        category: Optional[str] = None
        if "failure_category" in r and isinstance(r["failure_category"], str):
            category = r["failure_category"]
        elif isinstance(r.get("evaluation_details"), dict):
            fc = r["evaluation_details"].get("failure_category")
            if isinstance(fc, str):
                category = fc

        if category:
            category_counts[category] = category_counts.get(category, 0) + 1

    by_category: Dict[str, Dict[str, float]] = {}
    for cat, count in category_counts.items():
        rate = round(count / total_failed * 100, 2) if total_failed > 0 else 0.0
        by_category[cat] = {"count": count, "rate": rate}

    return {"total_failed_tasks": total_failed, "by_category": by_category}


# NOTE: generate_evaluation_summary now lives in browseruse_bench.eval.summary;
# re-export happens in browseruse_bench.utils.__init__ to avoid circular import.
