from __future__ import annotations

from pathlib import Path

import pytest

from browseruse_bench.cli.run import _resolve_site_skills
from browseruse_bench.utils.site_skills import (
    MIN_MAX_CHARS,
    apply_site_skills,
    build_skills_section,
    match_skill_files,
    task_target_urls,
)


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    root = tmp_path / "domain-skills"
    (root / "bilibili").mkdir(parents=True)
    (root / "bilibili" / "navigation.md").write_text("# bilibili nav", encoding="utf-8")
    (root / "10jqka-com-cn").mkdir()
    (root / "10jqka-com-cn" / "quotes.md").write_text("# ths quotes", encoding="utf-8")
    (root / "job-boards").mkdir()
    (root / "job-boards" / "hosts").write_text("jobs.example.com\nlever.co\n", encoding="utf-8")
    (root / "job-boards" / "search.md").write_text("# job search", encoding="utf-8")
    (root / "empty-site").mkdir()
    return root


class TestMatchSkillFiles:
    def test_full_url_matches_single_label(self, skills_dir: Path) -> None:
        files = match_skill_files("https://www.bilibili.com/video/BV1", skills_dir)
        assert [f.name for f in files] == ["navigation.md"]

    def test_bare_domain_matches(self, skills_dir: Path) -> None:
        files = match_skill_files("bilibili.com", skills_dir)
        assert [f.name for f in files] == ["navigation.md"]

    def test_noisy_dataset_value_matches(self, skills_dir: Path) -> None:
        """Dataset rows carry values like 'www.bilibili.com或其他'; the
        hostname-looking prefix must still match (parity with login routing)."""
        files = match_skill_files("www.bilibili.com或其他", skills_dir)
        assert [f.name for f in files] == ["navigation.md"]

    def test_normalized_dir_name_matches_dotted_host(self, skills_dir: Path) -> None:
        files = match_skill_files("https://10jqka.com.cn/", skills_dir)
        assert [f.name for f in files] == ["quotes.md"]

    def test_hosts_alias_matches(self, skills_dir: Path) -> None:
        files = match_skill_files("https://www.lever.co/postings", skills_dir)
        assert [f.name for f in files] == ["search.md"]

    def test_no_match_returns_empty(self, skills_dir: Path) -> None:
        assert match_skill_files("https://unknown-site.io/", skills_dir) == []

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        assert match_skill_files("https://bilibili.com/", tmp_path / "absent") == []

    def test_max_files_cap(self, tmp_path: Path) -> None:
        root = tmp_path / "domain-skills"
        (root / "example-com").mkdir(parents=True)
        for i in range(15):
            (root / "example-com" / f"s{i:02d}.md").write_text("x", encoding="utf-8")
        assert len(match_skill_files("https://example.com/", root)) == 10


class TestTaskTargetUrls:
    def test_prefers_urls_list(self) -> None:
        task = {"urls": ["https://a.com", "https://b.com"], "url": "https://c.com"}
        assert task_target_urls(task) == ["https://a.com", "https://b.com"]

    def test_falls_back_to_target_website(self) -> None:
        assert task_target_urls({"target_website": "bilibili.com"}) == ["bilibili.com"]

    def test_empty_when_nothing_declared(self) -> None:
        assert task_target_urls({"urls": [], "url": ""}) == []


class TestBuildSkillsSection:
    def test_contains_file_content_and_relative_path(self, skills_dir: Path) -> None:
        files = match_skill_files("bilibili.com", skills_dir)
        section, included, truncated = build_skills_section(files, skills_dir, max_chars=30000)
        assert "## Site knowledge (pre-collected)" in section
        assert "### bilibili/navigation.md" in section
        assert "# bilibili nav" in section
        assert included == files
        assert truncated is False

    def test_truncates_at_max_chars(self, tmp_path: Path) -> None:
        root = tmp_path / "domain-skills"
        (root / "example-com").mkdir(parents=True)
        (root / "example-com" / "big.md").write_text("A" * 5000, encoding="utf-8")
        files = match_skill_files("example.com", root)
        section, included, truncated = build_skills_section(files, root, max_chars=1000)
        assert len(section) <= 1000
        assert "truncated" in section
        assert truncated is True
        assert included == files  # partially included still counts as delivered

    def test_files_beyond_budget_are_not_reported(self, tmp_path: Path) -> None:
        """Files whose content never made it into the section must not be
        listed, or the manifest would claim treatment the agent never saw."""
        root = tmp_path / "domain-skills"
        (root / "example-com").mkdir(parents=True)
        (root / "example-com" / "a-first.md").write_text("A" * 2000, encoding="utf-8")
        (root / "example-com" / "b-second.md").write_text("B" * 2000, encoding="utf-8")
        files = match_skill_files("example.com", root)
        section, included, truncated = build_skills_section(files, root, max_chars=1500)
        assert truncated is True
        assert [f.name for f in included] == ["a-first.md"]
        assert "b-second" not in section


