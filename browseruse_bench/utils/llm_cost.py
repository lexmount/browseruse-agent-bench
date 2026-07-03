from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from browseruse_bench.utils import REPO_ROOT
from browseruse_bench.utils.parse_utils import safe_int

logger = logging.getLogger(__name__)

LITELLM_PRICE_TABLE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
DEFAULT_LITELLM_PRICE_TABLE_URL = LITELLM_PRICE_TABLE_URL
DEFAULT_BROWSERUSE_BENCH_TOKEN_COST_CACHE_DIR = Path.home() / ".cache" / "browseruse_bench" / "token_cost"
DEFAULT_CUSTOM_MODEL_PRICING_FILE = REPO_ROOT / "configs" / "pricing" / "model_pricing.yaml"
DEFAULT_PRICE_CACHE_TTL_SECONDS = 86400
DEFAULT_HTTP_TIMEOUT_SECONDS = 10

_PRICE_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}


def _as_mapping_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _normalize_price_table(price_table: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key.strip().lower(): value
        for key, value in price_table.items()
        if isinstance(key, str) and isinstance(value, dict)
    }


def _model_label_and_key(model_name: str | None) -> tuple[str, str]:
    if not isinstance(model_name, str):
        return "unknown", "unknown"
    label = model_name.strip() or "unknown"
    return label, label.lower()


def _extract_usage_totals(usage: Mapping[str, Any]) -> dict[str, int] | None:
    prompt_tokens = safe_int(usage.get("total_prompt_tokens", usage.get("prompt_tokens", 0)))
    completion_tokens = safe_int(usage.get("total_completion_tokens", usage.get("completion_tokens", 0)))
    total_tokens = safe_int(usage.get("total_tokens"), prompt_tokens + completion_tokens)

    cached_tokens = safe_int(usage.get("total_prompt_cached_tokens", usage.get("cached_tokens", 0)))
    if cached_tokens == 0:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached_tokens = safe_int(details.get("cached_tokens", 0))

    cache_creation_tokens = safe_int(
        usage.get("total_prompt_cache_creation_tokens", usage.get("cache_creation_input_tokens", 0))
    )

    if prompt_tokens == 0 and completion_tokens == 0 and total_tokens > 0:
        prompt_tokens = total_tokens
    if prompt_tokens == 0 and completion_tokens == 0 and total_tokens == 0:
        return None

    return {
        "prompt_tokens": max(0, prompt_tokens),
        "completion_tokens": max(0, completion_tokens),
        "total_tokens": max(0, total_tokens),
        "cached_tokens": max(0, cached_tokens),
        "cache_creation_tokens": max(0, cache_creation_tokens),
        "entry_count": max(1, safe_int(usage.get("entry_count", 1), 1)),
    }


def _read_rate(entry: Mapping[str, Any], per_token_key: str, per_million_key: str | None = None) -> float | None:
    per_token_rate = _as_float(entry.get(per_token_key))
    if per_token_rate is not None:
        return per_token_rate
    if per_million_key is None:
        return None
    per_million_rate = _as_float(entry.get(per_million_key))
    if per_million_rate is None:
        return None
    return per_million_rate / 1_000_000


def _extract_rates(
    entry: Mapping[str, Any],
    *,
    allow_per_million: bool,
) -> tuple[float | None, float | None, float | None, float | None]:
    if allow_per_million:
        return (
            _read_rate(entry, "input_cost_per_token", "input_cost_per_million_tokens"),
            _read_rate(entry, "output_cost_per_token", "output_cost_per_million_tokens"),
            _read_rate(entry, "cache_read_input_token_cost", "cache_read_input_cost_per_million_tokens"),
            _read_rate(entry, "cache_creation_input_token_cost", "cache_creation_input_cost_per_million_tokens"),
        )

    return (
        _read_rate(entry, "input_cost_per_token"),
        _read_rate(entry, "output_cost_per_token"),
        _read_rate(entry, "cache_read_input_token_cost"),
        _read_rate(entry, "cache_creation_input_token_cost"),
    )


