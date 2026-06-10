#!/usr/bin/env python3
"""
Development server for Browser Agent Analyzer.

Features:
  - Serves static files (HTML/JS/CSS/images)
  - Maps /experiments/* requests to REPO_ROOT/experiments/ (no symlink needed)
  - POST /api/regenerate  → re-generates experiments.json and returns it
  - GET  /api/index-mtime → returns last-modified time of experiments.json
  - --watch               → auto-regenerate when experiments/ changes

Usage:
    python serve.py                    # serve on port 8080
    python serve.py --port <YOUR_PORT> # custom port
    python serve.py --watch            # auto-regenerate on file changes
"""

import argparse
import json
import os
import posixpath
import socket
import sys
import threading
import time
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from browseruse_bench.visualization.generate_index import generate_index, OUTPUT_FILE, REPO_ROOT

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Index regeneration
# ---------------------------------------------------------------------------

_regen_lock = threading.Lock()


def regenerate_index() -> dict:
    """Thread-safe index regeneration."""
    with _regen_lock:
        print("[regen] Regenerating experiments.json ...")
        index = generate_index()
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        print(f"[regen] Done. {len(index['runs'])} runs, {len(index['all_tasks'])} tasks")
        return index


# ---------------------------------------------------------------------------
# File-system watcher (polling-based, no extra deps)
# ---------------------------------------------------------------------------

def _snapshot_experiments(base: Path) -> dict:
    """Return a lightweight snapshot: {relative_path: mtime} for directories."""
    snap = {}
    if not base.exists():
        return snap
    for root, dirs, files in os.walk(base, followlinks=True):
        rel = os.path.relpath(root, base)
        try:
            snap[rel] = os.stat(root).st_mtime
        except OSError:
            pass
        # Only track key files to limit overhead
        for fn in files:
            if fn in ("result.json", "agent_history.gif") or fn.startswith("step_"):
                fp = os.path.join(root, fn)
                try:
                    snap[os.path.relpath(fp, base)] = os.stat(fp).st_mtime
                except OSError:
                    pass
    return snap


def watch_experiments(interval: float = 3.0):
    """Background thread: poll experiments/ and regenerate on changes."""
    if REPO_ROOT is None:
        print("[watch] Cannot watch: experiments/ directory not found")
        return
    experiments_dir = REPO_ROOT / "experiments"
    print(f"[watch] Watching {experiments_dir}  (poll every {interval}s)")
    prev = _snapshot_experiments(experiments_dir)

    while True:
        time.sleep(interval)
        curr = _snapshot_experiments(experiments_dir)
        if curr != prev:
            added = set(curr) - set(prev)
            removed = set(prev) - set(curr)
            changed = {k for k in set(curr) & set(prev) if curr[k] != prev[k]}
            summary = []
            if added:
                summary.append(f"+{len(added)} new")
            if removed:
                summary.append(f"-{len(removed)} removed")
            if changed:
                summary.append(f"~{len(changed)} modified")
            print(f"[watch] Changes detected: {', '.join(summary)}")
            regenerate_index()
            prev = curr


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class AnalyzerHandler(SimpleHTTPRequestHandler):
    """Extends SimpleHTTPRequestHandler with API endpoints."""

    def translate_path(self, path):
        # Map repo-owned artifact directories directly so no symlink is needed
        # inside the package directory.
        p = posixpath.normpath(urllib.parse.unquote(path.split('?', 1)[0].split('#', 1)[0]))
        for prefix in ('experiments', 'output'):
            route = f'/{prefix}'
            if p != route and not p.startswith(f'{route}/'):
                continue
            rel = p[len(route):]
            artifact_root = (REPO_ROOT / prefix).resolve()
            final = Path(str(artifact_root) + rel).resolve()
            # Defense-in-depth: even after normpath, make sure the resolved
            # path is still confined to the intended artifact directory. Fall
            # back to SCRIPT_DIR (which has no matching file) to produce a 404.
            if final != artifact_root and not final.is_relative_to(artifact_root):
                return str(SCRIPT_DIR)
            return str(final)
        return super().translate_path(path)

    def do_GET(self):
        if self.path == "/api/index-mtime":
            self._handle_index_mtime()
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/regenerate":
            self._handle_regenerate()
        else:
            self.send_error(404)

    def _handle_index_mtime(self):
        """Return mtime of experiments.json for change-detection polling."""
        try:
            mtime = OUTPUT_FILE.stat().st_mtime
        except FileNotFoundError:
            mtime = 0
        body = json.dumps({"mtime": mtime}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _handle_regenerate(self):
        """Regenerate experiments.json and return the new index."""
        try:
            index = regenerate_index()
            body = json.dumps(index, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress noisy polling logs for /api/index-mtime. Guard against
        # malformed request lines (user-controlled input) that may not split
        # into the expected "METHOD PATH VERSION" shape.
        if args:
            parts = args[0].split()
            if len(parts) > 1 and parts[1].startswith("/api/index-mtime"):
                return
        super().log_message(format, *args)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _detect_lan_ip() -> Optional[str]:
    """Best-effort LAN IP detection (uses a UDP connect trick, no packets sent)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def run_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    watch: bool = False,
    watch_interval: float = 3.0,
    generate_only: bool = False,
) -> int:
    """Start the visualization server (or regenerate index and exit)."""
    if REPO_ROOT is None:
        raise RuntimeError(
            "Cannot locate experiments/ directory. "
            "Run from a repo checkout, or pass the repo root as argv[1] when invoking generate_index.py."
        )

    if not OUTPUT_FILE.exists() or generate_only:
        regenerate_index()

    if generate_only:
        return 0

    if watch:
        t = threading.Thread(target=watch_experiments, args=(watch_interval,), daemon=True)
        t.start()

    # Pass directory explicitly so SimpleHTTPRequestHandler serves static
    # files from SCRIPT_DIR without mutating the process-wide cwd.
    handler = partial(AnalyzerHandler, directory=str(SCRIPT_DIR))
    server = ThreadingHTTPServer((host, port), handler)
    print(f"\nBrowser Agent Analyzer → http://localhost:{port}")
    if host == "0.0.0.0":
        lan_ip = _detect_lan_ip()
        if lan_ip:
            print(f"  Network access      → http://{lan_ip}:{port}")
    elif host != "127.0.0.1":
        print(f"  Network access      → http://{host}:{port}")
    print(f"  Bind: {host}:{port}")
    print(f"  Watch mode: {'ON' if watch else 'OFF'}")
    print("  Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Browser Agent Analyzer dev server")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1; use 0.0.0.0 to expose to the network)",
    )
    parser.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    parser.add_argument("--watch", action="store_true", help="Auto-regenerate on file changes")
    parser.add_argument("--watch-interval", type=float, default=3.0, help="Watch poll interval in seconds")
    parser.add_argument("--generate-only", action="store_true", help="Generate index and exit")
    args = parser.parse_args(argv)
    return run_server(
        host=args.host,
        port=args.port,
        watch=args.watch,
        watch_interval=args.watch_interval,
        generate_only=args.generate_only,
    )


if __name__ == "__main__":
    main()
