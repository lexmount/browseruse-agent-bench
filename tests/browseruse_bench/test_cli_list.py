"""Tests for the bubench list discovery command."""
from __future__ import annotations

import argparse
import json

from browseruse_bench.cli.list_cmd import configure_list_parser, list_command

SAMPLE_CONFIG = {
    "default": {"agent": "browser-use", "data": "LexBench-Browser"},
    "agents": {
        "browser-use": {"active_model": "gpt41"},
        "my-custom-agent": {"active_model": "gpt41"},
    },
    "models": {"gpt41": {"model_id": "gpt-4.1", "provider": "openai"}},
    "browsers": {},
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    configure_list_parser(parser, SAMPLE_CONFIG)
    return parser.parse_args(argv)


class TestListCommandJson:
    def _payload(self, capsys) -> dict:
        exit_code = list_command(_parse_args(["--json"]), SAMPLE_CONFIG)
        assert exit_code == 0
        return json.loads(capsys.readouterr().out)

    def test_lists_registered_benchmarks_with_splits(self, capsys):
        payload = self._payload(capsys)
        by_name = {b["name"]: b for b in payload["benchmarks"]}
        expected = {"BrowseComp", "LexBench-Browser", "Odysseys", "Online-Mind2Web", "WebVoyager"}
        assert expected <= set(by_name)
        lexbench = by_name["LexBench-Browser"]
        assert lexbench["default_split"] == "All"
        assert "sample50" in lexbench["splits"]
        assert lexbench["hf_repo_id"] == "Lexmount/LexBench-Browser"

    def test_agents_merge_registry_and_config(self, capsys):
        payload = self._payload(capsys)
        by_name = {a["name"]: a for a in payload["agents"]}
        assert by_name["browser-use"]["enabled"] is True
        assert by_name["browser-use"]["registered"] is True
        assert by_name["browser-use"]["active_model"] == "gpt41"
        assert by_name["browser-use"]["model_id"] == "gpt-4.1"
        # In the registry YAML but absent from the sample config.
        assert by_name["skyvern"]["enabled"] is False
        # In the sample config but not in the registry YAML.
        assert by_name["my-custom-agent"]["enabled"] is True
        assert by_name["my-custom-agent"]["registered"] is False

    def test_browsers_and_defaults(self, capsys):
        payload = self._payload(capsys)
        assert "Chrome-Local" in payload["browsers"]
        assert "lexmount" in payload["browsers"]
        assert payload["defaults"]["agent"] == "browser-use"
        assert payload["defaults"]["data"] == "LexBench-Browser"


class TestListCommandText:
    def test_text_output_sections_and_default_marker(self, capsys):
        exit_code = list_command(_parse_args([]), SAMPLE_CONFIG)
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "Benchmarks (--data):" in out
        assert "All*" in out
        assert "Agents (--agent):" in out
        assert "(not enabled in config.yaml)" in out
        assert "(custom)" in out
        assert "Browsers: " in out
        assert "Defaults (config.yaml):" in out

    def test_empty_config_still_lists_registry(self, capsys):
        exit_code = list_command(_parse_args([]), {})
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "LexBench-Browser" in out
        assert "browser-use" in out
