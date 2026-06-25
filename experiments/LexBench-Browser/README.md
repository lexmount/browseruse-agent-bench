# LexBench Step Debugger

This directory contains a lightweight local debugger for LexBench-Browser experiment artifacts.

## Files

- `step_debugger_server.py`: local Python HTTP server
- `rpa_login_step_debugger.html`: browser UI served by the server

## Expected Artifact Layout

The server scans `experiments/LexBench-Browser` by default and expects runs that contain:

```text
config_snapshot.json
tasks/<task_id>/result.json
tasks/<task_id>/api_logs/step_*.json
tasks/<task_id>/api_logs/system_prompt.txt
tasks/<task_id>/trajectory/screenshot-*.png
```

## Start

From the repo root:

```bash
uv run python experiments/LexBench-Browser/step_debugger_server.py --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765/`.

If you place the HTML somewhere else, pass both `--root` and `--html`.

## Quick Validation

```bash
uv run python experiments/LexBench-Browser/step_debugger_server.py --check
```

This validates that the server can scan the experiment root and report how many runs and model configs it found.

## Security Note

Before sharing experiment artifacts, scrub secrets from every `config_snapshot.json`, especially fields such as:

```json
{
  "api_key": "",
  "lexmount_api_key": ""
}
```

Browsing existing steps does not require external model access. Replay only needs credentials if you actively run one-step model comparison from the UI.
