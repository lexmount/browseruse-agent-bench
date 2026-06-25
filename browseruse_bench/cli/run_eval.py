"""
run-eval orchestration: run a benchmark then evaluate its results in one call.

The platform invokes this with the same flags it would pass to ``run``; it
forwards them to ``bubench run``, then evaluates the exact run directory the
run stage produced. The two stages line up even when the model was passed
through (``--model <id>``) rather than configured, when ``--timestamp`` resumes
an older run, or when some tasks failed (a completed run with task failures is
still scored). Eval is skipped only when the run produced no output directory
(a genuine setup/infra failure) or for ``--dry-run`` / ``--skip-eval``.

``--model`` / ``--model-name`` here selects the *agent* model for the run stage
(it also names the experiments dir eval reads). It is not the eval judge model
— that comes from ``eval.model`` in config.yaml — so it is intentionally not
forwarded to the eval stage.

``--report-output-dir <file>`` is a caller-facing flag (consumed here, not
forwarded): when the run produces an experiment directory, its repo-relative
path (e.g. ``experiments/LexBench-Browser/global/cursor/gpt-5.2/20260613_074712``)
is written to *file*, so the caller can locate/upload artifacts without knowing
bench's directory layout. It is written whenever a dir exists (incl. partial
task failures, eval failure, or interrupt) and not written when the run
produced nothing.

Concurrency: the run stage is told to write its resolved output directory to a
per-invocation marker file (``run --write-output-dir``), so eval binds to the
exact directory this process produced even when several run-eval jobs for the
same agent/data/model/split overlap in wall-clock time. run-eval still confirms
that directory's tasks/ was written by this invocation (vs a stale ``--timestamp``
resume that reran no task). The tasks/ mtime heuristic is the fallback for an
older run stage that does not emit the marker.
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from pathlib import Path

from browseruse_bench.cli import CONFIG_PATH
from browseruse_bench.utils import (
    REPO_ROOT,
    load_config_file,
    load_data_info,
    normalize_agent_name,
    normalize_benchmark_name,
    resolve_agent_inline_config,
    resolve_output_model_id,
    resolve_split,
)

logger = logging.getLogger(__name__)


def _shared_parser() -> argparse.ArgumentParser:
    """Parser for only the flags needed to bridge run -> eval (rest forwarded)."""
    # allow_abbrev=False: otherwise run's --mode is matched as a prefix of our
    # --model and consumed here instead of being forwarded to the run stage.
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--agent")
    parser.add_argument("--data")
    parser.add_argument("--split", default=None)
    parser.add_argument("--model-name", "--model", dest="model_name", default=None)
    parser.add_argument("--browser-id", dest="browser_id", default=None)
    parser.add_argument("--agent-config", dest="agent_config", default=None)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--data-source", dest="data_source", default=None)
    parser.add_argument("--force-download", dest="force_download", action="store_true")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true")
    parser.add_argument("--skip-eval", dest="skip_eval", action="store_true")
    # Caller-facing: where to report the produced experiment dir. Consumed here
    # (not forwarded to run) — distinct from run's internal --write-output-dir.
    parser.add_argument("--report-output-dir", dest="report_output_dir", default=None)
    return parser


# Flags run-eval owns and must not forward to the run stage.
_RUN_EVAL_BOOL_FLAGS = {"--skip-eval"}
_RUN_EVAL_VALUE_FLAGS = {"--report-output-dir"}


def _strip_run_eval_flags(args: list[str]) -> list[str]:
    """Drop run-eval's own flags (and their values) from the run-stage argv."""
    out: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        key = arg.split("=", 1)[0]
        if key in _RUN_EVAL_VALUE_FLAGS:
            i += 1 if "=" in arg else 2
            continue
        if arg in _RUN_EVAL_BOOL_FLAGS:
            i += 1
            continue
        out.append(arg)
        i += 1
    return out


def _report_output_dir(report_file: str, run_dir: Path) -> None:
    """Write the produced run dir's repo-relative path for the caller."""
    try:
        rel = run_dir.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = run_dir.as_posix()  # outside the repo: report the absolute path
    try:
        Path(report_file).write_text(rel + "\n", encoding="utf-8")
        logger.info("[run-eval] Reported output dir to %s: %s", report_file, rel)
    except OSError as exc:
        logger.warning("[run-eval] Failed to write --report-output-dir %s: %s", report_file, exc)


# Eval-only options the run subparser rejects: strip them from the run stage
# and route them to eval. (--data-source / --force-download are shared and
# stay on both.)
_EVAL_ONLY_VALUE_FLAGS = {
    "--score-threshold", "--num-worker", "--api-key", "--base-url", "--eval-strategy",
    "--task-ids-file", "--exclude-task-ids-file",
}
_EVAL_ONLY_BOOL_FLAGS = {"--force-reeval"}


