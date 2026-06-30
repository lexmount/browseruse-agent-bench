#!/usr/bin/env python3
"""
Generate experiment index for Browser Agent Analyzer.

Scans the experiments directory and produces ``data/experiments.json``
consumed by the visualisation frontend.

Directory layout expected:
    experiments/{benchmark}/{split}/{agent}/{timestamp}/
        tasks/{task_id}/
            result.json
            agent_history.gif          (optional)
            api_logs/
                step_001.json … step_NNN.json
                system_prompt.txt       (optional)
                summary.md              (optional)
            trajectory/
                screenshot-1.png … screenshot-N.png
        tasks_eval_result/             (optional)
            *_eval_results.json        (JSONL or JSON)
            *summary.json

Usage:
    python generate_index.py                     # auto-detect repo root
    python generate_index.py /path/to/repo       # explicit repo root
"""

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def find_repo_root() -> Path:
    """Walk upward from this script to find the repository root (has experiments/)."""
    p = Path(__file__).resolve().parent
    while p != p.parent:
        if (p / "experiments").is_dir():
            return p
        # Also check parent (visualization/ lives one level under repo root)
        parent = p.parent
        if (parent / "experiments").is_dir():
            return parent
        p = parent
    raise RuntimeError("Cannot locate experiments/ directory")


def _get_repo_root() -> Path:
    """Use CLI arg only when running as main script, otherwise auto-detect."""
    if __name__ == "__main__" and len(sys.argv) > 1:
        return Path(sys.argv[1])
    return find_repo_root()


# Resolve lazily: _get_repo_root() walks the filesystem looking for experiments/,
# which may not exist in contexts where the package is imported for metadata only
# (e.g. pip install in a venv outside the repo). A missing experiments/ dir is only
# a real error when generate_index() is actually called.
try:
    REPO_ROOT: Optional[Path] = _get_repo_root()
except RuntimeError:
    REPO_ROOT = None
EXPERIMENTS_BASE: Optional[Path] = (REPO_ROOT / "experiments") if REPO_ROOT else None
OUTPUT_FILE = Path(__file__).resolve().parent / "data" / "experiments.json"


