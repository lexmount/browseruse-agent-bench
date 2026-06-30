#!/usr/bin/env python3
"""Local HTTP server for the LexBench step debugger."""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger("step_debugger")

DEFAULT_ROOT = Path(__file__).resolve().parent
DEFAULT_HTML = DEFAULT_ROOT / "rpa_login_step_debugger.html"
OPENAI_COMPATIBLE_TYPES = {"OPENAI", "AZURE"}
NO_TEMPERATURE_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def excerpt(value: Any, limit: int = 220) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit - 1]}..."


def parse_step_number(path: Path) -> int:
    return int(path.stem.replace("step_", ""))


def extract_section(state_message: str | None, tag: str) -> str:
    if not state_message:
        return ""
    start_tag = f"<{tag}>"
    end_tag = f"</{tag}>"
    start = state_message.find(start_tag)
    end = state_message.find(end_tag)
    if start < 0 or end < 0 or end <= start:
        return ""
    return state_message[start + len(start_tag) : end].strip()


def make_relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def resolve_under_root(root: Path, relative_path: str) -> Path:
    decoded = urllib.parse.unquote(relative_path).lstrip("/")
    candidate = (root / decoded).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("Requested path escapes experiment root") from exc
    return candidate


def run_display_info(root: Path, run_dir: Path, config: dict[str, Any]) -> dict[str, str]:
    rel_parts = Path(make_relative(root, run_dir)).parts
    timestamp = rel_parts[-1] if rel_parts else run_dir.name
    if len(rel_parts) >= 4:
        split = rel_parts[0]
        agent = rel_parts[-3]
        model = rel_parts[-2]
    elif len(rel_parts) >= 2:
        split = rel_parts[0]
        agent = config.get("active_model") or "agent"
        model_cfg = config.get("models", {}).get(agent, {})
        model = model_cfg.get("model_id") or rel_parts[-1]
    else:
        split = run_dir.name
        agent = config.get("active_model") or "agent"
        model = "unknown"
    return {
        "split": split,
        "agent": str(agent),
        "model": str(model),
        "timestamp": timestamp,
        "label": " / ".join(rel_parts),
    }


