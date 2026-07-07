"""Tests for browseruse_bench.utils.logger root-handler hygiene.

Regression: setup_logger() used to attach a fresh console handler to the
root logger on every call. Because every CLI submodule calls setup_logger
at import time, the root logger accumulated ~10 console handlers and any
propagating module-level logger printed each line ~10 times.
"""

from __future__ import annotations

import logging

import pytest

from browseruse_bench.utils.logger import (
    _CONSOLE_HANDLER_ATTR,
    _attach_handlers_to_root,
    setup_logger,
)

_CLI_LOGGER_NAMES = ("bubench", "run", "eval", "skills", "submit", "leaderboard")

# Loggers mutated by setup_logger during these tests: the CLI-style named
# loggers, the propagation probe, and the third-party loggers whose levels
# _configure_third_party_loggers adjusts.
_TOUCHED_LOGGER_NAMES = _CLI_LOGGER_NAMES + (
    "browseruse_bench.some_module",
    "httpx",
    "httpcore",
    "openai",
    "openai._base_client",
)


@pytest.fixture
def clean_root_logger():
    """Detach all root handlers for the test; restore root and every logger
    the tests touch afterwards so no global logging state leaks."""
    root = logging.getLogger()
    saved_root = (root.handlers[:], root.level)
    touched = [logging.getLogger(name) for name in _TOUCHED_LOGGER_NAMES]
    saved = [(lg, lg.handlers[:], lg.level, lg.propagate) for lg in touched]
    root.handlers[:] = []
    yield root
    root.handlers[:] = saved_root[0]
    root.setLevel(saved_root[1])
    for lg, handlers, level, propagate in saved:
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate


def _console_handlers(root: logging.Logger) -> list[logging.Handler]:
    # Count only handlers created by setup_logger; pytest's logging plugin
    # injects its own StreamHandler subclasses into root during tests.
    return [h for h in root.handlers if getattr(h, _CONSOLE_HANDLER_ATTR, False)]


def test_root_gets_at_most_one_console_handler(clean_root_logger):
    """Repeated setup_logger calls (one per CLI submodule) must not stack
    console handlers on the root logger."""
    for name in _CLI_LOGGER_NAMES:
        setup_logger(name)

    assert len(_console_handlers(clean_root_logger)) == 1


def test_attach_helper_enforces_invariant_within_one_call(clean_root_logger):
    """Even a single handler list holding several marked console handlers
    must yield exactly one console handler on root."""
    marked = []
    for _ in range(2):
        handler = logging.StreamHandler()
        setattr(handler, _CONSOLE_HANDLER_ATTR, True)
        marked.append(handler)

    _attach_handlers_to_root(marked, logging.INFO)

    assert len(_console_handlers(clean_root_logger)) == 1


def test_setup_logger_is_idempotent_for_same_name(clean_root_logger):
    """Re-configuring the same logger must not leak extra root handlers."""
    setup_logger("bubench")
    setup_logger("bubench")

    assert len(_console_handlers(clean_root_logger)) == 1


def test_propagating_module_logger_emits_line_once(clean_root_logger, capsys):
    """A module-level logger propagating to root must print each line once,
    no matter how many CLI submodules configured their own loggers."""
    for name in ("bubench", "run", "eval", "skills"):
        setup_logger(name)

    module_logger = logging.getLogger("browseruse_bench.some_module")
    module_logger.info("unique-regression-marker")

    captured = capsys.readouterr()
    assert captured.out.count("unique-regression-marker") == 1
