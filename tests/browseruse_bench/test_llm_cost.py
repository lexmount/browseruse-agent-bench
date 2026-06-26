"""Tests for LiteLLM-based usage cost utilities."""

from __future__ import annotations

import json
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.utils import llm_cost
from browseruse_bench.utils.llm_cost import (
    enrich_result_usage_cost_if_needed,
    enrich_usage_with_litellm_pricing,
    load_litellm_price_table,
)

TEST_PRICE_TABLE = {
    "gpt-4.1": {
        "input_cost_per_token": 2e-06,
        "output_cost_per_token": 8e-06,
        "cache_read_input_token_cost": 5e-07,
    }
}


@pytest.fixture(autouse=True)
def _clear_price_cache() -> None:
    llm_cost._PRICE_CACHE.clear()


class _FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb

    def read(self) -> bytes:
        return self._payload


def test_enrich_usage_with_token_records() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
        "entry_count": 2,
    }

    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name="gpt-4.1",
        price_table=TEST_PRICE_TABLE,
    )

    assert enriched["total_prompt_tokens"] == 1000
    assert enriched["total_completion_tokens"] == 200
    assert enriched["total_tokens"] == 1200
    assert enriched["entry_count"] == 2
    assert enriched["total_prompt_cost"] == pytest.approx(0.002)
    assert enriched["total_completion_cost"] == pytest.approx(0.0016)
    assert enriched["total_cost"] == pytest.approx(0.0036)
    assert enriched["by_model"]["gpt-4.1"]["invocations"] == 2
    assert enriched["by_model"]["gpt-4.1"]["average_tokens_per_invocation"] == pytest.approx(600.0)


def test_enrich_usage_with_cached_tokens() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
        "prompt_tokens_details": {"cached_tokens": 400},
    }

    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name="gpt-4.1",
        price_table=TEST_PRICE_TABLE,
    )

    assert enriched["total_prompt_tokens"] == 1000
    assert enriched["total_prompt_cached_tokens"] == 400
    assert enriched["total_completion_tokens"] == 100
    assert enriched["total_prompt_cost"] == pytest.approx(0.0014)
    assert enriched["total_prompt_cached_cost"] == pytest.approx(0.0002)
    assert enriched["total_completion_cost"] == pytest.approx(0.0008)
    assert enriched["total_cost"] == pytest.approx(0.0022)


def test_enrich_usage_keeps_complete_usage_without_force() -> None:
    usage = {
        "total_prompt_tokens": 50,
        "total_prompt_cost": 1.5,
        "total_prompt_cached_tokens": 0,
        "total_prompt_cached_cost": 0.0,
        "total_completion_tokens": 20,
        "total_completion_cost": 0.5,
        "total_tokens": 70,
        "total_cost": 2.0,
        "entry_count": 1,
        "by_model": {
            "gpt-4.1": {
                "model": "gpt-4.1",
                "prompt_tokens": 50,
                "completion_tokens": 20,
                "total_tokens": 70,
                "cost": 2.0,
                "invocations": 1,
                "average_tokens_per_invocation": 70.0,
            }
        },
    }

    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name="gpt-4.1",
        price_table=TEST_PRICE_TABLE,
    )

    assert enriched == usage


def test_enrich_usage_uses_strict_model_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm_cost,
        "DEFAULT_CUSTOM_MODEL_PRICING_FILE",
        tmp_path / "missing_custom_model_pricing.yaml",
    )
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1200,
    }

    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name="openai/gpt-4.1",
        price_table=TEST_PRICE_TABLE,
    )

    assert enriched["total_prompt_tokens"] == 1000
    assert enriched["total_completion_tokens"] == 200
    assert enriched["total_tokens"] == 1200
    assert enriched["total_prompt_cost"] == 0.0
    assert enriched["total_completion_cost"] == 0.0
    assert enriched["total_cost"] == 0.0
    assert "openai/gpt-4.1" in enriched["by_model"]


def test_enrich_usage_falls_back_to_custom_pricing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_price_file = tmp_path / "model_pricing.yaml"
    custom_price_file.write_text(
        "\n".join(
            [
                "doubao-seed-2-0-pro-260215:",
                "  input_cost_per_million_tokens: 2.0",
                "  output_cost_per_million_tokens: 4.0",
                "  cache_read_input_cost_per_million_tokens: 1.0",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_cost, "DEFAULT_CUSTOM_MODEL_PRICING_FILE", custom_price_file)

    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
    }
    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name="doubao-seed-2-0-pro-260215",
        price_table={},
    )

    assert enriched["total_prompt_tokens"] == 1000
    assert enriched["total_completion_tokens"] == 100
    assert enriched["total_tokens"] == 1100
    assert enriched["total_prompt_cost"] == pytest.approx(0.002)
    assert enriched["total_completion_cost"] == pytest.approx(0.0004)
    assert enriched["total_cost"] == pytest.approx(0.0024)


