from __future__ import annotations

import argparse
import sys
import textwrap
from typing import Any, Dict, List, Optional

from browseruse_bench.cli.attribute import attribute_command, configure_attribute_parser
from browseruse_bench.cli.eval import configure_eval_parser, eval_command
from browseruse_bench.cli.leaderboard import configure_leaderboard_parser, leaderboard_command
from browseruse_bench.cli.login import configure_login_parser, login_command
from browseruse_bench.cli.run import configure_run_parser, run_command
from browseruse_bench.cli.server import configure_server_parser, server_command
from browseruse_bench.cli.service import configure_service_parser, service_command
from browseruse_bench.cli.skills import configure_skills_parser, skills_command
from browseruse_bench.cli.viz import configure_viz_parser, viz_command
from browseruse_bench.utils import (
    REPO_ROOT,
    handle_cli_errors,
    load_config_file,
    load_env_file,
    setup_logger,
)

CONFIG_PATH = REPO_ROOT / "config.yaml"

# Preload .env from root directory for unified configuration reading
load_env_file(REPO_ROOT / ".env")

# Setup logger
logger = setup_logger("bubench")


def _submit_dependency_missing(args: argparse.Namespace, config: Dict[str, Any]) -> int:
    raise SystemExit(
        "[FAILED] The 'submit' command requires the optional lexbench_sdk dependency. "
        "Install the submit extras or run a non-submit command."
    )


def _build_parser(config: Dict[str, Any]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bubench",
        description="BrowserUse Bench CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              bubench submit --agent browser-use --data LexBench-Browser --mode first_n --count 1
              bubench leaderboard
              bubench server --host 0.0.0.0 --port 8000
              bubench viz --watch --port 8080
              sudo bubench service install
              sudo bubench service start
              sudo bubench service status
              sudo bubench service logs
            """
        ).strip(),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a benchmark with an agent")
    configure_run_parser(run_parser, config)
    run_parser.set_defaults(handler=run_command)

    submit_parser = subparsers.add_parser("submit", help="Submit a job to LexBench")
    try:
        from browseruse_bench.cli.submit import configure_submit_parser, submit_command
    except ModuleNotFoundError as exc:
        if exc.name != "lexbench_sdk":
            raise
        submit_parser.set_defaults(handler=_submit_dependency_missing)
    else:
        configure_submit_parser(submit_parser, config)
        submit_parser.set_defaults(handler=submit_command)

    eval_parser = subparsers.add_parser("eval", help="Evaluate benchmark results")
    configure_eval_parser(eval_parser, config)
    eval_parser.set_defaults(handler=eval_command)

    attribute_parser = subparsers.add_parser(
        "attribute", help="Label failure causes on existing eval results"
    )
    configure_attribute_parser(attribute_parser, config)
    attribute_parser.set_defaults(handler=attribute_command)

    leaderboard_parser = subparsers.add_parser("leaderboard", help="Generate leaderboard HTML")
    configure_leaderboard_parser(leaderboard_parser, config)
    leaderboard_parser.set_defaults(handler=leaderboard_command)

    server_parser = subparsers.add_parser("server", help="Start leaderboard web server")
    configure_server_parser(server_parser, config)
    server_parser.set_defaults(handler=server_command)

    service_parser = subparsers.add_parser("service", help="Manage systemd service (Linux)")
    configure_service_parser(service_parser, config)
    service_parser.set_defaults(handler=service_command)

    skills_parser = subparsers.add_parser("skills", help="Install shared skills into agent folders")
    configure_skills_parser(skills_parser, config)
    skills_parser.set_defaults(handler=skills_command)

    viz_parser = subparsers.add_parser("viz", help="Start visualization server for experiment explorer")
    configure_viz_parser(viz_parser, config)
    viz_parser.set_defaults(handler=viz_command)

    login_parser = subparsers.add_parser(
        "login", help="Manage Lexmount login contexts (required for login-gated evals)"
    )
    configure_login_parser(login_parser, config)
    login_parser.set_defaults(handler=login_command)

    return parser


@handle_cli_errors
def main(argv: Optional[List[str]] = None) -> int:
    cli_args = list(argv) if argv is not None else sys.argv[1:]
    # run-eval chains `run` then `eval`; handled before argparse so the
    # combined flag set is forwarded to each stage without re-declaring them.
    if cli_args and cli_args[0] == "run-eval":
        from browseruse_bench.cli.run_eval import run_and_eval

        return run_and_eval(cli_args[1:])

    config = load_config_file(CONFIG_PATH)
    parser = _build_parser(config)
    args, extra = parser.parse_known_args(argv)
    if extra:
        if args.command in {"eval", "leaderboard", "server", "service", "skills"}:
            logger.info("Forwarding extra arguments: %s", " ".join(extra))
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    setattr(args, "extra_args", extra)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
