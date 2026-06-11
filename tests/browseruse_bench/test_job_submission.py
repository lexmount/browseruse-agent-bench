from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from browseruse_bench.cli import submit as submit_cli


_ROOT_CONFIG_WITH_GEMINI = """
default:
  agent: browser-use
agents:
  browser-use:
    active_model: gemini
    models:
      gemini:
        model_type: GEMINI
        model_id: test-model
""".strip()


def _make_args() -> argparse.Namespace:
    return argparse.Namespace(
        mode="first_n",
        count=3,
        task_ids=None,
        id=None,
        timeout=120,
        skip_completed=True,
        dry_run=False,
        run_name="nightly-smoke",
        version="v1.4",
        split=None,
        agent_config=None,
    )


@pytest.fixture
def cwd_with_root_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Chdir into a tmp dir with a root config.yaml wired for the browser-use GEMINI model."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(_ROOT_CONFIG_WITH_GEMINI, encoding="utf-8")
    return tmp_path


def test_build_job_submission_payload_maps_common_args(cwd_with_root_config: Path) -> None:
    del cwd_with_root_config
    args = _make_args()
    payload = submit_cli.build_job_submission_payload("browser-use", "LexBench-Browser", args)

    assert payload["agentName"] == "browser-use"
    assert payload["benchmarkName"] == "LexBench-Browser"
    assert payload["mode"] == "first_n"
    assert payload["count"] == 3
    assert payload["timeout"] == 120
    assert payload["skipCompleted"] is True
    assert payload["runName"] == "nightly-smoke"
    assert payload["version"] == "v1.4"
    assert payload["config"]["modelType"] == "GEMINI"


def test_build_job_submission_payload_reads_env_only_submit_settings(
    monkeypatch: pytest.MonkeyPatch,
    cwd_with_root_config: Path,
) -> None:
    del cwd_with_root_config
    args = _make_args()
    monkeypatch.setenv("LEXBENCH_PROJECT_ID", "project-1")
    monkeypatch.setenv("LEXBENCH_PROJECT_BENCHMARK_ID", "benchmark-1")
    monkeypatch.setenv("LEXBENCH_UI_LANGUAGE", "zh")

    payload = submit_cli.build_job_submission_payload("browser-use", "LexBench-Browser", args)

    assert payload["projectId"] == "project-1"
    assert payload["projectBenchmarkId"] == "benchmark-1"
    assert payload["uiLanguage"] == "zh"


def test_submit_job_calls_client(monkeypatch, cwd_with_root_config: Path) -> None:
    del cwd_with_root_config
    args = _make_args()
    monkeypatch.setenv("LEXBENCH_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("LEXBENCH_API_TOKEN", "test-token")

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, base_url: str, token: str) -> None:
            captured["base_url"] = base_url
            captured["token"] = token

        def submit_eval_run(self, payload):  # type: ignore[no-untyped-def]
            captured["payload"] = payload
            return {"runUuid": "run-uuid", "executionId": "exec-1"}

    monkeypatch.setattr(submit_cli, "LexbenchClient", FakeClient)
    code = submit_cli.submit_job("browser-use", "LexBench-Browser", args)

    assert code == 0
    assert captured["base_url"] == "http://localhost:3000"
    assert captured["token"] == "test-token"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["agentName"] == "browser-use"


def test_submit_job_uses_stored_credentials_when_env_missing(
    monkeypatch: pytest.MonkeyPatch,
    cwd_with_root_config: Path,
) -> None:
    tmp_path = cwd_with_root_config
    args = _make_args()
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text(
        '{"baseUrl": "http://localhost:3000", "token": "stored-token"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(submit_cli, "_get_lexbench_credential_path", lambda: credentials_path)

    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, base_url: str, token: str) -> None:
            captured["base_url"] = base_url
            captured["token"] = token

        def submit_eval_run(self, payload):  # type: ignore[no-untyped-def]
            captured["payload"] = payload
            return {"runUuid": "run-uuid", "executionId": "exec-1"}

    monkeypatch.setattr(submit_cli, "LexbenchClient", FakeClient)
    code = submit_cli.submit_job("browser-use", "LexBench-Browser", args)

    assert code == 0
    assert captured["base_url"] == "http://localhost:3000"
    assert captured["token"] == "stored-token"


