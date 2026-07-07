"""bubench list: read-only discovery of benchmarks, agents, and browser backends.

Aggregates the three registries that CLI arguments validate against:
- --data:   eval registry + per-benchmark data_info.json (splits)
- --agent:  configs/agent_registry.yaml + config.yaml agents section (enabled state)
- browsers: browsers registry backend ids
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List, Optional

from browseruse_bench.browsers.registry import list_backends
from browseruse_bench.eval.registry import list_evaluators
from browseruse_bench.utils import (
    REPO_ROOT,
    get_default_split,
    load_agent_registry,
    load_agent_registry_names,
    load_data_info,
    resolve_agent_inline_config,
    resolve_output_model_id,
)


def _collect_benchmarks() -> List[Dict[str, Any]]:
    """One entry per registered evaluator, enriched with data_info.json splits."""
    benchmarks: List[Dict[str, Any]] = []
    for name in list_evaluators():
        data_info = load_data_info(REPO_ROOT / "browseruse_bench" / "data" / name)
        hf_config = data_info.get("huggingface") or {}
        # data_info nests HF config under the benchmark name in some datasets.
        hf_entry = hf_config.get(name) if isinstance(hf_config.get(name), dict) else hf_config
        benchmarks.append({
            "name": name,
            "splits": sorted(data_info.get("split", {})),
            "default_split": get_default_split(data_info),
            "has_local_data": bool(data_info),
            "hf_repo_id": hf_entry.get("repo_id"),
        })
    return benchmarks


def _collect_agents(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Merge configs/agent_registry.yaml (supported) with config.yaml agents (enabled)."""
    enabled = config.get("agents", {}) or {}
    names = sorted(set(load_agent_registry_names()) | set(enabled), key=str.lower)
    agents: List[Dict[str, Any]] = []
    for name in names:
        entry: Dict[str, Any] = {
            "name": name,
            "enabled": name in enabled,
            "registered": bool(load_agent_registry(name)),
        }
        if name in enabled:
            agent_cfg = enabled[name] or {}
            inline = resolve_agent_inline_config(name, config) or {}
            # Effective model key, mirroring resolve_agent_inline_config's fallback.
            entry["active_model"] = (
                agent_cfg.get("active_model")
                or (config.get("default", {}) or {}).get("model")
            )
            entry["model_id"] = resolve_output_model_id(name, inline)
        agents.append(entry)
    return agents


def _collect_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    default = config.get("default", {}) or {}
    return {
        "agent": default.get("agent"),
        "data": default.get("data") or default.get("benchmark"),
        "browser": default.get("browser") or default.get("browser_id"),
    }


def _print_benchmarks(benchmarks: List[Dict[str, Any]]) -> None:
    print("Benchmarks (--data):")
    width = max(len(b["name"]) for b in benchmarks)
    for bench in benchmarks:
        if not bench["has_local_data"]:
            print(f"  {bench['name'].ljust(width)}  (no local data_info.json)")
            continue
        splits = ", ".join(
            s + ("*" if s == bench["default_split"] else "") for s in bench["splits"]
        )
        print(f"  {bench['name'].ljust(width)}  splits: {splits}")
    print("  (* = default split)")


def _print_agents(agents: List[Dict[str, Any]]) -> None:
    print("Agents (--agent):")
    if not agents:
        print("  (none: configs/agent_registry.yaml missing and no agents in config.yaml)")
        return
    width = max(len(a["name"]) for a in agents)
    for agent in agents:
        if not agent["enabled"]:
            print(f"  {agent['name'].ljust(width)}  (not enabled in config.yaml)")
            continue
        details = f"active_model: {agent.get('active_model') or '-'}"
        if agent.get("model_id"):
            details += f"  model_id: {agent['model_id']}"
        if not agent["registered"]:
            details += "  (custom)"
        print(f"  {agent['name'].ljust(width)}  {details}")


def _print_defaults(defaults: Dict[str, Any]) -> None:
    pairs = [f"{key}={value}" for key, value in defaults.items() if value]
    if pairs:
        print(f"Defaults (config.yaml): {'  '.join(pairs)}")


def configure_list_parser(
    parser: argparse.ArgumentParser, config: Optional[Dict[str, Any]] = None
) -> None:
    """Configure arguments for the list command."""
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text",
    )


def list_command(args: argparse.Namespace, config: Dict[str, Any]) -> int:
    """Entry point for the list subcommand."""
    payload = {
        "benchmarks": _collect_benchmarks(),
        "agents": _collect_agents(config),
        "browsers": list_backends(),
        "defaults": _collect_defaults(config),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    _print_benchmarks(payload["benchmarks"])
    print()
    _print_agents(payload["agents"])
    print()
    print("Browsers: " + ", ".join(payload["browsers"]))
    _print_defaults(payload["defaults"])
    return 0
