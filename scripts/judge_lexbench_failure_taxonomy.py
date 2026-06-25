#!/usr/bin/env python3
"""Classify LexBench-Browser failed trajectories with a compact taxonomy."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from browseruse_bench.eval.model import encode_image, load_evaluation_model

try:
    from PIL import Image
except ImportError:  # pragma: no cover - validated at runtime when screenshots are used
    Image = None

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional progress display
    tqdm = None


LOGGER = logging.getLogger("lexbench-failure-taxonomy")

PROMPT_PATH = (
    REPO_ROOT
    / "browseruse_bench"
    / "eval"
    / "lexbench_browser"
    / "prompts"
    / "failure_taxonomy_system.txt"
)

TAXONOMY: dict[str, tuple[str, str]] = {
    "M1.1": ("Task Reasoning", "Requirement Following"),
    "M1.2": ("Task Reasoning", "Target Selection"),
    "M1.3": ("Task Reasoning", "Evidence Grounding"),
    "M2.1": ("Action Execution", "UI Misoperation"),
    "M2.2": ("Action Execution", "Infinite Loop"),
    "M2.3": ("Action Execution", "Format Breakdown"),
    "M3.1": ("Web Constraints", "Bot Defense"),
    "M3.2": ("Web Constraints", "Access Barrier"),
    "M3.3": ("Web Constraints", "Site Limitation"),
    "OTHER": ("Other", "Other"),
}

ALLOWED_CODES = set(TAXONOMY)

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "lexbench_failure_taxonomy",
        "schema": {
            "type": "object",
            "properties": {
                "primary_code": {"type": "string", "enum": sorted(ALLOWED_CODES)},
                "codes": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(ALLOWED_CODES)},
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "other_phrase": {"type": ["string", "null"]},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reasoning": {"type": "string"},
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                },
            },
            "required": [
                "primary_code",
                "codes",
                "other_phrase",
                "confidence",
                "reasoning",
                "evidence",
            ],
            "additionalProperties": False,
        },
    },
}


class SimpleChatModel:
    """Small OpenAI-compatible chat client used when the OpenAI SDK is unavailable."""

    def __init__(
        self,
        model: str,
        api_key: str | None,
        base_url: str | None,
        insecure: bool = False,
        claude_thinking: bool = False,
        reasoning_effort: str = "medium",
    ):
        self.model = model
        self.api_key = api_key or os.getenv("EVAL_MODEL_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.ssl_context = ssl._create_unverified_context() if insecure else None
        self.claude_thinking = claude_thinking
        self.reasoning_effort = reasoning_effort
        self.last_response: dict[str, Any] | None = None
        self.last_usage: dict[str, Any] | None = None
        if not self.api_key:
            raise ValueError("API key required: set EVAL_MODEL_API_KEY or OPENAI_API_KEY")

    def _chat_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def generate(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 2048,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0 if temperature is None else temperature,
        }
        for key in ("response_format",):
            if key in kwargs and kwargs[key] is not None:
                payload[key] = kwargs[key]
        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        if self.claude_thinking:
            payload.setdefault("reasoning_effort", self.reasoning_effort)
            allowed = list(payload.get("allowed_openai_params") or [])
            if "reasoning_effort" not in allowed:
                allowed.append("reasoning_effort")
            payload["allowed_openai_params"] = allowed

        request = urllib.request.Request(
            self._chat_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        body = ""
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(request, timeout=300, context=self.ssl_context) as response:
                    body = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if (exc.code >= 500 or exc.code in {401, 429}) and attempt < 3:
                    LOGGER.warning("LLM HTTP %s on attempt %s/3; retrying", exc.code, attempt)
                    time.sleep(4 * attempt)
                    continue
                raise RuntimeError(f"LLM HTTP error {exc.code}: {detail}") from exc
            except (socket.timeout, TimeoutError, urllib.error.URLError) as exc:
                if attempt < 3:
                    LOGGER.warning("LLM connection timeout/error on attempt %s/3; retrying: %s", attempt, exc)
                    time.sleep(4 * attempt)
                    continue
                raise RuntimeError(f"LLM connection error: {exc}") from exc

        parsed = json.loads(body)
        self.last_response = parsed
        self.last_usage = parsed.get("usage") if isinstance(parsed.get("usage"), dict) else None
        return parsed["choices"][0]["message"].get("content") or ""


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    head = max(1, limit // 2)
    tail = max(1, limit - head)
    return text[:head] + f"\n...[truncated {len(text) - limit} chars]...\n" + text[-tail:]


def _one_line(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                LOGGER.warning("Skipping malformed line %s in %s: %s", line_no, path, exc)
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _sort_screenshot_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)", path.name)
    if match:
        return int(match.group(1)), path.name
    return 0, path.name


def _find_screenshots(task_dir: Path, max_screenshots: int) -> list[Path]:
    trajectory_dir = task_dir / "trajectory"
    if max_screenshots <= 0 or not trajectory_dir.exists():
        return []
    screenshots = [
        path
        for path in trajectory_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]
    screenshots.sort(key=_sort_screenshot_key)
    return screenshots[-max_screenshots:]


def _compact_api_summary(path: Path, limit: int) -> str:
    if limit <= 0 or not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    keep: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("## Task Info", "- **Task ID**", "- **Model**", "- **Total Steps**")):
            keep.append(stripped)
            continue
        if stripped.startswith("## Step "):
            keep.append(stripped)
            continue
        if stripped.startswith(("**URL**", "**Memory**", "**Actions**", "### Action Results")):
            keep.append(stripped)
            continue
        if re.match(r"^\d+\.\s+`", stripped):
            keep.append(stripped)
            continue
        if stripped.startswith("- ") and re.search(
            r"(Error|Clicked|Typed|Navigated|Waited|Searched|Switched|Found|Downloaded|Wrote|Read|Saved|Scrolled|Pressed)",
            stripped,
            re.I,
        ):
            keep.append(stripped)

    compact = "\n".join(keep)
    if not compact:
        compact = text
    return _compact_text(compact, limit)


def _extract_eval_details(record: dict[str, Any]) -> dict[str, Any]:
    details = record.get("evaluation_details")
    return details if isinstance(details, dict) else {}


def _extract_prompt_params(record: dict[str, Any]) -> dict[str, Any]:
    details = _extract_eval_details(record)
    user_prompt = details.get("user_prompt")
    if not isinstance(user_prompt, dict):
        return {}
    params = user_prompt.get("params")
    return params if isinstance(params, dict) else {}


def _result_dir(record: dict[str, Any], eval_file: Path) -> Path:
    ref = record.get("agent_result_ref")
    if isinstance(ref, dict) and ref.get("result_dir"):
        return Path(str(ref["result_dir"]))
    return eval_file.parent.parent / "tasks" / str(record.get("task_id", ""))


def _build_user_text(
    record: dict[str, Any],
    eval_file: Path,
    *,
    trace_char_budget: int,
    feedback_char_budget: int,
) -> str:
    details = _extract_eval_details(record)
    params = _extract_prompt_params(record)
    task_dir = _result_dir(record, eval_file)
    result = _load_json(task_dir / "result.json")
    api_trace = _compact_api_summary(task_dir / "api_logs" / "summary.md", trace_char_budget)

    old_classification = details.get("failure_classification") or record.get("failure_classification")
    old_reasoning = ""
    if isinstance(old_classification, dict):
        old_reasoning = old_classification.get("reasoning", "") or ""

    action_history = result.get("action_history") or []
    if isinstance(action_history, list):
        action_history_text = "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(action_history[-25:]))
    else:
        action_history_text = str(action_history)

    text = f"""Classify this failed LexBench-Browser trajectory.