def _read_browseruse_bench_cached_price_table() -> tuple[float | None, dict[str, dict[str, Any]] | None]:
    cache_dir = DEFAULT_BROWSERUSE_BENCH_TOKEN_COST_CACHE_DIR
    if not cache_dir.is_dir():
        return None, None

    now = time.time()
    cache_files = sorted(cache_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for cache_file in cache_files:
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        payload_dict = _as_mapping_dict(payload)
        if payload_dict is None:
            continue

        timestamp_raw = payload_dict.get("timestamp")
        raw_table = payload_dict.get("data")
        if not isinstance(timestamp_raw, str) or not isinstance(raw_table, dict):
            continue

        timestamp_text = timestamp_raw.strip()
        if timestamp_text.endswith("Z"):
            timestamp_text = f"{timestamp_text[:-1]}+00:00"
        try:
            parsed_timestamp = datetime.fromisoformat(timestamp_text)
        except ValueError:
            continue
        if parsed_timestamp.tzinfo is None:
            parsed_timestamp = parsed_timestamp.replace(tzinfo=UTC)

        expires_at = parsed_timestamp.timestamp() + DEFAULT_PRICE_CACHE_TTL_SECONDS
        if now >= expires_at:
            continue

        normalized_table = _normalize_price_table(raw_table)
        if normalized_table:
            return expires_at, normalized_table

    return None, None


def _write_browseruse_bench_cached_price_table(price_table: Mapping[str, dict[str, Any]]) -> None:
    cache_dir = DEFAULT_BROWSERUSE_BENCH_TOKEN_COST_CACHE_DIR
    payload = {"timestamp": datetime.now(UTC).isoformat(), "data": dict(price_table)}
    file_name = f"pricing_bubench_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    cache_file = cache_dir / file_name

    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Failed to write browseruse-bench cache %s: %s", cache_file, exc)


def _load_custom_model_pricing_table() -> dict[str, dict[str, Any]]:
    if not DEFAULT_CUSTOM_MODEL_PRICING_FILE.exists():
        return {}

    try:
        payload = yaml.safe_load(DEFAULT_CUSTOM_MODEL_PRICING_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        logger.warning("Failed to load custom model pricing file %s: %s", DEFAULT_CUSTOM_MODEL_PRICING_FILE, exc)
        return {}

    payload_dict = _as_mapping_dict(payload)
    if payload_dict is None:
        return {}

    price_table: dict[str, dict[str, Any]] = {}
    for model_key, raw_entry in payload_dict.items():
        if not isinstance(model_key, str) or not isinstance(raw_entry, dict):
            continue

        input_rate, output_rate, cached_rate, creation_rate = _extract_rates(raw_entry, allow_per_million=True)
        normalized_entry = {
            "input_cost_per_token": input_rate,
            "output_cost_per_token": output_rate,
            "cache_read_input_token_cost": cached_rate,
            "cache_creation_input_token_cost": creation_rate,
        }
        normalized_entry = {key: value for key, value in normalized_entry.items() if value is not None}
        price_table[model_key] = normalized_entry

    return _normalize_price_table(price_table)


def load_litellm_price_table() -> dict[str, dict[str, Any]]:
    now = time.time()
    url = DEFAULT_LITELLM_PRICE_TABLE_URL
    cache_hit = _PRICE_CACHE.get(url)
    if cache_hit and now < cache_hit[0]:
        return cache_hit[1]

    if url == LITELLM_PRICE_TABLE_URL:
        expires_at, cached_table = _read_browseruse_bench_cached_price_table()
        if cached_table is not None and expires_at is not None:
            _PRICE_CACHE[url] = (expires_at, cached_table)
            return cached_table

    try:
        with urllib.request.urlopen(url, timeout=DEFAULT_HTTP_TIMEOUT_SECONDS) as response:
            raw_payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load LiteLLM price table from %s: %s", url, exc)
        return {}

    if not isinstance(raw_payload, dict):
        logger.warning("Unexpected LiteLLM price table format from %s", url)
        return {}

    normalized_table = _normalize_price_table(raw_payload)
    _PRICE_CACHE[url] = (now + DEFAULT_PRICE_CACHE_TTL_SECONDS, normalized_table)
    if url == LITELLM_PRICE_TABLE_URL:
        _write_browseruse_bench_cached_price_table(normalized_table)
    return normalized_table


def has_complete_usage_cost_fields(usage: Mapping[str, Any]) -> bool:
    required_keys = (
        "total_prompt_tokens",
        "total_prompt_cost",
        "total_completion_tokens",
        "total_completion_cost",
        "total_tokens",
        "total_cost",
    )
    return all(isinstance(usage.get(key), (int, float)) for key in required_keys)


def _build_usage_summary(
    usage_totals: Mapping[str, int],
    model_name: str | None,
    litellm_table: Mapping[str, dict[str, Any]],
    custom_table: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    model_label, model_key = _model_label_and_key(model_name)

    prompt_tokens = safe_int(usage_totals.get("prompt_tokens", 0))
    completion_tokens = safe_int(usage_totals.get("completion_tokens", 0))
    total_tokens = safe_int(usage_totals.get("total_tokens", 0), prompt_tokens + completion_tokens)
    # Convention: prompt_tokens INCLUDES cache reads and cache-creation tokens
    # (OpenAI-style); both are disjoint subsets billed at their own rates.
    cached_tokens = min(max(0, safe_int(usage_totals.get("cached_tokens", 0))), max(0, prompt_tokens))
    cache_creation_tokens = min(
        max(0, safe_int(usage_totals.get("cache_creation_tokens", 0))),
        max(0, prompt_tokens - cached_tokens),
    )
    entry_count = max(1, safe_int(usage_totals.get("entry_count", 1), 1))

    pricing = custom_table.get(model_key) or litellm_table.get(model_key) or {}
    input_rate, output_rate, cached_rate, creation_rate = _extract_rates(pricing, allow_per_million=False)
    input_rate = input_rate or 0.0
    output_rate = output_rate or 0.0
    if cached_rate is None:
        cached_rate = input_rate
    if creation_rate is None:
        creation_rate = input_rate

    if input_rate == 0.0 and output_rate == 0.0:
        logger.warning("Missing pricing for model '%s'. Cost set to 0.", model_label)

    non_cached_prompt_tokens = max(0, prompt_tokens - cached_tokens - cache_creation_tokens)
    prompt_non_cached_cost = non_cached_prompt_tokens * input_rate
    prompt_cached_cost = cached_tokens * cached_rate
    prompt_cache_creation_cost = cache_creation_tokens * creation_rate
    prompt_cost = prompt_non_cached_cost + prompt_cached_cost + prompt_cache_creation_cost
    completion_cost = completion_tokens * output_rate
    total_cost = prompt_cost + completion_cost
    resolved_total_tokens = total_tokens if total_tokens > 0 else prompt_tokens + completion_tokens

    return {
        "total_prompt_tokens": prompt_tokens,
        "total_prompt_cost": prompt_cost,
        "total_prompt_cached_tokens": cached_tokens,
        "total_prompt_cached_cost": prompt_cached_cost,
        "total_prompt_cache_creation_tokens": cache_creation_tokens,
        "total_prompt_cache_creation_cost": prompt_cache_creation_cost,
        "total_completion_tokens": completion_tokens,
        "total_completion_cost": completion_cost,
        "total_tokens": resolved_total_tokens,
        "total_cost": total_cost,
        "entry_count": entry_count,
        "by_model": {
            model_label: {
                "model": model_label,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": resolved_total_tokens,
                "cost": total_cost,
                "invocations": entry_count,
                "average_tokens_per_invocation": resolved_total_tokens / entry_count,
            }
        },
    }


def enrich_usage_with_litellm_pricing(
    usage: Any,
    model_name: str | None,
    price_table: Mapping[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    usage_dict = _as_mapping_dict(usage)
    if usage_dict is None:
        return {}

    if has_complete_usage_cost_fields(usage_dict) and not force:
        return usage_dict

    usage_totals = _extract_usage_totals(usage_dict)
    if usage_totals is None:
        return usage_dict

    litellm_table = load_litellm_price_table() if price_table is None else _normalize_price_table(price_table)
    custom_table = _load_custom_model_pricing_table()
    return _build_usage_summary(usage_totals, model_name, litellm_table, custom_table)


def enrich_result_usage_cost_if_needed(
    result: Mapping[str, Any],
    model_name: str | None = None,
) -> dict[str, Any]:
    result_dict = dict(result)
    metrics = _as_mapping_dict(result_dict.get("metrics")) or {}
    usage_dict = _as_mapping_dict(metrics.get("usage"))
    if usage_dict is None:
        return result_dict
    has_complete_cost = has_complete_usage_cost_fields(usage_dict)
    total_cost = _as_float(usage_dict.get("total_cost"))
    if has_complete_cost and total_cost is not None and total_cost > 0:
        return result_dict

    raw_model_name = model_name if model_name is not None else result_dict.get("model_id")
    resolved_model_name = raw_model_name if isinstance(raw_model_name, str) else None

    updated_metrics = dict(metrics)
    updated_metrics["usage"] = enrich_usage_with_litellm_pricing(
        usage=usage_dict,
        model_name=resolved_model_name,
        force=has_complete_cost and ((total_cost is None) or (total_cost <= 0)),
    )
    result_dict["metrics"] = updated_metrics
    return result_dict


__all__ = [
    "load_litellm_price_table",
    "has_complete_usage_cost_fields",
    "enrich_usage_with_litellm_pricing",
    "enrich_result_usage_cost_if_needed",
]