def _partition_eval_only(args: list[str]) -> tuple[list[str], list[str]]:
    """Split argv into (run-stage args, eval-only args run would reject)."""
    run_args: list[str] = []
    eval_only: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        key = arg.split("=", 1)[0]
        if key in _EVAL_ONLY_VALUE_FLAGS:
            if "=" in arg:
                eval_only.append(arg)
                i += 1
            else:
                eval_only.extend(args[i:i + 2])
                i += 2
        elif key in _EVAL_ONLY_BOOL_FLAGS:
            eval_only.append(arg)
            i += 1
        else:
            run_args.append(arg)
            i += 1
    return run_args, eval_only


def _source_config(root_config: dict, agent_config: str | None) -> dict:
    """The config the run stage resolves runtime values from (--agent-config or root)."""
    if not agent_config:
        return root_config
    cfg_path = Path(agent_config)
    if not cfg_path.is_absolute():
        cfg_path = Path.cwd() / cfg_path
    return load_config_file(cfg_path) if cfg_path.exists() else root_config


def _run_output_base(agent: str, data: str, split: str | None, model_id: str) -> Path:
    """The experiments dir the run writes timestamped subdirs into."""
    benchmark = normalize_benchmark_name(data)
    data_info = load_data_info(REPO_ROOT / "browseruse_bench" / "data" / benchmark)
    resolved_split = resolve_split(split, data_info)
    return REPO_ROOT / "experiments" / benchmark / resolved_split / agent / model_id


def _run_dir_mtimes(base: Path) -> dict[str, float]:
    """Map each run dir name to its tasks/ mtime (run dirs have a tasks/ subdir)."""
    snapshot: dict[str, float] = {}
    if not base.is_dir():
        return snapshot
    for p in base.iterdir():
        tasks = p / "tasks"
        if p.is_dir() and tasks.is_dir():
            try:
                snapshot[p.name] = tasks.stat().st_mtime
            except OSError:
                continue
    return snapshot


def _run_dir_written_since(base: Path, before: dict[str, float]) -> str | None:
    """Newest run dir that this run created or updated, vs a pre-run snapshot.

    A dir is this run's output if it is new (absent from *before*) or its
    tasks/ mtime advanced; a stale prior dir whose mtime did not change is
    ignored, even on a same-wall-clock-second name collision.
    """
    fresh = [name for name, mtime in _run_dir_mtimes(base).items()
             if name not in before or mtime > before[name]]
    return max(fresh) if fresh else None


def _dir_written_this_run(run_dir: Path, before: dict[str, float]) -> bool:
    """Whether *run_dir*'s tasks/ was created or updated since the *before* snapshot.

    Distinguishes a dir this invocation actually wrote from a stale resume
    target (e.g. ``--timestamp`` accepted but no task reran before the run
    failed), where the trajectories are from a prior run.
    """
    tasks = run_dir / "tasks"
    if not tasks.is_dir():
        return False
    try:
        current = tasks.stat().st_mtime
    except OSError:
        return False
    prior = before.get(run_dir.name)
    return prior is None or current > prior


def _bind_run_dir(
    marker_emitted: bool,
    run_dir: Path | None,
    output_base: Path | None,
    model_id: str | None,
    pre_mtimes: dict[str, float],
) -> Path | None:
    """The full run dir this invocation produced, or None when it produced nothing.

    A marker-capable run is authoritative (no mtime fallback). The dir is bound
    only if this invocation actually wrote it (so a stale --timestamp resume is
    not scored / reported). For an older run stage with no marker, fall back to
    the mtime heuristic under *output_base*.
    """
    if marker_emitted:
        if run_dir is not None and model_id and _dir_written_this_run(run_dir, pre_mtimes):
            return run_dir
        return None
    if output_base is not None and model_id:
        fresh = _run_dir_written_since(output_base, pre_mtimes)
        if fresh:
            return output_base / fresh
    return None


def _read_marker(marker: Path) -> tuple[bool, Path | None]:
    """Read the run's emitted output dir from its marker file.

    Returns ``(emitted, run_dir)``: *emitted* is True when the run stage wrote
    a path (it is marker-capable), regardless of whether that path is a usable
    run dir; *run_dir* is the path only when it is an actual run dir (has a
    tasks/ subdir). The two are distinct so a marker-capable run with a
    stale/invalid marker is never silently re-bound by the mtime fallback.
    """
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False, None
    if not text:
        return False, None
    run_dir = Path(text)
    return True, (run_dir if (run_dir / "tasks").is_dir() else None)


