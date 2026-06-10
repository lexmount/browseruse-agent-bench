from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import uuid
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

from browseruse_bench.utils.repo_root import REPO_ROOT

logger = logging.getLogger(__name__)
_EVAL_STRUCTURAL_KEYS = {"api_key", "base_url"}


def _skyvern_temp_database_string() -> str:
    db_name = f"bubench_skyvern_{uuid.uuid4().hex}"
    socket_host = os.getenv("BUBENCH_SKYVERN_POSTGRES_HOST")
    if not socket_host:
        socket_host = "/var/run/postgresql" if Path("/var/run/postgresql").exists() else "/tmp"
    encoded_host = urllib.parse.quote(socket_host, safe="/")
    return f"postgresql+psycopg:///{db_name}?host={encoded_host}"


def load_eval_config(benchmark_name: str) -> dict[str, Any]:
    """Load shared evaluation settings from root config.yaml.

    Returns everything under ``eval`` except the CLI-only structural keys
    (``api_key``, ``base_url``).
    The ``benchmark_name`` argument is kept for call-site compatibility but
    no longer selects a sub-section — all benchmarks share the same config.

    Args:
        benchmark_name: Unused; retained for call-site compatibility.

    Returns:
        Dict[str, Any]: Shared eval settings, or ``{}`` if eval key is absent.
    """
    root_cfg = load_config_file(REPO_ROOT / "config.yaml")
    eval_cfg = root_cfg.get("eval", {})
    return {k: v for k, v in eval_cfg.items() if k not in _EVAL_STRUCTURAL_KEYS}


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand $VAR or ${VAR} references in string values."""
    if isinstance(obj, str):
        return re.sub(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)", lambda m: os.environ.get(m.group(1) or m.group(2), ""), obj)
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(v) for v in obj]
    return obj


def load_config_file(path: Path) -> dict[str, Any]:
    """Load YAML configuration file with environment variable expansion.

    String values of the form $VAR or ${VAR} are replaced with the
    corresponding environment variable. Unset variables are left as-is.
    """
    if not path.exists():
        return {}

    if yaml is None:
        raise ImportError(
            "pyyaml is required to load YAML config files. Install with: pip install pyyaml"
        )

    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    return _expand_env_vars(data)


def load_default_package_config() -> dict[str, Any]:
    """Load default runtime config bundled with the package."""
    if yaml is None:
        raise ImportError(
            "pyyaml is required to load YAML config files. Install with: pip install pyyaml"
        )

    import importlib.resources as resources

    default_path = resources.files("browseruse_bench.config").joinpath("default_config.yaml")
    text = default_path.read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def merge_config_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge config dictionaries (override wins)."""
    merged: dict[str, Any] = deepcopy(base)
    for key, value in override.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = merge_config_dict(existing, value)
        else:
            merged[key] = value
    return merged


def load_data_info(benchmark_path: Path) -> dict[str, Any]:
    """Load data_info.json of the benchmark.

    Args:
        benchmark_path: Path to the dataset directory containing data_info.json.

    Returns:
        Dict[str, Any]: Content of data_info.json, or empty dict if file not found.
    """
    info_file = benchmark_path / "data_info.json"
    if not info_file.exists():
        return {}
    with open(info_file, encoding="utf-8") as f:
        return json.load(f)


def get_default_version(data_info: dict[str, Any]) -> str | None:
    """Get default version (legacy).

    Prioritizes default_version in data_info, otherwise uses the latest version in version_split.

    Args:
        data_info: Content of data_info.json.

    Returns:
        Optional[str]: Default version string, or None if undetermined.
    """
    if "default_version" in data_info:
        return data_info["default_version"]
    if "version_split" in data_info:
        versions = list(data_info["version_split"].keys())
        return sorted(versions)[-1] if versions else None
    return None


def get_default_split(data_info: dict[str, Any]) -> str | None:
    """Get default split.

    Priority:
    1) default_split in data_info
    2) "All" in split keys
    3) First split key (sorted)
    4) Legacy version_split fallback (uses default version, then same rules)

    Args:
        data_info: Content of data_info.json.

    Returns:
        Optional[str]: Default split name, or None if undetermined.
    """
    if "default_split" in data_info:
        return data_info["default_split"]

    if "split" in data_info:
        splits = list(data_info["split"].keys())
        if "All" in splits:
            return "All"
        return sorted(splits)[0] if splits else None

    if "version_split" in data_info:
        version = get_default_version(data_info)
        if not version:
            return None
        splits = list(data_info.get("version_split", {}).get(version, {}).keys())
        if "All" in splits:
            return "All"
        return sorted(splits)[0] if splits else None

    return None


# ============================================================================
# Agent Configuration Functions (merged from agent_config.py)
# ============================================================================


