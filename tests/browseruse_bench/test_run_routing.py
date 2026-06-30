"""Tests for cli/run.py per-task lexmount routing resolution."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from browseruse_bench.browsers import login_contexts as lc
from browseruse_bench.cli.run import (
    _canonicalize_cli_browser_id,
    _classify_js_code_for_log,
    _claim_unique_run_dir,
    _clarify_agent_stdout_line,
    resolve_lexmount_routing_for_task,
)


@pytest.fixture
def tmp_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "index.json"
    monkeypatch.setattr(lc, "INDEX_PATH", path)
    return path


def _cfg_with_profiles() -> dict:
    return {
        "browser_id": "lexmount",
        "lexmount_profiles": {
            "zh": {"api_key": "K-zh", "project_id": "P-zh", "base_url": "https://zh"},
            "en": {"api_key": "K-en", "project_id": "P-en", "base_url": "https://en"},
        },
    }


def test_routing_picks_zh_profile_for_zh_task(tmp_index: Path) -> None:
    profile, ctx = resolve_lexmount_routing_for_task(
        _cfg_with_profiles(),
        {"task_id": "1", "website_region": "zh", "login_required": False},
    )
    assert profile == "zh"
    assert ctx is None


def test_routing_picks_en_profile_for_en_task(tmp_index: Path) -> None:
    profile, ctx = resolve_lexmount_routing_for_task(
        _cfg_with_profiles(),
        {"task_id": "1", "website_region": "en", "login_required": False},
    )
    assert profile == "en"
    assert ctx is None


def test_routing_no_profile_when_region_unconfigured(tmp_index: Path) -> None:
    """A region key not present in lexmount_profiles falls through (no profile env set)."""
    profile, ctx = resolve_lexmount_routing_for_task(
        _cfg_with_profiles(),
        {"task_id": "1", "website_region": "fr", "login_required": False},
    )
    assert profile is None
    assert ctx is None


def test_routing_no_profile_when_browser_not_lexmount(tmp_index: Path) -> None:
    profile, ctx = resolve_lexmount_routing_for_task(
        {"browser_id": "Chrome-Local", "lexmount_profiles": {"zh": {}}},
        {"task_id": "1", "website_region": "zh", "login_required": True},
    )
    assert profile is None
    assert ctx is None


def test_routing_resolves_login_context_per_profile(tmp_index: Path) -> None:
    lc.upsert_profile("xiaohongshu", "zh", {"context_id": "ctx_zh"})
    lc.upsert_profile("xiaohongshu", "en", {"context_id": "ctx_en"})

    profile_zh, ctx_zh = resolve_lexmount_routing_for_task(
        _cfg_with_profiles(),
        {
            "task_id": "1",
            "website_region": "zh",
            "login_required": True,
            "target_website": "https://www.xiaohongshu.com",
        },
    )
    profile_en, ctx_en = resolve_lexmount_routing_for_task(
        _cfg_with_profiles(),
        {
            "task_id": "2",
            "website_region": "en",
            "login_required": True,
            "target_website": "https://www.xiaohongshu.com",
        },
    )
    assert (profile_zh, ctx_zh) == ("zh", "ctx_zh")
    assert (profile_en, ctx_en) == ("en", "ctx_en")


def test_routing_soft_fails_when_login_context_missing(tmp_index: Path) -> None:
    """No saved context for a login-required task → context_id is None, profile still set."""
    profile, ctx = resolve_lexmount_routing_for_task(
        _cfg_with_profiles(),
        {
            "task_id": "1",
            "website_region": "zh",
            "login_required": True,
            "target_website": "https://www.xiaohongshu.com",
        },
    )
    assert profile == "zh"
    assert ctx is None


def test_routing_returns_none_when_inline_cfg_is_none(tmp_index: Path) -> None:
    profile, ctx = resolve_lexmount_routing_for_task(None, {"website_region": "zh"})
    assert profile is None
    assert ctx is None


def test_routing_matches_profile_case_insensitively(tmp_index: Path) -> None:
    """Profile keys written in mixed case (or with whitespace) in config still match."""
    cfg = {
        "browser_id": "lexmount",
        "lexmount_profiles": {
            "ZH": {"api_key": "K-zh"},
            " en ": {"api_key": "K-en"},
        },
    }
    profile, _ = resolve_lexmount_routing_for_task(
        cfg, {"task_id": "1", "website_region": "zh", "login_required": False}
    )
    assert profile == "zh"

    profile, _ = resolve_lexmount_routing_for_task(
        cfg, {"task_id": "2", "website_region": "EN", "login_required": False}
    )
    assert profile == "en"


def test_canonicalize_cli_browser_id_prefers_exact_config_key() -> None:
    """An exact config browsers key wins over registered backend id casing."""
    cfg = {"browsers": {"LEXMOUNT": {"browser_id": "lexmount"}, "lexmount": {}}}
    assert _canonicalize_cli_browser_id("LEXMOUNT", cfg) == "LEXMOUNT"
    assert _canonicalize_cli_browser_id("lexmount", cfg) == "lexmount"


def test_canonicalize_cli_browser_id_falls_back_to_backend_registry() -> None:
    assert _canonicalize_cli_browser_id("LEXMOUNT", {}) == "lexmount"
    assert _canonicalize_cli_browser_id("LEXMOUNT", {"browsers": None}) == "lexmount"
    assert _canonicalize_cli_browser_id(None, {}) is None
    assert _canonicalize_cli_browser_id("no-such-backend", {}) == "no-such-backend"


def test_claim_unique_run_dir_avoids_collision(tmp_path: Path) -> None:
    # Concurrent runs with identical params must get distinct dirs, never one
    # shared (which would interleave their output).
    base = tmp_path / "model"
    first = _claim_unique_run_dir(base)
    second = _claim_unique_run_dir(base)
    third = _claim_unique_run_dir(base)
    names = {first.name, second.name, third.name}
    assert len(names) == 3  # all distinct
    for d in (first, second, third):
        assert d.is_dir()
        assert re.match(r"^\d{8}_\d{6}$", d.name)  # strict format preserved


def test_claim_unique_run_dir_exhaustion_raises(tmp_path: Path) -> None:
    base = tmp_path / "model"
    with pytest.raises(SystemExit, match="unique run output directory"):
        _claim_unique_run_dir(base, max_seconds=0)


def test_clarify_agent_stdout_line_renames_browser_evaluate_code() -> None:
    line = "INFO     [Agent]   ▶️   evaluate: code: document.querySelectorAll('h2')"

    assert _clarify_agent_stdout_line(line) == (
        "INFO     [Agent]   ▶️   execute_js(read_dom): code: document.querySelectorAll('h2')"
    )


def test_clarify_agent_stdout_line_keeps_other_tools_unchanged() -> None:
    line = "INFO     [Agent]   ▶️   click: index: 8695"

    assert _clarify_agent_stdout_line(line) == line


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("[...document.querySelectorAll('a')].map(a=>a.href)", "extract_links"),
        ("document.querySelectorAll('h1,h2').length", "read_dom"),
        ("Array.from(document.querySelectorAll('input')).map(i=>i.value)", "form_state"),
        ("localStorage.getItem('city')", "storage"),
        ("document.title + location.href + document.readyState", "page_state"),
        ("performance.getEntries().map(e=>e.name)", "network"),
        ("document.querySelector('#x').click()", "modify_page"),
        ("(() => 42)()", "custom"),
    ],
)
def test_classify_js_code_for_log(code: str, category: str) -> None:
    assert _classify_js_code_for_log(code) == category