def discover_runs(root: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for config_path in sorted(root.rglob("config_snapshot.json")):
        run_dir = config_path.parent
        tasks_dir = run_dir / "tasks"
        if not tasks_dir.is_dir():
            continue
        config = load_json(config_path)
        task_dirs = sorted(p for p in tasks_dir.iterdir() if p.is_dir())
        step_count = 0
        for task_dir in task_dirs:
            api_dir = task_dir / "api_logs"
            if api_dir.is_dir():
                step_count += len(list(api_dir.glob("step_*.json")))
        info = run_display_info(root, run_dir, config)
        runs.append(
            {
                "run": make_relative(root, run_dir),
                "label": info["label"],
                "split": info["split"],
                "agent": info["agent"],
                "model": info["model"],
                "timestamp": info["timestamp"],
                "task_count": len(task_dirs),
                "step_count": step_count,
                "has_config": True,
            }
        )
    return sorted(runs, key=lambda item: (item["split"], item["model"], item["timestamp"]))


def summarize_result(result_path: Path) -> dict[str, Any]:
    if not result_path.exists():
        return {}
    data = load_json(result_path)
    usage = data.get("metrics", {}).get("usage", {})
    return {
        "task": data.get("task") or "",
        "task_excerpt": excerpt(data.get("task"), 260),
        "answer": data.get("answer") or "",
        "answer_excerpt": excerpt(data.get("answer"), 260),
        "agent_success": data.get("agent_success"),
        "agent_done": data.get("agent_done"),
        "env_status": data.get("env_status"),
        "error": data.get("error"),
        "wall_clock_seconds": data.get("wall_clock_seconds"),
        "steps": data.get("metrics", {}).get("steps"),
        "total_tokens": usage.get("total_tokens"),
        "total_cost": usage.get("total_cost"),
    }


def action_label(actions: Any) -> str:
    if not actions:
        return "no action"
    first = actions[0] if isinstance(actions, list) else actions
    if not isinstance(first, dict) or not first:
        return excerpt(first, 120)
    name = next(iter(first))
    payload = first.get(name)
    if isinstance(payload, dict):
        if "index" in payload:
            return f"{name} #{payload['index']}"
        if "url" in payload:
            return f"{name} {payload['url']}"
        if "query" in payload:
            return f"{name}: {excerpt(payload['query'], 90)}"
    return f"{name}: {excerpt(payload, 90)}"


def step_summary(root: Path, task_dir: Path, step_file: Path) -> dict[str, Any]:
    data = load_json(step_file)
    step_number = data.get("metadata", {}).get("step_number") or parse_step_number(step_file)
    state_message = data.get("input", {}).get("state_message") or ""
    screenshot_ref = data.get("input", {}).get("screenshot_ref")
    screenshot_path = task_dir / screenshot_ref if screenshot_ref else None
    screenshot_url = None
    if screenshot_path and screenshot_path.exists():
        screenshot_url = f"/files/{make_relative(root, screenshot_path)}"
    return {
        "step_number": step_number,
        "model_id": data.get("metadata", {}).get("model_id"),
        "timestamp": data.get("metadata", {}).get("timestamp"),
        "url": data.get("input", {}).get("url") or "",
        "state_chars": len(state_message),
        "browser_state_chars": len(extract_section(state_message, "browser_state")),
        "agent_history_chars": len(extract_section(state_message, "agent_history")),
        "memory_excerpt": excerpt(data.get("output", {}).get("memory"), 240),
        "action_label": action_label(data.get("output", {}).get("actions")),
        "result_excerpt": excerpt(data.get("action_results"), 180),
        "screenshot_url": screenshot_url,
    }


def task_bundle(root: Path, run: str, task_id: str | None) -> dict[str, Any]:
    run_dir = resolve_under_root(root, run)
    tasks_dir = run_dir / "tasks"
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"tasks directory not found for run: {run}")

    task_dirs = sorted((p for p in tasks_dir.iterdir() if p.is_dir()), key=lambda p: p.name)
    if not task_dirs:
        raise FileNotFoundError(f"no task directories found for run: {run}")

    tasks = []
    for task_dir in task_dirs:
        api_dir = task_dir / "api_logs"
        steps = sorted(api_dir.glob("step_*.json")) if api_dir.is_dir() else []
        result = summarize_result(task_dir / "result.json")
        brief_result = {key: value for key, value in result.items() if key not in {"task", "answer"}}
        tasks.append(
            {
                "task_id": task_dir.name,
                "step_count": len(steps),
                **brief_result,
            }
        )

    preferred_task = "15" if run == "jiaxin/20260520_162042" and (tasks_dir / "15").is_dir() else None
    selected = (
        task_id
        or preferred_task
        or next((task["task_id"] for task in tasks if task["step_count"]), tasks[0]["task_id"])
    )
    selected_dir = tasks_dir / selected
    if not selected_dir.is_dir():
        raise FileNotFoundError(f"task not found: {selected}")

    api_dir = selected_dir / "api_logs"
    step_files = sorted(api_dir.glob("step_*.json"), key=parse_step_number) if api_dir.is_dir() else []
    steps = [step_summary(root, selected_dir, step_file) for step_file in step_files]
    system_prompt = api_dir / "system_prompt.txt"

    return {
        "run": run,
        "selected_task_id": selected,
        "tasks": tasks,
        "steps": steps,
        "selected_result": summarize_result(selected_dir / "result.json"),
        "system_prompt_available": system_prompt.exists(),
    }


def step_bundle(root: Path, run: str, task_id: str, step_number: int) -> dict[str, Any]:
    run_dir = resolve_under_root(root, run)
    task_dir = run_dir / "tasks" / task_id
    api_dir = task_dir / "api_logs"
    step_file = api_dir / f"step_{step_number:03d}.json"
    if not step_file.exists():
        raise FileNotFoundError(f"step log not found: {step_file.name}")

    data = load_json(step_file)
    state_message = data.get("input", {}).get("state_message") or ""
    screenshot_ref = data.get("input", {}).get("screenshot_ref")
    screenshot_path = task_dir / screenshot_ref if screenshot_ref else None
    screenshot_url = None
    if screenshot_path and screenshot_path.exists():
        screenshot_url = f"/files/{make_relative(root, screenshot_path)}"

    return {
        "run": run,
        "task_id": task_id,
        "step_number": step_number,
        "log": data,
        "system_prompt": read_text(api_dir / "system_prompt.txt") or "",
        "sections": {
            "agent_history": extract_section(state_message, "agent_history"),
            "agent_state": extract_section(state_message, "agent_state"),
            "browser_state": extract_section(state_message, "browser_state"),
        },
        "screenshot_url": screenshot_url,
        "screenshot_exists": bool(screenshot_url),
    }


