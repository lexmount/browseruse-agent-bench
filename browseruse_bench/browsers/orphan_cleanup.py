from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from browseruse_bench.utils import setup_logger

logger = setup_logger("session_cleanup")


def _load_session_state(state_file: Path) -> dict[str, str]:
    content = state_file.read_text(encoding="utf-8")
    data = json.loads(content)
    backend_id = str(data.get("backend_id") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    if not backend_id or not session_id:
        raise ValueError("Session state file must include backend_id and session_id")
    cleanup_metadata_raw = str(data.get("cleanup_metadata") or "").strip()
    cleanup_metadata: dict[str, str] = {}
    if cleanup_metadata_raw:
        try:
            decoded_metadata = json.loads(cleanup_metadata_raw)
        except json.JSONDecodeError:
            decoded_metadata = {}
        if isinstance(decoded_metadata, dict):
            cleanup_metadata = {str(k): str(v) for k, v in decoded_metadata.items()}

    return {
        "backend_id": backend_id,
        "session_id": session_id,
        "forked_context_id": str(data.get("forked_context_id") or "").strip(),
        "cleanup_metadata": json.dumps(cleanup_metadata, ensure_ascii=True),
    }


def _cleanup_lexmount_session(session_id: str, forked_context_id: str = "") -> bool:
    try:
        from lexmount import Lexmount
        from lexmount.exceptions import (
            ContextLockedError,
            ContextNotFoundError,
            SessionNotFoundError,
        )
    except ModuleNotFoundError as exc:
        if exc.name != "lexmount":
            raise
        logger.error("Lexmount SDK is not available for cleanup: %s", exc)
        return False

    client = Lexmount()
    ok = True
    try:
        client.sessions.delete(session_id=session_id)
        logger.info("Requested Lexmount session deletion: %s", session_id)
    except SessionNotFoundError:
        logger.info("Lexmount session already deleted: %s", session_id)
    except (OSError, RuntimeError, TimeoutError) as exc:
        logger.error("Lexmount session cleanup failed (session_id=%s): %s", session_id, exc)
        ok = False

    if forked_context_id:
        try:
            client.contexts.delete(forked_context_id)
            logger.info("Requested Lexmount forked context deletion: %s", forked_context_id)
        except ContextNotFoundError:
            logger.info("Lexmount forked context already deleted: %s", forked_context_id)
        except ContextLockedError as exc:
            # Unlikely here (session is gone), but guard anyway so the cleanup
            # script doesn't propagate an unhandled exception to its caller.
            logger.warning(
                "Lexmount forked context locked during cleanup (id=%s): %s",
                forked_context_id, exc,
            )
        except (OSError, RuntimeError, TimeoutError) as exc:
            # Forks without session are harmless (no lock, just storage); log but don't fail.
            logger.warning(
                "Lexmount forked context cleanup failed (id=%s): %s",
                forked_context_id, exc,
            )
    return ok


def _cleanup_agentbay_session(session_id: str) -> bool:
    try:
        from agentbay import AgentBay
        from agentbay import Session as AgentBaySession
    except ModuleNotFoundError as exc:
        if exc.name != "agentbay":
            raise
        logger.error("AgentBay SDK is not available for cleanup: %s", exc)
        return False
    except ImportError as exc:
        logger.error("AgentBay public Session API is unavailable for cleanup: %s", exc)
        return False

    api_key = os.getenv("AGENTBAY_API_KEY")
    if not api_key:
        logger.error("AGENTBAY_API_KEY is required for AgentBay cleanup")
        return False

    try:
        agent_bay = AgentBay(api_key=api_key)
        session = AgentBaySession(agent_bay, session_id)
        delete_result = agent_bay.delete(session)
        if delete_result is not None and getattr(delete_result, "success", True) is False:
            error_message = getattr(delete_result, "error_message", "unknown error")
            logger.error("AgentBay session cleanup failed (session_id=%s): %s", session_id, error_message)
            return False
        logger.info("Requested AgentBay session deletion: %s", session_id)
        return True
    except (ConnectionError, OSError, RuntimeError, TimeoutError) as exc:
        logger.error("AgentBay session cleanup failed (session_id=%s): %s", session_id, exc)
        return False


def _read_cleanup_metadata(state: dict[str, str]) -> dict[str, str]:
    raw_metadata = state.get("cleanup_metadata", "")
    if not raw_metadata:
        return {}
    try:
        decoded = json.loads(raw_metadata)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(k): str(v) for k, v in decoded.items()}


