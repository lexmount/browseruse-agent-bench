"""Tests for browseruse_bench.utils.config_loader module."""

import json
from pathlib import Path

import pytest
import yaml

import browseruse_bench.utils.config_loader as config_loader_module
from browseruse_bench.utils import (
    REPO_ROOT,
    get_default_split,
    get_default_version,
    load_config_file,
    load_data_info,
    normalize_agent_name,
    normalize_benchmark_name,
    normalize_split_name,
    resolve_agent_entry,
    resolve_agent_inline_config,
    resolve_dir_name_case_insensitive,
    resolve_split,
)
from browseruse_bench.utils.config_loader import (
    load_eval_config,
    resolve_key_case_insensitive,
)


class TestLoadConfigFile:
    """Tests for load_config_file function."""

    def test_load_yaml_config(self, tmp_path: Path):
        """Test loading a valid YAML config file."""
        config_data = {
            "agent": "browser-use",
            "benchmark": "LexBench-Browser",
            "timeout": 300,
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        result = load_config_file(config_file)

        assert result["agent"] == "browser-use"
        assert result["benchmark"] == "LexBench-Browser"
        assert result["timeout"] == 300

    def test_load_config_file_not_found(self, tmp_path: Path):
        """Test loading non-existent config file."""
        result = load_config_file(tmp_path / "nonexistent.yaml")
        assert result == {}

    def test_load_empty_config_file(self, tmp_path: Path):
        """Test loading empty config file."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        result = load_config_file(config_file)
        assert result == {} or result is None


class TestLoadEvalConfig:
    """Tests for load_eval_config — returns shared eval settings (structural keys excluded)."""

    def _patch(self, monkeypatch: pytest.MonkeyPatch, cfg: dict) -> None:
        monkeypatch.setattr(config_loader_module, "load_config_file", lambda _: cfg)

    def test_returns_shared_eval_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, {
            "eval": {
                "model": "gpt-5.4",   # returned (graders read it)
                "api_key": "sk-x",    # structural: excluded
                "base_url": "https://x",  # structural: excluded
                "temperature": 1.0,   # returned (graders use it, e.g. gpt-5 requires 1.0)
                "max_tokens": 1024,   # returned (graders use it for output token limit)
                "max_tries": 5,       # returned
                "api_max_images": 50, # returned
            }
        })
        result = load_eval_config("AnyBench")
        assert result == {"model": "gpt-5.4", "temperature": 1.0, "max_tokens": 1024, "max_tries": 5, "api_max_images": 50}

    def test_benchmark_name_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, {"eval": {"max_tries": 5}})
        assert load_eval_config("LexBench-Browser") == load_eval_config("BrowseComp")

    def test_returns_empty_dict_when_eval_key_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, {})
        assert load_eval_config("LexBench-Browser") == {}


class TestLoadDataInfo:
    """Tests for load_data_info function."""

    def test_load_data_info_exists(self, tmp_path: Path):
        """Test loading data_info.json when it exists."""
        data_info = {"default_split": "All", "split": {"All": "tasks.json"}}
        (tmp_path / "data_info.json").write_text(json.dumps(data_info))

        result = load_data_info(tmp_path)
        assert result["default_split"] == "All"

    def test_load_data_info_not_exists(self, tmp_path: Path):
        """Test loading data_info.json returns empty dict when not exists."""
        result = load_data_info(tmp_path)
        assert result == {}


class TestGetDefaultSplit:
    """Tests for get_default_split function."""

    def test_get_default_split_explicit(self):
        """Test getting explicit default_split."""
        data_info = {"default_split": "All", "split": {"All": "tasks.json"}}
        result = get_default_split(data_info)
        assert result == "All"

    def test_get_default_split_prefers_all(self):
        """Test getting default split preferring All."""
        data_info = {"split": {"All": "tasks.json", "L1": "l1.json"}}
        result = get_default_split(data_info)
        assert result == "All"

    def test_get_default_split_sorted_fallback(self):
        """Test getting default split from sorted keys when All is missing."""
        data_info = {"split": {"B": "b.json", "A": "a.json"}}
        result = get_default_split(data_info)
        assert result == "A"

    def test_get_default_split_legacy_version_split(self):
        """Test getting default split from legacy version_split."""
        data_info = {
            "default_version": "20251230",
            "version_split": {"20251230": {"All": "tasks.json"}},
        }
        result = get_default_split(data_info)
        assert result == "All"


class TestGetDefaultVersion:
    """Tests for legacy get_default_version function."""

    def test_get_default_version_explicit(self):
        """Test getting explicit default_version."""
        data_info = {"default_version": "20251230"}
        result = get_default_version(data_info)
        assert result == "20251230"


def _write_runtime_root_config(tmp_path: Path) -> Path:
    runtime_config = tmp_path / "config.yaml"
    runtime_config.write_text(
        (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return runtime_config


def test_resolve_agent_inline_config_uses_explicit_model_override(tmp_path: Path) -> None:
    config = load_config_file(_write_runtime_root_config(tmp_path))

    inline = resolve_agent_inline_config("browser-use", config, "qwen-plus")

    assert inline is not None
    assert inline.get("model_id") == "qwen3.5-plus"
    assert inline.get("browser_id") == "lexmount"


def test_resolve_agent_inline_config_merges_flat_model_and_browser_config() -> None:
    config = {
        "default": {"model": "gpt", "browser": "browserbase"},
        "models": {
            "gpt": {
                "model_id": "gpt-5.4",
                "api_key": "$OPENAI_API_KEY",
                "temperature": 1.0,
            },
        },
        "browsers": {
            "browserbase": {
                "browser_id": "browserbase",
                "browserbase_api_key": "$BROWSERBASE_API_KEY",
                "timeout": 999,
            },
        },
        "agents": {
            "browser-use": {
                "max_steps": 40,
                "timeout": 600,
            },
        },
    }

    inline = resolve_agent_inline_config("browser-use", config)

    assert inline == {
        "max_steps": 40,
        "timeout": 999,
        "model_id": "gpt-5.4",
        "api_key": "$OPENAI_API_KEY",
        "temperature": 1.0,
        "browser_id": "browserbase",
        "browserbase_api_key": "$BROWSERBASE_API_KEY",
    }


def test_resolve_agent_inline_config_uses_top_level_defaults_without_agent_active_keys() -> None:
    config = {
        "default": {"model": "gpt", "browser": "lexmount"},
        "models": {"gpt": {"model_id": "gpt-5.4"}},
        "browsers": {"lexmount": {"browser_id": "lexmount"}},
        "agents": {"browser-use": {"timeout": 600}},
    }

    inline = resolve_agent_inline_config("browser-use", config)

    assert inline == {
        "timeout": 600,
        "model_id": "gpt-5.4",
        "browser_id": "lexmount",
    }


def test_resolve_agent_inline_config_still_accepts_legacy_defaults_key() -> None:
    config = {
        "default": {"model": "gpt", "browser": "lexmount"},
        "models": {"gpt": {"model_id": "gpt-5.4"}},
        "browsers": {"lexmount": {"browser_id": "lexmount"}},
        "agents": {"browser-use": {"timeout": 300, "defaults": {"timeout": 600}}},
    }

    inline = resolve_agent_inline_config("browser-use", config)

    assert inline is not None
    assert inline["timeout"] == 600


def test_resolve_agent_inline_config_flat_browser_override() -> None:
    config = {
        "default": {"model": "gpt", "browser": "lexmount"},
        "models": {"gpt": {"model_id": "gpt-5.4"}},
        "browsers": {
            "lexmount": {"browser_id": "lexmount"},
            "steel": {"browser_id": "steel", "steel_api_key": "$STEEL_API_KEY"},
        },
        "agents": {"browser-use": {"timeout": 600}},
    }

    inline = resolve_agent_inline_config("browser-use", config, browser_id="steel")

    assert inline is not None
    assert inline["browser_id"] == "steel"
    assert inline["steel_api_key"] == "$STEEL_API_KEY"
    assert inline["model_id"] == "gpt-5.4"


def test_resolve_agent_inline_config_legacy_browser_override() -> None:
    config = {
        "agents": {
            "browser-use": {
                "active_model": "gpt",
                "browser": {"browser_id": "lexmount", "lexmount_api_key": "$LEXMOUNT_API_KEY"},
                "defaults": {"timeout": 600},
                "models": {"gpt": {"model_id": "gpt-5.4"}},
            },
        },
    }

    inline = resolve_agent_inline_config("browser-use", config, browser_id="browserbase")

    assert inline is not None
    assert inline["browser_id"] == "browserbase"
    assert inline["lexmount_api_key"] == "$LEXMOUNT_API_KEY"
    assert inline["model_id"] == "gpt-5.4"


class TestCaseInsensitiveNormalization:
    """Tests for case-insensitive CLI parameter normalization."""

    def test_normalize_benchmark_name_matches_directory(self) -> None:
        assert normalize_benchmark_name("lexbench-browser") == "LexBench-Browser"
        assert normalize_benchmark_name("ONLINE-MIND2WEB") == "Online-Mind2Web"
        assert normalize_benchmark_name("LexBench-Browser") == "LexBench-Browser"

    def test_normalize_benchmark_name_unknown_passthrough(self) -> None:
        assert normalize_benchmark_name("no-such-benchmark") == "no-such-benchmark"

    def test_resolve_key_case_insensitive_prefers_exact_match(self) -> None:
        assert resolve_key_case_insensitive("gpt", {"GPT": 1, "gpt": 2}) == "gpt"
        assert resolve_key_case_insensitive("GPT", {"GPT": 1, "gpt": 2}) == "GPT"

    def test_resolve_key_case_insensitive_non_string_passthrough(self) -> None:
        # YAML can parse unquoted keys/values as float or bool; never crash on them.
        assert resolve_key_case_insensitive(4.1, {4.1: {}}) == 4.1
        assert resolve_key_case_insensitive(True, {"gpt": {}}) is True

    def test_resolve_dir_name_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "GPT-5.4").mkdir()
        assert resolve_dir_name_case_insensitive("gpt-5.4", tmp_path) == "GPT-5.4"
        assert resolve_dir_name_case_insensitive("GPT-5.4", tmp_path) == "GPT-5.4"
        assert resolve_dir_name_case_insensitive("missing", tmp_path) == "missing"
        assert resolve_dir_name_case_insensitive("x", tmp_path / "absent") == "x"

    def test_normalize_agent_name_prefers_registry_canonical_casing(self) -> None:
        # The checked-in agent registry defines the canonical names; a config
        # key with divergent casing must not override them (the subprocess
        # registry lookup is exact-match on the code-registered names).
        assert normalize_agent_name("skyvern", {"agents": {"Skyvern": {}}}) == "skyvern"
        assert normalize_agent_name("AGENT-TARS", {}) == "Agent-TARS"
        assert normalize_agent_name("BROWSER-USE", {"agents": {}}) == "browser-use"

    def test_normalize_agent_name_falls_back_to_config_for_custom_agents(self) -> None:
        config = {"agents": {"My-Agent": {}}}
        assert normalize_agent_name("my-agent", config) == "My-Agent"

    def test_normalize_agent_name_unknown_passthrough(self) -> None:
        assert normalize_agent_name("no-such-agent", {"agents": {}}) == "no-such-agent"
        assert normalize_agent_name("no-such-agent", {}) == "no-such-agent"

    def test_normalize_split_name_matches_split_key(self) -> None:
        data_info = {"split": {"All": "tasks.json", "L1": "l1.json"}}
        assert normalize_split_name("all", data_info) == "All"
        assert normalize_split_name("l1", data_info) == "L1"
        assert normalize_split_name("All", data_info) == "All"

    def test_normalize_split_name_legacy_version_split(self) -> None:
        data_info = {
            "default_version": "20251230",
            "version_split": {"20251230": {"All": "tasks.json"}},
        }
        assert normalize_split_name("ALL", data_info) == "All"

    def test_normalize_split_name_unknown_passthrough(self) -> None:
        assert normalize_split_name("nope", {"split": {"All": "tasks.json"}}) == "nope"
        assert normalize_split_name("All", {}) == "All"

    def test_resolve_split_normalizes_or_defaults(self) -> None:
        data_info = {"default_split": "L1", "split": {"All": "t.json", "L1": "l1.json"}}
        assert resolve_split("all", data_info) == "All"
        assert resolve_split(None, data_info) == "L1"
        assert resolve_split(None, {}) == "All"

    def test_resolve_agent_inline_config_tolerates_non_string_model_keys(self) -> None:
        config = {
            "default": {"model": 4.1, "browser": "local"},
            "models": {4.1: {"model_id": "gpt-4.1"}},
            "browsers": {"local": {"browser_id": "local"}},
            "agents": {"browser-use": {}},
        }

        inline = resolve_agent_inline_config("browser-use", config)

        assert inline is not None
        assert inline["model_id"] == "gpt-4.1"

    def test_resolve_agent_inline_config_case_insensitive_model_and_browser(self) -> None:
        config = {
            "default": {"model": "gpt", "browser": "lexmount"},
            "models": {"GPT": {"model_id": "gpt-5.4"}},
            "browsers": {"Lexmount": {"browser_id": "lexmount"}},
            "agents": {"browser-use": {"timeout": 600}},
        }

        inline = resolve_agent_inline_config(
            "BROWSER-USE", config, model_name="gpt", browser_id="LEXMOUNT"
        )

        assert inline == {
            "timeout": 600,
            "model_id": "gpt-5.4",
            "browser_id": "lexmount",
        }

    def test_resolve_agent_inline_config_case_insensitive_legacy_model(self) -> None:
        config = {
            "agents": {
                "browser-use": {
                    "active_model": "gpt",
                    "browser": {"browser_id": "lexmount"},
                    "models": {"GPT": {"model_id": "gpt-5.4"}},
                },
            },
        }

        inline = resolve_agent_inline_config("browser-use", config, model_name="Gpt")

        assert inline is not None
        assert inline["model_id"] == "gpt-5.4"


def test_resolve_agent_entry_uses_registry_venvs_for_builtin_agents(tmp_path: Path) -> None:
    config = load_config_file(_write_runtime_root_config(tmp_path))

    assert resolve_agent_entry("browser-use", config).get("venv") == ".venvs/browser_use"
    assert resolve_agent_entry("skyvern", config).get("venv") == ".venvs/skyvern"
    assert resolve_agent_entry("Agent-TARS", config).get("venv") == ".venvs/agent_tars"
    assert resolve_agent_entry("deepbrowse", config).get("venv") == ".venvs/deepbrowse"


def test_resolve_agent_entry_supported_but_not_enabled_hint() -> None:
    # codex is in the checked-in agent registry but absent from this runtime
    # config's agents section (the stale-server-config case): the error must
    # point at the missing config entry, not claim the agent is unknown.
    config = {"agents": {"browser-use": {}}}
    with pytest.raises(SystemExit, match="not enabled in the runtime config"):
        resolve_agent_entry("codex", config)


def test_resolve_agent_entry_unknown_agent_lists_options() -> None:
    config = {"agents": {"browser-use": {}}}
    with pytest.raises(SystemExit, match="Unknown Agent: no-such-agent"):
        resolve_agent_entry("no-such-agent", config)


class TestSkyvernConfigCanonicalization:
    """Tests for canonicalize_skyvern_model_name + apply_skyvern_env legacy-key handling.

    All 7 `openai_compatible_*` keys were renamed to their short, agent-agnostic forms
    (`model_id`, `api_key`, `base_url`, `max_tokens`, `temperature`, `supports_vision`,
    `request_timeout`). Legacy keys still resolve via one-shot DeprecationWarning.
    """

    # (legacy_key, new_key, sample_value, expected_env_var)
    _RENAME_CASES = [
        ("openai_compatible_model_name", "model_id", "gpt-5.4", "OPENAI_COMPATIBLE_MODEL_NAME"),
        ("openai_compatible_api_key", "api_key", "sk-xyz", "OPENAI_COMPATIBLE_API_KEY"),
        ("openai_compatible_api_base", "base_url", "https://api.example.com/v1", "OPENAI_COMPATIBLE_API_BASE"),
        ("openai_compatible_max_tokens", "max_tokens", 8192, "OPENAI_COMPATIBLE_MAX_TOKENS"),
        ("openai_compatible_temperature", "temperature", 0.5, "OPENAI_COMPATIBLE_TEMPERATURE"),
        ("openai_compatible_supports_vision", "supports_vision", True, "OPENAI_COMPATIBLE_SUPPORTS_VISION"),
        # request_timeout has no env-var mapping — only consumed inside skyvern.py.
        ("openai_compatible_request_timeout", "request_timeout", 900, None),
    ]

    @pytest.mark.parametrize(
        "legacy_key,new_key,sample_value,_expected_env",
        _RENAME_CASES,
        ids=[c[0] for c in _RENAME_CASES],
    )
    def test_legacy_key_copied_to_new_name_with_deprecation_warning(
        self,
        legacy_key: str,
        new_key: str,
        sample_value,
        _expected_env,
    ) -> None:
        """Each legacy key aliases to its new name and emits exactly one DeprecationWarning."""
        import warnings

        cfg = {legacy_key: sample_value}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config_loader_module.canonicalize_skyvern_model_name(cfg)

        assert cfg[new_key] == sample_value
        depwarns = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(depwarns) == 1
        assert legacy_key in str(depwarns[0].message)
        assert new_key in str(depwarns[0].message)

    @pytest.mark.parametrize(
        "_legacy_key,new_key,sample_value,_expected_env",
        _RENAME_CASES,
        ids=[c[1] for c in _RENAME_CASES],
    )
    def test_new_key_passes_through_unchanged(
        self,
        _legacy_key: str,
        new_key: str,
        sample_value,
        _expected_env,
    ) -> None:
        """When the new key is already set, no warning fires and value is unchanged."""
        import warnings

        cfg = {new_key: sample_value}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config_loader_module.canonicalize_skyvern_model_name(cfg)

        assert cfg[new_key] == sample_value
        assert not any(issubclass(w.category, DeprecationWarning) for w in caught)

    def test_new_key_wins_when_both_set(self) -> None:
        """If a user sets both keys, new wins and no warning fires."""
        import warnings

        cfg = {"model_id": "new-id", "openai_compatible_model_name": "old-id"}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config_loader_module.canonicalize_skyvern_model_name(cfg)

        assert cfg["model_id"] == "new-id"
        assert not any(issubclass(w.category, DeprecationWarning) for w in caught)

    @pytest.mark.parametrize(
        "_legacy_key,new_key,sample_value,expected_env",
        [c for c in _RENAME_CASES if c[3] is not None],
        ids=[c[1] for c in _RENAME_CASES if c[3] is not None],
    )
    def test_apply_skyvern_env_routes_new_key_to_env_var(
        self,
        _legacy_key: str,
        new_key: str,
        sample_value,
        expected_env: str,
    ) -> None:
        """Each new key lands in its mapped OPENAI_COMPATIBLE_* env var."""
        env: dict[str, str] = {}
        cfg = {new_key: sample_value, "enable_openai_compatible": True}
        config_loader_module.apply_skyvern_env(cfg, env)
        assert env[expected_env] == (
            "true" if sample_value is True else "false" if sample_value is False else str(sample_value)
        )

    def test_apply_skyvern_env_still_honors_legacy_model_name(self) -> None:
        """Spot-check: legacy `openai_compatible_model_name` still reaches the env var."""
        import warnings

        env: dict[str, str] = {}
        cfg = {"openai_compatible_model_name": "gemini-3.1-pro-preview"}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            config_loader_module.apply_skyvern_env(cfg, env)

        assert env["OPENAI_COMPATIBLE_MODEL_NAME"] == "gemini-3.1-pro-preview"
        assert any(
            issubclass(w.category, DeprecationWarning)
            and "openai_compatible_model_name" in str(w.message)
            for w in caught
        )

    def test_apply_skyvern_env_uses_temp_postgres_db_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BUBENCH_SKYVERN_POSTGRES_HOST", "/var/run/postgresql")
        env = {"DATABASE_STRING": "postgresql+psycopg://old/skyvern"}

        config_loader_module.apply_skyvern_env({"enable_openai_compatible": True}, env)

        assert env["DATABASE_STRING"].startswith("postgresql+psycopg:///bubench_skyvern_")
        assert env["DATABASE_STRING"].endswith("?host=/var/run/postgresql")

    def test_apply_skyvern_env_preserves_explicit_database_string(self) -> None:
        env = {"DATABASE_STRING": "postgresql+psycopg://old/skyvern"}

        config_loader_module.apply_skyvern_env(
            {
                "enable_openai_compatible": True,
                "database_string": "postgresql+psycopg://new/skyvern",
            },
            env,
        )

        assert env["DATABASE_STRING"] == "postgresql+psycopg://new/skyvern"
