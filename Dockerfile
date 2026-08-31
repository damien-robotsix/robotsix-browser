# syntax=docker/dockerfile:1
FROM python:3.14-slim

# uv provides fast, reproducible dependency installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# git is required at BUILD time only: robotsix-config is a git dependency in
# pyproject.toml, and uv shells out to git to fetch it. python:*-slim ships
# without git, so `uv sync` fails with "Git operation failed" (same lesson as
# robotsix-file-hub's Dockerfile).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Install the Playwright Chromium browser plus its OS-level dependencies.
# (Reference: robotsix-chat render_url Dockerfile Chromium install block.)
RUN uv run playwright install --with-deps chromium

EXPOSE 8000

# Invoke the venv entrypoint directly: `uv run` re-resolves the environment at
# every container start and needs a writable uv cache — as the non-root runtime
# uid it crash-loops on `failed to create directory /.cache/uv` (same class as
# robotsix-file-hub PR #161). The venv is already complete after `uv sync`.
CMD ["/app/.venv/bin/robotsix-browser"]
