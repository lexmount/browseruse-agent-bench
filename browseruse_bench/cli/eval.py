from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from browseruse_bench.eval.base import EvaluatorArgs
from browseruse_bench.eval.model import TaskIdLogFilter
from browseruse_bench.eval.registry import get_evaluator_class
from browseruse_bench.utils import (
    REPO_ROOT,
    DataSource,
    add_eval_args,
    classify_failures_batch,
    find_latest_tasks_dir,
    get_env_var,
    handle_cli_errors,
    load_config_file,
    load_data_info,
    load_env_file,
    load_evaluation_model,
    normalize_agent_name,
    normalize_benchmark_name,
    normalized_results_file,
    resolve_agent_inline_config,
    resolve_dir_name_case_insensitive,
    resolve_output_model_id,
    resolve_split,
    setup_logger,
)

CONFIG_PATH = REPO_ROOT / "config.yaml"
load_env_file(REPO_ROOT / ".env")

logger = setup_logger("eval")


def _benchmark_data_dir(benchmark_name: str) -> Path:
    """Resolve dataset root for a benchmark using the conventional layout."""
    return REPO_ROOT / "browseruse_bench" / "data" / benchmark_name


def _experiments_root(benchmark_name: str) -> Path:
    """Resolve experiments output root for a benchmark using the conventional layout."""
    return REPO_ROOT / "experiments" / benchmark_name


