"""Unit tests for BaseAgent helper methods."""
from __future__ import annotations

from browseruse_bench.agents.base import BaseAgent

# ---------------------------------------------------------------------------
# Minimal concrete subclass for testing
# ---------------------------------------------------------------------------

class _DummyAgent(BaseAgent):
    name = "dummy"

    def run_task(self, task_info, agent_config, task_workspace):
        raise NotImplementedError


AGENT = _DummyAgent()


# ---------------------------------------------------------------------------
# build_task_prompt
# ---------------------------------------------------------------------------

class TestBuildTaskPrompt:
    def test_default_format_with_url(self):
        task_info = {"task_text": "Find the price", "url": "https://example.com"}
        prompt = AGENT.build_task_prompt(task_info)
        assert "Find the price" in prompt
        assert "https://example.com" in prompt
        assert "Use only" in prompt
        assert "Starting URL" in prompt

    def test_no_url_returns_task_text_only(self):
        task_info = {"task_text": "Do something", "url": ""}
        assert AGENT.build_task_prompt(task_info) == "Do something"

    def test_prompt_key_fallback(self):
        task_info = {"prompt": "Alt key task", "url": "https://x.com"}
        prompt = AGENT.build_task_prompt(task_info)
        assert "Alt key task" in prompt

    def test_prebuilt_prompt_wins_over_rebuild(self):
        """A prompt pre-formatted by cli/run.py (constraints + optional
        site-skills injection) must pass through verbatim — rebuilding from
        task_text would silently drop the injected content."""
        task_info = {
            "task_text": "Find the price",
            "url": "https://example.com",
            "prompt": "Find the price\nUse only ...\n## Site knowledge (pre-collected)\nnotes",
        }
        assert AGENT.build_task_prompt(task_info) == task_info["prompt"]

    def test_custom_template_overrides_prebuilt_prompt(self):
        task_info = {"task_text": "Buy milk", "url": "https://s.com", "prompt": "PREBUILT"}
        prompt = AGENT.build_task_prompt(task_info, template="T: {task_text}")
        assert prompt == "T: Buy milk"

    def test_custom_template(self):
        task_info = {"task_text": "Buy milk", "url": "https://shop.com"}
        prompt = AGENT.build_task_prompt(task_info, template="Task: {task_text} | URL: {url}")
        assert prompt == "Task: Buy milk | URL: https://shop.com"

    def test_multi_site_drops_only_use_constraint(self):
        """When urls has 2+ entries the prompt must list all sites and skip the
        single-site 'Don't go to any other site' constraint (which would
        contradict the multi-site requirement)."""
        task_info = {
            "task_text": "Compare Inception ratings",
            "url": "https://movie.douban.com",
            "urls": ["https://movie.douban.com", "https://imdb.com"],
        }
        prompt = AGENT.build_task_prompt(task_info)
        assert "Compare Inception ratings" in prompt
        assert "https://movie.douban.com" in prompt
        assert "https://imdb.com" in prompt
        assert "Start with https://movie.douban.com" in prompt
        assert "Don't go to any other site" not in prompt
        assert "Only use" not in prompt

    def test_singleton_urls_uses_single_site_format(self):
        """urls=[one_url] keeps the single-site constraint."""
        task_info = {
            "task_text": "Find X",
            "url": "https://example.com",
            "urls": ["https://example.com"],
        }
        prompt = AGENT.build_task_prompt(task_info)
        assert "Use only https://example.com" in prompt
        assert "do not navigate to unrelated third-party sites" in prompt

    def test_missing_urls_falls_back_to_single_site(self):
        """If urls is absent, behaviour matches the single-site format."""
        task_info = {"task_text": "Find X", "url": "https://example.com"}
        prompt = AGENT.build_task_prompt(task_info)
        assert "Use only https://example.com" in prompt
        assert "do not navigate to unrelated third-party sites" in prompt

    def test_single_site_allows_regional_subdomain_variants(self):
        """A single-site task must treat regional/country variants of the same
        site as on-site, so a region redirect (e.g. zalando.com ->
        zalando.co.uk) is not mistaken for an off-site jump and refused."""
        task_info = {"task_text": "Search on Zalando", "url": "https://www.zalando.com"}
        prompt = AGENT.build_task_prompt(task_info)
        assert "Use only https://www.zalando.com" in prompt
        assert "same site" in prompt
        assert "are allowed" in prompt
        # The allowance must be generic, not a hardcoded list of country TLDs.
        assert ".de" not in prompt
        assert ".uk" not in prompt
        assert ".cn" not in prompt

    def test_odysseys_allows_cross_site_navigation(self):
        """Odysseys tasks start from Google but intentionally span sites."""
        task_info = {
            "benchmark_name": "Odysseys",
            "task_text": "Find evidence across Hulu and Wikipedia.",
            "url": "https://www.google.com",
        }
        prompt = AGENT.build_task_prompt(task_info)
        assert "Start from https://www.google.com" in prompt
        assert "You may visit any websites needed" in prompt
        assert "Only use" not in prompt
        assert "Don't go to any other site" not in prompt


# ---------------------------------------------------------------------------
# get_system_prompt
# ---------------------------------------------------------------------------

