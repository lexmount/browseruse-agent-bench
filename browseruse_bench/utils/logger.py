from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_NOISY_THIRD_PARTY_LOGGERS = (
    "httpx",
    "httpcore",
    "openai",
    "openai._base_client",
)

_FILE_HANDLER_ATTR_TEMPLATE = "_{slot}_file_handler"

_CONSOLE_HANDLER_ATTR = "_browseruse_bench_console_handler"


def _resolve_log_level(level_text: str, default_level: int) -> int:
    """Resolve a text log level into a logging constant."""
    normalized = level_text.strip().upper()
    if not normalized:
        return default_level
    if normalized.isdigit():
        return int(normalized)
    level_value = getattr(logging, normalized, None)
    if isinstance(level_value, int):
        return level_value
    return default_level


def _configure_third_party_loggers() -> None:
    """Reduce noisy third-party logs unless explicitly overridden."""
    configured_level = _resolve_log_level(
        os.getenv("BROWSERUSE_BENCH_THIRD_PARTY_LOG_LEVEL", ""),
        logging.WARNING,
    )
    for logger_name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(logger_name).setLevel(configured_level)


def _make_formatter(format_mode: Optional[str] = None) -> logging.Formatter:
    """Create a log formatter based on the chosen format mode."""
    env_format_mode = os.getenv("BROWSERUSE_BENCH_LOG_FORMAT", "").strip().lower()
    chosen = (format_mode or env_format_mode).strip().lower()
    if chosen == "plain":
        return logging.Formatter("%(message)s")
    return logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def add_file_handler(
    logger_instance: logging.Logger,
    log_file: Path,
    *,
    level: int = logging.INFO,
    format_mode: Optional[str] = None,
    also_root: bool = True,
    slot: str = "run_log",
) -> logging.FileHandler:
    """Dynamically attach a file handler to an existing logger.

    Use this to add a ``run.log`` inside the experiment output directory
    after the directory path is known (i.e. after CLI args are parsed).

    Args:
        logger_instance: The logger to add the handler to.
        log_file: Absolute path to the log file.
        level: Logging level for the file handler.
        format_mode: Formatter mode (``"plain"`` or default structured).
        also_root: If True, also attach the handler to the root logger so
            that logs from all modules are captured.
        slot: Replacement key; a later call with the same slot replaces the
            previous handler, different slots coexist (e.g. the per-command
            script log and the experiment run.log).

    Returns:
        The created ``FileHandler`` (caller can close it later if needed).
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(_make_formatter(format_mode))

    # Remove the previous handler occupying this slot, if any
    slot_attr = _FILE_HANDLER_ATTR_TEMPLATE.format(slot=slot)
    prev = getattr(logger_instance, slot_attr, None)
    if prev is not None:
        logger_instance.removeHandler(prev)
        logging.getLogger().removeHandler(prev)
        prev.close()

    logger_instance.addHandler(handler)
    setattr(logger_instance, slot_attr, handler)

    if also_root:
        logging.getLogger().addHandler(handler)

    return handler


def add_script_log_handler(
    logger_instance: logging.Logger,
    log_dir: Path,
    name: str,
    *,
    format_mode: Optional[str] = None,
) -> Optional[logging.FileHandler]:
    """Attach the per-command script log (``<log_dir>/<name>/<timestamp>.log``).

    Called at command execution time so only the active command's script log
    exists in the process; via the root attachment it captures the command's
    own lines plus root-propagated module-level output. The script log is
    auxiliary: if the location is unwritable, the command continues
    console-only and this returns None.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_dir) / name / f"{timestamp}.log"
    try:
        handler = add_file_handler(
            logger_instance, log_file, format_mode=format_mode, slot="script_log"
        )
    except OSError as e:
        logger_instance.warning(f"[WARNING] Failed to set up script log {log_file}: {e}")
        return None
    logger_instance.info(f"[INFO] Logging to file: {log_file}")
    return handler


def _attach_console_handler_to_root(handler: logging.Handler, level: int) -> None:
    """Attach a console handler to the root logger so propagating module-level
    loggers are captured, keeping at most one console handler on root.

    Every CLI submodule calls ``setup_logger`` at import time; without the
    marker-based dedupe the root logger would accumulate one console handler
    per call and print every propagated line that many times. The first
    console handler to reach root owns the format and stream of propagated
    output; later ``format_mode`` choices apply only to their named logger.
    File handlers never attach here: root-wide file capture belongs to
    ``add_file_handler``.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    if any(
        getattr(existing, _CONSOLE_HANDLER_ATTR, False)
        for existing in root_logger.handlers
    ):
        return
    root_logger.addHandler(handler)


def setup_logger(
    name: str = __name__,
    level: int = logging.INFO,
    console_output: bool = True,
    format_mode: Optional[str] = None
) -> logging.Logger:
    """
    Setup a named logger with a console handler.

    File logging is an execution-time concern: use ``add_script_log_handler``
    for the per-command script log and ``add_file_handler`` for the
    experiment run.log.

    Args:
        name: Logger name
        level: Logging level
        console_output: Whether to output to console
        format_mode: Optional formatter mode (e.g., "plain" for message-only logs)
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # Remove existing handlers to avoid duplicates
    if logger.handlers:
        logger.handlers.clear()

    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(_make_formatter(format_mode))
        setattr(console_handler, _CONSOLE_HANDLER_ATTR, True)
        logger.addHandler(console_handler)
        _attach_console_handler_to_root(console_handler, level)

    _configure_third_party_loggers()

    return logger
