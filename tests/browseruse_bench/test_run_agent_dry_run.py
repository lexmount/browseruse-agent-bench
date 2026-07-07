"""Regression tests: `bubench run --dry-run` must leave the experiments tree untouched.

_claim_unique_run_dir() used to run before the dry-run early return, so every
dry run left an empty YYYYMMDD_HHMMSS directory behind that find-latest and
leaderboard scans could pick up.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from browseruse_bench.cli import run as run_module
from browseruse_bench.cli.run import configure_run_parser, run_agent

BENCHMARK = "FakeBench"
AGENT = "dummy-agent"
CONFIG = {"agents": {AGENT: {}}}


@pytest.fixture
def fake_repo_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data_dir = tmp_path / "browseruse_bench" / "data" / BENCHMARK
    data_dir.mkdir(parents=True)
    (data_dir / "data_info.json").write_text(
        json.dumps({"default_split": "All", "split": {"All": "task.jsonl"}}),
        encoding="utf-8",
    )
    task = {"id": 1, "query": "open the page", "website": "https://example.com"}
    (data_dir / "task.jsonl").write_text(json.dumps(task) + "\n", encoding="utf-8")
    monkeypatch.setattr(run_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        run_module,
        "collect_machine_identity",
        lambda *args, **kwargs: {"machine_id": "test-machine", "hostname": "test-host"},
    )
    return tmp_path


def _run_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="bubench run")
    configure_run_parser(parser, {})
    args = parser.parse_args(argv)
    args._inline_agent_config = {"model_id": "test-model"}
    return args


def test_dry_run_claims_no_run_directory(fake_repo_root: Path, tmp_path: Path) -> None:
    marker = tmp_path / "outdir-marker"
    args = _run_args(
        [
            "--agent", AGENT,
            "--data", BENCHMARK,
            "--mode", "first_n",
            "--count", "1",
            "--dry-run",
            "--write-output-dir", str(marker),
        ]
    )

    rc = run_agent(AGENT, BENCHMARK, CONFIG, args)

    assert rc == 0
    assert not (fake_repo_root / "experiments").exists()
    # The marker is still emitted (run-eval relies on it to stay on the
    # authoritative binding path), but points at the unclaimed output base,
    # which run-eval's _read_marker never binds (no tasks/ subdir).
    marker_content = Path(marker.read_text(encoding="utf-8"))
    assert marker_content == (
        fake_repo_root / "experiments" / BENCHMARK / "All" / AGENT / "test-model"
    )
    assert not marker_content.exists()


def test_dry_run_with_timestamp_writes_nothing_to_existing_dir(fake_repo_root: Path) -> None:
    existing = (
        fake_repo_root / "experiments" / BENCHMARK / "All" / AGENT / "test-model" / "20260101_000000"
    )
    existing.mkdir(parents=True)
    args = _run_args(
        [
            "--agent", AGENT,
            "--data", BENCHMARK,
            "--mode", "first_n",
            "--count", "1",
            "--dry-run",
            "--timestamp", "20260101_000000",
        ]
    )

    rc = run_agent(AGENT, BENCHMARK, CONFIG, args)

    assert rc == 0
    assert list(existing.iterdir()) == []
