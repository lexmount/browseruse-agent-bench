"""Tests for browseruse_bench/visualization/serve.py — AnalyzerHandler.translate_path."""

from pathlib import Path
from unittest.mock import patch

import pytest


class TestRepoRootNoneSafety:
    """Regression: when REPO_ROOT is unresolved, server code must fail cleanly, not crash."""

    def test_run_server_raises_when_repo_root_none(self, monkeypatch):
        import browseruse_bench.visualization.serve as serve_mod
        monkeypatch.setattr(serve_mod, "REPO_ROOT", None)
        with pytest.raises(RuntimeError, match="Cannot locate experiments/ directory"):
            serve_mod.run_server()

    def test_watch_experiments_returns_when_repo_root_none(self, monkeypatch, capsys):
        import browseruse_bench.visualization.serve as serve_mod
        monkeypatch.setattr(serve_mod, "REPO_ROOT", None)
        # Must return without dereferencing None; would TypeError otherwise
        serve_mod.watch_experiments(interval=0.01)
        captured = capsys.readouterr()
        assert "Cannot watch" in captured.out


class TestTranslatePath:
    def setup_method(self):
        self.repo_root = Path("/fake/repo")

    def _translate(self, url: str) -> str:
        import browseruse_bench.visualization.serve as serve_mod
        with patch.object(serve_mod, "REPO_ROOT", self.repo_root):
            from browseruse_bench.visualization.serve import AnalyzerHandler
            handler = AnalyzerHandler.__new__(AnalyzerHandler)
            handler.directory = str(serve_mod.SCRIPT_DIR)
            return handler.translate_path(url)

    def test_experiments_root(self):
        result = self._translate("/experiments")
        assert result == str(self.repo_root / "experiments")

    def test_experiments_subpath(self):
        result = self._translate("/experiments/bench/agent/task/screenshot.png")
        assert result == str(self.repo_root / "experiments") + "/bench/agent/task/screenshot.png"

    def test_output_subpath(self):
        result = self._translate("/output/logs/run/20260101_120000.log")
        assert result == str(self.repo_root / "output") + "/logs/run/20260101_120000.log"

    def test_experiments_with_query_string(self):
        result = self._translate("/experiments/foo.png?v=1")
        assert result == str(self.repo_root / "experiments") + "/foo.png"

    def test_experiments_with_fragment(self):
        result = self._translate("/experiments/foo.png#anchor")
        assert result == str(self.repo_root / "experiments") + "/foo.png"

    def test_traversal_does_not_escape(self):
        # /experiments/../../etc/passwd normalises to /etc/passwd — not mapped
        result = self._translate("/experiments/../../etc/passwd")
        assert not result.startswith(str(self.repo_root / "experiments"))

    def test_prefix_boundary_no_false_positive(self):
        # /experiments_extra should not be mapped to experiments dir
        result = self._translate("/experiments_extra/foo")
        assert result != str(self.repo_root / "experiments") + "_extra/foo"
        assert not result.startswith(str(self.repo_root / "experiments") + "/")

        result = self._translate("/output_extra/foo")
        assert result != str(self.repo_root / "output") + "_extra/foo"
        assert not result.startswith(str(self.repo_root / "output") + "/")

    def test_non_experiments_path_falls_through(self):
        # /index.html should be served from SCRIPT_DIR, not from experiments
        result = self._translate("/index.html")
        assert str(self.repo_root / "experiments") not in result