class TestGetSystemPrompt:
    def test_returns_config_value(self):
        assert AGENT.get_system_prompt({"system_prompt": "custom"}) == "custom"

    def test_falls_back_to_class_default(self):
        class AgentWithDefault(_DummyAgent):
            default_system_prompt = "default prompt"

        agent = AgentWithDefault()
        assert agent.get_system_prompt({}) == "default prompt"
        assert agent.get_system_prompt({"system_prompt": None}) == "default prompt"

    def test_none_when_no_default(self):
        assert AGENT.get_system_prompt({}) is None

    def test_empty_string_is_preserved(self):
        # Explicit "" is not None, so it is returned as-is (not replaced by default)
        class AgentWithDefault(_DummyAgent):
            default_system_prompt = "fallback"

        agent = AgentWithDefault()
        assert agent.get_system_prompt({"system_prompt": ""}) == ""


# ---------------------------------------------------------------------------
# get_model_id
# ---------------------------------------------------------------------------

class TestGetModelId:
    def test_reads_model_id(self):
        assert AGENT.get_model_id({"model_id": "gpt-5"}) == "gpt-5"

    def test_falls_back_to_model(self):
        assert AGENT.get_model_id({"model": "claude-sonnet-4-6"}) == "claude-sonnet-4-6"

    def test_returns_none_when_absent(self):
        assert AGENT.get_model_id({}) is None

    def test_model_id_takes_priority_over_model(self):
        assert AGENT.get_model_id({"model_id": "a", "model": "b"}) == "a"


# ---------------------------------------------------------------------------
# get_timeout
# ---------------------------------------------------------------------------

class TestGetTimeout:
    def test_reads_timeout_seconds(self):
        assert AGENT.get_timeout({"timeout_seconds": 120}) == 120

    def test_reads_timeout(self):
        assert AGENT.get_timeout({"timeout": 60}) == 60

    def test_reads_TIMEOUT(self):
        assert AGENT.get_timeout({"TIMEOUT": 90}) == 90

    def test_priority_order(self):
        # timeout_seconds wins over timeout
        assert AGENT.get_timeout({"timeout_seconds": 10, "timeout": 20}) == 10

    def test_default_when_absent(self):
        assert AGENT.get_timeout({}) == 300
        assert AGENT.get_timeout({}, default=600) == 600

    def test_zero_is_preserved(self):
        # 0 is a valid value, not treated as absent
        assert AGENT.get_timeout({"timeout_seconds": 0}) == 0

    def test_invalid_value_returns_default(self):
        assert AGENT.get_timeout({"timeout": "bad"}) == 300

    def test_string_number_coerced(self):
        assert AGENT.get_timeout({"timeout": "150"}) == 150


# ---------------------------------------------------------------------------
# get_max_steps
# ---------------------------------------------------------------------------

class TestGetMaxSteps:
    def test_reads_max_steps(self):
        assert AGENT.get_max_steps({"max_steps": 25}) == 25

    def test_reads_max_turns(self):
        assert AGENT.get_max_steps({"max_turns": 50}) == 50

    def test_reads_max_iterations(self):
        assert AGENT.get_max_steps({"max_iterations": 100}) == 100

    def test_reads_MAX_STEPS(self):
        assert AGENT.get_max_steps({"MAX_STEPS": 30}) == 30

    def test_priority_order(self):
        assert AGENT.get_max_steps({"max_steps": 5, "max_turns": 10}) == 5

    def test_default_when_absent(self):
        assert AGENT.get_max_steps({}) == 40
        assert AGENT.get_max_steps({}, default=20) == 20

    def test_zero_is_preserved(self):
        assert AGENT.get_max_steps({"max_steps": 0}) == 0

    def test_invalid_value_returns_default(self):
        assert AGENT.get_max_steps({"max_steps": "nan"}) == 40


# ---------------------------------------------------------------------------
# get_api_key / get_base_url
# ---------------------------------------------------------------------------

class TestGetApiKey:
    def test_reads_api_key(self):
        assert AGENT.get_api_key({"api_key": "sk-123"}) == "sk-123"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "env-key")
        assert AGENT.get_api_key({}, env_var="MY_API_KEY") == "env-key"

    def test_none_when_absent_no_env_var(self):
        assert AGENT.get_api_key({}) is None

    def test_config_takes_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "env-key")
        assert AGENT.get_api_key({"api_key": "config-key"}, env_var="MY_KEY") == "config-key"


class TestGetBaseUrl:
    def test_reads_base_url(self):
        assert AGENT.get_base_url({"base_url": "https://api.example.com"}) == "https://api.example.com"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("MY_BASE_URL", "https://env.example.com")
        assert AGENT.get_base_url({}, env_var="MY_BASE_URL") == "https://env.example.com"

    def test_none_when_absent(self):
        assert AGENT.get_base_url({}) is None


# ---------------------------------------------------------------------------
# save_screenshot
# ---------------------------------------------------------------------------

class TestSaveScreenshot:
    def test_saves_valid_screenshot(self, tmp_path):
        # 1x1 white PNG, base64-encoded
        png_1x1 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
        )
        result = AGENT.save_screenshot(png_1x1, 1, tmp_path)
        assert result is True
        assert (tmp_path / "screenshot-1.png").exists()

    def test_creates_directory(self, tmp_path):
        subdir = tmp_path / "trajectory"
        png_1x1 = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwADhQGAWjR9awAAAABJRU5ErkJggg=="
        )
        AGENT.save_screenshot(png_1x1, 2, subdir)
        assert (subdir / "screenshot-2.png").exists()

    def test_empty_data_returns_false(self, tmp_path):
        assert AGENT.save_screenshot("", 1, tmp_path) is False

    def test_invalid_base64_returns_false(self, tmp_path):
        assert AGENT.save_screenshot("!!!not-base64!!!", 1, tmp_path) is False
