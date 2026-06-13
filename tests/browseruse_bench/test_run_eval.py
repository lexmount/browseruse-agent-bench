"""Tests for the run-eval orchestration (scripts/run_and_eval.py)."""

from __future__ import annotations

from pathlib import Path

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


class _Harness:
    """Captures cli stage invocations and simulates the run's output dir."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.calls: list[list[str]] = []
        self.run_rc = 0
        self.produce_output = True
        self.run_timestamp = "20260101_000000"

    def cli_main(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        if argv[0] == "run" and self.produce_output:
            (self.base / self.run_timestamp / "tasks").mkdir(parents=True, exist_ok=True)
        # cli.main is wrapped by handle_cli_errors (sys.exit), so the real entry
        # raises SystemExit instead of returning — the fake must too.
        raise SystemExit(self.run_rc if argv[0] == "run" else 0)

    @property
    def stages(self) -> list[str]:
        return [c[0] for c in self.calls]

    def eval_call(self) -> list[str]:
        return next(c for c in self.calls if c[0] == "eval")


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Harness:
    h = _Harness(tmp_path / "out")
    monkeypatch.setattr(cli_pkg, "main", h.cli_main)
    monkeypatch.setattr(run_eval_mod, "load_config_file", lambda _: _ROOT_CONFIG)
    monkeypatch.setattr(run_eval_mod, "_run_output_base", lambda *a: h.base)
    return h


def test_chains_run_then_eval_with_model_id_and_timestamp(harness: _Harness) -> None:
    rc = run_and_eval(["--agent", "cursor", "--data", "LexBench-Browser", "--mode", "single"])
    assert rc == 0
    assert harness.stages == ["run", "eval"]
    assert harness.calls[0] == ["run", "--agent", "cursor", "--data", "LexBench-Browser", "--mode", "single"]
    ev = harness.eval_call()
    assert ev[ev.index("--model-id") + 1] == "gpt-5.2"
    assert ev[ev.index("--timestamp") + 1] == "20260101_000000"
    assert ev[ev.index("--agent") + 1] == "cursor"


def test_passthrough_model_flows_to_eval_model_id(harness: _Harness) -> None:
    run_and_eval(["--agent", "cursor", "--model", "claude-fable-5-thinking-high", "--mode", "single"])
    ev = harness.eval_call()
    assert ev[ev.index("--model-id") + 1] == "claude-fable-5-thinking-high"
    assert "claude-fable-5-thinking-high" in harness.calls[0]  # forwarded to run untouched


def test_completed_run_with_task_failures_is_still_evaluated(harness: _Harness) -> None:
    # run_agent returns 1 when any task fails; the run still produced output,
    # so it must be scored, not skipped.
    harness.run_rc = 1
    rc = run_and_eval(["--agent", "cursor", "--mode", "first_n", "--count", "3"])
    assert harness.stages == ["run", "eval"]
    assert rc == 0  # eval's exit code


def test_setup_failure_without_output_skips_eval(harness: _Harness) -> None:
    harness.run_rc = 1
    harness.produce_output = False
    rc = run_and_eval(["--agent", "cursor", "--mode", "single"])
    assert harness.stages == ["run"]
    assert rc == 1


def test_dry_run_stops_after_run(harness: _Harness) -> None:
    rc = run_and_eval(["--agent", "cursor", "--mode", "single", "--dry-run"])
    assert harness.stages == ["run"]
    assert rc == 0
    assert "--dry-run" in harness.calls[0]  # --dry-run is a real run flag, forwarded


def test_skip_eval_stops_after_run(harness: _Harness) -> None:
    rc = run_and_eval(["--agent", "cursor", "--mode", "single", "--skip-eval"])
    assert harness.stages == ["run"]
    assert rc == 0
    assert "--skip-eval" not in harness.calls[0]  # ours, not forwarded


def test_timestamp_resume_targets_that_run(harness: _Harness) -> None:
    # --timestamp resumes an older dir; eval must target it, not the newest.
    harness.produce_output = False  # nothing new created
    (harness.base / "20251231_120000" / "tasks").mkdir(parents=True)
    ev_rc = run_and_eval(["--agent", "cursor", "--mode", "by_id", "--timestamp", "20251231_120000"])
    assert ev_rc == 0
    ev = harness.eval_call()
    assert ev[ev.index("--timestamp") + 1] == "20251231_120000"


def test_forwards_data_source_and_force_download_to_eval(harness: _Harness) -> None:
    run_and_eval([
        "--agent", "cursor", "--mode", "single",
        "--data-source", "huggingface", "--force-download",
        "--split", "All", "--agent-config", "/app/config.yaml",
    ])
    ev = harness.eval_call()
    assert ev[ev.index("--data-source") + 1] == "huggingface"
    assert "--force-download" in ev
    assert ev[ev.index("--split") + 1] == "All"
    assert ev[ev.index("--agent-config") + 1] == "/app/config.yaml"


def test_defaults_agent_and_data_from_config(harness: _Harness) -> None:
    run_and_eval(["--mode", "single"])
    ev = harness.eval_call()
    assert ev[ev.index("--agent") + 1] == "cursor"
    assert ev[ev.index("--data") + 1] == "LexBench-Browser"
