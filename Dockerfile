# syntax=docker/dockerfile:1
FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

# uv provides fast, reproducible dependency installs.
# Pinned by immutable digest (tag kept for readability); Dependabot bumps both.
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# The image installs everything with uv, so the base image's bundled pip is
# unused. Remove it: its vendored certifi / msgpack / urllib3 / pkg_resources
# (setuptools) copies carry CRITICAL/HIGH CVEs flagged by the Trivy image gate.
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.*/site-packages/pip /usr/local/lib/python3.*/site-packages/pip-*.dist-info

# git is required at BUILD time only: robotsix-config is a git dependency in
# pyproject.toml, and uv shells out to git to fetch it. python:*-slim ships
# without git, so `uv sync` fails with "Git operation failed" (same lesson as
# robotsix-file-hub's Dockerfile).
# DL3008 (pin apt package versions) is suppressed deliberately: Debian
# rotates point-release versions out of the archive, so a hard `git=<ver>` pin
# would break the build on the next security update. git is a build-time
# helper only, and the base image itself is pinned by digest (Dependabot bumps it).
# hadolint ignore=DL3008
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
