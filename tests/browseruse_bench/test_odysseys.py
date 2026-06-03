"""Tests for Odysseys benchmark integration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from browseruse_bench.utils import load_tasks


class TestLoadTasksOdysseysFields:
    """Tests that load_tasks() handles Odysseys' standard fields."""

    def test_load_tasks_odysseys_confirmed_task_and_google_start(self, tmp_path: Path):
        tasks_file = tmp_path / "task.jsonl"
        tasks_file.write_text(json.dumps({
            "task_id": "odyssey-1",
            "confirmed_task": "Find three related pages and summarize them.",
            "website": "https://www.google.com",
            "rubrics": {"R1": {"requirement": "Find the first page."}},
        }) + "\n")

        tasks = load_tasks(str(tasks_file))

        assert len(tasks) == 1
        assert tasks[0]["task_id"] == "odyssey-1"
        assert tasks[0]["task_text"] == "Find three related pages and summarize them."
        assert tasks[0]["url"] == "https://www.google.com"
        assert "rubrics" in tasks[0]

    def test_odysseys_prompt_allows_cross_site_navigation(self):
        from browseruse_bench.cli.run import _prompt_format_for_benchmark

        prompt_fmt, prompt_fmt_multi = _prompt_format_for_benchmark("Odysseys")
        prompt = prompt_fmt.format(
            task="Find evidence across Hulu and Wikipedia.",
            url="https://www.google.com",
        )

        assert "You may visit any websites needed" in prompt
        assert "Only use https://www.google.com" not in prompt
        # Odysseys needs only one template; there is no multi-site variant.
        assert prompt_fmt_multi is None

    def test_single_site_prompt_keeps_existing_constraint(self):
        from browseruse_bench.cli.run import _prompt_format_for_benchmark

        prompt_fmt, _ = _prompt_format_for_benchmark("LexBench-Browser")
        prompt = prompt_fmt.format(
            task="Find one page.",
            url="https://example.com",
        )

        assert "Use only https://example.com" in prompt
        # Region redirects to the same site are allowed, off-site is not.
        assert "do not navigate to unrelated third-party sites" in prompt

    def test_multi_site_benchmark_prompt_lists_all_sites(self):
        """LexBench multi-site tasks must NOT be pinned to a single-site
        'use only' constraint via the CLI prompt path."""
        from browseruse_bench.cli.run import _prompt_format_for_benchmark

        _, prompt_fmt_multi = _prompt_format_for_benchmark("LexBench-Browser")
        assert prompt_fmt_multi is not None
        prompt = prompt_fmt_multi.format(
            task="Compare ratings.",
            url="https://movie.douban.com",
            urls="https://movie.douban.com, https://imdb.com",
        )
        assert "https://movie.douban.com" in prompt
        assert "https://imdb.com" in prompt
        assert "Use only" not in prompt


class TestOdysseysRegistry:
    """Tests that Odysseys evaluator is registered."""

    def test_odysseys_in_list_evaluators(self):
        from browseruse_bench.eval.registry import list_evaluators
        assert "Odysseys" in list_evaluators()

    def test_odysseys_get_evaluator_class(self):
        from browseruse_bench.eval.base import BaseEvaluator
        from browseruse_bench.eval.registry import get_evaluator_class

        cls = get_evaluator_class("Odysseys")

        assert issubclass(cls, BaseEvaluator)


class TestOdysseysGrader:
    """Tests for rubric parsing."""

    def _make_mock_model(self, response_text: str) -> MagicMock:
        model = MagicMock()
        model.generate.return_value = response_text
        model.last_usage = None
        return model

    def test_grade_rubrics_computes_partial_score(self):
        from browseruse_bench.eval.odysseys.grader import grade_rubrics

        model = self._make_mock_model(json.dumps({
            "rubric_results": {
                "R1": {"passed": True, "reasoning": "done"},
                "R2": {"passed": False, "reasoning": "missing"},
            },
            "reasoning": "partial",
        }))

        result = grade_rubrics(
            task="Do a long task.",
            answer="Finished part of it.",
            rubrics={"R1": {}, "R2": {}},
            screenshot_paths=[],
            model=model,
        )

        assert result["passed_rubrics"] == 1
        assert result["total_rubrics"] == 2
        assert result["rubric_score"] == 0.5
        assert result["is_correct"] is False

    def test_grade_rubrics_marks_perfect_success(self):
        from browseruse_bench.eval.odysseys.grader import grade_rubrics

        model = self._make_mock_model(json.dumps({
            "rubric_results": {
                "R1": {"passed": True},
                "R2": {"passed": True},
            }
        }))

        result = grade_rubrics(
            task="Do a long task.",
            answer="Done.",
            rubrics={"R1": {}, "R2": {}},
            screenshot_paths=[],
            model=model,
        )

        assert result["rubric_score"] == 1.0
        assert result["is_correct"] is True


class TestOdysseysEvaluatorLoadTasks:
    """Tests for OdysseysEvaluator.load_tasks()."""

    def _make_task_file(self, tmp_path: Path, tasks: list[dict[str, Any]]) -> Path:
        tasks_file = tmp_path / "task.jsonl"
        tasks_file.write_text("\n".join(json.dumps(t) for t in tasks) + "\n")
        return tasks_file

    def test_load_tasks_maps_task_id(self, tmp_path: Path):
        from browseruse_bench.eval.base import EvaluatorArgs
        from browseruse_bench.eval.odysseys.evaluator import OdysseysEvaluator

        task_file = self._make_task_file(tmp_path, [
            {
                "task_id": "odyssey-1",
                "confirmed_task": "Find several pages.",
                "website": "https://www.google.com",
                "rubrics": {"R1": {"requirement": "Find page."}},
            },
        ])
        args = EvaluatorArgs(
            benchmark="Odysseys", model="gpt-4o", api_key="test",
            base_url=None, trajectories_dir=tmp_path, output_path=tmp_path,
            score_threshold=None, num_worker=1, temperature=None,
            split="All", data_source="local", mode="odysseys_eval",
        )
        ev = OdysseysEvaluator(args, model=None)

        with patch("browseruse_bench.eval.odysseys.evaluator._TASKS_FILE_PATH", task_file):
            tasks = ev.load_tasks()

        assert "odyssey-1" in tasks
