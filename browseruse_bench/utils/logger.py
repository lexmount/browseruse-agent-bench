from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_NOISY_THIRD_PARTY_LOGGERS = (
    "httpx",
    "httpcore",
    "openai",
    "openai._base_client",
)

_RUN_LOG_HANDLER_ATTR = "_run_log_file_handler"

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

    Returns:
        The created ``FileHandler`` (caller can close it later if needed).
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(_make_formatter(format_mode))

    # Remove a previous run-log handler if one was attached
    prev = getattr(logger_instance, _RUN_LOG_HANDLER_ATTR, None)
    if prev is not None:
        logger_instance.removeHandler(prev)
        logging.getLogger().removeHandler(prev)
        prev.close()

    logger_instance.addHandler(handler)
    setattr(logger_instance, _RUN_LOG_HANDLER_ATTR, handler)

    if also_root:
        logging.getLogger().addHandler(handler)

    return handler


def _attach_handlers_to_root(handlers: List[logging.Handler], level: int) -> None:
    """Attach handlers to the root logger so propagating module-level loggers
    are captured, keeping at most one console handler on root.

    Every CLI submodule calls ``setup_logger`` at import time; without the
    marker-based dedupe the root logger would accumulate one console handler
    per call and print every propagated line that many times. The first
    console handler to reach root owns the format and stream of propagated
    output; later ``format_mode`` choices apply only to their named logger.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_has_console = any(
        getattr(existing, _CONSOLE_HANDLER_ATTR, False)
        for existing in root_logger.handlers
    )
    for handler in handlers:
        is_console = getattr(handler, _CONSOLE_HANDLER_ATTR, False)
        if is_console and root_has_console:
            continue
        root_logger.addHandler(handler)
        root_has_console = root_has_console or is_console


def setup_logger(
    name: str = __name__,
    log_dir: Optional[str] = None,
    level: int = logging.INFO,
    console_output: bool = True,
    format_mode: Optional[str] = None
) -> logging.Logger:
    """
    Setup a logger with console and file handlers.
    
    Args:
        name: Logger name
        log_dir: Optional directory to save log files
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

    formatter = _make_formatter(format_mode)

    handlers: List[logging.Handler] = []

    env_announce = os.getenv("BROWSERUSE_BENCH_LOG_ANNOUNCE", "").strip().lower()
    if env_announce in {"0", "false", "no", "off"}:
        announce_log_file = False
    elif env_announce in {"1", "true", "yes", "on"}:
        announce_log_file = True
    else:
        announce_log_file = "skills" not in sys.argv

    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        setattr(console_handler, _CONSOLE_HANDLER_ATTR, True)
        logger.addHandler(console_handler)
        handlers.append(console_handler)

    if log_dir:
        try:
            # Create log directory with subdirectory for each logger
            log_path = Path(log_dir) / name
            log_path.mkdir(parents=True, exist_ok=True)
            
            # Create log file with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_path / f"{timestamp}.log"
            
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            handlers.append(file_handler)
            
            # If console output is enabled, print where the log file is
            if console_output and announce_log_file:
                logger.info(f"[INFO] Logging to file: {log_file}")
                
        except (OSError, ValueError) as e:
            logger.warning(f"[WARNING] Failed to setup file logging: {e}")

    if handlers:
        _attach_handlers_to_root(handlers, level)

    _configure_third_party_loggers()

    return logger
