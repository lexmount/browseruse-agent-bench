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
  - `OPENAI_API_KEY` via `models.codex.api_key` (works headless), or
  - ChatGPT login: `codex login` is an interactive browser OAuth — on a
    headless server, log in elsewhere and copy `~/.codex/auth.json` into the
    service user's home. `--ignore-user-config` is always passed, so the
    operator's `~/.codex/config.toml` is never read, but `auth.json` is.
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

## Docker image status

The repo `Dockerfile` (used by self-hosted CI and server deploys) is based on
`python:3.11-slim` and currently ships **neither Node.js nor any of these
CLIs nor Chrome** — CLI agents cannot run in that image as-is. To support them
in a container, the image needs at minimum:

```dockerfile
# Node.js 22+ (openclaw requires >= 22.19; distro nodejs is usually too old)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code @openai/codex openclaw
# Pre-warm the Playwright MCP download AS THE RUNTIME USER (uid 1000 in this
# image) — npx caches per-user, so warming root's cache does not help:
USER 1000
RUN npx -y @playwright/mcp@latest --version
USER root
# cursor-agent installs outside npm, into the installing user's home.
# The repo image runs as uid 1000, and /root is not traversable by non-root
# users — install as the runtime user (after USER 1000), or relocate the
# install out of /root and point agents.cursor.cursor_agent_command at it:
RUN curl https://cursor.com/install -fsS | bash \
    && mv /root/.local/share/cursor-agent /opt/cursor-agent \
    && chmod -R a+rX /opt/cursor-agent
# then set agents.cursor.cursor_agent_command: /opt/cursor-agent/versions/<version>/cursor-agent
```

plus auth material at runtime (env vars above; `~/.codex/auth.json` for
ChatGPT-login codex). With the lexmount browser path no Chrome is needed in
the image for codex/cursor/openclaw; **include Chrome/Chromium (and headless
deps) if claude-code or `browser_id=local` runs are planned**. This
Dockerfile change is intentionally **not** applied yet — it grows the image
and affects CI; apply it when server-side runs of these agents are actually
scheduled.

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
