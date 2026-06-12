# Build argument to enable China mirrors (default: false)
# Usage: docker build --build-arg USE_CN_MIRROR=true .
ARG USE_CN_MIRROR=false
ARG BASE_IMAGE=${USE_CN_MIRROR:+docker.m.daocloud.io/}python:3.11-slim

# Stage 1: Build stage to install dependencies
FROM ${BASE_IMAGE:-python:3.11-slim} AS builder

SHELL ["/bin/bash", "-c"]

ARG USE_CN_MIRROR=false

# Conditionally use Aliyun apt mirror for faster downloads in China
RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources; \
    fi

# System tools required by self-hosted-ci workflow steps:
#   git              — actions/checkout, git operations inside the container
#   ca-certificates  — TLS roots for HTTPS to PyPI/GHCR/GitHub
#   curl             — astral-sh/setup-uv downloads uv via curl
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv, clean up in same layer
RUN pip install --no-cache-dir $([ "$USE_CN_MIRROR" = "true" ] && echo "-i https://mirrors.aliyun.com/pypi/simple/") uv \
    && rm -rf /root/.cache/pip

WORKDIR /app

# Copy all project files (code included)
COPY . .

# Install dependencies using uv sync (keep cache for faster runtime sync)
RUN UV_INDEX_URL=$([ "$USE_CN_MIRROR" = "true" ] && echo "https://mirrors.aliyun.com/pypi/simple/") \
    UV_HTTP_TIMEOUT=120 \
    uv sync --extra browser-use \
    && uv pip install -e . \
    && rm -rf /root/.cache/pip \
    && find /app/.venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Stage 2: Final image
FROM ${BASE_IMAGE:-python:3.11-slim}

SHELL ["/bin/bash", "-c"]

ARG USE_CN_MIRROR=false

# Conditionally use Aliyun apt mirror for faster downloads in China
RUN if [ "$USE_CN_MIRROR" = "true" ]; then \
        sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources; \
    fi

# System tools required by self-hosted-ci workflow steps (mirrored in final
# image because PR #313 runs the container as uid 1000 — non-root cannot
# `apt-get install` at workflow time).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv in final image for runtime use
RUN pip install --no-cache-dir $([ "$USE_CN_MIRROR" = "true" ] && echo "-i https://mirrors.aliyun.com/pypi/simple/") uv \
    && rm -rf /root/.cache/pip

WORKDIR /app

# Copy project files and virtual environment from builder
COPY --from=builder /app /app

# Copy uv cache from builder (for faster dependency sync)
COPY --from=builder /root/.cache/uv /root/.cache/uv

# CI self-hosted-ci runs the container as uid 1000 (per #313), so /app
# contents must be writable/deletable by that uid. Without this chown, the
# workflow's "Sync PR code into /app" step EACCES's on `rm -rf /app/*`.
RUN chown -R 1000:1000 /app /root/.cache/uv

# Set environment variables
ENV PATH="/app/.venv/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT="/app/.venv"

# Optional: CLI coding agents (codex / cursor / openclaw, plus the claude-code
# CLI). Off by default to keep the CI image small. Enable with:
#   docker build --build-arg INSTALL_CLI_AGENTS=true .
# Notes (see docs/cli-agents-deployment.md):
#   - Node 22.x via NodeSource: openclaw declares engines node>=22.19.0.
#   - npm cache lives at a world-writable path so the uid-1000 runtime user
#     reuses the pre-warmed Playwright MCP download (npx caches per-user via
#     this env, not under /root).
#   - cursor-agent installs under /root; relocate it and symlink the real
#     binary so uid 1000 can execute it (/root is not traversable).
#   - No Chrome in the image: server runs use the lexmount browser backend.
#     claude-code (local-browser only) still needs Chrome added separately.
ARG INSTALL_CLI_AGENTS=false
ENV NPM_CONFIG_CACHE=/opt/npm-cache
RUN if [ "$INSTALL_CLI_AGENTS" = "true" ]; then \
        curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
        && apt-get install -y --no-install-recommends nodejs \
        && rm -rf /var/lib/apt/lists/* \
        && if [ "$USE_CN_MIRROR" = "true" ]; then npm config set registry https://registry.npmmirror.com -g; fi \
        && npm install -g @anthropic-ai/claude-code @openai/codex openclaw \
        && curl https://cursor.com/install -fsS | bash \
        && mv /root/.local/share/cursor-agent /opt/cursor-agent \
        && ln -s /opt/cursor-agent/versions/*/cursor-agent /usr/local/bin/cursor-agent \
        && npx -y @playwright/mcp@latest --version \
        && chmod -R a+rwX /opt/npm-cache \
        && chmod -R a+rX /opt/cursor-agent \
        # Real passwd entry + home for the uid-1000 runtime user: codex
        # refuses a codex_home under temporary dirs, so HOME must not fall
        # back to /tmp. docker run --user 1000 resolves HOME from passwd.
        && useradd -u 1000 -m -s /bin/bash bench; \
    fi

# Setup entrypoint script (already copied from builder)
RUN chmod +x /app/scripts/docker-entrypoint.sh

# Provide default config.yaml from example (can be overridden by mounting a custom config.yaml at runtime)
RUN cp /app/config.example.yaml /app/config.yaml

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]

CMD ["uv", "run", "scripts/run.py", "--help"]