def discover_models(root: Path) -> dict[str, Any]:
    models: dict[str, dict[str, Any]] = {}
    for config_path in sorted(root.rglob("config_snapshot.json")):
        config = load_json(config_path)
        for alias, model_config in config.get("models", {}).items():
            if not isinstance(model_config, dict):
                continue
            model_id = model_config.get("model_id") or alias
            base_url = model_config.get("base_url") or ""
            model_type = model_config.get("model_type") or "OPENAI"
            key = f"{alias}|{model_id}|{base_url}"
            entry = models.setdefault(
                key,
                {
                    "key": key,
                    "alias": alias,
                    "model_id": model_id,
                    "model_type": model_type,
                    "base_url": base_url,
                    "has_api_key": bool(model_config.get("api_key")),
                    "supports_replay": model_type in OPENAI_COMPATIBLE_TYPES and bool(base_url),
                    "flags": {
                        "dont_force_structured_output": bool(model_config.get("dont_force_structured_output")),
                        "add_schema_to_system_prompt": bool(model_config.get("add_schema_to_system_prompt")),
                        "remove_min_items_from_schema": bool(model_config.get("remove_min_items_from_schema")),
                        "remove_defaults_from_schema": bool(model_config.get("remove_defaults_from_schema")),
                    },
                    "source_runs": [],
                },
            )
            entry["has_api_key"] = entry["has_api_key"] or bool(model_config.get("api_key"))
            entry["source_runs"].append(make_relative(root, config_path.parent))
    return {"models": sorted(models.values(), key=lambda item: (item["alias"], item["model_id"]))}


def get_model_config(root: Path, model_key: str) -> dict[str, Any]:
    for config_path in sorted(root.rglob("config_snapshot.json")):
        config = load_json(config_path)
        for alias, model_config in config.get("models", {}).items():
            if not isinstance(model_config, dict):
                continue
            model_id = model_config.get("model_id") or alias
            base_url = model_config.get("base_url") or ""
            key = f"{alias}|{model_id}|{base_url}"
            if key == model_key:
                return {"alias": alias, **model_config}
    raise ValueError(f"model not found: {model_key}")


def chat_endpoint(base_url: str) -> str:
    clean = base_url.rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


def supports_temperature(model_id: str | None) -> bool:
    if not model_id:
        return True
    normalized = model_id.lower().replace("_", "-")
    return not normalized.startswith(NO_TEMPERATURE_MODEL_PREFIXES)


