"""Cost calculation, JSONL normalization, and summary generation."""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


def calculate_evaluation_cost(
    usage_dict: Any,
    model_name: Optional[str] = None,
    price_table: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Calculate the evaluation-LLM cost (USD) from usage and shared pricing.

    Rates resolve through the same tables as agent-side costs (custom
    ``configs/pricing/model_pricing.yaml`` first, then the LiteLLM table);
    an unknown ``model_name`` yields cost 0 with a warning, matching the
    agent-side convention. ``price_table`` overrides the LiteLLM table
    (mainly for tests).
    """
    if not usage_dict:
        return None

    if not isinstance(usage_dict, dict):
        if hasattr(usage_dict, "__dict__"):
            usage_dict = usage_dict.__dict__
        elif hasattr(usage_dict, "model_dump"):
            usage_dict = usage_dict.model_dump()
        else:
            return None

    # Lazy import: llm_cost pulls in browseruse_bench.utils, whose __init__
    # re-exports from this module (same cycle as generate_evaluation_summary).
    from browseruse_bench.utils.llm_cost import enrich_usage_with_litellm_pricing

    enriched = enrich_usage_with_litellm_pricing(
        usage=usage_dict,
        model_name=model_name,
        price_table=price_table,
        force=True,
    )
    # A zero-token usage dict comes back unchanged from enrichment (nothing to
    # price); require the full enriched shape before indexing it.
    required_keys = (
        "total_prompt_tokens",
        "total_prompt_cost",
        "total_prompt_cached_tokens",
        "total_prompt_cached_cost",
        "total_prompt_cache_creation_tokens",
        "total_prompt_cache_creation_cost",
        "total_completion_tokens",
        "total_completion_cost",
        "total_cost",
    )
    if not all(key in enriched for key in required_keys):
        return None

    prompt_tokens = enriched["total_prompt_tokens"]
    cached_tokens = enriched["total_prompt_cached_tokens"]
    creation_tokens = enriched["total_prompt_cache_creation_tokens"]
    cached_cost = enriched["total_prompt_cached_cost"]
    creation_cost = enriched["total_prompt_cache_creation_cost"]
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": enriched["total_completion_tokens"],
        "cached_tokens": cached_tokens,
        "cache_creation_tokens": creation_tokens,
        "non_cached_prompt": max(0, prompt_tokens - cached_tokens - creation_tokens),
        "costs": {
            "input": enriched["total_prompt_cost"] - cached_cost - creation_cost,
            "output": enriched["total_completion_cost"],
            "cached": cached_cost,
            "cache_creation": creation_cost,
            "total": enriched["total_cost"],
        },
    }


def aggregate_evaluation_costs(
    usage_list: List[Any],
    model_name: Optional[str] = None,
    price_table: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Aggregate USD costs from multiple usage dictionaries."""
    total_prompt = 0
    total_completion = 0
    total_cached = 0
    total_input_cost = 0.0
    total_output_cost = 0.0
    total_cached_cost = 0.0

    total_cache_creation = 0
    total_cache_creation_cost = 0.0
    for usage in usage_list:
        if usage is None:
            continue
        cost_info = calculate_evaluation_cost(usage, model_name=model_name, price_table=price_table)
        if cost_info:
            total_prompt += cost_info["prompt_tokens"]
            total_completion += cost_info["completion_tokens"]
            total_cached += cost_info["cached_tokens"]
            total_cache_creation += cost_info["cache_creation_tokens"]
            total_input_cost += cost_info["costs"]["input"]
            total_output_cost += cost_info["costs"]["output"]
            total_cached_cost += cost_info["costs"]["cached"]
            total_cache_creation_cost += cost_info["costs"]["cache_creation"]

    total_cost = total_input_cost + total_output_cost + total_cached_cost + total_cache_creation_cost

    return {
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_cached_tokens": total_cached,
        "total_cache_creation_tokens": total_cache_creation,
        "total_non_cached_prompt": max(0, total_prompt - total_cached - total_cache_creation),
        "costs": {
            "input": total_input_cost,
            "output": total_output_cost,
            "cached": total_cached_cost,
            "cache_creation": total_cache_creation_cost,
            "total": total_cost,
        },
    }


def _parse_consecutive_json_objects(text: str) -> List[Any]:
    """Parse consecutive JSON objects from text (even without commas/brackets)."""
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    records: List[Any] = []

    while idx < length:
        while idx < length and text[idx].isspace():
            idx += 1
        if idx >= length:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            return []
        records.append(obj)
        idx = end
    return records


def _convert_json_to_jsonl(results_file: Path) -> Optional[Path]:
    """Convert JSON/JSONL/messy multi-line JSON into a temporary JSONL file."""
    try:
        text = results_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("   [WARNING] Failed to read evaluation result file %s: %s", results_file, exc)
        return None

    stripped = text.lstrip()
    if not stripped:
        return None

    parsed: Optional[List[Any]] = None

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            parsed = [data]
        elif isinstance(data, list):
            parsed = data
    except json.JSONDecodeError:
        parsed = _parse_consecutive_json_objects(text)

    if not parsed:
        return None

    temp_path = results_file.with_name(results_file.name + ".jsonl.tmp")
    with open(temp_path, "w", encoding="utf-8") as tmp:
        for item in parsed:
            json.dump(item, tmp, ensure_ascii=False)
            tmp.write("\n")

    return temp_path


def dedupe_records_keep_newest(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse duplicate task_id records, keeping the newest occurrence.

    Results JSONL files are append-only, so a later record for the same
    task_id supersedes an earlier one (e.g. a synthetic placeholder that was
    re-judged on resume). Order of first appearance is preserved; records
    without a string task_id are kept unchanged after the deduped ones.
    """
    by_task_id: Dict[str, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []
    for record in records:
        task_id = record.get("task_id")
        if isinstance(task_id, str):
            by_task_id[task_id] = record
        else:
            extras.append(record)
    return list(by_task_id.values()) + extras


@contextmanager
def normalized_results_file(results_file: Path) -> Iterator[Path]:
    """Yield a path that is guaranteed to be JSONL-like, cleaning up temp files."""
    temp_path = _convert_json_to_jsonl(results_file)
    try:
        yield temp_path or results_file
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError as exc:
                logger.warning(f"   [WARNING] Failed to delete temp file {temp_path}: {exc}")


def generate_evaluation_summary(
    results: List[Dict[str, Any]],
    total: int,
    metrics: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate evaluation summary.

    Args:
        results: List of evaluation results.
        total: Total number of tasks.
        metrics: List of metrics to calculate, default is ["ttft_ms", "end_to_end_ms", "steps"].

    Returns:
        Dict[str, Any]: Summary dictionary containing overall stats, metric stats, and task list.
    """
    # Lazy import: importing browseruse_bench.utils.stats triggers utils/__init__.py
    # which itself re-exports from this module, so we defer to call time.
    from browseruse_bench.utils.stats import (
        calculate_all_metrics_stats,
        calculate_failure_category_stats,
        filter_tasks_by_label,
    )

    if metrics is None:
        metrics = ["ttft_ms", "end_to_end_ms", "steps"]
    success = filter_tasks_by_label(results, "predicted_label", 1)
    failed = filter_tasks_by_label(results, "predicted_label", 0)
    n = len(results)

    return {
        "overall_statistics": {
            "total_tasks": total,
            "evaluated_tasks": n,
            "successful_tasks": len(success),
            "failed_tasks": len(failed),
            "success_rate": round(len(success) / n * 100, 2) if n > 0 else 0,
            "failure_rate": round(len(failed) / n * 100, 2) if n > 0 else 0,
        },
        "metrics_statistics": calculate_all_metrics_stats(results, metrics),
        "successful_tasks_metrics": calculate_all_metrics_stats(success, metrics),
        "failed_tasks_metrics": calculate_all_metrics_stats(failed, metrics),
        "failure_category_statistics": calculate_failure_category_stats(results),
        "task_list": {
            "successful_task_ids": [r.get("task_id") for r in success if "task_id" in r],
            "failed_task_ids": [r.get("task_id") for r in failed if "task_id" in r],
        },
    }