## Task
Task ID: {record.get("task_id")}
Model ID: {record.get("model_id") or result.get("model_id")}
Task type: {record.get("task_type") or params.get("task_type")}
Target website: {params.get("target_website", "")}
Task description:
{record.get("task") or params.get("task_description", "")}

## Reference and rubric
Correct steps:
{_compact_text(params.get("reference_steps", ""), 2500)}

Key points:
{_compact_text(params.get("key_points", ""), 1800)}

Common mistakes:
{_compact_text(params.get("common_mistakes", ""), 1800)}

Scoring items:
{_compact_text(params.get("scoring_items", ""), 2500)}

## Judge result
Predicted label: {record.get("predicted_label")} (0 means failed)
Score: {details.get("score")}
Score threshold: {(details.get("benchmark_details") or {}).get("score_threshold")}
Evaluator feedback:
{_compact_text(details.get("response", ""), feedback_char_budget)}

## Agent final answer
{_compact_text(params.get("agent_answer") or result.get("answer") or record.get("agent_response"), 4000)}

## Runtime result
env_status: {result.get("env_status")}
agent_done: {result.get("agent_done")}
agent_success: {result.get("agent_success")}
error: {result.get("error")}
steps: {(result.get("metrics") or {}).get("steps")}
wall_clock_seconds: {result.get("wall_clock_seconds")}
old_failure_category: {record.get("failure_category")}
old_failure_reasoning:
{_compact_text(old_reasoning, 1500)}

