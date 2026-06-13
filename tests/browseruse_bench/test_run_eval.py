"""Tests for the run-eval orchestration (scripts/run_and_eval.py)."""

from __future__ import annotations

import pytest

import browseruse_bench.cli as cli_pkg
import browseruse_bench.cli.run_eval as run_eval_mod
from browseruse_bench.cli.run_eval import run_and_eval

_ROOT_CONFIG = {
    "default": {"agent": "cursor", "data": "LexBench-Browser", "model": "cursor"},
    "models": {"cursor": {"model_id": "gpt-5.2", "api_key": "$CURSOR_API_KEY"}},
    "browsers": {"lexmount": {"browser_id": "lexmount"}},
    "agents": {"cursor": {"active_model": "cursor", "active_browser": "lexmount"}},
}


@pytest.fixture
def captured_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_cli_main(argv: list[str]) -> int:
        calls.append(list(argv))
        # cli.main is wrapped by handle_cli_errors, which sys.exit()s: the real
        # entry raises SystemExit instead of returning, so the fake must too.
        raise SystemExit(0)

    # run_and_eval imports cli_main lazily from browseruse_bench.cli.
    monkeypatch.setattr(cli_pkg, "main", fake_cli_main)
    monkeypatch.setattr(run_eval_mod, "load_config_file", lambda _: _ROOT_CONFIG)
    return calls


def test_chains_run_then_eval_with_resolved_model_id(captured_calls: list[list[str]]) -> None:
    rc = run_and_eval(["--agent", "cursor", "--data", "LexBench-Browser", "--mode", "single"])
    assert rc == 0
    assert len(captured_calls) == 2
    assert captured_calls[0] == ["run", "--agent", "cursor", "--data", "LexBench-Browser", "--mode", "single"]
    eval_call = captured_calls[1]
    assert eval_call[0] == "eval"
    assert "--model-id" in eval_call
    assert eval_call[eval_call.index("--model-id") + 1] == "gpt-5.2"
    assert eval_call[eval_call.index("--agent") + 1] == "cursor"


def test_passthrough_model_flows_to_eval_model_id(captured_calls: list[list[str]]) -> None:
    # --model <raw cursor id> => run writes under that id; eval must target it.
    rc = run_and_eval(["--agent", "cursor", "--model", "claude-fable-5-thinking-high", "--mode", "single"])
    assert rc == 0
    eval_call = captured_calls[1]
    assert eval_call[eval_call.index("--model-id") + 1] == "claude-fable-5-thinking-high"
    # the raw --model is forwarded to the run stage untouched
    assert "claude-fable-5-thinking-high" in captured_calls[0]


def test_skips_eval_when_run_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_cli_main(argv: list[str]) -> int:
        calls.append(list(argv))
        raise SystemExit(3 if argv[0] == "run" else 0)

    monkeypatch.setattr(cli_pkg, "main", fake_cli_main)
    monkeypatch.setattr(run_eval_mod, "load_config_file", lambda _: _ROOT_CONFIG)

    rc = run_and_eval(["--agent", "cursor", "--mode", "single"])
    assert rc == 3
    assert [c[0] for c in calls] == ["run"]


def test_skip_eval_flag_stops_after_run(captured_calls: list[list[str]]) -> None:
    rc = run_and_eval(["--agent", "cursor", "--mode", "single", "--skip-eval"])
    assert rc == 0
    assert [c[0] for c in captured_calls] == ["run"]
    # --skip-eval is consumed, not forwarded to the run stage
    assert "--skip-eval" not in captured_calls[0]


def test_forwards_split_and_agent_config_to_eval(captured_calls: list[list[str]]) -> None:
    run_and_eval(["--agent", "cursor", "--split", "All", "--agent-config", "/app/config.yaml", "--mode", "single"])
    eval_call = captured_calls[1]
    assert eval_call[eval_call.index("--split") + 1] == "All"
    assert eval_call[eval_call.index("--agent-config") + 1] == "/app/config.yaml"


def test_defaults_agent_and_data_from_config(captured_calls: list[list[str]]) -> None:
    # No --agent/--data: both stages fall back to config defaults consistently.
    run_and_eval(["--mode", "single"])
    eval_call = captured_calls[1]
    assert eval_call[eval_call.index("--agent") + 1] == "cursor"
    assert eval_call[eval_call.index("--data") + 1] == "LexBench-Browser"
