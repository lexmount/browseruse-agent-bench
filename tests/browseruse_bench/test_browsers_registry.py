"""Tests for browseruse_bench.browsers.registry backend lookup."""

from __future__ import annotations

import pytest

from browseruse_bench.browsers import registry as browsers_registry


@pytest.fixture()
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> object:
    """Register a single fake backend without importing provider SDKs."""
    sentinel = object()
    monkeypatch.setattr(
        browsers_registry, "_BACKEND_FACTORIES", {"Chrome-Local": lambda: sentinel}
    )
    monkeypatch.setattr(browsers_registry, "_DEFAULTS_REGISTERED", True)
    return sentinel


def test_get_backend_exact_match(fake_backend: object) -> None:
    assert browsers_registry.get_backend("Chrome-Local") is fake_backend


def test_get_backend_case_insensitive_match(fake_backend: object) -> None:
    assert browsers_registry.get_backend("chrome-local") is fake_backend
    assert browsers_registry.get_backend("CHROME-LOCAL") is fake_backend


def test_get_backend_unknown_raises(fake_backend: object) -> None:
    with pytest.raises(ValueError, match="Unknown browser backend"):
        browsers_registry.get_backend("no-such-backend")


def test_canonical_browser_id(fake_backend: object) -> None:
    assert browsers_registry.canonical_browser_id("chrome-local") == "Chrome-Local"
    assert browsers_registry.canonical_browser_id("Chrome-Local") == "Chrome-Local"
    assert browsers_registry.canonical_browser_id("no-such-backend") == "no-such-backend"


def test_canonical_browser_id_prefers_exact_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        browsers_registry,
        "_BACKEND_FACTORIES",
        {"Chrome-Local": lambda: None, "chrome-local": lambda: None},
    )
    monkeypatch.setattr(browsers_registry, "_DEFAULTS_REGISTERED", True)
    assert browsers_registry.canonical_browser_id("chrome-local") == "chrome-local"
    assert browsers_registry.canonical_browser_id("Chrome-Local") == "Chrome-Local"
