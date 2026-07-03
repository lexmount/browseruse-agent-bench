"""Tests for cli/run.py task-runner watchdog (lingering agent_runner recovery)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from browseruse_bench.cli import run as run_module
from browseruse_bench.cli.run import (
    _is_task_env_success_by_result_json,
    _wait_for_task_runner,
)


def _write_result(
    output_dir: Path,
    task_id: str,
    env_status: str = "success",
    error: str | None = None,
) -> Path:
    task_dir = output_dir / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    result_file = task_dir / "result.json"
    result_file.write_text(
        json.dumps({"env_status": env_status, "agent_done": "done", "error": error}),
        encoding="utf-8",
    )
    return result_file


def _spawn_sleeper() -> subprocess.Popen:
    # Own session/process group: _terminate_one signals the whole group, which
    # must never be the test runner's own group.
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=not sys.platform.startswith("win"),
    )


class TestIsTaskEnvSuccessByResultJson:
    def test_success_result(self, tmp_path: Path) -> None:
        _write_result(tmp_path, "t1")
        assert _is_task_env_success_by_result_json("t1", tmp_path)

    def test_failed_result(self, tmp_path: Path) -> None:
        _write_result(tmp_path, "t1", env_status="failed")
        assert not _is_task_env_success_by_result_json("t1", tmp_path)

    def test_result_with_error_not_success(self, tmp_path: Path) -> None:
        _write_result(tmp_path, "t1", error="boom")
        assert not _is_task_env_success_by_result_json("t1", tmp_path)

    def test_missing_result(self, tmp_path: Path) -> None:
        assert not _is_task_env_success_by_result_json("t1", tmp_path)

    def test_stale_result_rejected_by_newer_than(self, tmp_path: Path) -> None:
        # A result.json left over from a previous run must not count for the
        # current subprocess (it would let the watchdog kill a live re-run).
        result_file = _write_result(tmp_path, "t1")
        old = time.time() - 3600
        os.utime(result_file, (old, old))
        assert not _is_task_env_success_by_result_json("t1", tmp_path, newer_than=time.time() - 60)
        assert _is_task_env_success_by_result_json("t1", tmp_path, newer_than=old - 60)


class TestWaitForTaskRunner:
    def test_normal_exit_passthrough(self, tmp_path: Path) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "print('done')"])
        rc, killed = _wait_for_task_runner(
            proc, "t1", tmp_path, task_timeout=30, prefix="Task t1"
        )
        assert rc == 0
        assert killed is False

    def test_lingering_runner_killed_after_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import threading

        monkeypatch.setattr(run_module, "_TASK_RUNNER_RESULT_EXIT_GRACE_SECONDS", 1)
        proc = _spawn_sleeper()
        # result.json lands while the runner is still alive (as in a real run).
        timer = threading.Timer(1.0, _write_result, args=(tmp_path, "t1"))
        timer.start()
        t0 = time.monotonic()
        rc, killed = _wait_for_task_runner(
            proc, "t1", tmp_path, task_timeout=60, prefix="Task t1"
        )
        assert killed is True
        assert time.monotonic() - t0 < 30
        assert proc.poll() is not None

    def test_stale_result_does_not_trigger_early_kill(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(run_module, "_TASK_RUNNER_RESULT_EXIT_GRACE_SECONDS", 1)
        monkeypatch.setattr(run_module, "_TASK_RUNNER_WATCHDOG_GRACE_SECONDS", 2)
        result_file = _write_result(tmp_path, "t1")
        old = time.time() - 3600
        os.utime(result_file, (old, old))
        proc = _spawn_sleeper()
        rc, killed = _wait_for_task_runner(
            proc, "t1", tmp_path, task_timeout=3, prefix="Task t1"
        )
        # The stale result must not count as completion; the runner is only
        # reaped by the overall watchdog deadline.
        assert killed is False
        assert proc.poll() is not None

    def test_watchdog_deadline_kills_runaway_runner(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(run_module, "_TASK_RUNNER_WATCHDOG_GRACE_SECONDS", 1)
        proc = _spawn_sleeper()
        t0 = time.monotonic()
        rc, killed = _wait_for_task_runner(
            proc, "t1", tmp_path, task_timeout=1, prefix="Task t1"
        )
        assert killed is False
        assert time.monotonic() - t0 < 30
        assert proc.poll() is not None
