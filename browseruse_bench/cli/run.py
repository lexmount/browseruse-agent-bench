from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from browseruse_bench.browsers.login_contexts import (
    get_by_site_profile as login_get_by_site_profile,
)
from browseruse_bench.browsers.login_contexts import (
    resolve_site_for_url,
)
from browseruse_bench.browsers.providers.lexmount import (
    LEXMOUNT_PROFILE_ENV_KEY,
    LOGIN_CONTEXT_ID_ENV_KEY,
    normalize_profile_keys,
)
from browseruse_bench.browsers.registry import canonical_browser_id
from browseruse_bench.browsers.session_state import SESSION_STATE_ENV_KEY
from browseruse_bench.utils import (
    IS_WINDOWS,
    REPO_ROOT,
    DataSource,
    add_common_task_args,
    add_file_handler,
    add_script_log_handler,
    apply_skyvern_env,
    check_uv_available,
    ensure_venv,
    filter_completed_tasks,
    filter_tasks,
    filter_tasks_by_region,
    get_default_split,
    get_default_version,
    handle_cli_errors,
    install_agent_dependencies,
    is_task_completed_by_result_json,
    load_config_file,
    load_data_info,
    load_dataset_file,
    load_env_file,
    load_tasks_with_benchmark_support,
    normalize_agent_name,
    normalize_benchmark_name,
    resolve_agent_entry,
    resolve_agent_inline_config,
    resolve_agent_venv_path,
    resolve_split,
    resolve_timeout_value,
    setup_logger,
)
from browseruse_bench.utils.run_identity import (
    INCLUDE_RAW_MACHINE_IDENTIFIERS_ENV_KEY,
    MACHINE_ID_ENV_KEY,
    MACHINE_IDENTITY_ENV_KEY,
    collect_machine_identity,
)

CONFIG_PATH = REPO_ROOT / "config.yaml"

# Preload .env from root directory for unified configuration reading
load_env_file(REPO_ROOT / ".env")

# Setup logger
logger = setup_logger("run")