def load_agent_registry(agent: str) -> dict[str, Any]:
    """Load agent registration info from configs/agent_registry.yaml.

    Args:
        agent: Agent name.

    Returns:
        Dict[str, Any]: Registry info (venv, path, entrypoint, supported_benchmarks).
    """
    registry_path = REPO_ROOT / "configs" / "agent_registry.yaml"
    all_registries = load_config_file(registry_path)
    return all_registries.get(agent, {})


def _resolve_agent_key(agent: str, agents: dict[str, Any]) -> str:
    """Return the canonical agent key from *agents* dict, matching case-insensitively.

    Falls back to the original *agent* string when no match is found.
    """
    agent_lower = agent.lower()
    for key in agents:
        if key.lower() == agent_lower:
            return key
    return agent


def resolve_agent_entry(agent: str, root_config: dict[str, Any]) -> dict[str, Any]:
    """Resolve agent entry: merges agent_registry.yaml with root config agents section.

    Args:
        agent: Agent name.
        root_config: Root configuration dictionary.

    Returns:
        Dict[str, Any]: Agent entry with registration info.

    Raises:
        SystemExit: When agent does not exist.
    """
    agents = root_config.get("agents", {})
    agent = _resolve_agent_key(agent, agents)
    if agent not in agents:
        raise SystemExit(f"Unknown Agent: {agent}. Options: {', '.join(sorted(agents))}")
    registry = load_agent_registry(agent)
    # Registry fields take precedence for structural info; root config may override
    return {**agents[agent], **registry}


def resolve_agent_inline_config(
    agent: str,
    root_config: dict[str, Any],
    model_name: str | None = None,
    browser_id: str | None = None,
) -> dict[str, Any] | None:
    """Resolve runtime config for an agent/model/browser selection.

    New-style root config resolves as:
    ``agents.<agent> runtime fields + models.<selected_model> + browsers.<selected_browser>``.

    Legacy root config remains supported and resolves as:
    ``agents.<agent>.browser + agents.<agent>.defaults + agents.<agent>.models.<selected_model>``.

    Args:
        agent: Agent name.
        root_config: Root configuration dictionary.
        model_name: Optional explicit model key override.
        browser_id: Optional explicit browser backend key override.

    Returns:
        Dict with runtime params, or None if not using inline model config.
    """
    agents = root_config.get("agents", {})
    agent = _resolve_agent_key(agent, agents)
    agent_entry = agents.get(agent, {})

    structural_agent_keys = {
        "active_model",
        "active_browser",
        "models",
        "browser",
        "defaults",
    }
    agent_defaults = {
        key: value
        for key, value in agent_entry.items()
        if key not in structural_agent_keys
    }
    defaults = {**agent_defaults, **(agent_entry.get("defaults", {}) or {})}
    shared_models = root_config.get("models", {})
    shared_browsers = root_config.get("browsers", {})
    if isinstance(shared_models, dict) and isinstance(shared_browsers, dict) and shared_models:
        active_model = model_name or agent_entry.get("active_model") or root_config.get("default", {}).get("model")
        active_browser = (
            browser_id
            or agent_entry.get("active_browser")
            or root_config.get("default", {}).get("browser")
            or root_config.get("default", {}).get("browser_id")
        )
        if not (active_model and active_model in shared_models):
            return None
        model_cfg = shared_models[active_model] or {}
        browser_cfg = shared_browsers.get(active_browser, {}) if active_browser else {}
        if active_browser and active_browser not in shared_browsers:
            browser_cfg = {"browser_id": active_browser}
        return {**defaults, **model_cfg, **browser_cfg}

    active_model = model_name or agent_entry.get("active_model")
    models = agent_entry.get("models", {})
    if not (active_model and models and active_model in models):
        return None
    browser = agent_entry.get("browser", {})
    if browser_id:
        browser = {**browser, "browser_id": browser_id}
    return {**browser, **defaults, **models[active_model]}


def resolve_output_model_id(agent_name: str, agent_cfg: dict[str, Any]) -> str | None:
    """Extract the model_id that determines the per-run output subdirectory.

    Keeps the run-path output layout (experiments/{benchmark}/{split}/{agent}/{model_id})
    aligned between `bubench run` (which writes) and `bubench eval` (which reads).

    For Skyvern, legacy lowercase `openai_compatible_*` keys are aliased onto the
    short names via :func:`canonicalize_skyvern_model_name` (one-shot
    DeprecationWarning), then resolution prefers `model_id`, falling through to
    `engine` when neither is set.

    The UPPERCASE lookups (`MODEL_ID`, `OPENAI_COMPATIBLE_MODEL_NAME`, `ENGINE`) are
    intentional legacy tolerance, not a deprecation target — unlike the AgentBay
    config keys which were renamed to snake_case in this release. Historically some
    Skyvern configs and external callers pass UPPERCASE keys; keeping both is
    cheap and avoids breaking silent assumptions in downstream code. Revisit only
    when a broader config-key normalization happens.
    """
    if agent_name == "skyvern":
        # Alias legacy lowercase `openai_compatible_*` onto short keys before
        # reading, so `--dry-run` surfaces the DeprecationWarning the same way a
        # real launch does (legacy-config users learn about the rename before
        # waiting on a real task).
        canonicalize_skyvern_model_name(agent_cfg)
        return (
            agent_cfg.get("model_id")
            or agent_cfg.get("MODEL_ID")
            or agent_cfg.get("OPENAI_COMPATIBLE_MODEL_NAME")
            or agent_cfg.get("engine")
            or agent_cfg.get("ENGINE")
        )
    return agent_cfg.get("model_id") or agent_cfg.get("MODEL_ID")


