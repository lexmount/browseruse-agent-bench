"""Task processing utility functions."""
from __future__ import annotations

import logging
import os
import random
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from browseruse_bench.utils.browsecomp_core import QUERY_TEMPLATE, decrypt
from browseruse_bench.utils.json_io import load_task_file

logger = logging.getLogger(__name__)
DEFAULT_TASK_URL_ENV_VAR = "BUBENCH_DEFAULT_TASK_URL"
FALLBACK_TASK_URL = "https://www.google.com"


def _has_explicit_scheme(url: str) -> bool:
    """Return whether a URL already has a scheme that should be preserved."""
    parsed = urlparse(url)
    if not parsed.scheme:
        return False

    if "://" in url:
        return True

    return parsed.scheme.lower() in {
        "about",
        "blob",
        "chrome",
        "data",
        "file",
        "javascript",
        "mailto",
        "sms",
        "tel",
    }


def normalize_task_url(url: str) -> str:
    """Normalize task URL by adding protocol prefix when missing.

    Also strips trailing descriptive text (e.g. "www.example.com或其他网站")
    by keeping only the first whitespace- or Chinese-delimited token.
    """
    normalized = url.strip()
    # Strip anything after the first whitespace or Chinese character sequence
    # e.g. "www.xiachufang.com或其他美食网站" -> "www.xiachufang.com"
    match = re.match(r'^([^\s\u4e00-\u9fff]+)', normalized)
    if match:
        normalized = match.group(1).rstrip('.,;，。；')
    if normalized and not _has_explicit_scheme(normalized):
        normalized = f"https://{normalized}"
    return normalized


def resolve_default_task_url(default_url: Optional[str] = None) -> str:
    """Resolve default task URL from argument/env with Google fallback."""
    candidate_url = default_url or os.environ.get(DEFAULT_TASK_URL_ENV_VAR, "")
    if not candidate_url:
        return FALLBACK_TASK_URL

    normalized = normalize_task_url(candidate_url)
    if normalized:
        return normalized

    logger.warning(
        "Invalid default task URL from argument/env var %s: %r. Falling back to %s",
        DEFAULT_TASK_URL_ENV_VAR,
        candidate_url,
        FALLBACK_TASK_URL,
    )
    return FALLBACK_TASK_URL


def is_browsecomp_benchmark(tasks_json_path: Path) -> bool:
    """Check if this is BrowseComp benchmark"""
    return 'BrowseComp' in str(tasks_json_path)