def resolve_lexmount_routing_for_task(
    task_inline_cfg: dict[str, Any] | None,
    task_info: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Decide which lexmount profile and login context apply to one task.

    Returns ``(profile_key, login_context_id)``, both possibly None. Profile is
    set when the task's ``website_region`` matches a configured profile under
    ``lexmount_profiles``. Login context is looked up per (site, profile) and
    soft-fails to None when no saved context exists — the agent is then
    expected to attempt the task without login state.
    """
    if task_inline_cfg is None or str(task_inline_cfg.get("browser_id") or "") != "lexmount":
        return None, None

    profile_candidate = str(task_info.get("website_region") or "").strip().lower()
    profiles = normalize_profile_keys(task_inline_cfg.get("lexmount_profiles"))
    profile_key = profile_candidate if profile_candidate and profile_candidate in profiles else None

    if not task_info.get("login_required"):
        return profile_key, None

    site = resolve_site_for_url(
        task_info.get("target_website")
        or task_info.get("task_start_url")
        or task_info.get("url")
    )
    if not site:
        return profile_key, None
    entry = login_get_by_site_profile(site, profile_key)
    context_id = str(entry["context_id"]) if entry and entry.get("context_id") else None
    return profile_key, context_id


def _resolve_split_entry(splits: dict[str, Any], split: str) -> str:
    """Resolve split entry into a data file path."""
    if split not in splits:
        available = ", ".join(sorted(splits.keys()))
        raise SystemExit(f"[FAILED] Unknown split: {split}. Available: {available}")
    splits_conf = splits[split]
    if isinstance(splits_conf, str):
        return splits_conf
    if isinstance(splits_conf, dict):
        for key in ("path", "file", "filename"):
            candidate = splits_conf.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        raise SystemExit(
            "[FAILED] Split config must be a string or include a valid path key "
            "('path', 'file', or 'filename')"
        )
    raise SystemExit(f"[FAILED] Invalid split config type: {type(splits_conf).__name__}")


def resolve_data_file(benchmark_path: Path, split: str | None) -> str:
    """Resolve data file based on split.

    benchmark_path is the dataset root containing data_info.json.
    """
    data_info = load_data_info(benchmark_path)

    if not data_info:
        raise SystemExit("[FAILED] data_info.json not found or empty")

    if not split:
        split = get_default_split(data_info)

    if "split" in data_info:
        return _resolve_split_entry(data_info["split"], split)

    if "version_split" in data_info:
        version = get_default_version(data_info)
        if not version:
            raise SystemExit("[FAILED] data_info.json missing default_version for legacy version_split")
        logger.info("Using default version: %s", version)
        splits = data_info["version_split"].get(version, {})
        return _resolve_split_entry(splits, split)

    raise SystemExit("[FAILED] data_info.json format is incorrect, must include split")


_AVOID_LOOPS_TAIL = (
    "Avoid action loops: do not repeatedly switch between the same tabs or click the same filter "
    "more than twice. If a filter has no data after 1 retry, fallback to available results, "
    "return the best-effort answer with clear uncertainty, and finish."
)


def _prompt_format_for_benchmark(benchmark_name: str) -> tuple[str, str | None]:
    """Return ``(prompt_fmt, prompt_fmt_multi)`` for the benchmark.

    ``prompt_fmt`` is the single-site template (placeholders ``{task}`` and
    ``{url}``). ``prompt_fmt_multi`` is the template for tasks whose
    ``target_website`` lists several sites (e.g. ``a.com + b.com``); it adds a
    ``{urls}`` placeholder for the full site list. ``load_tasks`` picks one of
    the two per task based on how many URLs the task declares, so a multi-site
    task is never pinned to a single-site "use only" constraint.

    The second element is ``None`` for benchmarks that need only one template
    (Odysseys is already permissive across sites).
    """
    if benchmark_name == "Odysseys":
        prompt_fmt = (
            "{task}\n"
            "Start from {url}. You may visit any websites needed to complete the task, "
            "and keep requested proof pages open when the task asks for visual evidence.\n"
            + _AVOID_LOOPS_TAIL
        )
        return prompt_fmt, None
    prompt_fmt = (
        "{task}\n"
        "Use only {url} to achieve the task. Regional or country versions of the same "
        "site (a different subdomain or domain ending) count as the same site and are "
        "allowed; do not navigate to unrelated third-party sites. Starting URL: {url}\n"
        + _AVOID_LOOPS_TAIL
    )
    prompt_fmt_multi = (
        "{task}\n"
        "You may use the following websites to complete the task: {urls}. "
        "Start with {url}.\n"
        + _AVOID_LOOPS_TAIL
    )
    return prompt_fmt, prompt_fmt_multi


# Running subprocesses (keyed by pid) — accessed by signal handler.
_processes: dict[int, subprocess.Popen] = {}


def _resolve_output_model_id(agent_name: str, agent_cfg: dict[str, Any]) -> str | None:
    if agent_name == "skyvern":
        return (
            agent_cfg.get("model_id")
            or agent_cfg.get("MODEL_ID")
            or agent_cfg.get("openai_compatible_model_name")
            or agent_cfg.get("OPENAI_COMPATIBLE_MODEL_NAME")
            or agent_cfg.get("engine")
            or agent_cfg.get("ENGINE")
        )
    return agent_cfg.get("model_id") or agent_cfg.get("MODEL_ID")


_REDACTED_CONFIG_VALUE = "<redacted>"
_SECRET_KEY_PARTS = ("api_key", "apikey", "secret", "password", "private_key")
_SECRET_KEYS = {
    "authorization",
    "proxy_authorization",
    "token",
    "auth_token",
    "access_token",
    "refresh_token",
    "bearer_token",
    "id_token",
    "hf_token",
    "github_token",
}
_NON_SECRET_TOKEN_KEYS = {"max_tokens", "max_output_tokens"}


def _is_secret_config_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized in _NON_SECRET_TOKEN_KEYS:
        return False
    if normalized in _SECRET_KEYS:
        return True
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact_config_secrets(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_secret_config_key(key):
        return _REDACTED_CONFIG_VALUE if value else value
    if isinstance(value, dict):
        return {
            item_key: _redact_config_secrets(item_value, item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_config_secrets(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# task_brief.log: lightweight per-task digest (step numbers + action names)
# Parsed out of the subprocess stdout stream as it is teed to runtime.log.
# ---------------------------------------------------------------------------
_RE_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_RE_STEP = re.compile(r"\U0001f4cd Step (\d+)")
_RE_ACTION_NAME = re.compile(r"\u25b6\ufe0f\s+(?:\[\d+/\d+\]\s+)?([\w-]+)")
_RE_STATUS = re.compile(r"^\[(RUNNING|SUCCESS|FAILED|TIMING)\]")
_RE_FINAL = re.compile(r"Final Result:")


def _classify_js_code_for_log(code: str) -> str:
    lowered = code.lower()
    if any(token in lowered for token in ("localstorage", "sessionstorage", "document.cookie", "indexeddb")):
        return "storage"
    if any(token in lowered for token in ("fetch(", "xmlhttprequest", "performance.getentries")):
        return "network"
    if any(token in lowered for token in (
        ".click(",
        "dispatchevent",
        "setattribute",
        "appendchild",
        "removechild",
        "createelement",
    )):
        return "modify_page"
    if any(token in lowered for token in ("location.", "document.title", "document.readystate", "window.inner")):
        return "page_state"
    if (
        re.search(r"queryselectorall\(\s*['\"]a(?:[\\.\\[#:'\"]|$)", lowered)
        or (".href" in lowered and "location.href" not in lowered)
    ):
        return "extract_links"
    if (
        re.search(r"queryselector(?:all)?\(\s*['\"][^'\"]*(?:input|textarea|select)", lowered)
        or any(token in lowered for token in (".value", ".checked"))
    ):
        return "form_state"
    if any(token in lowered for token in (
        "queryselector",
        "getelement",
        "innertext",
        "textcontent",
        "innerhtml",
        "document.body",
    )):
        return "read_dom"
    return "custom"


def _clarify_agent_stdout_line(line: str) -> str:
    """Make third-party agent tool names clearer in run logs."""
    marker = "evaluate: code:"
    if marker not in line:
        return line
    prefix, _, code = line.partition(marker)
    category = _classify_js_code_for_log(code)
    return f"{prefix}execute_js({category}): code:{code}"


class _TaskBriefWriter:
    """Extract key event lines from subprocess output into task_brief.log."""

    def __init__(self, path: Path):
        self._fh = open(path, "w", encoding="utf-8")  # noqa: SIM115
        self._cur_step: str | None = None
        self._cur_actions: list[str] = []

    def _flush_step(self) -> None:
        if self._cur_step is None:
            return
        actions = ", ".join(self._cur_actions) if self._cur_actions else ""
        try:
            self._fh.write(f"Step {self._cur_step}: {actions}\n")
        finally:
            self._cur_step = None
            self._cur_actions = []

    def feed(self, line: str) -> None:
        clean = _RE_ANSI.sub("", line.rstrip("\n"))

        step_m = _RE_STEP.search(clean)
        if step_m:
            self._flush_step()
            self._cur_step = step_m.group(1)
            return

        if self._cur_step is not None:
            act_m = _RE_ACTION_NAME.search(clean)
            if act_m:
                self._cur_actions.append(act_m.group(1))
                return

        if _RE_STATUS.match(clean):
            self._flush_step()
            self._fh.write(f"{clean}\n")
            return

        if _RE_FINAL.search(clean):
            self._flush_step()
            self._fh.write(f"{clean}\n")

    def close(self) -> None:
        self._flush_step()
        with suppress(OSError):
            self._fh.close()


def _write_run_manifest(
    output_dir: Path,
    *,
    config: dict[str, Any] | None = None,
    agent_config: dict[str, Any] | None = None,
    resolved_agent_config: dict[str, Any] | None = None,
    run_context: dict[str, Any] | None = None,
    machine_identity: dict[str, Any] | None = None,
) -> None:
    """Write a redacted config snapshot for reproducibility."""
    try:
        runtime_config = resolved_agent_config or agent_config or config or {}
        snapshot = {
            "run": _redact_config_secrets(run_context or {}),
            "machine": machine_identity or collect_machine_identity(),
            "runtime_config": _redact_config_secrets(runtime_config),
        }
        config_snapshot_path = output_dir / "config_snapshot.json"
        with open(config_snapshot_path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        logger.info("Config snapshot written to %s", config_snapshot_path)
    except OSError as exc:
        logger.warning("Failed to write config_snapshot.json: %s", exc)


def _reset_task_workspace(task_workspace: Path) -> None:
    """Start one task run from an empty workspace."""
    if task_workspace.is_symlink() or task_workspace.is_file():
        task_workspace.unlink()
    elif task_workspace.exists():
        shutil.rmtree(task_workspace)
    task_workspace.mkdir(parents=True, exist_ok=True)


_processes_lock = __import__("threading").Lock()


def _read_positive_int_from_env(env_key: str, default: int) -> int:
    raw_value = os.getenv(env_key)
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid %s value %r, falling back to %s",
            env_key,
            raw_value,
            default,
        )
        return default
    if parsed <= 0:
        logger.warning(
            "Invalid %s value %r, expected a positive integer; falling back to %s",
            env_key,
            raw_value,
            default,
        )
        return default
    return parsed


_SIGINT_GRACE_SECONDS = _read_positive_int_from_env("BUBENCH_SIGINT_GRACE_SECONDS", 20)
_SIGTERM_GRACE_SECONDS = _read_positive_int_from_env("BUBENCH_SIGTERM_GRACE_SECONDS", 8)
_SESSION_CLEANUP_TIMEOUT_SECONDS = _read_positive_int_from_env(
    "BUBENCH_SESSION_CLEANUP_TIMEOUT_SECONDS",
    30,
)
_TASK_RUNNER_WATCHDOG_GRACE_SECONDS = _read_positive_int_from_env(
    "BUBENCH_TASK_RUNNER_WATCHDOG_GRACE_SECONDS",
    60,
)
_TASK_RUNNER_RESULT_EXIT_GRACE_SECONDS = _read_positive_int_from_env(
    "BUBENCH_TASK_RUNNER_RESULT_EXIT_GRACE_SECONDS",
    20,
)


def _cleanup_orphaned_browser_session(
    session_state_file: Path,
    env: dict[str, str],
    venv_path: Path,
) -> None:
    if not session_state_file.exists():
        return

    logger.warning("Detected stale browser session state, running parent-side cleanup...")

    python_bin = "Scripts\\python.exe" if IS_WINDOWS else "bin/python"
    cleanup_cmd = [
        str(venv_path / python_bin),
        "-m",
        "browseruse_bench.browsers.orphan_cleanup",
        "--state-file",
        str(session_state_file),
    ]
    cleanup_env = env.copy()
    cleanup_env.setdefault("AGENTBAY_LOG_LEVEL", "WARNING")
    cleanup_env.setdefault("AGENTBAY_LOG_FORMAT", "compact")

    try:
        result = subprocess.run(
            cleanup_cmd,
            env=cleanup_env,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=_SESSION_CLEANUP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError, TimeoutError, ValueError) as exc:
        logger.error("Parent-side browser session cleanup failed to execute: %s", exc)
        return

    if result.stdout:
        for line in result.stdout.splitlines():
            logger.info(line)

    if result.returncode == 0:
        logger.info("Parent-side browser session cleanup finished")
    else:
        logger.error("Parent-side browser session cleanup failed (exit code=%s)", result.returncode)


# Filesystem mtime granularity / clock-step slack for the freshness check.
_RESULT_MTIME_SLACK_SECONDS = 2


def _read_fresh_result_json(
    task_id: str,
    output_dir: Path,
    newer_than: float | None = None,
) -> dict[str, Any] | None:
    """Parse tasks/<id>/result.json when non-empty and fresh.

    ``newer_than`` (epoch seconds) guards against result.json files left over
    from a previous run of the same output directory: only files modified
    after it (minus a small mtime slack) count.
    """
    result_file = output_dir / "tasks" / task_id / "result.json"
    try:
        stat = result_file.stat()
    except OSError:
        return None
    if stat.st_size == 0:
        return None
    if newer_than is not None and stat.st_mtime < newer_than - _RESULT_MTIME_SLACK_SECONDS:
        return None
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def _is_task_env_success_by_result_json(
    task_id: str,
    output_dir: Path,
    newer_than: float | None = None,
) -> bool:
    """True when result.json holds a usable non-env-failed result."""
    result = _read_fresh_result_json(task_id, output_dir, newer_than)
    if result is None:
        return False
    status = result.get("env_status") or result.get("status")
    return status == "success" and not result.get("error")


def _is_task_result_terminal(
    task_id: str,
    output_dir: Path,
    newer_than: float | None = None,
) -> bool:
    """True when result.json is complete (success OR failure): the runner is done."""
    result = _read_fresh_result_json(task_id, output_dir, newer_than)
    return result is not None and bool(result.get("env_status") or result.get("status"))


def _watchdog_deadline_seconds(task_timeout: int) -> int:
    """Hard deadline for one agent_runner subprocess.

    Agents may legitimately run one full-timeout retry on top of the first
    attempt (e.g. OpenClaw outage_retries=1), so cover two attempts plus
    grace; this still bounds runaway runners.
    """
    return 2 * task_timeout + _TASK_RUNNER_WATCHDOG_GRACE_SECONDS


def _wait_for_task_runner(
    proc: subprocess.Popen,
    task_id: str,
    output_dir: Path,
    task_timeout: int,
    prefix: str,
) -> tuple[int, bool]:
    """Wait for one agent_runner subprocess with a lingering-process watchdog.

    Some agent CLIs (e.g. OpenClaw) spawn helpers that can keep agent_runner
    alive after result.json is complete. Returns ``(returncode,
    reaped_after_result)``; the latter is True when the runner was reaped
    after its result.json was already terminal (success or failure). A hard
    deadline covering the agent-level retry budget reaps runaway runners
    either way.
    """
    started_at = time.time()
    watchdog_deadline = time.monotonic() + _watchdog_deadline_seconds(task_timeout)
    result_seen_at: float | None = None
    while True:
        try:
            return proc.wait(timeout=1), False
        except subprocess.TimeoutExpired:
            pass
        now = time.monotonic()
        if not _is_task_result_terminal(task_id, output_dir, newer_than=started_at):
            result_seen_at = None
        elif result_seen_at is None:
            result_seen_at = now
        elif now - result_seen_at >= _TASK_RUNNER_RESULT_EXIT_GRACE_SECONDS:
            logger.warning(
                "[WATCHDOG] %s result.json is already terminal but agent_runner "
                "is still alive after %ss; terminating the task process group",
                prefix,
                _TASK_RUNNER_RESULT_EXIT_GRACE_SECONDS,
            )
            _terminate_one(proc)
            returncode = proc.poll()
            return (returncode if returncode is not None else -1), True
        if now >= watchdog_deadline:
            logger.error(
                "[TIMEOUT] %s agent_runner exceeded watchdog deadline "
                "(2 x task timeout %ss + grace %ss); terminating",
                prefix,
                task_timeout,
                _TASK_RUNNER_WATCHDOG_GRACE_SECONDS,
            )
            _terminate_one(proc)
            returncode = proc.poll()
            return (returncode if returncode is not None else -1), False


def _terminate_one(proc: subprocess.Popen) -> None:
    """Gracefully terminate one subprocess (SIGINT → SIGTERM → SIGKILL)."""
    if proc.pid is None:
        return
    try:
        if IS_WINDOWS:
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    except (ProcessLookupError, OSError) as exc:
        logger.error("Failed to send SIGINT to process group (pid=%s): %s", proc.pid, exc)
        with suppress(ProcessLookupError):
            proc.terminate()
    try:
        proc.wait(timeout=_SIGINT_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if IS_WINDOWS:
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, OSError):
        with suppress(ProcessLookupError):
            proc.terminate()
    try:
        proc.wait(timeout=_SIGTERM_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if IS_WINDOWS:
            proc.kill()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        with suppress(ProcessLookupError):
            proc.kill()


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle exit signals — terminate all running subprocesses."""
    with _processes_lock:
        procs = list(_processes.values())
    if procs:
        logger.warning("Received exit signal, terminating %d subprocess(es)...", len(procs))
        for proc in procs:
            try:
                _terminate_one(proc)
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                logger.error("Error terminating subprocess (pid=%s): %s", proc.pid, exc)
    sys.exit(130)


def configure_run_parser(parser: argparse.ArgumentParser, config: dict[str, Any]) -> None:
    """Configure arguments for the run command."""
    add_common_task_args(parser)
    parser.add_argument("--agent", default=config.get("default", {}).get("agent", "Agent-TARS"))
    parser.add_argument("--data", default=config.get("default", {}).get("data") or config.get("default", {}).get("benchmark", "Online-Mind2Web"))
    parser.add_argument(
        "--split",
        default=None,
        help="Dataset split (defaults to data_info.json's default_split, falling back to 'All'). Options depend on benchmark data_info.json.",
    )
    parser.add_argument(
        "--data-source",
        default=DataSource.LOCAL,
        choices=DataSource.tolist(),
        help="Data source: local (default) or huggingface (download to HF cache)",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="Specify timestamp directory to resume or run (format: YYYYMMDD_HHmmss)",
    )
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=None,
        help=(
            "Optional path to an alternate root-config YAML (same shape as the repo "
            "config.yaml). The runtime config for --agent is resolved from that file. "
            "By default the repo root config.yaml is used."
        ),
    )
    parser.add_argument(
        "--model-name",
        "--model",
        dest="model_name",
        default=None,
        help="Optional model config name under config.yaml models to override default.model for this run.",
    )
    parser.add_argument(
        "--browser-id",
        "--browser",
        dest="browser_id",
        default=None,
        help="Optional browser backend id under config.yaml browsers to override the selected browser for this run.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Force re-download from HuggingFace cache (only applies to huggingface mode)",
    )
    parser.add_argument(
        "--group-by-site",
        dest="group_by_site",
        action="store_true",
        default=True,
        help=(
            "Run tasks grouped by target_website (stable sort). "
            "Keeps same-site tasks contiguous so login-gated evals sweep a site together. "
            "Default: on."
        ),
    )
    parser.add_argument(
        "--no-group-by-site",
        dest="group_by_site",
        action="store_false",
        help="Disable site grouping; preserve dataset order.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        metavar="N",
        help="Number of tasks to run in parallel (default: 1 = sequential).",
    )
    parser.add_argument(
        "--machine-id",
        default=None,
        help=(
            "Optional stable label for the machine running this experiment. "
            f"Defaults to ${MACHINE_ID_ENV_KEY} or hostname."
        ),
    )
    parser.add_argument(
        "--include-raw-machine-identifiers",
        action="store_true",
        help=(
            "Include raw hardware identifiers such as MAC address in run outputs. "
            f"By default only hashed identifiers are recorded; can also be enabled "
            f"with ${INCLUDE_RAW_MACHINE_IDENTIFIERS_ENV_KEY}=1."
        ),
    )
    parser.add_argument(
        "--write-output-dir",
        dest="write_output_dir",
        default=None,
        help=argparse.SUPPRESS,  # internal: run-eval reads the exact run dir from here
    )


