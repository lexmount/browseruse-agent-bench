"""Tests for browseruse_bench.utils.logger handler hygiene.

Regression history: setup_logger() used to attach a fresh console handler and
its script-log file handler to the root logger on every call. Every CLI
submodule calls setup_logger at import time, so root accumulated ~10 console
handlers (each propagated line printed that many times) and one script-log
file per CLI module (each file receiving every other command's output).
Console handlers are now deduped on root via a marker attribute, and file
handlers are attached only at command execution time via add_file_handler /
add_script_log_handler.
"""

from __future__ import annotations

import logging

import pytest

from browseruse_bench.utils.logger import (
    _CONSOLE_HANDLER_ATTR,
    add_file_handler,
    add_script_log_handler,
    setup_logger,
)

_CLI_LOGGER_NAMES = (
    "bubench",
    "run",
    "eval",
    "skills",
    "submit",
    "leaderboard",
    "login",
)

# Loggers mutated during these tests: the CLI-style named loggers, the
# propagation probe, and the third-party loggers whose levels
# _configure_third_party_loggers adjusts.
_TOUCHED_LOGGER_NAMES = _CLI_LOGGER_NAMES + (
    "browseruse_bench.some_module",
    "httpx",
    "httpcore",
    "openai",
    "openai._base_client",
)

_FILE_HANDLER_SLOT_ATTRS = ("_run_log_file_handler", "_script_log_file_handler")


def _close_new_file_handlers(
    loggers: list[logging.Logger], saved_ids: set[int]
) -> None:
    """Close file handlers created during a test so no fd leaks."""
    current = [handler for lg in loggers for handler in lg.handlers]
    for handler in current:
        if id(handler) not in saved_ids and isinstance(handler, logging.FileHandler):
            handler.close()


_MISSING = object()


def _save_slot_attrs(lg: logging.Logger) -> dict[str, object]:
    return {
        attr: getattr(lg, attr, _MISSING) for attr in _FILE_HANDLER_SLOT_ATTRS
    }


def _restore_slot_attrs(lg: logging.Logger, attrs: dict[str, object]) -> None:
    for attr, value in attrs.items():
        if value is not _MISSING:
            setattr(lg, attr, value)
        elif hasattr(lg, attr):
            delattr(lg, attr)


@pytest.fixture
def clean_root_logger():
    """Detach all root handlers for the test; restore root and every logger
    the tests touch afterwards so no global logging state leaks."""
    root = logging.getLogger()
    touched = [logging.getLogger(name) for name in _TOUCHED_LOGGER_NAMES]
    saved_root = (root.handlers[:], root.level)
    saved = [
        (lg, lg.handlers[:], lg.level, lg.propagate, _save_slot_attrs(lg))
        for lg in touched
    ]
    saved_ids = {id(handler) for handler in saved_root[0]}
    for _, handlers, _, _, _ in saved:
        saved_ids.update(id(handler) for handler in handlers)
    root.handlers[:] = []
    yield root
    _close_new_file_handlers([root, *touched], saved_ids)
    root.handlers[:] = saved_root[0]
    root.setLevel(saved_root[1])
    for lg, handlers, level, propagate, slot_attrs in saved:
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate
        _restore_slot_attrs(lg, slot_attrs)


def _console_handlers(root: logging.Logger) -> list[logging.Handler]:
    # Count only handlers created by setup_logger; pytest's logging plugin
    # injects its own StreamHandler subclasses into root during tests.
    return [h for h in root.handlers if getattr(h, _CONSOLE_HANDLER_ATTR, False)]


def _file_handlers(lg: logging.Logger) -> list[logging.Handler]:
    return [h for h in lg.handlers if isinstance(h, logging.FileHandler)]


def test_root_gets_at_most_one_console_handler(clean_root_logger):
    """Repeated setup_logger calls (one per CLI submodule) must not stack
    console handlers on the root logger."""
    for name in _CLI_LOGGER_NAMES:
        setup_logger(name)

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


def test_setup_logger_never_attaches_file_handlers(clean_root_logger):
    """Import-time setup_logger must not create or attach file handlers;
    files are an execution-time concern (add_file_handler)."""
    logger = setup_logger("run")

    assert not _file_handlers(logger)
    assert not _file_handlers(clean_root_logger)


def test_script_log_captures_root_propagated_lines(clean_root_logger, tmp_path):
    """The active command's script log must receive both the command logger's
    own lines and root-propagated module-level lines."""
    logger = setup_logger("run")
    handler = add_script_log_handler(logger, tmp_path, "run")

    logging.getLogger("browseruse_bench.some_module").info("propagated-line")
    logger.info("own-line")
    handler.flush()

    content = next((tmp_path / "run").iterdir()).read_text()
    assert "propagated-line" in content
    assert "own-line" in content


def test_script_log_and_run_log_slots_replace_independently(
    clean_root_logger, tmp_path
):
    """Re-attaching the script log must replace only the script-log slot;
    the experiment run.log handler stays open and attached."""
    logger = setup_logger("run")
    first_script = add_script_log_handler(logger, tmp_path, "run")
    run_log = add_file_handler(logger, tmp_path / "run.log")

    second_script = add_script_log_handler(logger, tmp_path, "run")

    assert first_script.stream is None or first_script.stream.closed
    assert run_log.stream is not None and not run_log.stream.closed
    assert second_script in clean_root_logger.handlers
    assert run_log in clean_root_logger.handlers


def test_script_log_failure_degrades_to_console_only(clean_root_logger, tmp_path):
    """An unwritable script-log location must not abort the command: the
    helper warns and returns None, console logging keeps working."""
    logger = setup_logger("run")
    (tmp_path / "run").write_text("blocks the log directory")

    handler = add_script_log_handler(logger, tmp_path, "run")

    assert handler is None
    assert not _file_handlers(logger)


def test_add_file_handler_still_captures_root_traffic(clean_root_logger, tmp_path):
    """Execution-time run.log capture via add_file_handler(also_root=True)
    must keep receiving root-propagated module lines."""
    logger = setup_logger("run")
    handler = add_file_handler(logger, tmp_path / "run.log")

    logging.getLogger("browseruse_bench.some_module").info("captured-line")
    handler.flush()

    assert "captured-line" in (tmp_path / "run.log").read_text()