class TestApplySiteSkills:
    def test_appends_to_prompt_and_reports(self, skills_dir: Path) -> None:
        task = {
            "task_id": "t1",
            "prompt": "Find the top video.",
            "urls": ["https://www.bilibili.com/"],
        }
        summary = apply_site_skills([task], skills_dir)
        assert task["prompt"].startswith("Find the top video.")
        assert "# bilibili nav" in task["prompt"]
        assert summary["t1"]["files"] == ["bilibili/navigation.md"]
        assert summary["t1"]["chars"] > 0
        assert summary["t1"]["truncated"] is False

    def test_records_clean_prompt_and_injection_separately(self, skills_dir: Path) -> None:
        task = {
            "task_id": "t1",
            "prompt": "Find the top video.",
            "urls": ["https://www.bilibili.com/"],
        }
        apply_site_skills([task], skills_dir)
        assert task["prompt_base"] == "Find the top video."
        assert task["site_skills"]["files"] == ["bilibili/navigation.md"]
        assert task["site_skills"]["chars"] > 0

    def test_summary_and_task_share_one_record(self, skills_dir: Path) -> None:
        task = {"task_id": "t1", "prompt": "P", "urls": ["https://bilibili.com/"]}
        summary = apply_site_skills([task], skills_dir)
        assert summary["t1"] is task["site_skills"]

    def test_miss_sets_no_injection_fields(self, skills_dir: Path) -> None:
        task = {"task_id": "t2", "prompt": "P", "urls": ["https://unknown-site.io/"]}
        apply_site_skills([task], skills_dir)
        assert "prompt_base" not in task
        assert "site_skills" not in task

    def test_miss_keeps_prompt_untouched(self, skills_dir: Path) -> None:
        task = {"task_id": "t2", "prompt": "P", "urls": ["https://unknown-site.io/"]}
        summary = apply_site_skills([task], skills_dir)
        assert task["prompt"] == "P"
        assert summary["t2"] == {"files": [], "chars": 0, "truncated": False}

    def test_multi_site_union(self, skills_dir: Path) -> None:
        task = {
            "task_id": "t3",
            "prompt": "P",
            "urls": ["https://bilibili.com/", "https://lever.co/"],
        }
        summary = apply_site_skills([task], skills_dir)
        assert summary["t3"]["files"] == ["bilibili/navigation.md", "job-boards/search.md"]


class TestResolveSiteSkills:
    def test_disabled_by_default(self) -> None:
        assert _resolve_site_skills({}, None) is None

    def test_cli_off_overrides_config_on(self, skills_dir: Path) -> None:
        config = {"site_skills": {"enabled": True, "dir": str(skills_dir)}}
        assert _resolve_site_skills(config, "off") is None

    def test_cli_on_with_dir(self, skills_dir: Path) -> None:
        config = {"site_skills": {"dir": str(skills_dir), "max_chars": 5000, "max_files": 3}}
        resolved = _resolve_site_skills(config, "on")
        assert resolved == (skills_dir, 5000, 3)

    def test_null_values_fall_back_to_defaults(self, skills_dir: Path) -> None:
        """A bare `max_chars:` key parses to None; it must fall back, not crash."""
        config = {"site_skills": {"dir": str(skills_dir), "max_chars": None, "max_files": None}}
        skills, max_chars, max_files = _resolve_site_skills(config, "on")
        assert max_chars > 0 and max_files > 0

    def test_max_chars_below_floor_fails(self, skills_dir: Path) -> None:
        config = {"site_skills": {"dir": str(skills_dir), "max_chars": MIN_MAX_CHARS - 1}}
        with pytest.raises(SystemExit):
            _resolve_site_skills(config, "on")

    def test_enabled_without_dir_fails(self) -> None:
        with pytest.raises(SystemExit):
            _resolve_site_skills({"site_skills": {"enabled": True}}, None)

    def test_enabled_with_missing_dir_fails(self, tmp_path: Path) -> None:
        config = {"site_skills": {"dir": str(tmp_path / "absent")}}
        with pytest.raises(SystemExit):
            _resolve_site_skills(config, "on")
