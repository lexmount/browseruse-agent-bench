"""Tests for the run-eval orchestration (scripts/run_and_eval.py)."""

from __future__ import annotations

import os
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
    """Captures cli stage invocations and simulates the run's output dir.

    The fake run writes its output dir to the --write-output-dir marker, like
    real run.py, so run-eval binds via the marker. Set emit_marker=False to
    exercise the mtime fallback (older run stage).
    """

    def __init__(self, exp_root: Path) -> None:
        self.exp_root = exp_root  # stands in for experiments/{bench}/{split}/{agent}
        self.calls: list[list[str]] = []
        self.run_rc = 0
        self.produce_output = True
        self.emit_marker = True
        self.run_timestamp = "20260101_000000"

    @staticmethod
    def _model_dir(argv: list[str]) -> str:
        for i, a in enumerate(argv):
            if a in ("--model", "--model-name") and i + 1 < len(argv):
                return argv[i + 1]
            if a.startswith("--model="):
                return a.split("=", 1)[1]
        return "gpt-5.2"  # config default for the cursor agent

    def add_existing_run(self, name: str, model: str = "gpt-5.2", mtime: float = 1000.0) -> None:
        """A run dir present before run-eval starts, with a fixed (old) mtime."""
        tasks = self.exp_root / model / name / "tasks"
        tasks.mkdir(parents=True, exist_ok=True)
        os.utime(tasks, (mtime, mtime))

    def cli_main(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        if argv[0] == "run" and self.produce_output:
            run_dir = self.exp_root / self._model_dir(argv) / self.run_timestamp
            (run_dir / "tasks").mkdir(parents=True, exist_ok=True)
            os.utime(run_dir / "tasks", (5000.0, 5000.0))  # fresh vs any prior dir
            if self.emit_marker and "--write-output-dir" in argv:
                Path(argv[argv.index("--write-output-dir") + 1]).write_text(str(run_dir))
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
    h = _Harness(tmp_path / "exp")
    monkeypatch.setattr(cli_pkg, "main", h.cli_main)
    monkeypatch.setattr(run_eval_mod, "load_config_file", lambda _: _ROOT_CONFIG)
    # Fallback base is per-model: experiments/.../{model_id}
    monkeypatch.setattr(run_eval_mod, "_run_output_base", lambda agent, data, split, mid: h.exp_root / mid)
    return h


def test_chains_run_then_eval_with_model_id_and_timestamp(harness: _Harness) -> None:
    rc = run_and_eval(["--agent", "cursor", "--data", "LexBench-Browser", "--mode", "single"])
    assert rc == 0
    assert harness.stages == ["run", "eval"]
    run_call = harness.calls[0]
    assert run_call[:6] == ["run", "--agent", "cursor", "--data", "LexBench-Browser", "--mode"]
    assert "--write-output-dir" in run_call  # marker injected for binding
    ev = harness.eval_call()
    assert ev[ev.index("--model-id") + 1] == "gpt-5.2"  # from the emitted run dir
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


def test_marker_binds_to_this_run_under_concurrency(harness: _Harness) -> None:
    # A concurrent run-eval for the same agent/model creates a NEWER dir while
    # this run is in flight. The marker pins THIS run's dir, so eval targets it,
    # not the newest one.
    harness.add_existing_run("20260102_999999", mtime=9000.0)  # other process, newer
    rc = run_and_eval(["--agent", "cursor", "--mode", "single"])
    assert rc == 0
    ev = harness.eval_call()
    assert ev[ev.index("--timestamp") + 1] == "20260101_000000"  # this run, from marker


def test_fallback_same_second_dir_reuse_is_evaluated(harness: _Harness) -> None:
    # No marker (older run stage): a reused same-named dir whose mtime advanced
    # is recognized as this run's output via the mtime fallback.
    harness.emit_marker = False
    harness.add_existing_run("20260101_000000")  # same name, old mtime
    rc = run_and_eval(["--agent", "cursor", "--mode", "single"])
    assert harness.stages == ["run", "eval"]
    assert rc == 0


def test_fallback_same_second_stale_dir_not_evaluated(harness: _Harness) -> None:
    # No marker, this run produces nothing; a same-named prior dir is untouched
    # (mtime unchanged) so it is NOT scored.
    harness.emit_marker = False
    harness.produce_output = False
    harness.add_existing_run("20260101_000000")
    harness.run_rc = 1
    rc = run_and_eval(["--agent", "cursor", "--mode", "single"])
    assert harness.stages == ["run"]
    assert rc == 1


def test_setup_failure_no_marker_no_output_skips_eval(harness: _Harness) -> None:
    # No marker and no fresh dir: a setup failure that produced nothing.
    harness.emit_marker = False
    harness.produce_output = False
    harness.add_existing_run("20251231_120000")
    harness.run_rc = 1
    rc = run_and_eval(["--agent", "cursor", "--mode", "single"])
    assert harness.stages == ["run"]
    assert rc == 1


def test_timestamp_resume_targets_that_run(harness: _Harness) -> None:
    # --timestamp resumes an older dir; eval must target it. The run reuses the
    # dir and emits the marker for it.
    harness.run_timestamp = "20251231_120000"
    harness.add_existing_run("20251231_120000")
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


def test_eval_only_flags_routed_to_eval_not_run(harness: _Harness) -> None:
    # run's parser rejects eval-only options, so they must be stripped from the
    # run stage and appended to eval.
    run_and_eval([
        "--agent", "cursor", "--mode", "single",
        "--score-threshold", "0.7", "--num-worker", "4", "--force-reeval",
    ])
    run_call = harness.calls[0]
    ev = harness.eval_call()
    for flag in ("--score-threshold", "--num-worker", "--force-reeval"):
        assert flag not in run_call
        assert flag in ev
    assert ev[ev.index("--score-threshold") + 1] == "0.7"
    assert ev[ev.index("--num-worker") + 1] == "4"
    # shared run flags stay on the run stage
    assert "--mode" in run_call


def test_defaults_agent_and_data_from_config(harness: _Harness) -> None:
    run_and_eval(["--mode", "single"])
    ev = harness.eval_call()
    assert ev[ev.index("--agent") + 1] == "cursor"
    assert ev[ev.index("--data") + 1] == "LexBench-Browser"


def test_default_agent_falls_back_to_agent_tars_like_run_parser(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # No --agent and no default.agent: must match run/eval's Agent-TARS default,
    # not browser-use, or the stages target different model paths.
    h = _Harness(tmp_path / "exp")
    h.run_timestamp = "20260101_000000"
    monkeypatch.setattr(cli_pkg, "main", h.cli_main)
    monkeypatch.setattr(
        run_eval_mod, "load_config_file",
        lambda _: {"default": {"data": "LexBench-Browser"}, "agents": {}},
    )
    monkeypatch.setattr(run_eval_mod, "_run_output_base", lambda agent, data, split, mid: h.exp_root / mid)
    monkeypatch.setattr(run_eval_mod, "resolve_output_model_id", lambda *a: "gpt-5.2")
    monkeypatch.setattr(run_eval_mod, "normalize_agent_name", lambda a, c: a)

    run_and_eval(["--mode", "single"])
    ev = h.eval_call()
    assert ev[ev.index("--agent") + 1] == "Agent-TARS"