def test_enrich_usage_custom_pricing_prefers_per_token_when_both_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_price_file = tmp_path / "model_pricing.yaml"
    custom_price_file.write_text(
        "\n".join(
            [
                "doubao-seed-2-0-pro-260215:",
                "  input_cost_per_token: 0.000003",
                "  output_cost_per_token: 0.000005",
                "  cache_read_input_token_cost: 0.000002",
                "  input_cost_per_million_tokens: 1.0",
                "  output_cost_per_million_tokens: 1.0",
                "  cache_read_input_cost_per_million_tokens: 1.0",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_cost, "DEFAULT_CUSTOM_MODEL_PRICING_FILE", custom_price_file)

    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
    }
    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name="doubao-seed-2-0-pro-260215",
        price_table={},
    )

    assert enriched["total_prompt_cost"] == pytest.approx(0.003)
    assert enriched["total_completion_cost"] == pytest.approx(0.0005)
    assert enriched["total_cost"] == pytest.approx(0.0035)


def test_enrich_usage_prefers_custom_over_litellm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_price_file = tmp_path / "model_pricing.yaml"
    custom_price_file.write_text(
        "\n".join(
            [
                "gpt-4.1:",
                "  input_cost_per_million_tokens: 10.0",
                "  output_cost_per_million_tokens: 20.0",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_cost, "DEFAULT_CUSTOM_MODEL_PRICING_FILE", custom_price_file)

    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
    }
    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name="gpt-4.1",
        price_table=TEST_PRICE_TABLE,
    )

    assert enriched["total_prompt_cost"] == pytest.approx(0.01)
    assert enriched["total_completion_cost"] == pytest.approx(0.002)
    assert enriched["total_cost"] == pytest.approx(0.012)


def test_enrich_result_usage_cost_if_needed_fills_missing_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_cost, "load_litellm_price_table", lambda: TEST_PRICE_TABLE)
    result = {
        "model_id": "gpt-4.1",
        "metrics": {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
            }
        },
    }

    enriched_result = enrich_result_usage_cost_if_needed(result)

    usage = enriched_result["metrics"]["usage"]
    assert usage["total_prompt_tokens"] == 1000
    assert usage["total_completion_tokens"] == 100
    assert usage["total_tokens"] == 1100
    assert usage["total_cost"] == pytest.approx(0.0028)


def test_enrich_result_usage_cost_if_needed_keeps_positive_complete_cost() -> None:
    result = {
        "model_id": "gpt-4.1",
        "metrics": {
            "usage": {
                "total_prompt_tokens": 100,
                "total_prompt_cost": 1.0,
                "total_completion_tokens": 50,
                "total_completion_cost": 1.0,
                "total_tokens": 150,
                "total_cost": 2.0,
            }
        },
    }

    assert enrich_result_usage_cost_if_needed(result) == result


def test_enrich_result_usage_cost_if_needed_recomputes_when_total_cost_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_cost, "load_litellm_price_table", lambda: TEST_PRICE_TABLE)
    result = {
        "model_id": "gpt-4.1",
        "metrics": {
            "usage": {
                "total_prompt_tokens": 1000,
                "total_prompt_cost": 0.0,
                "total_completion_tokens": 100,
                "total_completion_cost": 0.0,
                "total_tokens": 1100,
                "total_cost": 0.0,
            }
        },
    }

    enriched_result = enrich_result_usage_cost_if_needed(result)
    usage = enriched_result["metrics"]["usage"]
    assert usage["total_cost"] == pytest.approx(0.0028)
    assert usage["total_prompt_cost"] == pytest.approx(0.002)
    assert usage["total_completion_cost"] == pytest.approx(0.0008)


def test_enrich_result_usage_cost_if_needed_noop_without_usage() -> None:
    result = {
        "model_id": "gpt-4.1",
        "metrics": {},
    }

    assert enrich_result_usage_cost_if_needed(result) == result