def extract_response_content(response_json: dict[str, Any]) -> str:
    choices = response_json.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def replay_step(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    run = str(payload.get("run") or "")
    task_id = str(payload.get("task_id") or "")
    step_number = int(payload.get("step_number") or 0)
    if not run or not task_id or step_number <= 0:
        raise ValueError("run, task_id, and step_number are required")

    bundle = step_bundle(root, run, task_id, step_number)
    system_prompt = payload.get("system_prompt")
    state_message = payload.get("state_message")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        system_prompt = bundle["system_prompt"]
    if not isinstance(state_message, str) or not state_message.strip():
        state_message = bundle["log"].get("input", {}).get("state_message") or ""

    extra_instruction = payload.get("extra_instruction")
    if isinstance(extra_instruction, str) and extra_instruction.strip():
        state_message = f"{state_message}\n\n<debug_instruction>\n{extra_instruction.strip()}\n</debug_instruction>"

    custom = payload.get("custom") if isinstance(payload.get("custom"), dict) else {}
    if payload.get("model_key") == "__custom__":
        model_config = {
            "alias": "custom",
            "model_type": custom.get("model_type") or "OPENAI",
            "model_id": custom.get("model_id"),
            "base_url": custom.get("base_url"),
            "api_key": custom.get("api_key"),
        }
    else:
        model_config = get_model_config(root, str(payload.get("model_key") or ""))

    model_type = model_config.get("model_type") or "OPENAI"
    if model_type not in OPENAI_COMPATIBLE_TYPES:
        raise ValueError(f"model type {model_type} is not OpenAI-compatible for direct replay")
    if not model_config.get("base_url") or not model_config.get("api_key") or not model_config.get("model_id"):
        raise ValueError("selected model must provide base_url, model_id, and api_key")

    request_body: dict[str, Any] = {
        "model": model_config["model_id"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state_message},
        ],
    }
    temperature = payload.get("temperature")
    if isinstance(temperature, int | float):
        if supports_temperature(str(model_config["model_id"])):
            request_body["temperature"] = temperature
    max_tokens = payload.get("max_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        request_body["max_tokens"] = max_tokens
    if payload.get("response_format"):
        request_body["response_format"] = {"type": "json_object"}

    endpoint = chat_endpoint(str(model_config["base_url"]))
    headers = {
        "Authorization": f"Bearer {model_config['api_key']}",
        "Content-Type": "application/json",
    }
    timeout = int(payload.get("timeout_seconds") or 120)
    start = time.perf_counter()
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_text = response.read().decode("utf-8")
        response_json = json.loads(response_text)
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    return {
        "ok": True,
        "model": model_config["model_id"],
        "alias": model_config.get("alias"),
        "endpoint": endpoint,
        "latency_ms": elapsed_ms,
        "content": extract_response_content(response_json),
        "usage": response_json.get("usage"),
        "response": response_json,
        "request": {
            "model": request_body["model"],
            "message_count": len(request_body["messages"]),
            "system_chars": len(system_prompt or ""),
            "state_chars": len(state_message or ""),
            "response_format": request_body.get("response_format"),
            "temperature": request_body.get("temperature"),
            "max_tokens": request_body.get("max_tokens"),
        },
    }


class StepDebuggerHandler(BaseHTTPRequestHandler):
    root: Path = DEFAULT_ROOT
    html_path: Path = DEFAULT_HTML

    def log_message(self, format_string: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format_string % args)

    def send_json(self, data: Any, status: int = 200) -> None:
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_error_json(self, message: str, status: int = 400, details: Any = None) -> None:
        payload = {"ok": False, "error": message}
        if details is not None:
            payload["details"] = details
        self.send_json(payload, status=status)

    def serve_html(self) -> None:
        data = self.html_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_file(self, relative_path: str) -> None:
        path = resolve_under_root(self.root, relative_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(relative_path)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path in {"/", "/rpa_login_step_debugger.html"}:
                self.serve_html()
            elif parsed.path == "/api/runs":
                runs = discover_runs(self.root)
                preferred = "jiaxin/20260520_162042"
                default_run = preferred if any(run["run"] == preferred for run in runs) else (runs[0]["run"] if runs else "")
                self.send_json({"root": str(self.root), "default_run": default_run, "runs": runs})
            elif parsed.path == "/api/models":
                self.send_json(discover_models(self.root))
            elif parsed.path == "/api/task":
                run = params.get("run", [""])[0]
                task_id = params.get("task", [None])[0]
                self.send_json(task_bundle(self.root, run, task_id))
            elif parsed.path == "/api/step":
                run = params.get("run", [""])[0]
                task_id = params.get("task", [""])[0]
                step = int(params.get("step", ["0"])[0])
                self.send_json(step_bundle(self.root, run, task_id, step))
            elif parsed.path.startswith("/files/"):
                self.serve_file(parsed.path.removeprefix("/files/"))
            else:
                self.send_error_json("not found", status=404)
        except FileNotFoundError as exc:
            self.send_error_json(str(exc), status=404)
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.send_error_json(str(exc), status=400)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/replay":
            self.send_error_json("not found", status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body or "{}")
            self.send_json(replay_step(self.root, payload))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            self.send_error_json("model endpoint returned an error", status=502, details={"status": exc.code, "body": body})
        except urllib.error.URLError as exc:
            self.send_error_json("model endpoint is unreachable", status=502, details=str(exc.reason))
        except TimeoutError as exc:
            self.send_error_json("model endpoint timed out", status=504, details=str(exc))
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            self.send_error_json(str(exc), status=400)


def handler_for(root: Path, html_path: Path) -> type[StepDebuggerHandler]:
    class ConfiguredHandler(StepDebuggerHandler):
        pass

    ConfiguredHandler.root = root.resolve()
    ConfiguredHandler.html_path = html_path.resolve()
    return ConfiguredHandler


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the LexBench RPA step debugger.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Experiment root directory")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="HTML debugger file")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--check", action="store_true", help="Validate indexes without starting the server")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    root = args.root.resolve()
    html_path = args.html.resolve()
    if args.check:
        runs = discover_runs(root)
        models = discover_models(root)["models"]
        logger.info("Found %d runs and %d model configs under %s", len(runs), len(models), root)
        return 0

    server = ThreadingHTTPServer((args.host, args.port), handler_for(root, html_path))
    logger.info("Serving %s at http://%s:%s/", root, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping server")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
