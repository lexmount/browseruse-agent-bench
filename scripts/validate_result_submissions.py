from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("validate_result_submissions")

DEFAULT_RESULTS_ROOT = Path("community/results")
DEFAULT_LEADERBOARD_PATH = Path("community/leaderboard/accepted-results.json")

SECRET_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bapi[_-]?key\b",
        r"api[_-]?key",
        r"\baccess[_-]?token\b",
        r"\bsecret\b",
        r"\bcookie\b",
        r"\bsk-[A-Za-z0-9_-]{12,}",
        r"\bgh[pousr]_[A-Za-z0-9_]{12,}",
        r"\bhf_[A-Za-z0-9]{12,}",
    )
]

REQUIRED_SUBMISSION_PATHS = (
    ("benchmark",),
    ("split",),
    ("benchmark_version",),
    ("agent", "name"),
    ("agent", "version"),
    ("agent", "source"),
    ("agent", "reproducibility_limitations"),
    ("model", "provider"),
    ("model", "model_id"),
    ("browser", "backend"),
    ("evaluation", "judge_model"),
    ("evaluation", "strategy"),
    ("commands", "run"),
    ("commands", "eval"),
    ("metrics", "success_rate"),
    ("metrics", "average_steps"),
    ("metrics", "average_end_to_end_seconds"),
    ("metrics", "total_tasks"),
    ("metrics", "accounted_tasks"),
    ("artifacts", "run_directory"),
    ("notes", "skips"),
)

REQUIRED_LEADERBOARD_PATHS = (
    ("schema_version",),
    ("generated_from",),
    ("results",),
)


class ValidationError(ValueError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"{path}: failed to read file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: invalid JSON: {exc}") from exc


def _get_path(data: Any, path: tuple[str, ...]) -> Any:
    current = data
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _require_paths(path: Path, data: dict[str, Any], required_paths: tuple[tuple[str, ...], ...]) -> list[str]:
    errors: list[str] = []
    for required_path in required_paths:
        value = _get_path(data, required_path)
        if value is None:
            errors.append(f"{path}: missing required field {'.'.join(required_path)}")
    return errors


def _walk_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        found: list[str] = []
        for child in value.values():
            found.extend(_walk_values(child))
        return found
    if isinstance(value, list):
        found = []
        for child in value:
            found.extend(_walk_values(child))
        return found
    if isinstance(value, str):
        return [value]
    return []


def _secret_errors(path: Path, data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for text in _walk_values(data):
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                errors.append(f"{path}: possible secret-like value matched {pattern.pattern!r}")
                break
    return errors


def _validate_submission(path: Path) -> list[str]:
    data = _load_json(path)
    if not isinstance(data, dict):
        return [f"{path}: submission must be a JSON object"]

    errors = _require_paths(path, data, REQUIRED_SUBMISSION_PATHS)

    total_tasks = _get_path(data, ("metrics", "total_tasks"))
    accounted_tasks = _get_path(data, ("metrics", "accounted_tasks"))
    if isinstance(total_tasks, int) and isinstance(accounted_tasks, int):
        if total_tasks != accounted_tasks:
            errors.append(
                f"{path}: metrics.total_tasks ({total_tasks}) must equal "
                f"metrics.accounted_tasks ({accounted_tasks})"
            )
    elif total_tasks is not None and accounted_tasks is not None:
        errors.append(f"{path}: metrics.total_tasks and metrics.accounted_tasks must be integers")

    success_rate = _get_path(data, ("metrics", "success_rate"))
    if success_rate is not None and not isinstance(success_rate, (int, float)):
        errors.append(f"{path}: metrics.success_rate must be numeric")
    if isinstance(success_rate, (int, float)) and not 0 <= float(success_rate) <= 1:
        errors.append(f"{path}: metrics.success_rate must be between 0 and 1")

    agent_source = _get_path(data, ("agent", "source"))
    if isinstance(agent_source, str) and agent_source not in {"open-source", "closed-source"}:
        errors.append(f"{path}: agent.source must be 'open-source' or 'closed-source'")

    errors.extend(_secret_errors(path, data))
    return errors


def _validate_leaderboard(path: Path) -> list[str]:
    data = _load_json(path)
    if not isinstance(data, dict):
        return [f"{path}: leaderboard file must be a JSON object"]

    errors = _require_paths(path, data, REQUIRED_LEADERBOARD_PATHS)
    results = data.get("results")
    if results is not None and not isinstance(results, list):
        errors.append(f"{path}: results must be an array")
    return errors


def find_submission_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    example_submission = root / "example" / "submission.json"
    return sorted(path for path in root.rglob("submission.json") if path != example_submission)


def validate(results_root: Path, leaderboard_path: Path, include_examples: bool = True) -> list[str]:
    errors: list[str] = []
    submissions = find_submission_files(results_root)
    if include_examples:
        example = results_root / "example" / "submission.json"
        if example.exists():
            submissions.append(example)

    for submission in sorted(set(submissions)):
        errors.extend(_validate_submission(submission))

    if leaderboard_path.exists():
        errors.extend(_validate_leaderboard(leaderboard_path))
    else:
        errors.append(f"{leaderboard_path}: missing leaderboard file")

    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate community result submission metadata.")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--leaderboard", type=Path, default=DEFAULT_LEADERBOARD_PATH)
    parser.add_argument(
        "--skip-examples",
        action="store_true",
        help="Skip validating community/results/example/submission.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args(argv)
    errors = validate(
        results_root=args.results_root,
        leaderboard_path=args.leaderboard,
        include_examples=not args.skip_examples,
    )
    if errors:
        for error in errors:
            LOGGER.error(error)
        return 1
    LOGGER.info("Result submission metadata is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