def test_job_submission_payload_reads_advanced_options_from_env(
    monkeypatch,
    cwd_with_root_config: Path,
) -> None:
    del cwd_with_root_config
    args = _make_args()

    monkeypatch.setenv("LEXBENCH_FORCE_RERUN", "true")
    monkeypatch.setenv("LEXBENCH_DEBUG", "true")
    monkeypatch.setenv("LEXBENCH_BATCH_SEQUENTIAL", "true")
    monkeypatch.setenv("LEXBENCH_EVAL_API_KEY", "eval-key")
    monkeypatch.setenv("LEXBENCH_CAPTCHA_SOLVER_SERVICE", "capsolver")
    monkeypatch.setenv("LEXBENCH_CAPTCHA_SOLVER_API_KEY", "captcha-key")

    payload = submit_cli.build_job_submission_payload("browser-use", "LexBench-Browser", args)

    assert payload["forceRerun"] is True
    assert payload["debug"] is True
    assert payload["batchSequential"] is True
    assert payload["config"]["evalApiKey"] == "eval-key"
    assert payload["config"]["captchaSolverService"] == "capsolver"
    assert payload["config"]["captchaSolverApiKey"] == "captcha-key"


def test_job_submission_payload_reads_agent_and_browser_keys_from_env(
    monkeypatch: pytest.MonkeyPatch,
    cwd_with_root_config: Path,
) -> None:
    del cwd_with_root_config
    args = _make_args()
    monkeypatch.setenv("GOOGLE_API_KEY", "gemini-key")
    monkeypatch.setenv("LEXMOUNT_API_KEY", "lexmount-key")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "lexmount-project")

    payload = submit_cli.build_job_submission_payload("browser-use", "LexBench-Browser", args)

    assert payload["config"]["agentApiKey"] == "gemini-key"
    assert payload["config"]["browserApiKey"] == "lexmount-key"
    assert payload["config"]["browserProjectId"] == "lexmount-project"


def test_job_submission_payload_falls_back_to_openai_key_for_eval_api_key(
    monkeypatch: pytest.MonkeyPatch,
    cwd_with_root_config: Path,
) -> None:
    del cwd_with_root_config
    args = _make_args()
    monkeypatch.setenv("OPENAI_API_KEY", "openai-eval-key")

    payload = submit_cli.build_job_submission_payload("browser-use", "LexBench-Browser", args)

    assert payload["config"]["evalApiKey"] == "openai-eval-key"


def test_submit_parser_keeps_run_name_and_version_only() -> None:
    parser = argparse.ArgumentParser()
    submit_cli.configure_submit_parser(parser, {"default": {}})

    args = parser.parse_args(
        [
            "--agent",
            "browser-use",
            "--data",
            "LexBench-Browser",
            "--run-name",
            "nightly-smoke",
            "--version",
            "v1.4",
        ]
    )

    assert args.run_name == "nightly-smoke"
    assert args.version == "v1.4"

    with pytest.raises(SystemExit):
        parser.parse_args(["--lexbench-base-url", "http://localhost:3000"])


def test_submit_validation_requires_positive_count() -> None:
    args = _make_args()
    args.mode = "sample_n"
    args.count = 0

    with pytest.raises(SystemExit, match="--count must be a positive integer"):
        submit_cli.validate_submit_args(args)


def test_load_submit_agent_config_prefers_cwd_root_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        """
default:
  agent: browser-use
agents:
  browser-use:
    active_model: gpt
    models:
      gpt:
        model_type: OPENAI
        model_id: gpt-4.1
        api_key: $OPENAI_API_KEY
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "cwd-openai-key")

    agent_cfg = submit_cli._load_submit_agent_config("browser-use", None)

    assert agent_cfg["model_type"] == "OPENAI"
    assert agent_cfg["model_id"] == "gpt-4.1"
    assert agent_cfg["api_key"] == "cwd-openai-key"


def test_resolve_submit_config_path_prefers_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("default: {}", encoding="utf-8")
    assert submit_cli._resolve_submit_config_path(None) == tmp_path / "config.yaml"
    explicit = tmp_path / "alt.yaml"
    explicit.write_text("default: {}", encoding="utf-8")
    assert submit_cli._resolve_submit_config_path(Path("alt.yaml")) == explicit


def test_submit_command_normalizes_custom_agent_against_cwd_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Custom agents defined only in the cwd config.yaml resolve case-insensitively."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        """
agents:
  My-Agent:
    active_model: gpt
    models:
      gpt:
        model_id: gpt-4.1
""".strip(),
        encoding="utf-8",
    )
    captured: dict[str, str] = {}

    def fake_submit_job(agent_name: str, benchmark_name: str, args: argparse.Namespace) -> int:
        captured["agent"] = agent_name
        captured["benchmark"] = benchmark_name
        return 0

    monkeypatch.setattr(submit_cli, "submit_job", fake_submit_job)
    args = _make_args()
    args.agent = "my-agent"
    args.data = "lexbench-browser"

    assert submit_cli.submit_command(args, {}) == 0
    assert captured["agent"] == "My-Agent"
    assert captured["benchmark"] == "LexBench-Browser"


def test_load_submit_env_prefers_cwd_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "LEXBENCH_BASE_URL=https://bench.lexmount.com\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LEXBENCH_BASE_URL", raising=False)

    submit_cli.load_submit_env()

    assert os.getenv("LEXBENCH_BASE_URL") == "https://bench.lexmount.com"
