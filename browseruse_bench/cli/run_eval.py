"""
run-eval orchestration: run a benchmark then evaluate its results in one call.

The platform invokes this with the same flags it would pass to ``run``; it
forwards them to ``bubench run``, then evaluates the exact run directory the
run stage produced. The two stages line up even when the model was passed
through (``--model <id>``) rather than configured, when ``--timestamp`` resumes
an older run, or when some tasks failed (a completed run with task failures is
still scored). Eval is skipped only when the run produced no output directory
(a genuine setup/infra failure) or for ``--dry-run`` / ``--skip-eval``.

Concurrency: the run stage is told to write its resolved output directory to a
per-invocation marker file (``run --write-output-dir``), so eval binds to the
exact directory this process produced even when several run-eval jobs for the
same agent/data/model/split overlap in wall-clock time. The tasks/ mtime
heuristic is only a fallback for an older run stage that does not emit the
marker.
"""

from __future__ import annotations

import argparse
import logging
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
    return parser


# Eval-only options the run subparser rejects: strip them from the run stage
# and route them to eval. (--data-source / --force-download are shared and
# stay on both.)
_EVAL_ONLY_VALUE_FLAGS = {
    "--score-threshold", "--num-worker", "--api-key", "--base-url", "--eval-strategy",
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


def _read_marker(marker: Path) -> Path | None:
    """Read the run's emitted output dir from its marker file, if written."""
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    run_dir = Path(text)
    # Trust it only if it is an actual run dir (has a tasks/ subdir).
    return run_dir if (run_dir / "tasks").is_dir() else None


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

    # --skip-eval is ours; eval-only options the run parser rejects are routed
    # to the eval stage; the rest is forwarded to run verbatim. The run stage
    # writes its resolved output dir to a per-invocation marker file so eval
    # binds to exactly this run even under concurrency.
    forwardable = [a for a in (raw_args or []) if a != "--skip-eval"]
    run_only, eval_only = _partition_eval_only(forwardable)
    marker = Path(tempfile.mkstemp(prefix="run-eval-outdir-")[1])
    run_argv = ["run", *run_only, "--write-output-dir", str(marker)]
    # Snapshot run dirs before the run for the mtime fallback (older run stages
    # that do not honor --write-output-dir).
    pre_mtimes = _run_dir_mtimes(output_base) if output_base else {}
    logger.info("[run-eval] Stage 1/2: run")
    try:
        run_rc = _invoke_cli(run_argv)
        run_dir = _read_marker(marker)
    finally:
        marker.unlink(missing_ok=True)

    if known.dry_run or known.skip_eval:
        logger.info("[run-eval] %s set; stopping after run.", "--dry-run" if known.dry_run else "--skip-eval")
        return run_rc

    # Prefer the exact dir the run emitted (concurrency-safe); model_id and the
    # timestamp come straight from it. Fall back to deriving model_id + the
    # mtime heuristic only when no marker was written.
    if run_dir is not None:
        eval_model_id: str | None = run_dir.parent.name
        run_ts: str | None = run_dir.name
    else:
        if not model_id or output_base is None:
            logger.error("[run-eval] Could not derive model_id for eval (agent=%s); skipping.", canonical_agent)
            return run_rc or 1
        # A completed run with task failures still has output and should be
        # scored; a setup failure that produced nothing is skipped. run_rc is
        # not a gate — non-zero often just means "some tasks failed".
        eval_model_id = model_id
        run_ts = known.timestamp or _run_dir_written_since(output_base, pre_mtimes)
    if not run_ts or not eval_model_id:
        logger.error(
            "[run-eval] Run produced no output directory (exit %d); skipping eval.", run_rc
        )
        return run_rc or 1

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
