# CLI Agents Deployment (claude-code / codex / cursor / openhands / openclaw)

The CLI-based agents drive an external coding-agent CLI as a subprocess. They
share the repo's main venv (`uv sync`, no extra) but each requires its CLI
binary, authentication, and a browser path on the machine that runs
`bubench run`. This page is the deployment checklist for running them on a
server.

All version numbers below are the versions the integration was verified
against; newer versions usually work but re-run the smoke command after
upgrading.

## Shared prerequisites

| Requirement | Why |
|---|---|
| Python >= 3.11 + `uv sync` | bench runtime (all CLI agents use the root `.venv`) |
| Node.js >= 18 + npm (claude-code, codex, **cursor**) / **>= 22.19 (openclaw)** | installs the npm CLIs, and `npx` launches Playwright MCP at runtime for codex, cursor, and openhands (cursor's own binary installs via curl but still needs `npx`). openclaw declares `engines: >=22.19.0` — install Node 22+ when openclaw is in the fleet |
| Python 3.12+ for OpenHands (`uv tool install openhands --python 3.12`) | OpenHands CLI runtime |
| Outbound HTTPS | model APIs, Cursor backend, lexmount CDP (wss) |
| `LEXMOUNT_API_KEY` in `.env` | recommended browser path on servers (see below) |

### Browser path

- **Recommended on servers: `lexmount`** (default via `agents.<agent>.active_browser`).
  The browser runs in the cloud; the server only needs outbound network. No
  local Chrome required for the CDP-capable agents (claude-code/codex/cursor/openhands/openclaw)
  in this mode — claude-code now opens the managed backend session and attaches
  Playwright MCP to its CDP endpoint (`--cdp-endpoint`), like the others.
- **`browser_id=local`**: requires a local Chrome/Chromium plus headless-Linux
  dependencies. For claude-code/codex/cursor/openhands, Playwright MCP downloads its own
  browser on first run — pre-warm with `npx -y @playwright/mcp@latest --version`
  during image build to avoid first-task latency.
- Cloud-native backends (`browser-use-cloud`, `skyvern-cloud`) are **not
  supported** by CLI agents (no CDP endpoint). All CLI agents
  fail fast with a clear error rather than silently self-launching a local browser.

## Per-agent install and auth

### claude-code

```bash
npm install -g @anthropic-ai/claude-code
```

- Auth: `ANTHROPIC_API_KEY` (+ optional `ANTHROPIC_BASE_URL`) via the model
  entry (`models.<name>.api_key/base_url`). Select an Anthropic model with
  `agents.claude-code.active_model` (e.g. `sonnet`); without it the agent
  inherits `default.model` (an OpenAI entry) and auth fails.
- `base_url` must be the Anthropic API **root** — the claude CLI appends
  `/v1/messages` itself, so a value ending in `/v1` becomes `/v1/v1/messages`
  and 404s. (`api.anthropic.com` needs no override; for a LiteLLM proxy use the
  bare host, not the OpenAI-style `.../v1` URL.) The endpoint must serve the
  Anthropic Messages API natively, including `thinking`/`context_management` —
  a proxy that routes Claude through an OpenAI adapter rejects those params with
  a 400.
- Browser: Playwright MCP via `npx @playwright/mcp@latest`; managed CDP backends
  (lexmount, cdp) attach automatically with `--cdp-endpoint`, so no local Chrome
  is needed in that mode.
- Runs as root (server/container): claude refuses `--dangerously-skip-permissions`
  under root unless `IS_SANDBOX=1` — the agent sets it in the subprocess env.

### codex (verified: codex-cli 0.130.0)

```bash
npm install -g @openai/codex
```

- Auth, either:
  - **ChatGPT login** (`codex login`): interactive browser OAuth — on a
    headless server, log in elsewhere and copy `~/.codex/auth.json` into the
    service user's home. Set no `base_url` (uses api.openai.com). `--ignore-user-config`
    is always passed, so the operator's `~/.codex/config.toml` is never read,
    but `auth.json` is.
  - **api_key + base_url proxy**: codex **ignores `OPENAI_BASE_URL`**, so a
    plain `OPENAI_API_KEY` alone hits api.openai.com (a proxy key → 401). When
    `models.codex.base_url` is set, the agent registers a codex model provider
    (`-c model_providers.<name>.base_url` + `wire_api="responses"` + `env_key="OPENAI_API_KEY"`)
    so codex routes there. The endpoint **must serve the OpenAI Responses API**
    (codex >=0.139 dropped `wire_api="chat"`, so a chat-only proxy fails), and
    `model_id` must be a model deployed on it (e.g. a LiteLLM proxy that already
    serves the model). `model_provider` in the model config names the provider
    (default `bench`). Verified end-to-end against a LiteLLM proxy serving the
    Responses API.
- Browser: Playwright MCP via `npx`; managed CDP backends (lexmount, cdp)
  attach automatically with `--cdp-endpoint`.

### cursor (verified: cursor-agent 2026.06.11)

```bash
curl https://cursor.com/install -fsS | bash   # installs to ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"          # ensure on PATH for the bench process
```

- Auth: `CURSOR_API_KEY` in `.env` (referenced by `models.cursor.api_key`).
  This is a **Cursor key from cursor.com → Settings → API Keys** — not an
  OpenAI key; model calls route through Cursor's backend and there is no
  BYOK/base-url support. Browser OAuth (`cursor-agent login`) is not viable
  headless; per-task `CURSOR_CONFIG_DIR` isolation requires API-key auth
  (set `agents.cursor.isolate_user_config: false` only for OAuth setups).
- Override the executable path with `agents.cursor.cursor_agent_command` if
  `~/.local/bin` is not on the service PATH.
- Browser: Playwright MCP via `npx`; managed CDP backends attach automatically.
- **Model selection**: `models.cursor.model_id` must be a Cursor-hosted model
  id (`cursor-agent --list-models`) — no BYOK, a proxy/OpenAI id is rejected.
  To test models, either point at a preset entry
  (`bubench run --agent cursor --model cursor-fable`) or pass a raw Cursor
  model id through directly without a config entry
  (`bubench run --agent cursor --model claude-opus-4-8-high`). The model
  Cursor actually resolves (e.g. "GPT-5.2 Medium") is recorded under
  `agent_metadata.reported_model` in each result; avoid `model_id: auto`
  (non-deterministic, breaks the experiments-dir / eval model match).

### openhands (verified: OpenHands CLI 1.16.0 / SDK 1.21.0)

```bash
uv tool install openhands --python 3.12
```

- Auth: `models.openhands.api_key/base_url/model_id` are passed via
  `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` with `--override-with-envs`.
  OpenAI-compatible endpoints work; in local smoke testing `gpt-5.5` through
  the LiteLLM gateway succeeded, while `gpt-5.4` returned 404 from that route.
- Browser: Playwright MCP via `npx`; managed CDP backends attach automatically.
- The agent writes a task-local `~/.openhands/mcp.json` by setting `HOME` and
  `OH_PERSISTENCE_DIR` to a workspace-local directory. `UV_CACHE_DIR` remains
  pointed at the operator cache so `uv tool run openhands` does not reinstall
  OpenHands for every task.
- OpenHands may emit transient `AgentErrorEvent`s when the model retries a tool
  call with the right schema; the adapter records final success when a later
  agent message answers the task.

### openclaw (verified: openclaw 2026.5.22)

```bash
npm install -g openclaw
```

- Auth: none on the server. The agent writes a per-task OpenClaw provider
  config from `models.openclaw.api_key/base_url` (OpenAI-compatible endpoints
  incl. LiteLLM proxies); the key is scrubbed from artifacts after each run.
  The operator's `~/.openclaw` is never read.
- Browser: OpenClaw's built-in browser tool. With lexmount/cdp it attaches to
  the remote CDP endpoint; with `browser_id=local` it launches a local
  Chrome/Chromium, which must be installed.

## Environment variables summary

| Variable | Used by | Notes |
|---|---|---|
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | codex (direct), openclaw/openhands (via config) | proxy endpoints OK |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | claude-code | |
| `CURSOR_API_KEY` | cursor | Cursor key, not OpenAI |
| `LEXMOUNT_API_KEY` / `LEXMOUNT_PROJECT_ID` | lexmount browser backend | recommended on servers |

## Docker image

The repo `Dockerfile` supports the CLI agents behind a build arg (off by
default, so the CI image is unchanged):

```bash
docker build --build-arg INSTALL_CLI_AGENTS=true -t bubench-cli .
```

This installs Node.js 22.x (openclaw requires >= 22.19), the
claude-code/codex/openclaw CLIs, OpenHands as a uv tool,
cursor-agent (relocated to
`/opt/cursor-agent` and symlinked into `/usr/local/bin` so the uid-1000
runtime user can execute it), and pre-warms the Playwright MCP download into
a world-readable npm cache (`NPM_CONFIG_CACHE=/opt/npm-cache`).

At runtime, provide auth material via env (`OPENAI_API_KEY`/`OPENAI_BASE_URL`,
`CURSOR_API_KEY`, `LEXMOUNT_API_KEY`, ...) — typically by mounting `.env` —
plus a `config.yaml`. Verified container invocations (image runs as uid 1000,
whose home is `/home/bench`):

```bash
# openclaw / cursor / openhands — env-only auth:
docker run --rm --user 1000 -v "$PWD/.env:/app/.env:ro" bubench-cli \
  uv run scripts/run.py --agent openclaw --data LexBench-Browser --mode single

# codex with ChatGPT login — do NOT bind-mount auth.json directly into
# ~/.codex (docker creates the parent dir root-owned and codex cannot write
# its session files); stage it and copy:
docker run --rm --user 1000 -v "$PWD/.env:/app/.env:ro" \
  -v "$HOME/.codex/auth.json:/auth/auth.json:ro" --entrypoint bash bubench-cli \
  -c 'mkdir -p ~/.codex && cp /auth/auth.json ~/.codex/ \
      && exec /app/scripts/docker-entrypoint.sh uv run scripts/run.py \
         --agent codex --data LexBench-Browser --mode single'
```

With API-key codex auth instead, set `models.codex.base_url` when using a
proxy endpoint (the key alone routes to api.openai.com).

With the lexmount browser path no Chrome is needed in the image for any of the
CLI agents (claude-code/codex/cursor/openhands/openclaw); **include Chrome/Chromium (and
headless deps) separately only if `browser_id=local` runs are planned**.

## Smoke verification (per agent, after deploy)

```bash
# CLI self-checks
claude --version; codex --version; cursor-agent --version
openhands --version; openclaw --version

# Real end-to-end check (one task, see AGENTS.md smoke policy)
bubench run --agent codex   --data LexBench-Browser --mode single
bubench run --agent cursor  --data LexBench-Browser --mode single
bubench run --agent openhands  --data LexBench-Browser --mode single
bubench run --agent openclaw --data LexBench-Browser --mode single
# claude-code on lexmount needs no local Chrome; config.example.yaml pins
# agents.claude-code.active_model: sonnet (an Anthropic model):
bubench run --agent claude-code --data LexBench-Browser --mode single
```

Verify by log, not exit code:

- CLI agents on a managed backend: `run.log` shows
  `Lexmount session created: wss://...` plus the agent's model-call lines.
- Any `browser_id=local` run: no Lexmount line is expected — look for the
  Playwright MCP browser starting and the agent's tool calls.
- In all cases `result.json` should record steps > 0 for browsing tasks.