def _cleanup_browserbase_session(session_id: str, metadata: dict[str, str] | None = None) -> bool:
    metadata = metadata or {}
    api_key = metadata.get("api_key") or os.getenv("BROWSERBASE_API_KEY")
    if not api_key:
        logger.error("BROWSERBASE_API_KEY is required for Browserbase cleanup")
        return False

    base_url = (metadata.get("base_url") or os.getenv("BROWSERBASE_BASE_URL", "https://api.browserbase.com")).rstrip("/")
    project_id = metadata.get("project_id") or os.getenv("BROWSERBASE_PROJECT_ID", "")
    try:
        timeout_seconds = int(metadata.get("request_timeout") or 30)
    except ValueError:
        timeout_seconds = 30
    body: dict[str, str] = {"status": "REQUEST_RELEASE"}
    if project_id:
        body["projectId"] = project_id
    try:
        from browseruse_bench.browsers.providers.cloud_utils import post_json

        post_json(
            url=f"{base_url}/v1/sessions/{session_id}",
            headers={"X-BB-API-Key": api_key},
            body=body,
            timeout_seconds=timeout_seconds,
        )
        logger.info("Requested Browserbase session release: %s", session_id)
        return True
    except (ConnectionError, OSError, RuntimeError, TimeoutError) as exc:
        logger.error("Browserbase session cleanup failed (session_id=%s): %s", session_id, exc)
        return False


def _cleanup_steel_session(session_id: str) -> bool:
    api_key = os.getenv("STEEL_API_KEY")
    if not api_key:
        logger.error("STEEL_API_KEY is required for Steel cleanup")
        return False

    base_url = os.getenv("STEEL_BASE_URL", "https://api.steel.dev").rstrip("/")
    try:
        from browseruse_bench.browsers.providers.cloud_utils import post_json

        post_json(
            url=f"{base_url}/v1/sessions/{session_id}/release",
            headers={"steel-api-key": api_key},
            body=None,
            timeout_seconds=30,
        )
        logger.info("Requested Steel session release: %s", session_id)
        return True
    except (ConnectionError, OSError, RuntimeError, TimeoutError) as exc:
        logger.error("Steel session cleanup failed (session_id=%s): %s", session_id, exc)
        return False


def _remove_state_file(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("Failed to remove session state file %s: %s", path, exc)
        return False
    return True


def cleanup_orphaned_session_state(state_file: Path) -> int:
    if not state_file.exists():
        logger.info("Session state file not found, nothing to cleanup: %s", state_file)
        return 0

    try:
        state = _load_session_state(state_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.error("Invalid session state file %s: %s", state_file, exc)
        return 1

    backend_id = state["backend_id"]
    session_id = state["session_id"]
    logger.info(
        "Detected stale browser session state, backend=%s, session_id=%s",
        backend_id,
        session_id,
    )

    if backend_id == "lexmount":
        success = _cleanup_lexmount_session(
            session_id=session_id,
            forked_context_id=state.get("forked_context_id", ""),
        )
    elif backend_id == "agentbay":
        success = _cleanup_agentbay_session(session_id=session_id)
    elif backend_id == "browserbase":
        success = _cleanup_browserbase_session(
            session_id=session_id,
            metadata=_read_cleanup_metadata(state),
        )
    elif backend_id == "steel":
        success = _cleanup_steel_session(session_id=session_id)
    else:
        logger.info("Unsupported backend_id in session state (%s), skipping cleanup", backend_id)
        success = True

    if not success:
        return 1

    if not _remove_state_file(state_file):
        return 1

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cleanup orphaned browser sessions")
    parser.add_argument("--state-file", required=True, type=Path, help="Path to browser session state file")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return cleanup_orphaned_session_state(state_file=args.state_file)


if __name__ == "__main__":
    sys.exit(main())