def run_failure_classification(
    results_file: Path,
    trajectories_dir: Path,
    model: str,
    api_key: str,
    base_url: str,
    skip_existing: bool = False,
    num_workers: int = 4,
    max_samples: int | None = None,
    temperature: float | None = None,
) -> int:
    """Run failure classification on results file (post-evaluation)."""
    if not results_file.exists():
        logger.warning("Results file not found, skipping failure classification: %s", results_file)
        return 0

    with normalized_results_file(results_file) as prepared_file:
        eval_results: list[dict[str, Any]] = []
        with open(prepared_file, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipped malformed JSON line in %s: %s", prepared_file, exc)
                    continue
                if isinstance(record, dict):
                    eval_results.append(record)

    if not eval_results:
        logger.warning("Evaluation results empty, skipping failure classification: %s", results_file)
        return 0

    model_instance = load_evaluation_model(model, api_key, base_url, temperature=temperature)
    logger.info("Starting failure classification: %s records", len(eval_results))

    updated_results = classify_failures_batch(
        eval_results,
        trajectories_dir,
        model_instance,
        skip_existing=skip_existing,
        max_samples=max_samples,
        num_workers=num_workers,
    )

    with open(results_file, "w", encoding="utf-8") as handle:
        for result in updated_results:
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")

    return 0


def _merge_manifest_into_summary(
    summary_path: Path,
    *,
    eval_mode: str,
    model: str,
    base_url: str,
    score_threshold: int | None,
    results_file: Path | None,
    trajectories_dir: Path,
    exit_code: int,
) -> None:
    """Append eval-run metadata to the summary file."""
    summary: dict[str, Any] = {}
    if summary_path.exists():
        try:
            with open(summary_path, encoding="utf-8") as fh:
                summary = json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass

    evaluated = 0
    passed = 0
    failed = 0
    if results_file and results_file.exists():
        with open(results_file, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                evaluated += 1
                score = rec.get("score") if "score" in rec else rec.get("predicted_label")
                if isinstance(score, int | float) and score >= 1:
                    passed += 1
                else:
                    failed += 1

    summary["eval_run"] = {
        "eval_mode": eval_mode,
        "model": model,
        "base_url": base_url or None,
        "score_threshold": score_threshold,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "exit_code": exit_code,
        "tasks_evaluated": evaluated,
        "tasks_passed": passed,
        "tasks_failed": failed,
        "results_file": results_file.name if results_file else None,
        "log_file": "eval.log",
        "trajectories_dir": str(trajectories_dir),
    }

    try:
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, ensure_ascii=False)
        logger.info("Eval summary (with manifest) written to %s", summary_path)
    except OSError as exc:
        logger.warning("Failed to write summary: %s", exc)


def _attach_file_logger(log_path: Path):
    """Tee evaluator logger output to log_path for the duration of the run."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n--- EVAL STARTED {datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')} ---\n"
        )
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s [task=%(task_id)s]: %(message)s"
    ))
    handler.addFilter(TaskIdLogFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    return handler


def _coerce_extra_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "null"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_extra_args(extra_args: list[str]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    idx = 0
    while idx < len(extra_args):
        token = extra_args[idx]
        if not token.startswith("--"):
            raise SystemExit(f"[FAILED] Unexpected eval extra argument: {token}")
        raw = token[2:]
        if not raw:
            raise SystemExit("[FAILED] Empty eval extra argument")
        if "=" in raw:
            key, value = raw.split("=", 1)
            idx += 1
        elif idx + 1 < len(extra_args) and not extra_args[idx + 1].startswith("--"):
            key = raw
            value = extra_args[idx + 1]
            idx += 2
        else:
            key = raw
            value = "true"
            idx += 1
        key = key.replace("-", "_")
        if not key:
            raise SystemExit("[FAILED] Empty eval extra argument key")
        extra[key] = _coerce_extra_value(value)
    return extra


def _read_task_ids_file(path: Path | None) -> list[str]:
    if path is None:
        return []
    if not path.exists():
        raise SystemExit(f"[FAILED] Task id file does not exist: {path}")
    return path.read_text(encoding="utf-8").split()


def run_evaluation(
    agent_name: str,
    benchmark_name: str,
    config: dict[str, Any],
    args: argparse.Namespace,
    extra_args: list[str],
) -> int:
    # Resolve evaluator class via registry (also validates benchmark name)
    evaluator_cls = get_evaluator_class(benchmark_name)

    # Resolve split (use default if not specified)
    benchmark_path = _benchmark_data_dir(benchmark_name)
    data_info = load_data_info(benchmark_path)
    args.split = resolve_split(args.split, data_info)

    if not args.model_id:
        inline_cfg = resolve_agent_inline_config(agent_name, config)
        fallback = resolve_output_model_id(agent_name, inline_cfg or {})
        if not fallback:
            raise SystemExit(
                "[FAILED] --model-id not provided and could not be derived from config.yaml.\n"
                f"Hint: set agents.{agent_name}.active_model to a model whose entry has a "
                "`model_id` field, or pass --model-id explicitly."
            )
        args.model_id = str(fallback)
        logger.info("Using model_id from active_model: %s", args.model_id)

    # Path structure: experiments/{benchmark}/{split}/{agent}/{model_id}
    output_base = _experiments_root(benchmark_name) / args.split
    args.model_id = resolve_dir_name_case_insensitive(args.model_id, output_base / agent_name)
    agent_output_dir = output_base / agent_name / args.model_id

    if args.timestamp:
        trajectories_dir = agent_output_dir / args.timestamp / "tasks"
        if not trajectories_dir.exists():
            raise SystemExit(f"[FAILED] Specified timestamp directory does not exist: {trajectories_dir}")
    else:
        trajectories_dir = find_latest_tasks_dir(agent_output_dir)
    eval_output_dir = trajectories_dir.parent / "tasks_eval_result"
    eval_output_dir.mkdir(parents=True, exist_ok=True)

    eval_log_path = eval_output_dir / "eval.log"

    eval_mode = args.mode or evaluator_cls.default_mode
    eval_cfg = config.get("eval", {})
    api_key = args.api_key or eval_cfg.get("api_key") or get_env_var(
        "OPENAI_API_KEY",
        required=True,
        error_message="OPENAI_API_KEY is required. Set it in config.yaml eval.api_key or OPENAI_API_KEY env var.",
    )
    base_url = args.base_url or eval_cfg.get("base_url") or ""
    model_name = args.model or eval_cfg.get("model") or "gpt-4.1"
    temperature = eval_cfg.get("temperature")
    max_tokens = eval_cfg.get("max_tokens")

    # Threshold handling: evaluators that declare uses_per_task_threshold read
    # the per-task value from the dataset and ignore the global CLI flag.
    if evaluator_cls.uses_per_task_threshold:
        if args.score_threshold is not None:
            logger.warning(
                "Ignoring --score-threshold for %s; per-task score_threshold will be used.",
                benchmark_name,
            )
        score_threshold: int | None = None
    else:
        score_threshold = args.score_threshold if args.score_threshold is not None else 3

    # Pack benchmark-private extras unconditionally — evaluators that don't read
    # a given key simply ignore it.
    extra: dict[str, Any] = {
        "eval_strategy": getattr(args, "eval_strategy", None) or "stepwise",
        "force_download": bool(getattr(args, "force_download", False)),
    }
    extra.update(_parse_extra_args(extra_args))
    task_ids = [str(task_id) for task_id in (getattr(args, "task_ids", None) or [])]
    task_ids.extend(_read_task_ids_file(getattr(args, "task_ids_file", None)))
    exclude_task_ids = [str(task_id) for task_id in (getattr(args, "exclude_task_ids", None) or [])]
    exclude_task_ids.extend(_read_task_ids_file(getattr(args, "exclude_task_ids_file", None)))
    if task_ids:
        extra["task_ids"] = task_ids
    if exclude_task_ids:
        extra["exclude_task_ids"] = exclude_task_ids
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens

    if args.dry_run:
        logger.info(
            "[DRY RUN] Would evaluate %s on %s (split=%s, mode=%s, model=%s)",
            agent_name, benchmark_name, args.split, eval_mode, model_name,
        )
        logger.info("[DRY RUN] Trajectories: %s", trajectories_dir)
        logger.info("[DRY RUN] Output:       %s", eval_output_dir)
        return 0

    evaluator_args = EvaluatorArgs(
        benchmark=benchmark_name,
        model=model_name,
        api_key=api_key,
        base_url=base_url or None,
        trajectories_dir=trajectories_dir,
        output_path=eval_output_dir,
        score_threshold=score_threshold,
        num_worker=args.num_worker,
        temperature=temperature,
        split=args.split,
        data_source=getattr(args, "data_source", DataSource.LOCAL),
        mode=eval_mode,
        extra=extra,
    )

    # Build an evaluation model. Some evaluators (LexBench) load their own model
    # internally and don't strictly need it; we still pass a default for uniformity.
    model_instance = load_evaluation_model(model_name, api_key, base_url, temperature=temperature)

    evaluator = evaluator_cls(evaluator_args, model_instance)

    handler = _attach_file_logger(eval_log_path)
    try:
        logger.info("Evaluating %s on %s (in-process)", agent_name, benchmark_name)
        logger.info("   Output: %s", eval_output_dir)
        try:
            exit_code = evaluator.run()
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            logger.exception("Evaluator failed: %s", exc)
            exit_code = 1
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()

    results_file: Path | None = evaluator.results_path()
    if not results_file.exists():
        results_file = None

    if exit_code != 0:
        _merge_manifest_into_summary(
            evaluator.summary_path(),
            eval_mode=eval_mode,
            model=model_name,
            base_url=base_url,
            score_threshold=score_threshold,
            results_file=None,
            trajectories_dir=trajectories_dir,
            exit_code=exit_code,
        )
        return exit_code

    # Failure classification post-step
    classification_exit = run_failure_classification(
        results_file,
        trajectories_dir,
        model_name,
        api_key,
        base_url,
        temperature=temperature,
    ) if results_file else 0

    _merge_manifest_into_summary(
        evaluator.summary_path(),
        eval_mode=eval_mode,
        model=model_name,
        base_url=base_url,
        score_threshold=score_threshold,
        results_file=results_file,
        trajectories_dir=trajectories_dir,
        exit_code=classification_exit,
    )

    return classification_exit


def configure_eval_parser(parser: argparse.ArgumentParser, config: dict[str, Any]) -> None:
    """Configure arguments for the eval command."""
    add_eval_args(parser)
    parser.add_argument("--data", default=config.get("default", {}).get("data") or config.get("default", {}).get("benchmark", "Online-Mind2Web"))
    parser.add_argument("--agent", default=config.get("default", {}).get("agent", "Agent-TARS"))
    parser.add_argument(
        "--model-id",
        default=None,
        help=(
            "Model ID used during run (determines output subdirectory under agent name). "
            "Defaults to the model_id of agents.<agent>.active_model in config.yaml."
        ),
    )
    parser.add_argument(
        "--split",
        default=None,
        help="Dataset split (defaults to data_info.json's default_split, falling back to 'All'). Options depend on benchmark.",
    )
    parser.add_argument(
        "--data-source",
        default=DataSource.LOCAL,
        choices=DataSource.tolist(),
        help="Data source: local (default) or huggingface (download to HF cache)",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Force re-download from HuggingFace cache (only applies to huggingface mode)",
    )
    parser.add_argument(
        "--force-reeval",
        action="store_true",
        help="Rerun evaluation (default reuses existing results, only runs failure classification)",
    )
    parser.add_argument("--task-ids", nargs="*", default=[], help="Only evaluate these task IDs.")
    parser.add_argument(
        "--task-ids-file",
        type=Path,
        default=None,
        help="Whitespace-separated task IDs to evaluate.",
    )
    parser.add_argument("--exclude-task-ids", nargs="*", default=[], help="Do not evaluate these task IDs.")
    parser.add_argument(
        "--exclude-task-ids-file",
        type=Path,
        default=None,
        help="Whitespace-separated task IDs to skip during evaluation.",
    )
    parser.add_argument(
        "--agent-config",
        type=Path,
        default=None,
        help=(
            "Optional path to an alternate root-config YAML (same shape as the repo "
            "config.yaml). Its `eval` section overrides the defaults from root config.yaml."
        ),
    )
    parser.set_defaults(
        model="",
        api_key=get_env_var("OPENAI_API_KEY", ""),
        base_url="",
    )


def eval_command(args: argparse.Namespace, config: dict[str, Any]) -> int:
    """Entry point for the eval subcommand."""
    extra_args = getattr(args, "extra_args", [])
    agent_name = normalize_agent_name(args.agent, config)
    benchmark_name = normalize_benchmark_name(args.data)
    return run_evaluation(agent_name, benchmark_name, config, args, extra_args)


@handle_cli_errors
def main(argv: list[str] | None = None) -> int:
    config = load_config_file(CONFIG_PATH)
    parser = argparse.ArgumentParser(prog="bubench eval")
    configure_eval_parser(parser, config)
    args, extra = parser.parse_known_args(argv)
    if extra:
        logger.info("Forwarding extra arguments: %s", " ".join(extra))
    args.extra_args = extra
    if args.agent_config is not None:
        cfg_path = args.agent_config
        if not cfg_path.is_absolute():
            cfg_path = Path.cwd() / cfg_path
        if not cfg_path.exists():
            raise SystemExit(f"[FAILED] --agent-config file not found: {cfg_path}")
        external_cfg = load_config_file(cfg_path)
        external_eval = external_cfg.get("eval", {})
        if external_eval:
            config = {**config, "eval": {**config.get("eval", {}), **external_eval}}
    return eval_command(args, config)


if __name__ == "__main__":
    main()
