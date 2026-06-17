"""Regression tests for the Online-Mind2Web evaluator model wiring.

WebJudge indexes ``model.generate(...)[0]`` expecting ``list[str]`` (the
OpenaiEngine contract). A prior bug wired the shared ``EvaluationModel`` (whose
``generate`` returns a ``str``) into the single-worker path, so ``[0]`` sliced
the first character of every judge response ("Thoughts..." -> "T"), forcing all
tasks to a parser-default failure regardless of the agent's real answer.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from browseruse_bench.eval.base import EvaluatorArgs
from browseruse_bench.eval.online_mind2web.evaluator import OnlineMind2WebEvaluator
from browseruse_bench.eval.online_mind2web.utils import OpenaiEngine


def _args(tmp_path: Path) -> EvaluatorArgs:
    traj = tmp_path / "tasks"
    traj.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    return EvaluatorArgs(
        benchmark="Online-Mind2Web",
        model="gpt-5.4",
        api_key="test-key",
        base_url=None,
        trajectories_dir=traj,
        output_path=out,
        score_threshold=3,
        num_worker=1,
        temperature=1.0,
        split="All",
        data_source="local",
        mode="WebJudge_Online_Mind2Web_eval",
    )


class _StrModel:
    """Mimics EvaluationModel: ``generate`` returns a ``str`` (not a list)."""

    def __init__(self) -> None:
        self.last_usage = None

    def generate(self, messages, *args, **kwargs) -> str:
        return 'Thoughts: looks complete.\nStatus: "success"'


class _FakeListEngine:
    """Mimics OpenaiEngine: ``generate`` returns ``list[str]``."""

    def __init__(self) -> None:
        self.last_usage = None

    def generate(self, messages, *args, **kwargs):
        system = messages[0]["content"] if messages else ""
        if "web navigation agent" in system:
            return ['Thoughts: All key points are satisfied.\nStatus: "success"']
        return ["**Key Points**:\n1. Complete the task"]


def test_webjudge_engine_is_list_returning_openai_engine(tmp_path):
    # The judge must run through a dedicated OpenaiEngine (list-returning),
    # never the shared str-returning model handed to BaseEvaluator.
    ev = OnlineMind2WebEvaluator(_args(tmp_path), model=_StrModel())
    assert isinstance(ev.engine, OpenaiEngine)
    assert ev.engine is not ev.model


def test_evaluate_one_preserves_full_verdict(tmp_path):
    # Shared model returns a str; if evaluate_one used it, generate(...)[0] would
    # slice "T" and the task would be force-failed. With the dedicated engine the
    # full 'Status: "success"' verdict must survive.
    ev = OnlineMind2WebEvaluator(_args(tmp_path), model=_StrModel())
    ev._engine = _FakeListEngine()  # avoid any network in the engine property

    tdir = ev.args.trajectories_dir / "task1"
    tdir.mkdir()
    agent_result = {
        "task": "Find the lowest-priced farm in Wilkes County, NC.",
        "action_history": ["Searched Wilkes County", "Found $38,888 as lowest"],
        "timestamp": datetime.now(UTC).isoformat(),
    }
    (tdir / "result.json").write_text(json.dumps(agent_result), encoding="utf-8")

    result = ev.evaluate_one("task1", {}, agent_result, tdir)

    assert result.predicted_label == 1
    assert 'Status: "success"' in result.evaluation_details.response
    # The response must be the full verdict, not a single sliced character.
    assert len(result.evaluation_details.response) > 1
