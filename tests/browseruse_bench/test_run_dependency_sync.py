from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from browseruse_bench.cli import run as run_module


class _StopAfterInstall(RuntimeError):
    pass


def test_run_agent_installs_dependencies_for_existing_venv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: dict[str, Any] = {}

    monkeypatch.setattr(run_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        run_module,
        "resolve_agent_entry",
        lambda agent_name, config: {
            "supported_benchmarks": ["Online-Mind2Web"],
            "venv": ".venv",
        },
    )
    monkeypatch.setattr(run_module, "load_data_info", lambda benchmark_path: {"split": {"All": "tasks.json"}})
    monkeypatch.setattr(run_module, "resolve_data_file", lambda benchmark_path, split: "tasks.json")
    monkeypatch.setattr(run_module, "load_dataset_file", lambda **kwargs: [{"task_id": "task-1"}])
    monkeypatch.setattr(run_module, "load_config_file", lambda path: {"MODEL_ID": "test-model"})
    monkeypatch.setattr(
        run_module,
        "load_tasks_with_benchmark_support",
        lambda benchmark_data, prompt_fmt, default_url, prompt_fmt_multi=None: [
            {"task_id": "task-1", "task_text": "Open page", "url": "https://example.com"}
        ],
    )
    monkeypatch.setattr(
        run_module,
        "filter_tasks",
        lambda tasks, mode, count, task_ids, task_id: tasks,
    )
    monkeypatch.setattr(run_module, "check_uv_available", lambda: True)
    monkeypatch.setattr(run_module, "ensure_venv", lambda venv_path, use_uv: False)
    monkeypatch.setattr(run_module, "resolve_agent_venv_path", lambda agent_entry: tmp_path / ".venv")

    def _fake_install(
        venv_path: Path,
        extra_name: str | None,
        use_uv: bool,
        additional_targets: list[str] | None = None,
    ) -> None:
        calls["venv_path"] = venv_path
        calls["extra_name"] = extra_name
        calls["use_uv"] = use_uv
        calls["additional_targets"] = additional_targets
        raise _StopAfterInstall("stop after dependency install")

    monkeypatch.setattr(run_module, "install_agent_dependencies", _fake_install)

    args = argparse.Namespace(
        split="All",
        data_source="local",
        force_download=False,
        timestamp=None,
        skip_completed=False,
        mode="first_n",
        count=1,
        task_ids=None,
        id=None,
        dry_run=False,
        _inline_agent_config={"MODEL_ID": "test-model"},
        timeout=None,
    )

    with pytest.raises(_StopAfterInstall):
        run_module.run_agent(
            agent_name="browser-use",
            benchmark_name="Online-Mind2Web",
            config={"default": {}},
            args=args,
        )

    assert calls["venv_path"] == tmp_path / ".venv"
    assert calls["extra_name"] == "browser-use"
    assert calls["use_uv"] is True
    assert calls["additional_targets"] is None


def _patch_minimal_run_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(run_module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        run_module,
        "resolve_agent_entry",
        lambda agent_name, config: {
            "supported_benchmarks": ["Online-Mind2Web"],
            "venv": ".venv",
        },
    )
    monkeypatch.setattr(run_module, "load_data_info", lambda benchmark_path: {"split": {"All": "tasks.json"}})
    monkeypatch.setattr(run_module, "resolve_data_file", lambda benchmark_path, split: "tasks.json")
    monkeypatch.setattr(run_module, "load_dataset_file", lambda **kwargs: [{"task_id": "task-1"}])
    monkeypatch.setattr(run_module, "load_config_file", lambda path: {"MODEL_ID": "test-model"})
    monkeypatch.setattr(
        run_module,
        "load_tasks_with_benchmark_support",
        lambda benchmark_data, prompt_fmt, default_url, prompt_fmt_multi=None: [
            {"task_id": "task-1", "task_text": "Open page", "url": "https://example.com"}
        ],
    )
    monkeypatch.setattr(
        run_module,
        "filter_tasks",
        lambda tasks, mode, count, task_ids, task_id: tasks,
    )


def test_run_agent_accepts_valid_timestamp_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_minimal_run_dependencies(monkeypatch, tmp_path)

    timestamp = "20260224_155321"
    output_dir = (
        tmp_path / "experiments" / "Online-Mind2Web" / "All" / "browser-use" / "test-model" / timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    args = argparse.Namespace(
        split="All",
        data_source="local",
        force_download=False,
        timestamp=timestamp,
        skip_completed=False,
        mode="first_n",
        count=1,
        task_ids=None,
        id=None,
        dry_run=True,
        _inline_agent_config={"MODEL_ID": "test-model"},
        timeout=None,
    )

    result = run_module.run_agent(
        agent_name="browser-use",
        benchmark_name="Online-Mind2Web",
        config={"default": {}},
        args=args,
    )
    assert result == 0


def test_run_agent_rejects_invalid_timestamp_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_minimal_run_dependencies(monkeypatch, tmp_path)

    args = argparse.Namespace(
        split="All",
        data_source="local",
        force_download=False,
        timestamp="2026-02-24_155321",
        skip_completed=False,
        mode="first_n",
        count=1,
        task_ids=None,
        id=None,
        dry_run=True,
        _inline_agent_config={"MODEL_ID": "test-model"},
        timeout=None,
    )

    with pytest.raises(SystemExit, match="--timestamp format must be YYYYMMDD_HHmmss"):
        run_module.run_agent(
            agent_name="browser-use",
            benchmark_name="Online-Mind2Web",
            config={"default": {}},
            args=args,
        )