def _resolve_run_output_dir(output_base: Path, args: argparse.Namespace) -> Path | None:
    """Resolve the run output directory, claiming a fresh one when needed.

    Dry runs must leave the experiments tree untouched — an empty timestamp
    dir left behind would pollute find-latest and leaderboard scans — so no
    directory is claimed and ``None`` is returned. Resuming an existing
    ``--timestamp`` directory is read-only here, so it is still resolved
    (and validated) for dry runs.
    """
    if args.timestamp:
        timestamp = args.timestamp.strip()
        if not re.match(r"^\d{8}_\d{6}$", timestamp):
            raise SystemExit("[FAILED] --timestamp format must be YYYYMMDD_HHmmss")
        output_dir = output_base / timestamp
        if not output_dir.exists():
            raise SystemExit(f"[FAILED] Specified timestamp directory does not exist: {output_dir}")
        logger.info("Resuming/Running in existing timestamp directory: %s", timestamp)
        return output_dir
    if args.dry_run:
        return None
    return _claim_unique_run_dir(output_base)


def _write_output_dir_marker(marker_path: str | None, output_dir: Path) -> None:
    """Emit the resolved output dir for run-eval to bind to deterministically
    (concurrency-safe: each caller passes its own marker file path)."""
    if not marker_path:
        return
    try:
        Path(marker_path).write_text(str(output_dir), encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write --write-output-dir marker: %s", exc)


def _claim_unique_run_dir(output_base: Path, max_seconds: int = 600) -> Path:
    """Atomically create a fresh ``YYYYMMDD_HHMMSS`` run dir under *output_base*.

    Two concurrent runs with identical parameters would otherwise resolve the
    same second-resolution timestamp and, with exist_ok, interleave their
    output in one directory. ``mkdir(exist_ok=False)`` is atomic, so on a
    collision we advance to the next whole second and retry. The name stays a
    strict ``YYYYMMDD_HHMMSS`` so downstream tools (eval find-latest,
    leaderboard, ``--timestamp`` resume) keep recognizing it.
    """
    base = datetime.now()
    for offset in range(max_seconds):
        candidate = output_base / (base + timedelta(seconds=offset)).strftime("%Y%m%d_%H%M%S")
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise SystemExit(
        f"[FAILED] Could not allocate a unique run output directory under {output_base} "
        f"after {max_seconds} attempts (too many concurrent identical runs)."
    )


def run_agent(agent_name: str, benchmark_name: str, config: dict[str, Any], args: argparse.Namespace) -> int:
    """
    Run agent using subprocess with process isolation per task.
    Uses the unified agent_runner.py to call modular browseruse_bench.agents code.
    """
    agent_config_dict = resolve_agent_entry(agent_name, config)

    supported = agent_config_dict.get("supported_benchmarks", [])
    if supported and benchmark_name not in supported:
        raise SystemExit(
            f"[FAILED] Agent '{agent_name}' does not support Benchmark '{benchmark_name}'\n"
            f"Supported: {', '.join(supported)}"
        )

    # Dataset root is conventional: REPO_ROOT/browseruse_bench/data/<benchmark_name>/
    benchmark_path = REPO_ROOT / "browseruse_bench" / "data" / benchmark_name
    data_info = load_data_info(benchmark_path)

    args.split = resolve_split(args.split, data_info)

    # Resolve data file
    data_file = resolve_data_file(benchmark_path, args.split)
    local_data_path = benchmark_path / data_file
    benchmark_data = load_dataset_file(
        local_path=local_data_path,
        data_info=data_info,
        data_source=args.data_source,
        force_download=args.force_download,
        split=args.split,
        benchmark_name=benchmark_name,
    )

    # Create output directory
    # Include MODEL_ID from agent config as a subfolder under the agent name
    agent_cfg = args._inline_agent_config
    model_id = _resolve_output_model_id(agent_name, agent_cfg)
    if not model_id:
        raise SystemExit(
            "[FAILED] model_id is required in agent config but not found. "
            "Please specify model_id in your agent config YAML file."
        )
    output_base = REPO_ROOT / "experiments" / benchmark_name / args.split / agent_name / model_id

    # Dry runs claim no directory and write no run files (output_dir is None
    # unless resuming an existing --timestamp directory).
    output_dir = _resolve_run_output_dir(output_base, args)
    if not args.dry_run:
        add_file_handler(logger, output_dir / "run.log", format_mode="plain")
    # The marker is emitted even on dry runs so run-eval stays on the
    # authoritative marker binding path instead of its mtime fallback; the
    # unclaimed output_base has no tasks/ subdir, so run-eval binds nothing.
    _write_output_dir_marker(getattr(args, "write_output_dir", None), output_dir or output_base)

    logger.info("Running %s on %s", agent_name, benchmark_name)
    logger.info("   Output: %s", output_dir or output_base)
    include_raw_machine_identifiers = bool(
        getattr(args, "include_raw_machine_identifiers", False)
        or str(os.getenv(INCLUDE_RAW_MACHINE_IDENTIFIERS_ENV_KEY) or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    machine_identity = collect_machine_identity(
        getattr(args, "machine_id", None),
        include_raw_identifiers=include_raw_machine_identifiers,
    )
    logger.info(
        "   Machine: %s (host=%s)",
        machine_identity.get("machine_id"),
        machine_identity.get("hostname"),
    )

    # Load tasks
    default_task_url = config.get("default", {}).get("task_start_url")
    prompt_fmt, prompt_fmt_multi = _prompt_format_for_benchmark(benchmark_name)
    tasks = load_tasks_with_benchmark_support(
        benchmark_data,
        prompt_fmt=prompt_fmt,
        default_url=default_task_url,
        prompt_fmt_multi=prompt_fmt_multi,
    )
    if not tasks:
        raise SystemExit(f"No tasks found in {benchmark_data}")

    logger.info("[SUCCESS] Loaded %s tasks", len(tasks))

    # Filter tasks
    region = getattr(args, "region", None)
    tasks = filter_tasks_by_region(tasks, region)
    task_id = getattr(args, "id", None)
    tasks_to_run = filter_tasks(tasks, args.mode, args.count, args.task_ids, task_id)

    # output_dir is None only on a fresh dry run, where nothing can be
    # completed yet.
    if args.skip_completed and output_dir is not None:
        tasks_to_run, skipped = filter_completed_tasks(
            tasks_to_run,
            output_dir,
            is_task_completed_by_result_json,
        )
        if skipped == 0:
            logger.info("[INFO] Resume: No completed tasks found")

    if not tasks_to_run:
        raise SystemExit("[FAILED] No tasks selected to run")

    # Group by site for contiguous sweeps of login-gated sites. Stable sort
    # preserves the dataset order within each group.
    if getattr(args, "group_by_site", False):
        tasks_to_run = sorted(
            tasks_to_run,
            key=lambda t: (
                resolve_site_for_url(
                    t.get("target_website") or t.get("task_start_url") or t.get("url")
                ) or "~",
            ),
        )

    if args.dry_run:
        logger.info("   [DRY RUN] Would run %s tasks using agent_runner.py", len(tasks_to_run))
        return 0

    # Determine extra flag for uv
    if agent_name == "skyvern":
        extra_name: str | None = "skyvern"
    elif agent_name == "browser-use":
        extra_name = "browser-use"
    elif agent_name == "openai-cua":
        extra_name = "openai-cua"
    elif agent_name == "webwright":
        extra_name = "webwright"
    else:
        extra_name = None

    install_targets = agent_config_dict.get("install_targets")
    if not isinstance(install_targets, list):
        install_targets = None
    elif not all(isinstance(target, str) and target.strip() for target in install_targets):
        raise SystemExit("[FAILED] Agent install_targets must be a list of non-empty strings")

    venv_path = resolve_agent_venv_path(agent_config_dict)
    use_uv = check_uv_available()
    ensure_venv(venv_path, use_uv)
    install_agent_dependencies(venv_path, extra_name, use_uv, install_targets)

    # Prepare environment
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["BROWSERUSE_BENCH_LOG_FORMAT"] = "plain"
    env[MACHINE_IDENTITY_ENV_KEY] = json.dumps(machine_identity, ensure_ascii=False)
    repo_root_str = str(REPO_ROOT)
    old_pythonpath = env.get("PYTHONPATH", "")
    if old_pythonpath:
        if repo_root_str not in old_pythonpath.split(os.pathsep):
            env["PYTHONPATH"] = os.pathsep.join([repo_root_str, old_pythonpath])
    else:
        env["PYTHONPATH"] = repo_root_str

    if agent_name == "skyvern":
        skyvern_config = args._inline_agent_config
        apply_skyvern_env(skyvern_config, env)

    # ------------------------------------------------------------------ #
    # Per-task runner (called sequentially or from a thread pool)         #
    # ------------------------------------------------------------------ #

    concurrency = max(1, getattr(args, "concurrency", 1))
    inline_cfg = getattr(args, "_inline_agent_config", None)
    python_bin = "Scripts\\python.exe" if IS_WINDOWS else "bin/python"

    def _run_one_task(task_info: dict[str, Any], idx: int) -> bool:
        """Run a single task in a subprocess. Returns True on success."""
        current_task_id = task_info["task_id"]
        task_text = task_info.get("task_text", task_info.get("task", ""))[:50]
        prefix = f"[{idx}/{len(tasks_to_run)}][{current_task_id}]"
        logger.info("\n%s\n%s %s…\n%s", "=" * 80, prefix, task_text, "=" * 80)

        task_workspace = output_dir / "tasks" / current_task_id
        _reset_task_workspace(task_workspace)
        session_state_file = task_workspace / ".browser_session_state.json"
        session_state_file.unlink(missing_ok=True)
        task_info = {**task_info, "benchmark_name": benchmark_name}

        task_info_path: Path | None = None
        tmp_cfg_path: Path | None = None
        proc: subprocess.Popen | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tf:
                json.dump(task_info, tf, ensure_ascii=False)
                task_info_path = Path(tf.name)

            cmd = [
                str(venv_path / python_bin),
                str(REPO_ROOT / "browseruse_bench/runner/agent_runner.py"),
                "--agent", agent_name,
                "--task-info", str(task_info_path),
                "--workspace", str(task_workspace),
            ]

            # Per-task runtime state for the lexmount backend. Passed via env
            # vars rather than agent_config because agent_config is for
            # user-authored agent/model choices — runtime-injected state
            # belongs on the subprocess env.
            task_inline_cfg = dict(inline_cfg) if inline_cfg else None
            lexmount_profile_key, login_context_id = resolve_lexmount_routing_for_task(
                task_inline_cfg, task_info
            )
            if task_inline_cfg is not None and str(task_inline_cfg.get("browser_id") or "") == "lexmount" \
                    and task_info.get("login_required"):
                site_for_log = resolve_site_for_url(
                    task_info.get("target_website")
                    or task_info.get("task_start_url")
                    or task_info.get("url")
                )
                if login_context_id:
                    logger.info(
                        "[LOGIN-CTX] %s site=%s profile=%s context=%s…",
                        prefix, site_for_log, lexmount_profile_key or "(default)",
                        login_context_id[:12],
                    )
                else:
                    # Soft-fail: don't short-circuit. Let the agent attempt the
                    # task without login state and surface the real "couldn't
                    # access" outcome as the case result.
                    logger.info(
                        "[LOGIN-CTX] %s site=%s profile=%s — no saved context, "
                        "running without login state",
                        prefix, site_for_log, lexmount_profile_key or "(default)",
                    )

            if task_inline_cfg is not None:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                ) as tc:
                    json.dump(task_inline_cfg, tc, ensure_ascii=False)
                    tmp_cfg_path = Path(tc.name)
                cmd.extend(["--agent-config", str(tmp_cfg_path)])
            if args.timeout:
                cmd.extend(["--timeout", str(args.timeout)])

            task_env = env.copy()
            task_env[SESSION_STATE_ENV_KEY] = str(session_state_file)
            # Always pop before conditional set so a stale parent-shell value
            # never bleeds into tasks that don't need this injection.
            task_env.pop(LOGIN_CONTEXT_ID_ENV_KEY, None)
            task_env.pop(LEXMOUNT_PROFILE_ENV_KEY, None)
            if login_context_id:
                task_env[LOGIN_CONTEXT_ID_ENV_KEY] = login_context_id
            if lexmount_profile_key:
                task_env[LEXMOUNT_PROFILE_ENV_KEY] = lexmount_profile_key

            popen_kwargs: dict[str, Any] = {
                "env": task_env,
                "cwd": str(REPO_ROOT),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
            }
            if IS_WINDOWS:
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["preexec_fn"] = os.setsid

            proc = subprocess.Popen(cmd, **popen_kwargs)
            with _processes_lock:
                _processes[proc.pid] = proc

            def _drain_runner_stdout() -> None:
                if not proc.stdout:
                    return
                for line in iter(proc.stdout.readline, ""):
                    if not line:
                        continue
                    display_line = _clarify_agent_stdout_line(line.rstrip("\n"))
                    # Prefix each output line with task id when concurrent.
                    if concurrency > 1:
                        logger.info("[%s] %s", current_task_id, display_line)
                    else:
                        logger.info(display_line)

            stdout_thread = threading.Thread(
                target=_drain_runner_stdout,
                name=f"bubench-output-{current_task_id}",
                daemon=True,
            )
            stdout_thread.start()

            task_timeout = resolve_timeout_value(args.timeout, task_inline_cfg or {})
            returncode, reaped_after_result = _wait_for_task_runner(
                proc, current_task_id, output_dir, task_timeout, prefix
            )
            stdout_thread.join(timeout=5)

            if reaped_after_result:
                if _is_task_env_success_by_result_json(current_task_id, output_dir):
                    logger.info(
                        "[SUCCESS] %s completed; killed lingering runner after result", prefix
                    )
                    return True
                logger.info(
                    "[FAILED] %s result is terminal but failed; killed lingering runner", prefix
                )
                return False

            if returncode == 0:
                logger.info("[SUCCESS] %s completed", prefix)
                return True
            logger.info("[FAILED] %s exit code %s", prefix, returncode)
            return False

        finally:
            if proc is not None:
                with _processes_lock:
                    _processes.pop(proc.pid, None)
            for _tmp in [task_info_path, tmp_cfg_path]:
                if _tmp is None:
                    continue
                with suppress(FileNotFoundError, OSError):
                    _tmp.unlink()
            _cleanup_orphaned_browser_session(
                session_state_file=session_state_file,
                env=env,
                venv_path=venv_path,
            )

    # ------------------------------------------------------------------ #
    # Execute tasks                                                        #
    # ------------------------------------------------------------------ #
    from concurrent.futures import ThreadPoolExecutor, as_completed

    success_count = 0
    failed_count = 0

    original_sigint = signal.signal(signal.SIGINT, _signal_handler)
    original_sigterm = signal.signal(signal.SIGTERM, _signal_handler)
    _counter_lock = threading.Lock()

    try:
        if concurrency == 1:
            for i, task_info in enumerate(tasks_to_run, 1):
                try:
                    ok = _run_one_task(task_info, i)
                except KeyboardInterrupt:
                    raise
                if ok:
                    success_count += 1
                else:
                    failed_count += 1
        else:
            logger.info("[INFO] Running with concurrency=%d", concurrency)
            indexed = list(enumerate(tasks_to_run, 1))
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {
                    pool.submit(_run_one_task, ti, idx): ti["task_id"]
                    for idx, ti in indexed
                }
                try:
                    for fut in as_completed(futures):
                        task_id = futures[fut]
                        try:
                            ok = fut.result()
                        except Exception as exc:  # noqa: BLE001
                            # Intentionally broad: any exception from a task
                            # should mark that task failed but never kill the
                            # whole thread pool. SystemExit / KeyboardInterrupt
                            # are BaseException and already bypass this handler.
                            logger.error("Task %s raised an exception: %s", task_id, exc)
                            ok = False
                        with _counter_lock:
                            if ok:
                                success_count += 1
                            else:
                                failed_count += 1
                except KeyboardInterrupt:
                    logger.warning("Interrupted — cancelling pending tasks")
                    for fut in futures:
                        fut.cancel()
                    raise

    except KeyboardInterrupt:
        _signal_handler(signal.SIGINT, None)
        return 130
    finally:
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)

    logger.info(
        "\n[SUMMARY] Total: %s | Success: %s | Failed: %s",
        len(tasks_to_run),
        success_count,
        failed_count,
    )
    # Dry runs returned earlier, so this always records a real run.
    try:
        _write_run_manifest(
            output_dir,
            resolved_agent_config=agent_cfg,
            run_context={
                "agent": agent_name,
                "benchmark": benchmark_name,
                "split": args.split,
                "model_id": model_id,
                "machine_id": machine_identity.get("machine_id"),
                "timestamp": output_dir.name,
                "model_name_override": getattr(args, "model_name", None),
                "browser_id_override": getattr(args, "browser_id", None),
                "mode": getattr(args, "mode", None),
                "task_ids": getattr(args, "task_ids", None),
                "task_id": getattr(args, "id", None),
                "count": getattr(args, "count", None),
                "region": getattr(args, "region", None),
                "concurrency": getattr(args, "concurrency", None),
                "agent_config_path": str(getattr(args, "agent_config", None) or ""),
            },
            machine_identity=machine_identity,
        )
    except (OSError, KeyError, AttributeError, TypeError) as exc:
        logger.warning("Failed to finalize run manifest: %s", exc)
    return 0 if failed_count == 0 else 1


def _canonicalize_cli_browser_id(browser_id: str | None, source_cfg: dict[str, Any]) -> str | None:
    """Canonicalize --browser-id to the registered backend id.

    An exact key of the config's browsers section wins — even one shadowing a
    backend id with different casing — so the inline-config exact-match
    preference is never bypassed.
    """
    if not browser_id or browser_id in (source_cfg.get("browsers") or {}):
        return browser_id
    return canonical_browser_id(browser_id)


def run_command(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Entry point for the run subcommand."""
    add_script_log_handler(logger, REPO_ROOT / "output" / "logs", "run")
    logger.info("Starting run command")
    args.agent = normalize_agent_name(args.agent, config)
    source_cfg = config
    source_label = "root config.yaml"
    if args.agent_config is not None:
        cfg_path = args.agent_config
        if not cfg_path.is_absolute():
            cfg_path = Path.cwd() / cfg_path
        if not cfg_path.exists():
            raise SystemExit(f"[FAILED] --agent-config file not found: {cfg_path}")
        source_cfg = load_config_file(cfg_path)
        source_label = str(cfg_path)

    args.browser_id = _canonicalize_cli_browser_id(args.browser_id, source_cfg)

    # --agent-config only overrides the inline runtime config (models/browsers/agents defaults).
    # Benchmark definitions and the agent registry keep coming from the root config,
    # which is why run_agent still receives `config`, not `source_cfg`.
    inline = resolve_agent_inline_config(args.agent, source_cfg, args.model_name, args.browser_id)
    if inline is None:
        model_hint = (
            f"models.{args.model_name}"
            if args.model_name
            else "default.model plus models.<name>"
        )
        raise SystemExit(
            f"[FAILED] Inline agent runtime config not found in {source_label}.\n"
            f"Hint: Configure {model_hint} in that file."
        )
    args._inline_agent_config = inline
    benchmark_name = normalize_benchmark_name(args.data)
    return run_agent(args.agent, benchmark_name, config, args)


@handle_cli_errors
def main(argv: list[str] | None = None) -> int:
    config = load_config_file(CONFIG_PATH)
    parser = argparse.ArgumentParser(prog="bubench run")
    configure_run_parser(parser, config)
    args, extra = parser.parse_known_args(argv)
    if extra:
        parser.error(f"unrecognized arguments: {' '.join(extra)}")
    return run_command(args, config)


if __name__ == "__main__":
    main()
