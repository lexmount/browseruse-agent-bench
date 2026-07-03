"""Tests for Skyvern agent runtime cleanup boundaries."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from browseruse_bench.agents import skyvern as skyvern_module
from browseruse_bench.agents.skyvern import SkyvernAgent, _build_local_chromium_args


def test_close_runtime_resources_closes_browser_and_client() -> None:
    state = {"browser_close_calls": 0, "client_close_calls": 0}

    class FakeBrowser:
        async def close(self) -> None:
            state["browser_close_calls"] += 1

    class FakeSkyvernClient:
        async def aclose(self) -> None:
            state["client_close_calls"] += 1

    agent = SkyvernAgent()
    asyncio.run(
        agent._close_runtime_resources(
            browser=FakeBrowser(),
            skyvern_client=FakeSkyvernClient(),
            task_id="task-1",
        )
    )

    assert state["browser_close_calls"] == 1
    assert state["client_close_calls"] == 1


def test_close_runtime_resources_tolerates_close_errors() -> None:
    class BrokenBrowser:
        async def close(self) -> None:
            raise OSError("browser close failed")

    class BrokenSkyvernClient:
        async def aclose(self) -> None:
            raise RuntimeError("client close failed")

    agent = SkyvernAgent()
    asyncio.run(
        agent._close_runtime_resources(
            browser=BrokenBrowser(),
            skyvern_client=BrokenSkyvernClient(),
            task_id="task-2",
        )
    )


# ---------------------------------------------------------------------------
# local_proxy → Chrome --proxy-server CLI args
# (Skyvern.launch_local_browser bypasses its own BrowserFactory, so we must
# inject proxy via Chrome flags rather than Skyvern's ENABLE_PROXY env vars.)
# ---------------------------------------------------------------------------


def test_build_local_chromium_args_no_proxy() -> None:
    assert _build_local_chromium_args({}, None) == []
    assert _build_local_chromium_args({}, {}) == []
    assert _build_local_chromium_args({}, {"server": ""}) == []
    assert _build_local_chromium_args({}, {"server": "   "}) == []


def test_build_local_chromium_args_server_only() -> None:
    args = _build_local_chromium_args({}, {"server": "http://127.0.0.1:7897"})
    assert args == ["--proxy-server=http://127.0.0.1:7897"]


def test_build_local_chromium_args_with_auth() -> None:
    args = _build_local_chromium_args(
        {},
        {
            "server": "http://proxy.corp:3128",
            "username": "alice",
            "password": "s3cr3t",
        },
    )
    assert args == ["--proxy-server=http://alice:s3cr3t@proxy.corp:3128"]


def test_build_local_chromium_args_quotes_special_chars_in_password() -> None:
    args = _build_local_chromium_args(
        {},
        {"server": "http://proxy:8080", "username": "user@home", "password": "p@ss:word"},
    )
    assert args == ["--proxy-server=http://user%40home:p%40ss%3Aword@proxy:8080"]


def test_build_local_chromium_args_strips_existing_auth_when_explicit_provided() -> None:
    # Explicit username/password win over any auth embedded in the server URL —
    # we must not emit a malformed "alice:s3cret@old:cred@host" netloc.
    args = _build_local_chromium_args(
        {},
        {
            "server": "http://oldUser:oldPass@proxy.corp:3128",
            "username": "alice",
            "password": "s3cr3t",
        },
    )
    assert args == ["--proxy-server=http://alice:s3cr3t@proxy.corp:3128"]


def test_build_local_chromium_args_keeps_embedded_auth_when_no_explicit() -> None:
    args = _build_local_chromium_args(
        {}, {"server": "http://embedded:cred@proxy.corp:3128"}
    )
    assert args == ["--proxy-server=http://embedded:cred@proxy.corp:3128"]


def test_build_local_chromium_args_includes_bypass_list() -> None:
    args = _build_local_chromium_args(
        {},
        {
            "server": "http://127.0.0.1:7897",
            "bypass": "127.0.0.1,localhost,*.local",
        },
    )
    assert args == [
        "--proxy-server=http://127.0.0.1:7897",
        "--proxy-bypass-list=127.0.0.1,localhost,*.local",
    ]


def test_build_local_chromium_args_unparseable_server_returned_as_is() -> None:
    # Defensive: bare "host:port" without scheme — Chrome accepts it; don't mangle.
    args = _build_local_chromium_args(
        {},
        {
            "server": "host:8080",
            "username": "alice",
            "password": "s3cr3t",
        },
    )
    assert args == ["--proxy-server=host:8080"]


def test_build_local_chromium_args_password_without_username_drops_auth() -> None:
    # A bare password (no username) would otherwise produce a malformed
    # ":secret@host" netloc that most proxies reject (RFC 3986 violation).
    # Skip the auth embedding entirely in that case.
    args = _build_local_chromium_args(
        {},
        {
            "server": "http://proxy.corp:3128",
            "password": "s3cr3t",
        },
    )
    assert args == ["--proxy-server=http://proxy.corp:3128"]


def test_build_local_chromium_args_username_only_no_password() -> None:
    # Username only (no password) is RFC-valid: "user@host". Verify we emit it.
    args = _build_local_chromium_args(
        {},
        {
            "server": "http://proxy.corp:3128",
            "username": "alice",
        },
    )
    assert args == ["--proxy-server=http://alice@proxy.corp:3128"]


# ---------------------------------------------------------------------------
# Local artifact matching / collection (timeout-path regression: a task_v2 run
# spreads steps across several tsk_ dirs; only the first embeds the user
# prompt, and the client-side timeout branch must still collect usage/steps).
# ---------------------------------------------------------------------------


def _write_artifact_task_dir(
    org_dir: Path,
    name: str,
    prompt_text: str | None,
    step_count: int = 1,
) -> None:
    task_dir = org_dir / name
    for i in range(step_count):
        step_dir = task_dir / f"{i:02d}_0_stp_{name.removeprefix('tsk_')}{i}"
        step_dir.mkdir(parents=True)
        if prompt_text is not None and i == 0:
            (step_dir / "a_llm_prompt.txt").write_text(prompt_text, encoding="utf-8")
        (step_dir / "a_llm_response.json").write_text(
            json.dumps({"usage": {"prompt_tokens": 10, "completion_tokens": 5}}),
            encoding="utf-8",
        )
        (step_dir / "a_llm_response_parsed.json").write_text(
            json.dumps({"actions": [{"action_type": "CLICK", "id": f"{name}-{i}"}]}),
            encoding="utf-8",
        )
        (step_dir / "a_screenshot_action.png").write_bytes(b"png")


def _patch_artifacts_base(monkeypatch, base: Path) -> None:
    monkeypatch.setattr(skyvern_module, "_get_skyvern_artifacts_base", lambda: base)


def test_resolve_artifact_task_dirs_expands_to_sibling_dirs(tmp_path, monkeypatch) -> None:
    """A prompt-anchored v2 run must claim every new in-window tsk_ dir."""
    prompt = "Find the highest rated braised pork recipe"
    org_dir = tmp_path / "o_test"
    org_dir.mkdir()
    # Numeric ids chosen so lexicographic order would be wrong (1000 < 900).
    _write_artifact_task_dir(org_dir, "tsk_900", prompt)
    _write_artifact_task_dir(org_dir, "tsk_1000", "planner sub-goal, no user prompt")
    _write_artifact_task_dir(org_dir, "tsk_1100", "extraction sub-goal")
    _patch_artifacts_base(monkeypatch, tmp_path)

    now = time.time()
    dirs = skyvern_module._resolve_artifact_task_dirs(
        now - 50,
        now + 50,
        org_id="o_test",
        existing_dirs_before_task=set(),
        task_prompt=prompt,
        include_sibling_dirs=True,
    )

    assert [d.name for d in dirs] == ["tsk_900", "tsk_1000", "tsk_1100"]


def test_resolve_artifact_task_dirs_default_keeps_anchor_only(tmp_path, monkeypatch) -> None:
    prompt = "Find the highest rated braised pork recipe"
    org_dir = tmp_path / "o_test"
    org_dir.mkdir()
    _write_artifact_task_dir(org_dir, "tsk_900", prompt)
    _write_artifact_task_dir(org_dir, "tsk_1000", "planner sub-goal, no user prompt")
    _patch_artifacts_base(monkeypatch, tmp_path)

    now = time.time()
    dirs = skyvern_module._resolve_artifact_task_dirs(
        now - 50,
        now + 50,
        org_id="o_test",
        existing_dirs_before_task=set(),
        task_prompt=prompt,
    )

    assert [d.name for d in dirs] == ["tsk_900"]


def test_resolve_artifact_task_dirs_sibling_expansion_skips_preexisting(
    tmp_path, monkeypatch
) -> None:
    prompt = "Find the highest rated braised pork recipe"
    org_dir = tmp_path / "o_test"
    org_dir.mkdir()
    _write_artifact_task_dir(org_dir, "tsk_800", "leftover from a previous task")
    _write_artifact_task_dir(org_dir, "tsk_900", prompt)
    _write_artifact_task_dir(org_dir, "tsk_1000", "planner sub-goal")
    _patch_artifacts_base(monkeypatch, tmp_path)

    now = time.time()
    dirs = skyvern_module._resolve_artifact_task_dirs(
        now - 50,
        now + 50,
        org_id="o_test",
        existing_dirs_before_task={"tsk_800"},
        task_prompt=prompt,
        include_sibling_dirs=True,
    )

    assert [d.name for d in dirs] == ["tsk_900", "tsk_1000"]


def test_collect_run_artifacts_aggregates_across_sibling_dirs(tmp_path, monkeypatch) -> None:
    """The shared collection helper (used by the timeout path) must aggregate
    screenshots, steps, actions, and usage across every matched tsk_ dir."""
    prompt = "Find the highest rated braised pork recipe"
    org_dir = tmp_path / "artifacts" / "o_test"
    org_dir.mkdir(parents=True)
    _write_artifact_task_dir(org_dir, "tsk_900", prompt, step_count=2)
    _write_artifact_task_dir(org_dir, "tsk_1000", None, step_count=3)
    _patch_artifacts_base(monkeypatch, tmp_path / "artifacts")

    trajectory_dir = tmp_path / "trajectory"
    trajectory_dir.mkdir()
    now = time.time()

    screenshot_count, steps, action_history, usage = skyvern_module._collect_run_artifacts(
        trajectory_dir,
        now - 50,
        now + 50,
        org_id="o_test",
        existing_dirs_before_task=set(),
        task_prompt=prompt,
        include_sibling_dirs=True,
    )

    assert screenshot_count == 5
    assert steps == 5
    assert len(action_history) == 5
    assert usage["total_prompt_tokens"] == 50
    assert usage["total_completion_tokens"] == 25
    assert usage["entry_count"] == 5


class TestExtractUsageFromResponseBlob:
    """Usage extraction must not mix OpenAI and Anthropic token semantics."""

    def test_openai_style_prompt_includes_cached(self) -> None:
        from browseruse_bench.agents.skyvern import _extract_usage_from_response_blob

        blob = {
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 20,
                "prompt_tokens_details": {"cached_tokens": 300},
            }
        }
        usage = _extract_usage_from_response_blob(blob)
        assert usage == {
            "prompt_tokens": 500,
            "completion_tokens": 20,
            "cached_tokens": 300,
            "cache_creation_tokens": 0,
            "total_tokens": 520,
        }

    def test_anthropic_style_adds_cache_components_to_prompt(self) -> None:
        from browseruse_bench.agents.skyvern import _extract_usage_from_response_blob

        # Anthropic input_tokens EXCLUDES cache reads/writes; the normalized
        # prompt count must include them so downstream cost math (which
        # subtracts cached from prompt) stays correct.
        blob = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 400,
                "cache_creation_input_tokens": 50,
            }
        }
        usage = _extract_usage_from_response_blob(blob)
        assert usage == {
            "prompt_tokens": 550,
            "completion_tokens": 20,
            "cached_tokens": 400,
            "cache_creation_tokens": 50,
            "total_tokens": 570,
        }

    def test_responses_api_style_input_already_includes_cached(self) -> None:
        from browseruse_bench.agents.skyvern import _extract_usage_from_response_blob

        # OpenAI Responses API: input_tokens INCLUDES cached tokens.
        blob = {
            "usage": {
                "input_tokens": 500,
                "output_tokens": 20,
                "input_tokens_details": {"cached_tokens": 300},
            }
        }
        usage = _extract_usage_from_response_blob(blob)
        assert usage == {
            "prompt_tokens": 500,
            "completion_tokens": 20,
            "cached_tokens": 300,
            "cache_creation_tokens": 0,
            "total_tokens": 520,
        }

    def test_collect_usage_sums_cache_creation(self, tmp_path) -> None:
        import json

        from browseruse_bench.agents.skyvern import collect_usage_from_skyvern_artifacts

        step_dir = tmp_path / "tsk_1" / "a_stp_001"
        step_dir.mkdir(parents=True)
        (step_dir / "raw_llm_response.json").write_text(
            json.dumps(
                {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 10,
                        "cache_read_input_tokens": 200,
                        "cache_creation_input_tokens": 30,
                    }
                }
            ),
            encoding="utf-8",
        )

        summary = collect_usage_from_skyvern_artifacts([tmp_path / "tsk_1"])
        assert summary["total_prompt_tokens"] == 330
        assert summary["total_prompt_cached_tokens"] == 200
        assert summary["total_prompt_cache_creation_tokens"] == 30
        assert summary["total_tokens"] == 340

    def test_details_zero_falls_back_to_top_level_cache_keys(self) -> None:
        from browseruse_bench.agents.skyvern import _extract_usage_from_response_blob

        # Zero-filled details must not mask a real top-level cache counter.
        blob = {
            "usage": {
                "prompt_tokens": 12000,
                "completion_tokens": 300,
                "prompt_tokens_details": {"cached_tokens": 0},
                "cache_read_input_tokens": 9000,
            }
        }
        usage = _extract_usage_from_response_blob(blob)
        assert usage is not None
        assert usage["cached_tokens"] == 9000

    def test_zero_prompt_tokens_falls_back_to_input_tokens(self) -> None:
        from browseruse_bench.agents.skyvern import _extract_usage_from_response_blob

        blob = {
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 500,
                "input_tokens": 8000,
                "cache_read_input_tokens": 6000,
            }
        }
        usage = _extract_usage_from_response_blob(blob)
        assert usage is not None
        assert usage["prompt_tokens"] == 14000  # anthropic-style fold
        assert usage["cached_tokens"] == 6000