def test_enrich_result_usage_cost_if_needed_missing_model_id_is_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_cost, "load_litellm_price_table", lambda: {})
    result = {
        "metrics": {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "total_tokens": 1100,
            }
        },
    }

    enriched_result = enrich_result_usage_cost_if_needed(result)
    usage = enriched_result["metrics"]["usage"]
    assert usage["total_prompt_tokens"] == 1000
    assert usage["total_completion_tokens"] == 100
    assert usage["total_tokens"] == 1100
    assert usage["total_cost"] == 0.0


# Model ids that must always price to a non-zero cost from the shipped
# configs/pricing/model_pricing.yaml. Covers the LexBench tech report (0624)
# model lineup plus model_ids used by config.example.yaml / past runs.
SHIPPED_PRICED_MODEL_IDS = (
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5-mini",
    "claude-opus-4-8",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
    "gemini-2.5-pro",
    "glm-5.2",
    "glm-5.1",
    "doubao-seed-2-0-pro",
    "qwen3.7-max",
    "qwen3.5-plus",
    "kimi-k2.6",
    "MiniMax-M3",
    "MiniMax-M2.5",
    "deepseek-v4-pro",
    "bu-2-0",
)


@pytest.mark.parametrize("model_id", SHIPPED_PRICED_MODEL_IDS)
def test_shipped_pricing_table_resolves_nonzero_cost(model_id: str) -> None:
    """The shipped custom pricing table must price every covered model."""
    usage = {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000}
    enriched = enrich_usage_with_litellm_pricing(
        usage=usage,
        model_name=model_id,
        price_table={},
    )

    assert enriched["total_prompt_cost"] > 0.0, model_id
    assert enriched["total_completion_cost"] > 0.0, model_id
    assert enriched["total_cost"] > 0.0, model_id


def test_shipped_pricing_recomputes_zero_cost_record_for_real_model() -> None:
    """Regression: a complete-but-zero-cost usage record (the upstream-cost bug)
    is re-priced once the model exists in the shipped table."""
    result = {
        "model_id": "deepseek-v4-pro",
        "metrics": {
            "usage": {
                "total_prompt_tokens": 83689,
                "total_prompt_cost": 0.0,
                "total_completion_tokens": 3950,
                "total_completion_cost": 0.0,
                "total_tokens": 87639,
                "total_cost": 0.0,
            }
        },
    }

    usage = enrich_result_usage_cost_if_needed(result)["metrics"]["usage"]
    assert usage["total_cost"] > 0.0
    assert usage["total_prompt_cost"] > 0.0
    assert usage["total_completion_cost"] > 0.0


def test_load_litellm_price_table_uses_browseruse_bench_cache_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "token_cost"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "pricing_20260225_000000.json"
    cache_payload = {
        "timestamp": datetime.now(UTC).isoformat(),
        "data": TEST_PRICE_TABLE,
    }
    cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")

    monkeypatch.setattr(llm_cost, "DEFAULT_BROWSERUSE_BENCH_TOKEN_COST_CACHE_DIR", cache_dir)
    monkeypatch.setattr(
        llm_cost.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be called")),
    )

    table = load_litellm_price_table()

    assert "gpt-4.1" in table


def test_load_litellm_price_table_writes_browseruse_bench_cache_after_fetch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "token_cost"
    monkeypatch.setattr(llm_cost, "DEFAULT_BROWSERUSE_BENCH_TOKEN_COST_CACHE_DIR", cache_dir)
    monkeypatch.setattr(llm_cost, "DEFAULT_LITELLM_PRICE_TABLE_URL", llm_cost.LITELLM_PRICE_TABLE_URL)
    monkeypatch.setattr(
        llm_cost.urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeHTTPResponse(json.dumps(TEST_PRICE_TABLE).encode("utf-8")),
    )

    table = load_litellm_price_table()

    assert "gpt-4.1" in table
    cache_files = list(cache_dir.glob("pricing_bubench_*.json"))
    assert cache_files
    payload = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert "timestamp" in payload
    assert "gpt-4.1" in payload["data"]


def test_load_litellm_price_table_returns_empty_on_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url = "https://example.com/model_prices_and_context_window.json"

    monkeypatch.setattr(llm_cost, "DEFAULT_LITELLM_PRICE_TABLE_URL", url)
    monkeypatch.setattr(llm_cost, "DEFAULT_BROWSERUSE_BENCH_TOKEN_COST_CACHE_DIR", tmp_path / "token_cost")
    monkeypatch.setattr(
        llm_cost.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("network down")),
    )

    table = load_litellm_price_table()

    assert table == {}