## Result action history
{_compact_text(action_history_text, 5000)}

## Compact API trace
{api_trace}
"""
    return text


def _image_content(path: Path, scale_factor: float) -> dict[str, Any] | None:
    if Image is None:
        raise ImportError("Pillow is required when --max-screenshots is greater than 0")
    try:
        image = Image.open(path)
        encoded = encode_image(image, scale_factor=scale_factor)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        LOGGER.warning("Failed to encode screenshot %s: %s", path, exc)
        return None
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"},
    }


def _build_messages(
    record: dict[str, Any],
    eval_file: Path,
    system_prompt: str,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    user_text = _build_user_text(
        record,
        eval_file,
        trace_char_budget=args.trace_char_budget,
        feedback_char_budget=args.feedback_char_budget,
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    task_dir = _result_dir(record, eval_file)
    for screenshot in _find_screenshots(task_dir, args.max_screenshots):
        image_part = _image_content(screenshot, args.image_scale_factor)
        if image_part:
            content.append(image_part)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


def _parse_response(raw: str) -> dict[str, Any]:
    if not raw.strip():
        raise ValueError("empty model response")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(match.group(0)) if match else {}

    if not isinstance(parsed, dict):
        parsed = {}
    if not parsed:
        raise ValueError("model response did not contain a JSON object")

    raw_codes = parsed.get("codes")
    if isinstance(raw_codes, str):
        codes = [raw_codes]
    elif isinstance(raw_codes, list):
        codes = [str(code) for code in raw_codes]
    else:
        codes = []

    codes = [code for code in codes if code in ALLOWED_CODES]
    primary_code = str(parsed.get("primary_code") or "").strip()
    if primary_code not in ALLOWED_CODES:
        primary_code = codes[0] if codes else "OTHER"
    if primary_code not in codes:
        codes.insert(0, primary_code)
    if not codes:
        codes = ["OTHER"]
        primary_code = "OTHER"

    confidence = str(parsed.get("confidence") or "medium").lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    other_phrase = parsed.get("other_phrase")
    if other_phrase is not None:
        other_phrase = _one_line(other_phrase, 80)
    if "OTHER" not in codes:
        other_phrase = None
    elif not other_phrase:
        other_phrase = "uncategorized"

    evidence = parsed.get("evidence")
    if isinstance(evidence, str):
        evidence_items = [evidence]
    elif isinstance(evidence, list):
        evidence_items = [_one_line(item, 300) for item in evidence if str(item).strip()]
    else:
        evidence_items = []

    return {
        "primary_code": primary_code,
        "codes": codes,
        "other_phrase": other_phrase,
        "confidence": confidence,
        "reasoning": str(parsed.get("reasoning") or "").strip(),
        "evidence": evidence_items,
        "raw_response": raw,
    }


def _claude_extra_body(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.claude_thinking:
        return None
    return {
        "reasoning_effort": args.claude_reasoning_effort,
        "allowed_openai_params": ["reasoning_effort"],
    }


def _taxonomy_payload(parsed: dict[str, Any]) -> dict[str, Any]:
    labels = [
        {
            "code": code,
            "group": TAXONOMY[code][0],
            "label": TAXONOMY[code][1],
        }
        for code in parsed["codes"]
    ]
    return {
        "primary_code": parsed["primary_code"],
        "primary_group": TAXONOMY[parsed["primary_code"]][0],
        "codes": parsed["codes"],
        "groups": sorted({TAXONOMY[code][0] for code in parsed["codes"]}),
        "labels": labels,
        "other_phrase": parsed["other_phrase"],
        "confidence": parsed["confidence"],
        "reasoning": parsed["reasoning"],
        "evidence": parsed["evidence"],
        "raw_response": parsed["raw_response"],
    }


def _classify_one(
    record: dict[str, Any],
    eval_file: Path,
    system_prompt: str,
    model: Any,
    args: argparse.Namespace,
) -> dict[str, Any]:
    messages = _build_messages(record, eval_file, system_prompt, args)
    extra_body = _claude_extra_body(args)
    raw = ""
    for attempt in range(1, 3):
        raw = model.generate(
            messages,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            response_format=None if args.no_response_format else RESPONSE_FORMAT,
            extra_body=extra_body,
        )
        if raw.strip():
            break
        LOGGER.warning("Empty model response for task %s on attempt %s/2", record.get("task_id"), attempt)
    if not raw.strip():
        raise RuntimeError(f"empty model response for task {record.get('task_id')}")
    try:
        parsed = _parse_response(raw)
    except (json.JSONDecodeError, ValueError):
        LOGGER.warning("Repairing malformed JSON for task %s", record.get("task_id"))
        repair_raw = model.generate(
            [
                {
                    "role": "system",
                    "content": "Repair malformed judge output. Return only valid JSON matching the requested schema.",
                },
                {
                    "role": "user",
                    "content": (
                        "Convert this malformed failure-taxonomy response into valid JSON. "
                        f"Allowed codes: {', '.join(sorted(ALLOWED_CODES))}. "
                        "Preserve the intended labels and evidence; do not add new analysis.\n\n"
                        f"Malformed response:\n{raw}"
                    ),
                },
            ],
            max_tokens=min(args.max_tokens, 900),
            temperature=args.temperature,
            response_format=None if args.no_response_format else RESPONSE_FORMAT,
            extra_body=extra_body,
        )
        parsed = _parse_response(repair_raw)
        raw = repair_raw
    details = _extract_eval_details(record)
    return {
        "schema_version": "lexbench_failure_taxonomy_v1",
        "classified_at": _utc_now(),
        "source_eval_file": str(eval_file),
        "task_id": str(record.get("task_id", "")),
        "model_id": record.get("model_id"),
        "task_type": record.get("task_type"),
        "predicted_label": record.get("predicted_label"),
        "score": details.get("score"),
        "old_failure_category": record.get("failure_category"),
        "taxonomy": _taxonomy_payload(parsed),
    }


def _default_output_path(eval_file: Path) -> Path:
    stem = eval_file.stem
    if stem.endswith("_eval_results"):
        stem = stem[: -len("_eval_results")]
    return eval_file.with_name(f"{stem}_failure_taxonomy.jsonl")


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return slug.strip("-") or "judge"


def _output_path_for_eval(eval_file: Path, args: argparse.Namespace) -> Path:
    if args.output and len(args.eval_files) == 1:
        return Path(args.output)
    path = _default_output_path(eval_file)
    if args.judge_suffix:
        return path.with_name(path.stem + f"_{_slug(args.judge_suffix)}" + path.suffix)
    if args.include_judge_in_output:
        return path.with_name(path.stem + f"_{_slug(args.model)}" + path.suffix)
    return path


def _summary_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.stem + "_summary.json")


def _load_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = _read_jsonl(path)
    return {str(row.get("task_id")): row for row in rows if row.get("task_id") is not None}


def _summarize(rows: list[dict[str, Any]], total_failures: int, selected_failures: int) -> dict[str, Any]:
    primary_code = Counter()
    mention_code = Counter()
    primary_group = Counter()
    mention_group = Counter()
    other_phrases = Counter()

    for row in rows:
        taxonomy = row.get("taxonomy") or {}
        primary = taxonomy.get("primary_code")
        if primary:
            primary_code[primary] += 1
            primary_group[TAXONOMY.get(primary, ("Other", "Other"))[0]] += 1
        for code in taxonomy.get("codes") or []:
            mention_code[code] += 1
            mention_group[TAXONOMY.get(code, ("Other", "Other"))[0]] += 1
        if taxonomy.get("other_phrase"):
            other_phrases[taxonomy["other_phrase"]] += 1

    return {
        "schema_version": "lexbench_failure_taxonomy_summary_v1",
        "generated_at": _utc_now(),
        "total_failures": total_failures,
        "selected_failures": selected_failures,
        "classified_failures": len(rows),
        "primary_code_counts": dict(primary_code),
        "mention_code_counts": dict(mention_code),
        "primary_group_counts": dict(primary_group),
        "mention_group_counts": dict(mention_group),
        "other_phrase_counts": dict(other_phrases),
        "taxonomy": {
            code: {"group": group, "label": label}
            for code, (group, label) in TAXONOMY.items()
        },
    }


def _write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _process_eval_file(eval_file: Path, system_prompt: str, model: Any, args: argparse.Namespace) -> int:
    all_failure_records = [row for row in _read_jsonl(eval_file) if row.get("predicted_label") == 0]
    records = all_failure_records
    if args.task_ids:
        keep_task_ids = {str(task_id) for task_id in args.task_ids}
        records = [row for row in records if str(row.get("task_id", "")) in keep_task_ids]
    if args.exclude_task_ids:
        exclude_task_ids = {str(task_id) for task_id in args.exclude_task_ids}
        records = [row for row in records if str(row.get("task_id", "")) not in exclude_task_ids]
    if args.max_samples is not None:
        records = records[: args.max_samples]

    output_path = _output_path_for_eval(eval_file, args)
    existing = {} if args.overwrite else _load_existing(output_path)

    pending = [row for row in records if str(row.get("task_id", "")) not in existing]
    LOGGER.info(
        "%s: failures=%s selected=%s existing=%s pending=%s output=%s",
        eval_file,
        len(all_failure_records),
        len(records),
        len(existing),
        len(pending),
        output_path,
    )

    if args.dry_run:
        if pending:
            preview = _build_user_text(
                pending[0],
                eval_file,
                trace_char_budget=min(args.trace_char_budget, 4000),
                feedback_char_budget=min(args.feedback_char_budget, 4000),
            )
            LOGGER.info("Dry-run prompt preview for task %s:\n%s", pending[0].get("task_id"), preview[:5000])
        return 0

    results: list[dict[str, Any]] = list(existing.values())
    failed_count = 0
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, args.num_workers)) as executor:
            future_map = {
                executor.submit(_classify_one, row, eval_file, system_prompt, model, args): row
                for row in pending
            }
            completed = as_completed(future_map)
            if tqdm is not None:
                completed = tqdm(
                    completed,
                    total=len(future_map),
                    desc=f"{eval_file.parents[2].name}:{args.model}",
                    unit="traj",
                )
            for future in completed:
                row = future_map[future]
                try:
                    classified = future.result()
                except Exception as exc:  # noqa: BLE001 - keep batch jobs moving
                    LOGGER.exception("Failed to classify task %s: %s", row.get("task_id"), exc)
                    failed_count += 1
                    continue
                if args.verbose:
                    LOGGER.info(
                        "classified task=%s primary=%s codes=%s",
                        classified["task_id"],
                        classified["taxonomy"]["primary_code"],
                        ",".join(classified["taxonomy"]["codes"]),
                    )
                results.append(classified)

    results.sort(key=lambda row: int(row["task_id"]) if str(row["task_id"]).isdigit() else str(row["task_id"]))
    _write_jsonl(output_path, results)
    _write_summary(_summary_path(output_path), _summarize(results, len(all_failure_records), len(records)))
    return failed_count


def _find_eval_files(args: argparse.Namespace) -> list[Path]:
    if args.eval_files:
        return [Path(path).expanduser().resolve() for path in args.eval_files]

    root = Path(args.experiments_root).expanduser().resolve()
    pattern = f"*/20*/tasks_eval_result/{args.eval_filename}"
    files = sorted(root.glob(pattern))
    if args.models:
        keep = set(args.models)
        files = [path for path in files if path.parents[2].name in keep]
    return files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify LexBench-Browser judge-failed trajectories into the compact failure taxonomy.",
    )
    parser.add_argument(
        "--experiments-root",
        default=str(REPO_ROOT / "experiments" / "LexBench-Browser" / "All" / "browser-use"),
        help="Root containing model/timestamp run directories.",
    )
    parser.add_argument(
        "--eval-filename",
        default="task_gpt-4.1_per_task_threshold_stepwise_eval_results.json",
        help="Evaluation JSONL filename under tasks_eval_result.",
    )
    parser.add_argument(
        "--eval-files",
        nargs="*",
        default=[],
        help="Specific eval JSONL files. Overrides --experiments-root discovery.",
    )
    parser.add_argument("--models", nargs="*", default=[], help="Optional model directory names to include.")
    parser.add_argument("--output", default=None, help="Output JSONL path. Only valid with exactly one eval file.")
    parser.add_argument("--model", default="gpt-4.1", help="LLM judge model.")
    parser.add_argument(
        "--include-judge-in-output",
        action="store_true",
        help="Append the judge model name to the default output filename.",
    )
    parser.add_argument("--judge-suffix", default=None, help="Custom suffix appended to the default output filename.")
    parser.add_argument("--api-key", default=None, help="API key. Defaults to EVAL_MODEL_API_KEY or OPENAI_API_KEY.")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL.")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument(
        "--no-response-format",
        action="store_true",
        help="Do not send the JSON schema response_format; rely on prompt-only JSON output.",
    )
    parser.add_argument("--claude-thinking", action="store_true", help="Enable Claude extended thinking through LiteLLM.")
    parser.add_argument(
        "--claude-reasoning-effort",
        default="medium",
        choices=["minimal", "low", "medium", "high"],
        help="Claude reasoning_effort value when --claude-thinking is set.",
    )
    parser.add_argument(
        "--smoke-thinking",
        action="store_true",
        help="Make one small API call and verify usage contains reasoning/thinking tokens.",
    )
    parser.add_argument("--max-samples", type=int, default=None, help="Max failed samples per eval file.")
    parser.add_argument("--task-ids", nargs="*", default=[], help="Only classify these task IDs.")
    parser.add_argument("--exclude-task-ids", nargs="*", default=[], help="Do not classify these task IDs.")
    parser.add_argument(
        "--exclude-task-ids-file",
        type=Path,
        default=None,
        help="Whitespace-separated task IDs to exclude from classification.",
    )
    parser.add_argument("--max-screenshots", type=int, default=3, help="Number of final screenshots to include.")
    parser.add_argument("--image-scale-factor", type=float, default=0.6)
    parser.add_argument("--trace-char-budget", type=int, default=18000)
    parser.add_argument("--feedback-char-budget", type=int, default=12000)
    parser.add_argument("--overwrite", action="store_true", help="Reclassify even when output rows already exist.")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification for the stdlib HTTP fallback client.",
    )
    parser.add_argument("--verbose", action="store_true", help="Log every classified task.")
    parser.add_argument("--dry-run", action="store_true", help="Print counts and a prompt preview without API calls.")
    return parser.parse_args()


def _find_reasoning_tokens(value: Any) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                key_path = f"{path}.{key}" if path else str(key)
                lower = str(key).lower()
                if isinstance(child, (int, float)) and ("reason" in lower or "think" in lower):
                    found.append((key_path, int(child)))
                walk(child, key_path)
        elif isinstance(node, list):
            for idx, child in enumerate(node):
                walk(child, f"{path}[{idx}]")

    walk(value, "")
    return found


def _find_reasoning_content(value: Any) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                key_path = f"{path}.{key}" if path else str(key)
                lower = str(key).lower()
                if isinstance(child, str) and ("reasoning_content" in lower or "thinking_blocks" in lower):
                    text = child.strip()
                    if text:
                        found.append((key_path, _one_line(text, 160)))
                walk(child, key_path)
        elif isinstance(node, list):
            for idx, child in enumerate(node):
                walk(child, f"{path}[{idx}]")

    walk(value, "")
    return found


def _smoke_thinking(model: Any, args: argparse.Namespace) -> int:
    extra_body = _claude_extra_body(args)
    raw = model.generate(
        [
            {"role": "system", "content": "Return only JSON."},
            {"role": "user", "content": "Classify this tiny failure: the agent clicked the same blocked button repeatedly until timeout. Return {\"label\":\"Infinite Loop\"}."},
        ],
        max_tokens=200,
        temperature=args.temperature,
        extra_body=extra_body,
    )
    usage = getattr(model, "last_usage", None)
    response = getattr(model, "last_response", None)
    print("SMOKE_RESPONSE:", raw)
    print("SMOKE_USAGE:", json.dumps(usage, ensure_ascii=False, indent=2))
    reasoning_tokens = _find_reasoning_tokens(response or usage or {})
    reasoning_content = _find_reasoning_content(response or {})
    print("SMOKE_REASONING_TOKEN_FIELDS:", reasoning_tokens)
    print("SMOKE_REASONING_CONTENT_FIELDS:", reasoning_content)
    if args.claude_thinking and not any(value > 0 for _, value in reasoning_tokens) and not reasoning_content:
        LOGGER.error("Claude thinking was requested but no reasoning/thinking token or content field was found.")
        return 1
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _load_env_file(REPO_ROOT / ".env")
    args = parse_args()
    if args.exclude_task_ids_file:
        if not args.exclude_task_ids_file.exists():
            LOGGER.error("--exclude-task-ids-file does not exist: %s", args.exclude_task_ids_file)
            return 2
        args.exclude_task_ids = [
            *args.exclude_task_ids,
            *args.exclude_task_ids_file.read_text(encoding="utf-8").split(),
        ]

    eval_files = _find_eval_files(args)
    args.eval_files = [str(path) for path in eval_files]
    if not eval_files:
        LOGGER.error("No eval files found.")
        return 1
    if args.output and len(eval_files) != 1:
        LOGGER.error("--output is only valid when classifying exactly one eval file.")
        return 1

    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    LOGGER.info("Taxonomy prompt: %s", PROMPT_PATH)
    LOGGER.info("Eval files: %s", len(eval_files))

    model = None
    if not args.dry_run:
        try:
            model = load_evaluation_model(args.model, args.api_key, args.base_url, temperature=args.temperature)
        except ImportError as exc:
            LOGGER.warning("OpenAI SDK wrapper unavailable (%s); using stdlib HTTP fallback.", exc)
            model = SimpleChatModel(
                args.model,
                args.api_key,
                args.base_url,
                insecure=args.insecure,
                claude_thinking=args.claude_thinking,
                reasoning_effort=args.claude_reasoning_effort,
            )

    if args.smoke_thinking:
        return _smoke_thinking(model, args)

    failed_total = 0
    for eval_file in eval_files:
        failed_total += _process_eval_file(eval_file, system_prompt, model, args)

    return 1 if failed_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