def _invoke_cli(argv: list[str]) -> int:
    """Run a bubench subcommand, returning its exit code.

    ``cli.main`` is wrapped by ``handle_cli_errors``, which calls ``sys.exit``
    and therefore raises SystemExit instead of returning — catch it so the two
    stages can be chained in one process.
    """
    from browseruse_bench.cli import main as cli_main

    try:
        cli_main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def run_and_eval(argv: list[str] | None = None) -> int:
    """Run a benchmark then evaluate the run it produced; return eval's exit code."""
    raw_args = list(argv) if argv is not None else None
    known, _ = _shared_parser().parse_known_args(raw_args)

    root_config = load_config_file(CONFIG_PATH)
    defaults = root_config.get("default", {})
    # Mirror configure_run_parser/eval's default agent so an omitted --agent
    # with no default.agent resolves to the same path both stages use.
    agent = known.agent or defaults.get("agent", "Agent-TARS")
    data = known.data or defaults.get("data") or defaults.get("benchmark", "Online-Mind2Web")
    canonical_agent = normalize_agent_name(agent, root_config)
    source_cfg = _source_config(root_config, known.agent_config)

    model_id = resolve_output_model_id(
        canonical_agent,
        resolve_agent_inline_config(canonical_agent, source_cfg, known.model_name, known.browser_id) or {},
    )
    output_base = (
        _run_output_base(canonical_agent, data, known.split, model_id) if model_id else None
    )

    # run-eval's own flags (--skip-eval, --report-output-dir) are consumed here,
    # not forwarded; eval-only options the run parser rejects are routed to the
    # eval stage; the rest is forwarded to run verbatim. The run stage writes
    # its resolved output dir to a per-invocation marker file so eval binds to
    # exactly this run even under concurrency.
    forwardable = _strip_run_eval_flags(list(raw_args or []))
    run_only, eval_only = _partition_eval_only(forwardable)
    fd, marker_path = tempfile.mkstemp(prefix="run-eval-outdir-")
    os.close(fd)  # the run subprocess writes this path; do not hold the fd open
    marker = Path(marker_path)
    run_argv = ["run", *run_only, "--write-output-dir", str(marker)]
    # Snapshot run dirs before the run for the mtime fallback (older run stages
    # that do not honor --write-output-dir).
    pre_mtimes = _run_dir_mtimes(output_base) if output_base else {}
    logger.info("[run-eval] Stage 1/2: run")
    try:
        run_rc = _invoke_cli(run_argv)
        marker_emitted, run_dir = _read_marker(marker)
    finally:
        marker.unlink(missing_ok=True)

    # The dir this invocation produced (None when it produced nothing). model_id
    # (resolved the same way run does) names the experiments subdir incl.
    # slash-bearing ids like "openai/gpt-5.4"; the timestamp is its final part.
    bound_dir = _bind_run_dir(marker_emitted, run_dir, output_base, model_id, pre_mtimes)

    # Report the produced dir to the caller whenever a dir exists (even on
    # partial task failures, eval failure, or interrupt — so artifacts can
    # still be uploaded); skip only when the run produced nothing.
    if bound_dir is not None and known.report_output_dir:
        _report_output_dir(known.report_output_dir, bound_dir)

    if known.dry_run or known.skip_eval:
        logger.info("[run-eval] %s set; stopping after run.", "--dry-run" if known.dry_run else "--skip-eval")
        return run_rc
    # An interrupted run (SIGINT/SIGTERM -> 130) was cut short; do not auto-score
    # it as a complete run. Propagate the interrupt instead.
    if run_rc == 130:
        logger.error("[run-eval] Run interrupted (exit 130); skipping eval.")
        return run_rc
    if bound_dir is None or not model_id:
        logger.error(
            "[run-eval] Run produced no fresh output directory (exit %d); skipping eval.", run_rc
        )
        return run_rc or 1

    eval_model_id, run_ts = model_id, bound_dir.name
    eval_argv = [
        "eval", "--agent", agent, "--data", data,
        "--model-id", eval_model_id, "--timestamp", run_ts,
    ]
    if known.split is not None:
        eval_argv += ["--split", known.split]
    if known.agent_config is not None:
        eval_argv += ["--agent-config", known.agent_config]
    if known.data_source is not None:
        eval_argv += ["--data-source", known.data_source]
    if known.force_download:
        eval_argv += ["--force-download"]
    eval_argv += eval_only
    logger.info("[run-eval] Stage 2/2: eval (model_id=%s, timestamp=%s)", eval_model_id, run_ts)
    return _invoke_cli(eval_argv)