def _load_browsecomp_tasks(tasks_json_path: Path, default_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load BrowseComp tasks and decrypt for agents"""
    # Ensure tasks_json_path is a Path object
    if isinstance(tasks_json_path, str):
        tasks_json_path = Path(tasks_json_path)

    # Load tasks using unified loader
    task_list = load_task_file(tasks_json_path)
    default_task_url = resolve_default_task_url(default_url)

    tasks = []
    for task_data in task_list:
        question = decrypt(task_data["encrypted_question"], task_data["canary"])
        prompt = QUERY_TEMPLATE.format(Question=question)

        tasks.append({
            "task_id": task_data["task_id"],
            "task_text": question,
            "url": default_task_url,
            "prompt": prompt,
            "encrypted_question": task_data["encrypted_question"],
            "encrypted_answer": task_data["encrypted_answer"],
            "canary": task_data["canary"],
        })
    return tasks


def load_tasks_with_benchmark_support(
    tasks_json_path: Path,
    prompt_fmt: Optional[str] = None,
    default_url: Optional[str] = None,
    prompt_fmt_multi: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Load tasks with support for different benchmarks (including BrowseComp)

    Args:
        tasks_json_path: Path to tasks JSON file
        prompt_fmt: Optional prompt template (ignored for BrowseComp which has its own template)
        default_url: Optional default starting URL used when task URL is missing
        prompt_fmt_multi: Optional template for multi-site tasks (``target_website``
            listing several sites). Accepts ``{task}``, ``{url}`` and ``{urls}``.
            Falls back to ``prompt_fmt`` when omitted.

    Returns:
        List of task dictionaries
    """
    if is_browsecomp_benchmark(tasks_json_path):
        logger.info("[INFO] BrowseComp benchmark detected")
        return _load_browsecomp_tasks(tasks_json_path, default_url=default_url)
    else:
        return load_tasks(
            tasks_json_path,
            prompt_fmt=prompt_fmt,
            default_url=default_url,
            prompt_fmt_multi=prompt_fmt_multi,
        )


def load_tasks(
    tasks_json_path: str,
    prompt_fmt: Optional[str] = None,
    default_url: Optional[str] = None,
    prompt_fmt_multi: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load task data from JSON or JSONL file.

    Args:
        tasks_json_path: Path to the tasks JSON or JSONL file.
        prompt_fmt: Optional prompt template, format "{task}\n...{url}...".
                    If provided, a 'prompt' field will be added to the task dictionary.
        default_url: Optional default starting URL used when task URL is missing.
        prompt_fmt_multi: Optional template applied to multi-site tasks
                    (``len(urls) > 1``). Accepts ``{task}``, ``{url}`` (the first
                    URL) and ``{urls}`` (comma-separated list). Falls back to
                    ``prompt_fmt`` when omitted, preserving prior behaviour.

    Returns:
        List[Dict[str, Any]]: List of tasks, each containing task_id, task_text, url.
                              Includes prompt if prompt_fmt is provided.
    """
    tasks: List[Dict[str, Any]] = []
    try:
        p = Path(tasks_json_path)
        if not p.exists():
            logger.error(f"[FAILED] Task file not found: {p}")
            return tasks

        # Uses unified loader which handles .json (dict/list) and .jsonl
        items = load_task_file(p)
        default_task_url = resolve_default_task_url(default_url)

        for it in items:
            # Support multiple task text field names
            task_text = (it.get('confirmed_task') or
                        it.get('task') or
                        it.get('query') or
                        it.get('title') or
                        it.get('ques') or '')

            # Support multiple URL field names
            raw_url = (it.get('website') or
                      it.get('url') or
                      it.get('target_website') or
                      it.get('web') or '')

            # Multi-website tasks (e.g. "movie.douban.com + imdb.com") split on the
            # " + " delimiter — narrower than a bare "+" so URL paths/queries that
            # legitimately contain "+" (e.g. "?q=foo+bar") are not mis-split.
            if ' + ' in raw_url:
                parts = [p.strip() for p in raw_url.split(' + ') if p.strip()]
                urls = [normalize_task_url(p) for p in parts]
                url = urls[0] if urls else default_task_url
            else:
                normalized_url = normalize_task_url(raw_url) if raw_url is not None else ""
                url = normalized_url or default_task_url
                urls = [url]

            # Support multiple ID field names (including numeric IDs)
            task_id = (it.get('task_id') or
                      it.get('annotation_id') or
                      str(it.get('id', f"unknown_{len(tasks)}")))

            if task_text and url:
                # Keep original fields to avoid data loss (fixes "Unknown task" in eval)
                task_dict = it.copy()
                task_dict.update({
                    'task_id': task_id,
                    'task_text': task_text,
                    'url': url,
                    'urls': urls,
                })
                # Multi-site tasks (target_website listing several sites) use the
                # multi-site template so they are not pinned to a single-site
                # "use only" constraint. Falls back to prompt_fmt when no
                # multi-site template was supplied.
                if len(urls) > 1 and prompt_fmt_multi:
                    task_dict['prompt'] = prompt_fmt_multi.format(
                        task=task_text, url=url, urls=", ".join(urls)
                    )
                elif prompt_fmt:
                    task_dict['prompt'] = prompt_fmt.format(task=task_text, url=url)
                tasks.append(task_dict)
    except (OSError, ValueError, TypeError) as exc:
        logger.exception("[FAILED] Failed to load tasks from %s: %s", tasks_json_path, exc)

    return tasks


def filter_tasks(tasks: List[Dict[str, Any]], mode: str, count: int, task_ids: Optional[List[str]], task_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Filter tasks based on mode.

    Args:
        tasks: List of tasks.
        mode: Filter mode ('single', 'first_n', 'sample_n', 'specific', 'by_id', 'all').
        count: Run first N tasks (mode=first_n) or sample N tasks (mode=sample_n).
        task_ids: List of specific task IDs (mode=specific).
        task_id: Specific single task ID (mode=by_id).

    Returns:
        List[Dict[str, Any]]: Filtered list of tasks.

    Raises:
        ValueError: If mode is invalid or parameters mismatch.
    """
    if mode == 'single':
        tasks_to_run = tasks[:1]
        logger.info("[INFO] Test mode: Run only the 1st task")
    elif mode == 'first_n':
        tasks_to_run = tasks[:count]
        logger.info(f"[INFO] Test mode: Run first {count} tasks")
    elif mode == 'sample_n':
        if count >= len(tasks):
            tasks_to_run = tasks
            logger.info(f"[INFO] Test mode: Randomly sample {len(tasks)} tasks (all tasks)")
        else:
            tasks_to_run = random.sample(tasks, count)
            logger.info(f"[INFO] Test mode: Randomly sample {count} tasks from {len(tasks)}")
            logger.info(f"   Sampled Task IDs: {[t['task_id'] for t in tasks_to_run]}")
    elif mode == 'specific':
        if not task_ids:
            raise ValueError("mode='specific' but task_ids not specified")
        tasks_to_run = [t for t in tasks if t['task_id'] in task_ids]
        logger.info(f"[INFO] Test mode: Run specific {len(tasks_to_run)} tasks")
        for tid in task_ids:
            logger.info(f"   - {tid}")
    elif mode == 'by_id':
        if task_id is None:
            raise ValueError("mode='by_id' but id not specified")
        # Try to find task by multiple possible ID fields
        task_id_str = str(task_id)
        tasks_to_run = [t for t in tasks if str(t.get('id') or t.get('task_id', '')) == task_id_str]
        if not tasks_to_run:
            raise ValueError(f"Task with ID {task_id} not found")
        task_text = (tasks_to_run[0].get('task_text') or
                     tasks_to_run[0].get('confirmed_task') or
                     tasks_to_run[0].get('query', ''))
        logger.info(f"task_id={task_id} Running task: {task_text[:80]}...")
    elif mode == 'all':
        tasks_to_run = tasks
        logger.info(f"[INFO] Official Benchmark: Run all {len(tasks)} tasks")
    else:
        raise ValueError(f"Unknown mode: {mode}")

    return tasks_to_run


def filter_tasks_by_region(tasks: List[Dict[str, Any]], region: Optional[str]) -> List[Dict[str, Any]]:
    """Filter tasks by website_region field.

    Args:
        tasks: List of tasks.
        region: Region to filter by ('zh', 'en', or None for no filtering).

    Returns:
        List[Dict[str, Any]]: Filtered task list.
    """
    if not region:
        return tasks
    filtered = [t for t in tasks if t.get('website_region') == region]
    if not filtered:
        logger.warning("No tasks match region '%s' — does this benchmark have 'website_region' field?", region)
    else:
        logger.info(f"Region filter '{region}': {len(filtered)}/{len(tasks)} tasks")
    return filtered


def is_task_completed_by_result_json(task_id: str, output_dir: Path) -> bool:
    """Check if task is completed (via result.json).

    Args:
        task_id: ID of the task.
        output_dir: Output directory path.

    Returns:
        bool: True if result.json exists and is not empty.
    """
    result_file = output_dir / 'tasks' / task_id / 'result.json'
    return result_file.exists() and result_file.stat().st_size > 0


def resolve_tasks_json_path(
    tasks_json_arg: Optional[str],
    default_tasks_json: Path,
    env_var: str = 'TASKS_JSON'
) -> str:
    """Resolve task JSON file path.

    Args:
        tasks_json_arg: Path passed via command line.
        default_tasks_json: Default path.
        env_var: Environment variable name.

    Returns:
        str: Resolved path to tasks JSON file.
    """
    if tasks_json_arg:
        return tasks_json_arg

    env_path = os.environ.get(env_var)
    if env_path and Path(env_path).exists():
        return env_path

    return str(default_tasks_json)


def filter_completed_tasks(
    tasks: List[Dict[str, Any]],
    output_dir: Path,
    check_func: Callable[[str, Path], bool],
) -> tuple[List[Dict[str, Any]], int]:
    """Filter completed tasks.

    Args:
        tasks: List of tasks.
        output_dir: Output directory.
        check_func: Function to check if a task is completed.

    Returns:
        tuple[List[Dict[str, Any]], int]: (List of remaining tasks, number of skipped tasks).
    """

    original_count = len(tasks)
    tasks_to_run = [t for t in tasks if not check_func(t['task_id'], output_dir)]
    skipped = original_count - len(tasks_to_run)

    if skipped > 0:
        logger.info(f"[INFO] Resume: Skipped {skipped} completed tasks, remaining {len(tasks_to_run)}")

    return tasks_to_run, skipped


def print_task_summary(
    total_tasks: int,
    tasks_to_run: int,
    success_count: int,
    failed_count: int,
    output_dir: Path
) -> None:
    """Print task execution summary.

    Args:
        total_tasks: Total number of tasks.
        tasks_to_run: Number of tasks run in this session.
        success_count: Number of successful tasks.
        failed_count: Number of failed tasks.
        output_dir: Output directory path.
    """
    logger.info(f"\n{'='*80}")
    logger.info("[SUCCESS] All tasks completed!")
    logger.info(f"{'='*80}")
    logger.info(f"   Total tasks: {total_tasks}")
    logger.info(f"   Run this time: {tasks_to_run}")
    logger.info(f"   SUCCESS: {success_count}")
    logger.info(f"   FAILED: {failed_count}")
    logger.info(f"\n[INFO] Output directory: {output_dir / 'tasks'}/")
    logger.info(f"{'='*80}\n")