def load_agent_config_from_path(config_path: Path | None) -> dict[str, Any]:
    """Load runtime agent config from an internal temp-file path.

    Used by the runner subprocess to read the JSON snapshot of the inline
    agent config produced by the parent CLI. Not a user-facing config loader.
    """
    if config_path is None:
        return {}
    config = load_config_file(config_path)
    return {
        key.lower() if isinstance(key, str) else key: value
        for key, value in config.items()
    }


# Legacy -> new key map for Skyvern config normalization. Legacy keys are the old
# `openai_compatible_*` names that mirrored Skyvern's env-var contract; the new keys
# are the short, agent-agnostic names used by browser-use / agent-tars etc.
#
# Each legacy key still resolves with a ``DeprecationWarning``. After the first
# call, the in-place rename means subsequent calls on the same dict are no-ops,
# so users see at most one warning per legacy key per process in practice.
_SKYVERN_LEGACY_KEY_RENAMES = {
    "openai_compatible_model_name": "model_id",
    "openai_compatible_api_key": "api_key",
    "openai_compatible_api_base": "base_url",
    "openai_compatible_max_tokens": "max_tokens",
    "openai_compatible_temperature": "temperature",
    "openai_compatible_supports_vision": "supports_vision",
    "openai_compatible_request_timeout": "request_timeout",
}


def canonicalize_skyvern_model_name(agent_config: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy Skyvern per-model keys to the short, agent-agnostic names.

    Historically Skyvern configs used ``openai_compatible_*`` — matching Skyvern's
    upstream env-var names. Every other agent in this repo uses ``model_id``,
    ``api_key``, ``base_url`` etc.; Skyvern now follows the same convention.

    See ``_SKYVERN_LEGACY_KEY_RENAMES`` for the rename table. Each legacy key still
    resolves with a one-shot ``DeprecationWarning``. Mutates ``agent_config`` in
    place and returns it for convenience.
    """
    for legacy, new in _SKYVERN_LEGACY_KEY_RENAMES.items():
        if new not in agent_config and legacy in agent_config:
            warnings.warn(
                f"agents.skyvern.*.{legacy} is deprecated; rename to `{new}` "
                "to match every other agent. Legacy key still honored.",
                DeprecationWarning,
                stacklevel=2,
            )
            agent_config[new] = agent_config[legacy]
    return agent_config


def apply_skyvern_env(agent_config: dict[str, Any], env: dict[str, str]) -> None:
    """Apply Skyvern-specific environment variables from agent config.

    Args:
        agent_config: Agent configuration dictionary.
        env: Environment dictionary to update.
    """
    canonicalize_skyvern_model_name(agent_config)
    key_map = {
        "enable_openai_compatible": "ENABLE_OPENAI_COMPATIBLE",
        "api_key": "OPENAI_COMPATIBLE_API_KEY",
        "base_url": "OPENAI_COMPATIBLE_API_BASE",
        "model_id": "OPENAI_COMPATIBLE_MODEL_NAME",
        "max_tokens": "OPENAI_COMPATIBLE_MAX_TOKENS",
        "temperature": "OPENAI_COMPATIBLE_TEMPERATURE",
        "supports_vision": "OPENAI_COMPATIBLE_SUPPORTS_VISION",
        "llm_key": "LLM_KEY",
        "skyvern_api_key": "SKYVERN_API_KEY",
        "database_string": "DATABASE_STRING",
    }
    for config_key, env_key in key_map.items():
        if config_key in agent_config and agent_config[config_key] is not None:
            value = agent_config[config_key]
            if config_key == "database_string" and value == "":
                env.pop(env_key, None)
            elif isinstance(value, bool):
                env[env_key] = "true" if value else "false"
            else:
                env[env_key] = str(value)

    if agent_config.get("enable_openai_compatible") and not env.get("LLM_KEY"):
        env["LLM_KEY"] = "OPENAI_COMPATIBLE"

    if agent_config.get("enable_openai_compatible") and "database_string" not in agent_config:
        env["DATABASE_STRING"] = _skyvern_temp_database_string()