def _read_json(path: Path) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_json_lines(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        return []
    return records


def load_lexbench_task_thresholds() -> Dict[str, int]:
    """Load LexBench task_id -> score_threshold from benchmark data files."""
    if REPO_ROOT is None:
        return {}
    data_dir = REPO_ROOT / "benchmarks" / "LexBench-Browser" / "data"
    if not data_dir.exists():
        return {}

    thresholds: Dict[str, int] = {}
    for path in data_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix == ".jsonl":
            records = _read_json_lines(path)
        elif path.suffix == ".json":
            loaded = _read_json(path)
            records = loaded if isinstance(loaded, list) else []
        else:
            continue

        for rec in records:
            task_id = rec.get("id")
            score_threshold = rec.get("score_threshold")
            if task_id is None or score_threshold is None:
                continue
            try:
                thresholds[str(task_id)] = int(score_threshold)
            except (TypeError, ValueError):
                continue

    return thresholds


_LEXBENCH_TASK_THRESHOLDS: Optional[Dict[str, int]] = None


def lexbench_task_thresholds() -> Dict[str, int]:
    """Lazy-cached accessor for LexBench score thresholds."""
    global _LEXBENCH_TASK_THRESHOLDS
    if _LEXBENCH_TASK_THRESHOLDS is None:
        _LEXBENCH_TASK_THRESHOLDS = load_lexbench_task_thresholds()
    return _LEXBENCH_TASK_THRESHOLDS


def load_task_rubrics() -> Dict[str, Dict]:
    """Load task rubrics (reference_answer etc.) from all benchmark data files."""
    rubrics: Dict[str, Dict] = {}

    if REPO_ROOT is None:
        return rubrics

    # Datasets live under browseruse_bench/data/<benchmark_name>/ post-refactor.
    # Tolerate parent-level fallback for unusual repo layouts where
    # find_repo_root() resolves a nested path.
    data_root = REPO_ROOT / "browseruse_bench" / "data"
    if not data_root.exists():
        data_root = REPO_ROOT.parent / "browseruse_bench" / "data"
    if not data_root.exists():
        return rubrics

    for bench_dir in data_root.iterdir():
        if not bench_dir.is_dir():
            continue

        for path in bench_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix == ".jsonl":
                records = _read_json_lines(path)
            elif path.suffix == ".json":
                loaded = _read_json(path)
                records = loaded if isinstance(loaded, list) else []
            else:
                continue

            for rec in records:
                task_id = rec.get("id")
                if task_id is None:
                    continue
                ref = rec.get("reference_answer")
                if not ref:
                    continue
                tid = str(task_id)
                if tid in rubrics:
                    continue  # first seen wins
                rubrics[tid] = {
                    "steps": ref.get("steps", []),
                    "key_points": ref.get("key_points", []),
                    "common_mistakes": ref.get("common_mistakes", []),
                    "scoring": ref.get("scoring"),
                    "score_threshold": rec.get("score_threshold"),
                }

    return rubrics


_TASK_RUBRICS: Optional[Dict[str, Dict]] = None


def task_rubrics() -> Dict[str, Dict]:
    """Lazy-cached accessor for task rubrics."""
    global _TASK_RUBRICS
    if _TASK_RUBRICS is None:
        _TASK_RUBRICS = load_task_rubrics()
    return _TASK_RUBRICS


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _natural_sort_key(s: str):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def _relative_repo_path(path: Path) -> Optional[str]:
    if REPO_ROOT is None:
        return None
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return None


def _collect_run_output_logs(timestamp_dir: Path) -> List[Dict[str, str]]:
    """Collect run-level logs that should be visible with an experiment."""
    if REPO_ROOT is None:
        return []

    candidates: List[tuple[str, Path]] = []

    for path in (timestamp_dir / "run.log", timestamp_dir / "eval.log"):
        if path.is_file():
            candidates.append(("experiment", path))

    logs_dir = timestamp_dir / "logs"
    if logs_dir.is_dir():
        for path in sorted(logs_dir.glob("*.log"), key=lambda p: _natural_sort_key(p.name)):
            if path.is_file():
                candidates.append(("experiment", path))

    eval_log = timestamp_dir / "tasks_eval_result" / "eval.log"
    if eval_log.is_file():
        candidates.append(("experiment", eval_log))

    output_logs_dir = REPO_ROOT / "output" / "logs"
    timestamp = timestamp_dir.name
    if output_logs_dir.is_dir():
        for category_dir in sorted(output_logs_dir.iterdir(), key=lambda p: _natural_sort_key(p.name)):
            if not category_dir.is_dir():
                continue
            for path in sorted(category_dir.glob(f"*{timestamp}*.log"), key=lambda p: _natural_sort_key(p.name)):
                if path.is_file():
                    candidates.append((f"output/{category_dir.name}", path))

    seen: set[str] = set()
    logs: List[Dict[str, str]] = []
    for source, path in candidates:
        rel_path = _relative_repo_path(path)
        if not rel_path or rel_path in seen:
            continue
        seen.add(rel_path)
        logs.append({
            "name": path.name,
            "path": rel_path,
            "source": source,
        })
    return logs


# Normalized browser kinds exposed to the frontend. Per-task `browser_id`
# values seen in real result.json files: "lexmount" (lex cloud browser),
# "Chrome-Local" / "local" (local Chrome), "" or missing (older runs / agents
# that don't record this field, e.g. early Agent-TARS / skyvern reconstructed).
_BROWSER_LABEL = {
    "lexmount": "Lexmount",
    "local": "Local",
    "unknown": "Unknown",
}


def _normalize_browser_id(raw: Optional[str]) -> str:
    """Map a raw browser_id to one of {lexmount, local, unknown}."""
    if not raw:
        return "unknown"
    s = str(raw).strip().lower()
    if not s:
        return "unknown"
    if "lexmount" in s:
        return "lexmount"
    if "local" in s:  # matches "local" and "Chrome-Local"
        return "local"
    return "unknown"


def _summarize_browsers(
    raw_values: List[str],
) -> Tuple[str, str, str, bool, Dict[str, int]]:
    """Aggregate per-task browser_id values into run-level summary fields.

    Returns a 5-tuple: (browser_kind, browser_label, browser_id_raw, is_mixed,
    breakdown).

    - `browser_kind`: dominant normalized kind across the run.
    - `browser_label`: display label for the dominant kind.
    - `browser_id_raw`: most-common original string (for tooltip / debugging).
    - `is_mixed`: True when more than one *known* normalized kind appears
       (unknown plus a single known kind is treated as the known kind, not
       mixed — unknown is data noise, not a real browser switch).
    - `breakdown`: ordered mapping `{kind: count}` over all tasks (including
       unknown), used by the frontend to render distributions like
       "Mixed (47 lexmount + 2 local)".
    """
    if not raw_values:
        return "unknown", _BROWSER_LABEL["unknown"], "", False, {}

    kinds = [_normalize_browser_id(v) for v in raw_values]
    kind_counts = Counter(kinds)

    known_kinds = [k for k in kinds if k != "unknown"]
    if known_kinds:
        # Counter preserves insertion order in CPython 3.7+, but we want a
        # deterministic majority-first order — that's what most_common gives us.
        dominant_kind = Counter(known_kinds).most_common(1)[0][0]
        is_mixed = len(set(known_kinds)) > 1
    else:
        dominant_kind = "unknown"
        is_mixed = False

    # Most common non-empty raw string for display (helps distinguish
    # "Chrome-Local" vs "local" inside the Local kind).
    raw_counts = Counter(v for v in raw_values if v)
    raw_dominant = raw_counts.most_common(1)[0][0] if raw_counts else ""

    # breakdown is sorted by count desc so the dominant kind always lands
    # first when the frontend renders "kind1 + kind2 + ..." inline.
    breakdown = dict(kind_counts.most_common())

    return (
        dominant_kind,
        _BROWSER_LABEL[dominant_kind],
        raw_dominant,
        is_mixed,
        breakdown,
    )


# ------------------------------------------------------------------
# Per-task scanning
# ------------------------------------------------------------------

def scan_task(task_dir: Path, run_path_rel: str) -> Optional[Dict]:
    """Scan a single task directory and return metadata dict."""
    result_file = task_dir / "result.json"
    if not result_file.exists():
        return None

    result = _read_json(result_file)
    if result is None:
        return None

    task_id = str(result.get("task_id", task_dir.name))

    # Screenshots
    trajectory_dir = task_dir / "trajectory"
    screenshots: Dict[int, str] = {}
    if trajectory_dir.exists():
        for f in trajectory_dir.iterdir():
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                nums = re.findall(r"\d+", f.stem)
                step_num = int(nums[-1]) if nums else 0
                screenshots[step_num] = f.name

    # API logs
    api_logs_dir = task_dir / "api_logs"
    api_logs: Dict[int, str] = {}
    if api_logs_dir.exists():
        for f in sorted(api_logs_dir.glob("step_*.json")):
            nums = re.findall(r"\d+", f.stem)
            if nums:
                step_num = int(nums[-1])
                api_logs[step_num] = f.name

    # GIF
    has_gif = (task_dir / "agent_history.gif").exists()

    return {
        "task_id": task_id,
        "task": result.get("task", ""),
        "model_id": result.get("model_id", "unknown"),
        "browser_id": result.get("browser_id", ""),
        "agent_success": result.get("agent_success"),
        "agent_done": result.get("agent_done"),
        "env_status": result.get("env_status"),
        "action_history": result.get("action_history", []),
        "metrics": result.get("metrics") or {},
        "config": result.get("config", {}),
        "run_metadata": result.get("run_metadata", {}),
        "screenshots": screenshots,
        "api_logs": api_logs,
        "has_gif": has_gif,
        "score_threshold": lexbench_task_thresholds().get(task_id),
    }


# ------------------------------------------------------------------
# Eval data loading
# ------------------------------------------------------------------

def _extract_prompt_text(prompt: Optional[Dict]) -> str:
    """Extract display text from a PromptSnapshot (TextPrompt or TemplatePrompt)."""
    if not prompt:
        return ""
    kind = prompt.get("kind", "")
    if kind == "text":
        return prompt.get("text", "")
    if kind == "template":
        return prompt.get("template", "")
    return ""


def load_eval_data(eval_dir: Path) -> Dict:
    """Load evaluation results from tasks_eval_result/."""
    eval_data: Dict[str, Any] = {
        "summary": {},
        "task_results": {},
        "eval_prompts": {},
    }
    if not eval_dir.exists():
        return eval_data

    # Summary
    for sf in eval_dir.glob("*summary.json"):
        s = _read_json(sf)
        if s:
            eval_data["summary"] = s
        break

    # Per-task results (JSONL)
    prompts_extracted = False
    for rf in eval_dir.glob("*eval_results.json"):
        try:
            with open(rf, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    tid = str(rec.get("task_id", ""))
                    details = rec.get("evaluation_details", {})

                    # Extract shared prompts from first record (run-level)
                    if not prompts_extracted:
                        sys_p = details.get("system_prompt")
                        usr_p = details.get("user_prompt")
                        eval_data["eval_prompts"] = {
                            "system_prompt": _extract_prompt_text(sys_p),
                            "user_prompt_template": (
                                usr_p.get("template", "")
                                if usr_p and usr_p.get("kind") == "template"
                                else None
                            ),
                        }
                        prompts_extracted = True

                    # Per-task result
                    task_entry: Dict[str, Any] = {
                        "score": details.get("score") or 0,
                        "predicted_label": rec.get("predicted_label") or 0,
                        "response": details.get("response", ""),
                        "reasoning": details.get("reasoning", ""),
                    }

                    # User prompt: store params (template) or full text
                    usr_p = details.get("user_prompt")
                    if usr_p:
                        if usr_p.get("kind") == "template":
                            task_entry["user_prompt_params"] = usr_p.get("params", {})
                        elif usr_p.get("kind") == "text":
                            task_entry["user_prompt_text"] = usr_p.get("text", "")

                    eval_data["task_results"][tid] = task_entry
        except Exception:
            pass
        break

    return eval_data


# ------------------------------------------------------------------
# Run scanning
# ------------------------------------------------------------------

def scan_run(benchmark: str, split: str, agent: str, timestamp_dir: Path, model: Optional[str] = None) -> Optional[Dict]:
    """Scan one experiment run directory."""
    tasks_dir = timestamp_dir / "tasks"
    if not tasks_dir.exists():
        return None

    run_path_rel = str(timestamp_dir.relative_to(REPO_ROOT))
    uuid = timestamp_dir.name

    tasks: Dict[str, Dict] = {}
    task_ids: List[str] = []

    for td in sorted(tasks_dir.iterdir(), key=lambda x: _natural_sort_key(x.name)):
        if not td.is_dir():
            continue
        task_data = scan_task(td, run_path_rel)
        if task_data:
            tid = task_data["task_id"]
            tasks[tid] = task_data
            task_ids.append(tid)

    if not tasks:
        return None

    # Eval – filter to this run's tasks only (eval files may cover the
    # entire subset, not just the tasks this run actually executed).
    eval_dir = timestamp_dir / "tasks_eval_result"
    eval_data = load_eval_data(eval_dir)
    run_task_set = set(tasks.keys())
    eval_data["task_results"] = {
        tid: v for tid, v in eval_data["task_results"].items()
        if tid in run_task_set
    }

    # Build model_id / config from first task
    first = next(iter(tasks.values()))
    model_id = first.get("model_id", "unknown")
    config = first.get("config", {})
    config_snapshot = _read_json(timestamp_dir / "config_snapshot.json") or {}
    machine = config_snapshot.get("machine")
    if not isinstance(machine, dict):
        run_metadata = first.get("run_metadata", {})
        machine = run_metadata.get("machine") if isinstance(run_metadata, dict) else {}
    if not isinstance(machine, dict):
        machine = {}

    # Aggregate browser_id across all tasks. Picking the dominant value
    # (rather than only first task) is robust to synthetic / reconstructed
    # placeholder records that may carry an empty browser_id.
    (
        browser_kind,
        browser_label,
        browser_raw,
        browser_mixed,
        browser_breakdown,
    ) = _summarize_browsers([t.get("browser_id", "") for t in tasks.values()])

    # Stats
    total = len(tasks)
    evaluated = len(eval_data["task_results"])
    success_count = sum(
        1 for t in eval_data["task_results"].values()
        if t.get("score", 0) >= 60 or t.get("predicted_label", 0) == 1
    )
    # Fallback: count from agent_success when no eval results
    if evaluated == 0:
        evaluated = sum(1 for t in tasks.values() if t.get("agent_success") is not None)
        success_count = sum(1 for t in tasks.values() if t.get("agent_success") is True)
    success_rate = round(success_count / evaluated * 100, 2) if evaluated else 0

    # Per-task avg steps and cost
    all_steps = [
        (s if (s := t["metrics"].get("steps")) is not None else len(t["action_history"]))
        for t in tasks.values()
    ]
    all_costs = [(t["metrics"].get("usage") or {}).get("total_cost", 0) for t in tasks.values()]
    avg_steps = round(sum(all_steps) / len(all_steps), 1) if all_steps else 0
    avg_cost = round(sum(all_costs) / len(all_costs), 4) if all_costs else 0

    # task_files: lightweight per-task file listings
    task_files: Dict[str, Dict] = {}
    task_meta: Dict[str, Dict[str, Any]] = {}
    for tid, td in tasks.items():
        task_files[tid] = {
            "screenshots": td["screenshots"],
            "api_logs": td["api_logs"],
            "has_gif": td["has_gif"],
        }
        td_raw_browser = td.get("browser_id", "")
        task_meta[tid] = {
            "score_threshold": td.get("score_threshold"),
            "agent_success": td.get("agent_success"),
            "browser": _normalize_browser_id(td_raw_browser),
            "browser_id_raw": td_raw_browser,
        }

    return {
        "uuid": uuid,
        "benchmark": benchmark,
        "split": split,
        "agent": agent,
        "model": model,
        "model_id": model_id,
        "machine_id": machine.get("machine_id", ""),
        "machine": machine,
        "config": config,
        "browser": browser_kind,
        "browser_label": browser_label,
        "browser_id_raw": browser_raw,
        "browser_mixed": browser_mixed,
        "browser_breakdown": browser_breakdown,
        "stats": {
            "total_tasks": total,
            "evaluated_tasks": evaluated,
            "success_count": success_count,
            "success_rate": success_rate,
            "avg_steps": avg_steps,
            "avg_cost": avg_cost,
        },
        "task_ids": task_ids,
        "task_files": task_files,
        "task_meta": task_meta,
        "output_logs": _collect_run_output_logs(timestamp_dir),
        "path": run_path_rel,
        "eval_data": eval_data,
    }


# ------------------------------------------------------------------
# Judge experiment set scanning
# ------------------------------------------------------------------

def scan_judge_eval_run(run_dir: Path) -> Optional[Dict]:
    """Scan one eval run directory: evals/{method}/{run}/."""
    summary = _read_json(run_dir / "summary.json")
    if summary is None:
        return None

    status = summary.get("status", "unknown")

    task_results: Dict[str, Dict] = {}
    records = _read_json_lines(run_dir / "eval_results.jsonl")
    for rec in records:
        tid = str(rec.get("task_id", ""))
        if not tid:
            continue
        task_results[tid] = {
            "score": rec.get("score"),
            "verdict": rec.get("verdict"),
            "reasoning": rec.get("reasoning", ""),
            "summary_text": rec.get("summary", ""),
        }

    return {
        "id": run_dir.name,
        "path": str(run_dir.relative_to(REPO_ROOT)),
        "status": status,
        "summary": {
            "score_mean": summary.get("score_mean"),
            "score_std": summary.get("score_std"),
            "pass_rate": summary.get("pass_rate"),
            "expected_task_count": summary.get("expected_task_count"),
            "completed_task_count": summary.get("completed_task_count"),
        },
        "task_results": task_results,
    }


def _compute_exp_set_aggregates(methods: List[Dict], task_ids: List[str]) -> Dict:
    """Compute per_method and per_task aggregates for an experiment set.

    Score statistics (mean/std/min/max) and verdict statistics (pass_rate/flip)
    are tracked independently so that samples with only a verdict (no numeric
    score) are not silently discarded.
    """
    per_method: Dict[str, Dict] = {}
    # Raw per-task accumulators; scores and verdicts tracked separately.
    per_task_raw: Dict[str, Dict] = {
        tid: {"scores": [], "verdicts": []} for tid in task_ids
    }

    for method in methods:
        m_scores: List[float] = []
        m_verdicts: List[str] = []

        for run in method["runs"]:
            if run["status"] == "failed" and not run["task_results"]:
                continue
            for tid, result in run["task_results"].items():
                score = result.get("score")
                verdict = result.get("verdict")
                if isinstance(score, (int, float)):
                    m_scores.append(float(score))
                    if tid in per_task_raw:
                        per_task_raw[tid]["scores"].append(float(score))
                if verdict in ("pass", "fail"):
                    m_verdicts.append(verdict)
                    if tid in per_task_raw:
                        per_task_raw[tid]["verdicts"].append(verdict)

        # Score stats — only defined when there are numeric scores.
        n_scores = len(m_scores)
        if n_scores > 0:
            mean = sum(m_scores) / n_scores
            std = statistics.stdev(m_scores) if n_scores > 1 else 0.0
            score_stats: Dict = {
                "score_sample_count": n_scores,
                "score_mean": round(mean, 4),
                "score_std": round(std, 4),
                "score_min": min(m_scores),
                "score_max": max(m_scores),
            }
        else:
            score_stats = {
                "score_sample_count": 0,
                "score_mean": None,
                "score_std": None,
                "score_min": None,
                "score_max": None,
            }

        # Verdict stats — independent of whether numeric scores exist.
        n_verdicts = len(m_verdicts)
        pass_c = sum(1 for v in m_verdicts if v == "pass")
        verdict_stats: Dict = {
            "verdict_sample_count": n_verdicts,
            "pass_rate": round(pass_c / n_verdicts, 4) if n_verdicts else None,
        }

        per_method[method["id"]] = {**score_stats, **verdict_stats}

    per_task: Dict[str, Dict] = {}
    for tid in task_ids:
        raw = per_task_raw.get(tid, {"scores": [], "verdicts": []})
        scores = raw["scores"]
        verdicts = raw["verdicts"]

        n_scores = len(scores)
        n_verdicts = len(verdicts)

        # Score statistics.
        if n_scores > 0:
            mean = sum(scores) / n_scores
            std = statistics.stdev(scores) if n_scores > 1 else 0.0
            t_score_stats: Dict = {
                "score_sample_count": n_scores,
                "score_mean": round(mean, 4),
                "score_std": round(std, 4),
                "score_min": min(scores),
                "score_max": max(scores),
            }
        else:
            t_score_stats = {
                "score_sample_count": 0,
                "score_mean": None,
                "score_std": None,
                "score_min": None,
                "score_max": None,
            }

        # Verdict statistics — independent of score count.
        has_flip = (
            "pass" in verdicts and "fail" in verdicts
        ) if n_verdicts >= 2 else False

        per_task[tid] = {
            **t_score_stats,
            "verdict_sample_count": n_verdicts,
            "has_verdict_flip": has_flip,
        }

    # high_variance uses score_sample_count (need numeric scores to compute variance).
    high_var = sorted(
        [tid for tid in task_ids
         if per_task[tid]["score_sample_count"] >= 2
         and per_task[tid]["score_std"] is not None],
        key=lambda t: per_task[t]["score_std"],
        reverse=True,
    )[:10]

    flip_tasks = [tid for tid in task_ids if per_task.get(tid, {}).get("has_verdict_flip")]

    return {
        "per_method": per_method,
        "per_task": per_task,
        "high_variance_tasks": high_var,
        "verdict_flip_tasks": flip_tasks,
    }


def _extract_task_rubrics_from_evals(evals_dir: Path) -> Dict[str, Dict]:
    """Extract per-task rubric info (scoring_items, key_points, etc.) from
    detailed eval result files (*_eval_results.json).  These fields are the
    same across runs, so we only need to read them once per task."""
    rubrics: Dict[str, Dict] = {}
    if not evals_dir.exists():
        return rubrics

    RUBRIC_FIELDS = [
        "scoring_items", "key_points", "reference_steps",
        "common_mistakes", "deductions",
    ]

    for method_dir in evals_dir.iterdir():
        if not method_dir.is_dir():
            continue
        for run_dir in method_dir.iterdir():
            if not run_dir.is_dir():
                continue
            # Find detailed eval result files (JSONL-like, one JSON per line)
            for f in run_dir.iterdir():
                if f.name == "eval_results.jsonl" or f.name == "summary.json":
                    continue
                if not f.name.endswith("_eval_results.json"):
                    continue
                records = _read_json_lines(f)
                for rec in records:
                    tid = str(rec.get("task_id", ""))
                    if not tid or tid in rubrics:
                        continue
                    params = (rec.get("evaluation_details") or {}).get(
                        "user_prompt", {}
                    ).get("params", {})
                    rubric = {
                        k: params[k]
                        for k in RUBRIC_FIELDS
                        if k in params and params[k]
                    }
                    bench = (rec.get("evaluation_details") or {}).get(
                        "benchmark_details", {}
                    )
                    if bench.get("score_threshold") is not None:
                        rubric["score_threshold"] = bench["score_threshold"]
                    if rubric:
                        rubrics[tid] = rubric
        # Once we have rubrics from first method/run, that's enough
        if rubrics:
            break
    return rubrics


def scan_experiment_set(benchmark: str, set_dir: Path) -> Optional[Dict]:
    """Scan one judge experiment set directory."""
    manifest = _read_json(set_dir / "manifest.json")
    if manifest is None:
        return None

    set_id = set_dir.name
    display_name = manifest.get("display_name", set_id)

    tasks_dir = set_dir / "tasks"
    task_ids: List[str] = []
    task_meta: Dict[str, Dict] = {}
    if tasks_dir.exists():
        for td in sorted(tasks_dir.iterdir(), key=lambda x: _natural_sort_key(x.name)):
            if not td.is_dir():
                continue
            result = _read_json(td / "result.json")
            if result is not None:
                tid = str(result.get("task_id", td.name))
                task_desc = result.get("task", "")
            else:
                tid = td.name
                task_desc = ""

            # Screenshot file listing (reuses the same logic as scan_task)
            trajectory_dir = td / "trajectory"
            screenshots: Dict[int, str] = {}
            if trajectory_dir.exists():
                for f in trajectory_dir.iterdir():
                    if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                        nums = re.findall(r"\d+", f.stem)
                        step_num = int(nums[-1]) if nums else 0
                        screenshots[step_num] = f.name

            has_gif = (td / "agent_history.gif").exists()

            meta: Dict[str, Any] = {
                "task": task_desc,
                "path": str(td.relative_to(REPO_ROOT)),
                "screenshots": screenshots,
                "has_gif": has_gif,
            }

            # Include key result.json fields for display
            if result is not None:
                meta["answer"] = result.get("answer", "")
                meta["agent_success"] = result.get("agent_success")
                meta["env_status"] = result.get("env_status", "")
                meta["model_id"] = result.get("model_id", "")
                meta["config"] = result.get("config")
                metrics = result.get("metrics")
                if metrics:
                    meta["metrics"] = {
                        "steps": metrics.get("steps"),
                        "end_to_end_ms": metrics.get("end_to_end_ms"),
                        "total_cost": (metrics.get("usage") or {}).get("total_cost"),
                    }

            task_ids.append(tid)
            task_meta[tid] = meta

    # Extract task-level rubric info from detailed eval files
    evals_dir = set_dir / "evals"
    local_rubrics = _extract_task_rubrics_from_evals(evals_dir)
    for tid, rubric in local_rubrics.items():
        if tid in task_meta:
            task_meta[tid]["rubric"] = rubric

    methods: List[Dict] = []
    if evals_dir.exists():
        for method_dir in sorted(evals_dir.iterdir(), key=lambda x: _natural_sort_key(x.name)):
            if not method_dir.is_dir():
                continue
            method_id = method_dir.name
            runs: List[Dict] = []
            for run_dir in sorted(method_dir.iterdir(), key=lambda x: _natural_sort_key(x.name)):
                if not run_dir.is_dir():
                    continue
                run_data = scan_judge_eval_run(run_dir)
                if run_data:
                    runs.append(run_data)

            if not runs:
                continue

            completed = sum(1 for r in runs if r["status"] == "completed")
            partial = sum(1 for r in runs if r["status"] == "partial")
            failed = sum(1 for r in runs if r["status"] == "failed")

            methods.append({
                "id": method_id,
                "display_name": method_id,
                "run_count": len(runs),
                "completed_run_count": completed,
                "partial_run_count": partial,
                "failed_run_count": failed,
                "runs": runs,
            })

    aggregates = _compute_exp_set_aggregates(methods, task_ids)

    return {
        "id": set_id,
        "type": "judge_experiment_set",
        "benchmark": benchmark,
        "display_name": display_name,
        "path": str(set_dir.relative_to(REPO_ROOT)),
        "task_count": len(task_ids),
        "task_ids": task_ids,
        "task_meta": task_meta,
        "methods": methods,
        "aggregates": aggregates,
    }


# ------------------------------------------------------------------
# Full scan
# ------------------------------------------------------------------

def generate_index() -> Dict:
    """Walk experiments/ and build full index."""
    if REPO_ROOT is None or EXPERIMENTS_BASE is None:
        raise RuntimeError(
            "Cannot locate experiments/ directory. "
            "Run from a repo checkout, or pass the repo root as argv[1] when invoking generate_index.py."
        )
    index: Dict[str, Any] = {
        "generated_at": None,
        "runs": [],
        "experiment_sets": [],
        "all_tasks": set(),
        "common_tasks": None,
    }

    if not EXPERIMENTS_BASE.exists():
        print(f"Warning: {EXPERIMENTS_BASE} does not exist")
        index["all_tasks"] = []
        index["common_tasks"] = []
        return index

    # Walk: experiments/{benchmark}/{split}/{agent}/{timestamp}
    for benchmark_dir in sorted(EXPERIMENTS_BASE.iterdir()):
        if not benchmark_dir.is_dir():
            continue
        benchmark = benchmark_dir.name

        # Scan judge experiment sets under judge_experiments/
        judge_dir = benchmark_dir / "judge_experiments"
        if judge_dir.is_dir():
            for set_dir in sorted(judge_dir.iterdir(), key=lambda x: _natural_sort_key(x.name)):
                if not set_dir.is_dir():
                    continue
                print(f"  Scanning exp set: {benchmark}/judge_experiments/{set_dir.name}")
                set_data = scan_experiment_set(benchmark, set_dir)
                if set_data:
                    index["experiment_sets"].append(set_data)

        for split_dir in sorted(benchmark_dir.iterdir()):
            if not split_dir.is_dir():
                continue
            split = split_dir.name
            if split == "judge_experiments":
                continue  # already handled above

            for agent_dir in sorted(split_dir.iterdir()):
                if not agent_dir.is_dir():
                    continue
                agent = agent_dir.name

                for sub_dir in sorted(agent_dir.iterdir()):
                    if not sub_dir.is_dir():
                        continue
                    # Support both 4-level ({agent}/{timestamp}) and
                    # 5-level ({agent}/{model}/{timestamp}) directory layouts.
                    if (sub_dir / "tasks").exists():
                        candidates = [(sub_dir, None)]
                    else:
                        model_name = sub_dir.name
                        candidates = [
                            (d, model_name)
                            for d in sorted(sub_dir.iterdir())
                            if d.is_dir()
                        ]
                    for ts_dir, model_name in candidates:
                        print(f"  Scanning {benchmark}/{split}/{agent}/{ts_dir.relative_to(agent_dir)}")
                        run_data = scan_run(benchmark, split, agent, ts_dir, model=model_name)
                        if run_data:
                            index["runs"].append(run_data)
                            index["all_tasks"].update(run_data["task_ids"])

                            if index["common_tasks"] is None:
                                index["common_tasks"] = set(run_data["task_ids"])
                            else:
                                index["common_tasks"] &= set(run_data["task_ids"])

    # Task rubrics (benchmark-level, shared across runs)
    # Only include rubrics for tasks that appear in at least one run.
    index["task_rubrics"] = {
        tid: rubric for tid, rubric in task_rubrics().items()
        if tid in index["all_tasks"]  # all_tasks is still a set here
    }

    # Serialise sets
    index["all_tasks"] = sorted(index["all_tasks"], key=_natural_sort_key)
    index["common_tasks"] = sorted(index["common_tasks"] or [], key=_natural_sort_key)

    from datetime import datetime, timezone
    index["generated_at"] = datetime.now(timezone.utc).isoformat()

    return index


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    print(f"Repo root  : {REPO_ROOT}")
    print(f"Experiments: {EXPERIMENTS_BASE}")
    print(f"Output     : {OUTPUT_FILE}")
    print()

    index = generate_index()

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"\nIndex written to: {OUTPUT_FILE}")
    print(f"  Runs           : {len(index['runs'])}")
    print(f"  Experiment sets: {len(index['experiment_sets'])}")
    print(f"  All tasks      : {len(index['all_tasks'])}")
    print(f"  Common tasks   : {len(index['common_tasks'])}")


if __name__ == "__main__":
    main()
