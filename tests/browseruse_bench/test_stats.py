"""Tests for browseruse_bench.utils.stats_utils module."""

import pytest

from browseruse_bench.utils import filter_tasks_by_label, generate_evaluation_summary


class TestFilterTasksByLabel:
    """Tests for filter_tasks_by_label function."""

    @pytest.fixture
    def sample_results(self):
        return [
            {"task_id": "1", "predicted_label": 1},  # success = 1
            {"task_id": "2", "predicted_label": 0},  # failed = 0
            {"task_id": "3", "predicted_label": 1},
            {"task_id": "4", "predicted_label": 0},
        ]

    def test_filter_success_tasks(self, sample_results):
        """Test filtering success tasks."""
        result = filter_tasks_by_label(sample_results, key="predicted_label", val=1)
        assert len(result) == 2
        assert all(r["predicted_label"] == 1 for r in result)

    def test_filter_failed_tasks(self, sample_results):
        """Test filtering failed tasks."""
        result = filter_tasks_by_label(sample_results, key="predicted_label", val=0)
        assert len(result) == 2
        assert all(r["predicted_label"] == 0 for r in result)

    def test_filter_empty_list(self):
        """Test filtering empty list returns empty."""
        result = filter_tasks_by_label([], key="predicted_label", val=1)
        assert result == []


class TestGenerateEvaluationSummary:
    """Tests for generated summary statistics."""

    def test_usage_statistics_include_sum(self):
        """Usage fields should expose a total across evaluated tasks."""
        results = [
            {
                "task_id": "1",
                "predicted_label": 1,
                "evaluation_details": {
                    "agent_metrics": {
                        "usage": {
                            "total_prompt_tokens": 100,
                            "total_completion_tokens": 20,
                            "total_tokens": 120,
                            "entry_count": 1,
                        }
                    }
                },
            },
            {
                "task_id": "2",
                "predicted_label": 0,
                "evaluation_details": {
                    "agent_metrics": {
                        "usage": {
                            "total_prompt_tokens": 200,
                            "total_completion_tokens": 80,
                            "total_tokens": 280,
                            "entry_count": 2,
                        }
                    }
                },
            },
        ]

        summary = generate_evaluation_summary(results, total=2)

        usage_stats = summary["metrics_statistics"]["usage"]
        assert usage_stats["total_prompt_tokens"]["sum"] == 300
        assert usage_stats["total_completion_tokens"]["sum"] == 100
        assert usage_stats["total_tokens"]["sum"] == 400
        assert usage_stats["entry_count"]["sum"] == 3
