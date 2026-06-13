"""
run-eval orchestration: run a benchmark then evaluate its results in one call.

The platform invokes this with the same flags it would pass to ``run``; it
forwards them to ``bubench run``, then derives the model_id the run wrote its
output under and calls ``bubench eval`` with an explicit ``--model-id`` so the
two stages line up even when the model was passed through (``--model <id>``)
rather than configured. The eval stage is skipped when the run hard-fails.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from browseruse_bench.cli import CONFIG_PATH
from browseruse_bench.utils import (
    load_config_file,
    normalize_agent_name,
    resolve_agent_inline_config,
    resolve_output_model_id,
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
    parser.add_argument("--skip-eval", action="store_true")
    return parser


def _resolve_run_model_id(
    agent: str,
    root_config: dict,
    model_name: str | None,
    browser_id: str | None,
    agent_config: str | None,
) -> str | None:
    """Compute the model_id the run wrote its output under (mirrors run_command)."""
    source_cfg = root_config
    if agent_config:
        cfg_path = Path(agent_config)
        if not cfg_path.is_absolute():
            cfg_path = Path.cwd() / cfg_path
        if cfg_path.exists():
            source_cfg = load_config_file(cfg_path)
    inline = resolve_agent_inline_config(agent, source_cfg, model_name, browser_id)
    return resolve_output_model_id(agent, inline or {})


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
    """Run a benchmark then evaluate it; return the eval exit code (or run's on failure)."""
    raw_args = list(argv) if argv is not None else None
    known, _ = _shared_parser().parse_known_args(raw_args)

    root_config = load_config_file(CONFIG_PATH)
    defaults = root_config.get("default", {})
    agent = known.agent or defaults.get("agent", "browser-use")
    data = known.data or defaults.get("data") or defaults.get("benchmark", "Online-Mind2Web")

    run_argv = ["run", *(raw_args if raw_args is not None else [])]
    # --skip-eval is ours, not a run flag; strip it before forwarding.
    run_argv = [arg for arg in run_argv if arg != "--skip-eval"]
    logger.info("[run-eval] Stage 1/2: run")
    run_rc = _invoke_cli(run_argv)
    if run_rc != 0:
        logger.error("[run-eval] Run failed (exit %d); skipping eval.", run_rc)
        return run_rc
    if known.skip_eval:
        logger.info("[run-eval] --skip-eval set; stopping after run.")
        return 0

    canonical_agent = normalize_agent_name(agent, root_config)
    model_id = _resolve_run_model_id(
        canonical_agent, root_config, known.model_name, known.browser_id, known.agent_config
    )
    if not model_id:
        logger.error(
            "[run-eval] Could not derive model_id for eval (agent=%s). "
            "Set a model_id in the agent's model config or pass --model.",
            canonical_agent,
        )
        return 1

    eval_argv = ["eval", "--agent", agent, "--data", data, "--model-id", model_id]
    if known.split is not None:
        eval_argv += ["--split", known.split]
    if known.agent_config is not None:
        eval_argv += ["--agent-config", known.agent_config]
    logger.info("[run-eval] Stage 2/2: eval (model_id=%s)", model_id)
    return _invoke_cli(eval_argv)
