"""Lightweight smoke tests for bubench login CLI helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from browseruse_bench.browsers import login_contexts as lc
from browseruse_bench.cli import login as login_cli


@pytest.fixture
def tmp_index(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "index.json"
    monkeypatch.setattr(lc, "INDEX_PATH", path)
    return path


def test_print_site_registry_runs_without_index(
    tmp_index: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`bubench login add help` must not crash when no contexts are saved.

    Pins a regression caught in review: the call inside ``_print_site_registry``
    used the old ``get_by_site`` symbol which got removed when the file
    switched to profile-aware imports.
    """
    login_cli._print_site_registry()
    out = capsys.readouterr().out
    assert "Built-in site registry" in out
    assert "Usage: bubench login add" in out


def test_print_site_registry_marks_registered_sites(
    tmp_index: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sites with at least one saved profile show the registered marker."""
    sample_site = next(iter(lc.SITE_REGISTRY))
    lc.upsert_profile(sample_site, "zh", {"context_id": "ctx_zh"})

    login_cli._print_site_registry()
    out = capsys.readouterr().out

    # The line for our seeded site should carry the ✓ marker.
    matching = [line for line in out.splitlines() if line.strip().startswith("✓") and sample_site in line]
    assert matching, f"expected a registered-site row for {sample_site!r} in:\n{out}"


def _make_args(**overrides) -> object:
    """Argparse-shaped namespace stub for _delete_remote_entry tests."""
    import argparse
    return argparse.Namespace(
        api_key=None, project_id=None, base_url=None, verify_ssl=None, **overrides,
    )


def test_profile_keys_for_run_reads_flat_lexmount_profiles() -> None:
    """bubench login add should discover profiles from new-style browsers.lexmount config."""
    args = _make_args(profile=None)
    cfg = {
        "browsers": {
            "lexmount": {
                "browser_id": "lexmount",
                "lexmount_profiles": {
                    "zh": {"api_key": "K-zh"},
                    "en": {"api_key": "K-en"},
                },
            }
        }
    }

    assert login_cli._profile_keys_for_run(args, cfg) == ["zh", "en"]


def test_resolve_creds_uses_flat_lexmount_official_proxy_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lexmount_official_proxy in browsers.lexmount flows into login session creation config."""
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_API", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.delenv("LEXMOUNT_OFFICIAL_PROXY", raising=False)
    args = _make_args()
    cfg = {
        "browsers": {
            "lexmount": {
                "browser_id": "lexmount",
                "lexmount_api_key": "K-flat",
                "lexmount_project_id": "P-flat",
                "lexmount_base_url": "https://flat.example",
                "lexmount_official_proxy": True,
            }
        }
    }

    creds = login_cli._resolve_creds_for_profile(args, cfg, profile_key=None)

    assert creds == {
        "api_key": "K-flat",
        "project_id": "P-flat",
        "base_url": "https://flat.example",
        "verify_ssl": True,
        "official_proxy": True,
    }


def test_resolve_creds_official_proxy_defaults_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """official_proxy defaults to false when config and env do not opt in."""
    monkeypatch.delenv("LEXMOUNT_OFFICIAL_PROXY", raising=False)
    args = _make_args()
    cfg = {
        "browsers": {
            "lexmount": {
                "browser_id": "lexmount",
                "lexmount_api_key": "K-flat",
                "lexmount_project_id": "P-flat",
            }
        }
    }

    creds = login_cli._resolve_creds_for_profile(args, cfg, profile_key=None)

    assert creds is not None
    assert creds["official_proxy"] is False


def test_login_one_profile_passes_official_proxy_to_session_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The login flow passes the resolved official_proxy value into Lexmount sessions.create."""
    captured: dict = {}

    class _Context:
        id = "ctx_login"

    class _Session:
        session_id = "session_login"
        id = session_id
        inspect_url = "https://inspect.example"

        def close(self) -> None:
            captured["closed"] = True

    class _Contexts:
        def create(self, metadata):
            captured["metadata"] = metadata
            return _Context()

    class _Sessions:
        def create(self, **kwargs):
            captured["session_create"] = kwargs
            return _Session()

        def delete(self, session_id):
            captured["deleted_session"] = session_id

    class _Client:
        contexts = _Contexts()
        sessions = _Sessions()

    monkeypatch.setattr(login_cli, "_build_client", lambda *args: _Client())
    monkeypatch.setattr(login_cli, "_get_page_target", lambda client, session: None)
    monkeypatch.setattr(login_cli, "_wait_for_user_login", lambda *args: None)
    monkeypatch.setattr(login_cli, "_persist_login_entry", lambda *args: None)
    args = _make_args(browser_mode="normal")
    cfg = {
        "browsers": {
            "lexmount": {
                "browser_id": "lexmount",
                "lexmount_api_key": "K-flat",
                "lexmount_project_id": "P-flat",
                "lexmount_official_proxy": True,
            }
        }
    }

    rc = login_cli._login_one_profile(
        args,
        cfg,
        site="example",
        website="https://example.com",
        profile_key=None,
    )

    assert rc == 0
    assert captured["session_create"] == {
        "context": {"id": "ctx_login", "mode": "read_write"},
        "browser_mode": "normal",
        "official_proxy": True,
    }


def test_delete_remote_entry_uses_per_profile_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`bubench login remove --delete-remote` for a profile must auth against
    that profile's endpoint, not the global LEXMOUNT_API_KEY.

    Pins a regression caught in review: a multi-profile remove was sending
    every delete to the legacy/default endpoint and stranding both local and
    remote entries on the non-default side.
    """
    captured: dict = {}

    def fake_build_client(api_key, project_id, base_url, verify_ssl):
        captured["api_key"] = api_key
        captured["project_id"] = project_id
        captured["base_url"] = base_url
        captured["verify_ssl"] = verify_ssl

        class _Contexts:
            def delete(self, ctx_id):
                captured["deleted_ctx_id"] = ctx_id

        class _Client:
            contexts = _Contexts()

        return _Client()

    monkeypatch.setattr(login_cli, "_build_client", fake_build_client)
    # Profile-specific creds: only zh's vars set; legacy LEXMOUNT_API_KEY left
    # unset so the test fails loudly if the resolver falls through.
    monkeypatch.delenv("LEXMOUNT_API_KEY", raising=False)
    monkeypatch.delenv("LEXMOUNT_API", raising=False)
    monkeypatch.delenv("LEXMOUNT_PROJECT_ID", raising=False)
    monkeypatch.delenv("LEXMOUNT_BASE_URL", raising=False)
    monkeypatch.setenv("LEXMOUNT_API_KEY_ZH", "K-zh")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID_ZH", "P-zh")
    monkeypatch.setenv("LEXMOUNT_BASE_URL_ZH", "https://zh.example")

    entry = {
        "context_id": "ctx_zh_999",
        "website": "https://www.xiaohongshu.com",
        "base_url": "https://zh.example",
        "verify_ssl": True,
    }
    rc = login_cli._delete_remote_entry(_make_args(), entry, profile_key="zh", _config={})

    assert rc == 0
    assert captured["api_key"] == "K-zh"
    assert captured["project_id"] == "P-zh"
    assert captured["base_url"] == "https://zh.example"
    assert captured["deleted_ctx_id"] == "ctx_zh_999"


def test_delete_remote_entry_default_profile_falls_back_to_legacy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy `_default` entry continues to authenticate with the legacy env vars."""
    captured: dict = {}

    def fake_build_client(api_key, project_id, base_url, verify_ssl):
        captured.update(api_key=api_key, project_id=project_id, base_url=base_url)

        class _Contexts:
            def delete(self, ctx_id):
                captured["deleted_ctx_id"] = ctx_id

        class _Client:
            contexts = _Contexts()

        return _Client()

    monkeypatch.setattr(login_cli, "_build_client", fake_build_client)
    monkeypatch.setenv("LEXMOUNT_API_KEY", "K-legacy")
    monkeypatch.setenv("LEXMOUNT_PROJECT_ID", "P-legacy")
    monkeypatch.setenv("LEXMOUNT_BASE_URL", "https://legacy.example")

    entry = {"context_id": "ctx_legacy", "base_url": "https://legacy.example"}
    rc = login_cli._delete_remote_entry(
        _make_args(), entry, profile_key=lc.DEFAULT_PROFILE_KEY, _config={},
    )

    assert rc == 0
    assert captured["api_key"] == "K-legacy"
    assert captured["project_id"] == "P-legacy"
    assert captured["deleted_ctx_id"] == "ctx_legacy"
