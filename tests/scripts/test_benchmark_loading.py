"""Tests for benchmark data loading."""

import json
from pathlib import Path

from browseruse_bench.utils import (
    REPO_ROOT,
    get_default_split,
    get_default_version,
    load_task_file,
)


def _resolve_tasks_file(benchmark_name: str, split: str = "All") -> Path:
    """Resolve a tasks file under browseruse_bench/data/<benchmark_name>/."""
    base = REPO_ROOT / "browseruse_bench" / "data" / benchmark_name
    data_info = json.loads((base / "data_info.json").read_text(encoding="utf-8"))

    if "split" in data_info:
        split_name = split or get_default_split(data_info) or "All"
        filename = data_info.get("split", {}).get(split_name)
        assert filename, f"Missing split '{split_name}' for {benchmark_name}"
        return base / filename

    version = get_default_version(data_info)
    assert version, f"Missing default version for {benchmark_name}"
    split_map = data_info.get("version_split", {}).get(version, {})
    filename = split_map.get(split)
    assert filename, f"Missing split '{split}' for {benchmark_name} version {version}"
    return base / version / filename


class TestBenchmarkLoading:
    """Tests for loading benchmark data files."""

    def test_lexbench_browser_tasks_exist(self):
        """Test LexBench-Browser tasks file exists and is valid JSON/JSONL."""
        tasks_file = _resolve_tasks_file("LexBench-Browser")
        tasks = load_task_file(tasks_file)
        assert len(tasks) > 0

    def test_lexbench_browser_access_splits_cover_all_tasks(self):
        """Test LexBench-Browser access-region splits are valid and exhaustive."""
        all_tasks = load_task_file(_resolve_tasks_file("LexBench-Browser", "All"))
        lexmount_tasks = load_task_file(_resolve_tasks_file("LexBench-Browser", "lexmount"))
        global_tasks = load_task_file(_resolve_tasks_file("LexBench-Browser", "global"))

        all_ids = {task["id"] for task in all_tasks}
        lexmount_ids = {task["id"] for task in lexmount_tasks}
        global_ids = {task["id"] for task in global_tasks}

        assert len(lexmount_tasks) == 118
        assert len(global_tasks) == 92
        assert lexmount_ids.isdisjoint(global_ids)
        assert lexmount_ids | global_ids == all_ids

    def test_online_mind2web_tasks_exist(self):
        """Test Online-Mind2Web tasks file exists and is valid JSON."""
        tasks_file = _resolve_tasks_file("Online-Mind2Web")
        tasks = load_task_file(tasks_file)
        assert len(tasks) > 0

    def test_browsecomp_tasks_exist(self):
        """Test BrowseComp tasks file exists and is valid JSONL."""
        tasks_file = _resolve_tasks_file("BrowseComp")
        tasks = load_task_file(tasks_file)
        assert len(tasks) > 0

    def test_odysseys_tasks_exist(self):
        """Test Odysseys tasks file exists and is valid JSONL."""
        tasks_file = _resolve_tasks_file("Odysseys")
        tasks = load_task_file(tasks_file)
        assert len(tasks) == 200
        assert "rubrics" in tasks[0]
