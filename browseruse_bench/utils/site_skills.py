"""Site-skills prompt injection.

Matches a task's declared target site(s) against a library of per-site skill
directories (the browser-harness ``agent-workspace/domain-skills`` layout) and
appends the matched skill files to the task prompt. The matching rules mirror
``browser_harness.helpers._domain_skills`` so hit rates stay comparable: a
directory matches when its name equals the hostname, any dotted suffix, or any
single label (compared ignoring ``.-_``), or when its ``hosts`` file lists the
hostname or a suffix.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_MAX_FILES = 10
DEFAULT_MAX_CHARS = 30000

_SECTION_HEADER = (
    "## Site knowledge (pre-collected)\n\n"
    "The notes below were field-tested on the target site earlier. Prefer the "
    "URL patterns, fallbacks, and anti-bot workarounds they describe over "
    "trial and error, but verify against the live page — details may have "
    "changed. Code snippets reference browser-harness helpers (js(), "
    "http_get(), ...); adapt them to your own tools.\n"
)
_TRUNCATION_NOTICE = "\n[site knowledge truncated: max_chars budget reached]\n"
# Below this budget the section is all header/notice boilerplate with no
# actual knowledge; _resolve_site_skills fails fast on smaller values so a
# treated experiment arm can never silently deliver no treatment.
MIN_MAX_CHARS = len(_SECTION_HEADER) + len(_TRUNCATION_NOTICE) + 500

# Some dataset rows carry noisy site values ("www.xiaohongshu.com或其他");
# keep the hostname-looking prefix, same tolerance as login-context routing.
_HOST_PREFIX_RE = re.compile(r"[A-Za-z0-9._\-:/]+")


def _hostname(url_or_host: str | None) -> str:
    """Lowercased hostname without ``www.``; accepts bare and noisy domains."""
    value = (url_or_host or "").strip()
    match = _HOST_PREFIX_RE.match(value)
    if not match:
        return ""
    value = match.group(0)
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return (parsed.hostname or "").removeprefix("www.").lower()


def _normalize(label: str) -> str:
    return label.lower().replace(".", "").replace("-", "").replace("_", "")


def _host_candidates(url_or_host: str | None) -> Set[str]:
    host = _hostname(url_or_host)
    if not host:
        return set()
    parts = host.split(".")
    return {host} | set(parts) | {".".join(parts[i:]) for i in range(1, len(parts))}


def _dir_matches(skill_dir: Path, cands: Set[str], ncands: Set[str]) -> bool:
    if _normalize(skill_dir.name) in ncands:
        return True
    hosts_file = skill_dir / "hosts"
    if not hosts_file.is_file():
        return False
    try:
        aliases = hosts_file.read_text(encoding="utf-8").split()
    except OSError as exc:
        logger.error("[SITE-SKILLS] Unreadable hosts file %s: %s", hosts_file, exc)
        return False
    return any(a.lower().removeprefix("www.") in cands for a in aliases)


def match_skill_files(
    url_or_host: str | None,
    skills_dir: Path,
    max_files: int = DEFAULT_MAX_FILES,
) -> List[Path]:
    """Skill markdown files matching the host of *url_or_host* (sorted, capped)."""
    cands = _host_candidates(url_or_host)
    if not cands or not skills_dir.is_dir():
        return []
    ncands = {_normalize(c) for c in cands}
    out: Set[Path] = set()
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_dir() and _dir_matches(entry, cands, ncands):
            out.update(entry.rglob("*.md"))
    return sorted(out)[:max_files]


def task_target_urls(task_info: Dict[str, Any]) -> List[str]:
    """The task's declared target URLs (multi-site list, or the single target)."""
    urls = task_info.get("urls")
    if isinstance(urls, list):
        declared = [u for u in urls if isinstance(u, str) and u.strip()]
        if declared:
            return declared
    single = (
        task_info.get("target_website")
        or task_info.get("task_start_url")
        or task_info.get("url")
    )
    return [single] if isinstance(single, str) and single.strip() else []


def build_skills_section(
    files: List[Path], skills_dir: Path, max_chars: int
) -> Tuple[str, List[Path], bool]:
    """Markdown section with the files' content, truncated at *max_chars*.

    Returns ``(section, included_files, truncated)`` — *included_files* lists
    only the files whose content (fully or partially) made it into the
    section, so callers never report skills the agent could not see.
    """
    parts = [_SECTION_HEADER]
    used = len(_SECTION_HEADER)
    included: List[Path] = []
    truncated = False
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            logger.error("[SITE-SKILLS] Unreadable skill file %s: %s", path, exc)
            continue
        block = f"\n### {path.relative_to(skills_dir)}\n\n{text.strip()}\n"
        budget = max_chars - used - len(_TRUNCATION_NOTICE)
        if len(block) > budget:
            truncated = True
            if budget > 0:
                parts.append(block[:budget] + _TRUNCATION_NOTICE)
                included.append(path)
            break
        parts.append(block)
        used += len(block)
        included.append(path)
    return "".join(parts), included, truncated


def apply_site_skills(
    tasks: List[Dict[str, Any]],
    skills_dir: Path,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_files: int = DEFAULT_MAX_FILES,
) -> Dict[str, Dict[str, Any]]:
    """Append matched skill sections to each task's prompt, in place.

    Returns a per-task summary ``{task_id: {"files": [...], "chars": int,
    "truncated": bool}}`` for the run manifest and for caller-side logging
    (module loggers duplicate in the CLI parent process, so per-task lines are
    logged by the caller). ``files`` lists only content actually injected; a
    miss means the skill library has a coverage gap.
    """
    summary: Dict[str, Dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task.get("task_id", "?"))
        matched: Set[Path] = set()
        for url in task_target_urls(task):
            matched.update(match_skill_files(url, skills_dir, max_files))
        files = sorted(matched)[:max_files]
        if not files:
            summary[task_id] = {"files": [], "chars": 0, "truncated": False}
            continue
        section, included, truncated = build_skills_section(files, skills_dir, max_chars)
        info = {
            "files": [str(f.relative_to(skills_dir)) for f in included],
            "chars": len(section),
            "truncated": truncated,
        }
        # Keep the pre-injection prompt so result.json can record the clean
        # task separately from the injected knowledge.
        task["prompt_base"] = task.get("prompt", "")
        task["prompt"] = f"{task.get('prompt', '')}\n\n{section}".strip()
        task["site_skills"] = info
        summary[task_id] = info
    return summary
