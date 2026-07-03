"""
SkyvernAgent - Browser automation using the Skyvern SDK.

This agent uses the Skyvern SDK to execute browser automation tasks.
Note: Skyvern has complex process isolation requirements, so this module
wraps the existing implementation rather than reimplementing from scratch.

Prerequisites
=============

1. Local database
   ------------------------------------------------
   In benchmark local mode, this adapter defaults Skyvern to an isolated
   temporary PostgreSQL database so smoke tests do not depend on a shared
   PostgreSQL schema.

   Set ``database_string`` in config.yaml only when you intentionally want a
   managed/persistent Skyvern database, for example:
   ``postgresql+psycopg://skyvern@localhost/skyvern``.

   # Install (Ubuntu/Debian)
   sudo apt install postgresql postgresql-contrib

   # Create database and user (peer auth, no password needed locally)
   sudo -u postgres createuser skyvern
   sudo -u postgres createdb skyvern -O skyvern

   On first ``Skyvern.local()`` call, ``start_forge_app()`` runs Alembic
   migrations automatically to initialize the schema.

2. Skyvern API Key
   ------------------------------------------------
   **Local mode** (``Skyvern.local()``):
     Auto-generated. ``_ensure_local_skyvern_auth()`` calls
     ``regenerate_local_api_key()`` to create an org + JWT key in
     PostgreSQL and sets ``SKYVERN_API_KEY`` in os.environ.
     No manual configuration needed.

   **Cloud mode** (``Skyvern(api_key=...)``):
     Set in .env or root config.yaml:
       SKYVERN_API_KEY=<your-cloud-api-key>

3. Using Lexmount browser with local Skyvern AI
   -----------------------------------------------
   This runs Skyvern's agent logic locally (your own LLM) while connecting
   to a Lexmount cloud browser via CDP.

   a) root config.yaml under agents.skyvern.models.<name>:
        BROWSER_ID: lexmount
        ENABLE_OPENAI_COMPATIBLE: true
        OPENAI_COMPATIBLE_MODEL_NAME: <model-name>
        OPENAI_COMPATIBLE_API_KEY: <api-key>      # or set in .env
        OPENAI_COMPATIBLE_API_BASE: <base-url>    # or set in .env

   b) .env (project root):
        LEXMOUNT_API_KEY=<your-lexmount-key>

   Flow: local DB auth → build LLMConfig → ``Skyvern.local(llm_config=...)``
   → ``connect_to_browser_over_cdp(lexmount_cdp_url)``.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import binascii
import importlib
import json
import logging
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from playwright.async_api import TargetClosedError
except ImportError:
    # Some playwright versions don't re-export TargetClosedError from the public
    # async_api module; fall back to the private location where it always lives.
    # The call site keeps the `if TargetClosedError is None` guard in case neither
    # import works (e.g. a wildly different playwright major).
    try:
        from playwright._impl._errors import TargetClosedError
    except ImportError:
        TargetClosedError = None

from browseruse_bench.agents.base import BaseAgent
from browseruse_bench.agents.registry import register_agent
from browseruse_bench.browsers import BrowserSessionContext
from browseruse_bench.browsers.registry import get_backend
from browseruse_bench.schemas import AgentMetrics, AgentResult, AgentUsage
from browseruse_bench.utils.config_loader import canonicalize_skyvern_model_name

logger = logging.getLogger(__name__)

# Browser backends whose runs leave Skyvern's per-step artifacts on the local
# filesystem (cloud-managed Skyvern browsers keep artifacts remote).
_LOCAL_ARTIFACT_BROWSERS = ("local", "lexmount", "cdp")

# Exception classes tolerated by best-effort screenshot capture; empty when
# no TargetClosedError could be imported (then nothing extra is caught).
_TARGET_CLOSED_ERRORS: tuple[type[BaseException], ...] = (
    (TargetClosedError,) if TargetClosedError is not None else ()
)

Skyvern: type[Any] | None = None
RunEngine: type[Any] | None = None
RunStatus: type[Any] | None = None
skyvern_settings: Any | None = None
_SKYVERN_IMPORT_ERROR: str | None = None


def _make_temp_database_string() -> str:
    db_name = f"bubench_skyvern_{uuid.uuid4().hex}"
    socket_host = os.getenv("BUBENCH_SKYVERN_POSTGRES_HOST")
    if not socket_host:
        socket_host = "/var/run/postgresql" if Path("/var/run/postgresql").exists() else "/tmp"
    return f"postgresql+psycopg:///{db_name}?host={urllib.parse.quote(socket_host, safe='/')}"


def _parse_temp_postgres_database(database_string: str) -> tuple[str, urllib.parse.SplitResult] | None:
    parsed = urllib.parse.urlsplit(database_string)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"}:
        return None

    db_name = parsed.path.lstrip("/")
    if not db_name.startswith("bubench_skyvern_"):
        return None

    return db_name, parsed


def _psycopg_conninfo(parsed: urllib.parse.SplitResult, database: str) -> str:
    query = f"?{parsed.query}" if parsed.query else ""
    if parsed.netloc:
        return f"postgresql://{parsed.netloc}/{database}{query}"
    return f"postgresql:///{database}{query}"


def _drop_temp_postgres_database(parsed: urllib.parse.SplitResult, db_name: str) -> None:
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        return

    admin_conninfo = _psycopg_conninfo(parsed, "postgres")
    try:
        with (
            psycopg.connect(admin_conninfo, autocommit=True) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (db_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(db_name)))
    except psycopg.Error as exc:
        logger.warning("Failed to drop temporary Skyvern database %s: %s", db_name, exc)


def _ensure_temp_postgres_database(database_string: str) -> bool:
    parsed_db = _parse_temp_postgres_database(database_string)
    if parsed_db is None:
        return False

    db_name, parsed = parsed_db
    try:
        import psycopg
        from psycopg import sql
        from psycopg.errors import DuplicateDatabase
    except ImportError as exc:
        raise ImportError(
            "Skyvern temporary Postgres mode requires psycopg. "
            "Install/run with the skyvern extra."
        ) from exc

    admin_conninfo = _psycopg_conninfo(parsed, "postgres")
    with (
        psycopg.connect(admin_conninfo, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        try:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        except DuplicateDatabase:
            logger.info("Temporary Skyvern database already exists: %s", db_name)
        else:
            logger.info("Created temporary Skyvern database: %s", db_name)
    target_conninfo = _psycopg_conninfo(parsed, db_name)
    with (
        psycopg.connect(target_conninfo, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    atexit.register(_drop_temp_postgres_database, parsed, db_name)
    return True


def _build_local_chromium_args(
    agent_config: dict[str, Any], proxy_meta: dict[str, str] | None
) -> list[str]:
    """Build Chrome CLI args for the local-browser launch path.

    bench calls `Skyvern.launch_local_browser(args=...)` which forwards args
    straight to Playwright's chromium.launch_persistent_context — that bypasses
    Skyvern's own setup_proxy() (BrowserFactory). So we wire `local_proxy_*`
    by emitting `--proxy-server=...` and `--proxy-bypass-list=...` directly.

    proxy_meta is expected to be the dict that LocalBackend stores under
    BrowserSessionContext.metadata["local_proxy"] (server, optional username /
    password / bypass). When auth is configured, embed user:pass into the URL —
    Chrome will then send it on Proxy-Authorization. Special chars in user/pwd
    are URL-quoted so '@' / ':' don't break the netloc.

    agent_config is accepted (and unused) for forward-compatibility — callers
    can pass it without first having to pre-extract the proxy dict.
    """
    del agent_config  # reserved
    if not proxy_meta:
        return []
    server = str(proxy_meta.get("server") or "").strip()
    if not server:
        return []
    user = str(proxy_meta.get("username") or "").strip()
    pwd = str(proxy_meta.get("password") or "").strip()
    bypass = str(proxy_meta.get("bypass") or "").strip()
    proxy_arg_value = server
    # Only embed auth when a username is present. A bare password (with no
    # username) would produce ":secret@host", which is malformed under
    # RFC 3986's userinfo grammar and rejected by most proxy clients/servers.
    if user:
        parts = urllib.parse.urlsplit(server)
        if parts.hostname:
            auth = urllib.parse.quote(user, safe="")
            if pwd:
                auth += ":" + urllib.parse.quote(pwd, safe="")
            host = parts.hostname + (f":{parts.port}" if parts.port else "")
            proxy_arg_value = urllib.parse.urlunsplit(
                (parts.scheme, f"{auth}@{host}", parts.path, parts.query, parts.fragment)
            )
    args = [f"--proxy-server={proxy_arg_value}"]
    if bypass:
        args.append(f"--proxy-bypass-list={bypass}")
    return args


def _load_skyvern_dependencies() -> None:
    global Skyvern, RunEngine, RunStatus, skyvern_settings, _SKYVERN_IMPORT_ERROR

    if (
        Skyvern is not None
        and RunEngine is not None
        and RunStatus is not None
        and skyvern_settings is not None
    ):
        return
    if _SKYVERN_IMPORT_ERROR:
        return

    try:
        skyvern_module = importlib.import_module("skyvern")
        config_module = importlib.import_module("skyvern.config")
        runs_module = importlib.import_module("skyvern.schemas.runs")

        Skyvern = skyvern_module.Skyvern
        skyvern_settings = config_module.settings
        RunEngine = runs_module.RunEngine
        RunStatus = runs_module.RunStatus
    except (ImportError, AttributeError) as exc:
        _SKYVERN_IMPORT_ERROR = str(exc)
        logger.error(f"skyvern dependency is not available: {exc}")
        Skyvern = None
        RunEngine = None
        RunStatus = None
        skyvern_settings = None


def _get_skyvern_artifacts_base() -> Path | None:
    if skyvern_settings is None:
        return None
    try:
        artifacts_path = skyvern_settings.ARTIFACT_STORAGE_PATH
    except AttributeError as exc:
        logger.error(f"Skyvern settings missing ARTIFACT_STORAGE_PATH: {exc}")
        return None
    if not artifacts_path:
        return None
    return Path(artifacts_path)


def _get_skyvern_org_id() -> str:
    api_key = os.getenv("SKYVERN_API_KEY", "")
    if not api_key:
        return "local"

    try:
        parts = api_key.split(".")
        if len(parts) < 2:
            return "local"
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        data = json.loads(decoded)
        return str(data.get("sub", "local"))
    except (ValueError, json.JSONDecodeError, binascii.Error) as exc:
        logger.error(f"Failed to parse SKYVERN_API_KEY: {exc}")
        return "local"


def get_existing_artifact_dirs(org_id: str | None = None) -> set[str]:
    artifacts_base = _get_skyvern_artifacts_base()
    if artifacts_base is None:
        return set()
    if org_id is None:
        org_id = _get_skyvern_org_id()

    artifacts_org_dir = artifacts_base / org_id
    if not artifacts_org_dir.exists():
        return set()

    existing_dirs: set[str] = set()
    try:
        for task_dir in artifacts_org_dir.iterdir():
            if task_dir.is_dir() and task_dir.name.startswith("tsk_"):
                existing_dirs.add(task_dir.name)
    except OSError as exc:
        logger.error(f"Failed to list artifacts directory: {exc}")

    return existing_dirs


def _find_task_dir_by_prompt(
    artifacts_org_dir: Path,
    task_prompt: str,
    candidate_dirs: list[str],
    start_time: float,
) -> str | None:
    prompt_signature = task_prompt[:100] if len(task_prompt) > 100 else task_prompt

    for task_name in candidate_dirs:
        task_dir = artifacts_org_dir / task_name
        try:
            dir_stat = task_dir.stat()
            if dir_stat.st_mtime < start_time - 5:
                continue
        except OSError:
            continue

        try:
            step_dirs = sorted(task_dir.iterdir())
            for step_dir in step_dirs[:1]:
                if not step_dir.is_dir():
                    continue
                for prompt_file in step_dir.glob("*_llm_prompt.txt"):
                    try:
                        content = prompt_file.read_text(encoding="utf-8", errors="ignore")
                        if prompt_signature in content:
                            return task_name
                    except OSError as exc:
                        logger.error(f"Failed to read prompt file {prompt_file}: {exc}")
        except OSError:
            continue

    return None


def _get_actions_from_step_dir(step_dir: Path) -> list[dict[str, Any]]:
    try:
        for response_file in step_dir.glob("*_llm_response_parsed.json"):
            content = response_file.read_text(encoding="utf-8")
            data = json.loads(content)
            actions = data.get("actions", [])
            if actions:
                return actions
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"Failed to parse actions in {step_dir}: {exc}")
    return []


def _extract_usage_from_response_blob(blob: Any) -> dict[str, int] | None:
    """Pull token counts out of a stored LLM response.

    Skyvern persists the raw LiteLLM response per step as
    ``*_llm_response.json``; the exact key name varies slightly between
    response types (OpenAI-style ``usage``, Anthropic-style ``usage``,
    or the top-level ``message``/``choices`` object). We accept anything
    shaped like ``{"prompt_tokens": int, "completion_tokens": int, ...}``
    regardless of where it lives in the JSON tree.
    """
    if not isinstance(blob, dict | list):
        return None

    def _safe_int(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def _details_cached(node: dict[str, Any]) -> int:
        # Trust a nonzero details counter first; a zero-filled details object
        # must not mask a real top-level cache counter.
        for details_key in ("prompt_tokens_details", "input_tokens_details"):
            details = node.get(details_key)
            if isinstance(details, dict):
                cached = _safe_int(details.get("cached_tokens", 0))
                if cached:
                    return cached
        fallback = node.get("cache_read_input_tokens")
        if fallback is None:
            fallback = node.get("cached_tokens")
        return _safe_int(fallback) if fallback is not None else 0

    def _candidate_usage(node: Any) -> dict[str, int] | None:
        if not isinstance(node, dict):
            return None
        # A zero-filled prompt_tokens falls through to input_tokens, matching
        # the pre-existing `or` chain semantics.
        pt = node.get("prompt_tokens") or None
        it = node.get("input_tokens")
        ct = node.get("completion_tokens")
        if ct is None:
            ct = node.get("output_tokens")
        tt = node.get("total_tokens")
        if pt is None and it is None and ct is None and tt is None:
            return None

        cached = _details_cached(node)
        creation = _safe_int(node.get("cache_creation_input_tokens", 0))
        prompt = _safe_int(pt if pt is not None else it)
        # Anthropic-style usage: input_tokens EXCLUDES cache reads/writes,
        # which arrive as separate cache_*_input_tokens counters. Fold them
        # into the prompt count so it matches the OpenAI convention used by
        # downstream cost math (prompt includes cached). OpenAI's Responses
        # API also uses input_tokens but reports cached via
        # input_tokens_details, so the cache_* keys are a safe discriminator.
        anthropic_style = pt is None and (
            "cache_read_input_tokens" in node or "cache_creation_input_tokens" in node
        )
        if anthropic_style:
            cached = _safe_int(node.get("cache_read_input_tokens", 0))
            prompt += cached + creation

        completion = _safe_int(ct) if ct is not None else 0
        total = _safe_int(tt) if tt is not None else prompt + completion
        if prompt == 0 and completion == 0 and total == 0:
            return None
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": min(cached, max(prompt, 0)),
            "cache_creation_tokens": min(creation, max(prompt, 0)),
            "total_tokens": total if total > 0 else prompt + completion,
        }

    # Walk a bounded subset of the tree — depth-first, but only through dict /
    # list payloads. Returns the *first* usage-shaped dict we recognise.
    stack: list[Any] = [blob]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            # Prefer an explicit "usage" child first (most accurate position).
            usage_child = node.get("usage")
            found = _candidate_usage(usage_child)
            if found:
                return found
            found = _candidate_usage(node)
            if found:
                return found
            for v in node.values():
                if isinstance(v, dict | list):
                    stack.append(v)
        elif isinstance(node, list):
            for item in node:
                if isinstance(item, dict | list):
                    stack.append(item)
    return None


def _task_run_to_dict(task_run: Any) -> Any:
    """Best-effort ``task_run`` → plain dict for usage scanning.

    Cloud task_run objects are pydantic models; local artifact walks produce
    dicts directly. Anything we can't serialise is returned as-is so the
    caller's scanner can still walk attribute-style fields via ``vars()``.
    """
    if isinstance(task_run, dict):
        return task_run
    for method in ("model_dump", "dict"):
        fn = getattr(task_run, method, None)
        if callable(fn):
            try:
                return fn()
            except (TypeError, ValueError):
                continue
    try:
        return vars(task_run)
    except TypeError:
        return task_run


def collect_usage_from_skyvern_artifacts(task_dirs: list[Path]) -> dict[str, Any]:
    """Aggregate LLM usage across every step in matched Skyvern artifact dirs.

    Each ``tsk_<id>/`` artifact directory contains one subdir per step
    (``*_stp_*``) which Skyvern fills with the raw LLM response payload under
    ``*_llm_response.json``. We sum ``prompt_tokens`` / ``completion_tokens``
    across every response file we can parse and return a usage summary
    suitable for ``AgentMetrics.usage``. Cost is left to the shared
    ``enrich_result_usage_cost_if_needed`` pass downstream.
    """
    total_prompt = 0
    total_completion = 0
    total_cached = 0
    total_cache_creation = 0
    entry_count = 0

    for task_dir in task_dirs:
        if not task_dir.is_dir():
            continue
        try:
            step_dirs = sorted(
                (d for d in task_dir.iterdir() if d.is_dir() and "_stp_" in d.name),
                key=lambda d: d.name,
            )
        except OSError:
            continue
        for step_dir in step_dirs:
            # Raw response first (most likely to carry `usage`), then fall
            # back to the parsed variant in case a step only wrote the latter.
            try:
                candidates = list(step_dir.glob("*_llm_response.json")) + list(
                    step_dir.glob("*_llm_response_parsed.json")
                )
            except OSError as exc:
                logger.debug(
                    "Skyvern usage: skipping unreadable step dir %s: %s",
                    step_dir,
                    exc,
                )
                continue
            for response_file in candidates:
                try:
                    blob = json.loads(response_file.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.debug(
                        "Skyvern usage: skipping unreadable %s: %s", response_file, exc
                    )
                    continue
                usage = _extract_usage_from_response_blob(blob)
                if usage is None:
                    continue
                total_prompt += usage["prompt_tokens"]
                total_completion += usage["completion_tokens"]
                total_cached += usage["cached_tokens"]
                total_cache_creation += usage["cache_creation_tokens"]
                entry_count += 1
                # One usage block per step is enough; avoid double-counting a
                # retry payload in the same step dir.
                break

    if entry_count == 0:
        return {}

    return {
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_prompt_cached_tokens": total_cached,
        "total_prompt_cache_creation_tokens": total_cache_creation,
        "total_tokens": total_prompt + total_completion,
        "entry_count": entry_count,
    }


def _format_action(action: dict[str, Any]) -> str:
    action_type = action.get("action_type", "UNKNOWN")
    parts = [action_type]
    for key in ("id", "text", "option", "url", "file_url", "download_url"):
        value = action.get(key)
        if value:
            parts.append(f"{key}={str(value)[:80]}")
    return " ".join(parts)


def _collect_action_history(task_dirs: list[Path]) -> list[str]:
    action_history: list[str] = []
    for task_dir in task_dirs:
        if not task_dir.exists():
            continue
        try:
            step_dirs = sorted(
                [d for d in task_dir.iterdir() if d.is_dir() and "_stp_" in d.name],
                key=lambda x: x.name,
            )
        except OSError:
            continue
        for step_dir in step_dirs:
            actions = _get_actions_from_step_dir(step_dir)
            action_history.extend([_format_action(a) for a in actions])
    return action_history


def _normalize_answer_text(value: Any) -> tuple[str, Any | None]:
    if value is None:
        return "", None
    if isinstance(value, str):
        return value, None
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False), value
    return str(value), value


def _task_dir_numeric_id(name: str) -> int:
    suffix = name.removeprefix("tsk_")
    if suffix.isdigit():
        return int(suffix)
    return 0


def _resolve_artifact_task_dirs(
    start_time: float,
    end_time: float,
    org_id: str | None,
    existing_dirs_before_task: set[str] | None,
    task_prompt: str | None,
    include_sibling_dirs: bool = False,
) -> list[Path]:
    artifacts_base = _get_skyvern_artifacts_base()
    if artifacts_base is None:
        logger.warning("Skyvern artifacts base not available")
        return []
    if org_id is None:
        org_id = _get_skyvern_org_id()

    artifacts_org_dir = artifacts_base / org_id
    if not artifacts_org_dir.exists():
        logger.warning(f"Skyvern artifacts directory not found: {artifacts_org_dir}")
        return []

    candidate_dirs: list[str] = []
    for task_dir in artifacts_org_dir.iterdir():
        if not task_dir.is_dir() or not task_dir.name.startswith("tsk_"):
            continue
        if existing_dirs_before_task is not None and task_dir.name in existing_dirs_before_task:
            continue
        try:
            dir_mtime = task_dir.stat().st_mtime
            if (start_time - 10) <= dir_mtime <= (end_time + 30):
                candidate_dirs.append(task_dir.name)
        except OSError:
            continue

    if not candidate_dirs:
        logger.warning(
            f"No candidate Skyvern artifacts found for time window {start_time:.0f}-{end_time:.0f}"
        )
        return []

    if task_prompt:
        matched_task = _find_task_dir_by_prompt(
            artifacts_org_dir, task_prompt, candidate_dirs, start_time
        )
        if matched_task and include_sibling_dirs:
            # A task_v2 run spreads its steps across several tsk_ dirs
            # (planner task + per-goal child tasks); the children never embed
            # the user prompt, so once the prompt anchors one dir, every new
            # in-window dir belongs to this run. Caveat: with concurrent
            # skyvern tasks sharing this artifact store (run --concurrency
            # > 1), attribution is best-effort and may claim a concurrent
            # run's dirs.
            ordered = sorted(candidate_dirs, key=_task_dir_numeric_id)
            if len(ordered) > 1:
                logger.info(
                    f"Prompt anchored {matched_task}; attributing "
                    f"{len(ordered)} sibling artifact dirs to this run"
                )
            return [artifacts_org_dir / name for name in ordered]
        if matched_task:
            return [artifacts_org_dir / matched_task]
        logger.warning(
            f"Could not match task by prompt among {len(candidate_dirs)} candidates, "
            "returning 0 screenshots to avoid contamination"
        )
        return []

    if len(candidate_dirs) > 1:
        logger.warning(
            f"Multiple candidates ({len(candidate_dirs)}) but no task_prompt for matching, "
            "returning 0 screenshots to avoid contamination"
        )
        return []

    return [artifacts_org_dir / candidate_dirs[0]]


def copy_screenshots_from_skyvern_artifacts(
    trajectory_dir: Path,
    start_time: float,
    end_time: float,
    org_id: str | None = None,
    existing_dirs_before_task: set[str] | None = None,
    task_prompt: str | None = None,
    include_sibling_dirs: bool = False,
) -> tuple[int, int, list[Path]]:
    task_dirs = _resolve_artifact_task_dirs(
        start_time,
        end_time,
        org_id,
        existing_dirs_before_task,
        task_prompt,
        include_sibling_dirs=include_sibling_dirs,
    )
    if not task_dirs:
        return 0, 0, []

    screenshot_files: list[Path] = []
    total_steps = 0

    for task_dir in task_dirs:
        try:
            for d in task_dir.iterdir():
                if d.is_dir() and "_stp_" in d.name:
                    total_steps += 1
        except OSError:
            continue

        for png_file in task_dir.rglob("*_screenshot_action.png"):
            screenshot_files.append(png_file)

    if not screenshot_files:
        logger.warning(
            f"No screenshots found in Skyvern artifacts for time window "
            f"{start_time:.0f}-{end_time:.0f}"
        )
        return 0, total_steps, task_dirs

    screenshot_files.sort(key=lambda f: f.stat().st_mtime)
    screenshot_count = 0
    for i, src_file in enumerate(screenshot_files, 1):
        try:
            dst_file = trajectory_dir / f"screenshot-{i}.png"
            shutil.copy2(src_file, dst_file)

            screenshot_count += 1
        except OSError as exc:
            logger.error(f"Failed to copy screenshot {src_file}: {exc}")

    return screenshot_count, total_steps, task_dirs


def _collect_run_artifacts(
    trajectory_dir: Path,
    start_time: float,
    end_time: float,
    org_id: str | None,
    existing_dirs_before_task: set[str] | None,
    task_prompt: str | None,
    include_sibling_dirs: bool,
) -> tuple[int, int, list[str], dict[str, Any]]:
    """Copy screenshots and aggregate steps, actions, and usage from local artifacts.

    Shared by the normal completion path and the client-side timeout path so a
    timed-out run still reports the work Skyvern actually did.
    """
    screenshot_count, steps, task_dirs = copy_screenshots_from_skyvern_artifacts(
        trajectory_dir,
        start_time,
        end_time,
        org_id=org_id,
        existing_dirs_before_task=existing_dirs_before_task,
        task_prompt=task_prompt,
        include_sibling_dirs=include_sibling_dirs,
    )
    if not task_dirs:
        return screenshot_count, steps, [], {}
    return (
        screenshot_count,
        steps,
        _collect_action_history(task_dirs),
        collect_usage_from_skyvern_artifacts(task_dirs),
    )


async def _capture_page_screenshot(page: Any, screenshot_path: Path, label: str) -> bool:
    """Best-effort page screenshot; tolerates a dead page/context.

    A closed page (common when the remote browser session died or the task
    timed out) must not turn a soft failure into a subprocess-level
    exception. Returns True when a screenshot file was written.
    """
    try:
        await page.screenshot(path=str(screenshot_path))
        return True
    except (OSError, RuntimeError) as exc:
        logger.error(f"Failed to capture {label} screenshot: {exc}")
        return False
    except _TARGET_CLOSED_ERRORS as exc:
        logger.warning("Skipped %s screenshot; page/context closed: %s", label, exc)
        return False


@register_agent
class SkyvernAgent(BaseAgent):
    """
    Browser automation agent using the Skyvern SDK.

    Supports multiple execution engines (skyvern_v1, skyvern_v2, openai-cua, etc.)
    and browser backends (local, CDP, Lexmount cloud, Skyvern cloud).

    Note: This agent has complex initialization requirements for the Skyvern SDK,
    including setting up OpenAI-compatible environment variables before import.
    """

    name = "skyvern"

    def prepare(self, agent_config: dict[str, Any]) -> None:
        """Prepare Skyvern runtime environment and dependencies."""
        self._setup_skyvern_env(agent_config)
        _load_skyvern_dependencies()
        self._require_skyvern_dependencies()

    def run_task(
        self,
        task_info: dict[str, Any],
        agent_config: dict[str, Any],
        task_workspace: Path,
    ) -> AgentResult | dict[str, Any]:
        """Execute a browser automation task using Skyvern."""
        timeout = self.get_timeout(agent_config, 600)
        browser_id = str(agent_config.get("browser_id", "local"))

        # The backend session lifecycle is managed inside _run_task_async rather
        # than with `open_browser_session` here, so the session can be closed
        # and reopened if Skyvern's ~60 s init races the cloud browser's idle
        # timeout (observed with Lexmount: the CDP session dies mid-init and
        # both the initial page and the whole BrowserContext become unusable).
        return asyncio.run(
            self._run_task_async(
                task_info,
                task_workspace,
                timeout,
                browser_id,
                agent_config,
            )
        )

    def _setup_skyvern_env(self, agent_config: dict[str, Any]) -> None:
        """Setup environment variables required by Skyvern SDK."""
        canonicalize_skyvern_model_name(agent_config)
        config_to_env = {
            "enable_openai_compatible": "ENABLE_OPENAI_COMPATIBLE",
            "api_key": "OPENAI_COMPATIBLE_API_KEY",
            "base_url": "OPENAI_COMPATIBLE_API_BASE",
            "model_id": "OPENAI_COMPATIBLE_MODEL_NAME",
            "max_tokens": "OPENAI_COMPATIBLE_MAX_TOKENS",
            "temperature": "OPENAI_COMPATIBLE_TEMPERATURE",
            "supports_vision": "OPENAI_COMPATIBLE_SUPPORTS_VISION",
            "llm_key": "LLM_KEY",
            "skyvern_api_key": "SKYVERN_API_KEY",
            "database_string": "DATABASE_STRING",
            "disable_connection_pool": "DISABLE_CONNECTION_POOL",
        }

        for config_key, env_key in config_to_env.items():
            # Config value takes priority, then existing env var
            config_val = agent_config.get(config_key)
            if config_val is not None:
                if config_key == "database_string" and config_val == "":
                    os.environ.pop(env_key, None)
                elif isinstance(config_val, bool):
                    os.environ[env_key] = "true" if config_val else "false"
                else:
                    os.environ[env_key] = str(config_val)

        # OpenAI-compatible mode must force LLM_KEY, otherwise unrelated values
        # inherited from the repo/root .env can route Skyvern to the wrong provider.
        if agent_config.get("enable_openai_compatible"):
            os.environ["LLM_KEY"] = str(
                agent_config.get("llm_key")
                or os.getenv("OPENAI_COMPATIBLE_MODEL_KEY")
                or "OPENAI_COMPATIBLE"
            )

        # For benchmark smoke runs, default Skyvern local mode to an isolated
        # temporary Postgres DB. A repo/root .env may contain a DATABASE_STRING
        # for an older Skyvern schema; only use it when explicitly selected in
        # config.
        if agent_config.get("enable_openai_compatible") and "database_string" not in agent_config:
            os.environ["DATABASE_STRING"] = _make_temp_database_string()

        # NOTE: local_proxy_* is NOT wired through Skyvern's own setup_proxy()
        # (which reads ENABLE_PROXY / HOSTED_PROXY_POOL env vars). The bench's
        # local-browser launch path calls Skyvern.launch_local_browser() which
        # bypasses Skyvern's BrowserFactory entirely. Instead we pass
        # `--proxy-server=...` directly via the `args` parameter — see the
        # local-launch branch in _run_task_async and _build_local_chromium_args.

    @staticmethod
    def _rewrite_local_db_error(error_msg: str, agent_config: dict[str, Any]) -> str:
        local_mode_enabled = bool(agent_config.get("enable_openai_compatible"))
        if not local_mode_enabled:
            return error_msg

        lowered = error_msg.lower()
        if "role \"skyvern\" does not exist" in lowered or (
            "connection failed" in lowered and "localhost" in lowered and "5432" in lowered
        ):
            return (
                "Skyvern local mode requires a PostgreSQL database, but the default local "
                "connection failed.\n"
                "Current mode: enable_openai_compatible=true -> Skyvern.local(...)\n"
                "Default database: postgresql+psycopg://skyvern@localhost/skyvern\n"
                "Fix options:\n"
                "1. Create the local Postgres role/database: `createuser skyvern` and "
                "`createdb skyvern -O skyvern`\n"
                "2. Or set `database_string` in root config.yaml (or DATABASE_STRING in env) "
                "to a working PostgreSQL connection string\n"
                "3. Or disable `enable_openai_compatible` and use Skyvern cloud mode instead "
                "(requires `skyvern_api_key`)\n"
                f"Original error: {error_msg}"
            )

        return error_msg

    async def _run_task_async(
        self,
        task_info: dict[str, Any],
        task_workspace: Path,
        timeout: int,
        browser_id_arg: str,
        agent_config: dict[str, Any],
    ) -> AgentResult:
        """Async implementation of task execution using Skyvern SDK."""
        # 在Prepare中已经检查过一次是否安装了，为什么在run single task中再检查一次？
        self._require_skyvern_dependencies()
        assert Skyvern is not None
        assert RunEngine is not None

        task_id = task_info["task_id"]
        url = task_info["url"]

        task_workspace.mkdir(parents=True, exist_ok=True)
        trajectory_dir = task_workspace / "trajectory"
        trajectory_dir.mkdir(parents=True, exist_ok=True)

        task_prompt = self.build_task_prompt(task_info)

        # Open backend session manually (replaces outer open_browser_session ctx
        # manager). We need to reopen on TargetClosedError during init so the
        # context manager pattern doesn't fit.
        backend = get_backend(browser_id_arg)
        session_context: BrowserSessionContext = backend.open(
            agent_name=self.name, agent_config=agent_config
        )
        if session_context.transport == "cdp":
            logger.info(
                "[INFO] Connecting to %s browser over CDP...", session_context.backend_id
            )
        elif session_context.transport == "cloud_native":
            logger.info(
                "[INFO] Executing task with %s browser backend...", session_context.backend_id
            )
        else:
            logger.info("[INFO] Executing task with local browser backend...")

        # Read config values
        browser_id = session_context.backend_id
        cdp_url = session_context.cdp_url
        transport = session_context.transport
        engine_str = agent_config.get("engine", "skyvern_v2")
        max_steps = self.get_max_steps(agent_config, 25)
        max_screenshot_scrolls = int(agent_config.get("max_screenshot_scrolls", 3))
        include_action_history = agent_config.get("include_action_history_in_verification", True)
        headless = agent_config.get("headless", False)
        if isinstance(headless, str):
            headless = headless.lower() in ("true", "1", "yes")

        # Model ID for result tracking (like browser-use's MODEL_ID). Legacy
        # `openai_compatible_model_name` is already aliased onto `model_id` by
        # `_setup_skyvern_env` (via `canonicalize_skyvern_model_name`) in `prepare()`.
        model_id = agent_config.get("model_id") or engine_str

        # Engine mapping
        ENGINE_MAP = {
            "skyvern_v1": RunEngine.skyvern_v1,
            "skyvern_v2": RunEngine.skyvern_v2,
        }
        engine = ENGINE_MAP.get(engine_str, RunEngine.skyvern_v2)
        # task_v2 runs spread their artifacts across multiple tsk_ dirs
        # (planner + child tasks), so artifact matching must claim siblings.
        include_sibling_dirs = engine == RunEngine.skyvern_v2

        config_info = {
            "timeout_seconds": timeout,
            "engine": engine_str,
            "model_id": model_id,
            "max_steps": max_steps,
            "browser_id": browser_id,
        }

        skyvern = None
        browser = None
        page = None
        start_time = time.time()
        org_id: str | None = None
        existing_artifact_dirs: set[str] | None = None

        try:
            # Initialize Skyvern client
            api_key = agent_config.get("skyvern_api_key") or os.getenv("SKYVERN_API_KEY")

            # Create explicit LLM config (similar to browser-use's _create_llm)
            llm_config = self._create_llm_config(agent_config)

            if llm_config:
                # Use our own AI provider with Skyvern's local agent framework.
                # Two modes, driven by DATABASE_STRING:
                # - persistent PG: we must provision org + auth token ourselves
                #   (SQLAlchemy query against existing Postgres tables) BEFORE
                #   Skyvern.local(...) so the embedded server can validate us.
                # - in-memory SQLite (DATABASE_STRING unset): Skyvern creates
                #   tables on first HTTP request and provisions its own org +
                #   token; running our setup first would query tables that
                #   don't exist yet and crash with "no such table".
                use_persistent_db = bool(os.getenv("DATABASE_STRING"))
                if use_persistent_db:
                    await self._ensure_local_skyvern_auth()
                    org_id = _get_skyvern_org_id()
                    existing_artifact_dirs = get_existing_artifact_dirs(org_id)
                skyvern = Skyvern.local(llm_config=llm_config)
                if not use_persistent_db:
                    # Safe to call before the embedded server has served its
                    # first HTTP request: `_get_skyvern_org_id()` only parses
                    # the `SKYVERN_API_KEY` JWT from os.environ, and
                    # `get_existing_artifact_dirs()` only lists the filesystem.
                    # Neither touches the SQLite DB, so there's no race with
                    # Skyvern's lazy `Base.metadata.create_all` on startup.
                    org_id = _get_skyvern_org_id()
                    existing_artifact_dirs = get_existing_artifact_dirs(org_id)
            elif api_key:
                # Cloud Skyvern client can drive both cloud-managed browsers and
                # local/CDP-connected browser sessions without requiring local Postgres.
                skyvern = Skyvern(api_key=api_key)
            elif transport in ("local", "cdp"):
                org_id = _get_skyvern_org_id()
                existing_artifact_dirs = get_existing_artifact_dirs(org_id)
                skyvern = Skyvern.local()
            else:
                skyvern = Skyvern.local()

            # Connect to browser and navigate. For cloud CDP sessions (e.g.
            # Lexmount) the remote session may idle-time-out during Skyvern's
            # ~60 s init, leaving either a closed page or a dead BrowserContext.
            # Retry at two layers: first reopen a page on the same browser; if
            # that also fails, reopen the whole backend session and reconnect.
            max_session_retries = 2 if transport == "cdp" else 0
            total_attempts = max_session_retries + 1
            # Exception tuple for best-effort cleanup of a dead browser/session.
            # Built at runtime because TargetClosedError may be None if neither
            # playwright import path worked above.
            _close_exc_types: tuple[type[BaseException], ...] = (
                OSError,
                RuntimeError,
                TimeoutError,
            )
            if TargetClosedError is not None:
                _close_exc_types = _close_exc_types + (TargetClosedError,)

            for session_attempt in range(total_attempts):
                # Close any previous dead browser before reconnecting. A dead
                # Playwright browser raises TargetClosedError on close(), so we
                # include it in the swallowed types — otherwise a second retry
                # attempt would crash the retry loop itself.
                if browser is not None:
                    try:
                        await browser.close()
                    except _close_exc_types as exc:
                        logger.warning("Ignoring error closing dead browser: %s", exc)
                    browser = None

                if transport == "cdp":
                    if not cdp_url:
                        raise ValueError(
                            f"CDP URL is required for browser backend: {browser_id}"
                        )
                    browser = await skyvern.connect_to_browser_over_cdp(cdp_url)
                elif browser_id == "skyvern-cloud":
                    proxy_location = agent_config.get("proxy_location")
                    browser = await skyvern.launch_cloud_browser(
                        timeout=float(timeout), proxy_location=proxy_location
                    )
                elif transport == "local":
                    user_data_dir = str(
                        Path(tempfile.gettempdir())
                        / f"skyvern-browser-{task_id}-{uuid.uuid4().hex[:8]}"
                    )
                    # Only pass `args=` when we actually have something to add.
                    # This narrows the kwarg-compatibility surface: users with no
                    # local_proxy_* configured land on the same launch_local_browser
                    # call shape as before this PR, regardless of skyvern version.
                    chromium_args = _build_local_chromium_args(
                        agent_config,
                        session_context.metadata.get("local_proxy"),
                    )
                    launch_kwargs: dict[str, Any] = {
                        "headless": headless,
                        "user_data_dir": user_data_dir,
                    }
                    if chromium_args:
                        launch_kwargs["args"] = chromium_args
                    browser = await skyvern.launch_local_browser(**launch_kwargs)
                else:
                    raise ValueError(
                        f"Unsupported browser backend for skyvern agent: "
                        f"backend_id={browser_id}, transport={transport}"
                    )

                try:
                    page = await browser.get_working_page()
                    if TargetClosedError is None:
                        await page.goto(url, timeout=timeout * 1000)
                    else:
                        try:
                            await page.goto(url, timeout=timeout * 1000)
                        except TargetClosedError as exc:
                            logger.warning(
                                "Working page closed (likely idle timeout); "
                                "opening a new page. %s",
                                exc,
                            )
                            page = await browser.new_page()
                            await page.goto(url, timeout=timeout * 1000)
                    break  # Page ready.
                except Exception as exc:
                    is_dead_context = TargetClosedError is not None and isinstance(
                        exc, TargetClosedError
                    )
                    if not is_dead_context or session_attempt >= max_session_retries:
                        raise
                    logger.warning(
                        "Browser context died during skyvern init "
                        "(attempt %d/%d); reopening %s backend session: %s",
                        session_attempt + 1,
                        total_attempts,
                        browser_id,
                        exc,
                    )
                    # Close the dead session first; null out session_context so
                    # the outer `finally` doesn't double-close it if the
                    # backend.open() below raises before we can reassign.
                    old_session_context, session_context = session_context, None
                    try:
                        backend.close(old_session_context)
                    except (ConnectionError, OSError, RuntimeError, TimeoutError) as close_exc:
                        logger.warning("Ignoring error closing dead session: %s", close_exc)
                    session_context = backend.open(
                        agent_name=self.name, agent_config=agent_config
                    )
                    cdp_url = session_context.cdp_url

            # Run the task
            try:
                run_response = await asyncio.wait_for(
                    skyvern.run_task(
                        prompt=task_prompt,
                        engine=engine,
                        max_steps=max_steps,
                        timeout=float(timeout),
                        url=url,
                        browser_session_id=browser.browser_session_id,
                        browser_address=browser.browser_address,
                        include_action_history_in_verification=include_action_history,
                        max_screenshot_scrolls=max_screenshot_scrolls,
                    ),
                    timeout=timeout,
                )

                # Wait for run completion
                result = await self._wait_for_completion(skyvern, run_response.run_id, timeout)

            except TimeoutError:
                error_msg = f"Timeout after {timeout} seconds"
                logger.error(f"Task {task_id} timed out after {timeout} seconds")
                timeout_end = time.time()
                end_to_end_ms = int((timeout_end - start_time) * 1000)

                # Skyvern already wrote per-step artifacts for the work done
                # before the deadline; collect them so a timed-out run still
                # reports steps, actions, screenshots, and token usage.
                timeout_screenshots = 0
                timeout_steps = 0
                timeout_actions: list[str] = []
                timeout_usage: dict[str, Any] = {}
                if browser_id in _LOCAL_ARTIFACT_BROWSERS:
                    timeout_screenshots, timeout_steps, timeout_actions, timeout_usage = (
                        _collect_run_artifacts(
                            trajectory_dir,
                            start_time,
                            timeout_end,
                            org_id=org_id,
                            existing_dirs_before_task=existing_artifact_dirs,
                            task_prompt=task_prompt,
                            include_sibling_dirs=include_sibling_dirs,
                        )
                    )

                if timeout_screenshots == 0 and page:
                    await _capture_page_screenshot(
                        page, trajectory_dir / "screenshot-1.png", "timeout"
                    )

                return AgentResult(
                    task_id=task_id,
                    task=task_prompt,
                    timestamp=datetime.now(UTC),
                    answer=f"[Task Failed: {error_msg}]",
                    env_status="success",  # type: ignore[arg-type]
                    agent_done="timeout",  # type: ignore[arg-type]
                    model_id=model_id,
                    browser_id=browser_id,
                    action_history=timeout_actions,
                    metrics=AgentMetrics(
                        end_to_end_ms=end_to_end_ms,
                        steps=timeout_steps,
                        usage=AgentUsage(**timeout_usage) if timeout_usage else None,
                    ),
                    config=config_info,
                    error=error_msg,
                )

            end_time = time.time()
            end_to_end_ms = int((end_time - start_time) * 1000)

            # Extract result
            final_result = ""
            raw_output = None
            raw_status = "completed"
            screenshot_urls: list[str] = []
            recording_url = None
            extracted_information = None
            failure_reason = None
            skyvern_errors = None

            if result:
                if hasattr(result, "output"):
                    final_result, raw_output = _normalize_answer_text(result.output)
                if hasattr(result, "status"):
                    raw_status = (
                        str(result.status.value)
                        if hasattr(result.status, "value")
                        else str(result.status)
                    )
                if hasattr(result, "screenshot_urls"):
                    screenshot_urls = result.screenshot_urls or []
                if hasattr(result, "recording_url"):
                    recording_url = result.recording_url
                if hasattr(result, "extracted_information"):
                    extracted_information = result.extracted_information
                if hasattr(result, "failure_reason"):
                    failure_reason = result.failure_reason
                if hasattr(result, "errors"):
                    skyvern_errors = result.errors

            # Map Skyvern RunStatus → (env_status, agent_done)
            _SKYVERN_STATUS_MAP: dict[str, tuple[str, str]] = {
                "completed": ("success", "done"),
                "terminated": ("success", "max_steps"),
                "timed_out": ("success", "timeout"),
                "failed": ("failed", "error"),
                "canceled": ("failed", "error"),
            }
            env_status, agent_done = _SKYVERN_STATUS_MAP.get(
                raw_status.lower(), ("failed", "error")
            )

            screenshot_count = 0
            actual_steps = 0
            matched_task_dirs: list[Path] = []

            if screenshot_urls:
                for i, screenshot_url in enumerate(screenshot_urls[:10], 1):
                    try:
                        screenshot_path = trajectory_dir / f"screenshot-{i}.png"
                        urllib.request.urlretrieve(screenshot_url, screenshot_path)

                        screenshot_count += 1
                    except (OSError, urllib.error.URLError) as exc:
                        logger.error(f"Failed to download screenshot {i}: {exc}")

            if screenshot_count == 0 and browser_id in _LOCAL_ARTIFACT_BROWSERS:
                screenshot_count, actual_steps, matched_task_dirs = (
                    copy_screenshots_from_skyvern_artifacts(
                        trajectory_dir,
                        start_time,
                        end_time,
                        org_id=org_id,
                        existing_dirs_before_task=existing_artifact_dirs,
                        task_prompt=task_prompt,
                        include_sibling_dirs=include_sibling_dirs,
                    )
                )

            # Resolve artifact dirs for token collection even when we already
            # got screenshots from cloud URLs — Skyvern writes its per-step
            # LLM payloads to the *local* artifact dir regardless of where
            # screenshots come from. Skip for skyvern-cloud browsers since
            # those runs don't produce local artifacts at all.
            if not matched_task_dirs and browser_id in _LOCAL_ARTIFACT_BROWSERS:
                matched_task_dirs = _resolve_artifact_task_dirs(
                    start_time,
                    end_time,
                    org_id=org_id,
                    existing_dirs_before_task=existing_artifact_dirs,
                    task_prompt=task_prompt,
                    include_sibling_dirs=include_sibling_dirs,
                )

            if screenshot_count == 0 and page:
                captured = await _capture_page_screenshot(
                    page, trajectory_dir / "screenshot-1.png", "final"
                )
                if captured:
                    screenshot_count = 1

            action_history = _collect_action_history(matched_task_dirs) if matched_task_dirs else []

            # Token usage resolution order:
            # 1. Local Skyvern artifact dir (preferred for local / lexmount /
            #    cdp runs): gives us per-step raw LLM responses.
            # 2. Fields on the cloud ``task_run`` object (skyvern-cloud runs,
            #    where artifacts live remotely and aren't copied back).
            usage_summary: dict[str, Any] = (
                collect_usage_from_skyvern_artifacts(matched_task_dirs)
                if matched_task_dirs
                else {}
            )
            if not usage_summary and result is not None:
                cloud_usage = _extract_usage_from_response_blob(_task_run_to_dict(result))
                if cloud_usage:
                    usage_summary = {
                        "total_prompt_tokens": cloud_usage["prompt_tokens"],
                        "total_completion_tokens": cloud_usage["completion_tokens"],
                        "total_prompt_cached_tokens": cloud_usage["cached_tokens"],
                        "total_prompt_cache_creation_tokens": cloud_usage["cache_creation_tokens"],
                        "total_tokens": cloud_usage["total_tokens"],
                        "entry_count": 1,
                    }

            agent_metadata: dict[str, Any] = {}
            if raw_output is not None:
                agent_metadata["raw_output"] = raw_output
            if extracted_information is not None:
                agent_metadata["extracted_information"] = extracted_information
            if recording_url is not None:
                agent_metadata["recording_url"] = recording_url
            if screenshot_urls:
                agent_metadata["screenshot_urls"] = screenshot_urls
            if failure_reason is not None:
                agent_metadata["failure_reason"] = failure_reason
            if skyvern_errors is not None:
                agent_metadata["skyvern_errors"] = skyvern_errors

            return AgentResult(
                task_id=task_id,
                task=task_prompt,
                timestamp=datetime.now(UTC),
                answer=final_result,
                env_status=env_status,  # type: ignore[arg-type]
                agent_done=agent_done,  # type: ignore[arg-type]
                model_id=model_id,
                browser_id=browser_id,
                action_history=action_history,
                metrics=AgentMetrics(
                    end_to_end_ms=end_to_end_ms,
                    steps=actual_steps if actual_steps > 0 else max_steps,
                    usage=AgentUsage(**usage_summary) if usage_summary else None,
                ),
                config=config_info,
                agent_metadata=agent_metadata,
                error=failure_reason if env_status == "failed" else None,
            )

        except (OSError, RuntimeError, TypeError, ValueError, KeyError, AttributeError) as e:
            error_msg = self._rewrite_local_db_error(str(e), agent_config)
            logger.error(f"Task {task_id} execution error: {e}")
            end_to_end_ms = int((time.time() - start_time) * 1000)

            return AgentResult(
                task_id=task_id,
                task=task_prompt,
                timestamp=datetime.now(UTC),
                answer=f"[Task Failed: {error_msg}]",
                env_status="failed",  # type: ignore[arg-type]
                agent_done="error",  # type: ignore[arg-type]
                model_id=model_id,
                browser_id=browser_id,
                action_history=[],
                metrics=AgentMetrics(end_to_end_ms=end_to_end_ms, steps=0),
                config=config_info,
                error=error_msg,
            )

        finally:
            await self._close_runtime_resources(
                browser=browser,
                skyvern_client=skyvern,
                task_id=task_id,
            )
            # session_context may be None here if the retry loop closed the old
            # session but `backend.open(...)` raised before reassignment — in
            # that case nothing is left to close.
            if session_context is not None:
                try:
                    backend.close(session_context)
                except (ConnectionError, OSError, RuntimeError, TimeoutError) as exc:
                    logger.error(
                        "Browser backend cleanup failed (backend=%s): %s",
                        session_context.backend_id,
                        exc,
                    )

    def _create_llm_config(self, agent_config: dict[str, Any]) -> Any:
        """Create explicit LLM config, similar to browser-use's _create_llm.

        When ENABLE_OPENAI_COMPATIBLE is set, builds an LLMConfig from agent_config
        parameters, allowing Skyvern to use our own AI provider instead of its cloud AI.
        """
        if not agent_config.get("enable_openai_compatible"):
            return None

        from skyvern.forge.sdk.api.llm.models import LiteLLMParams, LLMConfig

        canonicalize_skyvern_model_name(agent_config)

        model_name = agent_config.get("model_id") or ""
        if not model_name:
            raise ValueError(
                "model_id is required when enable_openai_compatible is true "
                "(legacy key: openai_compatible_model_name)"
            )

        api_key = agent_config.get("api_key") or os.getenv("OPENAI_COMPATIBLE_API_KEY", "")
        api_base = agent_config.get("base_url") or ""
        supports_vision = agent_config.get("supports_vision", True)
        add_assistant_prefix = agent_config.get(
            "OPENAI_COMPATIBLE_ADD_ASSISTANT_PREFIX", False
        )
        max_tokens = int(agent_config.get("max_tokens") or 4096)
        temperature_val = agent_config.get("temperature")
        temperature = float(temperature_val) if temperature_val is not None else 0.0

        request_timeout = agent_config.get("request_timeout")
        litellm_params = LiteLLMParams(
            api_key=api_key,
            api_base=api_base,
            **({"timeout": float(request_timeout)} if request_timeout is not None else {}),
        )

        return LLMConfig(
            model_name=f"openai/{model_name}",
            required_env_vars=[],
            supports_vision=supports_vision,
            add_assistant_prefix=add_assistant_prefix,
            litellm_params=litellm_params,
            max_completion_tokens=max_tokens,
            temperature=temperature,
        )

    async def _ensure_local_skyvern_auth(self) -> str:
        """Initialize Skyvern's local DB and ensure a valid API key exists.

        Calls start_forge_app() to create the database connection, then
        regenerate_local_api_key() to create the local org and API key
        in PostgreSQL. The key is also written to .env and os.environ
        so the embedded server transport can use it.
        """
        from skyvern.forge import app
        from skyvern.forge.forge_app_initializer import start_forge_app
        from skyvern.forge.sdk.db.models import Base
        from skyvern.forge.sdk.services.local_org_auth_token_service import (
            regenerate_local_api_key,
        )

        database_string = os.getenv("DATABASE_STRING", "")
        uses_temp_postgres = _ensure_temp_postgres_database(database_string)
        start_forge_app()
        if database_string.startswith("sqlite") or uses_temp_postgres:
            async with app.DATABASE.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        api_key, org_id, _, _ = await regenerate_local_api_key()
        logger.info(
            "Local Skyvern auth ready: org_id=%s, key=%s...%s",
            org_id,
            api_key[:6],
            api_key[-4:],
        )
        return api_key

    async def _close_runtime_resources(
        self,
        browser: Any | None,
        skyvern_client: Any | None,
        task_id: str,
    ) -> None:
        """Close Skyvern runtime objects; backend session is closed by manager."""
        if browser is not None:
            try:
                await browser.close()
            except (
                OSError,
                RuntimeError,
                TimeoutError,
            ) as exc:
                logger.error(f"Failed to close skyvern browser runtime for task {task_id}: {exc}")

        if skyvern_client is not None:
            try:
                await skyvern_client.aclose()
            except (
                OSError,
                RuntimeError,
                TimeoutError,
            ) as exc:
                logger.error(f"Failed to close skyvern client for task {task_id}: {exc}")

    async def _wait_for_completion(self, skyvern: Any, run_id: str, timeout: float) -> Any:
        """Wait for Skyvern task to complete."""
        if RunStatus is None:
            raise ImportError("skyvern dependency is not available")

        async with asyncio.timeout(timeout):
            while True:
                task_run = await skyvern.get_run(run_id)
                if RunStatus(task_run.status).is_final():
                    return task_run
                await asyncio.sleep(1)

    @staticmethod
    def _require_skyvern_dependencies() -> None:
        if Skyvern is None or RunEngine is None or RunStatus is None or skyvern_settings is None:
            message = (
                _SKYVERN_IMPORT_ERROR
                or "skyvern dependencies are missing. Install with --extra skyvern."
            )
            raise ImportError(message)
