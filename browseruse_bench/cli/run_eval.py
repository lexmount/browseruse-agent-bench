"""
run-eval orchestration: run a benchmark then evaluate its results in one call.

The platform invokes this with the same flags it would pass to ``run``; it
forwards them to ``bubench run``, then evaluates the exact run directory the
run stage produced. The eval stage targets that run via ``--model-id`` and
``--timestamp`` so the two stages line up even when the model was passed
through (``--model <id>``) rather than configured, when ``--timestamp`` resumes
an older run, or when some tasks failed (a completed run with task failures is
still scored). Eval is skipped only when the run produced no output directory
(a genuine setup/infra failure) or for ``--dry-run`` / ``--skip-eval``.
"""

from __future__ import annotations

import argparse
import logging
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


def _latest_run_dir(base: Path) -> str | None:
    """Newest timestamped run dir (with a tasks/ subdir) under *base*, by name."""
    if not base.is_dir():
        return None
    runs = [p.name for p in base.iterdir() if p.is_dir() and (p / "tasks").is_dir()]
    # Run dir names are YYYYMMDD_HHMMSS, so lexical max == most recent.
    return max(runs) if runs else None


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
    agent = known.agent or defaults.get("agent", "browser-use")
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
    pre_run = _latest_run_dir(output_base) if output_base else None

    # --skip-eval is ours; strip it before forwarding the rest to run.
    run_argv = ["run", *[a for a in (raw_args or []) if a != "--skip-eval"]]
    logger.info("[run-eval] Stage 1/2: run")
    run_rc = _invoke_cli(run_argv)

    if known.dry_run or known.skip_eval:
        logger.info("[run-eval] %s set; stopping after run.", "--dry-run" if known.dry_run else "--skip-eval")
        return run_rc
    if not model_id or output_base is None:
        logger.error("[run-eval] Could not derive model_id for eval (agent=%s); skipping.", canonical_agent)
        return run_rc or 1

    # Eval the run this invocation produced (or the one --timestamp targeted),
    # not merely the newest dir: a completed run with task failures still has
    # output and should be scored; a setup failure that produced nothing is
    # skipped. run_rc is intentionally not a gate here — non-zero often just
    # means "some tasks failed", which we still want evaluated.
    run_ts = known.timestamp
    if not run_ts:
        post_run = _latest_run_dir(output_base)
        run_ts = post_run if post_run and post_run != pre_run else None
    if not run_ts:
        logger.error(
            "[run-eval] Run produced no output directory (exit %d); skipping eval.", run_rc
        )
        return run_rc or 1

    eval_argv = [
        "eval", "--agent", agent, "--data", data,
        "--model-id", model_id, "--timestamp", run_ts,
    ]
    if known.split is not None:
        eval_argv += ["--split", known.split]
    if known.agent_config is not None:
        eval_argv += ["--agent-config", known.agent_config]
    if known.data_source is not None:
        eval_argv += ["--data-source", known.data_source]
    if known.force_download:
        eval_argv += ["--force-download"]
    logger.info("[run-eval] Stage 2/2: eval (model_id=%s, timestamp=%s)", model_id, run_ts)
    return _invoke_cli(eval_argv)
