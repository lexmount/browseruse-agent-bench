from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_STATE_ENV_KEY = "BROWSERUSE_BENCH_SESSION_STATE_FILE"


def _resolve_session_state_path() -> Path | None:
    value = os.getenv(SESSION_STATE_ENV_KEY)
    if not value:
        return None
    return Path(value)


def write_session_state(
    backend_id: str,
    session_id: str,
    forked_context_id: str | None = None,
    cleanup_metadata: dict[str, str] | None = None,
) -> None:
    path = _resolve_session_state_path()
    if path is None or not session_id:
        return
    payload: dict[str, str] = {
        "backend_id": backend_id,
        "session_id": session_id,
    }
    if forked_context_id:
        payload["forked_context_id"] = forked_context_id
    if cleanup_metadata:
        payload["cleanup_metadata"] = json.dumps(cleanup_metadata, ensure_ascii=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Failed to write browser session state file %s: %s", path, exc)


def clear_session_state() -> None:
    path = _resolve_session_state_path()
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.error("Failed to remove browser session state file %s: %s", path, exc)
        return
    logger.debug("Cleared browser session state: %s", path)
