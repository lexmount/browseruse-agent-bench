from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lexbench_sdk import LexbenchClient

from browseruse_bench.utils import (
    REPO_ROOT,
    handle_cli_errors,
    load_config_file,
    load_data_info,
    load_env_file,
    normalize_agent_name,
    normalize_benchmark_name,
    normalize_split_name,
    resolve_agent_inline_config,
    setup_logger,
)

CONFIG_PATH = REPO_ROOT / "config.yaml"

# Setup logger
logger = setup_logger("submit")

_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def load_submit_env() -> None:
    """Load submit environment variables.

    Priority:
    1. Current working directory `.env`
    2. Repository root `.env` fallback
    """
    # python-dotenv keeps existing variables by default, so
    # current working directory values stay higher priority.
    load_env_file(Path.cwd() / ".env")
    load_env_file(REPO_ROOT / ".env")


def configure_submit_parser(parser: argparse.ArgumentParser, config: dict[str, Any]) -> None:
    """Configure arguments for the submit command."""
    parser.description = (
        "Submit a job to LexBench Server. "
        "Reads LexBench connection settings from .env / environment, "
        "and can also reuse ~/.config/lexbench/credentials.json from `lexbench login`."
    )
    parser.add_argument("--agent", default=config.get("default", {}).get("agent", "Agent-TARS"))
    parser.add_argument("--data", default=config.get("default", {}).get("data") or config.get("default", {}).get("benchmark", "Online-Mind2Web"))
    parser.add_argument(
        "--split",
        default=None,
        help="Dataset split sent to LexBench (defaults to data_info.json's default_split, falling back to 'All')",
    )
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=None,
        help=(
            "Optional path to an alternate root-config YAML (same shape as the repo "
            "config.yaml). The agent runtime config is resolved from that file via "
            "agents.<agent>.models. Defaults to cwd/config.yaml, then repo root."
        ),
    )
    parser.add_argument("--run-name", default=None, help="Optional LexBench job name")
    parser.add_argument("--version", default=None, help="Optional benchmark version")
    parser.add_argument(
        "--mode",
        choices=["first_n", "sample_n", "all"],
        default="all",
        help="Task selection mode supported by LexBench Server (default: all)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Task count for first_n / sample_n (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Per-task timeout (seconds)",
    )
    parser.add_argument(
        "--skip-completed",
        action="store_true",
        help="Ask LexBench to skip completed tasks",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create/validate the LexBench job without actually running tasks",
    )



