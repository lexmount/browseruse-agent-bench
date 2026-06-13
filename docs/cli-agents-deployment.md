# CLI Agents Deployment (claude-code / codex / cursor / openclaw)

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
| Node.js >= 18 + npm (claude-code, codex, **cursor**) / **>= 22.19 (openclaw)** | installs the npm CLIs, and `npx` launches Playwright MCP at runtime for codex **and cursor** (cursor's own binary installs via curl but still needs `npx`). openclaw declares `engines: >=22.19.0` — install Node 22+ when openclaw is in the fleet |
| Outbound HTTPS | model APIs, Cursor backend, lexmount CDP (wss) |
| `LEXMOUNT_API_KEY` in `.env` | recommended browser path on servers (see below) |

### Browser path

- **Recommended on servers: `lexmount`** (default via `agents.<agent>.active_browser`).
  The browser runs in the cloud; the server only needs outbound network. No
  local Chrome required for the CDP-capable agents (codex/cursor/openclaw) in
  this mode. **claude-code is the exception**: it has no managed-backend
  support and always launches a local browser via Playwright MCP, so a
  deployment that includes claude-code still needs Chrome/Chromium.
- **`browser_id=local`**: requires a local Chrome/Chromium plus headless-Linux
  dependencies. For codex/cursor, Playwright MCP downloads its own browser on
  first run — pre-warm with `npx -y @playwright/mcp@latest --version` during
  image build to avoid first-task latency.
- Cloud-native backends (`browser-use-cloud`, `skyvern-cloud`) are **not
  supported** by CLI agents (no CDP endpoint). codex/cursor/openclaw fail fast
  with a clear error; **claude-code does not inspect `browser_id` at all** and
  silently uses its local Playwright MCP browser regardless of the selected
  backend — do not point claude-code at a managed backend and expect an error.

## Per-agent install and auth

### claude-code

```bash
npm install -g @anthropic-ai/claude-code
```

- Auth: `ANTHROPIC_API_KEY` (+ optional `ANTHROPIC_BASE_URL`) via the model
  entry (`models.<name>.api_key/base_url`).
- Browser: Playwright MCP via `npx @playwright/mcp@latest` (local browser only;
  no managed-backend support yet).

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
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | codex (direct), openclaw (via config) | proxy endpoints OK |
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
claude-code/codex/openclaw CLIs, cursor-agent (relocated to
`/opt/cursor-agent` and symlinked into `/usr/local/bin` so the uid-1000
runtime user can execute it), and pre-warms the Playwright MCP download into
a world-readable npm cache (`NPM_CONFIG_CACHE=/opt/npm-cache`).

At runtime, provide auth material via env (`OPENAI_API_KEY`/`OPENAI_BASE_URL`,
`CURSOR_API_KEY`, `LEXMOUNT_API_KEY`, ...) — typically by mounting `.env` —
plus a `config.yaml`. Verified container invocations (image runs as uid 1000,
whose home is `/home/bench`):

```bash
# openclaw / cursor — env-only auth:
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

With the lexmount browser path no Chrome is needed in the image for
codex/cursor/openclaw; **include Chrome/Chromium (and headless deps)
separately if claude-code or `browser_id=local` runs are planned**.

## Smoke verification (per agent, after deploy)

```bash
# CLI self-checks
claude --version; codex --version; cursor-agent --version; openclaw --version

# Real end-to-end check (one task, see AGENTS.md smoke policy)
bubench run --agent codex   --data LexBench-Browser --mode single
bubench run --agent cursor  --data LexBench-Browser --mode single
bubench run --agent openclaw --data LexBench-Browser --mode single
# claude-code needs local Chrome + headless deps even when others use lexmount,
# and an Anthropic model selected (config.example.yaml has no
# agents.claude-code.active_model, so without an override it inherits
# default.model and the wrong api key):
bubench run --agent claude-code --data LexBench-Browser --mode single --model sonnet
```

Verify by log, not exit code:

- codex/cursor/openclaw on a managed backend: `run.log` shows
  `Lexmount session created: wss://...` plus the agent's model-call lines.
- claude-code (and any `browser_id=local` run): no Lexmount line is expected —
  look for the Playwright MCP browser starting and the agent's tool calls.
- In all cases `result.json` should record steps > 0 for browsing tasks.