def _pick_first_str(config: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed
    return None



def _pick_bool(config: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = config.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in _TRUE_VALUES:
                return True
            if normalized in _FALSE_VALUES:
                return False
    return None



def _pick_int(config: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = config.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            trimmed = value.strip()
            if not trimmed:
                continue
            try:
                return int(trimmed)
            except ValueError:
                continue
    return None



def _set_if_present(payload: dict[str, Any], key: str, value: str | None) -> None:
    if value:
        payload[key] = value



def _set_bool_if_present(payload: dict[str, Any], key: str, value: bool | None) -> None:
    if value is not None:
        payload[key] = value



def _set_int_if_present(payload: dict[str, Any], key: str, value: int | None) -> None:
    if value is not None:
        payload[key] = value



def _read_bool_value(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    logger.warning(
        "Invalid boolean value %r, falling back to %s",
        raw_value,
        default,
    )
    return default



def _get_namespace_str(args: argparse.Namespace, attr_name: str) -> str | None:
    value = getattr(args, attr_name, None)
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed:
            return trimmed
    return None



def _get_namespace_bool(args: argparse.Namespace, attr_name: str) -> bool | None:
    value = getattr(args, attr_name, None)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _read_bool_value(value, default=False)
    return None



def _get_str_from_args_or_env(
    args: argparse.Namespace,
    attr_name: str,
    env_key: str,
) -> str | None:
    cli_value = _get_namespace_str(args, attr_name)
    if cli_value:
        return cli_value
    env_value = os.getenv(env_key)
    if env_value is None:
        return None
    trimmed = env_value.strip()
    return trimmed or None



def _get_str_from_env(env_key: str) -> str | None:
    env_value = os.getenv(env_key)
    if env_value is None:
        return None
    trimmed = env_value.strip()
    return trimmed or None



def _get_lexbench_credential_path() -> Path:
    return Path.home() / ".config" / "lexbench" / "credentials.json"


@dataclass(frozen=True)
class StoredLexbenchCredentials:
    path: Path
    base_url: str | None
    token: str | None



def _load_stored_lexbench_credentials() -> StoredLexbenchCredentials:
    path = _get_lexbench_credential_path()
    if not path.exists():
        return StoredLexbenchCredentials(path=path, base_url=None, token=None)

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning("Failed to read stored LexBench credentials from %s: %s", path, exc)
        return StoredLexbenchCredentials(path=path, base_url=None, token=None)

    if not isinstance(parsed, dict):
        logger.warning("Invalid stored LexBench credentials format in %s", path)
        return StoredLexbenchCredentials(path=path, base_url=None, token=None)

    raw_base_url = parsed.get("baseUrl")
    raw_token = parsed.get("token")
    base_url = raw_base_url.strip() if isinstance(raw_base_url, str) and raw_base_url.strip() else None
    token = raw_token.strip() if isinstance(raw_token, str) and raw_token.strip() else None
    return StoredLexbenchCredentials(path=path, base_url=base_url, token=token)



def _resolve_env_or_stored(
    env_key: str,
    stored_value: str | None,
) -> tuple[str | None, str]:
    env_value = _get_str_from_env(env_key)
    if env_value:
        return env_value, "env"
    if stored_value:
        return stored_value, "stored"
    return None, "missing"



def _get_bool_from_args_or_env(
    args: argparse.Namespace,
    attr_name: str,
    env_key: str,
    *,
    default: bool = False,
) -> bool:
    cli_value = _get_namespace_bool(args, attr_name)
    if cli_value is not None:
        return cli_value
    return _read_bool_value(os.getenv(env_key), default=default)


@dataclass(frozen=True)
class JobSubmissionOptions:
    base_url: str | None
    api_token: str | None
    base_url_source: str
    api_token_source: str
    credentials_path: Path
    project_id: str | None
    project_benchmark_id: str | None
    ui_language: str | None
    run_name: str | None
    version: str | None
    resume_timestamp: str | None
    force_rerun: bool
    debug: bool
    batch_sequential: bool
    eval_api_key: str | None
    captcha_solver_service: str | None
    captcha_solver_api_key: str | None

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> "JobSubmissionOptions":
        stored_credentials = _load_stored_lexbench_credentials()
        base_url, base_url_source = _resolve_env_or_stored(
            "LEXBENCH_BASE_URL",
            stored_credentials.base_url,
        )
        api_token, api_token_source = _resolve_env_or_stored(
            "LEXBENCH_API_TOKEN",
            stored_credentials.token,
        )
        return cls(
            base_url=base_url,
            api_token=api_token,
            base_url_source=base_url_source,
            api_token_source=api_token_source,
            credentials_path=stored_credentials.path,
            project_id=_get_str_from_env("LEXBENCH_PROJECT_ID"),
            project_benchmark_id=_get_str_from_env("LEXBENCH_PROJECT_BENCHMARK_ID"),
            ui_language=_get_str_from_env("LEXBENCH_UI_LANGUAGE"),
            run_name=_get_namespace_str(args, "run_name"),
            version=_get_namespace_str(args, "version"),
            resume_timestamp=_get_str_from_env("LEXBENCH_RESUME_TIMESTAMP"),
            force_rerun=_get_bool_from_args_or_env(
                args,
                "force_rerun",
                "LEXBENCH_FORCE_RERUN",
            ),
            debug=_get_bool_from_args_or_env(args, "debug", "LEXBENCH_DEBUG"),
            batch_sequential=_get_bool_from_args_or_env(
                args,
                "batch_sequential",
                "LEXBENCH_BATCH_SEQUENTIAL",
            ),
            eval_api_key=_get_str_from_args_or_env(
                args,
                "lexbench_eval_api_key",
                "LEXBENCH_EVAL_API_KEY",
            )
            or _get_str_from_env("OPENAI_API_KEY"),
            captcha_solver_service=_get_str_from_args_or_env(
                args,
                "lexbench_captcha_solver_service",
                "LEXBENCH_CAPTCHA_SOLVER_SERVICE",
            ),
            captcha_solver_api_key=_get_str_from_args_or_env(
                args,
                "lexbench_captcha_solver_api_key",
                "LEXBENCH_CAPTCHA_SOLVER_API_KEY",
            ),
        )



def _pick_first_str_with_env(config: dict[str, Any], *keys: str) -> str | None:
    return _pick_first_str(config, *keys) or next(
        (value for key in keys if (value := _get_str_from_env(key)) is not None),
        None,
    )



def _resolve_agent_api_key(agent_name: str, agent_cfg: dict[str, Any]) -> str | None:
    if agent_name == "browser-use":
        model_type = (_pick_first_str_with_env(agent_cfg, "MODEL_TYPE", "model_type") or "").upper()
        direct_key = _pick_first_str_with_env(agent_cfg, "API_KEY", "api_key")
        browser_use_key = _pick_first_str_with_env(agent_cfg, "BROWSER_USE_API_KEY")
        openai_key = _pick_first_str_with_env(agent_cfg, "OPENAI_API_KEY")
        gemini_key = _pick_first_str_with_env(agent_cfg, "GOOGLE_API_KEY", "GEMINI_API_KEY")
        anthropic_key = _pick_first_str_with_env(agent_cfg, "ANTHROPIC_API_KEY")
        if model_type == "BROWSER_USE":
            return browser_use_key or direct_key or openai_key or gemini_key or anthropic_key
        if model_type in {"OPENAI", "AZURE"}:
            return direct_key or openai_key or browser_use_key or gemini_key or anthropic_key
        if model_type == "GEMINI":
            return direct_key or gemini_key or openai_key or browser_use_key or anthropic_key
        if model_type == "ANTHROPIC":
            return direct_key or anthropic_key or openai_key or gemini_key or browser_use_key
        return direct_key or browser_use_key or openai_key or gemini_key or anthropic_key
    if agent_name == "Agent-TARS":
        return _pick_first_str_with_env(agent_cfg, "MODEL_APIKEY", "OPENAI_API_KEY", "API_KEY", "api_key")
    if agent_name == "skyvern":
        return _pick_first_str_with_env(agent_cfg, "OPENAI_API_KEY", "OPENAI_COMPATIBLE_API_KEY", "api_key")
    return None



def _load_submit_agent_config(agent_name: str, config_path: Path | None) -> dict[str, Any]:
    if config_path is None:
        cwd_root = Path.cwd() / "config.yaml"
        if cwd_root.exists():
            config_path = cwd_root
        else:
            default_root = REPO_ROOT / "config.yaml"
            if not default_root.exists():
                return {}
            config_path = default_root
    elif not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    if not config_path.exists():
        raise SystemExit(f"[FAILED] --agent-config file not found: {config_path}")

    config = load_config_file(config_path)
    if not isinstance(config, dict):
        return {}

    inline_cfg = resolve_agent_inline_config(agent_name, config)
    if isinstance(inline_cfg, dict):
        return inline_cfg
    logger.warning(
        "No inline agent runtime config for '%s' in %s — submitting with empty agent config.",
        agent_name,
        config_path,
    )
    return {}


def _normalize_job_agent_config(
    agent_name: str,
    agent_cfg: dict[str, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    _set_if_present(normalized, "agentApiKey", _resolve_agent_api_key(agent_name, agent_cfg))
    _set_if_present(
        normalized,
        "browserApiKey",
        _pick_first_str_with_env(agent_cfg, "LEXMOUNT_API_KEY", "lexmount_api_key"),
    )
    _set_if_present(
        normalized,
        "browserProjectId",
        _pick_first_str_with_env(agent_cfg, "LEXMOUNT_PROJECT_ID", "lexmount_project_id"),
    )
    _set_if_present(
        normalized,
        "browserBaseUrl",
        _pick_first_str_with_env(agent_cfg, "LEXMOUNT_BASE_URL", "lexmount_base_url"),
    )
    _set_if_present(
        normalized,
        "captchaSolverService",
        _pick_first_str_with_env(agent_cfg, "CAPTCHA_SOLVER_SERVICE"),
    )
    _set_if_present(
        normalized,
        "captchaSolverApiKey",
        _pick_first_str_with_env(agent_cfg, "CAPTCHA_SOLVER_API_KEY"),
    )

    if agent_name == "browser-use":
        _set_if_present(normalized, "modelType", _pick_first_str_with_env(agent_cfg, "MODEL_TYPE", "model_type"))
        _set_if_present(normalized, "modelId", _pick_first_str_with_env(agent_cfg, "MODEL_ID", "model_id"))
        _set_if_present(
            normalized,
            "apiBaseUrl",
            _pick_first_str_with_env(
                agent_cfg,
                "API_BASE_URL",
                "base_url",
                "OPENAI_BASE_URL",
                "GEMINI_BASE_URL",
                "ANTHROPIC_BASE_URL",
            ),
        )
        _set_if_present(
            normalized,
            "gemini3ThinkingLevel",
            _pick_first_str_with_env(agent_cfg, "GEMINI3_THINKING_LEVEL"),
        )
        _set_if_present(normalized, "browserId", _pick_first_str_with_env(agent_cfg, "BROWSER_ID", "browser_id"))
        _set_if_present(
            normalized,
            "lexmountBrowserMode",
            _pick_first_str_with_env(agent_cfg, "LEXMOUNT_BROWSER_MODE", "lexmount_browser_mode"),
        )
        _set_bool_if_present(normalized, "useVision", _pick_bool(agent_cfg, "USE_VISION", "use_vision"))
        _set_int_if_present(normalized, "maxSteps", _pick_int(agent_cfg, "MAX_STEPS", "max_steps"))

    if agent_name == "Agent-TARS":
        _set_if_present(
            normalized,
            "modelProvider",
            _pick_first_str_with_env(agent_cfg, "MODEL_PROVIDER"),
        )
        _set_if_present(normalized, "modelId", _pick_first_str_with_env(agent_cfg, "MODEL_ID", "model_id"))
        _set_if_present(
            normalized,
            "apiBaseUrl",
            _pick_first_str_with_env(agent_cfg, "MODEL_BASEURL", "OPENAI_BASE_URL", "API_BASE_URL", "base_url"),
        )
        _set_if_present(
            normalized,
            "browserControl",
            _pick_first_str_with_env(agent_cfg, "BROWSER_CONTROL"),
        )
        _set_int_if_present(normalized, "maxSteps", _pick_int(agent_cfg, "MAX_STEPS", "max_steps"))

    if agent_name == "skyvern":
        _set_if_present(normalized, "skyvernEngine", _pick_first_str_with_env(agent_cfg, "ENGINE", "engine"))
        _set_if_present(
            normalized,
            "skyvernModelName",
            _pick_first_str_with_env(agent_cfg, "OPENAI_COMPATIBLE_MODEL_NAME", "MODEL_ID", "model_id", "openai_compatible_model_name"),
        )
        _set_if_present(
            normalized,
            "apiBaseUrl",
            _pick_first_str_with_env(
                agent_cfg,
                "OPENAI_COMPATIBLE_BASE_URL",
                "OPENAI_BASE_URL",
                "API_BASE_URL",
                "openai_compatible_api_base",
                "base_url",
            ),
        )
        _set_if_present(normalized, "browserId", _pick_first_str_with_env(agent_cfg, "BROWSER_ID", "browser_id"))
        _set_if_present(
            normalized,
            "lexmountBrowserMode",
            _pick_first_str_with_env(agent_cfg, "LEXMOUNT_BROWSER_MODE", "lexmount_browser_mode"),
        )
        _set_int_if_present(normalized, "maxSteps", _pick_int(agent_cfg, "MAX_STEPS", "max_steps"))

    return normalized



def build_job_submission_payload(
    agent_name: str,
    benchmark_name: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    options = JobSubmissionOptions.from_namespace(args)

    payload: dict[str, Any] = {
        "agentName": agent_name,
        "benchmarkName": benchmark_name,
        "mode": args.mode,
    }

    string_fields: list[tuple[str, str | None]] = [
        ("runName", options.run_name),
        ("version", options.version),
        ("split", args.split),
        ("projectId", options.project_id),
        ("projectBenchmarkId", options.project_benchmark_id),
        ("uiLanguage", options.ui_language),
        ("resumeTimestamp", options.resume_timestamp),
    ]
    for key, value in string_fields:
        _set_if_present(payload, key, value)

    if args.count is not None and args.mode in ("first_n", "sample_n"):
        payload["count"] = args.count
    if args.timeout is not None:
        payload["timeout"] = args.timeout

    bool_fields: list[tuple[str, bool]] = [
        ("skipCompleted", args.skip_completed),
        ("forceRerun", options.force_rerun),
        ("debug", options.debug),
        ("dryRun", args.dry_run),
        ("batchSequential", options.batch_sequential),
    ]
    for key, enabled in bool_fields:
        if enabled:
            payload[key] = True

    agent_cfg = _load_submit_agent_config(agent_name, getattr(args, "agent_config", None))
    if agent_cfg:
        normalized_config = _normalize_job_agent_config(agent_name, agent_cfg)
        if normalized_config:
            payload["config"] = normalized_config

    config_overrides = {
        "evalApiKey": options.eval_api_key,
        "captchaSolverService": options.captcha_solver_service,
        "captchaSolverApiKey": options.captcha_solver_api_key,
    }
    filtered_config = {key: value for key, value in config_overrides.items() if value}
    if filtered_config:
        payload.setdefault("config", {})
        payload["config"].update(filtered_config)

    return payload



def validate_submit_args(args: argparse.Namespace) -> None:
    if args.mode in {"first_n", "sample_n"} and (args.count is None or args.count <= 0):
        raise SystemExit("[FAILED] --count must be a positive integer for first_n / sample_n mode")



def submit_job(
    agent_name: str,
    benchmark_name: str,
    args: argparse.Namespace,
) -> int:
    validate_submit_args(args)
    options = JobSubmissionOptions.from_namespace(args)
    if not options.base_url:
        raise SystemExit(
            "[FAILED] Missing LexBench base URL. Set LEXBENCH_BASE_URL in your "
            "environment or .env, or run `lexbench login` first so bubench can "
            f"reuse {options.credentials_path}."
        )
    if not options.api_token:
        raise SystemExit(
            "[FAILED] Missing LexBench API token. Set LEXBENCH_API_TOKEN in your "
            "environment or .env, or run `lexbench login` first so bubench can "
            f"reuse {options.credentials_path}."
        )

    if options.base_url_source == "stored" or options.api_token_source == "stored":
        logger.info("Using stored LexBench credentials from %s", options.credentials_path)
    if not options.eval_api_key:
        logger.warning(
            "No evaluation API key detected. If your LexBench deployment requires one, "
            "set LEXBENCH_EVAL_API_KEY or OPENAI_API_KEY."
        )

    payload = build_job_submission_payload(agent_name, benchmark_name, args)
    logger.info("Submitting LexBench job to %s", options.base_url)
    client = LexbenchClient(base_url=options.base_url, token=options.api_token)
    result = client.submit_eval_run(cast(Any, payload))
    logger.info(
        "[SUCCESS] Job submitted: run_uuid=%s execution_id=%s",
        result.get("runUuid"),
        result.get("executionId"),
    )
    return 0


def submit_command(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Entry point for the submit subcommand."""
    logger.info("Starting submit command")
    agent_name = normalize_agent_name(args.agent, config)
    benchmark_name = normalize_benchmark_name(args.data)
    if args.split:
        data_info = load_data_info(REPO_ROOT / "browseruse_bench" / "data" / benchmark_name)
        args.split = normalize_split_name(args.split, data_info)
    return submit_job(agent_name, benchmark_name, args)


@handle_cli_errors
def main(argv: list[str] | None = None) -> int:
    load_submit_env()
    config = load_config_file(CONFIG_PATH)
    parser = argparse.ArgumentParser(prog="bubench submit")
    configure_submit_parser(parser, config)
    args, extra = parser.parse_known_args(argv)
    if extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return submit_command(args, config)


if __name__ == "__main__":
    main()
